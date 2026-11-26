# 백테스트 예측 주입 비교 — 결과 표(rows.csv) → 기준일별 예측 표, 같은 입력으로 주입 없음/있음 bt 두 번
# cd backend && venv/bin/python -m offline.injection --rows <rows.csv> --start 2025-05-01 --end 2025-11-21 --out <폴더>
# 개발 구간 전용 — HOLDOUT_START 이후 기준일·일봉·15분봉 파일과 봉은 읽지 않는다 (--final 없음)
# 주입용 표는 offline.run --step 1로 만든다 (5일 간격 표는 lag=1에서 5일 중 하루만 주입됨)
import argparse
import datetime
import json
import sys
from pathlib import Path

import pandas as pd

from offline.daily import CACHE, history
from offline.rules import HOLDOUT_START
from offline.walk import HORIZON
from service.trading.backtest import BacktestConfig, BacktestScorer, bt, playback, prior
from service.trading.strategy import Scorer

# CandleStore 기본 폴더 — 읽기만
BARS = Path(__file__).resolve().parents[1] / "service" / "data"
# /api/backtest와 같은 최소 15분봉 수
MIN_BARS = 50
# 요약 수치 항목
_FIGURES = ("total_trades", "cum_return_pct", "mdd_pct", "win_rate_pct", "buy_hold_return_pct", "excess_return_pct")


# 하네스 전용 Direction — 매매 점수의 문(predgate) 없이 규칙(Scorer.labscore)대로 (검증 전 모델 평가용, 서비스 코드는 쓰지 않음)
class LabRule(Scorer):
    def pred(self, prediction: dict | None, price: int) -> tuple[float, str]:
        return self.labscore(prediction, price)


# 하네스 전용 백테스트 채점기 — Direction만 LabRule, 나머지 8팩터는 BacktestScorer 그대로
class LabScorer(BacktestScorer):
    def __init__(self):
        super().__init__()
        self._s = LabRule()


# 결과 표 → {종목: {기준일: 예측 dict}} — Scorer.labscore가 읽는 형태(predictions·base_close·base_date)만, 실제값 열은 넣지 않음
def forecasts(table) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for row in table:
        day = str(row["base_date"])[:10]
        if day >= HOLDOUT_START:
            continue
        code = str(row["code"]).zfill(6)
        closes = [float(row[f"pred{k}"]) for k in range(1, HORIZON + 1)]
        out.setdefault(code, {})[day] = {
            "predictions": [{"close": c} for c in closes],
            "base_close": float(row["base_close"]),
            "base_date": day,
        }
    return out


# FDR 일봉 DataFrame → 백테스트 일봉 dict 목록 (api/backtest.py dayrows와 같은 정수 변환)
def ohlc(df: pd.DataFrame) -> list[dict]:
    return [{"date": str(dt)[:10], "open": int(r["Open"]), "high": int(r["High"]), "low": int(r["Low"]),
             "close": int(r["Close"]), "volume": int(r["Volume"])} for dt, r in df.iterrows()]


# 봉 시각 → 날짜 문자열
def datekey(t) -> str:
    return t.date().isoformat() if isinstance(t, datetime.datetime) else str(t)[:10]


# CandleStore 15분봉 — [start, end] 날짜 파일만, HOLDOUT_START 이후 이름의 파일은 열지 않고 섞인 봉도 버림
def minutes(root, code: str, start: str, end: str) -> list[dict]:
    folder = Path(root) / code
    if not folder.is_dir():
        return []
    from service.market.candle_store import CandleStore
    reader = CandleStore(Path(root))
    begin, last = str(pd.Timestamp(start).date()), str(pd.Timestamp(end).date())
    bars: list[dict] = []
    for path in sorted(folder.glob("*_15m.csv")):
        day = path.name.split("_")[0]
        if begin <= day <= last and day < HOLDOUT_START:
            bars.extend(reader.csvin(path))
    bars = [b for b in bars if begin <= datekey(b["time"]) <= last and datekey(b["time"]) < HOLDOUT_START]
    return sorted(bars, key=lambda b: b["time"])


# 주입된 날 — 15분봉 거래일 가운데 bt가 쓸 예측(prior)이 있는 날 수
def coverage(candles_15m: list[dict], daily: list[dict], table: dict, lag: int = 1) -> dict:
    keys = {str(k)[:10]: v for k, v in table.items()}
    dates = sorted(keys)
    days = sorted({datekey(b["time"]) for b in candles_15m})
    covered = sum(
        1 for day in days
        if prior(keys, dates, day, [d for d in daily if str(d.get("date", ""))[:10] < day], lag) is not None
    )
    return {"days": len(days), "covered": covered}


# 백테스트 결과 요약 수치
def figures(result) -> dict:
    return {k: getattr(result, k) for k in _FIGURES}


