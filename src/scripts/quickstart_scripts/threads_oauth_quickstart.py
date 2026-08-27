"""One-off flow to mint a long-lived Threads access token.

The Threads pipeline reads its token from the `threads_access_token` secret, but nothing can create
that token automatically: `threads_app_id`/`threads_app_secret` identify the app, not the user, and
`/{threads-user-id}/threads_insights` needs a *user* token carrying `threads_basic`,
`threads_manage_insights` and `threads_read_replies`. That token can only come from a human.

https://developers.facebook.com/docs/threads/get-started/get-access-tokens-and-permissions

Using graph explorer tool - https://developers.facebook.com/tools/explorer/
      Meta for Developers -> your app -> Use cases -> "Access the Threads API" -> Customize ->
      Settings -> "Generate Threads Access Token". Pick the Threads account, and paste what it
      prints. No redirect URI involved, which is why this is the default (see below).

Run:
    poetry run python -m src.scripts.quickstart_scripts.threads_oauth_quickstart

Then store the printed token as documented at the end of the run. The token expires 60 days later and
must be refreshed before then by threads_refresh_token.py, or it dies permanently.
"""

import argparse
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from src.scripts.quickstart_scripts.threads_app_access_token import (
    WRONG_CLIENT_SECRET_SUBCODE,
    fetch_app_access_token,
    load_credentials,
)

PROJECT_ID = "gds-social-data"
APP_ID_SECRET = "threads_app_id"
APP_SECRET_SECRET = "threads_app_secret"
TOKEN_SECRET = "threads_long_lived_access_token"

AUTHORIZE_URL = "https://threads.net/oauth/authorize"
GRAPH_HOST = "https://graph.threads.net"

# threads_basic is required by every endpoint; the other two gate the edges this pipeline reads.
# Asking for less means /threads_insights and /replies 400 at runtime rather than here.
SCOPES = ["threads_basic", "threads_manage_insights", "threads_read_replies"]

# Deliberately not a localhost default: Threads rejects localhost redirects outright, so a default
# that looks plausible but can never work just sends people round the 1349168 loop again.
DEFAULT_REDIRECT_URI = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mint a long-lived Threads access token")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--from-dashboard",
        action="store_true",
        default=True,
        help="Paste a token from the app dashboard's 'Generate Threads Access Token' button (default).",
    )
    mode.add_argument(
        "--authorize",
        action="store_true",
        help="Run the full authorization window instead. Requires --redirect-uri on a real HTTPS domain.",
    )
    parser.add_argument(
        "--redirect-uri",
        default=DEFAULT_REDIRECT_URI,
        help=(
            "Redirect URI for --authorize. Must exactly match a Redirect Callback URL configured on "
            "the Threads app, and must be a publicly resolvable HTTPS URL — localhost is rejected."
        ),
    )
    parser.add_argument(
        "--prompt-credentials",
        action="store_true",
        help=(
            "Type the Threads app ID/secret in by hand instead of reading Secret Manager. Use this to "
            "test a corrected pair before writing it to Secret Manager."
        ),
    )
    parser.add_argument(
        "--scopes",
        default=",".join(SCOPES),
        help=f"Comma-separated scopes to request (default: {','.join(SCOPES)}).",
    )
    return parser.parse_args()


def build_authorize_url(app_id: str, redirect_uri: str, scopes: str) -> str:
    return f"{AUTHORIZE_URL}?" + urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "response_type": "code",
        }
    )


def extract_code(pasted: str) -> str:
    """Accept either a bare code or the whole failed-to-load redirect URL copied from the address bar.

    Meta appends '#_' to the redirect; it is not part of the code and the exchange rejects it.
    """
    pasted = pasted.strip()
    if pasted.startswith("http://") or pasted.startswith("https://"):
        codes = parse_qs(urlparse(pasted).query).get("code")
        if not codes:
            raise SystemExit(f"No 'code' parameter in the pasted URL: {pasted}")
        pasted = codes[0]
    return pasted.removesuffix("#_")


