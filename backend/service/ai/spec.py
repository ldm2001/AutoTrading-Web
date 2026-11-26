# 예측 모델 사양 — 종목별 학습(core)·공용 학습(offline.pool)·서빙 추론이 같은 값을 씀 (torch 없음)
import hashlib
import json

# 학습·예측 설정 (2단계 고정값 — 개발 구간 결과를 보고 바꾸지 않음, 3단계 공용 모델도 같은 값을 씀)
# seq 입력 일수 · horizon 예측 개장일 수 · val 뒤쪽 검증 비율(최소 valmin 윈도우) · decay = AdamW weight_decay
SPEC = {
    "seq": 20, "horizon": 5, "d_model": 32, "nhead": 4, "layers": 2, "ff": 128, "dropout": 0.1,
    "lr": 1e-3, "decay": 1e-2, "batch": 32, "epochs": 60, "patience": 8, "val": 0.15, "valmin": 20,
}
# 피처 열 — 가격 수준이 들어가지 않는 인과적 값만
# ret log(C/C₋₁) · range log(H/L) · body log(C/O) · ma5 C/MA5−1 · ma20 C/MA20−1 · vol log((V+1)/(MA20(V)+1)) · rsi RSI14/100−0.5
FEATURES = ("ret", "range", "body", "ma5", "ma20", "vol", "rsi")
# 이동평균이 채워지지 않는 앞 행 수 — 0으로 메우지 않고 버림
WARM = 20
# 식 개정 번호 — 피처·타깃·표준화·모델 구조의 식을 바꾸면 올림 (값이 같아도 저장된 가중치를 쓰지 않게)
REVISION = 1

# 사양 전체 — 공용 모델 메타에 그대로 저장하고, 서빙은 이것과 같을 때만 가중치를 씀
# target: 끝 행 종가 대비 1~horizon일 뒤 종가의 누적 로그수익률 · scale: 피처 z-score(학습 구간 통계), 타깃은 표준편차로만 나눔
PROFILE = {
    "revision": REVISION,
    "features": list(FEATURES),
    "warm": WARM,
    "target": "cumlogret",
    "scale": "zscore-x,sd-y",
    "head": "meanpool",
    **SPEC,
}


# 사양 → 버전 문자열 (키 순서와 무관, 값이 하나라도 다르면 다름)
def signature(profile: dict) -> str:
    blob = json.dumps(profile, sort_keys=True, ensure_ascii=False)
    return "v1-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


# 지금 코드의 사양 버전
VERSION = signature(PROFILE)
