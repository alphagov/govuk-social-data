"""Mint a Threads *app* access token from the app ID/secret pair.

An app access token identifies the application rather than a user. The only thing the quickstart
needs it for is asking `debug_token` to inspect a user token *as this app* — which is what turns
"Error while validating client secret" into a message naming the real problem, a token issued by a
different Threads app than the credentials in Secret Manager.

Two things worth knowing, both established against the live API rather than the docs:

  - Meta's classic `{app-id}|{app-secret}` pipe form does NOT work here. graph.threads.net answers it
    with code 190 "Cannot parse access token". The client_credentials grant below is the only form
    that works, and it returns a token that is itself shaped `TH|{app-id}|{secret}`.
  - The version prefix is optional; /oauth/access_token and /v1.0/oauth/access_token return the same
    token. Unversioned is used here to match the other OAuth hops in threads_oauth_quickstart.py.

App access tokens do not expire, so there is nothing to cache or refresh — it is cheaper to re-mint
than to store one. Never expose it client-side; it is the app secret in another wrapper.

Scope caveat, so this is not mistaken for a pipeline credential: an app token carries no user
permissions, so it cannot stand in for the user token that `/{threads-user-id}/threads_insights` and
`/replies` require. Do not store the output as `threads_access_token`.

Run standalone to check an app ID/secret pair is valid at all:
    poetry run python -m src.scripts.quickstart_scripts.threads_app_access_token
    poetry run python -m src.scripts.quickstart_scripts.threads_app_access_token --prompt-credentials

Ref: https://developers.facebook.com/docs/threads/get-started/get-access-tokens-and-permissions
"""

import argparse
from getpass import getpass

import requests

from src.authentication import get_gcp_secret

PROJECT_ID = "gds-social-data"
APP_ID_SECRET = "threads_app_id"
APP_SECRET_SECRET = "threads_app_secret"

GRAPH_HOST = "https://graph.threads.net"

# Meta issues two app ID/secret pairs per app — the Meta one and the Threads one — and they are
# indistinguishable by shape (16-digit ID, 32-char hex secret). Only the Threads pair works here.
# Defined in this module rather than the quickstart because the quickstart imports from it, not
# the other way round.
WRONG_CLIENT_SECRET_SUBCODE = 4279023


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mint a Threads app access token")
    parser.add_argument(
        "--prompt-credentials",
        action="store_true",
        help="Type the Threads app ID/secret in by hand instead of reading Secret Manager.",
    )
    parser.add_argument(
        "--show-token",
        action="store_true",
        help=(
            "Print the token in full. Off by default — its tail is the app secret's authority, and it "
            "is re-derivable from the credentials at any time, so there is rarely a reason to copy it."
        ),
    )
    return parser.parse_args()


def load_credentials(prompt: bool) -> tuple[str, str]:
    """Shared by this script and threads_oauth_quickstart.py, which imports it."""
    if prompt:
        app_id = input("Threads app ID: ").strip()
        app_secret = getpass("Threads app secret (not echoed): ").strip()
    else:
        print("Fetching app credentials from Secret Manager...")
        app_id = get_gcp_secret(APP_ID_SECRET, project_id=PROJECT_ID).strip()
        app_secret = get_gcp_secret(APP_SECRET_SECRET, project_id=PROJECT_ID).strip()
    print(f"  Using Threads app ID {app_id} — check this against the dashboard's *Threads* App ID.")
    return app_id, app_secret


def fetch_app_access_token(app_id: str, app_secret: str) -> dict:
    """Return the raw client_credentials response, e.g. {"access_token": "TH|...", "token_type": "bearer"}.

    Raises SystemExit rather than HTTPError so the quickstart reports a readable line instead of a
    traceback — a failure here means the ID/secret pair itself is wrong, which is a user problem.
    """
    response = requests.get(
        f"{GRAPH_HOST}/oauth/access_token",
        params={
            "grant_type": "client_credentials",
            "client_id": app_id,
            "client_secret": app_secret,
        },
        timeout=30,
    )
    if response.ok:
        return response.json()

    hint = ""
    if response.json().get("error", {}).get("error_subcode") == WRONG_CLIENT_SECRET_SUBCODE:
        hint = (
            "  Subcode 4279023 is Meta rejecting the client secret specifically. Meta issues TWO app "
            "ID/secret pairs per app and only the Threads pair works; both look identical (16-digit "
            "ID, 32-char hex secret), so you cannot tell them apart by eye.\n"
        )
    raise SystemExit(
        f"Could not mint an app access token for app {app_id} ({response.status_code}): {response.text}\n"
        "\nThis pairing is rejected before any user token is involved, so the ID and secret do not "
        f"belong together.\n{hint}"
        "  Dashboard -> Use cases -> 'Access the Threads API' -> Customize -> Settings.\n"
        "  Re-run with --prompt-credentials to test a pair before writing it to Secret Manager."
    )


def check_token_shape(token: str, app_id: str) -> None:
    """Documented format is TH|<APP_ID>|<TOKEN>; a mismatched app id means the wrong app answered."""
    parts = token.split("|")
    if len(parts) != 3 or parts[0] != "TH":
        print(f"  WARNING: token is not in the documented 'TH|<APP_ID>|<TOKEN>' form (got {len(parts)} parts).")
        return
    if parts[1] != app_id:
        print(f"  WARNING: token is scoped to app {parts[1]}, not the {app_id} it was requested for.")
        return
    print(f"  Token is well-formed and scoped to app {parts[1]}.")


def main() -> None:
    args = parse_args()

    app_id, app_secret = load_credentials(args.prompt_credentials)

    print("Requesting an app access token (grant_type=client_credentials)...")
    payload = fetch_app_access_token(app_id, app_secret)
    token = payload["access_token"]

    print(f"\nApp access token issued (token_type={payload.get('token_type')}).")
    check_token_shape(token, app_id)
    print(f"\n'{APP_ID_SECRET}' and '{APP_SECRET_SECRET}' are a valid Threads pair.")

    if args.show_token:
        print(f"\n{token}")
    else:
        # Masks the secret half — the tail of the token IS a credential.
        print(f"\n  {token.split('|')[1]}|{'*' * 8}   (--show-token to print it in full)")

    print(
        "\nReminder: this is an APP token. It carries no user permissions and cannot read "
        "/threads_insights or /replies — do not store it as 'threads_access_token'."
    )


if __name__ == "__main__":
    main()