def describe_token(token: str) -> dict:
    """Confirm the token authenticates at all, before spending the exchange on it."""
    response = requests.get(f"{GRAPH_HOST}/v1.0/me", params={"fields": "id,username", "access_token": token})
    if not response.ok:
        raise SystemExit(f"Token rejected by /me ({response.status_code}): {response.text}")
    return response.json()


def exchange_code_for_short_lived_token(app_id: str, app_secret: str, redirect_uri: str, code: str) -> str:
    """Swap the one-time authorization code for a short-lived (1 hour) user token."""
    response = requests.post(
        f"{GRAPH_HOST}/oauth/access_token",
        data={
            "client_id": app_id,
            "client_secret": app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=30,
    )
    if not response.ok:
        raise SystemExit(f"Code exchange failed ({response.status_code}): {response.text}")
    payload = response.json()
    print(f"  Short-lived token issued for user_id {payload.get('user_id')}")
    return payload["access_token"]


def exchange_for_long_lived_token(app_secret: str, short_lived_token: str) -> requests.Response:
    """Upgrade a short-lived token to a 60-day long-lived one. Server-side only — sends the app secret."""
    return requests.get(
        f"{GRAPH_HOST}/access_token",
        params={
            "grant_type": "th_exchange_token",
            "client_secret": app_secret,
            "access_token": short_lived_token,
        },
        timeout=30,
    )


def refresh_long_lived_token(token: str) -> requests.Response:
    """Reset an already-long-lived token's 60-day clock. Takes the token itself, not the app secret."""
    return requests.get(
        f"{GRAPH_HOST}/refresh_access_token",
        params={"grant_type": "th_refresh_token", "access_token": token},
        timeout=30,
    )


def assert_token_matches_app(app_id: str, app_secret: str, token: str) -> None:
    """Fail early when the token was minted by a different Threads app than the stored credentials.

    th_exchange_token validates the app secret against the token's *issuing* app, so a token from
    another app reports "Error while validating client secret" (subcode 4279023) even when the stored
    secret is perfectly correct. /me does not catch this — it only validates the token itself. Asking
    debug_token to inspect the token as this app turns that into a message that names the real problem.
    """
    app_token = fetch_app_access_token(app_id, app_secret)["access_token"]
    response = requests.get(
        f"{GRAPH_HOST}/debug_token",
        params={"input_token": token, "access_token": app_token},
        timeout=30,
    )
    if response.ok:
        print(f"  Token was issued by app {response.json().get('data', {}).get('app_id', app_id)} — matches.")
        return
    if "did not match the Viewing App" in response.text:
        raise SystemExit(
            f"This token was issued by a different Threads app than {app_id}.\n"
            "\nThe app secret in Secret Manager is valid (it minted an app access token fine), but "
            "th_exchange_token checks it against the app that issued the token, so this pairing can "
            "never work. Fix whichever end is wrong:\n"
            f"  - generate the token from app {app_id}'s own 'Generate Threads Access Token' button, or\n"
            f"  - if the other app is the intended one, update '{APP_ID_SECRET}'/'{APP_SECRET_SECRET}' "
            "to its Threads pair (--prompt-credentials tests a pair without storing it)."
        )
    print(f"  WARNING: could not verify the token's issuing app ({response.status_code}): {response.text}")


def to_long_lived(app_secret: str, token: str) -> dict:
    """Return a 60-day token whether the pasted one was short- or already long-lived.

    The dashboard's generator and the authorization window do not always hand back the same kind of
    token, and there is no field on the token saying which it is. th_exchange_token only accepts a
    short-lived one; th_refresh_token only accepts a long-lived one. Try the first, fall back to the
    second, and let the caller see both errors if neither applies.
    """
    exchanged = exchange_for_long_lived_token(app_secret, token)
    if exchanged.ok:
        return exchanged.json()

    if exchanged.json().get("error", {}).get("error_subcode") == WRONG_CLIENT_SECRET_SUBCODE:
        raise SystemExit(
            f"th_exchange_token rejected the app secret ({exchanged.status_code}): {exchanged.text}\n"
            "\nSubcode 4279023 means the client secret did not validate against the token's issuing "
            "app. The usual cause is a token from a different app, which assert_token_matches_app "
            "should have caught first — so suspect the credentials themselves. Meta issues TWO app ID/"
            "secret pairs per app and only the Threads pair works; both look identical (16-digit ID, "
            "32-char hex secret).\n"
            "  Dashboard -> Use cases -> 'Access the Threads API' -> Customize -> Settings.\n"
            f"  Re-run with --prompt-credentials to test a pair before storing it in "
            f"'{APP_ID_SECRET}'/'{APP_SECRET_SECRET}'."
        )

    print(f"  th_exchange_token refused it ({exchanged.status_code}); trying th_refresh_token instead.")
    refreshed = refresh_long_lived_token(token)
    if refreshed.ok:
        return refreshed.json()

    raise SystemExit(
        "Neither exchange worked — the token is not a usable Threads user token.\n"
        f"  th_exchange_token ({exchanged.status_code}): {exchanged.text}\n"
        f"  th_refresh_token  ({refreshed.status_code}): {refreshed.text}"
    )


def collect_token_via_authorize(app_id: str, app_secret: str, redirect_uri: str, scopes: str) -> str:
    if not redirect_uri:
        raise SystemExit("--authorize needs --redirect-uri (a whitelisted HTTPS URL; localhost is rejected).")
    if urlparse(redirect_uri).hostname in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit(f"Threads rejects localhost redirect URIs — {redirect_uri} will fail with error 1349168.")

    print(
        "\nOpen this in a browser (not curl) while logged in as the Threads account, and tap Allow:\n"
        f"\n    {build_authorize_url(app_id, redirect_uri, scopes)}\n"
        f"\nYou will land on {redirect_uri} with a ?code=... parameter. Copy the whole address bar."
    )
    code = extract_code(input("\nPaste the redirect URL (or just the code): "))
    if not code:
        raise SystemExit("No authorization code supplied — nothing to exchange.")

    print("\nExchanging the code for a short-lived token...")
    return exchange_code_for_short_lived_token(app_id, app_secret, redirect_uri, code)


def collect_token_from_dashboard() -> str:
    print(
        "\nIn the app dashboard: Use cases -> 'Access the Threads API' -> Customize -> Settings ->\n"
        "'Generate Threads Access Token'. Pick the Threads account and approve."
    )
    token = input("\nPaste the generated access token: ").strip()
    if not token:
        raise SystemExit("No access token supplied — nothing to exchange.")
    return token


def main() -> None:
    args = parse_args()

    app_id, app_secret = load_credentials(args.prompt_credentials)

    if args.authorize:
        token = collect_token_via_authorize(app_id, app_secret, args.redirect_uri, args.scopes)
    else:
        token = collect_token_from_dashboard()

    whoami = describe_token(token)
    print(f"\nToken belongs to @{whoami.get('username')} (id {whoami.get('id')}).")

    print("Checking the token was issued by this app...")
    assert_token_matches_app(app_id, app_secret, token)

    print("Obtaining a long-lived token...")
    long_lived = to_long_lived(app_secret, token)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(long_lived["expires_in"]))

    print(f"\nLong-lived token issued. Expires {expires_at:%Y-%m-%d} ({long_lived['expires_in']}s).\n")
    print(long_lived["access_token"])
    print(
        f"\nStore it as the '{TOKEN_SECRET}' secret:\n"
        f"\n    printf %s '<the token above>' | \\\n"
        f"      gcloud secrets create {TOKEN_SECRET} --project={PROJECT_ID} --data-file=-\n"
        f"\n(if the secret already exists, use 'versions add' instead of 'create')\n"
        f"\nDiarise a run of threads_refresh_token.py before {expires_at:%Y-%m-%d} — a long-lived token "
        "that goes 60 days without a refresh expires permanently and this whole flow has to be redone."
    )


if __name__ == "__main__":
    main()
