"""HTTP 서버를 진짜 포트에 띄워 웹 화면 · REST · MCP · 보안 경계를 본다."""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests._util import sample
from worldplan.http_server import Handler


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def req(self, path, body=None, headers=None, method=None):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                   headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_화면과_정적_파일(self):
        # 화면은 gentleMonster 의 것 -- 여기서는 가짜 화면 폴더를 붙여 내주는 길만 본다
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        (d / "index.html").write_text("<h1>worldplan</h1>"); (d / "app.js").write_text("/api/assistant")
        (d / ".source.json").write_text("{}")
        old = os.environ.get("WORLDPLAN_FRONTEND")
        try:
            os.environ.pop("WORLDPLAN_FRONTEND", None)
            st, _, b = self.req("/")
            self.assertEqual(st, 200); self.assertIn("worldplan app".encode(), b)          # 안내 페이지
            os.environ["WORLDPLAN_FRONTEND"] = str(d)
            for path, needle in (("/", b"<h1>worldplan</h1>"), ("/app.js", b"/api/assistant"), ("/sample.json", b"kickoff")):
                st, h, b = self.req(path)
                self.assertEqual(st, 200, path); self.assertIn(needle, b)
                self.assertIn("default-src 'self'", h["Content-Security-Policy"])
            for bad in ("/.source.json", "/../../etc/passwd", "/%2e%2e/x"):
                self.assertEqual(self.req(bad)[0], 404, bad)                                  # 숨김 · 폴더 밖은 안 낸다
            self.assertEqual(self.req("/api/nothing")[0], 404)
        finally:
            if old is None:
                os.environ.pop("WORLDPLAN_FRONTEND", None)
            else:
                os.environ["WORLDPLAN_FRONTEND"] = old

    def test_api_plan(self):
        st, _, b = self.req("/api/plan", {"request": sample()})
        self.assertEqual(st, 200); self.assertEqual(json.loads(b)["verdict"], "ACCEPT")

    def test_mcp_http(self):
        st, h, b = self.req("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                "clientInfo": {"name": "t", "version": "0"}}},
                            headers={"Accept": "application/json, text/event-stream"})
        self.assertEqual(st, 200); self.assertIn("Mcp-Session-Id", h)
        self.assertEqual(json.loads(b)["result"]["protocolVersion"], "2025-03-26")
        st, _, _ = self.req("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(st, 202)
        st, _, b = self.req("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                     "params": {"name": "world_clock", "arguments": {
                                         "instant": "2026-10-05T10:00Z", "zones": ["Asia/Seoul", "America/New_York"]}}})
        z = json.loads(b)["result"]["structuredContent"]["zones"]
        self.assertEqual([x["local"] for x in z], ["2026-10-05T19:00+09:00", "2026-10-05T06:00-04:00"])
        self.assertEqual(self.req("/mcp")[0], 405)
        self.assertEqual(self.req("/mcp", b"{bad", )[0], 400)
        self.assertEqual(self.req("/mcp", {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                                  headers={"MCP-Protocol-Version": "1999-01-01"})[0], 400)

    def test_다른_출처는_막는다(self):
        st, _, _ = self.req("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                            headers={"Origin": "http://evil.example"})
        self.assertEqual(st, 403)
        st, _, _ = self.req("/api/plan", {"request": sample()}, headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(st, 200)

    def test_토큰(self):
        os.environ["WORLDPLAN_TOKEN"] = "s3cret"
        try:
            self.assertEqual(self.req("/api/plan", {"request": sample()})[0], 401)
            self.assertEqual(self.req("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                      headers={"Authorization": "Bearer nope"})[0], 401)
            st, _, b = self.req("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                headers={"Authorization": "Bearer s3cret"})
            self.assertEqual(st, 200)
            self.assertEqual(self.req("/")[0], 200)          # 화면 자체는 열린다(토큰 칸이 있다)
        finally:
            del os.environ["WORLDPLAN_TOKEN"]

    def test_본문_상한(self):
        # 서버는 Content-Length 만 보고 본문을 읽기 전에 413 으로 닫는다. 본문을 다 밀어 넣으면 클라이언트 쪽이
        # 쓰다가 끊겨(Broken pipe) 시점에 따라 실패했다 -- 그래서 머리만 보내고 답을 읽는다
        import socket
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as c:
            c.sendall(b"POST /api/plan HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                      b"Content-Length: 1000001\r\n\r\n" + b"x" * 1000)
            head = c.recv(200)
        self.assertTrue(head.startswith(b"HTTP/1.1 413"), head)


if __name__ == "__main__":
    unittest.main()
