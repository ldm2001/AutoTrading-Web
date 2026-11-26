# 사전 등록 판정 기준 — 결과를 보기 전에 적은 값 그대로, 결과를 본 뒤 바꾸지 않는다
REGISTERED     = "2026-11-23"        # 기준을 적은 날 — 결과를 보기 전
HOLDOUT_START  = "2025-11-24"        # 이 날짜 이후는 --final 없이는 읽지 않는다
IC_T_MIN       = 2.0                 # 날짜별 rank IC 평균의 t값
IC_MIN_NAMES   = 10                  # 날짜당 최소 종목 수
SKILL_MIN      = 0.0                 # skill이 이 값보다 커야 한다
HIT_ABS_PCT    = 1.0                 # 적중률은 |예측 5일 수익률| > 1% 표본만
BUCKET_MIN_N   = 30                  # Direction 구간별 최소 건수
