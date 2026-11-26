# 오프라인 지표 — 수익률 MAE·skill, 부호 적중률(비교 기준 2개), 날짜별 rank IC, Direction 구간표, 사전 등록 판정
import math

import pandas as pd

from offline.rules import BUCKET_MIN_N, HIT_ABS_PCT, IC_MIN_NAMES, IC_T_MIN, SKILL_MIN

# Direction 점수 구간 (Scorer.pred가 내는 값)
BUCKETS = (-10, -5, 0, 5, 10)
# 적중률 95% 구간의 z
_Z = 1.96


# 채점 가능한 행 — 예측·실제 5일 수익률이 모두 있는 것
def pairs(table: list[dict]) -> list[dict]:
    out = []
    for r in table:
        p, a = r.get("pred_ret"), r.get("actual_ret")
        if p is None or a is None or pd.isna(p) or pd.isna(a):
            continue
        out.append(r)
    return out


# 수익률 MAE — 모델 vs "0% 예측", skill = 1 − MAE_model / MAE_zero (표본·분모 0이면 None)
def mae(table: list[dict]) -> dict:
    rs = pairs(table)
    n = len(rs)
    if n == 0:
        return {"n": 0, "mae_model": None, "mae_zero": None, "skill": None}
    model = sum(abs(r["pred_ret"] - r["actual_ret"]) for r in rs) / n
    zero = sum(abs(r["actual_ret"]) for r in rs) / n
    skill = 1 - model / zero if zero > 0 else None
    return {"n": n, "mae_model": model, "mae_zero": zero, "skill": skill}


# Wilson 95% 구간 (k/n)
def wilson(k: int, n: int, z: float = _Z) -> tuple[float, float] | tuple[None, None]:
    if n <= 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return mid - half, mid + half


# 적중률 한 줄 (k, n, 비율, 95% 구간)
def rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": k / n if n else None, "lo": lo, "hi": hi}


# 부호 적중률 — |예측| > threshold% 표본만, 같은 표본에서 "항상 상승"과 "직전 5일 모멘텀 부호" (0은 적중 아님)
def hits(table: list[dict], threshold: float = HIT_ABS_PCT) -> dict:
    rs = [r for r in pairs(table) if abs(r["pred_ret"]) > threshold]
    n = len(rs)
    model = sum(1 for r in rs if r["pred_ret"] * r["actual_ret"] > 0)
    up = sum(1 for r in rs if r["actual_ret"] > 0)
    mom = sum(1 for r in rs if r.get("mom_ret") is not None and not pd.isna(r["mom_ret"])
              and r["mom_ret"] * r["actual_ret"] > 0)
    return {"threshold_pct": threshold, "n": n,
            "model": rate(model, n), "up": rate(up, n), "momentum": rate(mom, n)}


# 날짜별 횡단 Spearman rank IC — 순위(평균 순위) 뒤 피어슨 상관, 종목 수 미달·순위 상수 날짜는 제외
def ic(table: list[dict], min_names: int = IC_MIN_NAMES) -> dict:
    rs = pairs(table)
    series: dict[str, float] = {}
    excluded: dict[str, str] = {}
    if rs:
        df = pd.DataFrame(rs)[["base_date", "pred_ret", "actual_ret"]]
        for day, g in df.groupby("base_date", sort=True):
            if len(g) < min_names:
                excluded[str(day)] = f"종목 {len(g)}개 < {min_names}"
                continue
            ranks = g[["pred_ret", "actual_ret"]].rank(method="average")
            value = ranks["pred_ret"].corr(ranks["actual_ret"])
            if pd.isna(value):
                excluded[str(day)] = "순위 상수"
                continue
            series[str(day)] = float(value)
    n = len(series)
    vals = pd.Series(list(series.values()), dtype=float)
    mean = float(vals.mean()) if n else None
    std = float(vals.std(ddof=1)) if n >= 2 else None
    t = mean / (std / math.sqrt(n)) if n >= 2 and std and std > 0 else None
    return {"dates": n, "mean": mean, "std": std, "t": t, "min_names": min_names,
            "excluded": len(excluded), "series": series, "excluded_dates": excluded}


# Direction 구간별 실제 5일 수익률 평균과 건수
def buckets(table: list[dict]) -> list[dict]:
    rs = pairs(table)
    out = []
    for b in BUCKETS:
        vals = [r["actual_ret"] for r in rs if r.get("direction") is not None and round(float(r["direction"])) == b]
        out.append({"score": b, "n": len(vals), "mean_ret": sum(vals) / len(vals) if vals else None})
    return out


# 전체 지표
def stats(table: list[dict]) -> dict:
    return {"rows": len(table), "usable": len(pairs(table)),
            "mae": mae(table), "hits": hits(table), "ic": ic(table), "buckets": buckets(table)}


# 사전 등록 판정 — 넷 다 통과해야 PASS, 표본 부족으로 판정 불가면 실패
def judgment(st: dict, *, overlap: bool = False) -> dict:
    checks: dict[str, bool] = {}
    notes: dict[str, str] = {}

    t = st["ic"]["t"]
    checks["ic"] = t is not None and t >= IC_T_MIN
    notes["ic"] = (f"판정 불가: 유효 날짜 {st['ic']['dates']}개 (날짜당 {IC_MIN_NAMES}종목 이상, 2개 이상·분산 > 0 필요)"
                   if t is None else f"t={t:.2f} (기준 ≥ {IC_T_MIN})")

    skill = st["mae"]["skill"]
    checks["skill"] = skill is not None and skill > SKILL_MIN
    notes["skill"] = "판정 불가: 표본 없음" if skill is None else f"skill={skill:.4f} (기준 > {SKILL_MIN})"

    h = st["hits"]
    m, up, mom = h["model"]["rate"], h["up"]["rate"], h["momentum"]["rate"]
    checks["hit"] = h["n"] > 0 and m is not None and m > up and m > mom
    notes["hit"] = ("판정 불가: |예측| > 1% 표본 없음" if h["n"] == 0
                    else f"모델 {m:.3f} / 항상 상승 {up:.3f} / 5일 모멘텀 {mom:.3f} (n={h['n']})")

    qual = [b for b in st["buckets"] if b["n"] >= BUCKET_MIN_N]
    rising = len(qual) >= 2 and all(a["mean_ret"] < b["mean_ret"] for a, b in zip(qual, qual[1:]))
    checks["bucket"] = rising
    notes["bucket"] = (f"판정 불가: 건수 {BUCKET_MIN_N} 이상 구간 {len(qual)}개" if len(qual) < 2
                       else " < ".join(f"{b['score']:+d}:{b['mean_ret']:+.2f}%" for b in qual)
                       + (" (점수 순 증가)" if rising else " (점수 순 증가 아님)"))

    passed = all(checks.values()) and not overlap
    if overlap:
        notes["overlap"] = "컷 간격 < 5거래일 — 블록이 겹쳐 판정 무효 (주입용 표 생성 전용)"
    return {"pass": passed, "checks": checks, "notes": notes,
            "message": "PASS" if passed else "Direction 효과 0 고정"}
