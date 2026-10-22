# KIS 시세 및 차트 데이터 조회 모듈
import asyncio
import datetime as dt
import logging

from config import settings
from service.kis.auth import Auth
from service.kis.policy import Policy
from service.market.stock_universe import INDICES, NAMES
from service.infra.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# 시세와 차트 조회를 담당
class Market:
    TTL_PRICE = 3
    TTL_OB = 3
    TTL_DAILY = 300
    TTL_15M = 300
    TTL_INDEX = 5
    TTL_TARGET = 30

    # 인증/캐시/정책 의존성 주입
    def __init__(self, auth: Auth, cache: TTLCache, policy: Policy) -> None:
        self.auth = auth
        self.cache = cache
        self.policy = policy

    # 현재가 요약을 조회 — stale=False면 장애 시 마지막 성공 값 대신 예외 전파
    async def price(self, code: str, stale: bool = True) -> dict:
        key = f"price:{code}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # 신선한 조회 값만 캐시 — stale 폴백 값이 캐시에 다시 실리지 않게
        async def slot() -> dict:
            client = await self.auth.ready()
            resp = await client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=self.auth.header("FHKST01010100"),
                params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
            )
            resp.raise_for_status()
            out = resp.json()["output"]
            result = {
                "code": code,
                "name": NAMES.get(code, code),
                "price": int(out["stck_prpr"]),
                "change": int(out["prdy_vrss"]),
                "change_percent": float(out["prdy_ctrt"]),
                "volume": int(out["acml_vol"]),
                "market_cap": out.get("hts_avls", "0"),
                "market": out.get("rprs_mrkt_kor_name", ""),
            }
            self.cache.set(key, result, self.TTL_PRICE)
            return result

        return await self.policy.safe(key, slot, mark="price", stale=stale)

    # 현재가 숫자만 반환 — 손절 판정용이라 stale 폴백 금지
    async def raw(self, code: str) -> int:
        return (await self.price(code, stale=False))["price"]

    # 5호가를 조회
    async def orderbook(self, code: str) -> dict:
        key = f"ob:{code}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # 호가 조회 실패 시 마지막 성공 값을 허용
        async def slot() -> dict:
            client = await self.auth.ready()
            resp = await client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                headers=self.auth.header("FHKST01010200"),
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
            )
            resp.raise_for_status()
            out = resp.json().get("output1", {})
            asks = []
            for idx in range(5, 0, -1):
                price = int(out.get(f"askp{idx}", 0) or 0)
                volume = int(out.get(f"askp_rsqn{idx}", 0) or 0)
                if price:
                    asks.append({"price": price, "volume": volume})
            bids = []
            for idx in range(1, 6):
                price = int(out.get(f"bidp{idx}", 0) or 0)
                volume = int(out.get(f"bidp_rsqn{idx}", 0) or 0)
                if price:
                    bids.append({"price": price, "volume": volume})
            return {"asks": asks, "bids": bids}

        result = await self.policy.safe(key, slot, mark="orderbook", stale=True)
        self.cache.set(key, result, self.TTL_OB)
        return result

    # 일봉 캔들을 조회
    async def daily(self, code: str, count: int = 60) -> list[dict]:
        key = f"daily:{code}:{count}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # 일봉 조회 실패 시 마지막 성공 값을 허용
        async def slot() -> list[dict]:
            client = await self.auth.ready()
            end = dt.date.today().strftime("%Y%m%d")
            start = (dt.date.today() - dt.timedelta(days=count * 2)).strftime("%Y%m%d")
            resp = await client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                headers=self.auth.header("FHKST03010100"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": start,
                    "FID_INPUT_DATE_2": end,
                    "FID_PERIOD_DIV_CODE": "D",
                    # 이 TR은 0=수정주가·1=원주가 — FHKST01010400(target)과 의미 반대, 값 복사 금지
                    "FID_ORG_ADJ_PRC": "0",
                },
            )
            resp.raise_for_status()
            candles = []
            for item in resp.json().get("output2", [])[:count]:
                day = item["stck_bsop_date"]
                candles.append({
                    "date": f"{day[:4]}-{day[4:6]}-{day[6:8]}",
                    "open": int(item["stck_oprc"]),
                    "high": int(item["stck_hgpr"]),
                    "low": int(item["stck_lwpr"]),
                    "close": int(item["stck_clpr"]),
                    "volume": int(item["acml_vol"]),
                })
            candles.reverse()
            return candles

        candles = await self.policy.safe(key, slot, mark="daily", stale=True)
        self.cache.set(key, candles, self.TTL_DAILY)
        return candles

    # 1분봉 3구간 수집 → 15분 단위 OHLCV 캔들로 집계
    async def c15(self, code: str) -> list[dict]:
        key = f"candles_15m:{code}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # 분봉 조회 실패 시 마지막 성공 값을 허용
        async def slot() -> list[dict]:
            client = await self.auth.ready()
            raw: list[dict] = []
            for hour in ("153000", "120000", "090000"):
                try:
                    resp = await client.get(
                        "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                        headers=self.auth.header("FHKST03010200"),
                        params={
                            "FID_ETC_CLS_CODE": "",
                            "FID_COND_MRKT_DIV_CODE": "J",
                            "FID_INPUT_ISCD": code,
                            "FID_INPUT_HOUR_1": hour,
                            "FID_PW_DATA_INCU_YN": "Y",
                        },
                    )
                    resp.raise_for_status()
                    raw.extend(resp.json().get("output2", []))
                except Exception:
                    continue

            seen: set[str] = set()
            buckets: dict[str, dict] = {}
            for item in raw:
                stamp = item.get("stck_bsop_date", "") + item.get("stck_cntg_hour", "")
                if len(stamp) < 12 or stamp in seen:
                    continue
                seen.add(stamp)
                try:
                    mark = dt.datetime.strptime(stamp, "%Y%m%d%H%M%S")
                except ValueError:
                    continue
                minute = (mark.minute // 15) * 15
                bucket = mark.replace(minute=minute, second=0, microsecond=0)
                key15 = bucket.strftime("%Y%m%d%H%M")
                open_ = int(item.get("stck_oprc", 0))
                high = int(item.get("stck_hgpr", 0))
                low = int(item.get("stck_lwpr", 0))
                close = int(item.get("stck_prpr", 0))
                volume = int(item.get("cntg_vol", 0))
                if key15 not in buckets:
                    buckets[key15] = {
                        "time": bucket,
                        "open": open_,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": volume,
                    }
                else:
                    row = buckets[key15]
                    row["high"] = max(row["high"], high)
                    row["low"] = min(row["low"], low)
                    row["close"] = close
                    row["volume"] += volume

            return sorted(buckets.values(), key=lambda item: item["time"])

        candles = await self.policy.safe(key, slot, mark="candles_15m", stale=True)
        self.cache.set(key, candles, self.TTL_15M)
        return candles

    # 여러 종목 현재가를 병렬 조회
    async def prices(self, codes: list[str] | None = None) -> list[dict]:
        source = codes or settings.symbol_list
        uniq = [code.zfill(6) for code in dict.fromkeys(source) if code]
        if not uniq:
            return []
        tasks = [self.price(code) for code in uniq]
        result = await asyncio.gather(*tasks, return_exceptions=True)
        return [item for item in result if not isinstance(item, Exception)]

    # 변동성 돌파 목표가를 계산
    async def target(self, code: str) -> float:
        key = f"target:{code}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # 목표가 조회 실패 시 마지막 성공 값을 허용
        async def slot() -> float:
            client = await self.auth.ready()
            resp = await client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-price",
                headers=self.auth.header("FHKST01010400"),
                params={
                    "fid_cond_mrkt_div_code": "J",
                    "fid_input_iscd": code,
                    # 이 TR은 1=수정주가반영 — FHKST03010100(daily)과 의미 반대, 값 복사 금지
                    "fid_org_adj_prc": "1",
                    "fid_period_div_code": "D",
                },
            )
            resp.raise_for_status()
            out = resp.json()["output"]
            return int(out[0]["stck_oprc"]) + (int(out[1]["stck_hgpr"]) - int(out[1]["stck_lwpr"])) * 0.5

        result = await self.policy.safe(key, slot, mark="target", stale=True)
        self.cache.set(key, result, self.TTL_TARGET)
        return result

    # 단일 지수 시세를 조회 — stale=False면 장애 시 마지막 성공 값 대신 예외 전파
    async def index(self, code: str, stale: bool = True) -> dict:
        key = f"index:{code}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # 신선한 조회 값만 캐시 — stale 폴백 값이 캐시에 다시 실리지 않게
        async def slot() -> dict:
            client = await self.auth.ready()
            resp = await client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-index-price",
                headers=self.auth.header("FHPUP02100000"),
                params={"fid_cond_mrkt_div_code": "U", "fid_input_iscd": code},
            )
            resp.raise_for_status()
            out = resp.json()["output"]
            result = {
                "value": float(out["bstp_nmix_prpr"]),
                "change": float(out.get("bstp_nmix_prdy_vrss", "0")),
                "change_percent": float(out.get("bstp_nmix_prdy_ctrt", "0")),
            }
            self.cache.set(key, result, self.TTL_INDEX)
            return result

        return await self.policy.safe(key, slot, mark="index", stale=stale)

    # 주요 지수 시세를 한 번에 모아서 조회 — stale=False면 하나라도 실패 시 예외 전파 (국면 게이트용)
    async def indices(self, stale: bool = True) -> list[dict]:
        items = list(INDICES.items())

        # 단일 지수 조회 → 실패 시 마지막 캐시 반환 (strict면 전파)
        async def one(name: str, api_code: str, label: str) -> dict | None:
            try:
                data = await self.index(api_code, stale=stale)
                entry = {"code": name, "name": label, **data}
                self.cache.set(f"idx_last:{name}", entry, 3600)
                return entry
            except Exception as e:
                logger.error("Index error (%s): %s", name, e)
                if not stale:
                    raise
                return self.cache.get(f"idx_last:{name}")

        result = await asyncio.gather(*[one(name, api_code, label) for name, (api_code, label) in items])
        return [item for item in result if item is not None]
