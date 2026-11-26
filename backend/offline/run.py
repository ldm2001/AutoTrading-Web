# 오프라인 평가 CLI
# cd backend && venv/bin/python -m offline.run --codes 005930,000660 --start 2025-05-01 --end 2025-11-21 --out <폴더>
# 공용 모델: --retrain N(워크포워드 재학습, 0이면 첫 컷 한 번) 또는 --pooled <폴더>(저장 모델 추론만) — 결과 형식은 종목별과 같음
# 결과: <out>/rows.csv(결과 표), metrics.json(지표·판정·model), summary.md(요약 — 첫 줄에 개발/최종 구간)
import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path

import pandas as pd

from offline import rules
from offline.daily import CACHE, MARKER, history, marker
from offline.metrics import BUCKETS, judgment, stats
from offline.pool import SINCE, Checkpoint, Retrainer
from offline.walk import HORIZON, rows
from service.ai.spec import SPEC


# 명령행 인자
def options(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="offline.run", description="예측 워크포워드 평가 (배포 core() 그대로)")
    ap.add_argument("--codes", required=True, help="쉼표로 구분한 종목 코드")
    ap.add_argument("--start", required=True, help="평가 구간 시작 (YYYY-MM-DD)")
    ap.add_argument("--end", required=True, help="평가 구간 끝 (YYYY-MM-DD, 목표 5거래일도 이 안에서)")
    ap.add_argument("--out", required=True, help="결과 폴더")
    ap.add_argument("--step", type=int, default=HORIZON, help="컷 간격 거래일 (기본 5, 1이면 주입용 표·판정 무효)")
    ap.add_argument("--cache", default=str(CACHE), help="일봉 캐시 폴더")
    ap.add_argument("--marker", default=str(MARKER), help="최종 구간 사용 표식 파일")
    ap.add_argument("--final", action="store_true", help="최종 구간 포함 (표식이 있으면 거부, 한 번만)")
    ap.add_argument("--pooled", default=None, help="저장된 공용 모델 폴더 평가 (추론만, 모델 일봉 끝 이후 컷만)")
    ap.add_argument("--retrain", type=int, default=None,
                    help="공용 모델 워크포워드 — 컷 N개마다 재학습 (0이면 첫 컷에서 한 번), --final과 함께 쓰지 않음")
    ap.add_argument("--since", default=None, help=f"--retrain 학습 일봉 시작 (YYYY-MM-DD, 기본 {SINCE})")
    return ap.parse_args(argv)


# 모델 인자 오류 문구 (없으면 None) — 표식·데이터보다 먼저 봄
# 최종 구간 판정은 저장 모델(--pooled)로 한다: 사용자가 validated를 켜는 대상이 평가한 바로 그 가중치여야 함
def misuse(a: argparse.Namespace) -> str | None:
    if a.pooled and a.retrain is not None:
        return "--pooled와 --retrain은 함께 쓰지 않음"
    if a.retrain is not None and a.retrain < 0:
        return f"--retrain은 0 이상: {a.retrain}"
    if a.retrain is not None and a.final:
        return "--retrain은 최종 구간에 쓰지 않음 — 최종 판정은 저장 모델(--pooled)로 한 번"
    if a.since and a.retrain is None:
        return "--since는 --retrain과 함께만 씀"
    if a.retrain is not None and pd.Timestamp(a.since or SINCE) >= pd.Timestamp(a.start):
        return f"--since({a.since or SINCE})는 --start({a.start})보다 앞이어야 함"
    return None


