import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.trading.journal as journal_module
from service.trading.journal import TradeJournal


# 알림 스텁
class NotifierStub:
    def __init__(self) -> None:
        self.msgs: list[str] = []

    async def msg(self, text: str) -> None:
        self.msgs.append(text)


# TradeJournal — 기록/이벤트/복원
class TradeJournalTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._append = journal_module.traderec
        self._rows = journal_module.traderows
        journal_module.traderec = lambda _e: None

    def tearDown(self) -> None:
        journal_module.traderec = self._append
        journal_module.traderows = self._rows

    # rec — 로그 누적 + snap 호출 + ontrade 이벤트 + 알림
    async def testrecord(self):
        snaps = {"n": 0}
        notifier = NotifierStub()
        j = TradeJournal(notifier, lambda: snaps.__setitem__("n", snaps["n"] + 1))
        events: list[dict] = []

        async def on(e: dict) -> None:
            events.append(e)

        j.ontrade = on
        await j.rec("005930", "삼성전자", "buy", 1, 70000, True)

        self.assertEqual(len(j.logs), 1)
        self.assertEqual(j.logs[0]["type"], "buy")
        self.assertEqual(snaps["n"], 1)
        self.assertEqual(len(events), 1)
        self.assertIn("매수 성공", notifier.msgs[0])

    # load — 파일에서 오늘 내역 복원
    async def testrecovery(self):
        journal_module.traderows = lambda: [{"x": 1}]
        j = TradeJournal(NotifierStub(), lambda: None)
        j.recovery()
        self.assertEqual(j.logs, [{"x": 1}])


if __name__ == "__main__":
    unittest.main()
