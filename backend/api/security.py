# 공통 보안 정책

# CORS/CSRF/WebSocket 허용 오리진 목록
ALLOWED_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
}

# CSRF 검증 대상 HTTP 메서드
MUTATING_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def originok(origin: str | None) -> bool:
    return not origin or origin in ALLOWED_ORIGINS


# CSRF 게이트 (mutating 전용) — Origin 있으면 허용 목록 검증,
# 없으면(비브라우저) X-API-Key 헤더 존재 필수 (값 검증은 guard 담당)
def csrfok(origin: str | None, key: str | None) -> bool:
    if origin:
        return origin in ALLOWED_ORIGINS
    return bool(key)
