# 통화 서비스 레이어
# - 외부 무료 API(exchangerate.host)에서 현재 환율과 3년 평균 계산
# - DB에 집계값 저장 (RateStat Document는 이 파일 내부에 정의)
# - 평균 대비 현재가 낮으면 구독자에게 이메일 발송

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict
import os
from pathlib import Path

import requests
from beanie import Document
from pydantic import Field
from beanie.operators import In
import pandas as pd # Alpha Vantage 응답 처리를 위해 필요
# from alpha_vantage.foreignexchange import ForeignExchange # Alpha Vantage 클라이언트 (더 이상 사용 안함)
import logging # 로깅 추가

# BigQuery 클라이언트 임포트
# google-cloud-bigquery는 Google Cloud BigQuery와 상호작용하기 위한 공식 Python 라이브러리입니다
from google.cloud import bigquery
from google.oauth2 import service_account
from google.auth.exceptions import DefaultCredentialsError

from ..core.config import settings
from ..models.notification import NotificationSetting
from ..models.user import User
import smtplib
from email.mime.text import MIMEText

# 로거 설정
logger = logging.getLogger(__name__)

# ---- 내부용 Document (요청된 디렉토리 구조를 유지하기 위해, 별도 models 파일을 추가하지 않고 여기 정의) ----
class RateStat(Document):
    base: str
    target: str
    current_rate: float
    avg_3y: float
    status: str  # "LOW" or "HIGH"
    calculated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "rate_stats"

# ---- 외부 API 호출 ----

def _api_latest(base: str, target: str) -> float:
    """
    exchangerate.host API에서 현재 환율을 조회하는 함수입니다.
    
    주니어 개발자님께:
    이 함수는 exchangerate.host API에 HTTP GET 요청을 보내서 최신 환율을 가져옵니다.
    API 키가 올바르게 설정되어 있어야 정상적으로 작동합니다.
    
    Args:
        base: 기준 통화 (예: "USD")
        target: 대상 통화 (예: "KRW")
    
    Returns:
        float: 현재 환율 값 (KRW/USD)
    """
    # exchangerate.host 엔드포인트 사용
    # 주니어 개발자님께: exchangerate.host API는 여러 버전이 있습니다
    # 무료 버전: API 키 없이 사용 가능하지만 일부 엔드포인트는 제한이 있을 수 있습니다
    # 유료 버전: API 키가 필요하며 더 많은 기능과 안정성을 제공합니다
    
    # 먼저 기본 /latest 엔드포인트 시도 (무료 버전)
    url = f"{settings.CURRENCY_API_BASE}/latest?base={base}&symbols={target}"
    
    logger.info(f"[exchangerate.host] Calling latest API: {url}")
    
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        # API 응답 확인
        # 주니어 개발자님께: exchangerate.host API는 success 필드로 성공 여부를 나타냅니다
        # 성공한 경우: {"success": true, "rates": {"KRW": 1350.5}, ...}
        # 실패한 경우: {"success": false, "error": {...}}
        
        # success가 False인 경우 처리
        if data.get('success') is False:
            error_info = data.get('error', {})
            error_code = error_info.get('code')
            error_msg = error_info.get('info', 'Unknown error')
            
            # API 키가 필요한 경우 (코드 101) - 다른 API 서비스 시도
            if error_code == 101:
                logger.warning(f"[exchangerate.host] API key required. Trying alternative API service...")
                # exchangerate-api.com 무료 서비스 시도
                alt_url = f"https://api.exchangerate-api.com/v4/latest/{base}"
                try:
                    alt_resp = requests.get(alt_url, timeout=10)
                    alt_resp.raise_for_status()
                    alt_data = alt_resp.json()
                    
                    if 'rates' in alt_data and target in alt_data['rates']:
                        rate = alt_data['rates'][target]
                        logger.info(f"[exchangerate-api.com]: Successfully fetched latest data for {base}/{target}. Rate: {rate:.4f}")
                        return float(rate)
                except Exception as alt_e:
                    logger.warning(f"[exchangerate-api.com] Alternative API also failed: {alt_e}")
            
            logger.error(f"[exchangerate.host] API error (code {error_code}): {error_msg}")
            logger.warning(f"[exchangerate.host] Returning dummy value due to API error.")
            return 1250.0
        
        # API 응답에서 환율 데이터 추출
        # 주니어 개발자님께: 성공한 경우 data['rates']는 {'SYMBOL': RATE} 형태입니다
        # 예: {"success": true, "rates": {"KRW": 1350.5}, ...}
        # success가 없는 경우도 있을 수 있으므로 (구버전 호환) 직접 확인합니다
        if 'rates' in data and target in data['rates']:
            rate = data['rates'][target]
            logger.info(f"[exchangerate.host]: Successfully fetched latest data for {base}/{target}. Rate: {rate:.4f}")
            return float(rate)
        else:
            logger.warning(f"[exchangerate.host] No latest rate data received for {base}/{target}. Response: {data}")
            logger.warning(f"[exchangerate.host] Returning dummy value.")
            return 1250.0
    
    except requests.exceptions.RequestException as e:
        logger.error(f"[exchangerate.host] Request failed: {e}", exc_info=True)
        logger.warning(f"[exchangerate.host] Returning dummy value due to request error.")
        return 1250.0
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[exchangerate.host] Error parsing response: {e}", exc_info=True)
        logger.warning(f"[exchangerate.host] Returning dummy value due to parsing error.")
        return 1250.0


