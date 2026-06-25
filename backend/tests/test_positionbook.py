import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.trading.positionbook import PositionBook


# Redis 없는 캐시 스텁
class _Cache:
    redis = None


# 잔고 스텁
class _Broker:
    def __init__(self, holdings: dict[str, dict]) -> None:
        self.cache = _Cache()
        self._h = holdings

    async def holdings(self) -> tuple[dict[str, dict], dict]:
        return self._h, {}


# PositionBook — 손절가 승계 / 제거 / 잔고 대조
class PositionBookTest(unittest.IsolatedAsyncioTestCase):
    # acct — 기존 손절가 승계 + 명시값 우선
    def test_acct_stop_price(self):
        pb = PositionBook(_Broker({}), {})
        pb.bought = {"005930": {"stop_price": 900.0}}
        self.assertEqual(pb.acct("005930", {"avg_price": 1000, "qty": 1})["stop_price"], 900.0)
        self.assertEqual(pb.acct("005930", {"avg_price": 1000, "qty": 1}, stop_price=950.0)["stop_price"], 950.0)

    # drop — 보유 제거
    def test_drop_removes(self):
        pb = PositionBook(_Broker({}), {})
        pb.bought = {"005930": {"qty": 1}}
        pb.drop("005930")
        self.assertNotIn("005930", pb.bought)

    # pend — 대기 매수를 실제 잔고로 반영 후 pending 비움
    async def test_pend_reconciles(self):
        broker = _Broker({"005930": {"avg_price": 1000, "qty": 2, "name": "삼성전자"}})
        pb = PositionBook(broker, {})
        pb.pending_buys = {"005930"}
        await pb.pend()
        self.assertIn("005930", pb.bought)
        self.assertNotIn("005930", pb.pending_buys)
        self.assertEqual(pb.bought["005930"]["qty"], 2)


if __name__ == "__main__":
    unittest.main()
