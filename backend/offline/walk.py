# 워크포워드 — 컷마다 그 날까지의 최근 365일만 core()에 넘겨 학습하고, 다음 5거래일 실제 종가와 비교
import time

import pandas as pd

from offline.daily import WINDOW
from service.ai.predict import codeseed

# 채점 거래일 — 배포 forecast_days와 같음
HORIZON = 5


# 컷 날짜 — 평가 구간 [start, end]의 거래일을 step 간격으로, 목표 HORIZON 거래일이 달력 안에 있는 것만
def cuts(calendar: pd.DatetimeIndex, start: str, end: str, step: int = HORIZON) -> list[pd.Timestamp]:
    if step < 1:
        raise ValueError(f"컷 간격 오류: {step}")
    calendar = pd.DatetimeIndex(sorted(set(calendar)))
    last = len(calendar) - 1
    picks = []
    for i, day in enumerate(calendar):
        if day < pd.Timestamp(start) or day > pd.Timestamp(end):
            continue
        picks.append((i, day))
    return [day for n, (i, day) in enumerate(picks) if n % step == 0 and i + HORIZON <= last]


# 배포 학습 — service.ai.predict의 core() (에폭·창 길이 등은 배포 기본값)
def deployment(window: pd.DataFrame, seed: int, label: str) -> dict:
    from service.ai.predict import predictor
    return predictor.core(window, seed=seed, label=label)


# Direction 점수 — 라이브 채점기의 규칙(Scorer.labscore)을 그대로 호출, 매매 점수의 문(predgate)은 거치지 않음 (검증 전 모델 평가용)
def direction(result: dict) -> float:
    from service.trading.strategy import scorer
    score, _ = scorer.labscore(result, result.get("base_close"))
    return float(score)


# 결과 표 — (base_date, code)마다 한 행, 학습 실패·결측 컷은 skipped에 사유와 함께
def rows(
    frames: dict[str, pd.DataFrame],
    start: str,
    end: str,
    *,
    step: int = HORIZON,
    core=None,
    log=None,
) -> tuple[list[dict], list[dict]]:
    core = core or deployment
    frames = {code: df[df.index <= pd.Timestamp(end)].sort_index() for code, df in frames.items()}
    calendar = pd.DatetimeIndex(sorted(set().union(*[set(df.index) for df in frames.values()])))
    schedule = cuts(calendar, start, end, step)
    table: list[dict] = []
    skipped: list[dict] = []
    total = len(schedule) * len(frames)
    done = 0
    for cut in schedule:
        for code, df in frames.items():
            done += 1
            day = str(cut.date())
            if cut not in df.index:
                skipped.append({"code": code, "base_date": day, "reason": "거래 없음"})
                continue
            i = df.index.get_loc(cut)
            if i + HORIZON > len(df) - 1:
                skipped.append({"code": code, "base_date": day, "reason": "목표 기간이 데이터 밖"})
                continue
            # 학습 창 — 컷 날짜 포함, 그 뒤 행은 넘기지 않음
            window = df[(df.index >= cut - pd.Timedelta(days=WINDOW)) & (df.index <= cut)]
            t0 = time.time()
            try:
                result = core(window.copy(), seed=codeseed(code), label=code)
            except Exception as e:
                skipped.append({"code": code, "base_date": day, "reason": f"{type(e).__name__}: {e}"})
                continue
            base = float(result.get("base_close") or 0)
            closes = [float(p["close"]) for p in result.get("predictions") or []]
            if not base or len(closes) < HORIZON:
                skipped.append({"code": code, "base_date": day, "reason": "예측·기준가 없음"})
                continue
            if result.get("base_date") != day:
                skipped.append({"code": code, "base_date": day, "reason": f"기준일 불일치 {result.get('base_date')}"})
                continue
            close = df["Close"].astype(float)
            actual = float(close.iloc[i + HORIZON])
            prior = float(close.iloc[i - HORIZON]) if i >= HORIZON else None
            row = {
                "base_date": day,
                "code": code,
                "base_close": result["base_close"],
                **{f"pred{k + 1}": closes[k] for k in range(HORIZON)},
                "pred_ret": (closes[HORIZON - 1] - base) / base * 100,
                "actual_close": actual,
                "actual_ret": (actual - base) / base * 100,
                "mom_ret": (base / prior - 1) * 100 if prior else None,
                "direction": direction(result),
                "val_mae": (result.get("metrics") or {}).get("mae"),
                "train_rows": len(window),
                "target_date": str(df.index[i + HORIZON].date()),
            }
            table.append(row)
            if log:
                log(f"[{done}/{total}] {code} {day} {time.time() - t0:.1f}s pred={row['pred_ret']:+.2f}% "
                    f"actual={row['actual_ret']:+.2f}% dir={row['direction']:+.0f}")
    return table, skipped