def _api_timeseries_avg_3y(base: str, target: str) -> float:
    # exchangerate.host API를 사용하여 3년치 시계열 데이터를 가져오고 평균을 계산합니다.
    try:
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=3 * 365) # 3년치 데이터

        # exchangerate.host timeseries 엔드포인트 사용
        # 주니어 개발자님께: timeseries API는 특정 기간의 환율 데이터를 가져옵니다
        url = (
            f"{settings.CURRENCY_API_BASE}/timeseries?"
            f"start_date={start_date.strftime('%Y-%m-%d')}&"
            f"end_date={end_date.strftime('%Y-%m-%d')}&"
            f"base={base}&symbols={target}"
        )
        
        # API 키가 설정되어 있으면 파라미터에 추가
        if settings.CURRENCY_API_KEY and settings.CURRENCY_API_KEY != "your_exchangerate_api_key_here":
            url += f"&access_key={settings.CURRENCY_API_KEY}"
        
        logger.info(f"[exchangerate.host] Calling timeseries API: {url}") # print 대신 logger 사용
        resp = requests.get(url, timeout=10)
        resp.raise_for_status() # HTTP 에러 발생 시 예외 발생
        data = resp.json()

        # API 응답에서 환율 데이터 추출
        # data['rates']는 {'YYYY-MM-DD': {'SYMBOL': RATE}} 형태입니다.
        rates = []
        for date_str, rate_data in data.get('rates', {}).items():
            if target in rate_data:
                rates.append(rate_data[target])
        
        if not rates:
            # 시계열 데이터가 없을 경우 경고 로그를 남기고, 임시 더미 값을 반환합니다.
            # 실제 운영 환경에서는 적절한 오류 처리 (예: 예외 발생) 또는 폴백 로직이 필요합니다.
            logger.warning(f"[exchangerate.host] No historical data received for {base}/{target}. Check API response or query parameters. Returning dummy value.")
            return 1250.0
        
        df = pd.DataFrame(rates, columns=['rate'])
        avg_rate = df['rate'].astype(float).mean()

        logger.info(f"[exchangerate.host]: Successfully fetched historical data for {base}/{target}. Average: {avg_rate:.4f}") # print 대신 logger 사용
        return avg_rate

    except requests.exceptions.RequestException as e:
        # requests 라이브러리 관련 예외 처리 (네트워크 오류, HTTP 오류 등)
        logger.error(f"[exchangerate.host]: Network or HTTP error fetching data for {base}/{target}. Message: {e}", exc_info=True)
        return 1250.0
    except Exception as e:
        # 그 외 모든 예외 처리
        logger.error(f"[exchangerate.host]: Error fetching or processing data for {base}/{target}. Message: {e}", exc_info=True)
        return 1250.0

