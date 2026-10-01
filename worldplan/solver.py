"""생성자(generator) -- 후보 계획을 **만든다.** 판정은 하지 않는다.

se_new 원칙: 생성자와 심판을 분리한다. 여기서 낸 계획은 judge.py 가 따로 재기 전에는
아무것도 보장하지 않는다. 이 모듈은 judge 를 임포트하지 않고, judge 도 이것을 임포트하지 않는다.

방법: UTC 분 단위 구간 연산.
  1) 참가자마다 '쓸 수 있는 창' 을 UTC 구간으로 짓는다 (업무시간 · 요일 · 휴일 · 체류지 -> busy 를 뺀다)
  2) 일정마다 필수 참가자 창의 교집합에서 격자 위 시작 후보를 뽑는다
  3) 분기한정(branch & bound) + MRV 로 고른다. 노드·시간 한도가 있다

정직한 한계 -- 보고서에 그대로 나간다:
  · '최적' 은 탐색이 한도 안에서 **끝까지 돈 경우에만** 말한다(proven_optimal).
    한도에 걸리면 best_found_not_proven 이다
  · 최적은 *이 비용함수* 와 *이 격자* 에 대해서다. 격자 밖 시작 시각은 보지 않는다
"""
from __future__ import annotations

import bisect
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from .model import Participant, Request, from_min, to_min, to_min_ceil, zone

W_OUTSIDE_CORE = 10      # 선호 시간대 밖 1분당
W_OPTIONAL_MISSING = 300  # 선택 참가자가 못 오는 일정 하나당


@dataclass
class Window:
    s: int
    e: int
    day: str        # 창이 시작한 현지 날짜(ISO) -- 하루 상한 계산용
    core_s: int     # 선호 시간대(UTC 분)
    core_e: int


@dataclass
class SolveResult:
    assignments: "dict[str, int]"            # event -> 시작(UTC 분)
    complete: bool
    cost: "int | None"
    status: str                              # proven_optimal | best_found_not_proven | infeasible_proven | unknown
    nodes: int
    elapsed: float
    hit_limit: str = ""
    candidates: "dict[str, int]" = field(default_factory=dict)
    diagnostics: "list[dict]" = field(default_factory=list)


