import copy
import unittest

from tests._util import sample
from worldplan import engine
from worldplan.model import InputError, parse_request


class 애매하면_거절한다(unittest.TestCase):
    def bad(self, mutate, needle):
        raw = sample()
        mutate(raw)
        with self.assertRaises(InputError) as cm:
            parse_request(raw)
        self.assertTrue(any(needle in e for e in cm.exception.errors), cm.exception.errors)
        r = engine.plan(raw, record=False)
        self.assertEqual(r["verdict"], "REJECT")

    def test_오프셋_없는_시각(self):
        self.bad(lambda r: r["horizon"].__setitem__("start", "2026-10-05T00:00"), "오프셋이 없다")

    def test_일정_0개(self):
        self.bad(lambda r: r.__setitem__("events", []), "events")

    def test_필수_참가자_없음(self):
        self.bad(lambda r: r["events"][0].__setitem__("required", []), "required")

    def test_모르는_시간대(self):
        self.bad(lambda r: r["participants"][0].__setitem__("tz", "Asia/Nowhere"), "모른다")

    def test_시간대_경로_탈출(self):
        self.bad(lambda r: r["participants"][0].__setitem__("tz", "../../etc/passwd"), "시간대")

    def test_선후_고리(self):
        def m(r):
            r["events"][0]["after"] = ["wrap"]
        self.bad(m, "고리")

    def test_체류_겹침(self):
        def m(r):
            r["participants"][0]["stays"].append({"from": "2026-10-09", "to": "2026-10-10", "tz": "Europe/Paris"})
        self.bad(m, "겹친다")

    def test_모르는_참가자(self):
        self.bad(lambda r: r["events"][0]["required"].append("ghost"), "ghost")

    def test_정상_예시는_통과(self):
        parse_request(sample())


if __name__ == "__main__":
    unittest.main()
