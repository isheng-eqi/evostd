"""页脚 OCR：找出「信息只放在图片里」的真实案例。

对每个站点截取页面底部，用 Qwen3-VL 读图，和页面可见文本对比：
- 图里读到了「联系电话 / 网站标识码 / ICP / 主办」；
- 但页面 innerText 里没有。
→ 这条信息只有人（看图）能看到，机器读文本看不到。

用法：
    cd 项目
    set VLM_KEY=...
    python tools/footer_vlm.py
输出：
    data/footer-ocr.json
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time

import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITES = os.path.join(ROOT, "data", "gov-sites-30.json")
OUT = os.path.join(ROOT, "data", "footer-ocr.json")
IMG_DIR = os.path.join(ROOT, "data", "footers")
KEY = os.environ.get("VLM_KEY", "")
MODEL = "Qwen/Qwen3-VL-8B-Instruct"
API = "https://api.siliconflow.cn/v1/chat/completions"
KW = ["联系电话", "网站标识码", "ICP", "主办"]


def ocr(path: str) -> str:
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    body = {"model": MODEL, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "逐字读出图片里的所有文字，不要解释。"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
        "max_tokens": 400, "temperature": 0}
    for attempt in range(3):
        try:
            r = requests.post(API, json=body, headers={"Authorization": "Bearer " + KEY}, timeout=90)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    return ""


def main() -> int:
    if not KEY:
        print("缺少 VLM_KEY", file=sys.stderr)
        return 1
    os.makedirs(IMG_DIR, exist_ok=True)
    sites = json.load(open(SITES, encoding="utf-8"))
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sites": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        for name, url in sites.items():
            page = ctx.new_page()
            img = os.path.join(IMG_DIR, f"{name}.png")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1200)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(600)
                page.screenshot(path=img, clip={"x": 0, "y": 580, "width": 1440, "height": 320})
                text = page.inner_text("body")
                o = ocr(img)
                in_page = {k: (k in text) for k in KW}
                in_img = {k: (k in o) for k in KW}
                only_img = [k for k in KW if in_img[k] and not in_page[k]]
                out["sites"][name] = {"only_in_image": only_img,
                                      "ocr_len": len(o), "in_page": in_page}
                if only_img:
                    print(f"   {name:4} 只在图片里: {only_img} | OCR 片段: {o[:60].replace(chr(10),' ')}")
                else:
                    print(f"   {name:4} -")
            except Exception as e:  # noqa: BLE001
                out["sites"][name] = {"error": str(e)[:80]}
                print(f"   {name:4} ERR {out['sites'][name]['error']}")
            finally:
                page.close()
        ctx.close()
        browser.close()
    cases = [(n, v["only_in_image"]) for n, v in out["sites"].items() if v.get("only_in_image")]
    out["cases"] = cases
    json.dump(out, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"\n只在图片里的信息：{len(cases)} 站 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
