"""Shared task callbacks for the ingestion DAGs."""

import logging


def on_failure(context: dict) -> None:
    log = logging.getLogger(__name__)
    log.error(
        "Task failed | dag=%s task=%s run_id=%s exception=%s",
        context["dag"].dag_id,
        context["task_instance"].task_id,
        context["run_id"],
        context.get("exception"),
    )
