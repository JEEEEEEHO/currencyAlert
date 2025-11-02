# dags/currency_etl_pipeline.py
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
import os
import subprocess

from scripts.exchange_rate_collector import collect_and_upload_exchange_rates

GCP_KEY = os.environ.get("GCP_SERVICE_ACCOUNT_KEY_PATH")
BQ_PROJECT = os.environ.get("BIGQUERY_PROJECT_ID")
# (위 쪽) 파일명 변수로 받아두면 편합니다
KEY_FILE = os.environ["GCP_KEY_FILENAME"]              # 예: fx-alert-currency-xxxx.json
AIRFLOW_KEY = f"/opt/airflow/keys/{KEY_FILE}"          # 드라이버 경로(Airflow 컨테이너 내부)
# Spark executor 경로: Spark worker 컨테이너에서는 /opt/spark/keys/로 마운트되어 있음
# docker-compose.yml을 보면 ./airflow/keys를 /opt/spark/keys로 마운트하고 있습니다
SPARK_KEY   = f"/opt/spark/keys/{KEY_FILE}"            # 실행기 경로(Spark 워커 컨테이너)

if not GCP_KEY:
    raise ValueError("환경 변수 GCP_SERVICE_ACCOUNT_KEY_PATH가 설정되지 않았습니다.")
if not BQ_PROJECT:
    raise ValueError("환경 변수 BIGQUERY_PROJECT_ID가 설정되지 않았습니다.")

