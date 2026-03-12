import json
import logging
import time
import httpx
from config import settings

logger = logging.getLogger(__name__)

# 토큰과 공통 헤더를 관리
class Auth:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._token = ""
        self._exp = 0.0

    # HTTP 세션을 열고 토큰을 받음
    async def open(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.url_base,
            timeout=httpx.Timeout(10.0),
        )
        await self.refresh()

    # HTTP 세션 종료
    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # 접근 토큰을 새로 받음
    async def refresh(self) -> None:
        client = self._client
        if client is None:
            raise RuntimeError("KIS client is not started")
        resp = await client.post(
            "/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": settings.app_key,
                "appsecret": settings.app_secret,
            },
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        self._exp = time.time() + 20 * 3600
        logger.info("Access token refreshed")

    # 만료를 확인한 뒤 세션을 반환
    async def ready(self) -> httpx.AsyncClient:
        client = self._client
        if client is None:
            raise RuntimeError("KIS client is not started")
        if time.time() >= self._exp:
            await self.refresh()
        return client

    # TR ID 기준 공통 헤더 생성
    def header(self, tr_id: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token}",
            "appKey": settings.app_key,
            "appSecret": settings.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    # 주문 바디용 hashkey를 생성
    async def hash(self, data: dict) -> str:
        client = await self.ready()
        resp = await client.post(
            "/uapi/hashkey",
            headers={
                "Content-Type": "application/json",
                "appKey": settings.app_key,
                "appSecret": settings.app_secret,
            },
            content=json.dumps(data),
        )
        resp.raise_for_status()
        return resp.json()["HASH"]