# ---- BigQuery 연동 ----

def _get_bigquery_client():
    """
    BigQuery 클라이언트를 생성하고 반환하는 함수입니다.
    
    주니어 개발자님께 설명:
    1. BigQuery 클라이언트는 Google Cloud BigQuery 서비스와 통신하기 위한 객체입니다.
    2. 인증은 GCP 서비스 계정 키 파일을 사용합니다.
    3. 환경변수 GOOGLE_APPLICATION_CREDENTIALS가 설정되어 있으면 자동으로 사용됩니다.
    4. 그렇지 않으면 설정 파일에서 지정한 경로의 키 파일을 사용합니다.
    
    Returns:
        bigquery.Client: BigQuery 클라이언트 객체
    """
    try:
        # 환경변수 GOOGLE_APPLICATION_CREDENTIALS 확인
        # 이 환경변수는 GCP SDK에서 자동으로 인식하는 표준 환경변수입니다
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        
        # 환경변수가 없으면 설정 파일에서 지정한 경로 사용
        if not credentials_path and settings.GCP_SERVICE_ACCOUNT_KEY_PATH:
            credentials_path = settings.GCP_SERVICE_ACCOUNT_KEY_PATH
            
            # 주니어 개발자님께: 상대 경로인 경우 프로젝트 루트 기준으로 변환합니다
            # .env 파일의 경로는 프로젝트 루트 기준으로 작성되어 있기 때문입니다
            if credentials_path and not os.path.isabs(credentials_path):
                # backend/app/services/currency_service.py에서 프로젝트 루트는 3단계 상위
                project_root = Path(__file__).parent.parent.parent.parent
                credentials_path = str(project_root / credentials_path.lstrip("./"))
                logger.info(f"[BigQuery] 키 파일 경로 변환: {credentials_path}")
        
        if credentials_path:
            # 서비스 계정 키 파일을 사용하여 인증 정보 로드
            # 주니어 개발자님께: service_account.Credentials.from_service_account_file()은
            # JSON 키 파일에서 인증 정보를 읽어와서 Credentials 객체를 생성합니다
            credentials = service_account.Credentials.from_service_account_file(
                credentials_path,
                scopes=["https://www.googleapis.com/auth/bigquery"]
            )
            # 프로젝트 ID는 키 파일에 포함되어 있지만, 명시적으로 지정할 수도 있습니다
            client = bigquery.Client(
                credentials=credentials,
                project=settings.BIGQUERY_PROJECT_ID or credentials.project_id
            )
            logger.info(f"[BigQuery] 클라이언트 생성 성공 (키 파일: {credentials_path})")
            return client
        else:
            # 환경변수나 설정 파일에 키 경로가 없으면 기본 인증 시도
            # 주니어 개발자님께: DefaultCredentialsError는 GCP SDK가 자동으로 인증 정보를 찾지 못할 때 발생합니다
            # 로컬에서는 보통 키 파일을 명시적으로 지정해야 합니다
            try:
                client = bigquery.Client(project=settings.BIGQUERY_PROJECT_ID)
                logger.info(f"[BigQuery] 클라이언트 생성 성공 (기본 인증 사용)")
                return client
            except DefaultCredentialsError:
                logger.error("[BigQuery] 인증 정보를 찾을 수 없습니다. GCP_SERVICE_ACCOUNT_KEY_PATH 또는 GOOGLE_APPLICATION_CREDENTIALS를 설정해주세요.")
                raise
    except Exception as e:
        logger.error(f"[BigQuery] 클라이언트 생성 실패: {e}", exc_info=True)
        raise

