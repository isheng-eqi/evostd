"""可见性探针：同一份信息，人看不看得见？

innerText 只包含可见文字，textContent 包含隐藏文字。
两者之差 = 「机器看得见、人看不见」。

检查项：联系电话 / 网站标识码 / ICP 备案 / 主办单位。

输出：
    data/hidden-report.json
"""
from __future__ import annotations

import json
import os
import time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITES = os.path.join(ROOT, "data", "gov-sites-30.json")
OUT = os.path.join(ROOT, "data", "hidden-report.json")

KEYWORDS = ["联系电话", "网站标识码", "ICP", "主办"]

JS = r"""
(keywords) => {
  const vis = document.body ? document.body.innerText : "";
  const all = document.body ? document.body.textContent : "";
  const out = {};
  for (const kw of keywords) {
    const inVis = vis.includes(kw);
    const inAll = all.includes(kw);
    out[kw] = {visible: inVis ? 1 : 0, hidden_only: (!inVis && inAll) ? 1 : 0};
    if (!inVis && inAll) {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let n, sample = "";
      while ((n = walker.nextNode())) {
        if (n.textContent && n.textContent.includes(kw)) {
          const el = n.parentElement;
          const cs = el ? getComputedStyle(el) : null;
          sample = (el ? el.textContent : "").trim().slice(0, 60) + " @ " +
                   (cs ? cs.display + "/" + cs.visibility : "");
          break;
        }
      }
      out[kw].sample = sample;
    }
  }
  return out;
}
"""


def main() -> int:
    sites = json.load(open(SITES, encoding="utf-8"))
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        for name, url in sites.items():
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1200)
                data = page.evaluate(JS, KEYWORDS)
                for frame in page.frames[1:]:
                    try:
                        sub = frame.evaluate(JS, KEYWORDS)
                    except Exception:  # noqa: BLE001
                        continue
                    for kw, v in sub.items():
                        if v.get("hidden_only") and not data[kw]["visible"]:
                            data[kw]["hidden_only"] = 1
                            data[kw].setdefault("sample", v.get("sample", ""))
                results[name] = data
                hidden = [k for k, v in data.items() if v.get("hidden_only")]
                print(f"   {name:4} " + ("隐藏: " + "、".join(hidden) if hidden else "全部可见"))
            except Exception as e:  # noqa: BLE001
                results[name] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
                print(f"   {name:4} ERR {results[name]['error']}")
            finally:
                page.close()
        ctx.close()
        browser.close()

    only_hidden = []
    for name, data in results.items():
        if "error" in data:
            continue
        for kw, v in data.items():
            if v.get("hidden_only"):
                only_hidden.append({"site": name, "keyword": kw, "sample": v.get("sample", "")})
    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "results": results, "only_hidden": only_hidden},
              open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n只存在于隐藏元素的：{len(only_hidden)} 条")
    for x in only_hidden[:10]:
        print("   ", x["site"], x["keyword"], "|", x["sample"][:70])
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
