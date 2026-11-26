# 오프라인 일봉 — FinanceDataReader 공개 일봉을 디스크에 캐시
# --final 없이는 HOLDOUT_START 이후 행을 받지도, 캐시에 쓰지도, 로드 결과에 남기지도 않는다
import datetime
import json
from pathlib import Path

import pandas as pd

from offline.rules import HOLDOUT_START, REGISTERED

# research 볼륨 — research.py는 바로 아래 {code}.jsonl만 읽으므로 하위 폴더·다른 확장자와 섞이지 않음
_RESEARCH = Path(__file__).resolve().parents[1] / "data" / "research"
# 기본 캐시 폴더
CACHE = _RESEARCH / "cache"
# 최종 구간 사용 표식 기본 경로
MARKER = _RESEARCH / "holdout_used.json"
# 학습 창 — 배포 raw()와 같은 달력일 365일
WINDOW = 365


# FDR 공개 일봉 (요청에는 종목 코드와 날짜만)
def fdrdaily(code: str, start: str, end: str) -> pd.DataFrame:
    import FinanceDataReader as fdr
    return fdr.DataReader(code, start, end)


# 최종 구간 앞 행만 (final이면 그대로)
def cutoff(df: pd.DataFrame, final: bool = False) -> pd.DataFrame:
    if final:
        return df
    return df[df.index < pd.Timestamp(HOLDOUT_START)]


# 평가 구간 [start, end]용 일봉 — 첫 컷의 학습 창(lookback 달력일, 기본 365)을 앞에 붙여 받고, (코드, 기간)별 CSV로 캐시
def history(
    code: str,
    start: str,
    end: str,
    *,
    cache: str | Path | None = None,
    final: bool = False,
    reader=None,
    lookback: int = WINDOW,
) -> pd.DataFrame:
    begin = (pd.Timestamp(start) - pd.Timedelta(days=lookback)).date()
    last = pd.Timestamp(end).date()
    if not final:
        last = min(last, (pd.Timestamp(HOLDOUT_START) - pd.Timedelta(days=1)).date())
    if last < begin:
        raise ValueError(f"기간 오류: {code} {begin} > {last}")
    folder = Path(cache) if cache else CACHE
    path = folder / f"{code}_{begin}_{last}.csv"
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    else:
        df = (reader or fdrdaily)(code, str(begin), str(last))
        if df is None or df.empty:
            raise ValueError(f"일봉 없음: {code}")
        df = cutoff(df.sort_index(), final)
        df = df[(df.index >= pd.Timestamp(begin)) & (df.index <= pd.Timestamp(last))]
        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        df.to_csv(tmp)
        tmp.replace(path)
    # 캐시에서 읽은 경우에도 기간과 최종 구간 경계를 다시 적용
    df = df.sort_index()
    df = df[(df.index >= pd.Timestamp(begin)) & (df.index <= pd.Timestamp(last))]
    return cutoff(df, final)


# 최종 구간 사용 표식 — 데이터를 읽기 전에 남김, 이미 있으면 거부 (다시 보려면 사용자가 파일을 지움)
def marker(path: str | Path, conf: dict) -> Path:
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"최종 구간은 이미 사용됨: {path} (다시 보려면 이 파일을 지운 뒤 실행)")
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ran_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "registered": REGISTERED,
        "holdout_start": HOLDOUT_START,
        "config": conf,
    }
    # 배타적 생성 — 동시에 두 번 실행해도 하나만 통과
    with path.open("x", encoding="utf-8") as fh:
        json.dump(row, fh, ensure_ascii=False, indent=1, default=str)
    return path
