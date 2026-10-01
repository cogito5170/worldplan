"""도우미의 세 층 -- 시각 센서가 답할 것만 답하고, 나머지는 위로 보내는가. Claude 는 가짜로 바꿔 끼운다."""
import datetime as dt
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from tests._util import sample
from worldplan import assistant as A
from worldplan.http_server import Handler

NOW = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.timezone.utc)


class FakeClaude:
    def __init__(self):
        self.calls = []

    def ask(self, text, request, today):
        self.calls.append(text)
        return {"answer": "가짜", "tokens": {"input": 1, "cache_creation": 0, "cache_read": 10, "output": 2, "total": 13},
                "cost_usd": 0.001}


class FakeWalp:
    def act(self, text):
        return ("greet", False) if text.startswith("안녕") else ("knowledge", True)


class Clock(unittest.TestCase):
    def ans(self, q):
        r = A.clock_answer(q, sample(), NOW)
        return r and r["answer"]

    def test_정해진_변환(self):
        self.assertIn("02:00", self.ans("서울 2026-10-05 오후 3시는 뉴욕 몇 시야?"))
        self.assertIn("2026-10-04 15:00", self.ans("시드니 오전 9시는 LA 몇 시야 10/5"))

    def test_기준이_뒤에_와도_원천이다(self):
        a = self.ans("10월 8일 오전 10시 kim 은 몇 시야? 런던 기준으로")
        self.assertTrue(a.startswith("런던 2026-10-08 10:00"), a)

    def test_사람은_체류지를_따른다(self):
        # kim 은 10-08..09 베를린에 있다 -- 서울이 아니다
        self.assertIn("CEST", self.ans("런던 기준 오전 10시는 kim 몇 시? 10월 8일"))
        self.assertIn("KST", self.ans("kim 쪽은 지금 몇 시?"))

    def test_정해지지_않으면_손대지_않는다(self):
        for q in ("3시에 서울은 뉴욕 몇 시",                      # 오전/오후 없음
                  "서울 오후 3시에 kim이랑 sam 회의 잡아줘",       # 일정 동사
                  "11월 1일 새벽 1시 30분 뉴욕은 서울 몇 시?",      # 가을 DST 로 두 번 있는 시각
                  "서울 오후 3시는 몇 시야",                        # 장소 하나
                  "지금 오후 3시인데 뉴욕은 몇 시?",               # '지금' 과 시각이 같이
                  "안녕"):
            self.assertIsNone(A.clock_answer(q, sample(), NOW), q)


class Layers(unittest.TestCase):
    def test_세_층(self):
        fc = FakeClaude()
        a = A.Assistant(walp=FakeWalp(), claude=fc)
        r = a.ask("안녕", sample(), NOW)
        self.assertEqual((r["by"], r["tokens"]["total"]), ("walp", 0))
        r = a.ask("지금 베를린 몇 시야", sample(), NOW)
        self.assertEqual(r["route"], "시각 변환")
        r = a.ask("다음 주 계획 세워줘", sample(), NOW)
        self.assertEqual((r["by"], r["tokens"]["total"]), ("claude", 13))
        self.assertEqual(fc.calls, ["다음 주 계획 세워줘"])

    def test_앞단을_끄면_전부_Claude(self):
        fc = FakeClaude()
        a = A.Assistant(walp=FakeWalp(), claude=fc, front=False)
        for q in ("안녕", "지금 베를린 몇 시야"):
            self.assertEqual(a.ask(q, sample(), NOW)["by"], "claude")
        self.assertEqual(len(fc.calls), 2)

    def test_WALP_가_없으면_그렇다고_적는다(self):
        a = A.Assistant(walp=None, claude=FakeClaude())
        self.assertEqual(a.ask("안녕", sample(), NOW)["route"], "앞단 없음 -> Claude")


class Http(unittest.TestCase):
    def test_api_assistant(self):
        A._DEFAULT = A.Assistant(walp=FakeWalp(), claude=FakeClaude())
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def post(body):
                r = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/assistant",
                                           data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(r, timeout=10) as resp:
                        return resp.status, json.loads(resp.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())
            st, b = post({"q": "안녕"})
            self.assertEqual((st, b["by"]), (200, "walp"))
            st, _ = post({"q": ""})
            self.assertEqual(st, 400)
        finally:
            srv.shutdown(); srv.server_close()
            A._DEFAULT = None


if __name__ == "__main__":
    unittest.main()
