# ./Dockerfile
FROM apache/airflow:2.9.2

# RUN apt-get update && apt-get install -y openjdk-17-jre-headless && rm -rf /var/lib/apt/lists/*
USER airflow

# Airflow 2.9.2와 맞는 constraints로 provider 설치
ARG AIRFLOW_VERSION=2.9.2
ARG PYTHON_MAJOR_MINOR=3.11
RUN pip install --no-cache-dir \
  "apache-airflow-providers-apache-spark" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_MAJOR_MINOR}.txt"
