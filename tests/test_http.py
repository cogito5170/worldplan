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
        for path, needle in (("/", b"World Schedule Planner"), ("/app.js", b"/api/plan"),
                             ("/app.css", b"--bg"), ("/sample.json", b"kickoff")):
            st, h, b = self.req(path)
            self.assertEqual(st, 200, path); self.assertIn(needle, b)
            self.assertIn("default-src 'self'", h["Content-Security-Policy"])

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
        st, _, _ = self.req("/api/plan", b"x" * 1_000_001)
        self.assertEqual(st, 413)


if __name__ == "__main__":
    unittest.main()
