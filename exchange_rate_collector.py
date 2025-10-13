
import requests
import pandas as pd
import datetime
from io import StringIO
from google.cloud import storage
import sys

# --- 환경 설정 --- #
import os
# ECOS API 인증키를 환경변수에서 가져옵니다 (보안을 위해 하드코딩 제거)
API_KEY = os.getenv("ECOS_API_KEY")
if not API_KEY:
    raise ValueError("환경 변수 ECOS_API_KEY가 설정되지 않았습니다. .env 파일에 설정해주세요.")

# Google Cloud Storage 버킷 이름을 환경변수에서 가져옵니다
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "fx-alert-currency-data")
# USD/KRW 환율 통계표 코드. ECOS API 문서에서 '주요국 통화의 대원화 환율' 통계표 확인.
STAT_CODE = "731Y001"
# 검색 대상 아이템 코드 (USD에 해당). ECOS API 문서에서 통계표의 항목 코드 확인.
ITEM_CODE = "0000001" # USD (미국 달러)에 해당하는 항목 코드
START_ROW = 1
END_ROW = 2500                      # 3년치 일별이면 1000 초과 → 넉넉히

# --- 함수 정의 --- #
def get_exchange_rate_data(start_date: str, end_date: str) -> pd.DataFrame:
    """
    ECOS API를 통해 USD/KRW 환율 데이터를 조회합니다.

    Args:
        start_date (str): 조회 시작일 (YYYYMMDD 형식)
        end_date (str): YYYYMMDD 형식의 조회 종료일

    Returns:
        pd.DataFrame: 조회된 환율 데이터를 담은 pandas DataFrame. 컬럼: Date, Exchange_Rate
    """
    # API 요청 URL 구성. 한 번에 최대 1000건의 데이터를 가져올 수 있도록 설정 (ECOS API의 일반적인 제한)
    # 시작 인덱스(1)부터 요청 건수(1000)까지 데이터를 가져옵니다.
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/"
        f"{API_KEY}/json/kr/{START_ROW}/{END_ROW}/"
        f"{STAT_CODE}/D/{start_date}/{end_date}/{ITEM_CODE}"
    )
    try:
        # API 요청 보내기
        response = requests.get(url, timeout=20)
        response.raise_for_status() # HTTP 오류 발생 시 예외 발생
        
        # 응답 데이터를 JSON 형식으로 파싱
        data = response.json()

        # 'StatisticSearch' 키가 응답 데이터에 있는지 확인
        if 'StatisticSearch' in data:
            # 'row' 키에 실제 데이터가 담겨 있습니다.
            rows = data['StatisticSearch']['row']
            # pandas DataFrame으로 변환
            df = pd.DataFrame(rows)
            
            # 'DATA_VALUE' 컬럼을 숫자형으로 변환. 변환 중 오류 발생 시 NaN으로 처리
            df["DATA_VALUE"] = pd.to_numeric(df["DATA_VALUE"], errors="coerce")
            # 'TIME' 컬럼을 datetime 객체로 변환
            df["TIME"] = pd.to_datetime(df["TIME"], format="%Y%m%d")
            
            # 필요한 컬럼만 선택하고 컬럼 이름 변경
            df = df[["TIME", "DATA_VALUE"]].rename(columns={"TIME": "Date", "DATA_VALUE": "Exchange_Rate"})
            
            return df
        else:
            print("응답 데이터에 'StatisticSearch' 키가 없습니다. API 응답을 확인하세요.")
            print(data) # 디버깅을 위해 전체 응답 출력
            return pd.DataFrame()
            
    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP 오류 발생: {http_err}")
        print(f"응답 내용: {response.text}")
        return pd.DataFrame()
    except requests.exceptions.ConnectionError as conn_err:
        print(f"연결 오류 발생: {conn_err}")
        return pd.DataFrame()
    except requests.exceptions.Timeout as timeout_err:
        print(f"시간 초과 오류 발생: {timeout_err}")
        return pd.DataFrame()
    except requests.exceptions.RequestException as req_err:
        print(f"기타 요청 오류 발생: {req_err}")
        return pd.DataFrame()
    except Exception as e:
        print(f"데이터 조회 중 예상치 못한 오류 발생: {e}")
        return pd.DataFrame()

def upload_dataframe_to_gcs(df: pd.DataFrame, bucket_name: str, destination_blob_name: str):
    """
    Pandas DataFrame을 CSV 형식으로 GCS에 업로드합니다.

    Args:
        df (pd.DataFrame): 업로드할 DataFrame.
        bucket_name (str): GCS 버킷 이름.
        destination_blob_name (str): GCS에 저장될 파일 경로 및 이름 (예: 'raw_data/exchange_rates_2023.csv').
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)

    # DataFrame을 CSV 문자열로 변환 (메모리에서 처리)
    csv_buffer = StringIO()
    df.to_csv(csv_buffer, index=False)
    
    # GCS에 업로드
    blob.upload_from_string(csv_buffer.getvalue(), content_type='text/csv')
    print(f"DataFrame이 gs://{bucket_name}/{destination_blob_name} 에 성공적으로 업로드되었습니다.")

def collect_and_upload_exchange_rates(target_date_str: str):
    """
    ECOS API에서 환율 데이터를 수집하고 GCS에 업로드하는 전체 워크플로우를 실행합니다.

    Args:
        target_date_str (str): 처리할 데이터의 기준 날짜 (YYYYMMDD 형식).
    """
    target_date = datetime.datetime.strptime(target_date_str, '%Y%m%d').date()

    # 3년 전 날짜 계산
    three_years_ago = target_date - datetime.timedelta(days=3*365) # 윤년을 고려하지 않은 간략 계산

    start_date_str = three_years_ago.strftime('%Y%m%d')
    end_date_str = target_date.strftime('%Y%m%d')

    print(f"ECOS API에서 {start_date_str}부터 {end_date_str}까지의 USD/KRW 환율 데이터를 가져옵니다.")
    exchange_rates_df = get_exchange_rate_data(start_date_str, end_date_str)

    if not exchange_rates_df.empty:
        print("성공적으로 데이터를 가져왔습니다. 처음 5개 행:")
        print(exchange_rates_df.head())
        print("\n마지막 5개 행:")
        print(exchange_rates_df.tail())
        print(f"총 {len(exchange_rates_df)}개의 데이터 포인트.")

        # GCS에 데이터 업로드
        upload_dataframe_to_gcs(exchange_rates_df, GCS_BUCKET_NAME, f"raw_data/exchange_rates_{target_date_str}.csv")
    else:
        print("데이터를 가져오지 못했습니다.")

# --- 스크립트 실행 예시 (로컬 테스트용) --- #
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("사용법: python exchange_rate_collector.py <date_str>")
        sys.exit(-1)

    # 명령줄 인자로 받은 날짜를 사용 (YYYYMMDD 형식)
    collect_and_upload_exchange_rates(sys.argv[1])