# 로컬에서 받은 JAR들이 마운트된 위치
# docker-compose.yml에서 ./spark_jars를 /opt/spark-3.5.1-bin-hadoop3/jars-extra로 마운트
# /opt/spark는 /opt/spark-3.5.1-bin-hadoop3의 심볼릭 링크이므로 /opt/spark/jars-extra도 사용 가능
# 하지만 명시적으로 전체 경로를 사용하는 것이 더 안전합니다
JAR_BASE_PATH = "/opt/spark-3.5.1-bin-hadoop3/jars-extra"
JARS = ",".join([
    f"{JAR_BASE_PATH}/hadoop-client-api-3.3.6.jar",
    f"{JAR_BASE_PATH}/hadoop-client-runtime-3.3.6.jar",
    f"{JAR_BASE_PATH}/gcs-connector-hadoop3-2.2.22.jar",
    f"{JAR_BASE_PATH}/spark-bigquery-with-dependencies_2.12-0.42.2.jar",
])

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id='currency_etl_pipeline',
    start_date=datetime(2023, 1, 1),
    schedule_interval=timedelta(days=1),
    catchup=False,
    tags=['currency', 'etl', 'spark'],
    default_args=default_args
) as dag:

    collect_exchange_rates_task = PythonOperator(
        task_id='collect_and_upload_exchange_rates_to_gcs',
        python_callable=collect_and_upload_exchange_rates,
        op_kwargs={'target_date_str': "{{ ds_nodash }}"},
        do_xcom_push=True,
    )

    def run_spark_job(**context):
        """
        Spark submit을 직접 실행하는 함수.
        Airflow의 SparkSubmitOperator가 Connection URI 파싱 시 프로토콜을 제거하는 문제를 우회하기 위해
        PythonOperator로 직접 spark-submit 명령어를 실행합니다.
        
        주니어 개발자님께: 이 함수는 subprocess 모듈을 사용하여 Spark 클러스터에 작업을 제출합니다.
        subprocess.run()은 외부 프로그램을 실행하는 Python의 표준 방법입니다.
        """
        # XCom에서 이전 태스크의 결과(GCS 경로) 가져오기
        # XCom은 Airflow 태스크 간 데이터를 전달하는 메커니즘입니다
        gcs_path = context['ti'].xcom_pull(task_ids='collect_and_upload_exchange_rates_to_gcs')
        if not gcs_path:
            # XCom에 없으면 날짜로부터 경로 생성
            # ds_nodash는 Airflow가 자동으로 제공하는 변수로, 실행 날짜를 YYYYMMDD 형식으로 제공합니다
            ds_nodash = context['ds_nodash']
            gcs_path = f"gs://fx-alert-currency-data/raw_data/exchange_rates_{ds_nodash}.csv"
        
        # Spark submit 명령어 구성
        # /opt/spark/bin/spark-submit은 심볼릭 링크로, /opt/spark-3.5.1-bin-hadoop3를 가리킵니다
        # 심볼릭 링크를 사용하면 Spark 버전이 변경되어도 경로를 수정할 필요가 없습니다
        spark_submit_cmd = [
            "/opt/spark/bin/spark-submit",  # 심볼릭 링크 사용으로 경로 안정성 향상
            "--master", "spark://spark-master:7077",  # Spark 클러스터 마스터 노드 주소
            "--deploy-mode", "client",  # 클라이언트 모드: 드라이버가 Airflow 컨테이너에서 실행됨
            "--jars", ",".join([
                # JAR 파일들: GCS와 BigQuery 연동에 필요한 라이브러리들
                # docker-compose.yml에서 ./spark_jars를 /opt/spark-3.5.1-bin-hadoop3/jars-extra로 마운트
                f"{JAR_BASE_PATH}/gcs-connector-hadoop3-2.2.22.jar",  # GCS 파일 시스템 연동
                f"{JAR_BASE_PATH}/spark-bigquery-with-dependencies_2.12-0.42.2.jar",  # BigQuery 연동
                f"{JAR_BASE_PATH}/google-api-client-2.2.0.jar",  # Google API 클라이언트
                f"{JAR_BASE_PATH}/google-http-client-1.43.3.jar",  # HTTP 클라이언트
                f"{JAR_BASE_PATH}/google-oauth-client-1.34.1.jar",  # OAuth 인증 클라이언트
            ]),
            # GCS(Google Cloud Storage) 파일 시스템 설정
            "--conf", f"spark.hadoop.fs.gs.impl=com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem",
            "--conf", f"spark.hadoop.fs.AbstractFileSystem.gs.impl=com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS",
            "--conf", f"spark.hadoop.fs.gs.project.id={os.environ['BIGQUERY_PROJECT_ID']}",
            "--conf", f"spark.hadoop.fs.gs.auth.service.account.enable=true",
            "--conf", f"spark.hadoop.fs.gs.auth.service.account.json.keyfile={SPARK_KEY}",
            "--conf", f"spark.hadoop.fs.gs.auth.type=SERVICE_ACCOUNT_JSON_KEYFILE",
            # 환경 변수 전달: 드라이버와 실행기 모두 GCP 인증 정보 필요
            "--conf", f"spark.driverEnv.GOOGLE_APPLICATION_CREDENTIALS={AIRFLOW_KEY}",
            "--conf", f"spark.executorEnv.GOOGLE_APPLICATION_CREDENTIALS={SPARK_KEY}",
            "--conf", f"spark.driverEnv.GCP_SERVICE_ACCOUNT_KEY_PATH={AIRFLOW_KEY}",
            "--conf", f"spark.executorEnv.GCP_SERVICE_ACCOUNT_KEY_PATH={SPARK_KEY}",
            # BigQuery 설정
            "--conf", f"spark.bigquery.project={os.environ['BIGQUERY_PROJECT_ID']}",
            "--conf", f"spark.bigquery.parentProject={os.environ['BIGQUERY_PROJECT_ID']}",
            "--conf", f"spark.bigquery.writeMethod=indirect",  # 간접 쓰기: GCS를 임시 저장소로 사용
            "--conf", f"spark.bigquery.temp.bucket=fx-alert-currency-data",
            # Python 경로 설정: Spark가 Python 코드를 실행할 때 사용할 인터프리터
            # 주니어 개발자님께: driver와 executor의 Python 버전이 일치해야 합니다
            # Airflow 컨테이너와 Spark worker 모두 Python 3.12를 사용하도록 설정되어 있습니다
            # python3는 환경 변수에 따라 자동으로 python3.12를 가리킵니다
            "--conf", f"spark.pyspark.python=python3",
            "--conf", f"spark.pyspark.driver.python=python3",
            "--name", "currency-etl-spark-job",  # Spark 작업 이름
            "--verbose",  # 상세한 로그 출력
            "/opt/spark/work-dir/spark_exchange_rate_processor.py",  # 실행할 Spark 스크립트
            gcs_path  # 인자: GCS에서 읽을 데이터 경로
        ]
        
        print(f"[INFO] 실행할 Spark submit 명령어:")
        print(" ".join(spark_submit_cmd))
        
        # spark-submit 실행
        # check=False로 설정하여 예외를 발생시키지 않고, 수동으로 에러를 확인합니다
        # 이렇게 하면 실제 Spark의 에러 메시지를 더 명확하게 볼 수 있습니다
        # 
        # 주니어 개발자님께: subprocess.run()에서 capture_output=True를 사용하면
        # stdout과 stderr가 모두 캡처되어 변수에 저장됩니다.
        # 이렇게 하면 Airflow 로그에 Spark의 모든 출력이 기록됩니다.
        print(f"[INFO] Spark submit 시작...")
        result = subprocess.run(
            spark_submit_cmd,
            capture_output=True,  # 표준 출력과 에러를 캡처
            text=True,  # 문자열로 디코딩
            check=False  # 에러 발생 시 예외를 발생시키지 않음 (수동 처리)
        )
        
        # 표준 출력 출력 (일반적인 로그 메시지)
        # Spark는 정상 실행 시에도 많은 정보를 stdout에 출력합니다
        if result.stdout:
            print(f"[INFO] === Spark submit 표준 출력 (stdout) ===")
            # stdout을 줄 단위로 출력하여 로그 가독성 향상
            for line in result.stdout.split('\n'):
                if line.strip():  # 빈 줄은 제외
                    print(f"[SPARK-STDOUT] {line}")
            print(f"[INFO] === Spark submit 표준 출력 끝 ===")
        
        # 표준 에러 출력 (경고 및 에러 메시지)
        # Spark는 많은 정보를 stderr로 출력하므로, 이 부분을 반드시 확인해야 합니다
        if result.stderr:
            print(f"[INFO] === Spark submit 표준 에러 출력 (stderr) ===")
            # stderr를 줄 단위로 출력하여 로그 가독성 향상
            for line in result.stderr.split('\n'):
                if line.strip():  # 빈 줄은 제외
                    print(f"[SPARK-STDERR] {line}")
            print(f"[INFO] === Spark submit 표준 에러 출력 끝 ===")
        
        # 종료 코드 확인
        # 0이면 성공, 0이 아니면 실패
        # 주니어 개발자님께: 프로그램은 성공적으로 종료되면 0을 반환하고,
        # 오류가 발생하면 0이 아닌 값을 반환합니다 (일반적으로 1)
        if result.returncode != 0:
            error_msg = (
                f"[ERROR] Spark submit 실패 (exit code: {result.returncode})\n"
                f"명령어: {' '.join(spark_submit_cmd)}\n"
                f"\n=== 표준 출력 (stdout) ===\n{result.stdout}\n"
                f"\n=== 표준 에러 (stderr) ===\n{result.stderr}\n"
                f"========================\n"
            )
            print(error_msg)
            raise RuntimeError(error_msg)
        
        print("[INFO] Spark submit 성공!")
        return True

    process_spark_data_task = PythonOperator(
        task_id='process_exchange_rate_data_with_spark',
        python_callable=run_spark_job,
    )

    collect_exchange_rates_task >> process_spark_data_task
