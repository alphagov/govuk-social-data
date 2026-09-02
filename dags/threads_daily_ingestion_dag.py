from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

from common.callbacks import on_failure
from common.config import IMAGE

# Threads daily mode fetches only the five time-windowed account metrics. Everything else Threads
# reports is lifetime-to-date and lives in threads_snapshot_ingestion instead.

with DAG(
    dag_id="threads_daily_ingestion",
    schedule="45 3 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["social", "threads"],
    params={
        # Empty by default -> falls back to two calendar days before the run, matching facebook,
        # instagram and the youtube daily reports so one night's runs all land on the same partition.
        # Override via a manual trigger's --conf, e.g. --conf '{"target_date": "2026-01-01"}'.
        # A date outside the rolling 730-day retention window is refused by threads/api.py._to_unix
        # before any request is made.
        "target_date": "",
    },
) as dag:
    KubernetesPodOperator(
        task_id="run_threads_daily_pipeline",
        name="threads-daily-ingestion",
        namespace="default",
        image=IMAGE,
        arguments=[
            "-m",
            "threads.main",
            "--mode",
            "daily",
            "--dataset",
            "govuk_threads",
            "--target-date",
            "{{ params.target_date or macros.ds_add(ds, -1) }}",
        ],
        get_logs=True,
        is_delete_operator_pod=True,
        image_pull_policy="Always",
        log_events_on_failure=True,
        on_failure_callback=on_failure,
    )
