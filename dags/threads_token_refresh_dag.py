from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.timetables.interval import CronDataIntervalTimetable

from common.callbacks import on_failure
from common.config import IMAGE

# Weekly refresh of the Threads token, which otherwise dies 60 days after it was last refreshed
with DAG(
    dag_id="threads_token_refresh",
    schedule=CronDataIntervalTimetable("0 6 * * 1", timezone="UTC"),
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=30),
    },
    tags=["social", "threads", "tokens"],
) as dag:
    KubernetesPodOperator(
        task_id="refresh_threads_token",
        name="threads-token-refresh",
        namespace="default",
        image=IMAGE,
        arguments=["-m", "src.meta.token_maintenance", "--task", "refresh-threads"],
        get_logs=True,
        is_delete_operator_pod=True,
        image_pull_policy="Always",
        log_events_on_failure=True,
        on_failure_callback=on_failure,
    )
