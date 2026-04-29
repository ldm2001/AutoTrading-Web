# 섹터 매핑 — stock_universe 에서 KRX listing 시 동시 적재된 데이터 재사용
import logging
from service.market.stock_universe import SECTOR_MAP, listing

logger = logging.getLogger(__name__)

# 섹터 매핑 확보 (listing 미실행 시 트리거, 동시성 가드는 listing 내부 락으로 처리)
def sectors() -> None:
    if not SECTOR_MAP:
        listing()
    count = len(SECTOR_MAP)
    if count == 0:
        logger.warning("섹터 매핑 비어있음 — KRX/네이버 모두 실패")
    else:
        logger.info("섹터 매핑 사용 가능: %s개 종목", count)

# 종목코드로 섹터 조회, 미매핑 시 "기타"
def label(code: str) -> str:
    return SECTOR_MAP.get(code.zfill(6), "기타")
