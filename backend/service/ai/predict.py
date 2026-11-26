# Transformer 인코더 기반 주가 예측 서비스
import hashlib
import io
import json
import logging
import math
import threading
import time
import zlib
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from config import settings
# 사양(SPEC·FEATURES·WARM)과 버전 — 종목별 core()·공용 학습·서빙 추론이 같은 값을 씀 (정의는 spec.py 한 곳)
from service.ai.spec import FEATURES, PROFILE, SPEC, VERSION, WARM
from service.infra.ttl_cache import TTLCache
from service.market.holidays import CLOSE, lastday, mkt, nextday

logger = logging.getLogger(__name__)

# 공용 모델 파일 — offline.pool이 쓰고 서빙(artifact)이 읽음, 폴더는 settings.pooled_dir
WEIGHTS = "weights.pt"
META    = "meta.json"

# 예측 캐시 키 — 결과 형식이 바뀌면 버전을 올려 재배포 뒤 Redis의 옛 형식이 읽히지 않게 함
# (v2: base_close·base_date, v3: 예측 {date, close, change_pct}·지표 {mae, mae_zero, skill, n_val}, v4: source·validated·spec)
_KEY = "pred:v4:"
# 학습 최소 확정 일봉 수 — 미만이면 학습하지 않음
_MIN_ROWS = 200
# 기준일이 최근 확정 거래일과 다를 때(공급자 지연 등) 캐시 초
_LAG_TTL = 600
# 캐시 최소 초
_MIN_TTL = 60
# FDR 일봉 조회 대기 한도(초) — FinanceDataReader에는 타임아웃이 없어 별도 데몬 스레드에서 돌리고, 넘기면 버림(예측 실패)
_FDR_WAIT = 15.0

# 예측 캐시 유효기간(초) — 입력(확정 일봉)이 바뀔 수 있는 다음 시점(다음 개장일 15:30)까지, 최소 60초
# 기준일이 가장 최근 확정 거래일보다 이전(공급자 지연)·이후(미완성봉 의심)이거나 없으면 600초
def predttl(base_date: str | None, now: datetime) -> float:
    try:
        base = date.fromisoformat(base_date)
    except (TypeError, ValueError):
        return _LAG_TTL
    last = lastday(now)
    if base != last:
        return _LAG_TTL
    day = nextday(last)
    until = now.replace(year=day.year, month=day.month, day=day.day,
                        hour=CLOSE.hour, minute=CLOSE.minute, second=0, microsecond=0)
    return max((until - now).total_seconds(), _MIN_TTL)

# 종목 코드 고정 시드 — 같은 입력이면 같은 예측 (프로세스마다 다른 hash() 대신 crc32)
def codeseed(symbol: str) -> int:
    return zlib.crc32(symbol.zfill(6).encode())

# 한도 초과 — timebox가 한도 안에 끝나지 않은 호출을 버림 (조회 자체가 낸 TimeoutError와 구분)
class Overtime(TimeoutError):
    pass

# 한도 있는 호출 — call을 데몬 스레드에서 돌리고 wait초까지만 기다림. 끝나면 결과(스레드 안 예외는 다시 올림), 넘기면 Overtime
# 넘긴 뒤 스레드는 버림 — 늦은 값·예외는 스레드 안에서 받아 두기만 해서 밖으로 나오지 않고, 데몬이라 프로세스 종료를 막지 않음
def timebox(call, wait: float, name: str = "timebox"):
    box: dict = {}

    def body() -> None:
        try:
            box["value"] = call()
        except BaseException as e:
            box["error"] = e

    worker = threading.Thread(target=body, name=name, daemon=True)
    worker.start()
    worker.join(wait)
    if worker.is_alive():
        raise Overtime(f"{wait:g}초 안에 응답 없음")
    if "error" in box:
        raise box["error"]
    return box["value"]

