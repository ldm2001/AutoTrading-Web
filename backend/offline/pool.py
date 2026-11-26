# 공용 모델 오프라인 학습 CLI — 전 종목(ETF 제외) 여러 해 일봉으로 Transformer 하나를 학습
# cd backend && venv/bin/python -m offline.pool --train-end 2024-11-22 --out <폴더>
# 개발 구간 전용 — HOLDOUT_START 이후 행은 읽지 않는다 (--final 없음)
import argparse
import hashlib
import io
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from offline.daily import CACHE, history
from offline.rules import HOLDOUT_START
from service.ai.predict import (META, SPEC, WEIGHTS, artifact, fullmodel, inference, panel, projection, scorecard,
                                trial)
from service.ai.spec import PROFILE, VERSION
from service.market.stock_universe import NAMES

# 공용 모델 시드 — 종목별 codeseed 대신 하나
SEED = 0
# 일봉 시작 기본값
SINCE = "2018-01-01"
# ETF 표식 — api/stock.py _SCAN_CODES와 같은 기준
_ETF = ("KODEX", "TIGER", "ETF")
# 메타에 남기는 대상 종목 설명
_UNIVERSE = "NAMES에서 ETF 제외 — 지금 목록에 있는 종목만 모았으므로 상장폐지·편출 종목이 빠짐(생존 편향)"


# 대상 종목 — NAMES에서 ETF를 뺀 목록 (지금 목록에 있는 종목만 모으므로 상장폐지 종목이 빠짐 → 생존 편향)
def universe() -> list[str]:
    return [code for code, name in NAMES.items() if not any(tag in name for tag in _ETF)]


# 공용 학습 한 번 — 2단계 core()와 같은 순서: 데이터셋 → 검증으로 에폭 선택 → 검증 지표(재학습 전) → 전체 윈도우 재학습
# 반환 stats는 재학습·서빙용(full), trial은 에폭 선택·지표에 쓴 학습 구간 통계
def session(frames: dict, *, cut=None, seed: int = SEED, epochs: int = SPEC["epochs"]) -> dict:
    p = panel(frames, cut=cut)
    model, chosen = trial(p["xt"], p["yt"], p["xv"], p["yv"], seed, epochs, SPEC["seq"])
    guess = inference(model, p["xv"], p["stats"]["ysd"])[:, -1]
    metrics = scorecard(np.expm1(guess) * 100, np.expm1(p["yraw"][:, -1]) * 100)
    final = fullmodel(p["xa"], p["ya"], seed, chosen, SPEC["seq"])
    spans = {k: [str(t["base"].min().date()), str(t["tail"].max().date())] for k, t in p["tags"].items()}
    return {"model": final, "stats": p["full"], "trial": p["stats"], "metrics": metrics, "chosen": chosen,
            "epochs": epochs, "seed": seed, "cut": p["cut"], "counts": p["counts"], "spans": spans,
            "codes": p["codes"], "skipped": p["skipped"]}


# 표준화 통계 → JSON 목록
def numbers(stats: dict) -> dict:
    return {k: [float(v) for v in stats[k]] for k in ("mu", "sd", "ysd")}


# 메타 — 사양·버전, 구간(일봉·컷·구간별 [첫 기준일, 마지막 타깃일]), 종목 목록·해시, 윈도우/날짜 수, 시드·에폭, 통계, 개발 구간 지표
# validated는 항상 false — 최종 구간 판정을 본 사용자가 meta.json을 직접 고친다 (코드가 true를 쓰는 경로 없음)
def manifest(conf: dict, pack: dict, frames: dict, failed: list[dict]) -> dict:
    codes = pack["codes"]
    used = [frames[c] for c in codes]
    return {
        "version": VERSION,
        "spec": PROFILE,
        "validated": False,
        "created": datetime.now().isoformat(timespec="seconds"),
        "universe": _UNIVERSE,
        "data": {"since": conf["since"], "val_end": conf["val_end"],
                 "first": str(min(df.index[0] for df in used).date()),
                 "last": str(max(df.index[-1] for df in used).date())},
        "train_end": str(pack["cut"].date()),
        "spans": pack["spans"],
        "codes": codes,
        "count": len(codes),
        "codes_sha256": hashlib.sha256(",".join(codes).encode()).hexdigest(),
        "skipped": failed + pack["skipped"],
        "counts": pack["counts"],
        "seed": pack["seed"],
        "epochs": {"max": pack["epochs"], "chosen": pack["chosen"]},
        "stats": numbers(pack["stats"]),
        "stats_trial": numbers(pack["trial"]),
        "metrics": pack["metrics"],
        "holdout_start": HOLDOUT_START,
    }


# 가중치·메타 쓰기 — state_dict만 weights.pt로, 그 sha256을 메타에 넣어 meta.json (임시 파일 → 교체, 가중치 먼저·메타 나중)
# 서빙은 메타의 해시와 가중치가 다르면 읽지 않으므로 중간에 끊겨도 섞인 모델을 쓰지 않음, validated는 넘긴 값과 무관하게 false
def deposit(out, model, meta: dict) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    blob = buf.getvalue()
    meta = {**meta, "weights_sha256": hashlib.sha256(blob).hexdigest(), "validated": False}
    text = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
    for name, data in ((WEIGHTS, blob), (META, text)):
        tmp = out / f".{name}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, out / name)
    return meta


