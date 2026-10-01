"""숙고층 -- Claude CLI · Gemini CLI 를 **진짜 하위 프로세스로** 부른다(가짜 실행 파일을 PATH 앞에 둔다).

가짜는 받은 인자 · 환경 · 작업 폴더의 설정 파일을 적어 두고, 그 CLI 의 실제 JSON 꼴로 답한다
(claude: usage · total_cost_usd / gemini 0.62: response · stats.models.*.tokens). 실제 Gemini 를 부르는 길은
키가 없어서 여기서 못 잰다 -- README 에 그렇게 적었다.
"""
import datetime as dt
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from tests._util import sample
from worldplan import assistant as A

NOW = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.timezone.utc)

FAKE = r'''#!/usr/bin/env python3
import json, os, sys, pathlib
log = pathlib.Path(os.environ["FAKE_LOG"])
rec = {"argv": sys.argv[1:], "cwd": os.getcwd(), "sysmd": os.environ.get("GEMINI_SYSTEM_MD")}
st = pathlib.Path(".gemini/settings.json")
if st.exists():
    rec["settings"] = json.loads(st.read_text())
if rec["sysmd"]:
    rec["system"] = pathlib.Path(rec["sysmd"]).read_text()
log.write_text(json.dumps(rec))
mode = os.environ.get("FAKE_MODE", "ok")
name = os.path.basename(sys.argv[0])
if mode == "auth" and name == "gemini":
    # 실측(gemini 0.62): 오류 JSON 은 stdout 이 아니라 stderr 에, 종료 코드 41
    print(json.dumps({"session_id": "x", "error": {"type": "Error", "message": "Please set an Auth method ...", "code": 41}}, indent=2), file=sys.stderr)
    sys.exit(41)
if name == "gemini":
    print(json.dumps({"session_id": "x", "response": "가짜 제미나이 답", "stats": {"models": {"gemini-2.5-pro": {
        "api": {"totalRequests": 2, "totalErrors": 0, "totalLatencyMs": 1234},
        "tokens": {"input": 900, "prompt": 1000, "candidates": 50, "total": 1060, "cached": 100, "thoughts": 10, "tool": 0}}}}}))
else:
    print(json.dumps({"result": "가짜 클로드 답", "is_error": False, "num_turns": 2, "duration_api_ms": 2000,
                      "total_cost_usd": 0.01, "usage": {"input_tokens": 4, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 8000, "output_tokens": 60}, "modelUsage": {"claude-sonnet-5-5": {}}}))
'''


