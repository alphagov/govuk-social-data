from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

from common.callbacks import on_failure
from common.config import IMAGE

# src/main.py drives every stage off argparse.BooleanOptionalAction flags that default to True, so a
# bare "-m src.main" runs the metadata refresh and the daily reports in one process. Both tasks below
# spell out every flag, including the ones already holding their default, so the pod arguments say
# exactly which stage they are.

with DAG(
    dag_id="youtube_ingestion",
    schedule="0 3 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["social", "youtube"],
    params={
        # Empty by default -> falls back to two calendar days before the run: ds is data_interval_start,
        # which for a daily schedule is already the day before the run, and ds_add takes one more off.
        # That second day back is deliberate and specific to YouTube: Analytics figures for the
        # previous day are still settling. Facebook, Instagram and Threads daily use a bare ds and so
        # sit one day ahead of this. Override via a manual trigger's --conf, e.g.
        # --conf '{"start_date": "2026-01-01", "end_date": "2026-01-01"}'.
        "start_date": "",
        "end_date": "",
    },
) as dag:
    # Refreshes video_id_title_mapping and video_id_metadata_mapping. Takes no dates: the refresh path
    # reads the whole uploads playlist, and src/main.py only enforces start-date == end-date when daily
    # reports are on.
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

    # Reads the video IDs the refresh just wrote, so it must run after it. Both date templates have to
    # resolve to the same day or src/main.py raises before any API call is made.
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
            "{{ params.start_date or macros.ds_add(ds, -1) }}",
            "--end-date",
            "{{ params.end_date or macros.ds_add(ds, -1) }}",
        ],
        get_logs=True,
        is_delete_operator_pod=True,
        image_pull_policy="Always",
        log_events_on_failure=True,
        on_failure_callback=on_failure,
    )

    refresh_metadata >> daily_reports
