# 공용 레이트리미터 — main(app.state.limiter)과 라우터가 동일 인스턴스 공유
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
