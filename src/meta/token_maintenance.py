"""
refresh-threads  Swaps the Threads long-lived token for a fresh 60-day one and saves it as a new
                 secret version. Runs weekly, so a failed refresh has several chances to be noticed.
check-expiry     Asks Meta when the Facebook and Instagram tokens expire and fails the run if any
                 has WARN_DAYS or fewer left, so someone rotates it before the pipelines stop.
"""

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests
from google.cloud import secretmanager

from src.authentication import get_gcp_secret
from src.logging_config import setup_logging

logger = logging.getLogger(__name__)

PROJECT_ID = "gds-social-data"
FACEBOOK_GRAPH = "https://graph.facebook.com/v25.0"
THREADS_GRAPH = "https://graph.threads.net"
THREADS_TOKEN_SECRET = "threads_long_lived_access_token"
WARN_DAYS = 14


@dataclass(frozen=True)
class CheckedToken:
    platform: str
    secret_id: str
    json_wrapped: bool
    app_id_secret_id: str
    app_secret_secret_id: str


# Tokens Meta's debug_token endpoint can report on, each checked with the app that issued it
CHECKED_TOKENS = [
    CheckedToken("Facebook", "facebook_page_token", True, "facebook_app_id", "facebook_app_secret"),
    CheckedToken("Instagram", "instagram_bearer_token", False, "instagram_app_id", "instagram_app_secret"),
]


def _secret(name: str) -> str:
    return get_gcp_secret(name, project_id=PROJECT_ID).strip()


def _meta_get(url: str, params: dict, label: str) -> dict:
    """GET a Meta endpoint without ever putting the request URL, and so its tokens, in an error."""
    try:
        response = requests.get(url, params=params, timeout=30)
    except requests.RequestException as e:
        # requests puts the full URL in its errors, so it's dropped here rather than logged
        raise RuntimeError(f"{label} failed: could not reach Meta ({type(e).__name__})") from None
    if response.ok:
        return response.json()
    try:
        detail = response.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    raise RuntimeError(f"{label} failed with HTTP {response.status_code}: {detail}")


def refresh_threads_token() -> datetime:
    """Refresh the Threads token, store it as a new secret version and return its new expiry."""
    data = _meta_get(
        f"{THREADS_GRAPH}/refresh_access_token",
        {"grant_type": "th_refresh_token", "access_token": _secret(THREADS_TOKEN_SECRET)},
        "Threads token refresh",
    )
    expires_at = datetime.now(UTC) + timedelta(seconds=int(data["expires_in"]))

    client = secretmanager.SecretManagerServiceClient()
    client.add_secret_version(
        request={
            "parent": f"projects/{PROJECT_ID}/secrets/{THREADS_TOKEN_SECRET}",
            "payload": {"data": data["access_token"].encode("utf-8")},
        }
    )
    logger.info("Threads token refreshed and saved, now valid until %s", expires_at.date())
    return expires_at


def token_expiry(debug_data: dict) -> datetime | None:
    """The earliest point the token stops working, or None if it never expires.

    Meta reports two dates: when the token itself expires, and when its access to data lapses.
    Either one stops the pipeline, and 0 means that limit doesn't apply.
    """
    timestamps = [debug_data.get(key) or 0 for key in ("expires_at", "data_access_expires_at")]
    timestamps = [t for t in timestamps if t > 0]
    return datetime.fromtimestamp(min(timestamps), UTC) if timestamps else None


def check_token(entry: CheckedToken, now: datetime) -> str | None:
    """Return a problem description if the token is invalid or close to expiring, otherwise None."""
    raw = _secret(entry.secret_id)
    access_token = json.loads(raw)["access_token"] if entry.json_wrapped else raw
    app_token = f"{_secret(entry.app_id_secret_id)}|{_secret(entry.app_secret_secret_id)}"
    debug = _meta_get(
        f"{FACEBOOK_GRAPH}/debug_token",
        {"input_token": access_token, "access_token": app_token},
        f"{entry.platform} expiry check",
    ).get("data", {})

    if not debug.get("is_valid"):
        return f"{entry.platform} token is no longer valid, rotate it now"
    expires_at = token_expiry(debug)
    if expires_at is None:
        logger.info("%s token never expires", entry.platform)
        return None
    days_left = (expires_at - now).days
    logger.info("%s token expires on %s (%d days left)", entry.platform, expires_at.date(), days_left)
    if days_left <= WARN_DAYS:
        return f"{entry.platform} token expires on {expires_at.date()} ({days_left} days left), rotate it"
    return None


def check_expiry(now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    problems = []
    for entry in CHECKED_TOKENS:
        try:
            problem = check_token(entry, now)
        except Exception as e:  # noqa: BLE001 - one unreadable token must not stop the others being checked
            problem = f"Could not check the {entry.platform} token: {e}"
        if problem:
            logger.error(problem)
            problems.append(problem)
    return problems


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh and check the Meta access tokens")
    parser.add_argument("--task", required=True, choices=["refresh-threads", "check-expiry"])
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    if args.task == "refresh-threads":
        refresh_threads_token()
        return
    problems = check_expiry()
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