def _get_latest_3y_avg_from_bigquery() -> Optional[float]:
    """
    BigQuery에서 최신 3년 평균 환율을 조회하는 함수입니다.
    
    주니어 개발자님께 설명:
    1. Spark Job에서 계산한 3년 평균 환율이 BigQuery 테이블에 저장되어 있습니다.
    2. processed_timestamp 컬럼으로 정렬하여 가장 최근에 처리된 값을 가져옵니다.
    3. SQL 쿼리를 사용하여 데이터를 조회합니다.
    
    Returns:
        Optional[float]: 최신 3년 평균 환율 (데이터가 없으면 None)
    """
    # BigQuery 설정 확인
    if not settings.BIGQUERY_PROJECT_ID:
        logger.warning("[BigQuery] BIGQUERY_PROJECT_ID가 설정되지 않았습니다. BigQuery 조회를 건너뜁니다.")
        return None
    
    if not settings.BIGQUERY_DATASET or not settings.BIGQUERY_TABLE:
        logger.warning("[BigQuery] BIGQUERY_DATASET 또는 BIGQUERY_TABLE이 설정되지 않았습니다. BigQuery 조회를 건너뜁니다.")
        return None
    
    try:
        # BigQuery 클라이언트 생성
        client = _get_bigquery_client()
        
        # 테이블 전체 경로 구성
        # 주니어 개발자님께: BigQuery 테이블은 `프로젝트ID.데이터셋.테이블` 형식으로 지정합니다
        table_id = f"{settings.BIGQUERY_PROJECT_ID}.{settings.BIGQUERY_DATASET}.{settings.BIGQUERY_TABLE}"
        
        # SQL 쿼리 작성
        # 주니어 개발자님께: 이 쿼리는 가장 최근에 처리된 3년 평균 환율 1개를 가져옵니다
        # ORDER BY processed_timestamp DESC: 처리 시각 기준 내림차순 정렬 (최신 것 먼저)
        # LIMIT 1: 첫 번째 행만 가져옴
        query = f"""
        SELECT 
            three_year_avg_rate,
            processed_timestamp
        FROM `{table_id}`
        ORDER BY processed_timestamp DESC
        LIMIT 1
        """
        
        logger.info(f"[BigQuery] 쿼리 실행: {query}")
        
        # 쿼리 실행
        # 주니어 개발자님께: query() 메서드는 SQL 쿼리를 실행하고 결과를 반환합니다
        # 결과는 RowIterator 객체로 반환되며, for 루프로 순회할 수 있습니다
        query_job = client.query(query)
        results = query_job.result()
        
        # 결과가 있는지 확인하고 첫 번째 행 가져오기
        row = next(results, None)
        if row is None:
            logger.warning(f"[BigQuery] 테이블 {table_id}에 데이터가 없습니다.")
            return None
        
        # three_year_avg_rate 값 추출
        avg_rate = float(row.three_year_avg_rate)
        processed_time = row.processed_timestamp
        
        logger.info(f"[BigQuery] 최신 3년 평균 환율 조회 성공: {avg_rate:.4f} (처리 시각: {processed_time})")
        return avg_rate
        
    except Exception as e:
        # BigQuery 조회 실패 시 로그만 남기고 None 반환
        # 이렇게 하면 외부 API로 폴백할 수 있습니다
        logger.error(f"[BigQuery] 3년 평균 환율 조회 실패: {e}", exc_info=True)
        return None

# ---- 이메일 ----

def _send_email(to_email: str, subject: str, body: str):
    # 간단한 SMTP 발송 (Gmail 등)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to_email

    server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
    try:
        if settings.SMTP_TLS:
            server.starttls()
        if settings.SMTP_USER and settings.SMTP_PASSWORD:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_FROM, [to_email], msg.as_string())
    finally:
        server.quit()

# ---- 퍼블릭 서비스 API ----

async def compute_and_store(base: str | None = None, target: str | None = None) -> RateStat:
    base = base or settings.DEFAULT_BASE_CURRENCY
    target = target or settings.DEFAULT_TARGET_CURRENCY

    current = _api_latest(base, target)
    avg3 = _api_timeseries_avg_3y(base, target)
    status = "LOW" if current < avg3 else "HIGH"

    stat = RateStat(base=base, target=target, current_rate=current, avg_3y=avg3, status=status)
    await stat.insert()
    return stat