# 요약의 모델 줄
def caption(model: dict) -> str:
    if model["kind"] == "per_symbol":
        return "종목별 즉석 학습 (컷마다 core())"
    if model["mode"] == "retrain":
        every = "첫 컷에서 한 번" if model["retrain"] == 0 else f"컷 {model['retrain']}개마다"
        return (f"공용 워크포워드 ({every} 재학습, 일봉 {model['since']}부터) · "
                f"학습 {len(model.get('trained') or [])}회 · validated false")
    data = model.get("data") or {}
    return (f"공용 저장 모델 {model['folder']} · {model.get('version')} · 컷 {model.get('train_end')} · "
            f"일봉 끝 {data.get('last')} · 종목 {model.get('count')} · "
            f"validated {'true' if model.get('validated') else 'false'}")


# 소수 표기 (None → -)
def num(v, fmt: str = "{:.4f}") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return fmt.format(v)


# 사람이 읽는 요약 (첫 줄 = 개발/최종 구간)
def digest(conf: dict, st: dict, verdict: dict, skipped: list[dict], elapsed: float, model: dict | None = None) -> str:
    scope = "최종 구간" if conf["final"] else "개발 구간"
    guard = "HOLDOUT 포함" if conf["final"] else f"{rules.HOLDOUT_START} 이전 행만 로드"
    h, m, i = st["hits"], st["mae"], st["ic"]
    lines = [
        f"{scope} — {conf['start']} ~ {conf['end']} ({guard})",
        "",
        "# 예측 워크포워드 평가",
        "",
        f"- 기준 등록일 {rules.REGISTERED} · 종목 {','.join(conf['codes'])} · 컷 간격 {conf['step']}거래일 · "
        f"표 {st['rows']}행 · 건너뜀 {len(skipped)} · {elapsed:.0f}초",
        f"- 모델: {caption(model or conf.get('model') or {'kind': 'per_symbol'})}",
        "",
        "## 지표",
        "",
        "| 항목 | 값 | 기준 | 결과 |",
        "|---|---|---|---|",
        f"| rank IC 평균 / 표준편차 / t | {num(i['mean'])} / {num(i['std'])} / {num(i['t'], '{:.2f}')} "
        f"(날짜 {i['dates']}, 제외 {i['excluded']}) | t ≥ {rules.IC_T_MIN} | "
        f"{'통과' if verdict['checks']['ic'] else '실패'} — {verdict['notes']['ic']} |",
        f"| MAE 모델 / 0% 예측 / skill | {num(m['mae_model'])} / {num(m['mae_zero'])} / {num(m['skill'])} "
        f"(n={m['n']}) | skill > {rules.SKILL_MIN} | {'통과' if verdict['checks']['skill'] else '실패'} |",
        f"| 적중률 모델 [95%] | {num(h['model']['rate'], '{:.3f}')} [{num(h['model']['lo'], '{:.3f}')}, "
        f"{num(h['model']['hi'], '{:.3f}')}] (n={h['n']}) | 항상 상승·모멘텀보다 높음 | "
        f"{'통과' if verdict['checks']['hit'] else '실패'} — {verdict['notes']['hit']} |",
        f"| 적중률 항상 상승 [95%] | {num(h['up']['rate'], '{:.3f}')} [{num(h['up']['lo'], '{:.3f}')}, "
        f"{num(h['up']['hi'], '{:.3f}')}] | 비교 기준 | |",
        f"| 적중률 5일 모멘텀 [95%] | {num(h['momentum']['rate'], '{:.3f}')} [{num(h['momentum']['lo'], '{:.3f}')}, "
        f"{num(h['momentum']['hi'], '{:.3f}')}] | 비교 기준 | |",
        f"| Direction 구간 | 건수 ≥ {rules.BUCKET_MIN_N} 구간 | 점수 순 증가 | "
        f"{'통과' if verdict['checks']['bucket'] else '실패'} — {verdict['notes']['bucket']} |",
        "",
        "## Direction 구간별 실제 5일 수익률",
        "",
        "| 점수 | 건수 | 평균 % |",
        "|---|---|---|",
    ]
    lines += [f"| {b['score']:+d} | {b['n']} | {num(b['mean_ret'], '{:+.3f}')} |" for b in st["buckets"]]
    lines += ["", "## 판정", "", f"**{verdict['message']}**"]
    if "overlap" in verdict["notes"]:
        lines.append(f"- {verdict['notes']['overlap']}")
    return "\n".join(lines) + "\n"


