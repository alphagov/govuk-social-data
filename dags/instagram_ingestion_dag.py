import logging
from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

from common.config import IMAGE


def _on_failure(context: dict) -> None:
    log = logging.getLogger(__name__)
    log.error(
        "Task failed | dag=%s task=%s run_id=%s exception=%s",
        context["dag"].dag_id,
        context["task_instance"].task_id,
        context["run_id"],
        context.get("exception"),
    )


with DAG(
    dag_id="instagram_ingestion",
    schedule="0 3 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["social", "instagram"],
    params={
        # Empty by default -> falls back to yesterday. Override via a manual
        # trigger's --conf, e.g. --conf '{"target_date": "2026-01-01"}'.
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
            "{{ params.target_date or macros.ds_add(ds, -1) }}",
        ],
        get_logs=True,
        is_delete_operator_pod=True,
        image_pull_policy="Always",
        log_events_on_failure=True,
        on_failure_callback=_on_failure,
    )