# 워크포워드 공용 학습 — offline.walk.rows()의 core 자리에 넣는 하네스 전용 호출 객체 (offline.run --retrain)
# 새 컷을 처음 볼 때 컷 번호가 0이거나 every개마다(every=0이면 첫 컷 한 번) 각 종목의 [frames 시작, 컷] 일봉으로 session() 재학습
# 사이 컷은 직전 모델로 추론만(projection), 학습은 컷당 한 번만 시도 — 실패하면 다음 재학습 컷까지 ValueError (rows()가 건너뜀 사유로 남김)
# 결과의 validated는 항상 false (검증 전 모델), trained = 학습 기록 (컷·검증 컷·종목 수·윈도우/날짜 수·에폭·검증 지표·초)
class Retrainer:
    def __init__(self, frames: dict, *, every: int = 0, seed: int = SEED, epochs: int = SPEC["epochs"],
                 log=None) -> None:
        if every < 0:
            raise ValueError(f"재학습 간격 오류: {every}")
        self.frames = {code: df.sort_index() for code, df in frames.items()}
        self.every, self.seed, self.epochs, self.log = every, seed, epochs, log
        self.cut: pd.Timestamp | None = None
        self.seen = 0
        self.bundle: dict | None = None
        self.fault: str | None = None
        self.trained: list[dict] = []

    # 컷 하나의 공용 학습 — 컷 뒤 행은 넘기지 않음 (종목별 워크포워드와 같은 경계: 컷 날짜 포함)
    def retraining(self, cut: pd.Timestamp) -> None:
        t0 = time.time()
        frames = {code: df[df.index <= cut] for code, df in self.frames.items()}
        frames = {code: df for code, df in frames.items() if len(df)}
        self.bundle, self.fault = None, None
        try:
            pack = session(frames, seed=self.seed, epochs=self.epochs)
        except Exception as e:
            self.fault = f"{type(e).__name__}: {e}"
            if self.log:
                self.log(f"[공용 학습] 컷 {cut.date()} 실패 — {self.fault}")
            return
        meta = {"version": VERSION, "validated": False, "metrics": pack["metrics"]}
        self.bundle = {"model": pack["model"], "stats": pack["stats"], "meta": meta}
        c, m = pack["counts"], pack["metrics"]
        self.trained.append({"cut": str(cut.date()), "train_end": str(pack["cut"].date()), "count": len(pack["codes"]),
                             "skipped": pack["skipped"], "counts": c, "epochs": {"max": pack["epochs"], "chosen": pack["chosen"]},
                             "metrics": m, "sec": round(time.time() - t0, 1)})
        if self.log:
            self.log(f"[공용 학습] 컷 {cut.date()} · 종목 {len(pack['codes'])} · 학습 윈도우 {c['train']['windows']:,} "
                     f"(날짜 {c['train']['dates']:,}) · 에폭 {pack['chosen']}/{pack['epochs']} · 검증 MAE {m['mae']} / "
                     f"0% {m['mae_zero']} / skill {m['skill']} · {time.time() - t0:.0f}초")

    def __call__(self, window: pd.DataFrame, seed: int = 0, label: str = "") -> dict:
        cut = pd.Timestamp(window.index[-1])
        if cut != self.cut:
            # 뒤로 가는 컷은 더 늦은 컷까지 본 모델로 추론하게 되므로 거부
            if self.cut is not None and cut < self.cut:
                raise ValueError(f"컷 순서 오류: {cut.date()} < {self.cut.date()}")
            self.cut = cut
            k, self.seen = self.seen, self.seen + 1
            if k == 0 or (self.every and k % self.every == 0):
                self.retraining(cut)
        if self.bundle is None:
            raise ValueError(f"공용 학습 실패 ({self.cut.date()}): {self.fault}")
        return projection(window, self.bundle, label)


# 저장된 공용 모델 평가 — artifact()로 읽은 모델로 추론만 하는 하네스 전용 호출 객체 (offline.run --pooled, 학습 없음)
# 읽을 수 없거나(없음·사양 불일치·손상) 메타에 일봉 끝(data.last)이 없으면 만들 때 ValueError
# 컷이 일봉 끝보다 앞이면 그 컷의 목표 기간이 학습 데이터 안이라 ValueError (rows()가 건너뜀 사유로 남김)
class Checkpoint:
    def __init__(self, folder) -> None:
        self.bundle = artifact(folder)
        if self.bundle is None:
            raise ValueError(f"공용 모델을 읽을 수 없음: {folder} (없음·사양 불일치·손상 — 경고 로그 참고)")
        self.meta = self.bundle["meta"]
        last = (self.meta.get("data") or {}).get("last")
        if not last:
            raise ValueError(f"메타에 일봉 끝(data.last)이 없음: {folder} — 학습 구간을 알 수 없어 평가하지 않음")
        self.last = pd.Timestamp(last)

    def __call__(self, window: pd.DataFrame, seed: int = 0, label: str = "") -> dict:
        cut = pd.Timestamp(window.index[-1])
        if cut < self.last:
            raise ValueError(f"학습 구간 안 컷: {cut.date()} < 모델 일봉 끝 {self.last.date()}")
        return projection(window, self.bundle, label)


