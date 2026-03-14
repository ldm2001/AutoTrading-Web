# KIS 실시간 체결 스트림
import asyncio
import datetime as dt
import json
import logging
import time

import websockets

from service.kis.auth import Auth
from service.market.price_sync import PriceSync
from service.market.stock_universe import ALL_STOCKS, NAMES

logger = logging.getLogger(__name__)

_TRY = "/tryitout"
_PING = "PINGPONG"
_TR = "H0STCNT0"
_STALE = 10.0
_GAP = 0.05
_COLS = [
    "MKSC_SHRN_ISCD",
    "STCK_CNTG_HOUR",
    "STCK_PRPR",
    "PRDY_VRSS_SIGN",
    "PRDY_VRSS",
    "PRDY_CTRT",
    "WGHN_AVRG_STCK_PRC",
    "STCK_OPRC",
    "STCK_HGPR",
    "STCK_LWPR",
    "ASKP1",
    "BIDP1",
    "CNTG_VOL",
    "ACML_VOL",
    "ACML_TR_PBMN",
    "SELN_CNTG_CSNU",
    "SHNU_CNTG_CSNU",
    "NTBY_CNTG_CSNU",
    "CTTR",
    "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN",
    "CCLD_DVSN",
    "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE",
    "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR",
    "HGPR_HOUR",
    "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_PRPR",
    "LWPR_HOUR",
    "LWPR_VRSS_PRPR_SIGN",
    "LWPR_VRSS_PRPR",
    "BSOP_DATE",
    "NEW_MKOP_CLS_CODE",
    "TRHT_YN",
    "ASKP_RSQN1",
    "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN",
    "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL",
    "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE",
    "MRKT_TRTM_CLS_CODE",
    "VI_STND_PRC",
]


# 실시간 체결가 연결과 최신 상태를 관리
class KISWS:
    def __init__(self, auth: Auth, pipe: PriceSync) -> None:
        self.auth = auth
        self.pipe = pipe
        self._task: asyncio.Task | None = None
        self._ws = None
        self._codes: tuple[str, ...] = ()
        self._want: tuple[str, ...] = ()
        self._seen = 0.0
        self._rows: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    # 최신 상태를 seed 한다
    def seed(self, items: list[dict]) -> None:
        for item in items:
            code = str(item.get("code", "")).zfill(6)
            if not code:
                continue
            self._rows[code] = dict(item)

    # 최신 시세 목록을 반환
    def rows(self, codes: list[str] | tuple[str, ...] | None = None) -> list[dict]:
        source = self._codes if codes is None else tuple(str(code).zfill(6) for code in codes if code)
        out: list[dict] = []
        for code in source:
            row = self._rows.get(code)
            if row is not None:
                out.append(dict(row))
        return out

    # 최근 수신 여부를 반환
    def live(self) -> bool:
        return self._task is not None and not self._task.done() and (time.time() - self._seen) < _STALE

    # 구독 종목을 맞춘다
    async def sync(self, codes: list[str]) -> None:
        want = tuple(dict.fromkeys(str(code).zfill(6) for code in codes if code))
        async with self._lock:
            if want == self._want and self._task is not None and not self._task.done():
                return
            self._want = want
            await self.close()
            if not want:
                return
            self._task = asyncio.create_task(self._run())

    # 연결을 닫는다
    async def close(self) -> None:
        task = self._task
        self._task = None
        self._codes = ()
        self._seen = 0.0
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # 구독 메시지를 만든다
    def _msg(self, key: str, code: str) -> dict:
        return {
            "header": {
                "approval_key": key,
                "content-type": "utf-8",
                "tr_type": "1",
                "custtype": "P",
            },
            "body": {
                "input": {
                    "tr_id": _TR,
                    "tr_key": code,
                }
            },
        }

    # 실시간 시간값을 datetime 으로 바꾼다
    def _ts(self, row: dict) -> dt.datetime:
        day = row.get("BSOP_DATE", "")
        hour = row.get("STCK_CNTG_HOUR", "")
        if len(day) == 8 and len(hour) == 6:
            try:
                return dt.datetime.strptime(day + hour, "%Y%m%d%H%M%S")
            except ValueError:
                pass
        return dt.datetime.now()

    # 실시간 row 를 공통 시세 shape 로 바꾼다
    def _row(self, row: dict) -> dict | None:
        code = str(row.get("MKSC_SHRN_ISCD", "")).zfill(6)
        if not code:
            return None

        price = int(row.get("STCK_PRPR", 0) or 0)
        if price <= 0:
            return None

        change = int(row.get("PRDY_VRSS", 0) or 0)
        change_pct = float(row.get("PRDY_CTRT", 0) or 0)
        if change_pct < 0 and change > 0:
            change = -change

        prev = self._rows.get(code, {})
        info = ALL_STOCKS.get(code, {}) if ALL_STOCKS else {}

        return {
            "code": code,
            "name": prev.get("name") or info.get("name") or NAMES.get(code, code),
            "price": price,
            "change": change,
            "change_percent": change_pct,
            "volume": int(row.get("ACML_VOL", 0) or 0),
            "market_cap": prev.get("market_cap", ""),
            "market": prev.get("market") or info.get("market", ""),
        }

    # 실시간 tick 을 처리한다
    async def _tick(self, row: dict) -> None:
        item = self._row(row)
        if item is None:
            return

        code = item["code"]
        self._rows[code] = item
        self._seen = time.time()
        await self.pipe.tick(
            code,
            item["price"],
            int(row.get("CNTG_VOL", 0) or 0),
            self._ts(row),
        )

    # 시스템 메시지를 처리한다
    async def _ack(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        head = data.get("header", {})
        tr_id = head.get("tr_id", "")
        if tr_id == _PING and self._ws is not None:
            try:
                await self._ws.pong(raw)
            except Exception:
                pass
            return

        body = data.get("body", {})
        if body:
            code = body.get("msg_cd", "")
            text = body.get("msg1", "")
            logger.info("KIS WS ack tr=%s code=%s msg=%s", tr_id, code, text)

    # 수신 raw 를 분해한다
    async def _feed(self, raw: str) -> None:
        if not raw:
            return

        if raw[0] in {"0", "1"}:
            part = raw.split("|", 3)
            if len(part) < 4:
                return
            cnt = int(part[2] or 0)
            body = part[3].split("^")
            width = len(_COLS)
            for idx in range(cnt):
                start = idx * width
                end = start + width
                if len(body) < end:
                    break
                row = dict(zip(_COLS, body[start:end]))
                await self._tick(row)
            return

        await self._ack(raw)

    # 실제 연결 루프
    async def _run(self) -> None:
        wait = 1.0
        while self._task is not None:
            try:
                key = await self.auth.approval()
                url = f"{self.auth.ws_url()}{_TRY}"
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    self._codes = self._want
                    for code in self._codes:
                        await ws.send(json.dumps(self._msg(key, code)))
                        await asyncio.sleep(_GAP)
                    logger.info("KIS WS open (%s symbols)", len(self._codes))

                    async for raw in ws:
                        await self._feed(raw)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                logger.warning("KIS WS fail: %s", err)
                await asyncio.sleep(wait)
                wait = min(wait * 2, 10.0)
            finally:
                self._ws = None
                self._codes = ()