class Backends(unittest.TestCase):
    def setUp(self):
        self.bin = Path(tempfile.mkdtemp())
        for n in ("claude", "gemini"):
            f = self.bin / n
            f.write_text(FAKE)
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        self.log = self.bin / "log.json"
        self.env = {k: os.environ.get(k) for k in ("PATH", "FAKE_LOG", "FAKE_MODE", "WORLDPLAN_LLM")}
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        os.environ["FAKE_LOG"] = str(self.log)
        os.environ.pop("FAKE_MODE", None)

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def rec(self):
        return json.loads(self.log.read_text())

    def test_gemini_인자_설정_토큰(self):
        r = A.GeminiCLI().ask("다음 주 계획 세워줘", sample(), "2026-10-01", "2026-10-01T09:00+00:00")
        self.assertEqual(r["answer"], "가짜 제미나이 답")
        self.assertEqual(r["tokens"], {"input": 900, "cache_creation": 0, "cache_read": 100, "output": 60, "total": 1060})
        self.assertIsNone(r["cost_usd"])                       # Gemini CLI 는 비용을 안 낸다 -- 0 이라고 하지 않는다
        self.assertEqual((r["api_ms"], r["turns"]), (1234, 2))
        x = self.rec()
        self.assertEqual(x["argv"][:2], ["-p", "[now: 2026-10-01T09:00+00:00]\n다음 주 계획 세워줘"])
        self.assertIn("--skip-trust", x["argv"]); self.assertIn("worldplan", x["argv"])
        self.assertNotIn("--yolo", x["argv"])                  # 모든 도구 자동 승인은 안 쓴다
        self.assertEqual(x["settings"]["tools"]["core"], [])   # 내장 도구(셸 · 파일 · 웹) 없음
        wp = x["settings"]["mcpServers"]["worldplan"]
        self.assertTrue(wp["trust"])
        self.assertNotIn("PYTHONPATH", wp["env"])              # gemini 가 막는다 -> Disconnected (0.62 실측)
        self.assertTrue((Path(wp["cwd"]) / "worldplan" / "__init__.py").is_file())   # 대신 cwd 로 찾는다
        self.assertIn("worldplan", x["system"]); self.assertIn("kickoff", x["system"])   # 현재 요청이 시스템 프롬프트에

    def test_claude_인자_토큰(self):
        r = A.ClaudeCLI().ask("안녕", sample(), "2026-10-01")
        self.assertEqual(r["tokens"]["total"], 8064)
        x = self.rec()
        self.assertIn("--strict-mcp-config", x["argv"]); self.assertIn("mcp__worldplan", x["argv"])
        self.assertEqual(x["argv"][x["argv"].index("--tools") + 1], "")

    def test_auto_는_gemini_먼저(self):
        os.environ["WORLDPLAN_LLM"] = "auto"
        self.assertEqual([x.name for x in A.pick_llms()], ["Gemini", "Claude"])
        self.assertEqual([x.name for x in A.pick_llms("claude")], ["Claude"])
        with self.assertRaises(ValueError):
            A.pick_llms("gpt")

    def test_gemini_로그인이_없으면_claude_로(self):
        os.environ["FAKE_MODE"] = "auth"
        a = A.Assistant(walp=None, front="clock", llm=[A.GeminiCLI(), A.ClaudeCLI()])
        r = a.ask("다음 주 계획 세워줘", sample(), NOW)
        self.assertEqual((r["by"], r["answer"]), ("claude", "가짜 클로드 답"))
        self.assertEqual(r["route"], "시각 센서 못 정함 -> Gemini auth -> Claude")

    def test_로그인이_없고_다음도_없으면_그렇게_말한다(self):
        os.environ["FAKE_MODE"] = "auth"
        r = A.Assistant(walp=None, front="clock", llm=[A.GeminiCLI()]).ask("계획 세워줘", sample(), NOW)
        self.assertEqual(r["error"], "auth"); self.assertIn("로그인", r["answer"])

    def test_깔리지_않은_CLI(self):
        r = A.GeminiCLI(binary="/nonexistent/gemini").ask("x", sample(), "2026-10-01")
        self.assertEqual(r["error"], "not_found")

    def test_시각_센서는_LLM_을_안_부른다(self):
        a = A.Assistant(walp=None, front="clock", llm=[A.GeminiCLI()])
        r = a.ask("지금 베를린 몇 시야", sample(), NOW)
        self.assertEqual((r["by"], r["tokens"]["total"]), ("walp", 0))
        self.assertFalse(self.log.exists())



class RealGemini(unittest.TestCase):
    """진짜 gemini 가 있으면(PATH 또는 WORLDPLAN_GEMINI_BIN): GeminiCLI 가 짓는 설정으로 MCP 가 **실제로 붙는가**.
    로그인은 필요 없다(`gemini mcp list` 는 서버에 붙어 보기만 한다)."""

    def test_mcp_가_붙는다(self):
        import shutil
        import subprocess
        b = os.environ.get("WORLDPLAN_GEMINI_BIN") or shutil.which("gemini")
        if not b:
            self.skipTest("gemini 가 없다")
        g = A.GeminiCLI(binary=b)
        home = Path(tempfile.mkdtemp())
        (home / ".gemini").mkdir()
        (home / ".gemini" / "trustedFolders.json").write_text(json.dumps({str(g.dir): "TRUST_FOLDER"}))
        r = subprocess.run([b, "mcp", "list"], cwd=g.dir, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "HOME": str(home)})
        out = r.stdout + r.stderr
        self.assertRegex(out, r"worldplan: .* - Connected", out[-800:])


if __name__ == "__main__":
    unittest.main()
