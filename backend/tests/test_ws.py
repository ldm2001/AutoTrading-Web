import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.ws as ws_module


class _WS:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers
        self.closed_code: int | None = None
        self.accepted = False

    async def close(self, code: int) -> None:
        self.closed_code = code

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        raise RuntimeError("stop test loop")


class WebSocketSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._api_key = ws_module.settings.api_key
        ws_module.settings.api_key = "server-secret"

    def tearDown(self) -> None:
        ws_module.settings.api_key = self._api_key

    def test_trade_websocket_requires_allowed_origin_and_api_key(self):
        self.assertTrue(ws_module.wsok({
            "origin": "http://127.0.0.1:5173",
            "x-api-key": "server-secret",
        }))
        self.assertFalse(ws_module.wsok({
            "origin": "http://evil.example",
            "x-api-key": "server-secret",
        }))
        self.assertFalse(ws_module.wsok({
            "origin": "http://127.0.0.1:5173",
            "x-api-key": "wrong-secret",
        }))

    def test_price_websocket_uses_same_auth_gate(self):
        ws = _WS({
            "origin": "http://evil.example",
            "x-api-key": "server-secret",
        })

        import asyncio
        asyncio.run(ws_module.prices(ws))

        self.assertEqual(ws.closed_code, 1008)
        self.assertFalse(ws.accepted)


if __name__ == "__main__":
    unittest.main()
