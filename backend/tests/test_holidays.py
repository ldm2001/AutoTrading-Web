import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.market.holidays as holidays_module
import service.trading.bot as bot_module
import service.trading.journal as journal_module
from service.market.holidays import mkt
from service.trading.bot import Bot


class MktTest(unittest.TestCase):
    # 주말은 휴장
    def testweekend(self):
        self.assertFalse(mkt(date(2026, 7, 11)))
        self.assertFalse(mkt(date(2026, 7, 12)))

    # 등록된 휴장일 — 대체공휴일(광복절 8/17, 개천절 10/5)과 연말휴장 포함
    def testholidays(self):
        for month, day in ((1, 1), (2, 17), (5, 1), (8, 17), (10, 5), (12, 31)):
            self.assertFalse(mkt(date(2026, month, day)), (month, day))

    # M-07 — 2026 지방선거(6/3)·제헌절(7/17) 휴장
    def testextra(self):
        self.assertFalse(mkt(date(2026, 6, 3)))
        self.assertFalse(mkt(date(2026, 7, 17)))

    # 평일 개장일
    def testweekday(self):
        self.assertTrue(mkt(date(2026, 7, 10)))
        self.assertTrue(mkt(date(2026, 9, 23)))

    # 등록된 휴장일은 모두 실재하는 평일 (오타 방지 — 주말·없는 날짜면 실패)
    def testregistered(self):
        for year, days in holidays_module._KRX_HOLIDAYS.items():
            for month, day in days:
                self.assertLess(date(year, month, day).weekday(), 5, (year, month, day))

    # 미등록 연도는 주말만 체크 + 최초 1회만 경고
    def testunregyear(self):
        holidays_module._warned_years.clear()
        with self.assertLogs(holidays_module.logger, level="WARNING"):
            self.assertTrue(mkt(date(2027, 7, 1)))
        with self.assertNoLogs(holidays_module.logger, level="WARNING"):
            self.assertTrue(mkt(date(2027, 7, 2)))

    # 미등록 연도의 평일은 개장 유지 (휴장 간주로 바꾸면 손절 감시가 선다), 주말은 휴장
    def testunregday(self):
        self.assertTrue(mkt(date(2099, 1, 1)))
        self.assertFalse(mkt(date(2099, 1, 3)))

    # 미등록 연도 — 올해, 12월이면 내년까지 (2027 등록 시 아래 두 기대값 갱신)
    def testunregyears(self):
        unregistered = holidays_module.unregistered
        self.assertEqual(unregistered(date(2026, 11, 23)), [])
        self.assertEqual(unregistered(date(2026, 12, 1)), [2027])
        self.assertEqual(unregistered(date(2027, 1, 4)), [2027])


# S-07 — 가장 최근 확정 거래일(개장일 15:30 이후면 오늘, 아니면 직전 개장일)과 다음 개장일
class LastdayTest(unittest.TestCase):
    # (지금, 기대 확정 거래일) — 15:30 경계는 settled()와 같음
    def testtable(self):
        cases = [
            (datetime(2026, 11, 20, 16, 0), date(2026, 11, 20)),       # 금 장 마감 후 → 오늘
            (datetime(2026, 11, 24, 10, 0), date(2026, 11, 23)),       # 화 장중 → 월
            (datetime(2026, 11, 24, 15, 30), date(2026, 11, 24)),      # 15:30 정각 → 오늘
            (datetime(2026, 11, 24, 15, 29, 59), date(2026, 11, 23)),  # 15:30 직전 → 월
            (datetime(2026, 11, 22, 12, 0), date(2026, 11, 20)),       # 일요일 → 금
            (datetime(2026, 10, 6, 10, 0), date(2026, 10, 2)),         # 화 장중, 월 10/5 휴장 → 금
            (datetime(2026, 12, 25, 16, 0), date(2026, 12, 24)),       # 휴장일은 마감 후에도 직전 개장일
        ]
        for now, want in cases:
            with self.subTest(now=now):
                self.assertEqual(holidays_module.lastday(now), want)

    # 다음 개장일 — 주말·휴장일을 건너뜀
    def testnext(self):
        cases = [
            (date(2026, 11, 20), date(2026, 11, 23)),  # 금 → 월
            (date(2026, 11, 23), date(2026, 11, 24)),  # 월 → 화
            (date(2026, 10, 2), date(2026, 10, 6)),    # 금 → 월 10/5 휴장 → 화
            (date(2026, 12, 24), date(2026, 12, 28)),  # 목 → 성탄절·주말 → 월
        ]
        for day, want in cases:
            with self.subTest(day=day):
                self.assertEqual(holidays_module.nextday(day), want)


