# Pydantic 요청/응답 스키마 정의
import re
from pydantic import BaseModel, Field, field_validator

class StockPrice(BaseModel):
    code: str
    name: str
    price: int
    change: int
    change_percent: float
    volume: int
    market_cap: str
    market: str

class DailyCandle(BaseModel):
    date: str
    open: int
    high: int
    low: int
    close: int
    volume: int

class MarketIndex(BaseModel):
    code: str
    name: str
    value: float
    change: float
    change_percent: float

_CODE_RE = re.compile(r"^\d{6}$")

class OrderRequest(BaseModel):
    code: str
    qty: int = Field(gt=0, le=99999)

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        v = v.strip().zfill(6)
        if not _CODE_RE.match(v):
            raise ValueError("종목코드는 6자리 숫자여야 합니다")
        return v
