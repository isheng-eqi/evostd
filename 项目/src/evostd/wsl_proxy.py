#!/usr/bin/env python3
"""在 WSL 内转发端口，让 Docker 容器能够访问跑在 Windows 上的 evostd MCP 服务。

为什么要这个东西（本机实测结论）：
  · 本机 WSL 是**镜像网络模式**：WSL 宿主用 127.0.0.1 就能连到 Windows 上的服务
  · 但 Docker 容器**不行**——容器只能到达 WSL 宿主的 docker 网桥网关 172.18.0.1
  · 而 WSL 里没有 pip / uv / ensurepip（裸 Python 3.14），装不了 nexent 依赖

所以：在 WSL 里起一个**纯标准库**的 TCP 转发（监听 0.0.0.0:<listen> →
转发到 127.0.0.1:<target>），容器就能通过 172.18.0.1:<listen> 访问到 Windows 上的 MCP 服务。

跑法（WSL 内）：
    python3 /mnt/c/.../evostd/wsl_proxy.py --listen 8771 --target 8770
"""
from __future__ import annotations

import argparse
import selectors
import socket
import sys
import threading
import time

# ★ 统计：**这个转发器此前完全没有诊断** ——
#   没有连接计数、没有心跳，连"它是否还活着并在转发"都判断不了。
#   笔记里记过"半死的 WSL 端口中继"：进程还在、端口还占着、但转发已经不通，
#   而外面只能看到"平台连不上 MCP"，看不出是哪一段断了。
#   下面这几个计数器 + 心跳，就是为了让"半死"变成"能看出来"。
_STAT = {"accepted": 0, "upstream_fail": 0, "bytes": 0, "closed": 0}
_LOCK = threading.Lock()


def _bump(key: str, n: int = 1) -> None:
    with _LOCK:
        _STAT[key] = _STAT.get(key, 0) + n


def _heartbeat(every: float) -> None:
    """周期性打一行心跳。**日志停了 = 转发器死了**，一眼可辨。"""
    while True:
        time.sleep(every)
        with _LOCK:
            snap = dict(_STAT)
        print(f"[proxy][hb] accepted={snap['accepted']} relayed={snap['closed']} "
              f"upstream_fail={snap['upstream_fail']} bytes={snap['bytes']}", flush=True)


def pipe(src: socket.socket, dst: socket.socket, sel: selectors.BaseSelector) -> None:
    """把 src 的数据搬到 dst，直到一端关闭。"""
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                sel.unregister(s)
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass


def handle(client: socket.socket, target_host: str, target_port: int) -> None:
    try:
        upstream = socket.create_connection((target_host, target_port), timeout=10)
    except OSError as e:
        _bump("upstream_fail")
        # ★ 上游连不上**必须打出来**：这是"转发器活着但目标死了"的唯一信号。
        print(f"[proxy] ★ 连不上上游 {target_host}:{target_port} -> {e}", file=sys.stderr)
        client.close()
        return
    _bump("accepted")
    upstream.settimeout(None)
    client.settimeout(None)
    t = threading.Thread(target=_one_way, args=(upstream, client), daemon=True)
    t.start()
    _one_way(client, upstream)


def _one_way(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            _bump("bytes", len(data))
            b.sendall(data)
    except OSError:
        # 连接中断在转发器里是常态（客户端提前断开），不必告警——
        # 但**计数**要有，否则分不清"没流量"和"流量全失败"。
        _bump("upstream_fail")
    finally:
        _bump("closed")
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, default=8771)
    ap.add_argument("--target-host", default="127.0.0.1")
    ap.add_argument("--target", type=int, default=8770)
    ap.add_argument("--heartbeat", type=float, default=60.0,
                    help="心跳间隔秒；0 表示不打（默认 60）")
    args = ap.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.listen))
    srv.listen(64)
    print(f"[proxy] 0.0.0.0:{args.listen}  ->  {args.target_host}:{args.target}", flush=True)
    if args.heartbeat > 0:
        threading.Thread(target=_heartbeat, args=(args.heartbeat,), daemon=True).start()
        print(f"[proxy] 心跳每 {args.heartbeat:g}s 一行（日志停了就说明转发器死了）", flush=True)
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle, args=(conn, args.target_host, args.target),
                         daemon=True).start()


if __name__ == "__main__":
    main()
