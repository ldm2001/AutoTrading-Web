import logging
from pathlib import Path
import requests
import json

logger = logging.getLogger(__name__)

# 기본 종목명 매핑
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

CODES: dict[str, str] = {name: code for code, name in NAMES.items()}
ALL_STOCKS: dict[str, dict] = {}

# 검색용 별칭 목록
ALIASES: dict[str, list[str]] = {
    "035420": ["naver"],
    "035900": ["jyp"],
    "033780": ["kt&g", "ktng"],
    "030200": ["kt"],
    "024110": ["hmm"],
    "069500": ["kodex200"],
    "102110": ["tiger200"],
}

# 주요 지수 코드 매핑
INDICES: dict[str, tuple[str, str]] = {
    "KOSPI": ("0001", "코스피"),
    "KOSDAQ": ("1001", "코스닥"),
    "KPI200": ("2001", "코스피200"),
    "KPI100": ("2007", "코스피100"),
}

# KRX API로 전체 종목 가져오기 (KOSPI + KOSDAQ)
def _krx_listing() -> int:
    count = 0
    for mkt_id, label in [("STK", "KOSPI"), ("KSQ", "KOSDAQ")]:
        resp = requests.post(
            "http://data.krx.co.kr/comm/bldAttend/getJsonData.cmd",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "http://data.krx.co.kr"},
            data={"bld": "dbms/MDC/STAT/standard/MDCSTAT01901", "mktId": mkt_id, "share": "1", "csvxls_isNo": "false"},
            timeout=15,
        )
        if not resp.ok:
            continue
        for row in resp.json().get("OutBlock_1", []):
            code = row.get("ISU_SRT_CD", "")
            name = row.get("ISU_ABBRV", "")
            if not code or not name or len(code) != 6 or not code.isdigit():
                continue
            ALL_STOCKS[code] = {"name": name, "market": label}
            if code not in NAMES:
                NAMES[code] = name
            count += 1
    return count


# 네이버 금융 API로 전체 종목 가져오기
def _naver_listing() -> int:
    count = 0
    seen: set[str] = set()
    page = 1
    while page <= 30:
        url = f"https://m.stock.naver.com/api/stocks/marketValue?market=KOSPI&page={page}&pageSize=100"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if not resp.ok:
            break
        stocks = resp.json().get("stocks", [])
        if not stocks:
            break
        for s in stocks:
            code = s.get("itemCode", "")
            name = s.get("stockName", "")
            if not code or not name or code in seen:
                continue
            seen.add(code)
            ALL_STOCKS[code] = {"name": name, "market": ""}
            count += 1
        page += 1

    # 시장 구분: 네이버 개별 종목 API로 보정 (상위 종목만)
    # 나머지는 빈 문자열 → 프론트에서 표시 안 함
    _resolve_markets(list(ALL_STOCKS.keys()))
    return count

# 시장 구분 보정 — KRX로 시도 후 캐시 파일에 저장
_MARKET_CACHE = Path(__file__).parent.parent / "trades" / "market_cache.json"

def _resolve_markets(codes: list[str]) -> None:
    # 1차: 캐시 파일에서 복원
    if _MARKET_CACHE.exists():
        try:
            cached = json.loads(_MARKET_CACHE.read_text())
            applied = 0
            for code, label in cached.items():
                if code in ALL_STOCKS:
                    ALL_STOCKS[code]["market"] = label
                    applied += 1
            if applied > 100:
                logger.info("Market labels restored from cache (%s)", applied)
                return
        except Exception:
            pass

    # 2차: KRX API 시도
    try:
        import requests
        market_map: dict[str, str] = {}
        for mkt_id, label in [("STK", "KOSPI"), ("KSQ", "KOSDAQ")]:
            resp = requests.post(
                "http://data.krx.co.kr/comm/bldAttend/getJsonData.cmd",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "http://data.krx.co.kr"},
                data={"bld": "dbms/MDC/STAT/standard/MDCSTAT01901", "mktId": mkt_id, "share": "1", "csvxls_isNo": "false"},
                timeout=10,
            )
            if not resp.ok:
                continue
            for row in resp.json().get("OutBlock_1", []):
                code = row.get("ISU_SRT_CD", "")
                if code:
                    market_map[code] = label
                    if code in ALL_STOCKS:
                        ALL_STOCKS[code]["market"] = label
        if market_map:
            _MARKET_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _MARKET_CACHE.write_text(json.dumps(market_map, ensure_ascii=False))
            logger.info("Market labels resolved via KRX (%s), cached", len(market_map))
    except Exception:
        logger.info("KRX market resolve skipped (off-hours)")

# 전체 상장 종목 메타 적재
def listing() -> None:
    # 1차: FDR
    try:
        import FinanceDataReader as fdr
        kospi = fdr.StockListing("KOSPI")[["Code", "Name"]]
        kosdaq = fdr.StockListing("KOSDAQ")[["Code", "Name"]]
        for _, row in kospi.iterrows():
            ALL_STOCKS[row["Code"]] = {"name": row["Name"], "market": "KOSPI"}
        for _, row in kosdaq.iterrows():
            ALL_STOCKS[row["Code"]] = {"name": row["Name"], "market": "KOSDAQ"}
        logger.info("Loaded %s stocks via FDR", len(ALL_STOCKS))
        return
    except Exception as e:
        logger.warning("FDR listing failed: %s — trying Naver fallback", e)

    # 2차: KRX
    try:
        count = _krx_listing()
        if count > 100:
            logger.info("Loaded %s stocks via KRX API", count)
            return
    except Exception as e:
        logger.warning("KRX listing failed: %s — trying Naver fallback", e)

    # 3차: 네이버 금융
    try:
        count = _naver_listing()
        if count > 0:
            logger.info("Loaded %s stocks via Naver API", count)
            return
    except Exception as e:
        logger.warning("Naver listing failed: %s", e)

    # 3차: 하드코딩 폴백
    for code, name in NAMES.items():
        ALL_STOCKS[code] = {"name": name, "market": "KOSPI"}
    logger.warning("Using hardcoded %s stocks as fallback", len(NAMES))

# 코드와 이름으로 종목 검색
def search(query: str) -> list[dict[str, str]]:
    q = query.strip().lower()
    result: list[dict[str, str]] = []
    source = ALL_STOCKS if ALL_STOCKS else {code: {"name": name} for code, name in NAMES.items()}
    for code, info in source.items():
        name = info["name"]
        aliases = ALIASES.get(code, [])
        if q in name.lower() or q in code or any(q in alias for alias in aliases):
            result.append({"code": code, "name": name, "market": info.get("market", "")})
        if len(result) >= 20:
            break

    # 로컬 결과 없으면 네이버 자동완성으로 폴백
    if not result:
        try:
            import requests
            url = f"https://ac.stock.naver.com/ac?q={query.strip()}&target=stock"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if resp.ok:
                for item in resp.json().get("items", []):
                    if item.get("nationCode") == "KOR":
                        result.append({"code": item["code"], "name": item["name"], "market": item.get("typeName", "")})
        except Exception:
            pass

    return result
