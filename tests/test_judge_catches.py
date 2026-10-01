"""옳은 계획 하나를 짓고 여러 가지로 망가뜨려 -- 심판이 **매번** 빨개지는지 본다.

글자를 보는 검사가 아니라 실제 판정을 돌린다(se_new 의 논문원장 게이트 시험과 같은 꼴).
"""
import unittest
from datetime import datetime, timedelta

from tests._util import sample
from worldplan import engine
from worldplan.judge import judge
from worldplan.model import parse_request


def _good():
    raw = sample()
    r = engine.plan(raw, record=False)
    assert r["verdict"] == "ACCEPT", r
    starts = {k: datetime.fromisoformat(v["start_utc"]) for k, v in r["plan"].items()}
    return raw, parse_request(raw), starts, r["cost"]["total"]


class 망가뜨리면_빨개진다(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw, cls.req, cls.starts, cls.cost = _good()

    def red(self, starts, code, claimed=None, req=None):
        j = judge(req or self.req, starts, claimed_cost=claimed)
        self.assertFalse(j["ok"], f"{code} 를 기대했는데 통과했다")
        self.assertIn(code, {v["check"] for v in j["violations"]}, j["violations"])

    def test_원본은_초록(self):
        j = judge(self.req, self.starts, claimed_cost=self.cost)
        self.assertTrue(j["ok"], j["violations"])

    def test_J0_빠진_일정(self):
        s = dict(self.starts); s.pop("wrap")
        self.red(s, "J0_complete")

    def test_J0_모르는_일정(self):
        s = dict(self.starts); s["ghost"] = s["wrap"]
        self.red(s, "J0_complete")

    def test_J1_horizon_밖(self):
        s = dict(self.starts); s["latam"] = self.req.horizon_end
        self.red(s, "J1_horizon_window")

    def test_J2_업무시간_밖(self):
        s = dict(self.starts); s["kickoff"] = s["kickoff"] + timedelta(hours=3)   # 서울 22시
        self.red(s, "J2_work_hours")

    def test_J2_휴일(self):
        # kim 의 2026-10-09 (베를린 체류 중) 은 휴일이다
        s = dict(self.starts); s["wrap"] = datetime.fromisoformat("2026-10-09T13:00+00:00")
        self.red(s, "J2_work_hours")

    def test_J2_체류지_시간대(self):
        # 10-08 에 kim 은 베를린에 있다. 서울 기준 10:00(=01:00Z)은 베를린 새벽 3시다
        s = dict(self.starts); s["design"] = datetime.fromisoformat("2026-10-08T10:00+09:00")
        self.red(s, "J2_work_hours")

    def test_J3_busy(self):
        s = dict(self.starts); s["kickoff"] = datetime.fromisoformat("2026-10-05T10:00+09:00")
        self.red(s, "J3_busy")

    def test_J4_겹침(self):
        s = dict(self.starts); s["design"] = s["wrap"]
        self.red(s, "J4_double_booking")

    def test_J4_buffer(self):
        s = dict(self.starts)
        s["wrap"] = s["design"] + timedelta(minutes=90 + 5)   # buffer 15분보다 가깝다
        self.red(s, "J4_double_booking")

    def test_J5_선후(self):
        s = dict(self.starts); s["design"] = s["kickoff"] - timedelta(days=1)
        self.red(s, "J5_precedence")

    def test_J6_하루_상한(self):
        raw = sample()
        raw["participants"][0]["daily_cap_minutes"] = 100   # design 90 + wrap 45 = 135 > 100
        self.red(self.starts, "J6_daily_cap", req=parse_request(raw))

    def test_J7_비용_불일치(self):
        self.red(self.starts, "J7_cost_crosscheck", claimed=self.cost + 1)

    def test_사소한_설명_분_아닌_시작(self):
        s = dict(self.starts); s["latam"] = s["latam"] + timedelta(seconds=30)
        self.red(s, "J0_complete")

    def test_verify_도구도_같은_심판(self):
        a = {k: v.isoformat() for k, v in self.starts.items()}
        self.assertEqual(engine.verify(self.raw, a, record=False)["verdict"], "ACCEPT")
        a["kickoff"] = "2026-10-05T13:00+00:00"   # 서울 22시
        self.assertEqual(engine.verify(self.raw, a, record=False)["verdict"], "REJECT")

    def test_오프셋_없는_배정은_거절(self):
        a = {k: v.isoformat() for k, v in self.starts.items()}
        a["kickoff"] = "2026-10-05T10:00"
        self.assertEqual(engine.verify(self.raw, a, record=False)["verdict"], "REJECT")


if __name__ == "__main__":
    unittest.main()
