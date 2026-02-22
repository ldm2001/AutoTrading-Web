# WebSocket 연결 매니저 및 가격 브로드캐스트
import asyncio
import datetime
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from service import kis

logger = logging.getLogger(__name__)
router = APIRouter()


# 웹소켓 연결 풀 관리
class WS:
    def __init__(self) -> None:
        self.price_clients: set[WebSocket] = set()
        self.trade_clients: set[WebSocket] = set()

    # 가격 채널 클라이언트 등록
    async def attach_price(self, ws: WebSocket) -> None:
        await ws.accept()
        self.price_clients.add(ws)

    # 거래 채널 클라이언트 등록
    async def attach_trade(self, ws: WebSocket) -> None:
        await ws.accept()
        self.trade_clients.add(ws)

    # 연결된 클라이언트에 데이터 브로드캐스트
    async def _broadcast(self, clients: set[WebSocket], data: dict) -> None:
        dead = []
        for ws in clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)

    # 가격 업데이트 브로드캐스트
    async def prices(self, data: dict) -> None:
        await self._broadcast(self.price_clients, data)

    # 거래 이벤트 브로드캐스트
    async def trade(self, data: dict) -> None:
        await self._broadcast(self.trade_clients, data)

    # 텍스트 메시지 브로드캐스트 (거래 채널)
    async def message(self, msg: str) -> None:
        await self.trade({"type": "message", "data": msg})


manager = WS()


@router.websocket("/ws/prices")
async def prices(websocket: WebSocket):
    await manager.attach_price(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, Exception):
        manager.price_clients.discard(websocket)


@router.websocket("/ws/trades")
async def trades(websocket: WebSocket):
    await manager.attach_trade(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, Exception):
        manager.trade_clients.discard(websocket)


# 실시간 가격 브로드캐스트 루프 — 장 시간(09:00~15:35 평일) 3초, 장외 30초
async def loop():
    while True:
        try:
            if manager.price_clients:
                stocks = await kis.prices()
                idx    = await kis.indices()
                await manager.prices({
                    "type":    "price_update",
                    "stocks":  stocks,
                    "indices": idx,
                })
        except Exception as e:
            logger.error(f"Price loop error: {e}")
        now  = datetime.datetime.now()
        mins = now.hour * 60 + now.minute
        # 평일 09:00 ~ 15:35 = 장 시간 → 3초, 그 외 → 30초
        if now.weekday() < 5 and 9 * 60 <= mins < 15 * 60 + 35:
            await asyncio.sleep(3)
        else:
            await asyncio.sleep(30)


# 하위 호환 별칭
price_loop = loop
