from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.timetables.interval import CronDataIntervalTimetable

from common.callbacks import on_failure
from common.config import IMAGE

with DAG(
    dag_id="instagram_ingestion",
    schedule=CronDataIntervalTimetable("30 3 * * *", timezone="UTC"),
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["social", "instagram"],
    params={
        # Empty by default -> falls back to the day before the run. A bare ds is what gives that: ds is
        # data_interval_start, not the run date, so for a daily schedule it is already yesterday.
        # Override via a manual trigger's --conf, e.g. --conf '{"target_date": "2026-01-01"}'.
        "target_date": "",
    },
) as dag:
    KubernetesPodOperator(
        task_id="run_instagram_pipeline",
        name="instagram-ingestion",
        namespace="default",
        image=IMAGE,
        arguments=[
            "-m",
            "instagram.main",
            "--dataset",
            "govuk_instagram",
            "--target-date",
            "{{ params.target_date or ds }}",
        ],
        get_logs=True,
        is_delete_operator_pod=True,
        image_pull_policy="Always",
        log_events_on_failure=True,
        on_failure_callback=on_failure,
    )
