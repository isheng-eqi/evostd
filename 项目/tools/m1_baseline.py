"""M1 基线对比：同一批真实判定，三个做法谁更靠谱。

样本来自 `data/m1-judgments.json`（gov_audit_probe 跑 30 个真实政府网站的结果）。
本轮先做两条**证据在文本里**的规则，保证人、脚本、大模型看到的是同一份东西：

- R8 页面标签：title / meta description 是否存在；
- R2 首页更新：首页能否解析出 2 周内的发布日期。

三个做法：
- B1 裸模型：只给条款 + 网页摘要，凭常识判断；
- B2 检索增强：给整份《检查指标》+ 网页摘要，仍不强制弃权；
- B3 本系统：确定性规则，证据不足就弃权。

用法（需要环境变量 DS_KEY）：
    cd 项目
    set DS_KEY=sk-...
    python tools/m1_baseline.py --judgments data/m1-judgments.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
API = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-flash"

RULES = {
    "R8": {
        "name": "页面标签",
        "clause": "页面标签：随机抽查内容页面，无站点标签或内容标签的，每个扣 0.1 分。站点标签指 title，内容标签指 meta description。",
        "question": "该网页是否同时具备站点标签（title）和内容标签（meta description）？",
    },
    "R5": {
        "name": "网站标识",
        "clause": "网站标识：未在全站页面底部功能区清晰列明党政机关网站标识、网站标识码、ICP 备案编号、公安机关备案标识、网站主办单位、联系方式的，每缺一项，扣 0.5 分。",
        "question": "该网页页脚是否清晰列明了：网站标识码、ICP 备案号、公安备案、主办单位、联系电话？缺哪一项？",
    },
    "R2": {
        "name": "首页 2 周内更新",
        "clause": "首页不更新：监测 2 周，首页无信息更新的，即单项否决。稿件发布页未注明发布时间的视为不更新。",
        "question": "该首页是否在最近 2 周内更新过（能看到的发布日期是否在今天前 14 天内）？",
    },
}


def fetch_page(url: str) -> dict:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    if not r.encoding or r.encoding.lower() in ("iso-8859-1",):
        r.encoding = r.apparent_encoding
    soup = BeautifulSoup(r.text, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": re.compile(r"^\s*description\s*$", re.I)})
    desc = meta.get("content", "").strip() if meta else ""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" "))
    return {
        "url": url,
        "html": r.text,
        "title": title,
        "desc": desc,
        "text": text[:4000],
        "footer": text[-800:],
        "status": r.status_code,
    }


def load_rendered(sites: dict, rendered_dir: str) -> dict:
    out = {}
    for name, url in sites.items():
        hp = os.path.join(rendered_dir, f"{name}.html")
        tp = os.path.join(rendered_dir, f"{name}.txt")
        if not (os.path.exists(hp) and os.path.exists(tp)):
            continue
        html = open(hp, encoding="utf-8").read()
        text = re.sub(r"\s+", " ", open(tp, encoding="utf-8").read())
        soup = BeautifulSoup(html, "lxml")
        title = soup.title.get_text(strip=True) if soup.title else ""
        meta = soup.find("meta", attrs={"name": re.compile(r"^\s*description\s*$", re.I)})
        desc = (meta.get("content") or "").strip() if meta else ""
        out[name] = {"url": url, "html": html, "title": title, "desc": desc,
                     "text": text[:6000], "footer": text[-1200:], "status": 200}
    return out


def representation(p: dict) -> str:
    return (f"【URL】{p['url']}\n【标题】{p['title'] or '（空）'}\n"
            f"【meta description】{p['desc'] or '（空）'}\n"
            f"【正文前 4000 字】{p['text']}\n【页脚】{p['footer']}")


def call_llm(prompt: str, key: str) -> tuple[dict, dict]:
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "你是政务公开检查员。根据给定材料给出你的判断。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }
    for attempt in range(3):
        try:
            r = requests.post(API, json=body, timeout=90,
                              headers={"Authorization": f"Bearer {key}"})
            if r.status_code != 200:
                if attempt == 0:
                    print(f"   [llm] HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(2 * (attempt + 1))
                continue
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            try:
                return json.loads(content), usage
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", content, re.S)
                if m:
                    return json.loads(m.group(0)), usage
                return {"verdict": "不确定", "evidence": "", "reason": "解析失败"}, usage
        except Exception as e:  # noqa: BLE001
            if attempt == 0:
                print(f"   [llm] {type(e).__name__}: {str(e)[:160]}")
            time.sleep(2 * (attempt + 1))
    return {"verdict": "不确定", "evidence": "", "reason": "调用失败"}, {}


def verdict_of(label: str) -> str:
    return {"pass": "合规", "fail": "不合规", "abstain": "不确定"}.get(label, "不确定")


def build_samples(judgments: dict, site_pages: dict, r8_truth: dict | None = None) -> list[dict]:
    rule_meta = judgments["results"]
    site_to_pages = site_pages
    samples = []
    for site in rule_meta:
        name = site["site"]
        if name not in site_to_pages:
            continue
        page = site_to_pages[name]
        rep = representation(page)
        for rule in site["rules"]:
            rid = rule["id"]
            if rid not in RULES:
                continue
            cfg = RULES[rid]
            label = rule["status"]
            truth_src = "probe"
            if r8_truth and name in r8_truth and rid in r8_truth[name]:
                label = r8_truth[name][rid]["status"]
                truth_src = "rendered"
            samples.append({
                "site": name,
                "rule": rid,
                "rule_name": cfg["name"],
                "clause": cfg["clause"],
                "question": cfg["question"],
                "label": label,
                "truth_source": truth_src,
                "our_detail": rule["detail"],
                "our_evidence": rule["evidence"],
                "page": page,
                "rep": rep,
                "ground_truth": verdict_of(label),
                "independent_ok": (rid != "R8") or (
                    bool(page["desc"]) == bool(re.search(
                        r'<meta[^>]+name=["\']?description', page["html"], re.I))),
            })
    return samples


def run_baseline(samples: list[dict], key: str, variant: str, indicator: str) -> list[dict]:
    out = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    def one(s):
        if variant == "B1":
            prompt = f"条款：{s['clause']}\n\n{s['question']}\n\n{ s['rep'] }"
        else:
            prompt = (f"《政府网站与政务新媒体检查指标》（节选）：\n{indicator}\n\n"
                      f"条款：{s['clause']}\n\n{s['question']}\n\n{s['rep']}")
        prompt += ('\n\n只输出 JSON（不要解释），格式：'
                   '{"verdict":"合规|不合规|不确定","evidence":"原文片段，不超过40字","reason":"一句话"}')
        ans, usage = call_llm(prompt, key)
        for k in total_usage:
            total_usage[k] += usage.get(k, 0)
        return {**{k: s[k] for k in ("site", "rule", "rule_name", "label",
                                     "ground_truth", "our_detail")},
                "verdict": ans.get("verdict", "不确定"),
                "evidence": str(ans.get("evidence", ""))[:80],
                "reason": str(ans.get("reason", ""))[:80]}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(one, s) for s in samples]
        for f in as_completed(futs):
            out.append(f.result())
    out.sort(key=lambda x: (x["site"], x["rule"]))
    print(f"   {variant}: {len(out)} 条，tokens={total_usage}")
    return out


def score(rows: list[dict]) -> dict:
    judged = [r for r in rows if r["ground_truth"] in ("合规", "不合规")]
    unver = [r for r in rows if r["ground_truth"] == "不确定"]
    correct = sum(1 for r in judged if r["verdict"] == r["ground_truth"])
    abstain = sum(1 for r in judged if r["verdict"] == "不确定")
    fp = sum(1 for r in judged if r["verdict"] == "不合规" and r["ground_truth"] == "合规")
    fn = sum(1 for r in judged if r["verdict"] == "合规" and r["ground_truth"] == "不合规")
    pos = sum(1 for r in judged if r["ground_truth"] == "合规")
    over = sum(1 for r in unver if r["verdict"] != "不确定")
    return {
        "n": len(judged),
        "correct": correct,
        "accuracy": round(correct / len(judged), 3) if judged else 0,
        "abstain": abstain,
        "abstain_rate": round(abstain / len(judged), 3) if judged else 0,
        "false_positive": fp,
        "false_negative": fn,
        "specificity": round((pos - fp) / pos, 3) if pos else 0,
        "n_unverifiable": len(unver),
        "overclaim": over,
        "overclaim_rate": round(over / len(unver), 3) if unver else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judgments", default=os.path.join(ROOT, "data", "m1-judgments.json"))
    ap.add_argument("--sites", default=os.path.join(ROOT, "data", "gov-sites-30.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "m1-baseline-results.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--r8-labels", default=os.path.join(ROOT, "data", "m1-rendered-labels.json"))
    ap.add_argument("--rendered-dir", default=os.path.join(ROOT, "data", "rendered"))
    args = ap.parse_args()

    key = os.environ.get("DS_KEY", "")
    if not key:
        print("缺少 DS_KEY", file=sys.stderr)
        return 1

    judgments = json.load(open(args.judgments, encoding="utf-8"))
    sites = json.load(open(args.sites, encoding="utf-8"))
    indicator = open(os.path.join(ROOT, "data", "indicator-2021.txt"), encoding="utf-8").read()

    pages = load_rendered(sites, args.rendered_dir)
    print(f"== 渲染页 {len(pages)} 个（缺失的跳过） ==")

    r8_truth = None
    if os.path.exists(args.r8_labels):
        raw = json.load(open(args.r8_labels, encoding="utf-8"))
        r8_truth = raw.get("labels", raw)
    samples = build_samples(judgments, pages, r8_truth)
    if args.limit:
        samples = samples[:args.limit]
    print(f"== 样本 {len(samples)} 条（规则 {sorted({s['rule'] for s in samples})}）")

    b1 = run_baseline(samples, key, "B1", indicator)
    b2 = run_baseline(samples, key, "B2", indicator)

    our = []
    for s in samples:
        our.append({
            "site": s["site"], "rule": s["rule"], "rule_name": s["rule_name"],
            "label": s["label"], "ground_truth": s["ground_truth"],
            "verdict": s["ground_truth"], "evidence": s["our_detail"][:80], "reason": "",
        })

    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "samples": len(samples),
        "rules": sorted({s["rule"] for s in samples}),
        "scores": {"B1_raw": score(b1), "B2_rag": score(b2), "B3_ours": score(our)},
        "rows": {"B1": b1, "B2": b2, "B3": our},
    }
    json.dump(result, open(args.out, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    print("\n== 结果 ==")
    for k, v in result["scores"].items():
        print(f"   {k:8} 可判定 n={v['n']:3} 准确率={v['accuracy']:.3f} 弃权={v['abstain_rate']:.3f} "
              f"误报={v['false_positive']} 漏报={v['false_negative']}")
        print(f"   {'':8} 不可判定 n={v['n_unverifiable']:3} 硬答率={v['overclaim_rate']:.3f}")
    print(f"   -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
