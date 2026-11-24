import sys
from types import SimpleNamespace

import pytest
import requests

import facebook.main as facebook_main
import instagram.main as instagram_main
import threads.main as threads_main
from facebook.models import Page, PageInsightMetric, PageInsightValue
from instagram.models import IGAccount, IGMedia, IGMediaInsightMetric
from src.meta.errors import InsightMetricError
from src.meta.insights import run_insight_fetch


def _expired_token_error() -> requests.HTTPError:
    return requests.HTTPError(
        "400 Client Error - Meta error: Error validating access token: Session has expired",
        response=SimpleNamespace(status_code=400),
    )


def _raise(error):
    raise error


@pytest.fixture
def uploads(monkeypatch):
    loaded: list[str] = []

    def record(table, name, *args, **kwargs):
        loaded.append(name)

    for module in (facebook_main, instagram_main, threads_main):
        monkeypatch.setattr(module, "upload_partitioned", record)
    return loaded


def test_run_insight_fetch_returns_failed_ids():
    def fetch_one(item):
        if item == "metric_fails":
            raise InsightMetricError("path", ["views"], {"views": _expired_token_error()}, [])
        if item == "request_fails":
            raise _expired_token_error()
        return item

    results, failed_ids = run_insight_fetch(["ok", "metric_fails", "request_fails"], fetch_one, str, "Item")

    assert results == ["ok", "metric_fails", "request_fails"]
    assert failed_ids == ["metric_fails", "request_fails"]


def test_threads_daily_fails_when_every_metric_fails(monkeypatch, uploads):
    client = SimpleNamespace(
        get_account_insights=lambda *args: _raise(
            InsightMetricError(
                "path", ["views", "likes"], {"views": _expired_token_error(), "likes": _expired_token_error()}, []
            )
        )
    )
    monkeypatch.setattr(threads_main, "build_client", lambda: (client, "threads_user"))
    monkeypatch.setattr(sys, "argv", ["threads.main", "--mode", "daily", "--target-date", "2026-11-21"])

    with pytest.raises(SystemExit) as exc_info:
        threads_main.main()

    assert exc_info.value.code == 1
    assert uploads == ["threads_account_insights"]


def test_threads_daily_succeeds_when_metrics_load(monkeypatch, uploads):
    client = SimpleNamespace(get_account_insights=lambda *args: [])
    monkeypatch.setattr(threads_main, "build_client", lambda: (client, "threads_user"))
    monkeypatch.setattr(sys, "argv", ["threads.main", "--mode", "daily", "--target-date", "2026-11-21"])

    threads_main.main()

    assert uploads == ["threads_account_insights"]


def _facebook_args(monkeypatch):
    monkeypatch.setattr(facebook_main, "get_gcp_secret", lambda *args, **kwargs: "page_id")
    monkeypatch.setattr(
        sys,
        "argv",
        ["facebook.main", "--start-date", "2026-11-21", "--no-run-post-data", "--no-run-video-data"],
    )


def test_facebook_fails_when_token_is_dead(monkeypatch, uploads):
    client = SimpleNamespace(
        get_page=lambda page_id: _raise(_expired_token_error()),
        get_page_insights=lambda page_id, since, until: [],
    )
    monkeypatch.setattr(facebook_main, "build_client", lambda: client)
    _facebook_args(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        facebook_main.main()

    assert exc_info.value.code == 1


def test_facebook_succeeds_when_page_data_loads(monkeypatch, uploads):
    insight = PageInsightMetric(name="page_views_total", period="day", values=[PageInsightValue(value=5)])
    client = SimpleNamespace(
        get_page=lambda page_id: Page(page_id="page_id", name="GOV.UK"),
        get_page_insights=lambda page_id, since, until: [insight],
    )
    monkeypatch.setattr(facebook_main, "build_client", lambda: client)
    _facebook_args(monkeypatch)

    facebook_main.main()

    assert uploads == ["facebook_page_info", "facebook_page_insights"]


def test_instagram_fails_but_keeps_good_insights_when_one_post_fails(monkeypatch, uploads):
    def get_media_insights(media, metrics):
        if media.id == "broken":
            raise _expired_token_error()
        return media.model_copy(update={"insights": [IGMediaInsightMetric(name="views", period="lifetime", value=10)]})

    client = SimpleNamespace(
        get_account=lambda ig_user_id: IGAccount(id="ig_user", username="govuk"),
        get_account_insights=lambda *args: [],
        get_account_total_value_insights=lambda *args: [],
        get_media=lambda ig_user_id, max_results=None: [IGMedia(id="working"), IGMedia(id="broken")],
        get_media_insights=get_media_insights,
    )
    monkeypatch.setattr(instagram_main, "build_client", lambda: (client, "ig_user"))
    monkeypatch.setattr(sys, "argv", ["instagram.main", "--target-date", "2026-11-21"])

    with pytest.raises(SystemExit) as exc_info:
        instagram_main.main()

    assert exc_info.value.code == 1
    assert uploads[-1] == "instagram_media_insights"
