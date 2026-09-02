from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

from common.callbacks import on_failure
from common.config import IMAGE

with DAG(
    dag_id="facebook_ingestion",
    schedule="15 3 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["social", "facebook"],
    params={
        # Empty by default -> falls back to two calendar days before the run: ds is data_interval_start,
        # which for a daily schedule is already the day before the run, and the template takes one more
        # off. Override via a manual trigger's --conf, e.g.
        # --conf '{"start_date": "2026-01-01", "end_date": "2026-01-01"}'.
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
