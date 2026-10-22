import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.kis.market import Market
from service.kis.policy import Policy


# 기간별시세 TR(FHKST03010100) 응답 바디
_DAILY = {"output2": [{
    "stck_bsop_date": "20261021", "stck_oprc": "100", "stck_hgpr": "110",
    "stck_lwpr": "90", "stck_clpr": "105", "acml_vol": "1",
}]}

# 일자별 TR(FHKST01010400) 응답 바디 — 당일·전일 2행
_DAYS = {"output": [
    {"stck_oprc": "100", "stck_hgpr": "110", "stck_lwpr": "90"},
    {"stck_oprc": "100", "stck_hgpr": "110", "stck_lwpr": "90"},
]}


# TR별 요청 파라미터를 기록하는 HTTP 클라이언트 스텁
class _Client:
    def __init__(self) -> None:
        self.params: dict[str, dict] = {}

    async def get(self, url: str, *, headers: dict, params: dict) -> httpx.Response:
        self.params[headers["tr_id"]] = params
        body = _DAILY if headers["tr_id"] == "FHKST03010100" else _DAYS
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


# 세션/헤더만 흉내내는 인증 스텁
class _Auth:
    def __init__(self, client: _Client) -> None:
        self.client = client

    async def ready(self) -> _Client:
        return self.client

    def header(self, tr_id: str) -> dict:
        return {"tr_id": tr_id}


# 항상 미스인 캐시 스텁 — 매 호출이 실제 요청을 거치게
class _Cache:
    def get(self, key: str):
        return None

    def set(self, key: str, value, ttl: float) -> None:
        pass


# KIS TR 요청 파라미터 — 같은 이름의 파라미터가 TR마다 의미가 달라 값 복사로 깨지기 쉬움
class KisParamsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = _Client()
        self.market = Market(_Auth(self.client), _Cache(), Policy())

    # FHKST03010100은 0=수정주가·1=원주가 — "1"이면 액면분할·증자 이전 구간이 원주가로 남아 지표가 왜곡
    async def test_daily_requests_adjusted_price(self):
        await self.market.daily("005930")
        self.assertEqual(self.client.params["FHKST03010100"]["FID_ORG_ADJ_PRC"], "0")

    # FHKST01010400은 반대로 1=수정주가반영 — daily와 값을 맞춘다고 "0"으로 바꾸면 실패
    async def test_target_requests_adjusted_price(self):
        await self.market.target("005930")
        self.assertEqual(self.client.params["FHKST01010400"]["fid_org_adj_prc"], "1")


if __name__ == "__main__":
    unittest.main()
