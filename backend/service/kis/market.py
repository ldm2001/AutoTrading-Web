import asyncio
import datetime as dt
import logging

from config import settings
from service.kis.auth import Auth
from service.policy import Policy
from service.market.stock_universe import INDICES, NAMES
from service.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# 시세와 차트 조회를 담당
class Market:
    TTL_PRICE = 3
    TTL_OB = 3
    TTL_DAILY = 300
    TTL_15M = 300
    TTL_INDEX = 5
    TTL_TARGET = 30

    def __init__(self, auth: Auth, cache: TTLCache, policy: Policy) -> None:
        self.auth = auth
        self.cache = cache
        self.policy = policy

    # 현재가 요약을 조회
    async def price(self, code: str) -> dict:
        key = f"price:{code}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # 시세 조회 실패 시 마지막 성공 값을 허용
        async def slot() -> dict:
            client = await self.auth.ready()
            resp = await client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=self.auth.header("FHKST01010100"),
                params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
            )
            resp.raise_for_status()
            out = resp.json()["output"]
            return {
                "code": code,
                "name": NAMES.get(code, code),
                "price": int(out["stck_prpr"]),
                "change": int(out["prdy_vrss"]),
                "change_percent": float(out["prdy_ctrt"]),
                "volume": int(out["acml_vol"]),
                "market_cap": out.get("hts_avls", "0"),
                "market": out.get("rprs_mrkt_kor_name", ""),
            }

        result = await self.policy.safe(key, slot, mark="price", stale=True)
        self.cache.set(key, result, self.TTL_PRICE)
        return result

    # 현재가 숫자만 반환
    async def price_raw(self, code: str) -> int:
        return (await self.price(code))["price"]

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
                    "FID_ORG_ADJ_PRC": "1",
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

    # 15분봉을 묶어서 만듦
    async def candles_15m(self, code: str) -> list[dict]:
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

    # 단일 지수 시세를 조회
    async def index(self, code: str) -> dict:
        key = f"index:{code}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # 지수 조회 실패 시 마지막 성공 값을 허용
        async def slot() -> dict:
            client = await self.auth.ready()
            resp = await client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-index-price",
                headers=self.auth.header("FHPUP02100000"),
                params={"fid_cond_mrkt_div_code": "U", "fid_input_iscd": code},
            )
            resp.raise_for_status()
            out = resp.json()["output"]
            return {
                "value": float(out["bstp_nmix_prpr"]),
                "change": float(out.get("bstp_nmix_prdy_vrss", "0")),
                "change_percent": float(out.get("bstp_nmix_prdy_ctrt", "0")),
            }

        result = await self.policy.safe(key, slot, mark="index", stale=True)
        self.cache.set(key, result, self.TTL_INDEX)
        return result

    # 주요 지수 시세를 한 번에 모아서 조회
    async def indices(self) -> list[dict]:
        items = list(INDICES.items())

        async def one(name: str, api_code: str, label: str) -> dict | None:
            try:
                data = await self.index(api_code)
                entry = {"code": name, "name": label, **data}
                self.cache.set(f"idx_last:{name}", entry, 3600)
                return entry
            except Exception as e:
                logger.error("Index error (%s): %s", name, e)
                return self.cache.get(f"idx_last:{name}")

        result = await asyncio.gather(*[one(name, api_code, label) for name, (api_code, label) in items])
        return [item for item in result if item is not None]