def _wall(d, minutes: int, tzname: str) -> datetime:
    """현지 날짜 d 의 자정에서 minutes 분 뒤의 '벽시계' 시각 -> aware datetime."""
    extra, m = divmod(minutes, 1440)
    dd = d + timedelta(days=extra)
    return datetime.combine(dd, time(m // 60, m % 60), tzinfo=zone(tzname))


def build_windows(p: Participant, h0: int, h1: int) -> "list[Window]":
    """참가자 p 가 [h0,h1) 안에서 회의에 쓸 수 있는 창(busy 를 뺀 것)."""
    d0 = from_min(h0).date() - timedelta(days=2)
    d1 = from_min(h1).date() + timedelta(days=2)
    out: "list[Window]" = []
    busy = [(to_min(s), to_min_ceil(e)) for s, e in p.busy]
    d = d0
    while d <= d1:
        tzname = p.zone_name_for(d)
        if d.weekday() in p.work.days and d not in p.holidays:
            ws = to_min(_wall(d, p.work.start, tzname))
            we = to_min(_wall(d, p.work.end_abs, tzname))
            cs = to_min(_wall(d, p.core[0], tzname))
            ce = to_min(_wall(d, p.core[1], tzname))
            s, e = max(ws, h0), min(we, h1)
            if s < e:
                pieces = [(s, e)]
                for bs, be in busy:
                    nxt = []
                    for a, b in pieces:
                        if be <= a or bs >= b:
                            nxt.append((a, b))
                        else:
                            if a < bs:
                                nxt.append((a, bs))
                            if be < b:
                                nxt.append((be, b))
                    pieces = nxt
                for a, b in pieces:
                    out.append(Window(a, b, d.isoformat(), cs, ce))
        d += timedelta(days=1)
    out.sort(key=lambda w: (w.s, w.e))
    return out


def _window_for(ws: "list[Window]", s: int, e: int) -> "Window | None":
    for w in ws:
        if w.s <= s and e <= w.e:
            return w
        if w.s > s:
            break
    return None


def _outside(s: int, e: int, w: Window) -> int:
    ov = max(0, min(e, w.core_e) - max(s, w.core_s))
    return (e - s) - ov


class _Problem:
    def __init__(self, req: Request):
        self.req = req
        self.h0 = to_min(req.horizon_start)
        self.h1 = to_min(req.horizon_end)
        self.win = {pid: build_windows(p, self.h0, self.h1) for pid, p in req.participants.items()}
        self.ev = list(req.events.values())
        self.cand: "dict[str, list[tuple[int, int]]]" = {}     # event -> [(cost, start)]
        self.cand_detail: "dict[str, dict[int, dict]]" = {}
        for e in self.ev:
            self.cand[e.id] = self._candidates(e)

    def _intersect(self, e) -> "list[tuple[int, int]]":
        cur = [(w.s, w.e) for w in self.win[e.required[0]]]
        for pid in e.required[1:]:
            other = [(w.s, w.e) for w in self.win[pid]]
            nxt = []
            for a, b in cur:
                for c, d in other:
                    lo, hi = max(a, c), min(b, d)
                    if lo < hi:
                        nxt.append((lo, hi))
            cur = sorted(set(nxt))
        return cur

    def _candidates(self, e) -> "list[tuple[int, int]]":
        lo = self.h0
        hi = self.h1
        if e.not_before:
            lo = max(lo, to_min_ceil(e.not_before))
        if e.not_after:
            hi = min(hi, to_min(e.not_after))
        starts = set()
        if e.fixed_start is not None:
            fs = to_min(e.fixed_start)
            starts_iter = [(fs, fs)]
        else:
            starts_iter = self._intersect(e)
        slot = self.req.slot
        for a, b in starts_iter:
            if e.fixed_start is not None:
                t = a
                if lo <= t and t + e.duration <= hi and all(
                        _window_for(self.win[p], t, t + e.duration) for p in e.required):
                    starts.add(t)
                continue
            a2, b2 = max(a, lo), min(b, hi)
            if b2 - a2 < e.duration:
                continue
            first = self.h0 + -(-(a2 - self.h0) // slot) * slot
            t = first
            while t + e.duration <= b2:
                starts.add(t)
                t += slot
        out = []
        detail = {}
        for t in sorted(starts):
            outside = 0
            for pid in e.required:
                w = _window_for(self.win[pid], t, t + e.duration)
                outside += _outside(t, t + e.duration, w)
            missing = sum(1 for pid in e.optional
                          if _window_for(self.win[pid], t, t + e.duration) is None)
            c = W_OUTSIDE_CORE * outside + W_OPTIONAL_MISSING * missing
            out.append((c, t))
            detail[t] = {"outside_core_minutes": outside, "optional_missing": missing}
        out.sort()
        self.cand_detail[e.id] = detail
        return out


def _diagnose(prob: _Problem, e) -> dict:
    """후보가 0개인 일정 하나 -- 누가 / 무엇이 막았나. 추측이 아니라 다시 재서 말한다."""
    lo, hi = prob.h0, prob.h1
    if e.not_before:
        lo = max(lo, to_min_ceil(e.not_before))
    if e.not_after:
        hi = min(hi, to_min(e.not_after))
    alone = []
    for pid in e.required:
        fits = [w for w in prob.win[pid] if min(w.e, hi) - max(w.s, lo) >= e.duration]
        if not fits:
            alone.append(pid)
    reason = {"event": e.id, "candidates": 0}
    if hi - lo < e.duration:
        reason["why"] = "창(horizon ∩ not_before..not_after)이 일정 길이보다 짧다"
    elif alone:
        reason["why"] = "이 참가자 혼자서도 이 창 안에 연속으로 비는 시간이 없다(업무시간·휴일·busy)"
        reason["participants"] = alone
    elif e.fixed_start is not None:
        reason["why"] = "fixed_start 시각에 필수 참가자 중 누군가가 업무시간 밖이거나 busy 다"
    else:
        pairs = []
        req = e.required
        for i in range(len(req)):
            for j in range(i + 1, len(req)):
                sub = type(e)(**{**e.__dict__, "required": [req[i], req[j]],
                                 "fixed_start": None, "id": e.id + "#pair"})
                if not prob._candidates(sub):
                    pairs.append([req[i], req[j]])
                prob.cand_detail.pop(sub.id, None)
        reason["why"] = ("각자는 시간이 있지만 겹치는 업무시간이 없다(시간대 차이)"
                         if pairs else "세 명 이상이 동시에 겹치는 시간이 없다")
        if pairs:
            reason["conflicting_pairs"] = pairs[:20]
    return reason


def solve(req: Request) -> SolveResult:
    t0 = _time.monotonic()
    prob = _Problem(req)
    cand = prob.cand
    evs = {e.id: e for e in prob.ev}
    ncand = {k: len(v) for k, v in cand.items()}

    empty = [e for e in prob.ev if not cand[e.id]]
    if empty:
        return SolveResult({}, False, None, "infeasible_proven", 0, _time.monotonic() - t0,
                           candidates=ncand, diagnostics=[_diagnose(prob, e) for e in empty])

    min_cost = {k: v[0][0] for k, v in cand.items()}
    buf = req.buffer
    booked: "dict[str, list[tuple[int, int]]]" = {pid: [] for pid in req.participants}
    used: "dict[tuple[str, str], int]" = {}
    assign: "dict[str, int]" = {}
    best = {"cost": None, "assign": None}
    best_partial = {"n": -1, "cost": None, "assign": {}}
    nodes = 0
    limit = {"why": ""}
    deadline = t0 + req.time_limit

    def feasible(e, t) -> "list | None":
        for d in e.after:
            if d not in assign:
                return None
            if t < assign[d] + evs[d].duration + e.gap:
                return None
        end = t + e.duration
        taken = []
        for pid in e.required:
            for a, b in booked[pid]:
                if t < b + buf and a < end + buf:
                    return None
            p = req.participants[pid]
            if p.daily_cap is not None:
                w = _window_for(prob.win[pid], t, end)
                key = (pid, w.day)
                if used.get(key, 0) + e.duration > p.daily_cap:
                    return None
                taken.append(key)
        return taken

    def rec(cost: int, lb_rest: int):
        nonlocal nodes
        nodes += 1
        if nodes > req.max_nodes:
            limit["why"] = "max_nodes"
            return True
        if nodes % 512 == 0 and _time.monotonic() > deadline:
            limit["why"] = "time_limit"
            return True
        if len(assign) > best_partial["n"] or (
                len(assign) == best_partial["n"] and best_partial["cost"] is not None
                and cost < best_partial["cost"]):
            best_partial.update(n=len(assign), cost=cost, assign=dict(assign))
        if len(assign) == len(evs):
            if best["cost"] is None or cost < best["cost"]:
                best.update(cost=cost, assign=dict(assign))
            return False
        if best["cost"] is not None and cost + lb_rest >= best["cost"]:
            return False
        # MRV: 선행이 다 배정된 일정 중 남은 후보가 가장 적은 것
        pick, pick_opts = None, None
        for e in prob.ev:
            if e.id in assign or any(d not in assign for d in e.after):
                continue
            opts = []
            for c, t in cand[e.id]:
                if best["cost"] is not None and cost + c + lb_rest - min_cost[e.id] >= best["cost"]:
                    break   # 후보는 비용순 -- 뒤는 더 비싸다
                tk = feasible(e, t)
                if tk is not None:
                    opts.append((c, t, tk))
            key = (len(opts), -e.priority, e.id)
            if pick is None or key < pick_key:
                pick, pick_opts, pick_key = e, opts, key
            if not opts:
                break
        if pick is None or not pick_opts:
            return False
        e = pick
        for c, t, tk in pick_opts:
            assign[e.id] = t
            for pid in e.required:
                bisect.insort(booked[pid], (t, t + e.duration))
            for k in tk:
                used[k] = used.get(k, 0) + e.duration
            stop = rec(cost + c, lb_rest - min_cost[e.id])
            for k in tk:
                used[k] -= e.duration
            for pid in e.required:
                booked[pid].remove((t, t + e.duration))
            del assign[e.id]
            if stop:
                return True
        return False

    rec(0, sum(min_cost.values()))
    el = _time.monotonic() - t0
    if best["assign"] is not None:
        st = "best_found_not_proven" if limit["why"] else "proven_optimal"
        return SolveResult(best["assign"], True, best["cost"], st, nodes, el, limit["why"], ncand)
    st = "unknown" if limit["why"] else "infeasible_proven"
    diag = [{"why": "각 일정은 따로는 들어가지만, 함께 놓으면 겹침·선후·하루 상한 때문에 다 못 넣는다"
             + ("" if not limit["why"] else " (탐색이 한도에 걸려 증명은 못 했다)"),
             "best_partial_count": max(best_partial["n"], 0), "total": len(evs)}]
    return SolveResult(best_partial["assign"], False, best_partial["cost"], st, nodes, el,
                       limit["why"], ncand, diag)


def candidate_details(req: Request, event_id: str, limit: int = 10) -> "list[dict]":
    """한 일정의 후보를 비용순으로 -- find_common_slots 가 쓴다(심판이 하나씩 다시 잰다)."""
    prob = _Problem(req)
    out = []
    for c, t in prob.cand[event_id][:limit]:
        d = prob.cand_detail[event_id][t]
        out.append({"start_min": t, "cost": c, **d})
    return out


def diagnose_event(req: Request, event_id: str) -> dict:
    prob = _Problem(req)
    return _diagnose(prob, req.events[event_id])
