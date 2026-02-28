# KRX 섹터 매핑 
import logging

logger = logging.getLogger(__name__)

# 종목코드 → 섹터명 매핑 캐시
SECTOR_MAP: dict[str, str] = {}

# FDR로 KOSPI/KOSDAQ 종목 섹터 로드
def load_sectors():
    global SECTOR_MAP
    try:
        import FinanceDataReader as fdr
        count = 0
        for market in ("KOSPI", "KOSDAQ"):
            df = fdr.StockListing(market)
            # Sector/Industry/업종 중 존재하는 컬럼 탐색
            sec_col = next(
                (c for c in df.columns if c in ("Sector", "Industry", "업종")),
                None,
            )
            if sec_col is None:
                logger.warning(f"{market}: 섹터 컬럼 없음 (columns={list(df.columns[:10])})")
                continue
            for _, row in df.iterrows():
                code = str(row.get("Code", "")).zfill(6)
                sector = str(row.get(sec_col, "")).strip() or "기타"
                SECTOR_MAP[code] = sector
                count += 1
        logger.info(f"섹터 매핑 로드 완료: {count}개 종목")
    except Exception as e:
        logger.error(f"섹터 매핑 실패: {e}")

# 종목코드로 섹터 조회, 미매핑 시 "기타"
def sector_of(code: str) -> str:
    return SECTOR_MAP.get(code.zfill(6), "기타")
