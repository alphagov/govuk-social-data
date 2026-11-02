from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

from common.callbacks import on_failure
from common.config import IMAGE

# Snapshot mode captures everything the Threads API only reports as of now: profile, posts, post
# insights, replies, followers_count and demographics. Kept separate from threads_daily_ingestion
# because it exits non-zero whenever any one table fails to load, and that must not block, or be
# blocked by, the daily account metrics.

with DAG(
    dag_id="threads_snapshot_ingestion",
    schedule="0 4 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["social", "threads"],
    params={
        # Empty by default -> falls back to the date the run actually fires on, which is what a
        # snapshot partition means: the day the reading was taken. data_interval_end is used rather
        # than ds because ds is data_interval_start, i.e. the day before the run. Override via a manual
        # trigger's --conf, e.g. --conf '{"snapshot_date": "2026-01-01"}'.
        "snapshot_date": "",
    },
) as dag:
    KubernetesPodOperator(
        task_id="run_threads_snapshot_pipeline",
        name="threads-snapshot-ingestion",
        namespace="default",
        image=IMAGE,
        arguments=[
            "-m",
            "threads.main",
            "--mode",
            "snapshot",
            "--dataset",
            "govuk_threads",
            "--snapshot-date",
            "{{ params.snapshot_date or (data_interval_end | ds) }}",
        ],
        get_logs=True,
        is_delete_operator_pod=True,
        image_pull_policy="Always",
        log_events_on_failure=True,
        on_failure_callback=on_failure,
    )