# 명령행 인자 — --final은 두지 않는다
def options(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="offline.pool", description="공용 모델 오프라인 학습 (개발 구간 전용)")
    ap.add_argument("--codes", default="", help="쉼표로 구분한 종목 코드 (기본: NAMES에서 ETF 제외)")
    ap.add_argument("--since", default=SINCE, help=f"일봉 시작 (YYYY-MM-DD, 기본 {SINCE})")
    ap.add_argument("--train-end", default=None,
                    help="날짜 컷 — 학습 타깃이 이 날짜 전에 끝나고 검증은 이 날짜부터 (기본: 기준일 날짜의 뒤쪽 15%%)")
    ap.add_argument("--val-end", default=None, help=f"일봉 끝 (기본 {HOLDOUT_START} 전날, 그 이후는 읽지 않음)")
    ap.add_argument("--out", required=True, help="결과 폴더")
    ap.add_argument("--cache", default=str(CACHE), help="일봉 캐시 폴더")
    return ap.parse_args(argv)


# 사람이 읽는 요약 — 윈도우 수와 날짜 수를 함께 (같은 날짜의 종목은 함께 움직여 독립 관측은 날짜 수에 가까움)
def outline(conf: dict, pack: dict, failed: list[dict], elapsed: float) -> str:
    c, m = pack["counts"], pack["metrics"]
    lines = [
        f"공용 학습 — 개발 구간 ({HOLDOUT_START} 이전 행만 로드)",
        f"- 종목 {len(pack['codes'])} (조회 실패 {len(failed)} · 건너뜀 {len(pack['skipped'])}) · "
        f"일봉 {conf['since']} ~ {conf['val_end']} · 컷 {pack['cut'].date()}",
        f"- 학습 윈도우 {c['train']['windows']:,} · 날짜 {c['train']['dates']:,}",
        f"- 검증 윈도우 {c['val']['windows']:,} · 날짜 {c['val']['dates']:,}",
        f"- 전체 윈도우 {c['full']['windows']:,} · 날짜 {c['full']['dates']:,} (재학습)",
        "- 같은 날짜의 종목은 함께 움직이므로 독립 관측 수는 윈도우 수보다 날짜 수에 가깝다",
        f"- 에폭 {pack['chosen']}/{pack['epochs']} · 검증 MAE {m['mae']} / 0% 예측 {m['mae_zero']} / "
        f"skill {m['skill']} (n={m['n_val']}) · {elapsed:.0f}초",
    ]
    lines += [f"- 건너뜀 {s['code']}: {s['reason']}" for s in failed + pack["skipped"]]
    return "\n".join(lines)


# 실행 — reader(일봉 공급자)와 epochs(최대 에폭)는 테스트에서 바꿈
def main(argv: list[str] | None = None, *, reader=None, epochs: int = SPEC["epochs"]) -> int:
    a = options(argv)
    out = Path(a.out)
    if (out / META).exists() or (out / WEIGHTS).exists():
        print(f"이미 모델이 있음: {out} — 다른 --out을 주거나 직접 지운 뒤 다시 실행", file=sys.stderr)
        return 2
    codes = [c.strip().zfill(6) for c in a.codes.split(",") if c.strip()] or universe()
    limit = (pd.Timestamp(HOLDOUT_START) - pd.Timedelta(days=1)).date()
    val_end = min(pd.Timestamp(a.val_end).date(), limit) if a.val_end else limit
    conf = {"since": a.since, "train_end": a.train_end, "val_end": str(val_end), "cache": a.cache, "out": a.out}

    t0 = time.time()
    frames: dict[str, pd.DataFrame] = {}
    failed: list[dict] = []
    for n, code in enumerate(codes, 1):
        try:
            frames[code] = history(code, a.since, str(val_end), cache=a.cache, reader=reader, lookback=0)
        except Exception as e:
            failed.append({"code": code, "reason": f"{type(e).__name__}: {e}"})
        print(f"[{n}/{len(codes)}] {code} 일봉 {len(frames[code]) if code in frames else 0}행", flush=True)
    try:
        pack = session(frames, cut=a.train_end, epochs=epochs)
    except ValueError as e:
        print(f"학습 불가: {e}", file=sys.stderr)
        return 1
    deposit(out, pack["model"], manifest(conf, pack, frames, failed))
    print(outline(conf, pack, failed, time.time() - t0), flush=True)
    print(f"- 저장: {out / WEIGHTS} · {out / META} (validated false — 최종 구간 판정 뒤 사용자가 직접 고침)", flush=True)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
