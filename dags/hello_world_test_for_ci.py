from datetime import datetime
from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator


def hello_world():
    print("Hello world!")


with DAG(
    dag_id="helloworld",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
):
    PythonOperator(
        task_id="helloworld",
        python_callable=hello_world,
    )
