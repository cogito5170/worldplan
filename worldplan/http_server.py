"""HTTP 서버 -- 웹 프론트엔드 + REST API + MCP(Streamable HTTP) 를 한 포트에.

    GET  /...            화면(프론트엔드) 폴더의 파일 -- WORLDPLAN_FRONTEND 또는 --frontend. 없으면 / 가 안내 페이지.
                         화면은 gentleMonster 의 gentle_monster/apps/worldplan/ 이다(`worldplan app` 이 받아 붙인다)
    GET  /sample.json    예시 요청(엔진의 것)
    GET  /healthz        살아 있나 + 원장 사슬이 성한가
    POST /api/plan       {request}
    POST /api/verify     {request, assignments, claimed_cost?}
    POST /api/slots      find_common_slots 인자
    POST /api/clock      {instant, zones}
    POST /api/assistant  {q, request?}  자연어 도우미(WALP 앞단 + Claude) -- assistant.py
    GET  /api/ledger     원장 상태
    GET  /api/describe   심판이 재는 것 / 못 재는 것
    POST /mcp            MCP JSON-RPC (Streamable HTTP, JSON 응답)

환경 변수
    WORLDPLAN_HOST              기본 127.0.0.1 (밖으로 열려면 0.0.0.0 -- 그때는 토큰을 세워라)
    WORLDPLAN_PORT              기본 8765
    WORLDPLAN_TOKEN             세우면 /api · /mcp 에 Authorization: Bearer <토큰> 이 필요하다
    WORLDPLAN_ALLOWED_ORIGINS   쉼표로. 브라우저 Origin 헤더 허용 목록(같은 호스트는 늘 허용)
    WORLDPLAN_LEDGER_ROOT       원장 자리(기본 ./data)
    WORLDPLAN_FRONTEND          화면 폴더
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import engine, ledger, mcp

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 1_000_000
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json",
        ".md": "text/plain; charset=utf-8"}
NO_FRONTEND = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>worldplan engine</title></head><body>
<h1>worldplan 엔진은 돌고 있다</h1>
<p>화면(프론트엔드)이 붙지 않았다. 화면은 gentleMonster 저장소의 <code>gentle_monster/apps/worldplan/</code> 이다.</p>
<p><code>worldplan app</code> 으로 띄우면 받아 와서 붙인다. 이미 있으면 <code>WORLDPLAN_FRONTEND=&lt;그 폴더&gt;</code>.</p>
<p>API 와 MCP 는 지금도 쓸 수 있다: <code>POST /api/plan</code> · <code>POST /mcp</code>.</p></body></html>"""


def frontend_dir() -> "Path | None":
    d = os.environ.get("WORLDPLAN_FRONTEND")
    if not d:
        return None
    p = Path(d).resolve()
    return p if (p / "index.html").is_file() else None


