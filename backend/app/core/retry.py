# 재시도 로직 유틸리티
# 주니어 개발자님께: 외부 API 호출은 네트워크 오류나 일시적 서버 오류로 
# 실패할 수 있습니다. 이런 경우 몇 번 재시도하면 성공할 수 있습니다.
# tenacity 라이브러리를 사용하여 재시도 로직을 쉽게 구현할 수 있습니다.

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
    after_log,
)
import logging
from typing import Type, Tuple

from .exceptions import CurrencyAPIError

# 로거 설정
logger = logging.getLogger(__name__)


def create_api_retry_decorator(
    max_attempts: int = 3,
    initial_wait: float = 1.0,
    max_wait: float = 10.0,
    exceptions: Tuple[Type[Exception], ...] = (CurrencyAPIError, Exception)
):
    """
    외부 API 호출용 재시도 데코레이터를 생성하는 팩토리 함수입니다.
    
    주니어 개발자님께 설명:
    1. max_attempts: 최대 재시도 횟수 (예: 3이면 처음 1번 + 재시도 2번 = 총 3번 시도)
    2. initial_wait: 첫 재시도 전 대기 시간 (초). 지수 백오프의 시작 값입니다.
    3. max_wait: 최대 대기 시간 (초). 너무 오래 기다리지 않도록 제한합니다.
    4. exceptions: 재시도할 예외 타입. 이 예외가 발생하면 재시도합니다.
    
    지수 백오프(Exponential Backoff):
    - 1번째 실패 후: 1초 대기 (initial_wait)
    - 2번째 실패 후: 2초 대기 (1 * 2)
    - 3번째 실패 후: 4초 대기 (2 * 2)
    - 최대 10초까지만 (max_wait)
    
    이렇게 하면 서버에 부하를 주지 않으면서 재시도할 수 있습니다.
    
    사용 예시:
        @create_api_retry_decorator(max_attempts=3)
        def call_api():
            # API 호출 코드
            pass
    """
    return retry(
        # 재시도 중지 조건: 최대 시도 횟수에 도달하면 중지
        stop=stop_after_attempt(max_attempts),
        
        # 재시도 대기 전략: 지수 백오프 (exponential backoff)
        # wait_exponential은 매번 대기 시간을 2배로 늘립니다
        wait=wait_exponential(
            multiplier=2,  # 대기 시간 배수 (1초 → 2초 → 4초)
            min=initial_wait,  # 최소 대기 시간
            max=max_wait  # 최대 대기 시간
        ),
        
        # 재시도할 예외 타입: 지정한 예외가 발생하면 재시도
        retry=retry_if_exception_type(exceptions),
        
        # 재시도 전 로그 출력
        before_sleep=before_sleep_log(logger, logging.WARNING),
        
        # 재시도 후 로그 출력 (모든 재시도 실패 시)
        after=after_log(logger, logging.ERROR)
    )


# 기본 재시도 데코레이터 (바로 사용 가능)
# 주니어 개발자님께: 이 데코레이터를 함수에 붙이면 자동으로 재시도 로직이 적용됩니다.
api_retry = create_api_retry_decorator(
    max_attempts=3,
    initial_wait=1.0,
    max_wait=10.0
)

