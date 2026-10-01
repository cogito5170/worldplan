"""웹 화면의 디자인 토큰 -- gentleMonster frontend engine 으로 짓는다(빌드할 때만).

worldplan 을 gentleMonster 의 job 하나로 적는다(브랜드 · 팔레트 · 강조색 하나 · 조명 · 재료). 그 job 에서
gentle_monster.engine.tokens 가 색(WCAG 대비까지 밀어 올린) · 유동 타입 스케일 · 8px 간격 · 움직임을 계산하고,
그것을 static/tokens.css(CSS 변수) 와 static/tokens.json(W3C DTCG) 으로 적는다.

실행할 때는 gentleMonster 가 필요 없다 -- 적힌 두 파일만 쓴다(worldplan 은 표준 라이브러리만).

    GENTLE_MONSTER_HOME=/path/to/gentlemonster python3 -m worldplan ui-build
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"

# worldplan 의 정체성 -- 밤의 남색(다른 시간대의 밤) · 종이 · 강조색 하나(지금 이 순간)
JOB = {
    "brand": "worldplan",
    "title": "World Schedule Planner",
    "room": {"light": "white_gallery"},
    "palette": [{"hex": "#0E1A2B"}, {"hex": "#F4F1EA"}, {"hex": "#3D5A80"}, {"hex": "#98A6B5"}],
    "accent": "#D9480F",
    "materials": [{"preset": "paper"}, {"preset": "concrete"}, {"preset": "steel"}, {"preset": "glass"}],
}
# 시간표를 다루는 도구라 움직임은 없다(still) -- 표는 읽혀야 하고, 움직이면 안 된다
GENOME = {"ratio": 1.25, "air": 1.0, "cols": 12, "tension": 0, "mast": "solid", "voice": "grotesk",
          "motion": "still", "order": "intent-first"}


def _gm():
    home = os.environ.get("GENTLE_MONSTER_HOME")
    if home and home not in sys.path:
        sys.path.insert(0, home)
    from gentle_monster.engine import tokens as T     # noqa: PLC0415 -- 빌드할 때만 있는 의존
    return T


def tokens() -> dict:
    T = _gm()
    return T.tokens(JOB, GENOME)


def build(out: Path = STATIC) -> dict:
    T = _gm()
    t = T.tokens(JOB, GENOME)
    css = ("/* gentleMonster frontend engine 이 지은 토큰 -- 손으로 고치지 말고 `worldplan ui-build` 로 다시 짓는다 */\n"
           + T.css_vars(t) + "\n")
    (out / "tokens.css").write_text(css, encoding="utf-8")
    (out / "tokens.json").write_text(json.dumps(T.dtcg(t), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return t


def bundle(out: Path) -> Path:
    """화면을 파일 하나로 묶는다 -- 심판은 file:// 로 열고 밖으로 나가는 요청을 막으므로, css · js · 예시를 안에 넣는다."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "tokens.css").read_text(encoding="utf-8") + (STATIC / "app.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    sample = (STATIC / "sample.json").read_text(encoding="utf-8")
    for tag in ('<link rel="stylesheet" href="/tokens.css">\n', '<link rel="stylesheet" href="/app.css">\n',
                '<script src="/app.js" defer></script>\n'):
        if tag not in html:
            raise SystemExit(f"index.html 에 {tag.strip()} 가 없다 -- 묶기 규칙이 낡았다")
        html = html.replace(tag, "")
    head = (f"<style>\n{css}</style>\n<script type=\"application/json\" id=\"sample-data\">{sample}</script>\n"
            f"<script defer src=\"data:text/javascript;base64,{_b64(js)}\"></script>\n")
    html = html.replace("</head>", head + "</head>")
    out.write_text(html, encoding="utf-8")
    return out


def _b64(s: str) -> str:
    import base64
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def check(out_dir: "Path | None" = None) -> dict:
    """gentleMonster 의 심판(Chromium, 375 px · 1440 px)으로 화면을 잰다. V 가 하나라도 거짓이면 쓸 수 없는 화면이다."""
    import tempfile
    _gm()
    from gentle_monster.engine import judge as J      # noqa: PLC0415
    d = Path(out_dir or tempfile.mkdtemp(prefix="worldplan-ui-"))
    p = bundle(d / "index.html")
    return J.measure(p, tokens())