class Handler(BaseHTTPRequestHandler):
    server_version = "worldplan/" + engine.VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 요청 본문은 남기지 않는다 -- 사람의 일정이 들어 있다
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---- 공통 ----
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
                         "frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), extra=extra)

    def _origin_ok(self) -> bool:
        o = self.headers.get("Origin")
        if not o:
            return True
        host = self.headers.get("Host", "")
        allowed = {f"http://{host}", f"https://{host}"}
        allowed |= {x.strip() for x in os.environ.get("WORLDPLAN_ALLOWED_ORIGINS", "").split(",") if x.strip()}
        return o in allowed

    def _auth_ok(self) -> bool:
        tok = os.environ.get("WORLDPLAN_TOKEN")
        if not tok:
            return True
        got = self.headers.get("Authorization", "")
        return hmac.compare_digest(got.encode(), f"Bearer {tok}".encode())

    def _guard(self) -> bool:
        if not self._origin_ok():
            self._json(403, {"error": "Origin 이 허용 목록에 없다"})
            return False
        if not self._auth_ok():
            self._json(401, {"error": "토큰이 필요하다 (Authorization: Bearer ...)"},
                       extra={"WWW-Authenticate": "Bearer"})
            return False
        return True

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self.close_connection = True
            self._json(413, {"error": f"본문은 {MAX_BODY} 바이트 이하"})
            return False, None
        raw = self.rfile.read(n)
        try:
            return True, json.loads(raw.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError):
            if self.path.startswith("/mcp"):
                self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            else:
                self._json(400, {"error": "JSON 이 아니다"})
            return False, None

    # ---- 메서드 ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/sample.json":
            self._send(200, (STATIC / "sample.json").read_bytes(), MIME[".json"])
        elif path == "/healthz":
            self._json(200, {"ok": True, "engine": engine.VERSION, "ledger": ledger.verify()})
        elif path == "/api/ledger":
            if self._guard():
                self._json(200, engine.ledger_status(20))
        elif path == "/api/describe":
            if self._guard():
                self._json(200, engine.describe())
        elif path == "/mcp":
            self._json(405, {"error": "이 서버는 서버발 SSE 스트림을 열지 않는다 -- POST 를 써라"},
                       extra={"Allow": "POST"})
        elif path.startswith("/api/"):
            self._json(404, {"error": "없다"})
        else:
            self._static(path)

    def _static(self, path: str):
        root = frontend_dir()
        if root is None:
            if path in ("/", "/index.html"):
                self._send(200, NO_FRONTEND.encode("utf-8"), MIME[".html"])
            else:
                self._json(404, {"error": "없다 (프론트엔드가 붙지 않았다)"})
            return
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        f = (root / rel).resolve()
        # 폴더 밖(../)과 숨김 파일(.source.json 등)은 내주지 않는다
        if root not in f.parents or not f.is_file() or any(p.startswith(".") for p in Path(rel).parts):
            self._json(404, {"error": "없다"})
            return
        ctype = MIME.get(f.suffix) or mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self._send(200, f.read_bytes(), ctype, {"Cache-Control": "no-cache"} if f.name == "index.html" else None)

    def do_DELETE(self):
        self._json(405, {"error": "세션 상태를 두지 않는다"}, extra={"Allow": "POST"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/mcp", "/api/plan", "/api/verify", "/api/slots", "/api/clock", "/api/assistant"):
            self._json(404, {"error": "없다"})
            return
        if not self._guard():
            return
        ok, body = self._body()
        if not ok:
            return
        if path == "/mcp":
            return self._mcp(body)
        if not isinstance(body, dict):
            self._json(400, {"error": "JSON 객체가 필요하다"})
            return
        if path == "/api/plan":
            self._json(200, engine.plan(body.get("request")))
        elif path == "/api/verify":
            self._json(200, engine.verify(body.get("request"), body.get("assignments"), body.get("claimed_cost")))
        elif path == "/api/slots":
            self._json(200, engine.common_slots(body))
        elif path == "/api/clock":
            self._json(200, engine.world_clock(body.get("instant"), body.get("zones")))
        elif path == "/api/assistant":
            q = body.get("q")
            if not isinstance(q, str) or not q.strip() or len(q) > 2000:
                self._json(400, {"error": "q: 1~2000 글자의 말"})
                return
            req = body.get("request") if isinstance(body.get("request"), dict) else None
            from . import assistant
            self._json(200, assistant.default().ask(q, req))

    def _mcp(self, msg):
        pv = self.headers.get("MCP-Protocol-Version")
        if pv and pv not in mcp.SUPPORTED:
            self._json(400, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": f"Unsupported MCP-Protocol-Version {pv}"}})
            return
        resp = mcp.handle(msg)
        if resp is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        extra = {}
        if isinstance(msg, dict) and msg.get("method") == "initialize":
            extra["Mcp-Session-Id"] = uuid.uuid4().hex   # 상태는 없다. 클라이언트 호환용
        self._json(200, resp, extra=extra)


def make_server(host=None, port=None, frontend=None) -> ThreadingHTTPServer:
    if frontend:
        os.environ["WORLDPLAN_FRONTEND"] = str(frontend)
    host = host or os.environ.get("WORLDPLAN_HOST", "127.0.0.1")
    port = int(port if port is not None else os.environ.get("WORLDPLAN_PORT", "8765"))
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("WORLDPLAN_TOKEN"):
        sys.stderr.write("경고: 밖으로 열린 주소인데 WORLDPLAN_TOKEN 이 없다 -- 누구나 원장에 쓸 수 있다\n")
    if os.environ.get("WORLDPLAN_FRONTEND") and frontend_dir() is None:
        sys.stderr.write(f"경고: WORLDPLAN_FRONTEND={os.environ['WORLDPLAN_FRONTEND']} 에 index.html 이 없다 -- 안내 페이지를 낸다\n")
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def serve(host=None, port=None, frontend=None):
    httpd = make_server(host, port, frontend)
    h, p = httpd.server_address[:2]
    sys.stderr.write(f"worldplan {engine.VERSION} -- http://{h}:{p}/  (MCP: POST /mcp · 화면: {frontend_dir() or '없음'})\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
