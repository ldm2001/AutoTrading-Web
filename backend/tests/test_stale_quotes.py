import logging
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.kis.market import Market
from service.kis.policy import Policy
from service.trading.entryengine import EntryEngine
from service.trading.riskmonitor import RiskMonitor


# 현재가 TR(FHKST01010100) 응답 바디
def quotebody(price: int) -> dict:
    return {"output": {"stck_prpr": str(price), "prdy_vrss": "0", "prdy_ctrt": "0.0", "acml_vol": "1"}}


# 업종 현재지수 TR(FHPUP02100000) 응답 바디
def indexbody(pct: float) -> dict:
    return {"output": {"bstp_nmix_prpr": "2500.00", "bstp_nmix_prdy_vrss": "0", "bstp_nmix_prdy_ctrt": str(pct)}}


# KIS 장애를 켜고 끌 수 있는 HTTP 클라이언트 스텁
class ClientStub:
    def __init__(self, price: int) -> None:
        self.price = price
        self.down = False

    async def get(self, url: str, **_kw) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("kis down")
        body = indexbody(0.1) if "inquire-index-price" in url else quotebody(self.price)
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


# 세션/헤더만 흉내내는 인증 스텁
class AuthStub:
    def __init__(self, client: ClientStub) -> None:
        self.client = client

    async def ready(self) -> ClientStub:
        return self.client

    def header(self, tr_id: str) -> dict:
        return {"tr_id": tr_id}


# 인메모리 캐시 스텁 — TTL 만료는 expire()로 흉내
class CacheStub:
    def __init__(self) -> None:
        self.data: dict = {}

    def lookup(self, key: str):
        item = self.data.get(key)
        return item[0] if item else None

    def memo(self, key: str, value, ttl: float) -> None:
        self.data[key] = (value, ttl)

    # 단기 TTL(시세·지수) 만료 — 장기 TTL(idx_last 3600초)은 유지
    def expire(self, under: float = 60) -> None:
        self.data = {k: v for k, v in self.data.items() if v[1] >= under}


# 알림 캡처 스텁
class NotifierStub:
    def __init__(self) -> None:
        self.msgs: list[str] = []

    async def msg(self, text: str) -> None:
        self.msgs.append(text)


# 보유 포지션 스텁
class PositionStub:
    def __init__(self, bought: dict) -> None:
        self.bought = bought


# 손절 시세 경로 — 실제 Market/Policy로 KIS 장애 중 stale 가격 은폐 여부 검증
class StaleQuoteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # 장애 시뮬레이션의 재시도·폴백 경고 로그는 기대된 출력 — 억제
        logging.disable(logging.ERROR)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.client = ClientStub(70000)
        self.cache = CacheStub()
        self.policy = Policy()
        self.policy._wait = (0, 0)
        self.market = Market(AuthStub(self.client), self.cache, self.policy)

    # 정상 조회 1회로 마지막 성공값(현재가·지수)을 남긴 뒤 단기 캐시 만료 + 장애로 전환
    async def outage(self) -> None:
        await self.market.price("005930")
        await self.market.indices()
        self.cache.expire()
        self.client.down = True

    # 손절용 raw()는 장애 시 예외 — stale 폴백을 허용하면 마지막 성공가 70000이 반환돼 실패
    async def testrawoutage(self):
        await self.outage()
        with self.assertRaises(httpx.ConnectError):
            await self.market.raw("005930")

    # 화면용 price()는 장애 중에도 마지막 성공가 유지 — stale을 전면 제거하면 실패
    async def teststaleprice(self):
        await self.outage()
        self.assertEqual((await self.market.price("005930"))["price"], 70000)

    # stale 폴백 값은 캐시에 재저장 안 됨 — 재저장되면 다음 raw()가 캐시 히트로 70000 반환
    async def testnorecache(self):
        await self.outage()
        await self.market.price("005930")
        with self.assertRaises(httpx.ConnectError):
            await self.market.raw("005930")

    # 장애 중 risk() 3회 → 손절 모니터링 장애 경보 정확히 1회 — stale 은폐면 경보 0회
    async def testriskalert(self):
        await self.outage()
        notifier = NotifierStub()
        positions = PositionStub({"005930": {"name": "삼성전자", "qty": 1, "avg_price": 70000}})
        monitor = RiskMonitor(self.market, positions, None, notifier, {})
        for _ in range(3):
            await monitor.risk()
        alerts = [m for m in notifier.msgs if "[손절 모니터링 장애]" in m]
        self.assertEqual(len(alerts), 1)
        self.assertIn("005930", alerts[0])

    # 국면 게이트용 strict indices()는 장애 시 예외 — idx_last·stale 폴백을 쓰면 실패
    async def teststrictidx(self):
        await self.outage()
        with self.assertRaises(httpx.ConnectError):
            await self.market.indices(stale=False)

    # 화면용 indices()는 장애 중에도 마지막 지수 유지 — 폴백을 전면 제거하면 실패
    async def teststaleidx(self):
        await self.outage()
        rows = await self.market.indices()
        self.assertEqual(sorted(r["code"] for r in rows), ["KOSDAQ", "KOSPI", "KPI100", "KPI200"])

    # 지수 stale 폴백 값은 캐시에 재저장 안 됨 — 재저장되면 직후 strict 조회가 캐시 히트로 통과
    async def testidxcache(self):
        await self.outage()
        await self.market.index("0001")
        with self.assertRaises(httpx.ConnectError):
            await self.market.index("0001", stale=False)

    # 장애 중 국면 게이트는 신규매수 차단 + unknown 경보 — stale 지수로 판정하면 neutral로 허용
    async def testgateoutage(self):
        await self.outage()
        notifier = NotifierStub()
        engine = EntryEngine(self.market, None, None, notifier, {})
        self.assertFalse(await engine.gate())
        self.assertTrue(any("[시장 국면] unknown" in m for m in notifier.msgs))


if __name__ == "__main__":
    unittest.main()
