import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.trading.journal as journal_module
from service.trading.journal import TradeJournal


# 알림 스텁
class _Notifier:
    def __init__(self) -> None:
        self.msgs: list[str] = []

    async def msg(self, text: str) -> None:
        self.msgs.append(text)


# TradeJournal — 기록/이벤트/복원
class TradeJournalTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._append = journal_module.trade_log_append
        self._rows = journal_module.trade_log_rows
        journal_module.trade_log_append = lambda _e: None

    def tearDown(self) -> None:
        journal_module.trade_log_append = self._append
        journal_module.trade_log_rows = self._rows

    # rec — 로그 누적 + snap 호출 + ontrade 이벤트 + 알림
    async def test_rec_records_and_fires(self):
        snaps = {"n": 0}
        notifier = _Notifier()
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
    async def test_load_restores(self):
        journal_module.trade_log_rows = lambda: [{"x": 1}]
        j = TradeJournal(_Notifier(), lambda: None)
        j.load()
        self.assertEqual(j.logs, [{"x": 1}])


if __name__ == "__main__":
    unittest.main()
