# 설정 모듈
# - .env 값들을 한 곳에서 관리
# - 기본값을 제공하여 로컬 실행 편의성 확보

from pydantic_settings import BaseSettings, SettingsConfigDict
import os
from pathlib import Path
from typing import Optional
from pydantic import Field

# 프로젝트 루트 디렉토리 경로 찾기
# 주니어 개발자님께: 이 파일은 backend/app/core/config.py에 있으므로,
# backend 디렉토리에서 2단계 상위로 올라가면 프로젝트 루트가 됩니다.
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
ENV_FILE_PATH = PROJECT_ROOT / ".env"

class Settings(BaseSettings):
    APP_NAME: str = "fx-alert"
    ENV: str = "dev"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    MONGODB_URI: str = "mongodb://localhost:27017/fx_alert"

    JWT_SECRET_KEY: str = Field(..., description="JWT 토큰 서명에 사용되는 비밀키. 반드시 강력한 랜덤 문자열로 설정하세요.")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    CORS_ALLOW_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    DEFAULT_BASE_CURRENCY: str = "USD"
    DEFAULT_TARGET_CURRENCY: str = "KRW"
    # exchangerate.host API의 기본 URL입니다. 시계열 데이터를 포함한 모든 API 호출에 사용됩니다.
    CURRENCY_API_BASE: str = "https://api.exchangerate.host"
    # exchangerate.host API에 접근하기 위한 API 키입니다. .env 파일에 설정해야 합니다.
    CURRENCY_API_KEY: str

    REDIS_URL: str = "redis://localhost:6379/0"
    TIMEZONE: str = "Asia/Seoul"

    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "FX Alert <noreply@example.com>"
    SMTP_TLS: bool = True

    # 캐시 TTL 설정 (초 단위). 기본값 1시간 (3600초)
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", 3600))

    # exchangerate.host API Key
    EXCHANGERATE_API_KEY: Optional[str] = Field(None, env="EXCHANGERATE_API_KEY")

    # BigQuery 설정
    # GCP 프로젝트 ID입니다. BigQuery 데이터셋이 위치한 프로젝트를 지정합니다.
    BIGQUERY_PROJECT_ID: Optional[str] = Field(None, env="BIGQUERY_PROJECT_ID", description="BigQuery 프로젝트 ID")
    # BigQuery 데이터셋 이름입니다. Spark Job에서 사용한 데이터셋과 동일해야 합니다.
    BIGQUERY_DATASET: str = Field(default="fx_alert_data", env="BIGQUERY_DATASET", description="BigQuery 데이터셋 이름")
    # BigQuery 테이블 이름입니다. Spark Job에서 사용한 테이블명과 동일해야 합니다.
    BIGQUERY_TABLE: str = Field(default="usd_krw_daily_avg", env="BIGQUERY_TABLE", description="BigQuery 테이블 이름")
    # GCP 서비스 계정 키 파일 경로입니다. BigQuery 인증에 사용됩니다.
    # 로컬에서는 절대 경로나 상대 경로를 사용할 수 있습니다.
    # 환경변수 GOOGLE_APPLICATION_CREDENTIALS도 지원됩니다.
    GCP_SERVICE_ACCOUNT_KEY_PATH: Optional[str] = Field(None, env="GCP_SERVICE_ACCOUNT_KEY_PATH", description="GCP 서비스 계정 키 파일 경로")

    model_config = SettingsConfigDict(
        # 주니어 개발자님께: env_file에 절대 경로를 지정하면 backend 디렉토리에서 실행해도
        # 프로젝트 루트의 .env 파일을 찾을 수 있습니다.
        env_file=str(ENV_FILE_PATH) if ENV_FILE_PATH.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
