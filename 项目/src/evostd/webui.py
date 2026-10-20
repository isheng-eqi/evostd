"""决策工作台（单页 Web UI，纯标准库）。

为什么要有它
------------
赛题评分里 25% 是「系统交互体验与能力沉淀」。Nexent 的对话页是通用聊天界面，
评委看不到"这个系统的判定长什么样"。这个工作台只做一件事：

    左边点一条资产 → 中间出决策记录 → 右边出证据链。

判定全部来自 `std_verify`（与服务层同一份代码），页面不做任何判断。

跑法：
    cd 项目/src
    set EVOSTD_NOW=2026-10-21
    python -m evostd.webui --port 8899
    浏览器打开 http://127.0.0.1:8899
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DOMAIN = os.environ.get("EVOSTD_DOMAIN", "manufacturing")
# webui.py 在 项目/src/evostd/ 下：上三级才是 项目/
PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 每个领域一份图：制造业用 kg/，政务用 kg-gov/（目录名与导出脚本保持一致）
KG_DIR = {"manufacturing": "kg", "gov_catalog": "kg-gov"}.get(DOMAIN, f"kg-{DOMAIN}")
KG_PATH = os.path.join(PROJ, "submission", KG_DIR, "graph.json")

_SVC = None
_ASSETS = None


def svc():
    global _SVC, _ASSETS
    if _SVC is None:
        import evostd.server as S

        _SVC = S.build_service(DOMAIN)
        mod = __import__(f"domains.{DOMAIN}.assets", fromlist=["all_assets"])
        _ASSETS = mod.all_assets()
    return _SVC


def _clean(a: dict) -> dict:
    return {k: v for k, v in a.items() if not k.startswith("_")}


def _id(a: dict) -> str:
    return str(a.get("asset_id") or a.get("catalog_id") or a.get("asset_no") or "")


def assets_payload() -> list[dict]:
    svc()
    import evostd.server as S

    out = []
    for a in _ASSETS:
        c = _clean(a)
        r = S.std_verify(json.dumps([c], ensure_ascii=False))
        res = r["results"][0]
        out.append({
            "id": _id(a),
            "label": a.get("_label", ""),
            "why": a.get("_why", ""),
            "n_violations": res.get("n_violations"),
            "compliant": res.get("compliant"),
            "rate": res.get("compliance"),
        })
    return out


def decision_payload(asset_id: str) -> dict:
    svc()
    import evostd.server as S

    a = next((x for x in _ASSETS if _id(x) == asset_id), None)
    if a is None:
        return {"ok": False, "error": "未知资产"}
    c = _clean(a)
    r = S.std_verify(json.dumps([c], ensure_ascii=False))
    res = r["results"][0]
    vs = res.get("violations", [])
    ab = r.get("abstained", {})
    return {
        "ok": True,
        "asset_id": asset_id,
        "conclusion": "不可入库" if vs else "可入库",
        "n_violations": len(vs),
        "violations": [{
            "clause_id": v.get("clause_id"),
            "source": v.get("source"),
            "clause_text": v.get("clause_text"),
            "message": v.get("message"),
            "evidence": v.get("evidence"),
        } for v in vs],
        "abstained": {
            "n": ab.get("n_abstained_clauses"),
            "by_clause": list((ab.get("by_clause") or {}).keys()),
            "note": r.get("skipped_note"),
        },
        "next": ("修正上述字段后重新校验" if vs else "可进入入库流程（asset_ingest）"),
        "fields": {k: c.get(k) for k in ("equipment_class", "maintenance_cycle_days",
                                         "check_interval_hours", "equipment_no",
                                         "owner_dept", "secrecy") if k in c},
    }


def kg_payload(asset_id: str) -> dict:
    if not os.path.exists(KG_PATH):
        return {"nodes": [], "edges": []}
    with open(KG_PATH, encoding="utf-8") as f:
        g = json.load(f)
    aid = f"asset:{asset_id}"
    keep = {aid}
    edges = []
    for e in g["edges"]:
        if e["s"] == aid and e["p"] in ("引用", "来源", "涉及", "归属"):
            edges.append(e)
            keep.add(e["o"])

    # ★ 把**实际命中的条款**接进图：这是决策要看的证据链。
    #   只画"资产→标准/文档/设备"是不够的——评委要问"凭什么判它不行"。
    try:
        svc()
        import evostd.server as S

        a = next((x for x in _ASSETS if _id(x) == asset_id), None)
        if a is not None:
            r = S.std_verify(json.dumps([_clean(a)], ensure_ascii=False))
            for v in r["results"][0].get("violations", []):
                cnode = f"clause:{v.get('clause_id')}"
                keep.add(cnode)
                edges.append({"s": aid, "p": "命中", "o": cnode})
                for e in g["edges"]:
                    if e["p"] == "含条款" and e["o"] == cnode:
                        edges.append(e)
                        keep.add(e["s"])
    except Exception as exc:
        sys.stderr.write(f"[webui] 命中条款注入失败：{type(exc).__name__}: {exc}\n")

    nodes = [n for n in g["nodes"] if n["id"] in keep]
    return {"nodes": nodes, "edges": edges}


def stats_payload() -> dict:
    svc()
    import evostd.server as S

    cl = S.std_clause_list()
    an = S.std_hard_anchors()
    cap = S.std_capability()
    return {
        "n_tools": cap.get("n_tools"),
        "fingerprint": cap.get("fingerprint"),
        "n_clauses": cl.get("stats", {}).get("by_status", {}).get("adopted"),
        "n_uncompilable": cl.get("stats", {}).get("by_status", {}).get("uncompilable"),
        "n_anchors": an.get("n"),
        "n_assets": len(_ASSETS or []),
        "n_skills": 5,
    }


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<link rel="icon" href="data:,">
<title>标准驱动 · 可进化决策工作台</title>
<style>
  :root{--bg:#f6f8fa;--card:#fff;--line:#d0d7de;--ink:#1f2328;--muted:#57606a;
        --red:#cf222e;--green:#1a7f37;--blue:#0969da;--amber:#9a6700}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif}
  header{background:#fff;border-bottom:1px solid var(--line);padding:14px 22px;
         display:flex;align-items:baseline;gap:18px;flex-wrap:wrap}
  header h1{font-size:19px;margin:0}
  header .sub{color:var(--muted);font-size:13px}
  .stats{margin-left:auto;display:flex;gap:16px;font-size:13px;color:var(--muted)}
  .stats b{color:var(--ink);font-size:15px}
  main{display:grid;grid-template-columns:300px 1fr 360px;gap:14px;padding:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
        overflow:hidden;display:flex;flex-direction:column;min-height:0}
  .card h2{font-size:14px;margin:0;padding:11px 14px;border-bottom:1px solid var(--line);
           color:var(--muted);font-weight:600}
  .list{overflow:auto;max-height:calc(100vh - 150px)}
  .item{padding:10px 14px;border-bottom:1px solid #eaeef2;cursor:pointer;font-size:13px}
  .item:hover{background:#f6f8fa}
  .item.active{background:#ddf4ff;border-left:3px solid var(--blue)}
  .item .id{font-weight:600}
  .item .tag{float:right;font-size:11px;padding:1px 6px;border-radius:10px}
  .tag.ok{background:#dafbe1;color:var(--green)}
  .tag.bad{background:#ffebe9;color:var(--red)}
  .body{padding:16px 18px;overflow:auto;max-height:calc(100vh - 150px)}
  .verdict{font-size:26px;font-weight:700;margin:4px 0 2px}
  .verdict.bad{color:var(--red)} .verdict.ok{color:var(--green)}
  .muted{color:var(--muted);font-size:13px}
  table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}
  th,td{border-bottom:1px solid #eaeef2;padding:8px 6px;text-align:left;vertical-align:top}
  th{color:var(--muted);font-weight:600;background:#f6f8fa}
  code{background:#f6f8fa;padding:1px 5px;border-radius:5px;font-size:12px}
  .box{margin-top:14px;padding:12px 14px;border-radius:8px;font-size:13px;line-height:1.6}
  .box.abstain{background:#fff8c5;border:1px solid #d4a72c55}
  .box.next{background:#ddf4ff;border:1px solid #54aeff55}
  .box.honest{background:#f6f8fa;border:1px dashed var(--line);color:var(--muted)}
  svg{width:100%;height:calc(100vh - 250px)}
  footer{color:var(--muted);font-size:12px;padding:0 22px 14px}
</style></head>
<body>
<header>
  <h1>标准驱动 · 可进化决策工作台</h1>
  <span class="sub">判定全部来自 MCP 工具；模型只做翻译</span>
  <div class="stats" id="stats"></div>
</header>
<main>
  <section class="card"><h2>数据资产（点击查看决策）</h2><div class="list" id="list"></div></section>
  <section class="card"><h2>决策记录</h2><div class="body" id="decision">
    <p class="muted">从左边选一条资产。</p></div></section>
  <section class="card"><h2>证据链</h2><div class="body" id="graph"></div></section>
</main>
<footer>弃权可见：判不了会明确写"弃权"，不会用"通过"掩盖。｜数据为演示样例，非真实标准。</footer>
<script>
const $ = (s)=>document.querySelector(s);
async function j(u){const r=await fetch(u);return r.json()}
function esc(s){return String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function boot(){
  const s = await j('/api/stats');
  $('#stats').innerHTML = `<span>MCP 工具 <b>${s.n_tools}</b></span>
    <span>判据 <b>${s.n_clauses}</b></span><span>硬锚点 <b>${s.n_anchors}</b></span>
    <span>资产 <b>${s.n_assets}</b></span><span>技能 <b>${s.n_skills}</b></span>`;
  const list = await j('/api/assets');
  $('#list').innerHTML = list.map(a=>`
    <div class="item" data-id="${esc(a.id)}">
      <span class="tag ${a.compliant?'ok':'bad'}">${a.compliant?'合规':'违例 '+a.n_violations}</span>
      <div class="id">${esc(a.id)}</div>
      <div class="muted">${esc(a.why||'').slice(0,26)}</div>
    </div>`).join('');
  document.querySelectorAll('.item').forEach(el=>el.onclick=()=>pick(el.dataset.id, el));
  // 默认选中第一条**有违例**的资产：演示时先看到判定，而不是先看到"合规"
  const first = list.find(a=>!a.compliant) || list[0];
  if(first) pick(first.id, document.querySelector(`.item[data-id="${first.id}"]`));
}
async function pick(id, el){
  document.querySelectorAll('.item').forEach(x=>x.classList.remove('active'));
  if(el) el.classList.add('active');
  const d = await j('/api/decision?id='+encodeURIComponent(id));
  const bad = !d.violations.length;
  let html = `<div class="muted">${esc(d.asset_id)}</div>
    <div class="verdict ${bad?'ok':'bad'}">${esc(d.conclusion)}</div>
    <div class="muted">命中 ${d.n_violations} 条判据 · 字段：${esc(JSON.stringify(d.fields))}</div>`;
  if(d.violations.length){
    html += `<table><tr><th>条款</th><th>原文</th><th>命中信息</th><th>证据</th></tr>` +
      d.violations.map(v=>`<tr><td><b>${esc(v.clause_id)}</b><div class="muted">${esc(v.source||'')}</div></td>
        <td>${esc(v.clause_text||'')}</td><td>${esc(v.message||'')}</td>
        <td><code>${esc(JSON.stringify(v.evidence))}</code></td></tr>`).join('') + `</table>`;
  }
  const ab = d.abstained||{};
  html += `<div class="box abstain"><b>未决（弃权）</b>：${ab.n?('本次有 '+ab.n+' 条判据弃权：'+ab.by_clause.join('、')):'本次无弃权'}<br>
    <span class="muted">${esc(String(ab.note||'').slice(0,220))}</span></div>
    <div class="box next"><b>下一步</b>：${esc(d.next)}</div>
    <div class="box honest">判定由 std_verify 给出；本页不计算任何分数，也不调用模型。</div>`;
  $('#decision').innerHTML = html;
  const g = await j('/api/kg?id='+encodeURIComponent(id));
  $('#graph').innerHTML = drawGraph(g, id);
}
function drawGraph(g, id){
  if(!g.nodes.length) return '<p class="muted">未找到图谱数据（先跑 tools/kg_export.py）。</p>';
  const W=340,H=520, cx=170, cy=250;
  const others = g.nodes.filter(n=>n.id!=='asset:'+id);
  const pos = others.map((n,i)=>{
    const ang = (i/Math.max(others.length,1))*Math.PI*2 - Math.PI/2;
    return {n, x: cx+Math.cos(ang)*118, y: cy+Math.sin(ang)*175};
  });
  const map = new Map(pos.map(p=>[p.n.id,p]));
  const color = {'引用':'#54aeff','来源':'#8c959f','涉及':'#8c959f','归属':'#8c959f',
                 '含条款':'#4ac26b','查表':'#c297ff','命中':'#cf222e'};
  let svg = `<svg viewBox="0 0 ${W} ${H}">`;
  g.edges.forEach(e=>{
    const a = e.s==='asset:'+id? {x:cx,y:cy} : map.get(e.s);
    const b = map.get(e.o) || (e.o==='asset:'+id?{x:cx,y:cy}:null);
    if(!a||!b) return;
    svg += `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${color[e.p]||'#8c959f'}"
      stroke-width="1.4" ${e.p==='引用'?'stroke-dasharray="4 3"':''}/>`;
  });
  const short = (s,n)=> String(s).length>n ? String(s).slice(0,n)+'…' : String(s);
  pos.forEach(p=>{
    svg += `<circle cx="${p.x}" cy="${p.y}" r="16" fill="#fff" stroke="${color[g.edges.find(e=>e.o===p.n.id)?.p]||'#8c959f'}"/>`;
    const label = short(p.n.id.split(':').slice(1).join(':'), 14);
    svg += `<text x="${p.x}" y="${p.y+31}" text-anchor="middle" font-size="9" fill="#1f2328">${esc(label)}</text>`;
  });
  svg += `<circle cx="${cx}" cy="${cy}" r="26" fill="#ffebe9" stroke="#ff8182" stroke-width="1.6"/>`;
  svg += `<text x="${cx}" y="${cy+44}" text-anchor="middle" font-size="10" font-weight="600">${esc(id)}</text>`;
  svg += `</svg><p class="muted" style="text-align:center;font-size:12px;margin:6px 0 0">
    蓝色虚线=引用标准　灰色=来源/涉及/归属　绿色=条款归属</p>`;
  return svg;
}
boot();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif u.path == "/api/stats":
                self._json(stats_payload())
            elif u.path == "/api/assets":
                self._json(assets_payload())
            elif u.path == "/api/decision":
                self._json(decision_payload(q.get("id", [""])[0]))
            elif u.path == "/api/kg":
                self._json(kg_payload(q.get("id", [""])[0]))
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as exc:            # 出错必须可见，不能静默
            import traceback
            traceback.print_exc()
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[webui] " + (fmt % args) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    os.environ.setdefault("EVOSTD_NOW", "2026-10-21")
    svc()
    print(f"[webui] 决策工作台已装配（domain={DOMAIN}），"
          f"http://{args.host}:{args.port}", flush=True)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
