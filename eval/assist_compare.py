"""Claude 만 vs 앞단 + Claude -- 토큰 · 응답 시간 · 앞단 오답. 사전등록 eval/PREREG_앞단비교.md (v1) · PREREG_앞단비교_v2.md.

    WORLDPLAN_WALP_HOME=... WORLDPLAN_WALP_MODEL=... python3 eval/assist_compare.py <봉인.tsv> --set v1 --out eval/results/assist_v1.json

    v1  온라인: C(Claude 만) · W(시각 센서 + WALP 잡담)   오프라인(길만): R(시각 센서 + 낱말)
    v2  온라인: C(Claude 만, 지금 시각을 받음) · K(시각 센서만)   오프라인(길만): W · R

요청마다 두 조건을 **무작위 순서로 붙여서** 돌린다(캐시 · 시간대 표류를 두 조건이 같이 받게). 줄마다 체크포인트를 남겨
끊겨도 이어 돈다(WORLDPLAN_CKPT, 기본 /tmp/worldplan-ckpt).

R(낱말 앞단)은 Claude 를 부르지 않는다 -- 길만 고르고, Claude 로 갈 줄은 C 에서 잰 값을 그대로 쓴다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from worldplan import assistant as A      # noqa: E402
from worldplan import engine              # noqa: E402

SHAS = {"v1": "da1fa278372d24cee29b0a560b4f1ed0217f4aa9537a6bb3eb8854fbf3f51d63",
        "v2": "9bc0251ceca0e5e7ae3a6c365fbf1bb2d0e4521e231af892d52b760f0b9a7cc5"}
ONLINE = {"v1": "W", "v2": "K"}         # C 와 짝지어 실제로 Claude 를 부르는 처치
SAMPLE = Path(__file__).resolve().parent.parent / "worldplan" / "static" / "sample.json"
CKPT = Path(os.environ.get("WORLDPLAN_CKPT") or "/tmp/worldplan-ckpt")

# R: 낱말 앞단(사전등록에 적힌 그대로) -- WALP 가 낱말 목록보다 나은지 보려는 대조
KEYWORDS = {
    "greet": r"^\s*(안녕|하이|헬로|hello|hi\b|hey\b)",
    "thanks": r"고마|감사|땡큐|thank",
    "bye": r"잘\s*가|바이|bye|수고",
    "capability": r"뭘?\s*할\s*수|무엇을\s*할|뭐\s*할\s*줄|기능",
    "help": r"사용법|어떻게\s*써|어떻게\s*사용|도움말|\bhelp\b",
    "about_self": r"누구(야|세요|니)|너\s*뭐야|who are you",
}


def rows(path: Path, sha: str) -> list:
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise SystemExit("봉인 sha 가 다르다 -- 돌리지 않는다")
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if i == 0 or not line.strip():
            continue
        cat, msg, gold = (line.split("\t") + ["", ""])[:3]
        out.append({"i": len(out), "cat": cat, "msg": msg, "gold": json.loads(gold) if gold.strip() else None})
    return out


# ---------------- 채점 ----------------

_Q = r"(오전|오후|새벽|아침|저녁|밤|낮)?\s*(\d{1,2})\s*시\s*(?:(\d{1,2})\s*분|(반))?"


def times_in(text: str) -> set:
    """답 속 시각들(하루 안의 분). 오전/오후가 없으면 두 뜻을 다 넣는다 -- 두 조건에 똑같이 너그럽다."""
    got = set()
    for m in re.finditer(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)\s*(am|pm|AM|PM)?", text):
        h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
        if ap:
            got.add(((h % 12) + (12 if ap == "pm" else 0)) * 60 + mi)
        elif h <= 23:
            got.add(h * 60 + mi)
    for m in re.finditer(r"(?<![\d:])(\d{1,2})\s*(am|pm)\b", text, re.I):
        h = int(m.group(1))
        got.add(((h % 12) + (12 if m.group(2).lower() == "pm" else 0)) * 60)
    for m in re.finditer(_Q, text):
        ap, h = m.group(1), int(m.group(2))
        mi = 30 if m.group(4) else int(m.group(3) or 0)
        if h > 24 or mi > 59:
            continue
        if ap in ("오후", "저녁") or (ap == "밤" and h >= 6) or (ap == "낮" and h < 6):
            got.add(((h % 12) + 12) * 60 + mi)
        elif ap in ("오전", "새벽", "아침") or (ap == "밤" and h < 6):
            got.add((h % 12) * 60 + mi)
        else:
            got |= {(h % 24) * 60 + mi, ((h % 12) + 12) * 60 + mi}
    return got


def clock_ok(answer: str, gold: dict, at: dt.datetime) -> bool:
    if gold.get("dst_local") == "now":
        lt = at.astimezone(A.zone(gold["dst_tz"]))
        want = lt.hour * 60 + lt.minute
        return any(min(abs(t - want), 1440 - abs(t - want)) <= 2 for t in times_in(answer))
    hh, mm = map(int, gold["dst_local"][11:16].split(":"))
    return hh * 60 + mm in times_in(answer)


def front_error(r: dict, row: dict, at: dt.datetime) -> "str | None":
    """앞단이 답했는데 틀렸나. Claude 로 보낸 것은 앞단 오답이 아니다."""
    if r.get("by") != "walp":
        return None
    if r["route"] == "시각 변환":
        if row["cat"] != "clock":
            return f"시각 센서가 {row['cat']} 를 가로챘다"
        return None if clock_ok(r["answer"], row["gold"], at) else "시각이 틀렸다"
    if row["cat"] != "smalltalk":
        return f"잡담으로 답했는데 {row['cat']} 였다"
    return None


def route_R(msg: str, req: dict, now: dt.datetime) -> "dict | None":
    c = A.clock_answer(msg, req, now)
    if c:
        return {**c, "by": "walp", "route": "시각 변환"}
    for act, pat in KEYWORDS.items():
        if re.search(pat, msg, re.I):
            return {"answer": A.REPLY[act], "by": "walp", "route": f"낱말 {act}"}
    return None


# ---------------- 통계 ----------------

def boot_upper(d: list, n: int = 10000, q: float = 0.95) -> float:
    rng = random.Random(0)
    ms = sorted(sum(d[rng.randrange(len(d))] for _ in d) / len(d) for _ in range(n))
    return ms[int(q * n) - 1]


def wilson_upper(k: int, n: int, z: float = 1.645) -> float:
    if n == 0:
        return 1.0
    p = k / n
    return (p + z * z / (2 * n) + z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def summarize(recs: list, T: str = "W") -> dict:
    n = len(recs)
    out = {"n": n, "treatment": T}
    for k in [k for k in ("C", "K", "W", "R") if k in recs[0]]:
        tok = [r[k]["tokens"]["total"] for r in recs]
        ms = [r[k]["ms"] for r in recs]
        out[k] = {"tokens_sum": sum(tok), "tokens_mean": sum(tok) / n,
                  "cost_usd": round(sum(r[k].get("cost_usd") or 0 for r in recs), 4),
                  "ms_mean": sum(ms) / n, "ms_median": sorted(ms)[n // 2],
                  "claude_calls": sum(1 for r in recs if r[k].get("by") == "claude"),
                  "front_errors": sum(1 for r in recs if r[k].get("front_error")),
                  "claude_errors": sum(1 for r in recs if r[k].get("error"))}
        out[k]["front_error_rate"] = out[k]["front_errors"] / n
        out[k]["front_error_wilson95"] = wilson_upper(out[k]["front_errors"], n)
    dt_ = [r[T]["tokens"]["total"] - r["C"]["tokens"]["total"] for r in recs]
    dm = [r[T]["ms"] - r["C"]["ms"] for r in recs]
    out["H1"] = {"mean_diff_tokens": sum(dt_) / n, "upper95": boot_upper(dt_)}
    out["H1"]["holds"] = out["H1"]["upper95"] < 0
    out["H2"] = {"mean_diff_ms": sum(dm) / n, "upper95": boot_upper(dm)}
    out["H2"]["holds"] = out["H2"]["upper95"] < 0
    out["H3"] = {"rate": out[T]["front_error_rate"], "holds": out[T]["front_error_rate"] <= 0.05}
    out["decision_front_on"] = out["H1"]["holds"] and out["H3"]["holds"]
    both = [r for r in recs if r[T].get("by") == "claude"]
    if both:   # 대조: 둘 다 Claude 로 간 줄에서 차이는 잡음뿐이어야 한다
        d = [r[T]["tokens"]["total"] - r["C"]["tokens"]["total"] for r in both]
        dm2 = [r[T]["ms"] - r["C"]["ms"] for r in both]
        out["null_check_escalated"] = {"n": len(both), "mean_diff_tokens": sum(d) / len(d),
                                       "mean_diff_ms": sum(dm2) / len(dm2)}
    cats = {}
    for r in recs:
        c = cats.setdefault(r["cat"], {"n": 0})
        c["n"] += 1
        for k in [k for k in ("C", "K", "W", "R") if k in r]:
            c[f"{k}_tok"] = c.get(f"{k}_tok", 0) + r[k]["tokens"]["total"]
            c[f"{k}_ms"] = c.get(f"{k}_ms", 0.0) + r[k]["ms"]
            c[f"{k}_front"] = c.get(f"{k}_front", 0) + (r[k].get("by") == "walp")
            c[f"{k}_front_err"] = c.get(f"{k}_front_err", 0) + bool(r[k].get("front_error"))
            if r["cat"] == "clock" and "clock_ok" in r[k]:
                c[f"{k}_clock_ok"] = c.get(f"{k}_clock_ok", 0) + bool(r[k]["clock_ok"])
    out["by_category"] = cats
    return out


# ---------------- 돌리기 ----------------

def offline(name: str, row: dict, req: dict, res: dict, walp) -> dict:
    """Claude 를 부르지 않고 길만 고른다. Claude 로 갈 줄은 C 에서 잰 값을 그대로 쓴다."""
    at = dt.datetime.now(dt.timezone.utc)
    if name == "R":
        r = route_R(row["msg"], req, at)
    else:
        t0 = time.perf_counter()
        r = A.Assistant(walp=walp, claude=None, front="walp")._ask(row["msg"], req, at)
        r = r if r.get("by") == "walp" else None
        if r:
            r["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    if r:
        r.update(tokens=A._zero(), cost_usd=0.0)
        r.setdefault("ms", 5.0)
    else:
        r = {k: res["C"][k] for k in ("tokens", "cost_usd", "ms", "answer")} | {"by": "claude", "route": "Claude"}
    r["front_error"] = front_error(r, row, at)
    if row["cat"] == "clock" and r["by"] == "walp":
        r["clock_ok"] = clock_ok(r["answer"], row["gold"], at)
    elif row["cat"] == "clock":
        r["clock_ok"] = res["C"].get("clock_ok")
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sealed")
    ap.add_argument("--set", choices=sorted(SHAS), required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    data = rows(Path(a.sealed), SHAS[a.set])
    T = ONLINE[a.set]
    req = json.loads(SAMPLE.read_text(encoding="utf-8"))
    walp = A.load_walp()
    if not walp:
        raise SystemExit("WALP 가 없다 -- WORLDPLAN_WALP_HOME · WORLDPLAN_WALP_MODEL")
    claude = A.ClaudeCLI()
    arms = {"C": A.Assistant(walp=None, claude=claude, front="off"),
            T: A.Assistant(walp=walp, claude=claude, front="walp" if T == "W" else "clock")}
    ck = CKPT / a.set
    ck.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261001)
    orders = [rng.random() < 0.5 for _ in data]
    recs = []
    for row, c_first in zip(data, orders):
        p = ck / f"row{row['i']:03d}.json"
        if p.exists():
            recs.append(json.loads(p.read_text(encoding="utf-8")))
            continue
        res = {}
        for k in (("C", T) if c_first else (T, "C")):
            at = dt.datetime.now(dt.timezone.utc)
            r = arms[k].ask(row["msg"], req, now=at)
            r["at"] = at.isoformat()
            r["front_error"] = front_error(r, row, at)
            if row["cat"] == "clock":
                r["clock_ok"] = clock_ok(r["answer"], row["gold"], at)
            res[k] = r
        for k in [k for k in ("W", "R") if k not in res]:
            res[k] = offline(k, row, req, res, walp)
        rec = {"i": row["i"], "cat": row["cat"], "c_first": c_first, **res}
        p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        recs.append(rec)
        print(f"{row['i']:3d} {row['cat']:12s} C {res['C']['tokens']['total']:6d}tok {res['C']['ms']:8.0f}ms | "
              f"{T} {res[T]['route']:18s} {res[T]['tokens']['total']:6d}tok {res[T]['ms']:8.0f}ms"
              f"{' FRONT-ERR ' + res[T]['front_error'] if res[T]['front_error'] else ''}", flush=True)
    s = summarize(recs, T)
    s["model"] = sorted({r["C"].get("model") for r in recs if r["C"].get("model")})
    s["sealed_sha256"] = SHAS[a.set]
    keys = [k for k in ("C", "K", "W", "R") if k in recs[0]]
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps({"summary": s, "rows": [
            {"i": r["i"], "cat": r["cat"], **{k: {kk: r[k].get(kk) for kk in ("by", "route", "tokens", "cost_usd", "ms",
                                                                               "api_ms", "turns", "front_error", "clock_ok", "error")}
                                              for k in keys}} for r in recs]},
            ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in s.items() if k != "by_category"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
