"""MCP(Model Context Protocol) 서버 -- 표준 라이브러리만으로 짓는다.

배포가 설치할 것이 없게(se_new '사람에게 설치를 시키지 마라'). 두 전송을 같은 handle() 이 처리한다:
  · stdio            -- `worldplan mcp`  (줄 하나 = JSON-RPC 메시지 하나)
  · Streamable HTTP  -- `worldplan serve` 의 POST /mcp  (http_server.py)

이 서버에 붙은 LLM 은 **생성자**일 수 있어도 **심판**은 아니다. LLM 이 낸 계획은
verify_world_schedule 로 결정적 심판에 올라가고, 심판이 REJECT 면 REJECT 다.
"""
from __future__ import annotations

import json
import sys
import traceback

from . import engine

SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_INFO = {"name": "worldplan", "title": "World Schedule Planner", "version": engine.VERSION}
INSTRUCTIONS = (
    "Plans multi-event schedules across world time zones. The engine separates GENERATOR from JUDGE: "
    "a deterministic judge (not an LLM) re-checks every hard constraint in each participant's local wall clock "
    "and REJECTS by default. If you (the model) propose a schedule yourself, submit it to verify_world_schedule "
    "and report the judge's verdict, not your own. Never call a plan 'optimal' unless search.status is "
    "'proven_optimal'. Always relay not_checked and invalidated_if to the user. "
    "All instants must carry a UTC offset (e.g. 2026-10-05T09:00+09:00); naive times are rejected."
)

_INSTANT = {"type": "string", "description": "ISO 8601 instant WITH offset, e.g. 2026-10-05T09:00+09:00 or ...Z"}
_PARTICIPANT = {
    "type": "object",
    "required": ["id", "tz"],
    "properties": {
        "id": {"type": "string"},
        "tz": {"type": "string", "description": "IANA zone, e.g. Asia/Seoul"},
        "work": {"type": "object", "description": "Working window in LOCAL wall clock. end<=start means overnight.",
                 "properties": {"start": {"type": "string", "default": "09:00"},
                                "end": {"type": "string", "default": "18:00"},
                                "days": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6},
                                         "description": "0=Mon..6=Sun (day the window starts)", "default": [0, 1, 2, 3, 4]}}},
        "core": {"type": "object", "description": "Preferred hours (soft). Minutes outside cost 10 each.",
                 "properties": {"start": {"type": "string"}, "end": {"type": "string"}}},
        "busy": {"type": "array", "items": {"type": "object", "required": ["start", "end"],
                                             "properties": {"start": _INSTANT, "end": _INSTANT}}},
        "holidays": {"type": "array", "items": {"type": "string", "format": "date"},
                     "description": "Local dates off. There is NO built-in public holiday database."},
        "stays": {"type": "array", "description": "Travel: inclusive local date ranges spent in another zone.",
                  "items": {"type": "object", "required": ["from", "to", "tz"],
                            "properties": {"from": {"type": "string", "format": "date"},
                                           "to": {"type": "string", "format": "date"},
                                           "tz": {"type": "string"}}}},
        "daily_cap_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
    },
}
_EVENT = {
    "type": "object",
    "required": ["id", "duration_minutes", "required"],
    "properties": {
        "id": {"type": "string"}, "title": {"type": "string"},
        "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
        "required": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "optional": {"type": "array", "items": {"type": "string"}},
        "not_before": _INSTANT, "not_after": _INSTANT, "fixed_start": _INSTANT,
        "after": {"type": "array", "items": {"type": "string"}, "description": "Event ids that must finish first"},
        "gap_after_deps_minutes": {"type": "integer", "minimum": 0},
        "priority": {"type": "integer"},
    },
}
_HORIZON = {"type": "object", "required": ["start", "end"], "properties": {"start": _INSTANT, "end": _INSTANT}}
_REQUEST = {
    "type": "object",
    "required": ["horizon", "participants", "events"],
    "properties": {
        "horizon": _HORIZON,
        "slot_minutes": {"type": "integer", "minimum": 5, "maximum": 240, "default": 15},
        "buffer_minutes": {"type": "integer", "minimum": 0, "maximum": 240, "default": 0},
        "participants": {"type": "array", "items": _PARTICIPANT, "minItems": 1},
        "events": {"type": "array", "items": _EVENT, "minItems": 1},
        "search": {"type": "object", "properties": {"max_nodes": {"type": "integer"},
                                                    "time_limit_seconds": {"type": "number"}}},
    },
}

