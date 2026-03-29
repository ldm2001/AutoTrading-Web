# KIS 잔고 조회 및 시장가 주문 모듈
import json
import time
import datetime

import httpx
from config import settings
from collections.abc import Callable

from service.kis.auth import Auth
from service.policy import Policy
from service.ttl_cache import TTLCache

# 잔고와 주문을 담당
class Trade:
    TTL_HOLDINGS = 10
    TTL_CASH = 10

    # 인증/캐시/정책/감사로그 의존성 주입
    def __init__(self, auth: Auth, cache: TTLCache, policy: Policy, audit: Callable[[dict], None] | None = None) -> None:
        self.auth = auth
        self.cache = cache
        self.policy = policy
        self._audit = audit

    # 감사 로그 기록
    def _log(self, entry: dict) -> None:
        if self._audit:
            self._audit(entry)

    # 보유 종목과 요약을 조회
    async def holdings(self) -> tuple[dict[str, dict], dict]:
        key = "holdings"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # 잔고 조회는 stale 없이 재시도만 허용
        async def slot() -> tuple[dict[str, dict], dict]:
            client = await self.auth.ready()
            resp = await client.get(
                "/uapi/domestic-stock/v1/trading/inquire-balance",
                headers=self.auth.header("TTTC8434R"),
                params={
                    "CANO": settings.cano,
                    "ACNT_PRDT_CD": settings.acnt_prdt_cd,
                    "AFHR_FLPR_YN": "N",
                    "OFL_YN": "",
                    "INQR_DVSN": "02",
                    "UNPR_DVSN": "01",
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN": "01",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            items: dict[str, dict] = {}
            for row in data["output1"]:
                if int(row["hldg_qty"]) > 0:
                    items[row["pdno"]] = {
                        "code": row["pdno"],
                        "name": row["prdt_name"],
                        "qty": int(row["hldg_qty"]),
                        "avg_price": int(float(row.get("pchs_avg_pric", "0"))),
                        "current_price": int(row.get("prpr", "0")),
                        "eval_amount": int(row.get("evlu_amt", "0")),
                        "profit_loss": int(row.get("evlu_pfls_amt", "0")),
                        "profit_loss_percent": float(row.get("evlu_pfls_rt", "0")),
                    }
            summary = data["output2"][0] if data["output2"] else {}
            return items, summary

        result = await self.policy.safe(key, slot, mark="holdings", stale=False)
        self.cache.set(key, result, self.TTL_HOLDINGS)
        return result

    # 주문 가능 현금을 조회한다.
    async def cash(self) -> int:
        key = "cash"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # 예수금 조회는 stale 없이 재시도만 허용
        async def slot() -> int:
            client = await self.auth.ready()
            resp = await client.get(
                "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
                headers=self.auth.header("TTTC8908R"),
                params={
                    "CANO": settings.cano,
                    "ACNT_PRDT_CD": settings.acnt_prdt_cd,
                    "PDNO": "005930",
                    "ORD_UNPR": "65500",
                    "ORD_DVSN": "01",
                    "CMA_EVLU_AMT_ICLD_YN": "Y",
                    "OVRS_ICLD_YN": "Y",
                },
            )
            resp.raise_for_status()
            return int(resp.json()["output"]["ord_psbl_cash"])

        result = await self.policy.safe(key, slot, mark="cash", stale=False)
        self.cache.set(key, result, self.TTL_CASH)
        return result

    # 시장가 주문을 전송
    async def order(self, code: str, qty: int, tr_id: str) -> dict:
        started = time.perf_counter()
        client = await self.auth.ready()
        body = {
            "CANO": settings.cano,
            "ACNT_PRDT_CD": settings.acnt_prdt_cd,
            "PDNO": code,
            "ORD_DVSN": "01",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        }
        headers = self.auth.header(tr_id)
        headers["hashkey"] = await self.auth.hash(body)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            resp = await client.post(
                "/uapi/domestic-stock/v1/trading/order-cash",
                headers=headers,
                content=json.dumps(body),
            )
            resp.raise_for_status()
            result = resp.json()
            ok = result.get("rt_cd") == "0"
            self._log({
                "time": stamp,
                "code": code,
                "qty": qty,
                "tr_id": tr_id,
                "status": resp.status_code,
                "success": ok,
                "rt_cd": result.get("rt_cd"),
                "msg_cd": result.get("msg_cd"),
                "msg1": result.get("msg1", ""),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            })
            if ok:
                self.cache.invalidate("holdings", "cash")
            return {"success": ok, "data": result}
        except Exception as err:
            status = None
            if isinstance(err, httpx.HTTPStatusError):
                status = err.response.status_code
            self._log({
                "time": stamp,
                "code": code,
                "qty": qty,
                "tr_id": tr_id,
                "status": status,
                "success": False,
                "error": str(err),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            })
            raise

    # 시장가 매수를 요청
    async def buy(self, code: str, qty: int) -> dict:
        return await self.order(code, qty, "TTTC0802U")

    # 시장가 매도를 요청
    async def sell(self, code: str, qty: int) -> dict:
        return await self.order(code, qty, "TTTC0801U")