# 실행 — reader(일봉 공급자)·core(학습)·epochs(공용 재학습 최대 에폭)는 테스트에서 바꿈
def main(argv: list[str] | None = None, *, reader=None, core=None, epochs: int = SPEC["epochs"]) -> int:
    a = options(argv)
    codes = [c.strip().zfill(6) for c in a.codes.split(",") if c.strip()]
    conf = {"codes": codes, "start": a.start, "end": a.end, "step": a.step, "final": a.final,
            "cache": a.cache, "out": a.out, "holdout_start": rules.HOLDOUT_START}
    fault = misuse(a)
    if fault:
        print(fault, file=sys.stderr)
        return 2
    # 모델 — 저장 모델은 표식을 남기기 전에 읽어 둠 (읽기 실패로 최종 구간 한 번을 날리지 않게)
    model: dict = {"kind": "per_symbol", "mode": "walk"}
    checkpoint = None
    if a.pooled:
        try:
            checkpoint = Checkpoint(a.pooled)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        meta = checkpoint.meta
        model = {"kind": "pooled", "mode": "checkpoint", "folder": a.pooled, "version": meta.get("version"),
                 "train_end": meta.get("train_end"), "data": meta.get("data"), "count": meta.get("count"),
                 "codes_sha256": meta.get("codes_sha256"), "weights_sha256": meta.get("weights_sha256"),
                 "validated": meta.get("validated") is True}
    elif a.retrain is not None:
        model = {"kind": "pooled", "mode": "retrain", "retrain": a.retrain, "since": a.since or SINCE}
    conf["model"] = model
    # 최종 구간 — 표식부터 남기고(이미 있으면 거부) 그다음에 읽음
    if a.final:
        try:
            marker(a.marker, conf)
        except FileExistsError as e:
            print(e, file=sys.stderr)
            return 2
    elif pd.Timestamp(a.end) >= pd.Timestamp(rules.HOLDOUT_START):
        print(f"--final 없음: {rules.HOLDOUT_START} 이후 행은 읽지 않음", file=sys.stderr)

    t0 = time.time()
    log = lambda s: print(s, flush=True)
    if a.retrain is not None:
        # 공용 재학습 — --since부터 (학습 창 365일을 앞에 붙이지 않음)
        frames = {c: history(c, model["since"], a.end, cache=a.cache, final=a.final, reader=reader, lookback=0)
                  for c in codes}
        core = Retrainer(frames, every=a.retrain, epochs=epochs, log=log)
    else:
        frames = {c: history(c, a.start, a.end, cache=a.cache, final=a.final, reader=reader) for c in codes}
        core = checkpoint or core
    table, skipped = rows(frames, a.start, a.end, step=a.step, core=core, log=log)
    if a.retrain is not None:
        model = {**model, "trained": core.trained}
    st = stats(table)
    verdict = judgment(st, overlap=a.step < HORIZON)
    elapsed = time.time() - t0

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(table, columns=list(table[0].keys()) if table else None).to_csv(out / "rows.csv", index=False)
    report = {"scope": "final" if a.final else "dev", "config": conf,
              "rules": {k: getattr(rules, k) for k in ("REGISTERED", "HOLDOUT_START", "IC_T_MIN", "IC_MIN_NAMES",
                                                       "SKILL_MIN", "HIT_ABS_PCT", "BUCKET_MIN_N")},
              "buckets": list(BUCKETS), "stats": st, "verdict": verdict, "skipped": skipped,
              "elapsed_sec": round(elapsed, 1), "model": model}
    (out / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "summary.md").write_text(digest(conf, st, verdict, skipped, elapsed, model), encoding="utf-8")
    print(verdict["message"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main())
