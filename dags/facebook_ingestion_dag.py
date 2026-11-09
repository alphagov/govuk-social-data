from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.timetables.interval import CronDataIntervalTimetable

from common.callbacks import on_failure
from common.config import IMAGE

with DAG(
    dag_id="facebook_ingestion",
    schedule=CronDataIntervalTimetable("15 3 * * *", timezone="UTC"),
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["social", "facebook"],
    params={
        # Empty by default -> falls back to the day before the run. A bare ds is what gives that: ds is
        # data_interval_start, not the run date, so for a daily schedule it is already yesterday.
        # Override via a manual trigger's --conf, e.g.
        # --conf '{"start_date": "2026-01-01", "end_date": "2026-01-01"}'.
        # Both must resolve to the same day: facebook/main.py partitions on --start-date and uses
        # --end-date only as the insights `until`, so a mismatch widens the window silently.
        "start_date": "",
        "end_date": "",
    },
) as dag:
    KubernetesPodOperator(
        task_id="run_facebook_pipeline",
        name="facebook-ingestion",
        namespace="default",
        image=IMAGE,
        arguments=[
            "-m",
            "facebook.main",
            "--dataset",
            "govuk_facebook",
            "--start-date",
            "{{ params.start_date or ds }}",
            "--end-date",
            "{{ params.end_date or ds }}",
        ],
        get_logs=True,
        is_delete_operator_pod=True,
        image_pull_policy="Always",
        log_events_on_failure=True,
        on_failure_callback=on_failure,
    )
