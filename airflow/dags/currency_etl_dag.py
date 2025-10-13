from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from datetime import datetime, timedelta
import os
import sys

# Airflow에서 외부 Python 스크립트를 import 할 수 있도록 sys.path에 추가합니다.
# 이 경로는 Airflow 스케줄러 및 워커가 DAG 파일을 로드할 때 현재 프로젝트의 루트 디렉토리를
# Python 경로에 추가하여 collector.py 및 processor.py 모듈을 찾을 수 있도록 합니다.
# 만약 Airflow가 Docker 컨테이너 등 별도 환경에서 실행된다면, 해당 환경에 맞게 경로를 조정해야 합니다.
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.append(PROJECT_ROOT)

# 이제 exchange_rate_collector.py와 spark_exchange_rate_processor.py를 import 할 수 있습니다.
# collector 스크립트에서 함수를 직접 호출하기 위해 import 합니다.
from exchange_rate_collector import collect_and_upload_exchange_rates

# --- DAG 정의 --- #
with DAG(
    dag_id='currency_etl_pipeline',
    start_date=datetime(2023, 1, 1), # DAG의 첫 실행 날짜. 보통 과거 날짜로 설정하여 DAG 배포 후 즉시 실행되지 않도록 합니다.
    schedule_interval=timedelta(days=1), # 매일 한 번 실행되도록 설정합니다.
    catchup=False, # start_date 이후의 지난 실행을 백필하지 않도록 설정합니다. (중요!)
    tags=['currency', 'etl', 'spark'], # Airflow UI에서 DAG를 필터링하는 데 사용되는 태그.
    default_args={
        'owner': 'airflow', # DAG 소유자 지정.
        'depends_on_past': False, # 이전 DAG 실행 성공 여부에 의존하지 않도록 설정.
        'email_on_failure': False, # 태스크 실패 시 이메일 알림 비활성화.
        'email_on_retry': False, # 태스크 재시도 시 이메일 알림 비활성화.
        'retries': 1, # 태스크 실패 시 1회 재시도.
        'retry_delay': timedelta(minutes=5), # 재시도 간격 5분.
    }
) as dag:
    # --- 1. 환율 데이터 수집 및 GCS 업로드 태스크 (PythonOperator) --- #
    # PythonOperator를 사용하여 exchange_rate_collector.py 스크립트의
    # collect_and_upload_exchange_rates 함수를 실행합니다.
    # Airflow의 "{{ ds_nodash }}" 매크로는 현재 DAG 실행 날짜(YYYYMMDD)를 자동으로 전달합니다.
    collect_exchange_rates_task = PythonOperator(
        task_id='collect_and_upload_exchange_rates_to_gcs', # 태스크 고유 ID.
        python_callable=collect_and_upload_exchange_rates, # 실행할 Python 함수 지정.
        op_kwargs={ # Python 함수에 전달할 인자들을 딕셔너리 형태로 정의합니다.
            'target_date_str': "{{ ds_nodash }}" # 매크로를 사용하여 현재 실행 날짜를 YYYYMMDD 형식으로 전달.
        },
        # DAG 파일과 동일한 위치에 있는 스크립트를 참조할 수 있도록 설정합니다.
        # sys.path에 프로젝트 루트를 추가했으므로, 이제 collect_and_upload_exchange_rates 함수를 찾을 수 있습니다.
    )

    # --- 2. Spark Job 실행 태스크 (SparkSubmitOperator) --- #
    # SparkSubmitOperator를 사용하여 spark_exchange_rate_processor.py 스크립트를 Spark 클러스터에 제출합니다.
    # 이 태스크는 GCS에서 데이터를 읽어 BigQuery에 저장하는 역할을 합니다.
    # SparkSubmitOperator는 Airflow에 설정된 Spark Connection ID를 사용하여 Spark 클러스터에 연결합니다.
    # (예: airflow connections에서 spark_default 또는 직접 설정한 connection ID)
    #
    # 중요: GCP 서비스 계정 키 파일 경로는 Airflow 워커가 접근 가능한 경로에 있어야 합니다.
    # 실제 환경에서는 이 경로를 환경 변수 또는 Airflow Variables를 통해 안전하게 관리해야 합니다.
    # 또한, spark_exchange_rate_processor.py 스크립트 자체도 Airflow 워커가 접근 가능한 경로에 있어야 합니다.
    # 여기서는 프로젝트 루트 경로를 기준으로 상대 경로를 사용합니다.
    # Spark 컨테이너 내부에서 스크립트가 마운트된 경로를 참조하도록 수정합니다.
    SPARK_JOB_SCRIPT = "/opt/spark/app/spark_exchange_rate_processor.py"
    # GCP 서비스 계정 키 파일 경로는 환경 변수에서 가져오도록 변경합니다.
    # 보안을 위해 하드코딩된 경로를 제거하고 환경변수에서만 가져옵니다.
    GCP_SERVICE_ACCOUNT_KEY_PATH = os.environ.get("GCP_SERVICE_ACCOUNT_KEY_PATH")
    if not GCP_SERVICE_ACCOUNT_KEY_PATH:
        raise ValueError("환경 변수 GCP_SERVICE_ACCOUNT_KEY_PATH가 설정되지 않았습니다.")
    
    # BigQuery 프로젝트 ID도 환경 변수에서 가져오도록 변경합니다.
    BIGQUERY_PROJECT_ID = os.environ.get("BIGQUERY_PROJECT_ID")
    if not BIGQUERY_PROJECT_ID:
        raise ValueError("환경 변수 BIGQUERY_PROJECT_ID가 설정되지 않았습니다.")

    process_spark_data_task = SparkSubmitOperator(
        task_id='process_exchange_rate_data_with_spark', # 태스크 고유 ID.
        application=SPARK_JOB_SCRIPT, # 실행할 Spark 애플리케이션 스크립트 경로.
        conn_id='spark_default', # Airflow Connections에 설정된 Spark Connection ID (예: 'spark_default').
                                 # 만약 Spark Standalone, YARN, Mesos 등 다른 클러스터 매니저를 사용한다면
                                 # 해당 설정에 맞게 Connection을 생성하고 ID를 지정해야 합니다.
                                 # (로컬 Spark 환경의 경우, 'local[*]' 등을 사용하여 SparkSession을 구성할 수 있습니다.)
        conf={ # Spark 설정 파라미터.
            "spark.driver.extraJavaOptions": "-Dlog4j.configuration=file:/opt/spark/conf/log4j2.properties",
            "spark.executor.extraJavaOptions": "-Dlog4j.configuration=file:/opt/spark/conf/log4j2.properties",
            "spark.hadoop.google.cloud.auth.service.account.json.keyfile": GCP_SERVICE_ACCOUNT_KEY_PATH,
            "spark.hadoop.fs.gs.project.id": BIGQUERY_PROJECT_ID # 환경 변수에서 가져온 프로젝트 ID 사용
        },
        application_args=[ # Spark 애플리케이션 스크립트에 전달할 인자들.
            GCP_SERVICE_ACCOUNT_KEY_PATH, # 첫 번째 인자: GCP 서비스 계정 키 파일 경로.
            "{{ ds_nodash }}" # 두 번째 인자: 매크로를 사용하여 현재 실행 날짜를 YYYYMMDD 형식으로 전달.
        ],
        # Airflow 워커가 Spark 스크립트를 찾을 수 있도록 PATH 설정이 필요할 수 있습니다.
        # 또는 SparkSubmitOperator가 사용하는 Spark 클러스터 환경에 스크립트가 배포되어 있어야 합니다.
    )

    # --- 태스크 의존성 설정 --- #
    # collect_exchange_rates_task가 성공적으로 완료된 후에 process_spark_data_task가 실행되도록 설정합니다.
    collect_exchange_rates_task >> process_spark_data_task



