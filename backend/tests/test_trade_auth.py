import sys
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.routing import APIRoute

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.auth import guard
import api.trade as trade_module
from api.trade import WatchlistBody, router, watchput


def route(path: str, method: str) -> APIRoute:
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"Route not found: {method} {path}")


class TradeApiAuthTest(unittest.TestCase):
    def test_sensitive_trading_reads_require_api_key(self):
        sensitive_reads = [
            ("/api/trading/watchlist", "GET"),
            ("/api/trading/portfolio", "GET"),
            ("/api/trading/balance", "GET"),
            ("/api/trading/bot/status", "GET"),
            ("/api/trading/portfolio/heatmap", "GET"),
            ("/api/trading/history", "GET"),
            ("/api/trading/orders", "GET"),
        ]

        for path, method in sensitive_reads:
            with self.subTest(path=path, method=method):
                dependencies = route(path, method).dependant.dependencies
                self.assertTrue(
                    any(dep.call is guard for dep in dependencies),
                    f"{method} {path} must depend on guard",
                )

    def test_watchlist_normalizes_unique_numeric_codes(self):
        saved: list[list[str]] = []
        original = trade_module.save_watchlist
        trade_module.save_watchlist = lambda codes: saved.append(codes)
        try:
            import asyncio
            result = asyncio.run(watchput(
                WatchlistBody(codes=["5930", "005930", "000660", "005930"]),
                _key="ok",
            ))
        finally:
            trade_module.save_watchlist = original

        self.assertEqual(result, {"codes": ["005930", "000660"], "count": 2})
        self.assertEqual(saved, [["005930", "000660"]])

    def test_watchlist_rejects_invalid_codes(self):
        with self.assertRaises(HTTPException) as raised:
            import asyncio
            asyncio.run(watchput(
                WatchlistBody(codes=["005930", "bad-code"]),
                _key="ok",
            ))

        self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
