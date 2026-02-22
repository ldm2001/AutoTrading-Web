import asyncio
import json
import logging
import time
from typing import Any
import httpx
from config import settings

logger = logging.getLogger(__name__)

# TTL 캐시
class TTLCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        exp, value = entry
        if time.time() < exp:
            return value
        del self._data[key]
        return None

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._data[key] = (time.time() + ttl, value)

    # 주어진 prefix로 시작하는 캐시 항목 삭제
    def invalidate(self, *prefixes: str) -> None:
        keys = [k for k in self._data if any(k.startswith(p) for p in prefixes)]
        for k in keys:
            del self._data[k]

    def clear(self) -> None:
        self._data.clear()

# 종목 데이터
NAMES: dict[str, str] = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "373220": "LG에너지솔루션",
    "005380": "현대차",
    "005490": "POSCO홀딩스",
    "035420": "네이버",
    "035720": "카카오",
    "006400": "삼성SDI",
    "051910": "LG화학",
    "028260": "삼성물산",
    "003670": "포스코퓨처엠",
    "066570": "LG전자",
    "105560": "KB금융",
    "055550": "신한지주",
    "096770": "SK이노베이션",
    "012330": "현대모비스",
    "003550": "LG",
    "032830": "삼성생명",
    "034730": "SK",
    "015760": "한국전력",
    "033780": "KT&G",
    "009150": "삼성전기",
    "086790": "하나금융지주",
    "000270": "기아",
    "017670": "SK텔레콤",
    "018260": "삼성에스디에스",
    "316140": "우리금융지주",
    "034020": "두산에너빌리티",
    "003490": "대한항공",
    "010130": "고려아연",
    "024110": "HMM",
    "000810": "삼성화재",
    "030200": "KT",
    "011200": "HJ중공업",
    "009540": "HD한국조선해양",
    "036570": "엔씨소프트",
    "010950": "S-Oil",
    "004020": "현대제철",
    "011170": "롯데케미칼",
    "352820": "하이브",
    "259960": "크래프톤",
    "263750": "펄어비스",
    "251270": "넷마블",
    "293490": "카카오게임즈",
    "041510": "에스엠",
    "035900": "JYP Ent.",
    "122870": "와이지엔터테인먼트",
    "005940": "NH투자증권",
    "068270": "셀트리온",
    "207940": "삼성바이오로직스",
    "326030": "SK바이오팜",
    "091990": "셀트리온헬스케어",
    "128940": "한미약품",
    "009830": "한화솔루션",
    "267260": "HD현대일렉트릭",
    "329180": "HD현대중공업",
    "042700": "한미반도체",
    "247540": "에코프로비엠",
    "086520": "에코프로",
    "006800": "미래에셋증권",
    "004170": "신세계",
    "023530": "롯데쇼핑",
    "069500": "KODEX 200",
    "229200": "KODEX 코스닥150",
    "114800": "KODEX 인버스",
    "102110": "TIGER 200",
    "252670": "KODEX 200선물인버스2X",
    "005830": "DB손해보험",
    "180640": "한진칼",
    "047050": "포스코인터내셔널",
    "010140": "삼성중공업",
    "011790": "SKC",
    "402340": "SK스퀘어",
    "361610": "SK아이이테크놀로지",
    "377300": "카카오페이",
    "323410": "카카오뱅크",
    "090430": "아모레퍼시픽",
    "051900": "LG생활건강",
}

CODES: dict[str, str] = {v: k for k, v in NAMES.items()}

# 전체 KOSPI + KOSDAQ 상장사 (서버 시작 시 FDR로 로드)
ALL_STOCKS: dict[str, dict] = {}  # code -> {name, market}

# 전체 상장사 목록 로드 (KOSPI + KOSDAQ, FDR 사용)
def load_all_stocks():
    global ALL_STOCKS
    try:
        import FinanceDataReader as fdr
        kospi = fdr.StockListing('KOSPI')[['Code', 'Name']]
        kosdaq = fdr.StockListing('KOSDAQ')[['Code', 'Name']]
        for _, row in kospi.iterrows():
            ALL_STOCKS[row['Code']] = {"name": row['Name'], "market": "KOSPI"}
        for _, row in kosdaq.iterrows():
            ALL_STOCKS[row['Code']] = {"name": row['Name'], "market": "KOSDAQ"}
        logger.info(f"Loaded {len(ALL_STOCKS)} stocks (KOSPI+KOSDAQ)")
    except Exception as e:
        logger.error(f"Failed to load stock listing: {e}")
        # 폴백: NAMES 사용
        for code, name in NAMES.items():
            ALL_STOCKS[code] = {"name": name, "market": "KOSPI"}

