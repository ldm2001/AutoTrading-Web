# 한국 주식 자동매매 시스템

---

## 1. 문제 정의

### 개인 투자자의 구조적 불리함

한국 주식 시장에서 개인 투자자는 다음과 같은 반복적인 문제에 직면

- **정보 비대칭**: 기관·외국인 대비 실시간 분석 능력 부재
- **감정적 판단**: 공포·탐욕으로 인한 손절 지연, 고점 매수
- **시간 제약**: 장중 모니터링 불가로 인한 기회 손실
- **분산 정보**: 차트, 뉴스, 재무지표가 흩어져 있어 종합 판단 어려움
- **일관성 없는 전략**: 매번 다른 기준으로 매매해 승률 측정 불가

### 핵심 질문

> 기술 지표 + AI 예측 + 뉴스 감성을 통합해 개인 투자자가 감정 없이 규칙 기반으로 매매할 수 있는 시스템을 만들 수 있는가?

---

## 2. 솔루션 개요

**AI 멀티팩터 전략 + Transformer 예측 + Gemini 분석 + SMC 구조 분석**을 결합한 자동매매 웹 애플리케이션

```
[실시간 시세 수신] → [asyncio.Queue 틱 파이프라인] → [15분/60분봉 자동 조립]
        ↓                                                    ↓
[9팩터 스코어링] ←── [15분 FVG 진입 트리거] + [Transformer 방향 필터]
        ↓                                                    ↓
[Gemini 뉴스 감성 분석]  ←→  [종합 신호 판단 (매수/관망/매도)]
        ↓
[자동 매수 실행] → [FVG 동적 손절/익절/장마감 청산] → [Discord 알림]
```

### 핵심 원칙

| 원칙 | 구현 방식 |
|------|----------|
| **규칙 기반 진입** | 스코어 ≥55점일 때만 매수, 임의 판단 배제 |
| **동적 리스크 관리** | FVG 구조적 지지선 기반 손절 + 고정 % 폴백, 익절 자동 집행 |
| **근거 투명화** | 팩터별 점수·이유 UI에 전부 표시 |
| **캐시 설계** | 주가 5초·일봉 5분·추천 10분·예측 1시간 TTL |

---

## 3. 주요 기능 정의

### 3-1. 실시간 시세 모니터링

- WebSocket 기반 10초 간격 전 종목 가격 브로드캐스트
- 상승 빨강 / 하락 파랑 per-point 색상 라인 차트 (lightweight-charts v5)
- KOSPI / KOSDAQ / 코스피200 지수 패널 실시간 갱신

### 3-2. AI 멀티팩터 추천 (2단계, 9팩터)

```
1단계 — 빠른 스크리닝 (전 종목, ~10초)
  RSI          최대 ±15점  과매도/과매수 판단
  MACD         최대 ±15점  골든크로스/데드크로스
  Bollinger    최대 ±10점  밴드 이탈/근접
  변동성 돌파    최대 ±12점  당일 목표가 돌파 여부
  FVG (일봉)   최대 ±8점   Fair Value Gap 근접도 (SMC)
  Order Block  최대 ±7점   기관 주문 집중 좌표 (SMC)
  FVG (15분봉) 최대 ±15점  분봉 FVG 진입 트리거
  구조 점수     최대 ±8점   BOS/CHoCH 추세 전환 감지
  → 상위 20개 후보 선정

2단계 — Transformer 방향 필터 (상위 20개, ~30초)
  방향성 필터   최대 ±10점  5일 예측이 매수 방향과 일치 시 가점
  → 최종 Top 10 반환 (10분 캐시)

합계 100점: 매수 ≥55 / 매도 ≤-40
```

### 3-3. Transformer 주가 예측

- PyTorch Transformer 인코더 기반 5일 후 종가 예측
- FinanceDataReader로 1년치 일봉 수집 후 온디바이스 학습
- 종목당 최초 10~30초 소요, 이후 1시간 캐시 적용

### 3-4. Gemini 종합 분석

- 기술지표 + 뉴스 감성을 Gemini에 전달해 종합 시그널 생성
- 종목별 최근 뉴스 긍정/중립/부정 감성 분류
- 당일 거래 요약 마켓 리포트 자동 생성

### 3-5. 실시간 틱 파이프라인

