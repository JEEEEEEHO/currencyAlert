# 커스텀 예외 클래스 정의
# 주니어 개발자님께: Python에서는 표준 예외(Exception)를 상속받아 
# 프로젝트에 특화된 예외를 만들 수 있습니다.
# 이렇게 하면 에러 처리가 더 명확하고 디버깅이 쉬워집니다.

class CurrencyServiceError(Exception):
    """환율 서비스 관련 기본 예외 클래스
    
    주니어 개발자님께: 모든 환율 관련 예외의 기본 클래스입니다.
    이렇게 하면 try-except 블록에서 특정 타입의 에러만 잡을 수 있습니다.
    """
    pass


class CurrencyAPIError(CurrencyServiceError):
    """외부 환율 API 호출 실패 시 발생하는 예외
    
    주니어 개발자님께: API 호출이 실패했을 때 발생하는 예외입니다.
    예를 들어, 네트워크 오류, 타임아웃, API 서버 오류 등을 포함합니다.
    
    Attributes:
        api_name: API 서비스 이름 (예: "exchangerate.host")
        status_code: HTTP 상태 코드 (있는 경우)
        message: 에러 메시지
    """
    def __init__(self, api_name: str, message: str, status_code: int = None):
        self.api_name = api_name
        self.status_code = status_code
        self.message = message
        super().__init__(f"[{api_name}] API 호출 실패: {message}")


class BigQueryError(CurrencyServiceError):
    """BigQuery 조회 실패 시 발생하는 예외
    
    주니어 개발자님께: BigQuery에서 데이터를 가져오지 못했을 때 발생합니다.
    예를 들어, 인증 실패, 쿼리 오류, 권한 없음 등을 포함합니다.
    
    Attributes:
        query: 실행한 SQL 쿼리 (있는 경우)
        message: 에러 메시지
    """
    def __init__(self, message: str, query: str = None):
        self.query = query
        self.message = message
        super().__init__(f"BigQuery 조회 실패: {message}")


class DataValidationError(CurrencyServiceError):
    """데이터 검증 실패 시 발생하는 예외
    
    주니어 개발자님께: 데이터가 예상한 형식이나 범위를 벗어났을 때 발생합니다.
    예를 들어, 환율이 음수이거나 비정상적으로 큰 값인 경우입니다.
    
    Attributes:
        field_name: 검증 실패한 필드 이름
        field_value: 검증 실패한 값
        message: 에러 메시지
    """
    def __init__(self, field_name: str, field_value: any, message: str):
        self.field_name = field_name
        self.field_value = field_value
        self.message = message
        super().__init__(f"데이터 검증 실패 [{field_name}]: {message}")








