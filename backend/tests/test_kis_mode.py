import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from service.kis.trade import trid


# 주문 모드(실전/모의)에 따른 TR 코드 분기
class KisModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._url = settings.url_base

    def tearDown(self) -> None:
        settings.url_base = self._url

    # 실전 URL → TTTC* + mock=False
    def test_real(self):
        settings.url_base = "https://openapi.koreainvestment.com:9443"
        self.assertFalse(settings.mock)
        self.assertEqual(trid("buy"), "TTTC0802U")
        self.assertEqual(trid("sell"), "TTTC0801U")
        self.assertEqual(trid("holdings"), "TTTC8434R")
        self.assertEqual(trid("cash"), "TTTC8908R")

    # 모의 URL → VTTC* + mock=True
    def test_mock(self):
        settings.url_base = "https://openapivts.koreainvestment.com:29443"
        self.assertTrue(settings.mock)
        self.assertEqual(trid("buy"), "VTTC0802U")
        self.assertEqual(trid("sell"), "VTTC0801U")
        self.assertEqual(trid("holdings"), "VTTC8434R")
        self.assertEqual(trid("cash"), "VTTC8908R")


if __name__ == "__main__":
    unittest.main()