TOOLS = [
    {
        "name": "plan_world_schedule",
        "title": "Plan a multi-event world schedule",
        "description": "Generate a schedule for all events across participants' time zones, then have the "
                       "deterministic judge verify it. verdict is ACCEPT only if every event is placed and the "
                       "judge finds zero violations. Returns local times per participant, cost, search status "
                       "(proven_optimal / best_found_not_proven / infeasible_proven / unknown), diagnostics "
                       "when infeasible, not_checked and invalidated_if.",
        "inputSchema": {"type": "object", "required": ["request"], "properties": {"request": _REQUEST}},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "verify_world_schedule",
        "title": "Judge a proposed schedule",
        "description": "Judge ANY proposed schedule (yours, a human's, another tool's) against the request's hard "
                       "constraints. Use this whenever you propose times yourself. Default verdict is REJECT.",
        "inputSchema": {"type": "object", "required": ["request", "assignments"], "properties": {
            "request": _REQUEST,
            "assignments": {"type": "object", "additionalProperties": _INSTANT,
                            "description": "{event_id: start instant with offset}"},
            "claimed_cost": {"type": "integer", "description": "Optional: if given, judge cross-checks it"},
        }},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "find_common_slots",
        "title": "Find common slots for one meeting",
        "description": "Ranked start times for a single meeting where all required participants are inside their "
                       "local working hours and free. Each returned slot was individually re-checked by the judge.",
        "inputSchema": {"type": "object", "required": ["participants", "horizon", "duration_minutes"], "properties": {
            "participants": {"type": "array", "items": _PARTICIPANT, "minItems": 1},
            "horizon": _HORIZON,
            "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
            "required": {"type": "array", "items": {"type": "string"}, "description": "default: all participants"},
            "optional": {"type": "array", "items": {"type": "string"}},
            "not_before": _INSTANT, "not_after": _INSTANT,
            "slot_minutes": {"type": "integer", "minimum": 5, "maximum": 240},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
        }},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "world_clock",
        "title": "Show one instant in many zones",
        "description": "Convert one instant to local time, UTC offset, abbreviation and DST flag in each IANA zone.",
        "inputSchema": {"type": "object", "required": ["instant", "zones"], "properties": {
            "instant": _INSTANT, "zones": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50}}},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "ledger_status",
        "title": "Decision ledger status",
        "description": "Re-verify the append-only hash-chained decision ledger and return the last n entries "
                       "(hashes and verdicts only; schedules are not stored).",
        "inputSchema": {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 200}}},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "describe_judge",
        "title": "What the judge checks (and does not)",
        "description": "List the hard-constraint checks the judge runs and what it explicitly does NOT check.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


def _call(name: str, a: dict):
    if name == "plan_world_schedule":
        return engine.plan(a.get("request"))
    if name == "verify_world_schedule":
        return engine.verify(a.get("request"), a.get("assignments"), a.get("claimed_cost"))
    if name == "find_common_slots":
        return engine.common_slots(a)
    if name == "world_clock":
        return engine.world_clock(a.get("instant"), a.get("zones"))
    if name == "ledger_status":
        return engine.ledger_status(a.get("n", 10))
    if name == "describe_judge":
        return engine.describe()
    raise KeyError(name)


def _err(mid, code, msg):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": msg}}


def handle(msg) -> "dict | list | None":
    """JSON-RPC 메시지 하나(또는 배치)를 처리한다. 알림이면 None."""
    if isinstance(msg, list):
        out = [r for r in (handle(m) for m in msg) if r is not None]
        return out or None
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method", ""), str):
        return _err(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method = msg.get("method")
    mid = msg.get("id")
    is_note = "id" not in msg
    if method is None:      # 클라이언트가 보낸 응답 -- 우리는 요청을 보내지 않으니 버린다
        return None
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return None if is_note else _err(mid, -32602, "params must be an object")
    try:
        if method == "initialize":
            want = params.get("protocolVersion")
            ver = want if want in SUPPORTED else SUPPORTED[0]
            res = {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name not in {t["name"] for t in TOOLS}:
                return _err(mid, -32602, f"Unknown tool: {name}")
            if not isinstance(args, dict):
                return _err(mid, -32602, "arguments must be an object")
            try:
                data = _call(name, args)
                res = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}],
                       "structuredContent": data, "isError": False}
            except Exception as e:  # 도구 실행 오류는 프로토콜 오류가 아니라 도구 결과다
                traceback.print_exc(file=sys.stderr)
                res = {"content": [{"type": "text", "text": f"내부 오류: {type(e).__name__}: {e}"}],
                       "isError": True}
        elif method.startswith("notifications/"):
            return None
        else:
            return None if is_note else _err(mid, -32601, f"Method not found: {method}")
    except Exception as e:  # pragma: no cover
        traceback.print_exc(file=sys.stderr)
        return None if is_note else _err(mid, -32603, f"Internal error: {e}")
    return None if is_note else {"jsonrpc": "2.0", "id": mid, "result": res}


def run_stdio(inp=None, out=None):
    """줄 단위 JSON-RPC. stdout 에는 프로토콜 메시지만 쓴다 -- 로그는 stderr."""
    inp = inp or sys.stdin
    out = out or sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = _err(None, -32700, "Parse error")
        else:
            resp = handle(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out.flush()
