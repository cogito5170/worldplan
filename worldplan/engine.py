"""엔진 -- 생성(solver) -> 판정(judge) -> 원장(ledger) -> 보고.

보고서의 규칙(se_new '과장하지 않는다' · '재고 나서 보고하기 전에'):
  · verdict 는 심판이 정한다. 생성자가 '찾았다' 고 해도 심판이 거절하면 REJECT 다
  · 최적이라는 말은 탐색이 끝까지 돌았을 때만 한다
  · 무효화 조건(invalidated_if)과 못 본 것(not_checked)을 매번 같이 낸다
  · 원장에는 사람의 일정 내용을 남기지 않는다 -- 입력 해시와 판정만 남긴다
"""
from __future__ import annotations

import copy
from datetime import timedelta

from . import ledger
from .judge import CHECKS, judge
from .model import (InputError, Request, check_zone, from_min, iso, parse_instant,
                    parse_request, tzdata_version, zone)
from .solver import candidate_details, solve

VERSION = "0.1.0"

NOT_CHECKED = [
    "체류지 사이 이동 시간(비행·시차 적응)은 재지 않는다 -- stays 의 날짜 경계만 본다",
    "공휴일 데이터베이스가 없다 -- 입력 holidays 에 준 날만 휴일로 안다",
    "참가자의 실제 캘린더와 동기화하지 않는다 -- busy 는 입력에 준 것뿐이다",
    "회의실·장비 같은 자원 제약은 없다",
    "격자(slot_minutes) 밖의 시작 시각은 생성자가 보지 않는다(심판은 어떤 분이든 잰다)",
]


def _invalidated_if(req: "Request | None", sha: str) -> "list[str]":
    out = [f"입력이 한 글자라도 바뀌면(input_sha256={sha[:12]}…) 이 판정은 그 입력에 대한 것이 아니다",
           f"tz 데이터베이스가 지금 판({tzdata_version()})과 다르면(나라가 DST 규칙을 바꾸면) 다시 판정해야 한다"]
    if req is not None:
        zs = sorted({p.tz for p in req.participants.values()}
                    | {s.tz for p in req.participants.values() for s in p.stays})
        out.append("이 시간대들의 규칙에 기댄다: " + ", ".join(zs))
    return out


def _optimality(status: str, req: Request, hit: str) -> str:
    return {
        "proven_optimal": f"탐색이 한도 안에서 끝까지 돌았다 -- 이 비용함수와 {req.slot}분 격자 위에서는 "
                          f"더 싼 배치가 없다. 격자 밖·다른 비용함수에 대해서는 말하지 않는다",
        "best_found_not_proven": f"탐색이 {hit} 한도에 걸렸다 -- 찾은 것 중 가장 싼 배치일 뿐, "
                                 f"더 싼 배치가 있을 수 있다",
        "infeasible_proven": f"{req.slot}분 격자 위에서 모든 일정을 다 넣는 배치가 없다 -- 탐색이 끝까지 돌아 보였다",
        "unknown": f"탐색이 {hit} 한도에 걸려 배치를 못 찾았고, 없다는 증명도 못 했다 -- 모른다",
    }[status]


def _record(kind, sha, verdict, extra) -> dict:
    rec = ledger.append(kind, {"input_sha256": sha, "verdict": verdict, "tzdata": tzdata_version(),
                               "engine": VERSION, **extra})
    return {"seq": rec["seq"], "hash": rec["hash"]}


def _reject_input(kind, raw, e: InputError, record: bool) -> dict:
    sha = ledger.sha(raw)
    out = {"verdict": "REJECT", "reason": "입력을 판정할 수 없다 -- 애매하거나 틀린 입력은 추측하지 않는다",
           "input_errors": e.errors, "input_sha256": sha, "engine": VERSION}
    if record:
        out["ledger"] = _record(kind, sha, "REJECT", {"stage": "input", "errors": len(e.errors)})
    return out