# 주입 유무 비교 — 주입 없음은 /api/backtest와 같은 호출, 있음은 같은 입력에 예측 표와 lag만 더함
# 주입 쪽은 하네스 전용 채점기(LabScorer) — 검증 전 모델도 매매 점수의 문과 무관하게 규칙대로 채점 (bt는 문이 닫혀 있으면 0점)
def comparison(code: str, candles_15m: list[dict], daily: list[dict], table: dict,
               cfg: BacktestConfig | None = None, *, lag: int = 1) -> dict:
    cfg = cfg or BacktestConfig()
    plain = bt(code, candles_15m, daily, cfg)
    injected = playback(LabScorer(), code, candles_15m, daily, cfg, forecasts=table, lag=lag)
    a, b = figures(plain), figures(injected)
    before, after = {t.entry_bar for t in plain.trades}, {t.entry_bar for t in injected.trades}
    return {
        "code": code,
        "lag": lag,
        "bars": plain.total_bars,
        **coverage(candles_15m, daily, table, lag),
        "plain": a,
        "injected": b,
        "delta": {k: round(b[k] - a[k], 2) for k in ("total_trades", "cum_return_pct", "mdd_pct")},
        "entries": {"added": len(after - before), "removed": len(before - after)},
    }


# 명령행 인자 — --final은 두지 않는다
def options(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="offline.injection", description="백테스트 예측 주입 비교 (개발 구간 전용)")
    ap.add_argument("--rows", required=True, help="offline.run 결과 표 rows.csv (주입용은 --step 1)")
    ap.add_argument("--start", required=True, help="비교 구간 시작 (YYYY-MM-DD)")
    ap.add_argument("--end", required=True, help=f"비교 구간 끝 (YYYY-MM-DD, {HOLDOUT_START} 이후는 읽지 않음)")
    ap.add_argument("--out", required=True, help="결과 폴더")
    ap.add_argument("--codes", default="", help="쉼표로 구분한 종목 코드 (기본: 결과 표의 종목 전부)")
    ap.add_argument("--bars", default=str(BARS), help="CandleStore 15분봉 폴더 (읽기만)")
    ap.add_argument("--cache", default=str(CACHE), help="일봉 캐시 폴더")
    ap.add_argument("--lag", type=int, default=1, help="예측 기준일 허용 경과 거래일 (기본 1 = 직전 거래일만)")
    return ap.parse_args(argv)


# 사람이 읽는 요약 (첫 줄 = 개발 구간)
def overview(conf: dict, results: list[dict], skipped: list[dict]) -> str:
    lines = [
        f"개발 구간 — {conf['start']} ~ {conf['end']} ({HOLDOUT_START} 이전 행만 로드)",
        "",
        "# 백테스트 예측 주입 비교",
        "",
        f"- 결과 표 {conf['rows']} · lag {conf['lag']} · 설정 라이브 기본값(BacktestConfig) · 건너뜀 {len(skipped)}",
        "",
        "| 종목 | 봉 | 주입된 날 | 거래 없음→있음 | 누적 % 없음→있음 | MDD % 없음→있음 | 진입 +/− |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        p, q = r["plain"], r["injected"]
        lines.append(f"| {r['code']} | {r['bars']} | {r['covered']}/{r['days']} | {p['total_trades']}→{q['total_trades']} | "
                     f"{p['cum_return_pct']:+.2f}→{q['cum_return_pct']:+.2f} | {p['mdd_pct']:.2f}→{q['mdd_pct']:.2f} | "
                     f"+{r['entries']['added']}/−{r['entries']['removed']} |")
    lines += [f"- 건너뜀 {s['code']}: {s['reason']}" for s in skipped]
    return "\n".join(lines) + "\n"


# 실행 — reader(일봉 공급자)는 테스트에서 가짜로 바꿈
def main(argv: list[str] | None = None, *, reader=None) -> int:
    a = options(argv)
    start, end = str(pd.Timestamp(a.start).date()), str(pd.Timestamp(a.end).date())
    frame = pd.read_csv(a.rows, dtype={"code": str, "base_date": str})
    table = forecasts(frame.to_dict("records"))
    codes = [c.strip().zfill(6) for c in a.codes.split(",") if c.strip()] or sorted(table)
    conf = {"rows": a.rows, "start": start, "end": end, "codes": codes, "lag": a.lag, "bars": a.bars,
            "cache": a.cache, "holdout_start": HOLDOUT_START}
    results: list[dict] = []
    skipped: list[dict] = []
    for code in codes:
        candles_15m = minutes(a.bars, code, start, end)
        if len(candles_15m) < MIN_BARS:
            skipped.append({"code": code, "reason": f"15분봉 부족 ({len(candles_15m)}개 < {MIN_BARS}, "
                                                    f"{HOLDOUT_START} 이전만 읽음)"})
            continue
        daily = ohlc(history(code, start, end, cache=a.cache, reader=reader))
        results.append(comparison(code, candles_15m, daily, table.get(code, {}), lag=a.lag))
        print(f"{code} 비교 완료", flush=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"scope": "dev", "config": conf, "results": results, "skipped": skipped}
    (out / "injection.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "injection.md").write_text(overview(conf, results, skipped), encoding="utf-8")
    print(f"비교 {len(results)}종목 · 건너뜀 {len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