- `asyncio.Queue` producer/consumer 패턴으로 WebSocket 틱 논블로킹 처리
- 틱 데이터 → 15분봉/60분봉 자동 조립 (CandleStore)
- 장 마감 시 분봉 데이터 CSV 자동 저장 및 과거 데이터 로드 지원
- 큐 포화 시 가장 오래된 항목 자동 폐기 (백프레셔 처리)

### 3-6. 자동매매 봇

- 멀티팩터 전략 스코어 기반 자동 매수 진입
- **동적 손절**: FVG 구조적 지지선 하단을 손절가로 설정 (폴백: 고정 -3%)
- 익절(+5%) / 장마감 일괄매도 자동 집행
- Discord 웹훅으로 매수·매도·에러 알림 발송

### 3-7. 수동 주문 패널

- **실시간 호가창**: KIS API 매도/매수 10단계 호가 + 거래량 바 시각화 (3초 갱신)
- 호가 클릭 시 주문 가격 자동 입력
- 잔고 기반 10% / 25% / 50% / 100% 빠른 수량 입력
- 체결 내역 / 미체결 내역 탭 구분 표시
- KIS OpenAPI 연동 시장가 매수/매도 즉시 실행

### 3-8. 이벤트 기반 백테스터

- CandleStore에 축적된 15분봉 + FDR 일봉으로 전략 시뮬레이션
- 9팩터 스코어링 동일 로직 적용 (KIS API 호출 없음)
- **청산 우선순위**: FVG 동적 손절 → 고정 % 폴백 → 익절 → 보유 만기 트레일링
- 미래 참조 방지: 완성 봉(`candles[:i]`)만 사용, 다음 봉 시가에 진입
- 결과 지표: 누적수익률, 연환산, MDD, 승률, 손익비 + 거래 내역 테이블

### 3-9. 포트폴리오 섹터 히트맵

- 보유 종목을 섹터별로 그룹핑한 트리맵 시각화
- 셀 크기 = 투자 비중, 셀 색상 = 일일 수익률 (빨강=수익, 파랑=손실)
- FDR StockListing 기반 KOSPI/KOSDAQ 전 종목 섹터 매핑 (서버 기동 시 로드)
- 호버 시 섹터 내 개별 종목명 + 수익률 툴팁 표시

---

## 4. 데이터 및 기술 활용

### 데이터 흐름

```
[KIS OpenAPI]          실시간 주가, 일봉, 분봉, 잔고, 주문
[FinanceDataReader]    1년치 일봉 (Transformer 학습용)
[yfinance]             보조 가격 데이터 (FDR 실패 시 폴백)
[뉴스 크롤링]          종목별 최신 뉴스 헤드라인
[Google Gemini]        AI 분석·감성·리포트 생성
[CandleStore]          틱 → 15분/60분봉 조립 + CSV 파일 적재
```

### 기술 스택

| 레이어 | 기술 |
|--------|------|
| **백엔드** | Python 3.11 / FastAPI / asyncio / httpx |
| **AI 예측** | PyTorch — Transformer 인코더 + PositionalEncoding |
| **AI 분석** | Google Gemini API — 자연어 분석·리포트 |
| **기술지표** | RSI / MACD / 볼린저밴드 — NumPy 벡터 연산 |
| **SMC** | FVG(일봉+15분봉) / Order Block / BOS·CHoCH — 오버나잇 갭 필터 + 구조적 손절 |
| **틱 파이프라인** | asyncio.Queue producer/consumer → CandleStore 15분/60분봉 조립 |
| **백테스터** | BacktestScorer — Scorer 팩터 메서드 직접 호출, FDR 일봉 + CandleStore 15분봉 |
| **프론트엔드** | SvelteKit + TypeScript / lightweight-charts v5 |
| **실시간** | WebSocket (FastAPI) ↔ Svelte 스토어 |
| **브로커** | 한국투자증권 KIS OpenAPI (실거래 / 모의투자) |

### 핵심 설계 결정