# 틱 큐 스텁 — boot만 노출
class QueueStub:
    async def boot(self) -> None:
        return None


# Redis 없는 캐시 스텁
class CacheStub:
    redis = None


# 최소 broker 스텁 — loop 진입용 잔고 조회만
class BrokerStub:
    def __init__(self) -> None:
        self.cache = CacheStub()

    async def cash(self) -> int:
        return 0

    async def holdings(self) -> tuple[dict, dict]:
        return {}, {"scts_evlu_amt": "0", "evlu_pfls_smtl_amt": "0", "tot_evlu_amt": "0"}


# M-07 — 휴장일 미등록 경보: 기동 시·날짜가 바뀐 뒤 1회, 같은 날 중복 없음, 경보 실패가 기동을 깨지 않음
class CalendarAlertTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.sent: list[str] = []
        self.fail_alert = False
        self._rows = journal_module.traderows
        journal_module.traderows = lambda: []
        self.bot = Bot(BrokerStub(), QueueStub(), {}, lambda: [])
        self.bot.msg = self.cap

        # 즉시 종료 loop — boot가 띄우는 supervisor 태스크를 바로 끝냄
        async def quickloop() -> None:
            return None

        self.quickloop = quickloop

    def tearDown(self) -> None:
        journal_module.traderows = self._rows

    # msg 캡처 — fail_alert면 경보 문구에서만 예외
    async def cap(self, text: str) -> None:
        if self.fail_alert and "[휴장일 미등록]" in text:
            raise RuntimeError("discord down")
        self.sent.append(text)

    def alerts(self) -> list[str]:
        return [m for m in self.sent if "[휴장일 미등록]" in m]

    # boot → 자연 종료 1회
    async def cycle(self) -> None:
        self.bot.loop = self.quickloop
        await self.bot.boot()
        await self.bot._task

    # 미등록이면 기동 시 1회, 같은 날 재기동에는 다시 보내지 않음
    async def testbootalert(self):
        with mock.patch.object(bot_module, "unregistered", lambda _d: [2027]):
            await self.cycle()
            await self.cycle()
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn("2027년", self.alerts()[0])

    # 등록돼 있으면 경보 없음
    async def testnoalert(self):
        with mock.patch.object(bot_module, "unregistered", lambda _d: []):
            await self.cycle()
        self.assertEqual(self.alerts(), [])

    # loop도 날짜 기준으로 1회 — 기동 없이 loop만 두 번 돌아도 1건 (mkt 호출 동작은 그대로: False면 종료 문구)
    async def testloopalert(self):
        self.bot.running = True
        with mock.patch.object(bot_module, "unregistered", lambda _d: [2027]), \
                mock.patch.object(bot_module, "mkt", lambda _d: False):
            await self.bot.loop()
            await self.bot.loop()
        self.assertEqual(len(self.alerts()), 1)
        self.assertEqual(sum("주말/휴장일이므로" in m for m in self.sent), 2)

    # 날짜가 바뀐 뒤 첫 호출에 다시 1회
    async def testnextday(self):
        with mock.patch.object(bot_module, "unregistered", lambda d: [d.year + 1]):
            await self.bot.calendar(date(2026, 12, 1))
            await self.bot.calendar(date(2026, 12, 1))
            await self.bot.calendar(date(2026, 12, 2))
        self.assertEqual(len(self.alerts()), 2)

    # notifier 예외에도 기동 계속 (시작 문구까지 나감)
    async def testalertfault(self):
        self.fail_alert = True
        with mock.patch.object(bot_module, "unregistered", lambda _d: [2027]):
            await self.cycle()
        self.assertTrue(any("자동매매 시작" in m for m in self.sent))

    # 판정 함수 예외에도 기동 계속
    async def testprobefault(self):
        def boom(_d):
            raise RuntimeError("table broken")

        with mock.patch.object(bot_module, "unregistered", boom):
            await self.cycle()
        self.assertTrue(any("자동매매 시작" in m for m in self.sent))


if __name__ == "__main__":
    unittest.main()
