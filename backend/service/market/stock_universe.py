import logging

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

# 전체 상장 종목 메타 적재
def listing() -> None:
    try:
        import FinanceDataReader as fdr

        kospi = fdr.StockListing("KOSPI")[["Code", "Name"]]
        kosdaq = fdr.StockListing("KOSDAQ")[["Code", "Name"]]
        for _, row in kospi.iterrows():
            ALL_STOCKS[row["Code"]] = {"name": row["Name"], "market": "KOSPI"}
        for _, row in kosdaq.iterrows():
            ALL_STOCKS[row["Code"]] = {"name": row["Name"], "market": "KOSDAQ"}
        logger.info("Loaded %s stocks (KOSPI+KOSDAQ)", len(ALL_STOCKS))
    except Exception as e:
        logger.error("Failed to load stock listing: %s", e)
        for code, name in NAMES.items():
            ALL_STOCKS[code] = {"name": name, "market": "KOSPI"}

# 코드와 이름으로 종목 검색
def search(query: str) -> list[dict[str, str]]:
    q = query.strip().lower()
    result: list[dict[str, str]] = []
    source = ALL_STOCKS if ALL_STOCKS else {code: {"name": name} for code, name in NAMES.items()}
    for code, info in source.items():
        name = info["name"]
        aliases = ALIASES.get(code, [])
        if q in name.lower() or q in code or any(q in alias for alias in aliases):
            result.append({"code": code, "name": name})
        if len(result) >= 20:
            break
    return result
