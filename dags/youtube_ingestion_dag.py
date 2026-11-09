from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.timetables.interval import CronDataIntervalTimetable

from common.callbacks import on_failure
from common.config import IMAGE

# Daily YouTube ingestion: refresh video metadata, then load per-video daily reports
# for the run date minus 4 days (YouTube Analytics data lags ~3 days).
# Override with --conf '{"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}'.
with DAG(
    dag_id="youtube_ingestion",
    schedule=CronDataIntervalTimetable("0 3 * * *", timezone="UTC"),
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["social", "youtube"],
    params={
        "start_date": "",
        "end_date": "",
    },
) as dag:
    # Refresh video_id_title_mapping and video_id_metadata_mapping from the uploads playlist
    refresh_metadata = KubernetesPodOperator(
        task_id="refresh_youtube_metadata",
        name="youtube-metadata-refresh",
        namespace="default",
        image=IMAGE,
        arguments=[
            "-m",
            "src.main",
            "--refresh-youtube-ids-and-metadata",
            "--no-run-daily-reports",
            "--no-run-periodic-reports",
            "--full-dataset",
        ],
        get_logs=True,
        is_delete_operator_pod=True,
        image_pull_policy="Always",
        log_events_on_failure=True,
        on_failure_callback=on_failure,
    )

    # Load per-video daily reports for a single day (start and end must match)
    daily_reports = KubernetesPodOperator(
        task_id="run_youtube_daily_reports",
        name="youtube-daily-reports",
        namespace="default",
        image=IMAGE,
        arguments=[
            "-m",
            "src.main",
            "--no-refresh-youtube-ids-and-metadata",
            "--run-daily-reports",
            "--no-run-periodic-reports",
            "--full-dataset",
            "--start-date",
            "{{ params.start_date or macros.ds_add(ds, -3) }}",
            "--end-date",
            "{{ params.end_date or macros.ds_add(ds, -3) }}",
        ],
        get_logs=True,
        is_delete_operator_pod=True,
        image_pull_policy="Always",
        log_events_on_failure=True,
        on_failure_callback=on_failure,
    )

    refresh_metadata >> daily_reports
