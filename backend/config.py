# 환경변수 및 전략 파라미터 설정
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_env_file = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    app_key: str
    app_secret: str
    cano: str
    acnt_prdt_cd: str = "01"
    discord_webhook_url: str = ""
    url_base: str = "https://openapi.koreainvestment.com:9443"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    symbol_list: list[str] = ["005930", "373220", "035720", "000660"]
    target_buy_count: int = 3
    buy_percent: float = 0.33

    # 전략 파라미터
    stop_loss_pct: float = -3.0       # 손절 라인 (%)
    take_profit_pct: float = 5.0      # 익절 라인 (%)
    buy_score_threshold: int = 55     # 매수 시그널 최소 스코어
    use_prediction: bool = False      # Transformer 예측 연동 (느림, 선택)

    model_config = SettingsConfigDict(
        env_file=str(_env_file),
        env_file_encoding="utf-8",
    )


settings = Settings()
