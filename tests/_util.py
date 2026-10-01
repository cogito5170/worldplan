import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "worldplan" / "static" / "sample.json"

# 검사는 재는 것이지 남기는 것이 아니다 -- 원장은 늘 임시 자리에 쓴다
_TMP = tempfile.mkdtemp(prefix="worldplan-test-ledger-")
os.environ["WORLDPLAN_LEDGER_ROOT"] = _TMP


def sample():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))
