"""독립 대조 -- 생성자가 '최적' 이라고 한 비용을 **다른 길**로 다시 구한다.

작은 문제를 무작위로 지어, 격자 위 모든 배정 조합을 전수로 늘어놓고 **심판(judge)** 으로만
가능·비용을 잰다. 생성자 코드는 하나도 안 쓴다. 두 최솟값이 같아야 한다.
가능한 조합이 하나도 없으면 생성자도 infeasible_proven 이어야 한다.
"""
import itertools
import random
import unittest
from datetime import timedelta

import tests._util  # noqa: F401  (원장 임시 자리)
from worldplan import engine
from worldplan.judge import judge
from worldplan.model import parse_request

ZONES = ["Asia/Seoul", "Europe/Berlin", "America/New_York", "Asia/Kolkata", "Australia/Sydney",
         "America/Sao_Paulo", "Pacific/Auckland", "Asia/Kathmandu"]


def make(rng: random.Random):
    np_ = rng.randint(2, 3)
    parts = []
    for i in range(np_):
        s = rng.choice([6, 7, 8, 9, 10, 20])
        e = (s + rng.choice([6, 8, 10])) % 24
        p = {"id": f"p{i}", "tz": rng.choice(ZONES),
             "work": {"start": f"{s:02d}:00", "end": f"{e:02d}:00", "days": [0, 1, 2, 3, 4]},
             "core": None}
        p.pop("core")
        if rng.random() < 0.5:
            p["core"] = {"start": f"{(s + 1) % 24:02d}:00", "end": f"{(s + 4) % 24:02d}:00"}
        if rng.random() < 0.4:
            p["busy"] = [{"start": "2026-10-06T08:00Z", "end": "2026-10-06T12:00Z"}]
        if rng.random() < 0.3:
            p["daily_cap_minutes"] = 90
        parts.append(p)
    ids = [p["id"] for p in parts]
    evs = []
    for j in range(rng.randint(1, 3)):
        req_ = rng.sample(ids, rng.randint(1, len(ids)))
        opt = [x for x in ids if x not in req_ and rng.random() < 0.5]
        ev = {"id": f"e{j}", "duration_minutes": rng.choice([60, 120]), "required": req_, "optional": opt}
        if j and rng.random() < 0.5:
            ev["after"] = [f"e{j - 1}"]
            ev["gap_after_deps_minutes"] = rng.choice([0, 60])
        evs.append(ev)
    return {"horizon": {"start": "2026-10-05T00:00Z", "end": "2026-10-07T00:00Z"},
            "slot_minutes": 120, "buffer_minutes": rng.choice([0, 60]),
            "participants": parts, "events": evs}


def brute(raw):
    req = parse_request(raw)
    grid = []
    t = req.horizon_start
    while t < req.horizon_end:
        grid.append(t)
        t += timedelta(minutes=req.slot)
    ids = list(req.events)
    best = None
    for combo in itertools.product(grid, repeat=len(ids)):
        j = judge(req, dict(zip(ids, combo)), check_cost=False)
        if j["ok"] and (best is None or j["cost"]["total"] < best):
            best = j["cost"]["total"]
    return best


class 전수와_맞는가(unittest.TestCase):
    def test_무작위_60개(self):
        rng = random.Random(20261001)
        seen = {"feasible": 0, "infeasible": 0}
        for k in range(60):
            raw = make(rng)
            want = brute(raw)
            got = engine.plan(raw, record=False)
            st = got["search"]["status"]
            with self.subTest(case=k, status=st, want=want):
                if want is None:
                    seen["infeasible"] += 1
                    self.assertEqual(got["verdict"], "REJECT")
                    self.assertEqual(st, "infeasible_proven")
                else:
                    seen["feasible"] += 1
                    self.assertEqual(got["verdict"], "ACCEPT", got.get("judge"))
                    self.assertEqual(st, "proven_optimal")
                    self.assertEqual(got["cost"]["total"], want)
        # 사소한 설명 죽이기: 전부 불가능(또는 전부 가능)인 표본이면 이 대조는 아무것도 안 잰 것이다
        self.assertGreaterEqual(seen["feasible"], 10, seen)
        self.assertGreaterEqual(seen["infeasible"], 5, seen)
        print("\n  전수 대조 표본:", seen)


if __name__ == "__main__":
    unittest.main()
