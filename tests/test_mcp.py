"""MCP 를 진짜 프로세스로 띄워 진짜 메시지를 주고받는다(stdio). 함수 호출 흉내가 아니다."""
import json
import os
import subprocess
import sys
import unittest

from tests._util import ROOT, sample


class Stdio(unittest.TestCase):
    def test_왕복(self):
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "plan_world_schedule", "arguments": {"request": sample()}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "verify_world_schedule",
                        "arguments": {"request": sample(), "assignments": {"kickoff": "2026-10-05T22:00+09:00"}}}},
            {"jsonrpc": "2.0", "id": 5, "method": "nope"},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "rm_rf", "arguments": {}}},
        ]
        p = subprocess.run([sys.executable, "-m", "worldplan", "mcp"], cwd=ROOT, env=os.environ.copy(),
                           input="\n".join(json.dumps(m) for m in msgs) + "\nnot json\n",
                           capture_output=True, text=True, timeout=60)
        out = [json.loads(x) for x in p.stdout.splitlines() if x.strip()]
        by = {m.get("id"): m for m in out}
        self.assertEqual(len(out), 7, p.stdout + p.stderr)        # 알림에는 답하지 않는다
        self.assertEqual(by[1]["result"]["protocolVersion"], "2025-06-18")
        names = {t["name"] for t in by[2]["result"]["tools"]}
        self.assertEqual(names, {"plan_world_schedule", "verify_world_schedule", "find_common_slots",
                                 "world_clock", "ledger_status", "describe_judge"})
        r3 = by[3]["result"]
        self.assertFalse(r3["isError"])
        self.assertEqual(r3["structuredContent"]["verdict"], "ACCEPT")
        self.assertEqual(json.loads(r3["content"][0]["text"])["verdict"], "ACCEPT")
        self.assertEqual(by[4]["result"]["structuredContent"]["verdict"], "REJECT")
        self.assertEqual(by[5]["error"]["code"], -32601)
        self.assertEqual(by[6]["error"]["code"], -32602)
        self.assertEqual(by[None]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