async def notify_if_low(stat: RateStat):
    if stat.status != "LOW":
        return

    # 알림 구독자 조회
    subs = await NotificationSetting.find(NotificationSetting.is_active == True).to_list()
    if not subs:
        return

    # 사용자 이메일 목록 추출
    user_ids = [s.user.id for s in subs if getattr(s, "user", None)]
    if not user_ids:
        return
    users = await User.find(In(User.id, user_ids)).to_list()

    subject = f"[FX Alert] {stat.base}/{stat.target} 현재 환율이 3년 평균보다 낮습니다"
    body = (
        f"기준통화: {stat.base}\n"
        f"대상통화: {stat.target}\n"
        f"현재 환율: {stat.current_rate:.4f}\n"
        f"3년 평균: {stat.avg_3y:.4f}\n"
        f"상태: {stat.status}\n"
        f"계산 시각(UTC): {stat.calculated_at.isoformat()}"
    )

    for u in users:
        _send_email(u.email, subject, body)

async def compute_store_and_notify(base: str | None = None, target: str | None = None) -> RateStat:
    stat = await compute_and_store(base, target)
    await notify_if_low(stat)
    return stat

async def get_latest_stat_or_live(base: str, target: str) -> dict:
    """
    현재 환율과 3년 평균 환율을 반환하는 함수입니다.
    
    주니어 개발자님께 설명:
    1. 현재 환율: 외부 API(exchangerate.host)에서 실시간으로 가져옵니다.
    2. 3년 평균 환율: BigQuery에서 Spark Job으로 계산된 최신 값을 가져옵니다.
    3. BigQuery에서 데이터를 가져오지 못하면 외부 API로 폴백합니다.
    
    Args:
        base: 기준 통화 (예: "USD")
        target: 대상 통화 (예: "KRW")
    
    Returns:
        dict: 환율 정보 (base, target, current_rate, avg_3y, status, last_updated, source)
    """
    # 1단계: 현재 환율을 외부 API에서 가져옵니다
    # 주니어 개발자님께: 현재 환율은 실시간으로 변동하므로 외부 API에서 가져와야 합니다
    logger.info(f"[CurrencyService] 현재 환율 조회 시작: {base}/{target}")
    current = _api_latest(base, target)
    
    # 2단계: 3년 평균 환율을 BigQuery에서 가져옵니다
    # 주니어 개발자님께: 3년 평균은 Spark Job에서 계산되어 BigQuery에 저장됩니다
    # 이렇게 하면 매번 3년치 데이터를 계산할 필요가 없어 성능이 향상됩니다
    logger.info("[CurrencyService] BigQuery에서 3년 평균 환율 조회 시도...")
    avg3 = _get_latest_3y_avg_from_bigquery()
    
    # 3단계: BigQuery에서 데이터를 가져오지 못한 경우 폴백 처리
    # 주니어 개발자님께: 폴백(fallback)은 주 데이터 소스가 실패했을 때 대체 방법을 사용하는 것입니다
    # 여기서는 BigQuery가 실패하면 외부 API를 사용하여 3년 평균을 계산합니다
    if avg3 is None:
        logger.warning("[CurrencyService] BigQuery에서 3년 평균을 가져오지 못했습니다. 외부 API로 폴백합니다.")
        avg3 = _api_timeseries_avg_3y(base, target)
        avg3_source = "fallback-api"  # 폴백 API 사용을 표시
    else:
        avg3_source = "bigquery"  # BigQuery 사용을 표시
    
    # 4단계: 현재 환율과 3년 평균을 비교하여 상태 결정
    # 주니어 개발자님께: 현재 환율이 3년 평균보다 낮으면 "LOW" (매수 좋은 시점)
    # 높으면 "HIGH" (상대적으로 비싼 시점)
    status = "LOW" if current < avg3 else "HIGH"
    
    logger.info(
        f"[CurrencyService] 환율 정보 조회 완료 - "
        f"현재: {current:.4f}, "
        f"3년평균: {avg3:.4f} ({avg3_source}), "
        f"상태: {status}"
    )
    
    return {
        "base": base,
        "target": target,
        "current_rate": current,
        "avg_3y": avg3,
        "status": status,
        "last_updated": datetime.utcnow().isoformat(),
        "source": f"current-api+avg-{avg3_source}"  # 데이터 소스 표시
    }
