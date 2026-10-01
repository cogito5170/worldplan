"""DST 와 밤샘 근무 -- 생성자(UTC 구간)와 심판(현지 벽시계)이 갈라지기 쉬운 자리."""
import unittest
from datetime import datetime

import tests._util  # noqa: F401
from worldplan import engine
from worldplan.judge import judge
from worldplan.model import parse_request

NY_BERLIN = [
    {"id": "ny", "tz": "America/New_York", "work": {"start": "09:00", "end": "17:00"}},
    {"id": "ber", "tz": "Europe/Berlin", "work": {"start": "09:00", "end": "17:00"}},
]


class 서머타임(unittest.TestCase):
    def slots(self, h0, h1, dur):
        return engine.common_slots({"participants": NY_BERLIN, "horizon": {"start": h0, "end": h1},
                                    "duration_minutes": dur, "slot_minutes": 15, "limit": 100})

    def test_겹침이_3시간에서_2시간으로(self):
        # 2026-10-26(월): 유럽은 10-25 에 겨울시간, 미국은 아직 여름시간 -> 5시간 차, 겹침 13~16Z
        a = self.slots("2026-10-26T00:00Z", "2026-10-27T00:00Z", 180)
        self.assertEqual(a["verdict"], "ACCEPT")
        self.assertEqual([s["start_utc"] for s in a["slots"]], ["2026-10-26T13:00+00:00"])
        # 2026-11-02(월): 미국도 11-01 에 겨울시간 -> 6시간 차, 겹침 14~16Z. 3시간짜리는 없다
        b = self.slots("2026-11-02T00:00Z", "2026-11-03T00:00Z", 180)
        self.assertEqual(b["verdict"], "REJECT")
        self.assertIn("겹치는 업무시간이 없다", b["diagnostics"][0]["why"])
        c = self.slots("2026-11-02T00:00Z", "2026-11-03T00:00Z", 120)
        self.assertEqual([s["start_utc"] for s in c["slots"]], ["2026-11-02T14:00+00:00"])

    def test_심판이_옛_오프셋을_잡는다(self):
        # 10월 오프셋으로 생각해 13:00Z 로 잡으면 11-02 에는 뉴욕 08:00 이다
        raw = {"horizon": {"start": "2026-11-02T00:00Z", "end": "2026-11-03T00:00Z"},
               "participants": NY_BERLIN,
               "events": [{"id": "m", "duration_minutes": 60, "required": ["ny", "ber"]}]}
        r = engine.verify(raw, {"m": "2026-11-02T13:00Z"}, record=False)
        self.assertEqual(r["verdict"], "REJECT")
        self.assertIn("08:00", r["judge"]["violations"][0]["message"])


class 밤샘_근무(unittest.TestCase):
    RAW = {"horizon": {"start": "2026-10-31T00:00Z", "end": "2026-11-02T12:00Z"},
           "slot_minutes": 30,
           "participants": [{"id": "night", "tz": "America/New_York",
                             "work": {"start": "22:00", "end": "06:00", "days": [5]},
                             "core": {"start": "23:00", "end": "01:00"}}],
           "events": [{"id": "x", "duration_minutes": 60, "required": ["night"]}]}

    def test_가을_되돌림_밤(self):
        # 10-31(토) 22:00 EDT ~ 11-01 06:00 EST -- 실제로는 9시간짜리 창. 01:00~02:00 을 두 번 지난다
        r = engine.plan(self.RAW, record=False)
        self.assertEqual(r["verdict"], "ACCEPT", r.get("judge"))
        self.assertEqual(r["cost"]["total"], 0)       # core 23~01 안에 넣을 수 있다
        req = parse_request(self.RAW)
        # 05:30Z = 01:30 EDT(첫 번째 01:30). core(~01:00) 밖 -- 실제 60분 전부 밖이다
        j = judge(req, {"x": datetime.fromisoformat("2026-11-01T05:30+00:00")})
        self.assertTrue(j["ok"], j["violations"])
        self.assertEqual(j["cost"]["outside_core_minutes"], 60)
        # 생성자가 같은 자리를 같은 값으로 재는가 -- 비용 대조로 본다
        j2 = judge(req, {"x": datetime.fromisoformat("2026-11-01T05:30+00:00")}, claimed_cost=600)
        self.assertTrue(j2["ok"], j2["violations"])

    def test_창_끝_밖(self):
        req = parse_request(self.RAW)
        # 11:00Z = 06:00 EST -> 창 끝에서 시작
        j = judge(req, {"x": datetime.fromisoformat("2026-11-01T11:00+00:00")})
        self.assertFalse(j["ok"])


if __name__ == "__main__":
    unittest.main()
