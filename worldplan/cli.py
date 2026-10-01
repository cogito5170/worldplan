"""명령줄.

    worldplan plan  <요청.json>                 계획 + 판정 (종료 코드 0=ACCEPT 1=REJECT)
    worldplan verify <요청.json> <배정.json>     판정만
    worldplan app [--frontend 폴더] [--ref main] [--update] [--no-open] [--llm auto|claude|gemini]
                                                어플리케이션: 화면(gentleMonster apps/worldplan)을 받아 붙이고 브라우저를 연다
    worldplan serve [--host H] [--port P] [--frontend 폴더]   엔진(API + MCP) -- 화면은 폴더를 줄 때만
    worldplan ask "말" [--llm ...]               도우미에 한 번 묻기(터미널에서)
    worldplan mcp                               MCP stdio 서버
    worldplan ledger                            원장 사슬 검사 (0=성함 1=끊김)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading

from . import engine


def _load(p):
    with (sys.stdin if p == "-" else open(p, encoding="utf-8")) as f:
        return json.load(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="worldplan", description="세계 일정 계획 엔진")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plan"); a.add_argument("request")
    a = sub.add_parser("verify"); a.add_argument("request"); a.add_argument("assignments")
    a = sub.add_parser("serve"); a.add_argument("--host"); a.add_argument("--port", type=int); a.add_argument("--frontend")
    a.add_argument("--llm", help="auto | claude | gemini | claude,gemini (도우미의 숙고층)")
    a = sub.add_parser("app"); a.add_argument("--host"); a.add_argument("--port", type=int); a.add_argument("--frontend")
    a.add_argument("--ref", default="main"); a.add_argument("--update", action="store_true")
    a.add_argument("--no-open", action="store_true"); a.add_argument("--llm")
    a = sub.add_parser("ask"); a.add_argument("q"); a.add_argument("--llm"); a.add_argument("--request")
    sub.add_parser("mcp")
    sub.add_parser("ledger")
    args = ap.parse_args(argv)
    if getattr(args, "llm", None):
        os.environ["WORLDPLAN_LLM"] = args.llm

    if args.cmd == "plan":
        r = engine.plan(_load(args.request))
    elif args.cmd == "verify":
        r = engine.verify(_load(args.request), _load(args.assignments))
    elif args.cmd == "serve":
        from .http_server import serve
        serve(args.host, args.port, args.frontend)
        return 0
    elif args.cmd == "app":
        return _app(args)
    elif args.cmd == "ask":
        from . import assistant
        from .http_server import STATIC
        req = _load(args.request) if args.request else json.loads((STATIC / "sample.json").read_text(encoding="utf-8"))
        r = assistant.default().ask(args.q, req)
        print(r.get("answer", ""))
        print(f"-- {r.get('route')} · 토큰 {r['tokens']['total']:,} · {r['ms']:.0f} ms", file=sys.stderr)
        return 1 if r.get("error") else 0
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


def _app(args) -> int:
    """화면을 붙인 엔진 하나 -- 어플리케이션."""
    from pathlib import Path
    from . import assistant, frontend
    from .http_server import make_server
    fd = args.frontend or os.environ.get("WORLDPLAN_FRONTEND")
    if not fd:
        try:
            fd = str(frontend.fetch(args.ref, update=args.update, log=lambda m: print(m, file=sys.stderr)))
        except frontend.FetchError as e:
            print(f"[worldplan] {e}", file=sys.stderr)
            print("[worldplan] gentleMonster 를 받아 두었다면 --frontend <gentleMonster>/gentle_monster/apps/worldplan", file=sys.stderr)
            return 2
    if not frontend.complete(Path(fd)):
        print(f"[worldplan] {fd} 에 화면 파일({', '.join(frontend.NEED)})이 다 있지 않다", file=sys.stderr)
        return 2
    httpd = make_server(args.host, args.port, fd)
    h, p = httpd.server_address[:2]
    url = f"http://{'127.0.0.1' if h in ('0.0.0.0', '') else h}:{p}/"
    try:
        llms = ", ".join(x.name for x in assistant.default().llms) or "없음(물어보기는 시각 변환만)"
    except ValueError as e:
        print(f"[worldplan] {e}", file=sys.stderr)
        return 2
    print(f"[worldplan] 어플리케이션: {url}  (화면 {fd} · 도우미 LLM {llms} · MCP {url}mcp) -- 끄려면 Ctrl+C", file=sys.stderr)
    if not args.no_open:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
