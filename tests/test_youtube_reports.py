import json

import httplib2
import pyarrow as pa
import pytest
from googleapiclient.errors import HttpError

import src.main as youtube
from src.main import (
    PLAYBACK_LOCATION_BQ_SCHEMA,
    RATE_LIMIT_WAITS_SECONDS,
    fetch_daily_report_for_all_videos,
    get_playback_locations_single_video_config,
    run_daily_reports,
    run_periodic_reports,
)

DAY = "2026-10-15"


def _http_error(status: int, message: str = "error") -> HttpError:
    return HttpError(httplib2.Response({"status": status}), json.dumps({"error": {"message": message}}).encode())


def _report(schema: dict, rows: int = 1) -> dict:
    """A YouTube Analytics API response with one row per entry, matching the schema's columns."""
    samples = {pa.date32(): DAY, pa.string(): "EMBEDDED", pa.int64(): 5, pa.float64(): 1.5}
    headers = [name for name in schema if name != "video_id"]
    return {
        "columnHeaders": [{"name": name} for name in headers],
        "rows": [[samples[schema[name]] for name in headers] for _ in range(rows)],
    }


class FakeAnalytics:
    """Stands in for the YouTube Analytics service, serving responses or errors in order."""

    def __init__(self, outcomes: list):
        self.outcomes = outcomes
        self.calls: list[dict] = []

    def reports(self):
        return self

    def query(self, **params):
        self.calls.append(params)
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        return FakeRequest(outcome)


