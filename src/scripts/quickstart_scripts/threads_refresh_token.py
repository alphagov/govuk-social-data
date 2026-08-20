"""Refresh the long-lived Threads access token and store it as a new Secret Manager version.

A Threads long-lived token lasts 60 days. Refreshing resets the clock to 60 days; going 60 days without
a refresh kills it permanently, and recovery means a human re-running the authorization window in
threads_oauth_quickstart.py. So this needs to run comfortably inside every 60-day window.

Deliberately separate from the pipeline: `threads/api.py` only ever reads the secret. Refreshing writes
to Secret Manager, which is a different permission and a different failure mode, and a token refresh
failing should not take an ingestion run down with it.

Run:
    poetry run python -m src.scripts.quickstart_scripts.threads_refresh_token
    poetry run python -m src.scripts.quickstart_scripts.threads_refresh_token --dry-run

Ref: https://developers.facebook.com/docs/threads/get-started/long-lived-tokens
"""

import argparse
from datetime import datetime, timedelta, timezone

import requests
from google.cloud import secretmanager

from src.authentication import get_gcp_secret

PROJECT_ID = "gds-social-data"
TOKEN_SECRET = "threads_long_lived_access_token"

GRAPH_HOST = "https://graph.threads.net"

# Refreshing a token that still has plenty of life left is harmless but pointless; below this it is
# overdue. Printed as a warning rather than enforced, so a manual early refresh still works.
REFRESH_DUE_DAYS = 30


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the long-lived Threads access token")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Call the refresh endpoint and report the new expiry, but do not write to Secret Manager.",
    )
    return parser.parse_args()


def refresh_long_lived_token(token: str) -> dict:
    """Reset a long-lived token's 60-day clock. Note this takes the token itself, not the app secret."""
    response = requests.get(
        f"{GRAPH_HOST}/refresh_access_token",
        params={"grant_type": "th_refresh_token", "access_token": token},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def add_secret_version(secret_id: str, payload: str, project_id: str = PROJECT_ID) -> str:
    """Add a new version to an existing secret. Does not create the secret — the quickstart does that."""
    client = secretmanager.SecretManagerServiceClient()
    version = client.add_secret_version(
        request={
            "parent": f"projects/{project_id}/secrets/{secret_id}",
            "payload": {"data": payload.encode("utf-8")},
        }
    )
    return version.name


def main() -> None:
    args = parse_args()

    print(f"Reading current token from '{TOKEN_SECRET}'...")
    current_token = get_gcp_secret(TOKEN_SECRET, project_id=PROJECT_ID).strip()

    print("Refreshing...")
    refreshed = refresh_long_lived_token(current_token)
    expires_in = int(refreshed["expires_in"])
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    print(f"  New token valid until {expires_at:%Y-%m-%d} ({expires_in}s).")

    if expires_in < REFRESH_DUE_DAYS * 86400:
        print(
            f"  WARNING: the refreshed token has under {REFRESH_DUE_DAYS} days of life, which is not what a "
            "successful refresh looks like. Check the app's permissions before relying on it."
        )

    if args.dry_run:
        print(
            "\n--dry-run: Secret Manager not written, so the token just issued is discarded. The stored "
            "token keeps its original expiry — this proves the refresh works, it does not extend anything."
        )
        return

    version_name = add_secret_version(TOKEN_SECRET, refreshed["access_token"])
    print(f"\nStored as {version_name}")
    print(f"Next refresh due before {expires_at:%Y-%m-%d}.")


if __name__ == "__main__":
    main()