# 영문 별칭 (한글 이름 외 영문으로도 검색 가능)
ALIASES: dict[str, list[str]] = {
    "035420": ["naver"],
    "035900": ["jyp"],
    "033780": ["kt&g", "ktng"],
    "030200": ["kt"],
    "024110": ["hmm"],
    "069500": ["kodex200"],
    "102110": ["tiger200"],
}

INDICES: dict[str, tuple[str, str]] = {
    "KOSPI": ("0001", "코스피"),
    "KOSDAQ": ("1001", "코스닥"),
    "KPI200": ("2001", "코스피200"),
    "KPI100": ("2007", "코스피100"),
}

def search(query: str) -> list[dict[str, str]]:
    q = query.strip().lower()
    results = []
    # 전체 상장사에서 검색
    source = ALL_STOCKS if ALL_STOCKS else {c: {"name": n} for c, n in NAMES.items()}
    for code, info in source.items():
        name = info["name"]
        aliases = ALIASES.get(code, [])
        if q in name.lower() or q in code or any(q in a for a in aliases):
            results.append({"code": code, "name": name})
        if len(results) >= 20:
            break
    return results

# 한국투자증권 OpenAPI 클라이언트
class KIS:

    # 캐시 TTL
    TTL_PRICE = 3 # 주가: 3초 (장 시간 3초 루프와 동기화)
    TTL_DAILY = 300 # 일봉: 5분
    TTL_INDEX = 5 # 지수: 5초
    TTL_HOLDINGS = 10 # 잔고: 10초
    TTL_CASH = 10 # 예수금: 10초
    TTL_TARGET = 30 # 목표가: 30초

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._token: str = ""
        self._token_exp: float = 0
        self.cache = TTLCache()

    # HTTP 클라이언트 초기화 및 토큰 발급
    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.url_base,
            timeout=httpx.Timeout(10.0),
        )
        await self._refresh()

    # HTTP 클라이언트 종료
    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    # 인증
    async def _refresh(self) -> None:
        resp = await self._client.post(
            "/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": settings.app_key,
                "appsecret": settings.app_secret,
            },
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        self._token_exp = time.time() + 20 * 3600
        logger.info("Access token refreshed")

    # 토큰 만료 시 재발급
    async def _ensure(self) -> None:
        if time.time() >= self._token_exp:
            await self._refresh()

    # 요청 헤더 구성 (인증 + TR ID)
    def _headers(self, tr_id: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token}",
            "appKey": settings.app_key,
            "appSecret": settings.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    # 해시키 발급 (주문 위변조 방지용)
    async def _hash(self, data: dict) -> str:
        resp = await self._client.post(
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

    # 시세
    # 현재가 조회 — 현재가/등락/등락률/거래량/시총 반환 (5초 캐시)
    async def price(self, code: str) -> dict:
        key = f"price:{code}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        await self._ensure()
        resp = await self._client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=self._headers("FHKST01010100"),
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code},
        )
        resp.raise_for_status()
        o = resp.json()["output"]
        result = {
            "code": code,
            "name": NAMES.get(code, code),
            "price": int(o["stck_prpr"]),
            "change": int(o["prdy_vrss"]),
            "change_percent": float(o["prdy_ctrt"]),
            "volume": int(o["acml_vol"]),
            "market_cap": o.get("hts_avls", "0"),
            "market": o.get("rprs_mrkt_kor_name", ""),
        }
        self.cache.set(key, result, self.TTL_PRICE)
        return result

    # 현재가 숫자만 반환 (전략 계산용 축약 래퍼)
    async def price_raw(self, code: str) -> int:
        return (await self.price(code))["price"]

    # 일봉 캔들 조회 — 최대 count개 (5분 캐시)
    async def daily(self, code: str, count: int = 60) -> list[dict]:
        key = f"daily:{code}:{count}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        await self._ensure()
        resp = await self._client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-price",
            headers=self._headers("FHKST01010400"),
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": code,
                "fid_org_adj_prc": "1",
                "fid_period_div_code": "D",
            },
        )
        resp.raise_for_status()
        candles = []
        for item in resp.json()["output"][:count]:
            dt = item["stck_bsop_date"]
            candles.append({
                "date": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}",
                "open": int(item["stck_oprc"]),
                "high": int(item["stck_hgpr"]),
                "low": int(item["stck_lwpr"]),
                "close": int(item["stck_clpr"]),
                "volume": int(item["acml_vol"]),
            })
        candles.reverse()
        self.cache.set(key, candles, self.TTL_DAILY)
        return candles

    # 관심 종목 전체 현재가 병렬 조회
    async def prices(self) -> list[dict]:
        tasks = [self.price(code) for code in settings.symbol_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if not isinstance(r, Exception)]

    # 변동성 돌파 목표가 = 당일 시가 + 전일 고저 범위 × 0.5 (30초 캐시)
    async def target(self, code: str) -> float:
        key = f"target:{code}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        await self._ensure()
        resp = await self._client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-price",
            headers=self._headers("FHKST01010400"),
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": code,
                "fid_org_adj_prc": "1",
                "fid_period_div_code": "D",
            },
        )
        resp.raise_for_status()
        o = resp.json()["output"]
        result = int(o[0]["stck_oprc"]) + (int(o[1]["stck_hgpr"]) - int(o[1]["stck_lwpr"])) * 0.5
        self.cache.set(key, result, self.TTL_TARGET)
        return result

    # 지수
    # 단일 지수 현재가 조회 — 지수값/등락/등락률 반환 (5초 캐시)
    async def index(self, code: str) -> dict:
        key = f"index:{code}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        await self._ensure()
        resp = await self._client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            headers=self._headers("FHPUP02100000"),
            params={"fid_cond_mrkt_div_code": "U", "fid_input_iscd": code},
        )
        resp.raise_for_status()
        o = resp.json()["output"]
        result = {
            "value": float(o["bstp_nmix_prpr"]),
            "change": float(o.get("bstp_nmix_prdy_vrss", "0")),
            "change_percent": float(o.get("bstp_nmix_prdy_ctrt", "0")),
        }
        self.cache.set(key, result, self.TTL_INDEX)
        return result

    # KOSPI/KOSDAQ/코스피200 전체 지수 조회
    async def indices(self) -> list[dict]:
        result = []
        for key, (api_code, name) in INDICES.items():
            try:
                data = await self.index(api_code)
                result.append({"code": key, "name": name, **data})
            except Exception as e:
                logger.error(f"Index error ({key}): {e}")
        return result

    # 잔고
    # 보유 종목 및 평가 정보 조회 — (종목맵, 계좌요약) 반환 (10초 캐시)
    async def holdings(self) -> tuple[dict[str, dict], dict]:
        key = "holdings"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        await self._ensure()
        resp = await self._client.get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=self._headers("TTTC8434R"),
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
        for s in data["output1"]:
            if int(s["hldg_qty"]) > 0:
                items[s["pdno"]] = {
                    "code": s["pdno"],
                    "name": s["prdt_name"],
                    "qty": int(s["hldg_qty"]),
                    "avg_price": int(float(s.get("pchs_avg_pric", "0"))),
                    "current_price": int(s.get("prpr", "0")),
                    "eval_amount": int(s.get("evlu_amt", "0")),
                    "profit_loss": int(s.get("evlu_pfls_amt", "0")),
                    "profit_loss_percent": float(s.get("evlu_pfls_rt", "0")),
                }
        evaluation = data["output2"][0] if data["output2"] else {}
        result = (items, evaluation)
        self.cache.set(key, result, self.TTL_HOLDINGS)
        return result

    # 주문 가능 예수금 조회 (10초 캐시)
    async def cash(self) -> int:
        key = "cash"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        await self._ensure()
        resp = await self._client.get(
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            headers=self._headers("TTTC8908R"),
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

    # 주문
    # 시장가 매수/매도 공통 처리 — 주문 후 잔고/예수금 캐시 무효화
    async def _order(self, code: str, qty: int, tr_id: str) -> dict:
        await self._ensure()
        body = {
            "CANO": settings.cano,
            "ACNT_PRDT_CD": settings.acnt_prdt_cd,
            "PDNO": code,
            "ORD_DVSN": "01",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        }
        headers = self._headers(tr_id)
        headers["hashkey"] = await self._hash(body)
        resp = await self._client.post(
            "/uapi/domestic-stock/v1/trading/order-cash",
            headers=headers,
            content=json.dumps(body),
        )
        resp.raise_for_status()
        result = resp.json()
        # 주문 후 잔고/예수금 캐시 무효화
        self.cache.invalidate("holdings", "cash")
        return {"success": result.get("rt_cd") == "0", "data": result}

    # 시장가 매수 주문
    async def buy(self, code: str, qty: int) -> dict:
        return await self._order(code, qty, "TTTC0802U")

    # 시장가 매도 주문
    async def sell(self, code: str, qty: int) -> dict:
        return await self._order(code, qty, "TTTC0801U")

kis = KIS()
