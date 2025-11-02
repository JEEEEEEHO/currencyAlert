# ./Dockerfile
FROM apache/airflow:2.9.2

USER root

# 기본 의존성 설치
# Airflow 기본 이미지가 Python 3.12를 사용하므로,
# Spark worker도 Python 3.12를 사용하도록 변경했습니다.
# 이렇게 하면 추가로 Python을 설치할 필요가 없습니다.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates openjdk-17-jre-headless procps \
 && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH="$JAVA_HOME/bin:$PATH"

# === Spark 3.5.1 설치 ===
RUN curl -fsSL https://archive.apache.org/dist/spark/spark-3.5.1/spark-3.5.1-bin-hadoop3.tgz -o /tmp/spark.tgz \
 && mkdir -p /opt \
 && tar -xzf /tmp/spark.tgz -C /opt \
 && ln -s /opt/spark-3.5.1-bin-hadoop3 /opt/spark \
 && rm /tmp/spark.tgz

# === Google API client libs for GCS connector ===
RUN mkdir -p /opt/spark/jars-extra && \
    curl -L -o /opt/spark/jars-extra/google-api-client-2.2.0.jar https://repo1.maven.org/maven2/com/google/api-client/google-api-client/2.2.0/google-api-client-2.2.0.jar && \
    curl -L -o /opt/spark/jars-extra/google-http-client-1.43.3.jar https://repo1.maven.org/maven2/com/google/http-client/google-http-client/1.43.3/google-http-client-1.43.3.jar && \
    curl -L -o /opt/spark/jars-extra/google-oauth-client-1.34.1.jar https://repo1.maven.org/maven2/com/google/oauth-client/google-oauth-client/1.34.1.jar

# === 추가: GCS & BigQuery 커넥터 설치 ===
RUN curl -L -o /opt/spark/jars-extra/gcs-connector-hadoop3-2.2.22.jar https://storage.googleapis.com/hadoop-lib/gcs/gcs-connector-hadoop3-2.2.22.jar && \
    curl -L -o /opt/spark/jars-extra/spark-bigquery-with-dependencies_2.12-0.42.2.jar https://storage.googleapis.com/spark-lib/bigquery/spark-bigquery-with-dependencies_2.12-0.42.2.jar

# Spark 환경변수 (전역 적용)
ENV SPARK_HOME=/opt/spark
ENV PATH="${SPARK_HOME}/bin:${PATH}"
ENV SPARK_BINARY=spark-submit
# Spark와 Python 버전 일치를 위한 환경 변수 설정
# 주니어 개발자님께: Spark worker와 동일한 Python 3.12 버전을 사용하도록 설정합니다
# Airflow 기본 이미지의 Python 경로를 확인하여 설정합니다.
ENV PYSPARK_PYTHON=python3
ENV PYSPARK_DRIVER_PYTHON=python3

USER airflow

# === Airflow Spark provider 설치 ===
ARG AIRFLOW_VERSION=2.9.2
ARG PYTHON_MAJOR_MINOR=3.12
RUN pip install --no-cache-dir \
  apache-airflow-providers-apache-spark \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_MAJOR_MINOR}.txt"

# === 런타임 Python 의존성 고정 ===
RUN pip install --no-cache-dir \
  "celery[redis]==5.4.0" "redis>=5,<6" \
  pyspark==3.5.1 \
  pandas \
  requests \
  google-cloud-storage \
  google-cloud-bigquery \
  google-auth