- **OOP 구조**: `KIS`, `Scorer`, `Predictor`, `AIPipeline`, `CandleStore`, `TickQueue` 클래스 분리
- **TTL 캐시**: 레이어별 다른 TTL로 API 호출 최소화 (5초~1시간)
- **async 전용**: 모든 I/O 비동기 처리, `asyncio.gather`로 병렬화
- **타임프레임 앙상블**: Transformer 5일 예측 = 방향 필터, 15분 FVG = 진입 트리거
- **동적 손절**: FVG 구조적 지지선 기반 (폴백: 고정 %) — 고정 비율 대비 리스크 정밀화
- **Semaphore 제한**: 스크리닝 5개 / Transformer 2개 동시 실행 제한
- **모의투자 지원**: `KIS_MOCK=true` 한 줄로 실거래↔모의 전환

---

## 5. 시나리오

### 시나리오 A — 아침 출근 전 자동 매매 시작

```
08:55  봇 시작 (POST /api/trading/bot/start)
       → 전 종목 멀티팩터 스크리닝 시작

09:05  삼성전자 스코어 +67점 (매수 기준 55점 초과)
       → 예수금의 30% 자동 매수 체결
       → Discord 알림: "삼성전자 67점 매수 100주 @93,000"

11:30  SKC 스코어 +12점 → 관망, 매수 스킵

15:00  삼성전자 +5.2% 익절 조건 충족
       → 자동 매도 체결
       → Discord 알림: "삼성전자 익절 +5.2% 매도"

15:20  장 마감 전 잔여 보유 종목 일괄 청산
```

### 시나리오 B — 종목 분석 후 수동 매수

```
1. StockTable에서 에코프로비엠 클릭
2. 추천 팝업 → AI예측 +8.3% 상승 표시
3. AI 분석 탭 → Gemini "기술적 매수 구간, 긍정 뉴스 3건"
4. 주문 패널 → 목표가 계산기로 수익/손실 시뮬레이션
5. 매수 실행 → KIS API 체결 확인
```

### 시나리오 C — 리포트 확인

```
장 마감 후 /api/ai/report 호출
→ Gemini가 당일 거래 요약 + 시장 분위기 + 다음날 전략 제안 생성
→ 대시보드 Daily Report 패널에 표시
```

---

## 6. 기대 효과 및 추후 확장성

### 기대 효과

| 효과 | 설명 |
|------|------|
| **감정 배제** | 스코어 기반 규칙 매매로 공포·탐욕 제거 |
| **기회 포착** | 장중 상시 모니터링으로 인간 대비 빠른 진입 |
| **리스크 자동화** | 손절·익절 자동 집행으로 손실 제한 |
| **전략 학습** | 팩터별 점수 로그로 전략 성과 분석 가능 |
| **시간 절약** | 장중 모니터링 없이 백그라운드 자동 운용 |

### 추후 확장성

**단기 (보강)**
- ~~백테스팅 모드: 과거 데이터로 전략 수익률 검증~~ ✅ 이벤트 기반 백테스터 구현 완료
- ~~포트폴리오 히트맵: 섹터별 수익률 시각화~~ ✅ 섹터 트리맵 히트맵 구현 완료
- 텔레그램 연동: 모바일 알림 + 원격 봇 제어

**중기 (고도화)**
- ~~타임프레임 앙상블~~ ✅ 적용 완료
- ~~동적 손절/익절 비율 조정~~ ✅ FVG 구조적 손절 적용 완료
- 재무제표 팩터 추가 (PER/PBR/ROE 스코어링)
- 강화학습 기반 포지션 사이징 최적화

**장기 (플랫폼화)**
- 멀티 계좌 / 멀티 전략 동시 운용
- 전략 마켓플레이스: 팩터 조합 공유
- 해외 주식 확장: 미국 주식 (Alpaca/Interactive Brokers)

---

### 실행

```bash
# 백엔드
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 프론트엔드
cd frontend
npm install
npm run dev
```

---