def plan(raw: dict, record: bool = True) -> dict:
    """요청 하나를 계획하고 판정한다."""
    sha = ledger.sha(raw)
    try:
        req = parse_request(raw)
    except InputError as e:
        return _reject_input("plan", raw, e, record)

    res = solve(req)
    starts = {k: from_min(t) for k, t in res.assignments.items()}
    out = {
        "engine": VERSION,
        "input_sha256": sha,
        "tzdata": tzdata_version(),
        "search": {"status": res.status, "nodes": res.nodes, "elapsed_s": round(res.elapsed, 3),
                   "hit_limit": res.hit_limit or None, "candidates_per_event": res.candidates,
                   "optimality": _optimality(res.status, req, res.hit_limit)},
        "not_checked": NOT_CHECKED,
        "invalidated_if": _invalidated_if(req, sha),
    }
    if res.complete:
        j = judge(req, starts, claimed_cost=res.cost)
        out["verdict"] = "ACCEPT" if j["ok"] else "REJECT"
        out["reason"] = ("심판이 하드 제약 {}개를 다시 재서 위반 0건, 비용 대조 일치".format(len(j["checks_run"]))
                         if j["ok"] else "생성자가 낸 계획을 심판이 거절했다 -- 위반 목록을 보라")
        out["plan"] = j["events"]
        out["cost"] = {**j["cost"], "solver_total": res.cost}
        out["judge"] = {"checks_run": j["checks_run"], "violations": j["violations"]}
    else:
        out["verdict"] = "REJECT"
        out["reason"] = "모든 일정을 넣는 배치를 못 냈다 -- 일부만 넣은 계획은 성공이 아니다"
        out["diagnostics"] = res.diagnostics
        if starts:
            # 참고용 부분 계획 -- 이것도 심판을 지나게 해서 '부분이라도 맞는가' 는 보여 준다
            sub = copy.copy(req)
            sub.events = {k: v for k, v in req.events.items() if k in starts}
            jp = judge(sub, starts, check_cost=False)
            out["best_partial"] = {"scheduled": sorted(starts), "unscheduled": sorted(set(req.events) - set(starts)),
                                   "judge_ok_for_scheduled_part": jp["ok"], "plan": jp["events"],
                                   "violations": jp["violations"]}
    if record:
        out["ledger"] = _record("plan", sha, out["verdict"], {
            "status": res.status, "events": len(req.events), "participants": len(req.participants),
            "cost_total": out.get("cost", {}).get("total"),
            "violations": len(out.get("judge", {}).get("violations", []))})
    return out


def verify(raw: dict, assignments: dict, claimed_cost=None, record: bool = True) -> dict:
    """누가 만든 계획이든(사람·LLM·다른 도구) 같은 심판에 올린다."""
    sha = ledger.sha({"request": raw, "assignments": assignments})
    try:
        req = parse_request(raw)
    except InputError as e:
        return _reject_input("verify", {"request": raw, "assignments": assignments}, e, record)
    errs = []
    starts = {}
    if not isinstance(assignments, dict):
        errs.append("assignments: {event_id: 시작 ISO 시각} 객체여야 한다")
        assignments = {}
    for k, v in assignments.items():
        t = parse_instant(v, f"assignments['{k}']", errs)
        if t is not None:
            starts[k] = t
    if errs:
        return _reject_input("verify", {"request": raw, "assignments": assignments}, InputError(errs), record)
    cc = claimed_cost if isinstance(claimed_cost, int) and not isinstance(claimed_cost, bool) else None
    j = judge(req, starts, claimed_cost=cc, check_cost=cc is not None)
    out = {
        "engine": VERSION,
        "verdict": "ACCEPT" if j["ok"] else "REJECT",
        "reason": ("하드 제약 위반 0건" if j["ok"] else f"위반 {len(j['violations'])}건 -- 기본은 거절이다"),
        "plan": j["events"],
        "cost": j["cost"],
        "judge": {"checks_run": j["checks_run"], "violations": j["violations"]},
        "input_sha256": sha,
        "tzdata": tzdata_version(),
        "note": ("이 판정은 계획이 '맞는가' 만 말한다. 더 나은 계획이 있는지는 말하지 않는다 "
                 "-- 그것은 plan_world_schedule 의 search.optimality 가 말한다"),
        "not_checked": NOT_CHECKED,
        "invalidated_if": _invalidated_if(req, sha),
    }
    if record:
        out["ledger"] = _record("verify", sha, out["verdict"], {"violations": len(j["violations"])})
    return out


