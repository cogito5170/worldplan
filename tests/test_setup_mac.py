"""scripts/setup_mac.sh 를 **끝까지 실제로 돌린다** -- bash -n 은 셸 스크립트가 한 줄도 안 도는 것도 통과시킨다.

임시 HOME 에 venv 를 짓고 이 저장소(--source)를 깐다. gemini · claude 는 받은 인자를 적어 두는 가짜다(진짜 로그인은
사람만 할 수 있다). 본다: venv 의 worldplan 이 돈다 · ~/.local/bin 에 걸렸다 · 두 CLI 에 MCP 가 user 범위로 등록됐다 ·
로그인이 없으면 그렇게 말한다 · 다시 돌려도 된다. pip 가 패키지 빌드 도구를 못 받으면(망 없음) 건너뛴다.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "setup_mac.sh"
FAKE = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_LOG"
case "$*" in "mcp remove"*) exit 1 ;; esac
exit 0
"""


class SetupMac(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="wp-setup-"))
        cls.home = cls.tmp / "home"
        cls.home.mkdir()
        fb = cls.tmp / "fakebin"
        fb.mkdir()
        for n in ("gemini", "claude"):
            (fb / n).write_text(FAKE)
            (fb / n).chmod(0o755)
        cls.log = cls.tmp / "cli.log"
        # brew · npm 이 없는 PATH(가짜 둘 + 시스템 기본) -- 아무것도 깔려고 하지 않는다
        cls.env = {"HOME": str(cls.home), "PATH": f"{fb}:{cls.home}/.local/bin:/usr/local/bin:/usr/bin:/bin",
                   "FAKE_LOG": str(cls.log), "XDG_CACHE_HOME": str(cls.tmp / "cache"),
                   "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
        for k in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PIP_CERT",
                  "PIP_INDEX_URL", "NODE_EXTRA_CA_CERTS"):
            if os.environ.get(k):
                cls.env[k] = os.environ[k]
        cls.runs = [cls._run(), cls._run()]

    @classmethod
    def _run(cls):
        return subprocess.run(["bash", str(SCRIPT), "--source", str(ROOT), "--no-worldtrip", "--no-cli-install"],
                              env=cls.env, capture_output=True, text=True, timeout=900)

    def setUp(self):
        r = self.runs[0]
        if r.returncode != 0 and "pip" in (r.stderr + r.stdout).lower() and "setuptools" in (r.stderr + r.stdout).lower():
            self.skipTest("pip 가 빌드 도구(setuptools)를 못 받았다 -- 망 없음")

    def test_두_번_다_끝까지_돈다(self):
        for r in self.runs:
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("다 됐다", r.stdout)

    def test_venv_의_worldplan_이_돈다(self):
        exe = self.home / ".local" / "bin" / "worldplan"
        self.assertTrue(exe.is_symlink())
        r = subprocess.run([str(exe), "ask", "서울 2026-10-05 오후 3시는 뉴욕 몇 시야?"], env={**self.env, "WORLDPLAN_LLM": "off",
                           "WORLDPLAN_LEDGER_ROOT": str(self.tmp / "led")}, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("02:00", r.stdout)

    def test_MCP_를_user_범위로_건다(self):
        log = self.log.read_text().splitlines()
        venv_wp = f"{self.home}/.worldplan/venv/bin/worldplan"
        self.assertIn(f"gemini mcp add -s user --trust worldplan {venv_wp} mcp", log)
        self.assertIn(f"claude mcp add -s user worldplan -- {venv_wp} mcp", log)
        self.assertEqual(sum(1 for x in log if x.startswith("gemini mcp add")), 2)   # 두 번 돌렸다 -- 지우고 다시 건다
        self.assertEqual(sum(1 for x in log if x.startswith("gemini mcp remove")), 2)

    def test_로그인이_없으면_말한다(self):
        self.assertIn("gemini 로그인이 아직이다", self.runs[0].stderr)


if __name__ == "__main__":
    unittest.main()
