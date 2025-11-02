from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from datetime import datetime, timedelta
import os

# [수정 1] sys.path를 조작하는 복잡한 코드를 모두 삭제하고,
#          새로운 경로에서 바로 import 하도록 변경합니다.
from scripts.exchange_rate_collector import collect_and_upload_exchange_rates

# --- DAG 정의 --- #
with DAG(
    dag_id='currency_etl_pipeline',
    start_date=datetime(2023, 1, 1),
    schedule_interval=timedelta(days=1),
    catchup=False,
    tags=['currency', 'etl', 'spark'],
    default_args={
        'owner': 'airflow',
        'depends_on_past': False,
        'email_on_failure': False,
        'email_on_retry': False,
        'retries': 1,
        'retry_delay': timedelta(minutes=5),
    }
) as dag:
    # --- 1. PythonOperator 태스크 --- #
    # python_callable 경로가 변경되었지만, import 구문이 수정되었으므로
    # 이 부분은 변경할 필요 없이 그대로 작동합니다.
    collect_exchange_rates_task = PythonOperator(
        task_id='collect_and_upload_exchange_rates_to_gcs',
        python_callable=collect_and_upload_exchange_rates,
        op_kwargs={'target_date_str': "{{ ds_nodash }}"},
    )

    # --- 2. SparkSubmitOperator 태스크 --- #
    # [수정 2] Spark Job 스크립트 경로를 docker-compose.yml에서 마운트한 경로로 변경합니다.
    SPARK_JOB_SCRIPT = "/opt/spark/work-dir/spark_exchange_rate_processor.py"
    
    # 환경 변수 관련 코드는 그대로 유지합니다.
    GCP_SERVICE_ACCOUNT_KEY_PATH = os.environ.get("GCP_SERVICE_ACCOUNT_KEY_PATH")
    if not GCP_SERVICE_ACCOUNT_KEY_PATH:
        raise ValueError("환경 변수 GCP_SERVICE_ACCOUNT_KEY_PATH가 설정되지 않았습니다.")
    
    BIGQUERY_PROJECT_ID = os.environ.get("BIGQUERY_PROJECT_ID")
    if not BIGQUERY_PROJECT_ID:
        raise ValueError("환경 변수 BIGQUERY_PROJECT_ID가 설정되지 않았습니다.")

    process_spark_data_task = SparkSubmitOperator(
        task_id='process_exchange_rate_data_with_spark',
        application=SPARK_JOB_SCRIPT, # 수정된 경로 적용
        conn_id='spark_default',
        conf={
            "spark.driver.extraJavaOptions": "-Dlog4j.configuration=file:/opt/spark/conf/log4j2.properties",
            "spark.executor.extraJavaOptions": "-Dlog4j.configuration=file:/opt/spark/conf/log4j2.properties",
            "spark.hadoop.google.cloud.auth.service.account.json.keyfile": GCP_SERVICE_ACCOUNT_KEY_PATH,
            "spark.hadoop.fs.gs.project.id": BIGQUERY_PROJECT_ID
        },
        application_args=[
            GCP_SERVICE_ACCOUNT_KEY_PATH,
            "{{ ds_nodash }}"
        ],
    )

    # --- 태스크 의존성 설정 --- #
    collect_exchange_rates_task >> process_spark_data_task