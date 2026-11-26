from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.timetables.interval import CronDataIntervalTimetable

from common.callbacks import on_failure
from common.config import IMAGE

# Daily check that fails once the Facebook or Instagram token has 7 days or fewer left
with DAG(
    dag_id="meta_token_expiry_check",
    schedule=CronDataIntervalTimetable("0 8 * * *", timezone="UTC"),
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
    },
    tags=["social", "tokens"],
) as dag:
    KubernetesPodOperator(
        task_id="check_meta_token_expiry",
        name="meta-token-expiry-check",
        namespace="default",
        image=IMAGE,
        arguments=["-m", "src.meta.token_maintenance", "--task", "check-expiry"],
        get_logs=True,
        is_delete_operator_pod=True,
        image_pull_policy="Always",
        log_events_on_failure=True,
        on_failure_callback=on_failure,
    )
