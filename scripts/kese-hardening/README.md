# KESE Hardening Scripts

KISA CII 취약점 분석 결과 기반 하드닝 스크립트.

## 적용된 수정사항 요약

### CRITICAL (코드에 직접 적용 완료)

| # | 항목 | 수정 내용 | 파일 |
|---|------|----------|------|
| 1 | WS-11,12 | API Key 인증 미들웨어 도입 | `backend/api/auth.py`, `backend/api/trade.py` |
| 2 | WS-32 | `.env` 파일 권한 600 적용 | `.env` |
| 3 | D-02 | Elasticsearch xpack.security 활성화 | `docker-compose.yml` |

### HIGH (코드에 직접 적용 완료)

| # | 항목 | 수정 내용 | 파일 |
|---|------|----------|------|
| 4 | WS-22 | 보안 헤더 미들웨어 (X-Frame, CSP, etc.) | `backend/main.py`, `frontend/nginx.conf` |
| 5 | WS-18 | Rate Limiting (slowapi, 주문 5/min, 봇 3/min) | `backend/api/trade.py` |
| 6 | WS-07 | 에러 메시지에서 내부 예외 정보 제거 | `backend/api/*.py` 전체 |
| 7 | CL-02 | Docker 비root 사용자, 네트워크 분리, 포트 제한 | `backend/Dockerfile`, `docker-compose.yml` |
| 8 | D-01 | Redis 인증 (requirepass) | `docker-compose.yml` |
| 9 | WS-36 | Grafana 기본 비밀번호 변경 | `docker-compose.yml` |

### MEDIUM (코드에 직접 적용 완료)

| # | 항목 | 수정 내용 | 파일 |
|---|------|----------|------|
| 10 | WS-10 | OrderRequest qty 범위 제한 (1~99999) | `backend/schema.py` |
| 11 | WS-04 | 종목코드 6자리 숫자 정규식 검증 | `backend/schema.py` |
| 12 | WS-25 | /docs, /redoc 프로덕션 비활성화 | `backend/main.py` |
| 13 | EXTRA-06 | BacktestRequest 파라미터 범위 제한 | `backend/api/backtest.py` |
| 14 | WS-20 | CORS allow_methods/headers 명시적 지정 | `backend/main.py` |
| 15 | EXTRA-01 | Nginx server_tokens off, 보안 헤더, rate limit | `frontend/nginx.conf` |
| 16 | WS-41 | /metrics 엔드포인트 Nginx에서 차단 | `frontend/nginx.conf` |

## 배포 후 수동 확인 사항

```bash
# 1. .env 파일 권한 확인
ls -la .env  # -rw------- 인지 확인

# 2. API Key 인증 테스트
curl http://localhost:8000/api/trading/buy \
  -X POST -H "Content-Type: application/json" \
  -d '{"code":"005930","qty":1}'
# -> 403 반환 확인

curl http://localhost:8000/api/trading/buy \
  -X POST -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"code":"005930","qty":1}'
# -> 정상 응답

# 3. /docs 비활성화 확인
curl http://localhost:8000/docs
# -> 404 반환 확인

# 4. 보안 헤더 확인
curl -I http://localhost:3001/
# -> X-Frame-Options: DENY, X-Content-Type-Options: nosniff 확인

# 5. Redis 인증 확인
redis-cli -p 6379 PING
# -> NOAUTH 에러 확인

# 6. Elasticsearch 인증 확인
curl http://localhost:9200/
# -> 접근 불가 또는 인증 요구 확인

# 7. Rate Limiting 테스트
for i in {1..10}; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST -H "X-API-Key: YOUR_KEY" \
    -H "Content-Type: application/json" \
    -d '{"code":"005930","qty":1}' \
    http://localhost:8000/api/trading/buy
done
# -> 5회 이후 429 반환 확인
```

## 프론트엔드 API Key 설정

브라우저 콘솔에서:
```javascript
localStorage.setItem('api_key', 'YOUR_API_KEY_HERE');
```

또는 향후 설정 UI에서 API Key 입력 기능 추가 예정.