class FakeRequest:
    def __init__(self, outcome):
        self.outcome = outcome

    def execute(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def sleeps(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(youtube.time, "sleep", waits.append)
    return waits


def _fetch(service, video_ids):
    return fetch_daily_report_for_all_videos(
        service, video_ids, DAY, DAY, PLAYBACK_LOCATION_BQ_SCHEMA, get_playback_locations_single_video_config
    )


def test_200_returns_rows_for_every_video(sleeps):
    service = FakeAnalytics([_report(PLAYBACK_LOCATION_BQ_SCHEMA, rows=2)])

    table, failed_ids = _fetch(service, ["video_a", "video_b"])

    assert failed_ids == []
    assert table.num_rows == 4
    assert table.column("video_id").to_pylist() == ["video_a", "video_a", "video_b", "video_b"]
    assert table.column_names[0] == "video_id"
    assert [call["filters"] for call in service.calls] == ["video==video_a", "video==video_b"]


def test_400_stops_the_report_and_fails_remaining_videos(sleeps):
    service = FakeAnalytics([_report(PLAYBACK_LOCATION_BQ_SCHEMA), _http_error(400, "Unknown identifier")])

    table, failed_ids = _fetch(service, ["video_a", "video_b", "video_c"])

    assert failed_ids == ["video_b", "video_c"]
    assert table.column("video_id").to_pylist() == ["video_a"]
    assert len(service.calls) == 2


def test_429_waits_then_retries(sleeps):
    service = FakeAnalytics([_http_error(429, "Quota exceeded"), _report(PLAYBACK_LOCATION_BQ_SCHEMA)])

    table, failed_ids = _fetch(service, ["video_a"])

    assert failed_ids == []
    assert table.num_rows == 1
    assert len(service.calls) == 2
    assert RATE_LIMIT_WAITS_SECONDS[0] in sleeps


def test_429_on_every_retry_fails_only_that_video(sleeps):
    rate_limited = [_http_error(429, "Quota exceeded")] * (len(RATE_LIMIT_WAITS_SECONDS) + 1)
    service = FakeAnalytics([*rate_limited, _report(PLAYBACK_LOCATION_BQ_SCHEMA)])

    table, failed_ids = _fetch(service, ["video_a", "video_b"])

    assert failed_ids == ["video_a"]
    assert table.column("video_id").to_pylist() == ["video_b"]
    assert len(service.calls) == len(RATE_LIMIT_WAITS_SECONDS) + 2


def test_500_is_retried(sleeps):
    service = FakeAnalytics([_http_error(500), _report(PLAYBACK_LOCATION_BQ_SCHEMA)])

    table, failed_ids = _fetch(service, ["video_a"])

    assert failed_ids == []
    assert table.num_rows == 1


def test_no_videos_returns_empty_table_with_schema(sleeps):
    table, failed_ids = _fetch(FakeAnalytics([]), [])

    assert failed_ids == []
    assert table.num_rows == 0
    assert table.column_names == ["video_id", *PLAYBACK_LOCATION_BQ_SCHEMA]


@pytest.fixture
def fake_loads(monkeypatch):
    loaded: list[str] = []
    monkeypatch.setattr(youtube, "load_data_to_partitioned_bq", lambda table, name, *a, **k: loaded.append(name))
    monkeypatch.setattr(youtube, "load_data_to_partitioned_bq_merge", lambda table, name, *a, **k: loaded.append(name))
    return loaded


def _fake_run_report(failing_label=None, user_activity_rows=1):
    def fake(service, video_ids, start, end, schema, config_fn, label):
        rows = user_activity_rows if label == "User Activity" else 1
        table = pa.table(
            {name: pa.array([None] * rows, type=dtype) for name, dtype in schema.items() if name != "month"}
        )
        table = table.add_column(0, "video_id", pa.array(["video_a"] * rows, type=pa.string()))
        return table, (["video_a"] if label == failing_label else [])

    return fake


def test_run_daily_reports_loads_every_table(monkeypatch, fake_loads):
    monkeypatch.setattr(youtube, "get_videos_with_views", lambda *a: {"video_a": 10})
    monkeypatch.setattr(youtube, "_run_report", _fake_run_report())

    failures = run_daily_reports(object(), ["video_a"], DAY, "test_dataset", full_dataset=True)

    assert failures == []
    assert fake_loads == [
        "user_activity",
        "user_activity_with_country",
        "playback_location",
        "traffic_sources",
        "operating_system_and_device",
    ]


def test_run_daily_reports_returns_labelled_failures(monkeypatch, fake_loads):
    monkeypatch.setattr(youtube, "get_videos_with_views", lambda *a: {"video_a": 10})
    monkeypatch.setattr(youtube, "_run_report", _fake_run_report(failing_label="Traffic Sources"))

    failures = run_daily_reports(object(), ["video_a"], DAY, "test_dataset", full_dataset=True)

    assert failures == ["Traffic Sources: video_a"]
    assert len(fake_loads) == 5


def test_run_daily_reports_raises_when_no_views(monkeypatch, fake_loads):
    monkeypatch.setattr(youtube, "get_videos_with_views", lambda *a: {})

    with pytest.raises(RuntimeError, match="no video views"):
        run_daily_reports(object(), ["video_a"], DAY, "test_dataset", full_dataset=True)

    assert fake_loads == []


def test_run_daily_reports_raises_when_user_activity_is_empty(monkeypatch, fake_loads):
    monkeypatch.setattr(youtube, "get_videos_with_views", lambda *a: {"video_a": 10})
    monkeypatch.setattr(youtube, "_run_report", _fake_run_report(user_activity_rows=0))

    with pytest.raises(RuntimeError, match="User Activity returned 0 rows"):
        run_daily_reports(object(), ["video_a"], DAY, "test_dataset", full_dataset=True)

    assert fake_loads == []


def test_run_periodic_reports_returns_labelled_failures(monkeypatch, fake_loads):
    monkeypatch.setattr(youtube, "_run_report", _fake_run_report(failing_label="Viewer Demographics (2026-09)"))

    failures = run_periodic_reports(object(), ["video_a"], "2026-09-01", "2026-10-31", "test_dataset")

    assert failures == ["Viewer Demographics (2026-09): video_a"]
    assert fake_loads == [
        "viewer_demographic",
        "engagement_and_content_location",
        "viewer_demographic",
        "engagement_and_content_location",
    ]
