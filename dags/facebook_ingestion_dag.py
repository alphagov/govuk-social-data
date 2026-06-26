import logging
from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

_IMAGE = "europe-west2-docker.pkg.dev/gds-social-data/gds-social-data-composer-artifacts/pipeline:latest"


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
    dag_id="facebook_ingestion",
    schedule="0 6 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["social", "facebook"],
) as dag:
    KubernetesPodOperator(
        task_id="run_facebook_pipeline",
        name="facebook-ingestion",
        namespace="default",
        image=_IMAGE,
        arguments=["-m", "facebook.main", "--dataset", "govuk_facebook"],
        get_logs=True,
        is_delete_operator_pod=True,
        image_pull_policy="Always",
        log_events_on_failure=True,
        on_failure_callback=_on_failure,
    )