def common_slots(raw: dict) -> dict:
    """일정 하나를 놓을 수 있는 시각들 -- 후보마다 심판이 하나씩 다시 잰다."""
    if not isinstance(raw, dict):
        return {"verdict": "REJECT", "input_errors": ["객체여야 한다"]}
    limit = raw.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        return {"verdict": "REJECT", "input_errors": ["limit: 1..100"]}
    parts = raw.get("participants")
    ids = [p.get("id") for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []
    req_ids = raw.get("required") or ids
    ev = {"id": "slot", "title": raw.get("title", "slot"), "duration_minutes": raw.get("duration_minutes"),
          "required": req_ids, "optional": raw.get("optional") or []}
    for k in ("not_before", "not_after"):
        if raw.get(k):
            ev[k] = raw[k]
    req_raw = {k: raw[k] for k in ("horizon", "slot_minutes", "buffer_minutes") if k in raw}
    req_raw.update(participants=parts, events=[ev])
    try:
        req = parse_request(req_raw)
    except InputError as e:
        return {"verdict": "REJECT", "input_errors": e.errors}
    cands = candidate_details(req, "slot", limit)
    out, disagree = [], []
    for c in cands:
        t = from_min(c["start_min"])
        j = judge(req, {"slot": t}, claimed_cost=c["cost"])
        row = {"start_utc": iso(t), "end_utc": iso(t + timedelta(minutes=req.events["slot"].duration)),
               "cost": j["cost"], "people": j["events"]["slot"]["people"]}
        (out if j["ok"] else disagree).append(row if j["ok"] else {**row, "violations": j["violations"]})
    res = {"engine": VERSION, "slots": out, "count": len(out), "tzdata": tzdata_version(),
           "ranking": "선호 시간대(core) 밖 분 × 10 + 못 오는 선택 참가자 × 300 이 작은 순",
           "not_checked": NOT_CHECKED}
    if disagree:
        res["judge_rejected_candidates"] = disagree
        res["warning"] = "생성자의 후보 일부를 심판이 거절했다 -- 두 길이 어긋났다. 위 slots 만 믿어라"
    if not out:
        from .solver import diagnose_event
        res["verdict"] = "REJECT"
        res["diagnostics"] = [diagnose_event(req, "slot")]
    else:
        res["verdict"] = "ACCEPT"
    return res


def world_clock(instant: str, zones: list) -> dict:
    errs = []
    t = parse_instant(instant, "instant", errs)
    if not isinstance(zones, list) or not zones or len(zones) > 50:
        errs.append("zones: 1~50개의 IANA 이름 목록")
        zones = []
    good = [z for z in zones if check_zone(z, f"zones['{z}']", errs)]
    if errs:
        return {"verdict": "REJECT", "input_errors": errs}
    rows = []
    for z in good:
        lt = t.astimezone(zone(z))
        off = lt.utcoffset()
        rows.append({"tz": z, "local": lt.isoformat(timespec="minutes"), "abbr": lt.tzname(),
                     "utc_offset_minutes": int(off.total_seconds() // 60),
                     "dst": bool(lt.dst()), "weekday": lt.strftime("%a")})
    return {"instant_utc": iso(t), "zones": rows, "tzdata": tzdata_version()}


def ledger_status(n: int = 10) -> dict:
    n = n if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 200 else 10
    return {"chain": ledger.verify(), "recent": ledger.read(n), "path": str(ledger.path())}


def describe() -> dict:
    return {"engine": VERSION, "checks": [{"id": c, "what": w} for c, w in CHECKS],
            "not_checked": NOT_CHECKED, "tzdata": tzdata_version()}