# 일봉(Open·High·Low·Close·Volume) → 피처 7열 (FEATURES 순서, 앞 WARM행 버림, 각 행은 그날까지의 값만 씀)
# 남은 행에 NaN·inf가 있으면 0으로 바꾸지 않고 ValueError (종가 결측, 고가·저가 0 등)
def features(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in ("Open", "High", "Low", "Close", "Volume") if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 부족: {missing}")
    o, hi, lo, c, v = (df[k].astype(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    # RSI14 (단순 이동평균, 옛 feat()와 같은 식)
    delta = c.diff()
    gain  = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi   = 100 - (100 / (1 + gain / (loss + 1e-10)))
    with np.errstate(divide="ignore", invalid="ignore"):
        out = pd.DataFrame({
            "ret":   np.log(c / c.shift(1)),
            "range": np.log(hi / lo),
            "body":  np.log(c / o),
            "ma5":   c / c.rolling(window=5).mean() - 1,
            "ma20":  c / c.rolling(window=20).mean() - 1,
            # 거래정지일(거래량 0)에도 유한
            "vol":   np.log((v + 1) / (v.rolling(window=20).mean() + 1)),
            "rsi":   rsi / 100 - 0.5,
        }, index=df.index)[list(FEATURES)].iloc[WARM:]
    bad = ~np.isfinite(out.to_numpy())
    if bad.any():
        row, col = np.argwhere(bad)[0]
        raise ValueError(f"피처 비유한값(NaN/inf): {FEATURES[col]} {out.index[row]}")
    return out

# 피처 → 윈도우 (X: 끝 행까지 seq행, y: 끝 행 종가 대비 1~horizon일 뒤 종가의 누적 로그수익률, ends: 끝 행 번호)
# close는 일봉 종가 — 피처 날짜로 맞춤, 비었거나 0 이하면 ValueError
def windows(feats: pd.DataFrame, close: pd.Series, seq: int = SPEC["seq"],
            horizon: int = SPEC["horizon"]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    close = close.reindex(feats.index).astype(float).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        logc = np.log(close)
    if not np.isfinite(logc).all():
        raise ValueError("종가가 비었거나 0 이하")
    x     = feats.to_numpy(dtype=float)
    count = len(x) - seq - horizon + 1
    if count <= 0:
        return np.empty((0, seq, x.shape[1])), np.empty((0, horizon)), np.empty(0, dtype=int)
    ends = np.arange(seq - 1, seq - 1 + count)
    X    = np.stack([x[e - seq + 1: e + 1] for e in ends])
    y    = np.stack([logc[e + 1: e + 1 + horizon] - logc[e] for e in ends])
    return X, y, ends

# 시간순 분할 (윈도우 수 → 학습 윈도우 수, 검증 첫 윈도우 번호) — 검증은 뒤쪽 15%(최소 20)
# 학습은 검증 앞 horizon개를 버림(purge) → 학습 타깃의 마지막 날 < 검증 첫 윈도우의 기준일(컷)
def holdout(total: int, horizon: int = SPEC["horizon"]) -> tuple[int, int]:
    nval = max(int(total * SPEC["val"]), SPEC["valmin"])
    v0   = total - nval
    ntr  = v0 - horizon
    if ntr < 10:
        raise ValueError(f"학습 윈도우 부족: 전체 {total}개 (검증 {nval}, purge {horizon})")
    return ntr, v0

# 표준화 통계 — 피처 평균·표준편차(rows), 타깃 표준편차(targets, 열마다), 표준편차 0이면 1
def moments(rows: np.ndarray, targets: np.ndarray) -> dict:
    rows, targets = np.asarray(rows, dtype=float), np.asarray(targets, dtype=float)
    sd, ysd = rows.std(axis=0), targets.std(axis=0)
    return {
        "mu":  rows.mean(axis=0),
        "sd":  np.where(sd > 1e-12, sd, 1.0),
        "ysd": np.where(ysd > 1e-12, ysd, 1.0),
    }

# 피처 표준화 (통계는 moments())
def zscore(x: np.ndarray, stats: dict) -> np.ndarray:
    return (np.asarray(x, dtype=float) - stats["mu"]) / stats["sd"]

# 일봉 → 학습·검증 표본 (통계는 학습 윈도우의 입력 행·타깃으로만 계산, 타깃은 표준편차로만 나눔)
# 반환: feats·X·y·ends(원본), ntr·v0(분할), cut(검증 첫 윈도우의 기준일), stats, xt·yt(학습), xv·yv(검증)
def samples(df: pd.DataFrame, seq: int = SPEC["seq"], horizon: int = SPEC["horizon"]) -> dict:
    feats      = features(df)
    X, y, ends = windows(feats, df["Close"], seq, horizon)
    ntr, v0    = holdout(len(X), horizon)
    stats      = moments(feats.to_numpy(dtype=float)[: ends[ntr - 1] + 1], y[:ntr])
    xs, ys     = zscore(X, stats), y / stats["ysd"]
    return {
        "feats": feats, "X": X, "y": y, "ends": ends, "ntr": ntr, "v0": v0,
        "cut": feats.index[ends[v0]], "stats": stats,
        "xt": xs[:ntr], "yt": ys[:ntr], "xv": xs[v0:], "yv": ys[v0:],
    }

# 여러 종목 일봉 → 공용 학습·검증 표본 (피처·윈도우는 samples()와 같은 함수, 종목은 코드 순)
# 날짜 컷 하나를 모든 종목에 씀: 학습 = 타깃 마지막 날 < 컷, 검증 = 기준일 ≥ 컷, 그 사이(기준일 < 컷 ≤ 타깃 끝) 윈도우는 버림(purge)
# cut이 없으면 전 종목 기준일 날짜의 뒤쪽 15%(최소 20일)부터 검증 — 종목이 하나면 samples()와 같은 분할
# 통계: stats = 학습 윈도우의 입력 행·타깃(전 종목 합쳐 한 번), full = 전체 윈도우(재학습·서빙용)
# 반환: cut, stats, full, xt·yt·xv·yv(표준화, float32), yraw(검증 원 타깃), xa·ya(전체, full 통계),
#       tags(구간별 code·base·tail), counts(구간별 윈도우 수·기준일 날짜 수), codes, skipped(사유)
def panel(frames: dict, cut=None, seq: int = SPEC["seq"], horizon: int = SPEC["horizon"]) -> dict:
    parts, skipped = [], []
    for code in sorted(frames):
        df = frames[code]
        try:
            feats      = features(df)
            X, y, ends = windows(feats, df["Close"], seq, horizon)
        except ValueError as e:
            skipped.append({"code": code, "reason": str(e)})
            continue
        if not len(X):
            skipped.append({"code": code, "reason": f"윈도우 없음 ({len(df)}일)"})
            continue
        parts.append({"code": code, "rows": feats.to_numpy(dtype=float), "X": X, "y": y, "ends": ends,
                      "base": feats.index[ends], "tail": feats.index[ends + horizon]})
    if not parts:
        raise ValueError(f"학습할 종목 없음 (건너뜀 {len(skipped)})")
    if cut is None:
        days = sorted(set().union(*[set(p["base"]) for p in parts]))
        nval = max(int(len(days) * SPEC["val"]), SPEC["valmin"])
        cut  = days[max(len(days) - nval, 0)]
    cut = pd.Timestamp(cut)
    for p in parts:
        p["train"] = np.asarray(p["tail"] < cut)
        p["val"]   = np.asarray(p["base"] >= cut)
    ntrain = sum(int(p["train"].sum()) for p in parts)
    nval   = sum(int(p["val"].sum()) for p in parts)
    if ntrain < 10:
        raise ValueError(f"학습 윈도우 부족: {ntrain}개 (컷 {cut.date()})")
    if nval == 0:
        raise ValueError(f"검증 윈도우 없음 (컷 {cut.date()})")
    stats = moments(np.concatenate([p["rows"][: p["ends"][p["train"]][-1] + 1] for p in parts if p["train"].any()]),
                    np.concatenate([p["y"][p["train"]] for p in parts]))
    full  = moments(np.concatenate([p["rows"][: p["ends"][-1] + 1] for p in parts]),
                    np.concatenate([p["y"] for p in parts]))

    # 구간 텐서 — 종목마다 표준화한 뒤 float32로 쌓음 (학습 데이터셋이 어차피 float32, 메모리 절반)
    def block(key: str | None, st: dict) -> tuple[np.ndarray, np.ndarray]:
        picks = [(p["X"][p[key]], p["y"][p[key]]) if key else (p["X"], p["y"]) for p in parts]
        return (np.concatenate([zscore(a, st).astype(np.float32) for a, _ in picks]),
                np.concatenate([(b / st["ysd"]).astype(np.float32) for _, b in picks]))

    # 구간 표 — 윈도우마다 종목·기준일·타깃 마지막 날
    def table(key: str | None) -> pd.DataFrame:
        rows = [pd.DataFrame({"code": p["code"], "base": p["base"][p[key]] if key else p["base"],
                              "tail": p["tail"][p[key]] if key else p["tail"]}) for p in parts]
        return pd.concat(rows, ignore_index=True)

    xt, yt = block("train", stats)
    xv, yv = block("val", stats)
    xa, ya = block(None, full)
    tags   = {"train": table("train"), "val": table("val"), "full": table(None)}
    return {
        "cut": cut, "stats": stats, "full": full,
        "xt": xt, "yt": yt, "xv": xv, "yv": yv, "yraw": np.concatenate([p["y"][p["val"]] for p in parts]),
        "xa": xa, "ya": ya, "tags": tags,
        "counts": {k: {"windows": len(t), "dates": int(t["base"].nunique())} for k, t in tags.items()},
        "codes": [p["code"] for p in parts], "skipped": skipped,
    }

# 시계열 데이터셋
class StockDataset(Dataset):
    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X = torch.tensor(X, dtype=torch.float32)
        self.y = torch.tensor(y, dtype=torch.float32)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]

# 시퀀스 위치 인코딩
class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 5000):
        super().__init__()
        pe       = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(
            torch.arange(0, d_model, 2).float() * -(np.log(10000.0) / d_model)
        )
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(1)
        self.register_buffer("pe", pe)

    def forward(self, x):
        return x + self.pe[: x.size(0), :]

# Transformer 인코더 기반 수익률 예측 모델 — 시간축 평균 풀링 뒤 선형 헤드 (Flatten 헤드 없음)
class StockTransformer(nn.Module):
    def __init__(
        self,
        input_dim: int,
        output_dim: int,
        sequence_length: int,
        d_model: int = SPEC["d_model"],
        nhead: int = SPEC["nhead"],
        num_layers: int = SPEC["layers"],
        dim_feedforward: int = SPEC["ff"],
        dropout: float = SPEC["dropout"],
    ):
        super().__init__()
        self.embedding           = nn.Linear(input_dim, d_model)
        self.positional_encoding = PositionalEncoding(d_model, max_len=sequence_length)
        # dim_feedforward 명시 — 기본값 2048이면 파라미터 대부분이 FFN에 몰림
        encoder_layer            = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=dim_feedforward, dropout=dropout, batch_first=True
        )
        self.transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.fc                  = nn.Linear(d_model, output_dim)

    def forward(self, x):
        x = self.embedding(x)
        x = self.positional_encoding(x.permute(1, 0, 2))
        x = x.permute(1, 0, 2)
        x = self.transformer_encoder(x)
        return self.fc(x.mean(dim=1))

# 시드 고정 모델 생성 — 같은 시드면 같은 초기 가중치 (뒤따르는 셔플·드롭아웃도 이 시드에서 이어짐)
def network(seed: int, inputs: int, outputs: int, seq: int) -> StockTransformer:
    torch.manual_seed(seed)
    return StockTransformer(inputs, outputs, seq)

# 학습 1에폭 (셔플 배치)
def epoch(model: nn.Module, loader: DataLoader, optimizer, criterion) -> None:
    model.train()
    for X_b, y_b in loader:
        optimizer.zero_grad()
        loss = criterion(model(X_b), y_b)
        loss.backward()
        optimizer.step()

# 검증 손실 — 샘플 수로 가중한 평균 (배치 평균의 평균이 아님, 마지막 배치가 작아도 같은 무게), 표본 없으면 inf
def valloss(model: nn.Module, loader: DataLoader, criterion) -> float:
    model.eval()
    total, count = 0.0, 0
    with torch.no_grad():
        for X_b, y_b in loader:
            total += criterion(model(X_b), y_b).item() * len(X_b)
            count += len(X_b)
    return total / count if count else float("inf")

# 옵티마이저 — AdamW (lr·weight_decay는 SPEC)
def adamw(model: nn.Module):
    return torch.optim.AdamW(model.parameters(), lr=SPEC["lr"], weight_decay=SPEC["decay"])

# 검증으로 에폭 수 고르기 — 검증 손실이 가장 낮은 에폭의 가중치와 그 에폭 수 (patience 동안 개선 없으면 중단)
# 검증 손실이 한 번도 유한하지 않으면(발산·NaN) ValueError
def trial(xt: np.ndarray, yt: np.ndarray, xv: np.ndarray, yv: np.ndarray, seed: int, epochs: int,
          seq: int) -> tuple[nn.Module, int]:
    model     = network(seed, xt.shape[-1], yt.shape[-1], seq)
    train     = DataLoader(StockDataset(xt, yt), batch_size=SPEC["batch"], shuffle=True)
    val       = DataLoader(StockDataset(xv, yv), batch_size=SPEC["batch"])
    opt       = adamw(model)
    criterion = nn.HuberLoss()
    best, state, chosen, stale = float("inf"), None, 0, 0
    for n in range(1, epochs + 1):
        epoch(model, train, opt, criterion)
        loss = valloss(model, val, criterion)
        if math.isfinite(loss) and loss < best:
            best, chosen, stale = loss, n, 0
            state = {k: v.clone() for k, v in model.state_dict().items()}
            continue
        stale += 1
        if stale >= SPEC["patience"]:
            break
    if state is None:
        raise ValueError("검증 손실이 유한하지 않음 (학습 발산)")
    model.load_state_dict(state)
    model.eval()
    return model, chosen

# 전체 윈도우(학습+검증)로 같은 시드에서 rounds 에폭 다시 학습 (조기 종료 없음)
def fullmodel(x: np.ndarray, y: np.ndarray, seed: int, rounds: int, seq: int) -> nn.Module:
    model     = network(seed, x.shape[-1], y.shape[-1], seq)
    loader    = DataLoader(StockDataset(x, y), batch_size=SPEC["batch"], shuffle=True)
    opt       = adamw(model)
    criterion = nn.HuberLoss()
    for _ in range(rounds):
        epoch(model, loader, opt, criterion)
    model.eval()
    return model

# 표준화 입력 → 누적 로그수익률 (모델 출력 × 타깃 표준편차)
def inference(model: nn.Module, xs: np.ndarray, ysd: np.ndarray) -> np.ndarray:
    model.eval()
    with torch.no_grad():
        out = model(torch.tensor(np.asarray(xs), dtype=torch.float32)).numpy().astype(float)
    return out * ysd

# 예측 입력 — 피처의 마지막 seq행 (종목별 core()·공용 projection()이 같이 씀), 모자라면 ValueError
def latest(feats: pd.DataFrame, seq: int = SPEC["seq"]) -> np.ndarray:
    rows = feats.to_numpy(dtype=float)
    if len(rows) < seq:
        raise ValueError(f"예측 입력 부족: 피처 {len(rows)}행 < {seq}행")
    return rows[-seq:]

# 검증 지표 — 5일 누적 단순 수익률(%) 기준 모델 MAE, 0% 예측 MAE, skill = 1 − mae/mae_zero(음수 가능, 0% MAE가 0이면 None)
def scorecard(pred, true) -> dict:
    pred, true = np.asarray(pred, dtype=float), np.asarray(true, dtype=float)
    mae  = float(np.mean(np.abs(pred - true)))
    zero = float(np.mean(np.abs(true)))
    return {
        "mae":      round(mae, 4),
        "mae_zero": round(zero, 4),
        "skill":    round(1 - mae / zero, 4) if zero > 0 else None,
        "n_val":    int(len(true)),
    }

# 공용 모델 읽기 — folder의 meta.json·weights.pt → {"model", "stats", "meta"}, 쓸 수 없으면 None (예외를 던지지 않음)
# None: 파일 없음, 메타 손상, 사양(version·spec)이 지금 코드와 다름, 가중치 해시가 메타와 다름, 가중치·통계 손상
# 가중치는 state_dict만 — torch.load(weights_only=True)라 텐서 아닌 피클 객체는 실행하지 않고 거부
def artifact(folder) -> dict | None:
    try:
        root = Path(folder)
        if not (root / META).is_file() or not (root / WEIGHTS).is_file():
            logger.info(f"공용 모델 없음: {root}")
            return None
        meta = json.loads((root / META).read_text(encoding="utf-8"))
        if meta.get("version") != VERSION or meta.get("spec") != PROFILE:
            logger.warning(f"공용 모델 사양 불일치 — 쓰지 않음: {meta.get('version')} ≠ {VERSION}")
            return None
        blob = (root / WEIGHTS).read_bytes()
        if hashlib.sha256(blob).hexdigest() != meta.get("weights_sha256"):
            logger.warning(f"공용 모델 가중치 해시가 메타와 다름 — 쓰지 않음: {root}")
            return None
        state = torch.load(io.BytesIO(blob), map_location="cpu", weights_only=True)
        model = StockTransformer(len(FEATURES), SPEC["horizon"], SPEC["seq"])
        model.load_state_dict(state)
        model.eval()
        stats = {k: np.asarray(meta["stats"][k], dtype=float) for k in ("mu", "sd", "ysd")}
        for k, n in (("mu", len(FEATURES)), ("sd", len(FEATURES)), ("ysd", SPEC["horizon"])):
            if stats[k].shape != (n,) or not np.isfinite(stats[k]).all():
                raise ValueError(f"표준화 통계 손상: {k} {stats[k].shape}")
        if (stats["sd"] <= 0).any() or (stats["ysd"] <= 0).any():
            raise ValueError("표준화 통계 손상: 표준편차 0 이하")
        return {"model": model, "stats": stats, "meta": meta}
    except Exception as e:
        # 크기 불일치 오류는 매개변수마다 한 줄이라 앞부분만
        logger.warning(f"공용 모델 읽기 실패 — 쓰지 않음 ({folder}): {repr(e)[:300]}")
        return None

# 기준일 다음 n개 개장일 (mkt() 기준 — 주말·휴장일 건너뜀)
def sessions(day: date, n: int) -> list[date]:
    out = []
    for _ in range(n):
        day = nextday(day)
        out.append(day)
    return out

# 누적 로그수익률 → 예측 항목 {date, close, change_pct} (close = round(기준가 × exp(r)), change_pct = 그 close의 기준가 대비 %)
def curve(base: int, day: date, rets) -> list[dict]:
    if base <= 0:
        raise ValueError(f"기준가가 0 이하: {base}")
    out = []
    for d, r in zip(sessions(day, len(rets)), rets):
        close = int(round(base * math.exp(float(r))))
        out.append({"date": d.isoformat(), "close": close, "change_pct": round((close - base) / base * 100, 2)})
    return out

# 공용 모델 추론 — 확정 일봉 → core()와 같은 형식 + source·validated·spec (학습 없음, 같은 입력이면 같은 출력)
# 입력은 마지막 seq행 피처를 메타의 full 통계로 표준화한 것, 지표는 메타의 개발 구간 값
# validated는 메타 값이 정확히 true일 때만 True (문자열·숫자·없음은 False), spec은 메타의 버전
def projection(stock_df: pd.DataFrame, bundle: dict, label: str = "") -> dict:
    if len(stock_df) < _MIN_ROWS:
        raise ValueError(f"예측 데이터 부족: {label} ({len(stock_df)}일 < {_MIN_ROWS}일)")
    stats = bundle["stats"]
    meta  = bundle.get("meta") or {}
    x     = zscore(latest(features(stock_df), SPEC["seq"]), stats)
    rets  = inference(bundle["model"], x[None], stats["ysd"])[0]
    if not np.isfinite(rets).all():
        raise ValueError(f"예측값이 유한하지 않음: {label}")
    base = int(round(float(stock_df["Close"].iloc[-1])))
    day  = pd.Timestamp(stock_df.index[-1]).date()
    return {
        "predictions": curve(base, day, rets),
        "metrics":     dict(meta.get("metrics") or {}),
        "base_close":  base,
        "base_date":   day.isoformat(),
        "source":      "pooled",
        "validated":   meta.get("validated") is True,
        "spec":        meta.get("version"),
    }

# Transformer 기반 주가 예측 엔진
class Predictor:

    # 캐시/쓰레드풀 초기화
    def __init__(self):
        self._cache       = TTLCache()
        # 학습은 한 번에 1건 — 동시 학습이면 전역 시드를 걸어도 결과가 갈림
        self._executor    = ThreadPoolExecutor(max_workers=1)
        self._FAIL_TTL    = 600   # 백그라운드 학습 실패 후 재시도 차단 10분
        self._inflight: dict[str, asyncio.Task] = {}
        # 백그라운드 학습 태스크 참조 (GC 방지)
        self._bg: set[asyncio.Task] = set()
        # 같은 프로세스의 매매 루프와 CPU 경합 방지 — 학습 내부 스레드 제한
        torch.set_num_threads(1)
        # 공용 모델 (bundle()이 처음 부를 때 한 번 읽음, 없음도 기억)
        self._pool: dict | None = None
        self._probed = False
        self._lock = threading.Lock()

    # 공용 모델 — 처음 필요할 때 settings.pooled_dir에서 한 번 읽고 결과(없음 포함)를 기억, 읽기 실패도 None
    # 가중치를 올리거나 바꾼 뒤에는 재시작해야 반영 (운영 반영 순서: 올리기 → 재시작 → /api/predict의 source 확인)
    def bundle(self) -> dict | None:
        with self._lock:
            if not self._probed:
                self._pool   = artifact(settings.pooled_dir)
                self._probed = True
                if self._pool is not None:
                    meta = self._pool["meta"]
                    logger.info(f"공용 모델 사용: {meta.get('version')} · 컷 {meta.get('train_end')} · "
                                f"종목 {meta.get('count')} · validated={meta.get('validated') is True}")
            return self._pool

    # 1년치 주가 데이터 수집 (FDR 우선, YFinance 폴백)
    # FDR은 별도 데몬 스레드에서 wait초(기본 _FDR_WAIT)까지만 기다림 — 넘기면 조회를 버리고 예측 실패 (yfinance로 넘어가지 않음, 하나뿐인 학습 스레드를 묶지 않음)
    def raw(self, symbol: str, wait: float | None = None) -> pd.DataFrame:
        symbol     = symbol.zfill(6)
        wait       = _FDR_WAIT if wait is None else wait
        end_date   = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        # FDR 시도
        try:
            import FinanceDataReader as fdr
            data = timebox(lambda: fdr.DataReader(symbol, start_date, end_date), wait, name=f"fdr-{symbol}")
            if data is not None and not data.empty:
                data = self.settled(data, symbol)
                logger.info(f"FDR 데이터 수집 성공: {symbol} ({len(data)}일)")
                return data
        except Overtime as e:
            logger.warning(f"FDR 응답 없음 {symbol}: {e} — 조회를 버리고 예측 실패")
            raise ValueError(f"데이터 수집 실패: {symbol} (FDR {wait:g}초 응답 없음)") from e
        except Exception as e:
            logger.warning(f"FDR 실패 {symbol}: {e}")
        # YFinance 폴백
        try:
            import yfinance as yf
            for suffix in [".KS", ".KQ"]:
                ticker = f"{symbol}{suffix}"
                stock  = yf.Ticker(ticker)
                data   = stock.history(start=start_date, end=end_date, timeout=10)
                if not data.empty:
                    data = self.settled(data, ticker)
                    logger.info(f"YF 데이터 수집 성공: {ticker} ({len(data)}일)")
                    return data
        except Exception as e:
            logger.warning(f"YFinance 실패 {symbol}: {e}")
        raise ValueError(f"데이터 수집 실패: {symbol}")

    # 장 마감 전이면 형성 중인 당일봉을 제거 (네이버/야후 일봉은 장중 미완성 봉 포함)
    def settled(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if df.empty:
            return df
        now = datetime.now()
        if not mkt(now.date()):
            return df
        if now >= now.replace(hour=15, minute=30, second=0, microsecond=0):
            return df
        if df.index[-1].date() == now.date():
            logger.info(f"당일 미완성봉 제외: {symbol}")
            return df.iloc[:-1]
        return df

    # 5일 예측 — 확정 일봉 수집(raw) 뒤, 공용 모델이 있으면 추론만(projection, 학습 없음), 없으면 종목별 즉석 학습(core, 화면 표시용)
    # 반환 형식은 두 경로가 같음 (source "pooled"/"per_symbol" · validated · spec), 매매 점수에 닿을지는 strategy의 문이 정함
    def training(
        self,
        symbol: str,
        forecast_days: int = SPEC["horizon"],
        epochs: int = SPEC["epochs"],
        sequence_length: int = SPEC["seq"],
    ) -> dict:
        start_t  = time.time()
        stock_df = self.raw(symbol)
        pool     = self.bundle()
        if pool is not None:
            out = projection(stock_df, pool, symbol)
            logger.info(f"[{symbol}] 공용 모델 예측: {time.time() - start_t:.1f}초 · {out['spec']} · validated={out['validated']}")
            return out
        return self.core(stock_df, seed=codeseed(symbol), forecast_days=forecast_days, epochs=epochs,
                         sequence_length=sequence_length, label=symbol, start_t=start_t)

    # 확정 일봉 DataFrame → 누적 로그수익률 학습 및 5개 개장일 예측 (네트워크·현재 시각 미사용, 같은 df·seed면 같은 결과)
    # 순서: 피처·윈도우 → 시간순 분할(purge) → 학습 구간 통계로 표준화 → 검증으로 에폭 선택·지표 → 전체 윈도우로 재학습 → 예측
    def core(
        self,
        stock_df: pd.DataFrame,
        seed: int = 0,
        *,
        forecast_days: int = SPEC["horizon"],
        epochs: int = SPEC["epochs"],
        sequence_length: int = SPEC["seq"],
        label: str = "",
        start_t: float | None = None,
    ) -> dict:
        symbol  = label
        start_t = time.time() if start_t is None else start_t
        # 확정 일봉이 짧으면 학습하지 않음 (상장 직후·데이터 결손) — API 404, Direction 0점
        if len(stock_df) < _MIN_ROWS:
            raise ValueError(f"학습 데이터 부족: {symbol} ({len(stock_df)}일 < {_MIN_ROWS}일)")
        # 피처 NaN·inf, 0 이하 종가도 여기서 ValueError (조용히 0으로 바꾸지 않음)
        s = samples(stock_df, sequence_length, forecast_days)

        # 에폭 선택 (학습 윈도우로 학습, 검증 윈도우 손실로 고름) — 같은 입력이면 같은 예측, 시드는 training()의 codeseed
        model, chosen = trial(s["xt"], s["yt"], s["xv"], s["yv"], seed, epochs, sequence_length)
        # 검증 지표 — 재학습 전 모델, 5일 누적 단순 수익률(%) 기준
        guess   = inference(model, s["xv"], s["stats"]["ysd"])[:, -1]
        truth   = s["y"][s["v0"]:, -1]
        metrics = scorecard(np.expm1(guess) * 100, np.expm1(truth) * 100)

        # 재학습 — 전체 윈도우(학습+검증), 통계도 전체 윈도우의 입력 행·타깃으로 다시 계산, 같은 시드·고른 에폭 수
        rows  = s["feats"].to_numpy(dtype=float)
        full  = moments(rows[: s["ends"][-1] + 1], s["y"])
        final = fullmodel(zscore(s["X"], full), s["y"] / full["ysd"], seed, chosen, sequence_length)
        rets  = inference(final, zscore(latest(s["feats"], sequence_length), full)[None], full["ysd"])[0]
        if not np.isfinite(rets).all():
            raise ValueError(f"예측값이 유한하지 않음: {symbol}")

        # 예측 기준 — 학습에 쓴 확정 일봉의 마지막 종가(파이썬 int, JSON 직렬화 안전)와 날짜, 날짜는 다음 개장일부터
        base = int(round(float(stock_df["Close"].iloc[-1])))
        day  = pd.Timestamp(stock_df.index[-1]).date()

        elapsed = time.time() - start_t
        logger.info(f"[{symbol}] 예측 완료: {elapsed:.1f}초, 에폭 {chosen}, "
                    f"검증 MAE={metrics['mae']:.4f}%p (0% 예측 {metrics['mae_zero']:.4f}%p, skill={metrics['skill']})")

        # 종목별 모델은 검증 대상이 아님 — validated 항상 False (Direction 0점), spec은 지금 코드의 사양 버전
        return {
            "predictions": curve(base, day, rets),
            "metrics":     metrics,
            "base_close":  base,
            "base_date":   day.isoformat(),
            "source":      "per_symbol",
            "validated":   False,
            "spec":        VERSION,
        }

    # 캐시된 예측 조회 (없으면 None)
    def cached(self, symbol: str) -> dict | None:
        return self._cache.lookup(f"{_KEY}{symbol.zfill(6)}")

    # 비동기 예측 진입점 (캐시 + 동일 종목 동시 요청은 학습 1회로 병합)
    async def forecast(self, symbol: str) -> dict:
        symbol = symbol.zfill(6)
        key = f"{_KEY}{symbol}"
        cached = self._cache.lookup(key)
        if cached is not None:
            logger.info(f"캐시 사용: {symbol}")
            return cached

        task = self._inflight.get(symbol)
        if task is None:
            # 학습 1회 실행 후 캐시 저장
            async def slot() -> dict:
                loop   = asyncio.get_running_loop()
                result = await loop.run_in_executor(self._executor, self.training, symbol)
                # 다음 확정 일봉이 나올 때까지 캐시 (settled()와 같은 시계)
                self._cache.memo(key, result, predttl(result.get("base_date"), datetime.now()))
                return result

            # 완료 시 in-flight 해제 + 예외 회수 (대기자가 모두 취소된 뒤 실패해도 "never retrieved" 경고 없음)
            def landed(done: asyncio.Task) -> None:
                self._inflight.pop(symbol, None)
                if not done.cancelled():
                    done.exception()

            task = asyncio.create_task(slot())
            self._inflight[symbol] = task
            task.add_done_callback(landed)
        # 대기자 하나가 취소돼도 학습과 나머지 대기자는 유지
        return await asyncio.shield(task)

    # 백그라운드 학습 시작 — 호출자는 기다리지 않음 (학습 중·최근 실패 종목은 건너뜀, 예외를 던지지 않음)
    def warmup(self, symbol: str) -> None:
        try:
            symbol = symbol.zfill(6)
            name = f"warm:{symbol}"
            if symbol in self._inflight or any(t.get_name() == name for t in self._bg):
                return
            if self._cache.lookup(f"predfail:{symbol}"):
                return

            # 끝나면 참조 해제, 실패는 경고 로그 + 재시도 차단
            def closed(task: asyncio.Task) -> None:
                self._bg.discard(task)
                if task.cancelled():
                    return
                err = task.exception()
                if err is not None:
                    logger.warning(f"[{symbol}] 백그라운드 예측 실패: {err!r}")
                    self._cache.memo(f"predfail:{symbol}", True, self._FAIL_TTL)

            task = asyncio.create_task(self.forecast(symbol), name=name)
            self._bg.add(task)
            task.add_done_callback(closed)
        except Exception as e:
            logger.warning(f"[{symbol}] 백그라운드 예측 시작 실패: {e!r}")

# 모듈 레벨 인스턴스
predictor = Predictor()

# 하위 호환 별칭
forecast = predictor.forecast
