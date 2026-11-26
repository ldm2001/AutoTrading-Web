import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.kis import kis
from service.trading.ports import Account, Orders, Quotes

# KIS facade가 매매 도메인 포트를 구조적으로 충족하는지 (ISP/DIP 계약)
class PortsConformanceTest(unittest.TestCase):
    def testquotes(self):
        self.assertIsInstance(kis, Quotes)

    def testaccount(self):
        self.assertIsInstance(kis, Account)

    def testorders(self):
        self.assertIsInstance(kis, Orders)

if __name__ == "__main__":
    unittest.main()
