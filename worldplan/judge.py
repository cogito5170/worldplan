"""심판(judge) -- 계획이 하드 제약을 지키는지 **결정적으로** 잰다. LLM 이 아니다.

se_new 원칙:
  · 생성자와 심판을 분리한다. 이 모듈은 solver.py 를 임포트하지 않는다
  · 심판은 생성자와 **다른 길로** 잰다(독립 대조). solver 는 UTC 구간 연산으로 창을 짓고,
    judge 는 일정 하나하나를 각 참가자의 **현지 벽시계**로 바꿔서 본다. 같은 답이 나와야 한다
  · 기본은 REJECT. 위반이 하나라도 있으면, 혹은 생성자가 말한 비용과 심판이 잰 비용이
    다르면 거절한다 -- 다른 것을 잰 것이기 때문이다
  · 누가 만든 계획이든 같은 심판을 지난다. 사람이 손으로 짠 것이든 LLM 이 낸 것이든

심판이 **못** 보는 것은 not_checked 로 보고서에 그대로 나간다(engine.py).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .model import Participant, Request, iso, zone

W_OUTSIDE_CORE = 10
W_OPTIONAL_MISSING = 300
_DAYS = ["월", "화", "수", "목", "금", "토", "일"]

CHECKS = [
    ("J0_complete", "모든 일정이 정확히 한 번 배정됐다 · 모르는 일정이 없다 · 분 단위다"),
    ("J1_horizon_window", "horizon 과 not_before/not_after 안에 있다 · fixed_start 를 지켰다"),
    ("J2_work_hours", "필수 참가자마다 현지 업무시간 · 업무일 · 휴일 · 체류지 시간대를 지킨다(벽시계로 잰다)"),
    ("J3_busy", "필수 참가자의 busy 와 안 겹친다"),
    ("J4_double_booking", "같은 참가자의 일정끼리 buffer_minutes 이상 떨어져 있다"),
    ("J5_precedence", "after 의 선행 일정이 끝나고 gap 이상 지난 뒤 시작한다"),
    ("J6_daily_cap", "참가자의 하루(창 시작 현지일) 회의 합이 daily_cap_minutes 이하다"),
    ("J7_cost_crosscheck", "생성자가 보고한 비용과 심판이 따로 잰 비용이 같다"),
]


def _hm(m: int) -> str:
    d, r = divmod(m, 1440)
    s = f"{r // 60:02d}:{r % 60:02d}"
    return s + (f"(+{d}일)" if d else "")


def _wall_minutes(t: datetime, tzname: str, d: date) -> int:
    lt = t.astimezone(zone(tzname))
    return (lt.date() - d).days * 1440 + lt.hour * 60 + lt.minute


def outside_core(s: datetime, e: datetime, tzname: str, d: date, c0: int, c1: int) -> int:
    """[s,e) 의 **실제 분** 가운데 현지 벽시계가 선호 시간대 [c0,c1) 밖인 분의 수.

    오프셋이 앞뒤로 같으면 벽시계 뺄셈으로(빠른 길). DST 가 끼면 1분씩 실제로 걸어 본다 --
    벽시계는 가을에 한 시간을 두 번 지나가므로 뺄셈이 틀린다.
    """
    z = zone(tzname)
    if s.astimezone(z).utcoffset() == e.astimezone(z).utcoffset():
        m_s = _wall_minutes(s, tzname, d)
        m_e = m_s + int((e - s).total_seconds() // 60)
        return (m_e - m_s) - max(0, min(m_e, c1) - max(m_s, c0))
    n = 0
    t = s
    while t < e:
        m = _wall_minutes(t, tzname, d)
        if not c0 <= m < c1:
            n += 1
        t += timedelta(minutes=1)
    return n


def locate(p: Participant, s: datetime, e: datetime):
    """[s,e) 를 담는 업무창을 현지 벽시계로 찾는다.

    돌려주는 것: (ok, info). ok 면 info = {day, tz, m_s, m_e}; 아니면 info = {why}.
    """
    zones = [p.tz] + [st.tz for st in p.stays]
    seen = []
    first_why = None
    for tzname in zones:
        if tzname in seen:
            continue
        seen.append(tzname)
        ls = s.astimezone(zone(tzname))
        for d in (ls.date(), ls.date() - timedelta(days=1)):
            if p.zone_name_for(d) != tzname:
                continue
            m_s = _wall_minutes(s, tzname, d)
            m_e = _wall_minutes(e, tzname, d)
            why = None
            if d.weekday() not in p.work.days:
                why = f"{d} ({_DAYS[d.weekday()]}) 은 업무일이 아니다"
            elif d in p.holidays:
                why = f"{d} 은 휴일이다"
            elif not (m_s >= p.work.start and m_e <= p.work.end_abs):
                why = (f"현지 {_hm(m_s)}~{_hm(m_e)} ({tzname}, {d} 기준) 이 업무시간 "
                       f"{_hm(p.work.start)}~{_hm(p.work.end_abs)} 밖이다")
            if why is None:
                return True, {"day": d, "tz": tzname, "m_s": m_s, "m_e": m_e}
            if first_why is None and d == ls.date():
                first_why = why
    return False, {"why": first_why or "그 시각을 담는 업무창이 없다"}


def judge(req: Request, assignments: "dict[str, datetime]", claimed_cost: "int | None" = None,
          check_cost: bool = True) -> dict:
    v: "list[dict]" = []

    def bad(code, msg, **kw):
        v.append({"check": code, "message": msg, **kw})

    # J0
    for k in assignments:
        if k not in req.events:
            bad("J0_complete", f"모르는 일정 '{k}' 가 배정돼 있다", event=k)
    for k in req.events:
        if k not in assignments:
            bad("J0_complete", f"일정 '{k}' 가 배정되지 않았다", event=k)
    for k, s in assignments.items():
        if s.second or s.microsecond:
            bad("J0_complete", f"'{k}' 시작이 분 단위가 아니다", event=k)

    span: "dict[str, tuple[datetime, datetime]]" = {}
    for k, s in assignments.items():
        if k in req.events:
            span[k] = (s, s + timedelta(minutes=req.events[k].duration))

    # J1
    for k, (s, e) in span.items():
        ev = req.events[k]
        if s < req.horizon_start or e > req.horizon_end:
            bad("J1_horizon_window", f"'{k}' 가 horizon 밖이다", event=k)
        if ev.not_before and s < ev.not_before:
            bad("J1_horizon_window", f"'{k}' 가 not_before({iso(ev.not_before)}) 보다 이르다", event=k)
        if ev.not_after and e > ev.not_after:
            bad("J1_horizon_window", f"'{k}' 가 not_after({iso(ev.not_after)}) 뒤에 끝난다", event=k)
        if ev.fixed_start and s != ev.fixed_start:
            bad("J1_horizon_window", f"'{k}' 는 {iso(ev.fixed_start)} 로 고정인데 옮겨졌다", event=k)

    # J2 + 비용 + 현지 시각 표
    outside_total = 0
    missing_total = 0
    where: "dict[tuple[str, str], dict]" = {}
    detail: "dict[str, dict]" = {}
    for k, (s, e) in span.items():
        ev = req.events[k]
        rows = []
        for pid in ev.required:
            p = req.participants[pid]
            ok, info = locate(p, s, e)
            if not ok:
                bad("J2_work_hours", f"'{k}': {pid} -- {info['why']}", event=k, participant=pid)
                rows.append({"participant": pid, "role": "required", "ok": False, "why": info["why"]})
                continue
            where[(k, pid)] = info
            outside = outside_core(s, e, info["tz"], info["day"], *p.core)
            outside_total += outside
            lt = s.astimezone(zone(info["tz"]))
            rows.append({"participant": pid, "role": "required", "ok": True, "tz": info["tz"],
                         "local_start": lt.isoformat(timespec="minutes"),
                         "local_end": e.astimezone(zone(info["tz"])).isoformat(timespec="minutes"),
                         "outside_core_minutes": outside})
        for pid in ev.optional:
            p = req.participants[pid]
            ok, info = locate(p, s, e)
            free = ok and not any(s < be and bs < e for bs, be in p.busy)
            if not free:
                missing_total += 1
            tzname = info["tz"] if ok else p.zone_name_for(s.astimezone(zone(p.tz)).date())
            rows.append({"participant": pid, "role": "optional", "available": bool(free),
                         "tz": tzname,
                         "local_start": s.astimezone(zone(tzname)).isoformat(timespec="minutes"),
                         **({} if ok else {"why": info["why"]})})
        detail[k] = {"title": ev.title, "start_utc": iso(s), "end_utc": iso(e), "people": rows}

    # J3
    for k, (s, e) in span.items():
        for pid in req.events[k].required:
            for bs, be in req.participants[pid].busy:
                if s < be and bs < e:
                    bad("J3_busy", f"'{k}': {pid} 의 busy {iso(bs)}~{iso(be)} 와 겹친다",
                        event=k, participant=pid)

    # J4
    per: "dict[str, list]" = {}
    for k, (s, e) in span.items():
        for pid in req.events[k].required:
            per.setdefault(pid, []).append((s, e, k))
    gap = timedelta(minutes=req.buffer)
    for pid, lst in per.items():
        lst.sort()
        far_end, far_k = None, None
        for s, e, k in lst:
            if far_end is not None and s < far_end + gap:
                bad("J4_double_booking", f"{pid}: '{far_k}' 와 '{k}' 가 겹치거나 buffer "
                    f"{req.buffer}분보다 가깝다", event=k, participant=pid)
            if far_end is None or e > far_end:
                far_end, far_k = e, k

    # J5
    for k, (s, e) in span.items():
        ev = req.events[k]
        for d in ev.after:
            if d in span:
                need = span[d][1] + timedelta(minutes=ev.gap)
                if s < need:
                    bad("J5_precedence", f"'{k}' 는 '{d}' 가 끝나고 {ev.gap}분 뒤({iso(need)}) "
                        f"이후에 시작해야 한다", event=k)

    # J6
    load: "dict[tuple[str, date], int]" = {}
    for (k, pid), info in where.items():
        key = (pid, info["day"])
        load[key] = load.get(key, 0) + req.events[k].duration
    for (pid, d), m in load.items():
        cap = req.participants[pid].daily_cap
        if cap is not None and m > cap:
            bad("J6_daily_cap", f"{pid}: {d} 에 회의 {m}분 -- 상한 {cap}분을 넘는다", participant=pid)

    cost = {"outside_core_minutes": outside_total, "optional_missing": missing_total,
            "total": W_OUTSIDE_CORE * outside_total + W_OPTIONAL_MISSING * missing_total}

    # J7 -- 독립 대조. 심판이 잰 비용과 생성자가 잰 비용이 다르면 둘 중 하나가 다른 것을 쟀다
    if check_cost and claimed_cost is not None and not v and claimed_cost != cost["total"]:
        bad("J7_cost_crosscheck", f"생성자 비용 {claimed_cost} ≠ 심판 비용 {cost['total']} -- "
            f"두 길이 다른 것을 쟀다(DST 경계 등). 수를 보고하지 않고 거절한다")

    return {
        "ok": not v,
        "violations": v,
        "checks_run": [c for c, _ in CHECKS if check_cost or c != "J7_cost_crosscheck"],
        "cost": cost,
        "events": detail,
    }
