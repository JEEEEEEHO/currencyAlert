# FastAPI 진입점
# - Beanie ODM 초기화 (MongoDB)
# - 라우터 라우팅
# - CORS 설정
# - Celery는 별도 프로세스로 동작 (tasks/currency_tasks.py 참고)

import asyncio
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from pydantic import BaseModel
import os

from .core.config import settings
from .models.user import User
from .models.notification import NotificationSetting
from .services.currency_service import RateStat # RateStat 모델 임포트
from .api.v1.auth import router as auth_router
from .api.v1.currency import router as currency_router

# FastAPI 애플리케이션 인스턴스 생성
app = FastAPI(
    title="환율 알림 서비스 API",
    description="3년 평균 대비 환율 변동 알림 서비스",
    version="1.0.0"
)

# CORS 허용 도메인 세팅
origins = [o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Beanie 초기화 (앱 시작 시 1회)
# 주니어 개발자님께: MongoDB 연결은 사용자 인증과 알림 기능에만 필요합니다.
# 환율 조회 API(/api/v1/currency/latest)는 MongoDB 없이도 작동합니다.
@app.on_event("startup")
async def app_init():
    try:
        # MongoDB 연결 시도
        # 주니어 개발자님께: AsyncIOMotorClient는 비동기 MongoDB 클라이언트입니다.
        # 연결 문자열은 mongodb://호스트:포트/데이터베이스 형식입니다.
        client = AsyncIOMotorClient(settings.MONGODB_URI, serverSelectionTimeoutMS=5000)
        # 주니어 개발자님께: serverSelectionTimeoutMS는 서버 선택 타임아웃(밀리초)입니다.
        # 5초 안에 연결하지 못하면 타임아웃 에러가 발생합니다.
        
        # 연결 테스트
        # 주니어 개발자님께: admin 명령을 실행하여 실제로 연결되는지 확인합니다.
        await client.admin.command('ping')
        
        db = client.get_default_database()
        await init_beanie(database=db, document_models=[User, NotificationSetting, RateStat])
        # 참고: RateStat(환율 통계) Document는 services/currency_service.py 내부에 정의됨.
        print(f"[INFO] MongoDB 연결 성공: {settings.MONGODB_URI}")
    except Exception as e:
        # MongoDB 연결 실패 시에도 서버는 시작됩니다
        # 주니어 개발자님께: 환율 조회 API는 MongoDB 없이도 작동하므로,
        # 연결 실패해도 서버를 시작할 수 있습니다. 다만 사용자 인증과 알림 기능은 사용할 수 없습니다.
        print(f"[WARNING] MongoDB 연결 실패: {e}")
        print("[INFO] 서버는 계속 시작됩니다. 환율 조회 API는 정상 작동하지만, 사용자 인증 및 알림 기능은 사용할 수 없습니다.")
        print("[INFO] MongoDB를 시작하려면: mongod (MongoDB 서비스 시작)")
        print(f"[INFO] 또는 MongoDB URI를 확인하세요: {settings.MONGODB_URI}")

# 간단한 헬스체크
@app.get("/")
async def root():
    return {"ok": True, "app": "fx-alert", "time": datetime.utcnow().isoformat()}

@app.get("/health") # Render 헬스 체크용 엔드포인트 추가
async def health_check():
    return {"status": "ok", "app": "fx-alert", "version": "1.0.0"}

# API v1 라우터 등록
app.include_router(auth_router, prefix="/api/v1")
app.include_router(currency_router, prefix="/api/v1")
