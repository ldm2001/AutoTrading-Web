# 분봉 캔들 빌더 + 파일 적재 — 틱 -> 15분/60분봉 조립 및 parquet 저장
import asyncio
import datetime
import logging
from pathlib import Path
from collections import defaultdict

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)

class Candle:
    __slots__ = ("o", "h", "l", "c", "v", "ts")

    def __init__(self, price: int, volume: int, ts: datetime.datetime) -> None:
        self.o = price
        self.h = price
        self.l = price
        self.c = price
        self.v = volume
        self.ts = ts

    def update(self, price: int, volume: int) -> None:
        self.h = max(self.h, price)
        self.l = min(self.l, price)
        self.c = price
        self.v += volume

    def snapshot(self) -> dict:
        return {
            "time": self.ts,
            "open": self.o,
            "high": self.h,
            "low": self.l,
            "close": self.c,
            "volume": self.v,
        }

class CandleStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self._dir = base_dir or _DATA_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        # code - interval - bucket_key - Candle
        self._buf: dict[str, dict[int, dict[str, Candle]]] = defaultdict(
            lambda: {15: {}, 60: {}}
        )
        self._lock = asyncio.Lock()

    # 틱 데이터를 15분/60분봉 버킷에 반영
    async def ingest(self, code: str, price: int, volume: int,
                     ts: datetime.datetime | None = None) -> None:
        ts = ts or datetime.datetime.now()
        async with self._lock:
            for interval in (15, 60):
                bk = self._bucket_key(ts, interval)
                bucket = self._buf[code][interval]
                if bk in bucket:
                    bucket[bk].update(price, volume)
                else:
                    bucket[bk] = Candle(price, volume, self._bucket_ts(ts, interval))

    # 특정 종목의 N분봉 캔들 리스트 반환
    def candles(self, code: str, interval: int = 15) -> list[dict]:
        bucket = self._buf.get(code, {}).get(interval, {})
        return [c.snapshot() for c in sorted(bucket.values(), key=lambda c: c.ts)]

    # 장 마감 후 모든 종목의 캔들을 parquet/csv로 저장
    async def flush(self, date_str: str | None = None) -> int:
        date_str = date_str or datetime.date.today().isoformat()
        saved = 0
        async with self._lock:
            for code, intervals in self._buf.items():
                for interval, bucket in intervals.items():
                    if not bucket:
                        continue
                    rows = [c.snapshot() for c in sorted(bucket.values(), key=lambda c: c.ts)]
                    path = self._path(code, interval, date_str)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self._write_csv(path, rows)
                    saved += 1
                    logger.info(f"Saved {len(rows)} candles → {path}")
            self._buf.clear()
        return saved

    # 저장된 과거 분봉 로드 (csv)
    def load(self, code: str, interval: int = 15,
             date_str: str | None = None) -> list[dict]:
        date_str = date_str or datetime.date.today().isoformat()
        path = self._path(code, interval, date_str)
        if not path.exists():
            return []
        return self._read_csv(path)

    # 최근 N일 분봉 병합 로드
    def load_days(self, code: str, interval: int = 15, days: int = 5) -> list[dict]:
        result: list[dict] = []
        today = datetime.date.today()
        for d in range(days - 1, -1, -1):
            dt = today - datetime.timedelta(days=d)
            result.extend(self.load(code, interval, dt.isoformat()))
        return result

    # private
    def _bucket_key(self, ts: datetime.datetime, interval: int) -> str:
        m = (ts.minute // interval) * interval
        return ts.replace(minute=m, second=0, microsecond=0).strftime("%H%M")

    def _bucket_ts(self, ts: datetime.datetime, interval: int) -> datetime.datetime:
        m = (ts.minute // interval) * interval
        return ts.replace(minute=m, second=0, microsecond=0)

    def _path(self, code: str, interval: int, date_str: str) -> Path:
        return self._dir / code / f"{date_str}_{interval}m.csv"

    def _write_csv(self, path: Path, rows: list[dict]) -> None:
        header = "time,open,high,low,close,volume\n"
        lines = [header]
        for r in rows:
            ts = r["time"]
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime.datetime) else str(ts)
            lines.append(f"{ts_str},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']}\n")
        path.write_text("".join(lines))

    def _read_csv(self, path: Path) -> list[dict]:
        rows: list[dict] = []
        for line in path.read_text().strip().split("\n")[1:]:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            rows.append({
                "time": datetime.datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S"),
                "open": int(parts[1]),
                "high": int(parts[2]),
                "low": int(parts[3]),
                "close": int(parts[4]),
                "volume": int(parts[5]),
            })
        return rows


store = CandleStore()
