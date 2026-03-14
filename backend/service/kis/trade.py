import json
from config import settings
from service.kis_auth import Auth
from service.ttl_cache import TTLCache

# 잔고와 주문을 담당
class Trade:
    TTL_HOLDINGS = 10
    TTL_CASH = 10

    def __init__(self, auth: Auth, cache: TTLCache) -> None:
        self.auth = auth
        self.cache = cache

    # 보유 종목과 요약을 조회
    async def holdings(self) -> tuple[dict[str, dict], dict]:
        key = "holdings"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
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
        result = (items, summary)
        self.cache.set(key, result, self.TTL_HOLDINGS)
        return result

    # 주문 가능 현금을 조회한다.
    async def cash(self) -> int:
        key = "cash"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
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
        result = int(resp.json()["output"]["ord_psbl_cash"])
        self.cache.set(key, result, self.TTL_CASH)
        return result

    # 시장가 주문을 전송
    async def order(self, code: str, qty: int, tr_id: str) -> dict:
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
        resp = await client.post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            headers=headers,
            content=json.dumps(body),
        )
        resp.raise_for_status()
        result = resp.json()
        self.cache.invalidate("holdings", "cash")
        return {"success": result.get("rt_cd") == "0", "data": result}

    # 시장가 매수를 요청
    async def buy(self, code: str, qty: int) -> dict:
        return await self.order(code, qty, "TTTC0802U")

    # 시장가 매도를 요청
    async def sell(self, code: str, qty: int) -> dict:
        return await self.order(code, qty, "TTTC0801U")
