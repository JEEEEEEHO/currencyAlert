import sys
from pyspark.sql import SparkSession
from pyspark.sql.functions import avg, lit, current_timestamp
from pyspark.sql.types import DoubleType, DateType
from datetime import datetime
import os # os 모듈 import 추가

def process_exchange_rate_data(date_str: str):
    """
    GCS에서 환율 데이터를 읽어 변환하고 BigQuery에 저장하는 Spark Job.

    Args:
        date_str (str): 처리할 데이터의 날짜 문자열 (YYYYMMDD 형식).
    """
    # GCP 서비스 계정 키 파일 경로를 환경 변수에서 가져옵니다.
    # Airflow DAG에서 이 환경 변수를 설정할 것입니다.
    gcp_key_path = os.environ.get("GCP_SERVICE_ACCOUNT_KEY_PATH")
    if not gcp_key_path:
        raise ValueError("환경 변수 GCP_SERVICE_ACCOUNT_KEY_PATH가 설정되지 않았습니다.")

    # BigQuery 프로젝트 ID를 환경 변수에서 가져옵니다.
    BIGQUERY_PROJECT_ID = os.environ.get("BIGQUERY_PROJECT_ID", "fx-alert-currency")
    if not BIGQUERY_PROJECT_ID:
        raise ValueError("환경 변수 BIGQUERY_PROJECT_ID가 설정되지 않았습니다.")

    # 인자 로깅 (추가)
    print(f"[DEBUG] Received gcp_key_path (from env): {gcp_key_path}")
    print(f"[DEBUG] Received date_str: {date_str}")
    print(f"[DEBUG] Received BIGQUERY_PROJECT_ID (from env): {BIGQUERY_PROJECT_ID}")

    # --- 환경 설정 ---
    GCS_BUCKET_NAME = "fx-alert-currency-data"  # 원본 데이터가 저장된 버킷
    # BIGQUERY_PROJECT_ID는 위에서 환경 변수에서 가져오도록 수정했습니다.
    BIGQUERY_DATASET = "fx_alert_data"     # BigQuery 데이터셋 이름
    BIGQUERY_TABLE = "usd_krw_daily_avg"       # BigQuery 테이블 이름

    # 1. SparkSession 생성
    # BigQuery와 GCS 연동을 위한 필수 설정들을 추가합니다.
    spark = SparkSession.builder \
        .appName("ExchangeRateETL") \
        .config("spark.hadoop.fs.gcs.auth.service.account.enable", "true") \
        .config("spark.hadoop.google.cloud.auth.service.account.json.keyfile", gcp_key_path) \
        .config("spark.hadoop.fs.gs.impl", "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem") \
        .config("spark.hadoop.fs.gs.project.id", BIGQUERY_PROJECT_ID) \
        .getOrCreate()

    print("Spark Session이 성공적으로 생성되었습니다.")

    # 2. GCS에서 데이터 읽기 (Extract)
    # ECOS API 스크립트가 저장한 CSV 파일 경로를 지정합니다.
    gcs_input_path = f"gs://{GCS_BUCKET_NAME}/raw_data/exchange_rates_{date_str}.csv"
    print(f"[DEBUG] GCS input path: {gcs_input_path}") # GCS 경로 로깅 (추가)
    
    try:
        raw_df = spark.read \
            .format("csv") \
            .option("header", "true") \
            .option("inferSchema", "false") \
            .load(gcs_input_path)
        
        print(f"GCS 경로에서 데이터를 성공적으로 읽었습니다: {gcs_input_path}")
        raw_df.printSchema()
        raw_df.show(5)

    except Exception as e:
        print(f"GCS에서 데이터를 읽는 중 오류 발생: {e}")
        spark.stop()
        return

    # 3. 데이터 변환 (Transform)
    # 컬럼 타입을 명시적으로 변환하고, 3년 평균을 계산합니다.
    transformed_df = raw_df \
        .withColumn("Date", raw_df["Date"].cast(DateType())) \
        .withColumn("Exchange_Rate", raw_df["Exchange_Rate"].cast(DoubleType()))

    # 결측치(null)가 있을 경우를 대비해 제거합니다.
    transformed_df = transformed_df.na.drop()
    
    # 3년 평균 환율 계산
    # .first()[0]을 사용하여 DataFrame에서 실제 숫자 값(스칼라 값)을 추출합니다.
    three_year_average = transformed_df.select(avg("Exchange_Rate")).first()[0]
    
    print(f"계산된 3년 평균 환율: {three_year_average:.2f}")

    # 4. BigQuery에 저장할 결과 DataFrame 생성
    # 나중에 분석의 편의를 위해 계산된 시점의 타임스탬프를 함께 저장합니다.
    result_df = spark.createDataFrame(
        [(three_year_average, datetime.now())],
        ["three_year_avg_rate", "processed_timestamp"]
    )
    
    print("BigQuery에 저장할 최종 데이터:")
    result_df.show()

    # 5. BigQuery에 데이터 쓰기 (Load)
    # BigQuery에 쓰기 위해 임시 GCS 버킷이 필요합니다.
    # BigQuery 커넥터는 데이터를 먼저 이 임시 버킷에 쓴 다음 BigQuery로 로드합니다.
    spark.conf.set("temporaryGcsBucket", GCS_BUCKET_NAME)
    
    bq_table_path = f"{BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET}.{BIGQUERY_TABLE}"
    
    try:
        result_df.write \
            .format("bigquery") \
            .option("table", bq_table_path) \
            .mode("append") \
            .save() # mode("append")는 기존 데이터에 추가, "overwrite"는 덮어쓰기
            
        print(f"데이터를 BigQuery 테이블({bq_table_path})에 성공적으로 저장했습니다.")

    except Exception as e:
        print(f"BigQuery에 데이터를 쓰는 중 오류 발생: {e}")

    finally:
        # 6. SparkSession 종료
        spark.stop()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("사용법: spark-submit spark_exchange_rate_processor.py <date_str>")
        sys.exit(-1)
    
    date_str = sys.argv[1] # 예: 20250926
    
    process_exchange_rate_data(date_str)