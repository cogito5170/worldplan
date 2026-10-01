"""입력 모델 -- 요청 JSON 을 검증해서 자료형으로 바꾼다.

원칙(se_new): **모르는 것은 안 된 것으로 다룬다.** 애매한 입력은 추측하지 않고 거절한다.
  · 오프셋 없는 시각("2026-10-05T09:00") -> 거절. 어느 나라 9시인지 모른다
  · 필수 참가자가 없는 일정 -> 거절. 검증할 제약이 없으니 '통과' 가 아무 뜻도 없다
  · 일정이 0개 -> 거절. 빈 계획은 성공이 아니다

생성자(solver)와 심판(judge)이 **이 파서만** 공유한다. 제약을 재는 코드는 공유하지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc

# 한 요청이 서버를 붙들 수 있는 크기의 상한. 배포용이라 둔다.
MAX_PARTICIPANTS = 200
MAX_EVENTS = 300
MAX_HORIZON_DAYS = 62
MAX_BUSY_PER_PARTICIPANT = 2000


class InputError(ValueError):
    """요청이 애매하거나 틀렸다. 메시지 목록을 같이 든다."""

    def __init__(self, errors: "list[str]"):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


@dataclass(frozen=True)
class WorkRule:
    start: int            # 현지 자정부터 분
    end: int              # start 보다 작거나 같으면 밤샘(다음 날 end 까지)
    days: frozenset       # 0=월 .. 6=일 -- 창이 *시작하는* 날의 요일

    @property
    def end_abs(self) -> int:
        return self.end if self.end > self.start else self.end + 1440


@dataclass(frozen=True)
class Stay:
    first: date           # 이 날부터 (체류지 현지 날짜, 포함)
    last: date            # 이 날까지 (포함)
    tz: str


@dataclass
class Participant:
    id: str
    tz: str
    work: WorkRule
    core: "tuple[int, int]"            # 선호 시간대(현지, 분). 이 밖은 비용이 붙는다
    busy: "list[tuple[datetime, datetime]]"
    holidays: "set[date]"
    stays: "list[Stay]"
    daily_cap: "int | None"            # 하루(창 시작일 기준) 최대 회의 분

    def zone_name_for(self, d: date) -> str:
        """창이 시작하는 현지 날짜 d 에 이 사람이 어느 시간대에 있나 -- 정의 그 자체."""
        for s in self.stays:
            if s.first <= d <= s.last:
                return s.tz
        return self.tz


@dataclass
class Event:
    id: str
    title: str
    duration: int
    required: "list[str]"
    optional: "list[str]"
    not_before: "datetime | None"
    not_after: "datetime | None"
    after: "list[str]"
    gap: int
    priority: int
    fixed_start: "datetime | None"


@dataclass
class Request:
    horizon_start: datetime
    horizon_end: datetime
    slot: int
    buffer: int
    participants: "dict[str, Participant]"
    events: "dict[str, Event]"
    max_nodes: int
    time_limit: float
    raw: dict = field(repr=False, default_factory=dict)


_ID = re.compile(r"^[A-Za-z0-9_.\-가-힣]{1,64}$")
_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


def zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def check_zone(name, where, errs) -> "str | None":
    if not isinstance(name, str) or not name or ".." in name or name.startswith("/"):
        errs.append(f"{where}: 시간대 이름이 아니다: {name!r}")
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        errs.append(f"{where}: IANA 시간대 '{name}' 를 모른다 (예: Asia/Seoul)")
        return None
    return name


def parse_instant(s, where, errs) -> "datetime | None":
    if not isinstance(s, str):
        errs.append(f"{where}: 시각은 ISO 8601 문자열이어야 한다")
        return None
    try:
        dt = datetime.fromisoformat(s.strip())
    except ValueError:
        errs.append(f"{where}: ISO 8601 로 못 읽는다: {s!r}")
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        errs.append(f"{where}: 오프셋이 없다({s!r}). 어느 나라 시각인지 모르면 거절한다 -- "
                    f"'+09:00' 이나 'Z' 를 붙여라")
        return None
    return dt.astimezone(UTC)


def parse_date(s, where, errs) -> "date | None":
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        errs.append(f"{where}: 날짜(YYYY-MM-DD)가 아니다: {s!r}")
        return None


def parse_hhmm(s, where, errs, allow_24=True) -> "int | None":
    m = _HHMM.match(str(s)) if s is not None else None
    if not m:
        errs.append(f"{where}: 'HH:MM' 이 아니다: {s!r}")
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    v = h * 60 + mi
    if mi >= 60 or v > 1440 or (v == 1440 and not allow_24):
        errs.append(f"{where}: 범위 밖 시각: {s!r}")
        return None
    return v


def _int(v, where, errs, lo, hi, default=None):
    if v is None:
        v = default
    if isinstance(v, bool) or not isinstance(v, int):
        errs.append(f"{where}: 정수여야 한다: {v!r}")
        return default
    if not lo <= v <= hi:
        errs.append(f"{where}: {lo}..{hi} 범위여야 한다: {v}")
        return default
    return v


def _idlist(v, where, errs) -> "list[str]":
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        errs.append(f"{where}: 문자열 목록이어야 한다")
        return []
    if len(set(v)) != len(v):
        errs.append(f"{where}: 같은 id 가 두 번 있다")
    return list(v)


def _parse_participant(p, i, errs) -> "Participant | None":
    w = f"participants[{i}]"
    if not isinstance(p, dict):
        errs.append(f"{w}: 객체여야 한다")
        return None
    pid = p.get("id")
    if not isinstance(pid, str) or not _ID.match(pid):
        errs.append(f"{w}.id: 1~64자 영문·숫자·한글·_.- 여야 한다: {pid!r}")
        return None
    w = f"participant '{pid}'"
    tz = check_zone(p.get("tz"), f"{w}.tz", errs)

    wk = p.get("work") or {}
    if not isinstance(wk, dict):
        errs.append(f"{w}.work: 객체여야 한다")
        wk = {}
    ws = parse_hhmm(wk.get("start", "09:00"), f"{w}.work.start", errs, allow_24=False)
    we = parse_hhmm(wk.get("end", "18:00"), f"{w}.work.end", errs)
    days = wk.get("days", [0, 1, 2, 3, 4])
    if not isinstance(days, list) or not days or not all(
            isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 6 for x in days):
        errs.append(f"{w}.work.days: 0(월)..6(일) 정수의 비지 않은 목록이어야 한다")
        days = [0, 1, 2, 3, 4]
    if ws is not None and we is not None and ws == we % 1440 and we != 1440:
        errs.append(f"{w}.work: 시작과 끝이 같다 -- 창이 비었거나 24시간인지 모른다")
    work = WorkRule(ws or 0, we or 0, frozenset(days))

    core_raw = p.get("core")
    if core_raw is None:
        core = (work.start, work.end_abs)
    else:
        if not isinstance(core_raw, dict):
            errs.append(f"{w}.core: 객체여야 한다")
            core = (work.start, work.end_abs)
        else:
            cs = parse_hhmm(core_raw.get("start"), f"{w}.core.start", errs, allow_24=False)
            ce = parse_hhmm(core_raw.get("end"), f"{w}.core.end", errs)
            if cs is None or ce is None:
                core = (work.start, work.end_abs)
            else:
                # core 는 업무창과 같은 '창 시작일' 기준 분. 밤샘 업무면 다음 날 쪽으로 편다
                if work.end <= work.start and cs < work.start:
                    cs += 1440
                ce_abs = ce + (cs // 1440) * 1440
                if ce_abs <= cs:
                    ce_abs += 1440
                core = (cs, ce_abs)

    busy = []
    braw = p.get("busy") or []
    if not isinstance(braw, list) or len(braw) > MAX_BUSY_PER_PARTICIPANT:
        errs.append(f"{w}.busy: 최대 {MAX_BUSY_PER_PARTICIPANT}개의 목록이어야 한다")
        braw = []
    for j, b in enumerate(braw):
        if not isinstance(b, dict):
            errs.append(f"{w}.busy[{j}]: 객체여야 한다")
            continue
        s = parse_instant(b.get("start"), f"{w}.busy[{j}].start", errs)
        e = parse_instant(b.get("end"), f"{w}.busy[{j}].end", errs)
        if s and e:
            if e <= s:
                errs.append(f"{w}.busy[{j}]: 끝이 시작보다 앞이거나 같다")
            else:
                busy.append((s, e))

    holidays = set()
    for j, h in enumerate(p.get("holidays") or []):
        d = parse_date(h, f"{w}.holidays[{j}]", errs)
        if d:
            holidays.add(d)

    stays = []
    for j, s in enumerate(p.get("stays") or []):
        if not isinstance(s, dict):
            errs.append(f"{w}.stays[{j}]: 객체여야 한다")
            continue
        f = parse_date(s.get("from"), f"{w}.stays[{j}].from", errs)
        t = parse_date(s.get("to"), f"{w}.stays[{j}].to", errs)
        z = check_zone(s.get("tz"), f"{w}.stays[{j}].tz", errs)
        if f and t and z:
            if t < f:
                errs.append(f"{w}.stays[{j}]: to 가 from 보다 앞이다")
            else:
                stays.append(Stay(f, t, z))
    stays.sort(key=lambda s: s.first)
    for a, b in zip(stays, stays[1:]):
        if b.first <= a.last:
            errs.append(f"{w}.stays: 체류 기간이 겹친다 ({a.first}..{a.last} / {b.first}..{b.last}) "
                        f"-- 그날 어디 있는지 모르면 거절한다")

    cap = p.get("daily_cap_minutes")
    if cap is not None:
        cap = _int(cap, f"{w}.daily_cap_minutes", errs, 1, 1440)

    if tz is None:
        return None
    return Participant(pid, tz, work, core, sorted(busy), holidays, stays, cap)


def _parse_event(e, i, errs) -> "Event | None":
    w = f"events[{i}]"
    if not isinstance(e, dict):
        errs.append(f"{w}: 객체여야 한다")
        return None
    eid = e.get("id")
    if not isinstance(eid, str) or not _ID.match(eid):
        errs.append(f"{w}.id: 1~64자 영문·숫자·한글·_.- 여야 한다: {eid!r}")
        return None
    w = f"event '{eid}'"
    title = e.get("title", eid)
    if not isinstance(title, str) or len(title) > 200:
        errs.append(f"{w}.title: 200자 이하 문자열")
        title = eid
    dur = _int(e.get("duration_minutes"), f"{w}.duration_minutes", errs, 1, 1440, default=None)
    req = _idlist(e.get("required"), f"{w}.required", errs)
    opt = _idlist(e.get("optional"), f"{w}.optional", errs)
    if not req:
        errs.append(f"{w}.required: 비었다. 필수 참가자가 없으면 검증할 제약이 없다 -- "
                    f"그 '통과' 는 아무것도 재지 않은 초록불이라 거절한다")
    both = set(req) & set(opt)
    if both:
        errs.append(f"{w}: {sorted(both)} 가 required 와 optional 에 둘 다 있다")
    nb = parse_instant(e["not_before"], f"{w}.not_before", errs) if e.get("not_before") else None
    na = parse_instant(e["not_after"], f"{w}.not_after", errs) if e.get("not_after") else None
    fs = parse_instant(e["fixed_start"], f"{w}.fixed_start", errs) if e.get("fixed_start") else None
    after = _idlist(e.get("after"), f"{w}.after", errs)
    gap = _int(e.get("gap_after_deps_minutes", 0), f"{w}.gap_after_deps_minutes", errs, 0, 60 * 24 * 30, 0)
    pri = _int(e.get("priority", 0), f"{w}.priority", errs, -1000, 1000, 0)
    if dur is None:
        return None
    return Event(eid, title, dur, req, opt, nb, na, after, gap, pri, fs)


def parse_request(raw) -> Request:
    errs: "list[str]" = []
    if not isinstance(raw, dict):
        raise InputError(["요청은 JSON 객체여야 한다"])
    hz = raw.get("horizon")
    if not isinstance(hz, dict):
        raise InputError(["horizon: {start, end} 객체가 필요하다"])
    h0 = parse_instant(hz.get("start"), "horizon.start", errs)
    h1 = parse_instant(hz.get("end"), "horizon.end", errs)
    if h0 and h1:
        if h1 <= h0:
            errs.append("horizon: end 가 start 보다 앞이거나 같다")
        elif h1 - h0 > timedelta(days=MAX_HORIZON_DAYS):
            errs.append(f"horizon: {MAX_HORIZON_DAYS}일을 넘는다")
        if h0.second or h0.microsecond:
            errs.append("horizon.start: 분 단위여야 한다(초가 있으면 격자가 어긋난다)")
    slot = _int(raw.get("slot_minutes", 15), "slot_minutes", errs, 5, 240, 15)
    buf = _int(raw.get("buffer_minutes", 0), "buffer_minutes", errs, 0, 240, 0)
    search = raw.get("search") or {}
    if not isinstance(search, dict):
        errs.append("search: 객체여야 한다")
        search = {}
    max_nodes = _int(search.get("max_nodes", 200_000), "search.max_nodes", errs, 1, 5_000_000, 200_000)
    tl = search.get("time_limit_seconds", 10)
    if isinstance(tl, bool) or not isinstance(tl, (int, float)) or not 0.1 <= tl <= 120:
        errs.append("search.time_limit_seconds: 0.1..120")
        tl = 10

    praw = raw.get("participants")
    if not isinstance(praw, list) or not praw:
        errs.append("participants: 비지 않은 목록이 필요하다")
        praw = []
    if len(praw) > MAX_PARTICIPANTS:
        errs.append(f"participants: {MAX_PARTICIPANTS}명을 넘는다")
        praw = []
    parts: "dict[str, Participant]" = {}
    for i, p in enumerate(praw):
        pp = _parse_participant(p, i, errs)
        if pp:
            if pp.id in parts:
                errs.append(f"participant id '{pp.id}' 가 두 번 있다")
            parts[pp.id] = pp

    eraw = raw.get("events")
    if not isinstance(eraw, list) or not eraw:
        errs.append("events: 비었다. 일정이 0개인 계획은 성공이 아니라 아무것도 안 한 것이다")
        eraw = []
    if len(eraw) > MAX_EVENTS:
        errs.append(f"events: {MAX_EVENTS}개를 넘는다")
        eraw = []
    evs: "dict[str, Event]" = {}
    for i, e in enumerate(eraw):
        ee = _parse_event(e, i, errs)
        if ee:
            if ee.id in evs:
                errs.append(f"event id '{ee.id}' 가 두 번 있다")
            evs[ee.id] = ee

    for e in evs.values():
        for pid in e.required + e.optional:
            if pid not in parts:
                errs.append(f"event '{e.id}': 모르는 참가자 '{pid}'")
        for d in e.after:
            if d not in evs:
                errs.append(f"event '{e.id}'.after: 모르는 일정 '{d}'")
            elif d == e.id:
                errs.append(f"event '{e.id}'.after: 자기 자신")
        if e.not_before and e.not_after and e.not_after - e.not_before < timedelta(minutes=e.duration):
            errs.append(f"event '{e.id}': 창(not_before..not_after)이 길이보다 짧다")

    # 선후 관계에 고리가 있으면 영영 못 푼다 -- 탐색에 맡기지 않고 바로 거절한다
    state: "dict[str, int]" = {}

    def visit(x, path):
        if state.get(x) == 2:
            return
        if state.get(x) == 1:
            errs.append(f"after: 고리가 있다 ({' -> '.join(path + [x])})")
            return
        state[x] = 1
        for d in evs[x].after if x in evs else []:
            if d in evs:
                visit(d, path + [x])
        state[x] = 2

    for x in evs:
        visit(x, [])

    if errs:
        raise InputError(errs)
    return Request(h0, h1, slot, buf, parts, evs, max_nodes, float(tl), raw)


def to_min(dt: datetime) -> int:
    """UTC 분(에포크부터). 초는 내림."""
    return int(dt.timestamp()) // 60


def to_min_ceil(dt: datetime) -> int:
    t = int(dt.timestamp())
    return -((-t) // 60)


def from_min(m: int) -> datetime:
    return datetime.fromtimestamp(m * 60, UTC)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def tzdata_version() -> str:
    """판정이 기대는 tz 데이터베이스 판 -- zoneinfo 가 **실제로 읽는** 쪽을 말한다.

    zoneinfo 는 TZPATH(시스템)를 먼저 보고, 없을 때만 pip 의 tzdata 를 쓴다. 깔려 있다고 해서
    쓰이는 것이 아니다 -- 그래서 설치 여부가 아니라 찾는 순서대로 본다.
    """
    import zoneinfo
    from pathlib import Path
    for d in zoneinfo.TZPATH:
        if (Path(d) / "UTC").is_file():
            zi = Path(d) / "tzdata.zi"
            try:
                with zi.open(encoding="utf-8") as f:
                    line = f.readline().strip()
                return f"system {line.lstrip('# ').replace('version ', '')} ({d})"
            except OSError:
                return f"system (판 모름) ({d})"
    try:
        from importlib.metadata import version
        return "tzdata(pip) " + version("tzdata")
    except Exception:
        return "unknown"
