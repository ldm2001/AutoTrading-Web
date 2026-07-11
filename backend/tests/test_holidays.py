import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.market.holidays as holidays_module
from service.market.holidays import mkt


class MktTest(unittest.TestCase):
    # 주말은 휴장
    def test_weekend(self):
        self.assertFalse(mkt(date(2026, 7, 11)))
        self.assertFalse(mkt(date(2026, 7, 12)))

    # 등록된 휴장일 — 대체공휴일(광복절 8/17, 개천절 10/5)과 연말휴장 포함
    def test_holidays(self):
        for month, day in ((1, 1), (2, 17), (5, 1), (8, 17), (10, 5), (12, 31)):
            self.assertFalse(mkt(date(2026, month, day)), (month, day))

    # 평일 개장일
    def test_weekday_open(self):
        self.assertTrue(mkt(date(2026, 7, 10)))
        self.assertTrue(mkt(date(2026, 9, 23)))

    # 미등록 연도는 주말만 체크 + 최초 1회만 경고
    def test_unregistered_year(self):
        holidays_module._warned_years.clear()
        with self.assertLogs(holidays_module.logger, level="WARNING"):
            self.assertTrue(mkt(date(2027, 7, 1)))
        with self.assertNoLogs(holidays_module.logger, level="WARNING"):
            self.assertTrue(mkt(date(2027, 7, 2)))


if __name__ == "__main__":
    unittest.main()
