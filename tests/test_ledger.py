import json
import os
import tempfile
import unittest
from pathlib import Path

from tests._util import ROOT, sample
from worldplan import engine, ledger


class 원장(unittest.TestCase):
    def setUp(self):
        self.old = os.environ["WORLDPLAN_LEDGER_ROOT"]
        os.environ["WORLDPLAN_LEDGER_ROOT"] = tempfile.mkdtemp(prefix="wp-ledger-")

    def tearDown(self):
        os.environ["WORLDPLAN_LEDGER_ROOT"] = self.old

    def test_판정마다_한_줄_사슬이_성하다(self):
        engine.plan(sample())
        bad = sample(); bad["events"] = []
        engine.plan(bad)
        rows = ledger.read(10)
        self.assertEqual([r["body"]["verdict"] for r in rows], ["ACCEPT", "REJECT"])
        self.assertEqual(rows[1]["prev"], rows[0]["hash"])
        self.assertTrue(ledger.verify()["ok"])

    def test_사람의_일정은_원장에_없다(self):
        engine.plan(sample())
        text = ledger.path().read_text(encoding="utf-8")
        for needle in ("kim", "Asia/Seoul", "킥오프", "2026-10-05T09:00"):
            self.assertNotIn(needle, text)

    def test_고치면_들킨다(self):
        for _ in range(3):
            engine.plan(sample())
        p = ledger.path()
        lines = p.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[1]); rec["body"]["verdict"] = "ACCEPT_TAMPERED"
        p.write_text("\n".join([lines[0], json.dumps(rec), lines[2]]) + "\n", encoding="utf-8")
        v = ledger.verify()
        self.assertFalse(v["ok"]); self.assertEqual(v["broken_at_line"], 2)

    def test_지우면_들킨다(self):
        for _ in range(3):
            engine.plan(sample())
        p = ledger.path()
        lines = p.read_text(encoding="utf-8").splitlines()
        p.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
        self.assertFalse(ledger.verify()["ok"])


class 검사는_흔적을_안_남긴다(unittest.TestCase):
    def test_저장소에_data_가_없다(self):
        # 검사 전체가 임시 원장을 쓰는지 -- 저장소 안의 기본 자리가 생기면 흔적이다
        self.assertFalse((ROOT / "data" / "ledger.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
