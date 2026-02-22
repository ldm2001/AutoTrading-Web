# Pydantic 요청/응답 스키마 정의
from pydantic import BaseModel

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

class OrderRequest(BaseModel):
    code: str
    qty: int
