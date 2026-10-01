"""명령줄.

    worldplan plan  <요청.json>                 계획 + 판정 (종료 코드 0=ACCEPT 1=REJECT)
    worldplan verify <요청.json> <배정.json>     판정만
    worldplan serve [--host H] [--port P]       웹 화면 + REST + MCP(HTTP)
    worldplan mcp                               MCP stdio 서버
    worldplan ledger                            원장 사슬 검사 (0=성함 1=끊김)
"""
from __future__ import annotations

import argparse
import json
import sys

from . import engine


def _load(p):
    with (sys.stdin if p == "-" else open(p, encoding="utf-8")) as f:
        return json.load(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="worldplan", description="세계 일정 계획 엔진")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plan"); a.add_argument("request")
    a = sub.add_parser("verify"); a.add_argument("request"); a.add_argument("assignments")
    a = sub.add_parser("serve"); a.add_argument("--host"); a.add_argument("--port", type=int)
    sub.add_parser("mcp")
    sub.add_parser("ledger")
    args = ap.parse_args(argv)

    if args.cmd == "plan":
        r = engine.plan(_load(args.request))
    elif args.cmd == "verify":
        r = engine.verify(_load(args.request), _load(args.assignments))
    elif args.cmd == "serve":
        from .http_server import serve
        serve(args.host, args.port)
        return 0
    elif args.cmd == "mcp":
        from .mcp import run_stdio
        run_stdio()
        return 0
    else:
        r = engine.ledger_status(10)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["chain"]["ok"] else 1
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r.get("verdict") == "ACCEPT" else 1