## API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/stocks` | 전체 종목 목록 |
| GET | `/api/stocks/{code}/price` | 실시간 현재가 |
| GET | `/api/stocks/{code}/daily` | 일봉 캔들 (최대 60일) |
| GET | `/api/stocks/recommend` | AI 추천 Top 10 (예측 배지 포함) |
| GET | `/api/stocks/{code}/orderbook` | 실시간 10단계 호가창 |
| GET | `/api/predict/{code}` | Transformer 5일 예측 |
| GET | `/api/ai/signal/{code}` | AI 기술지표 + 뉴스 종합 분석 |
| GET | `/api/ai/news/{code}` | 뉴스 감성 분석 |
| GET | `/api/ai/report` | 일일 마켓 리포트 |
| POST | `/api/trading/buy` | 매수 주문 |
| POST | `/api/trading/sell` | 매도 주문 |
| POST | `/api/trading/bot/start` | 자동매매 봇 시작 |
| POST | `/api/trading/bot/stop` | 자동매매 봇 중지 |
| GET | `/api/trading/portfolio` | 보유 종목 + 평가금액 |
| GET | `/api/trading/portfolio/heatmap` | 섹터별 히트맵 데이터 |
| POST | `/api/backtest` | 백테스트 실행 (code, days, take_profit_pct, max_hold_bars) |
| WS | `/ws/prices` | 실시간 가격 스트림 |
| WS | `/ws/trades` | 거래 이벤트 스트림 |

## 변경 이력

### 2026-03-01
- **이벤트 기반 백테스터**: CandleStore 15분봉 + FDR 일봉으로 9팩터 전략 시뮬레이션, 미래 참조 방지, 누적수익률/MDD/승률/손익비 산출
- **포트폴리오 섹터 히트맵**: 보유 종목 섹터별 트리맵 (비중=크기, 수익률=색상), FDR KOSPI/KOSDAQ 섹터 자동 매핑
- **섹터 매핑 서비스**: `load_sectors()` 서버 기동 시 전 종목 섹터 캐시 구축

### 2026-02-26
- **9팩터 스코어링 개편**: 기존 7팩터 → 9팩터 (FVG 15분봉 ±15점, 구조 점수 ±8점 추가), 가중치 재조정
- **틱 파이프라인 신규**: `asyncio.Queue` producer/consumer로 WebSocket 틱 논블로킹 처리, `CandleStore`가 15분/60분봉 자동 조립 + CSV 저장
- **동적 손절**: 고정 -3% → FVG 구조적 지지선 하단 기반 손절 (폴백: 고정 %)
- **타임프레임 앙상블**: Transformer 5일 예측을 방향 필터로 전환, 15분 FVG를 진입 트리거로 활용
- **CHoCH 로직 버그 수정**: BOS/CHoCH 구조 전환 감지 조건 모순 해소

### 2026-02-25
- **주문 패널 전면 재설계**: 호가창(매도·매수 10단계) + 주문 폼 + 체결내역 탭으로 레이아웃 개편
- **KIS 호가 API 연동**: `GET /api/stocks/{code}/orderbook` — TR `FHKST01010200`, 3초 TTL 캐시
- **SMC 지표 추가**: FVG(±8점) · Order Block(±7점) 팩터를 멀티팩터 스코어에 통합 (7팩터 100점)
- **AI 추천 2단계 고도화**: 상위 20개 후보에 Transformer 예측 적용 후 재평가 → Top 10 반환, 목록에 예측 변동률 배지(+3.8%↑) 표시
- **NumPy 도입**: `indicators.py` RSI(Wilder 스무딩) · MACD · 볼린저밴드 전부 벡터 연산으로 전환
- **WebSocket 메모리 누수 수정**: 핸들러 Map→Set 변경, `on()` cleanup 함수 반환, ping 타이머 `close()` 시 정리

---

## Preview
<img width="1728" height="961" alt="메인 템플릿" src="https://github.com/user-attachments/assets/2380c33c-51b7-4a50-ae57-262786dbd619" />
<img width="1728" height="961" alt="주식 주문" src="https://github.com/user-attachments/assets/1a52989c-817a-4851-b2a4-b5b4ec0fce4f" />
<img width="1728" height="961" alt="AI 종목 추천" src="https://github.com/user-attachments/assets/7916ed1a-d1f7-4511-ac11-dd7c19284acf" />
<img width="1728" height="961" alt="주가 5일 예측" src="https://github.com/user-attachments/assets/c08f3d2c-4ca9-4253-b8f4-32fe0643191f" />
<img width="1728" height="961" alt="뉴스" src="https://github.com/user-attachments/assets/0be7abff-543f-4795-acc2-b509eb0f4cf1" />
<img width="1728" height="961" alt="일일 오픈 마켓" src="https://github.com/user-attachments/assets/be466411-359e-404c-b941-2b33ffed2795" />

---


