# =============================================================================
# One-off OAuth refresh token generator — YouTube Analytics API
# Run cell-by-cell in a BigQuery Colab (or any hosted Jupyter) notebook.
#
# Pre-requisite: in GCP → APIs & Services → Credentials, open your web OAuth
# client and add  http://localhost:8080/  to "Authorised redirect URIs".
# (Google allows localhost redirect URIs for web clients during development.)
#
# How it works (no local server required):
#   Cell 1 – fetches secrets and prints the auth URL.
#   Open that URL in your LOCAL browser and complete sign-in.
#   Your browser will redirect to http://localhost:8080/?code=<AUTH_CODE>&…
#   The page won't load (nothing is listening) — that is expected.
#   Copy ONLY the value of the "code=" parameter from the address bar.
#   Cell 2 – paste the code, exchange it for tokens, write credentials.json,
#             and store the refresh token in Secret Manager.
# =============================================================================

# ── Cell 1: imports & config ──────────────────────────────────────────────────
import json
from google_auth_oauthlib.flow import Flow
from google.cloud import secretmanager
from google.api_core import exceptions

PROJECT_ID = "1031179907628"
SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly"]
CLIENT_ID_STRING_SECRET = "youtube-processing-client-id-web-app"
CLIENT_SECRET_STRING_SECRET = "youtube-processing-client-secret-web-app"
REDIRECT_URI = "http://localhost:8080/"   # must match GCP console
CREDENTIALS_OUTPUT_PATH = "credentials.json"


def get_gcp_secret(secret_id, project_id=PROJECT_ID):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    return client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")


def upload_gcp_secret(secret_id, payload, project_id=PROJECT_ID):
    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{project_id}"
    secret_path = f"{parent}/secrets/{secret_id}"
    try:
        client.get_secret(request={"name": secret_path})
    except exceptions.NotFound:
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            }
        )
    client.add_secret_version(
        request={"parent": secret_path, "payload": {"data": payload.encode("utf-8")}}
    )
    print(f"  Uploaded '{secret_id}' to Secret Manager.")


# ── Cell 2: build auth URL ────────────────────────────────────────────────────
print("Fetching client credentials from Secret Manager...")
client_id = get_gcp_secret(CLIENT_ID_STRING_SECRET)
client_secret = get_gcp_secret(CLIENT_SECRET_STRING_SECRET)

client_config = {
    "web": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT_URI],
    }
}

flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)
auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")

print("\nOpen this URL in your LOCAL browser and sign in:\n")
print(auth_url)
print(
    "\nAfter authorising, your browser will redirect to http://localhost:8080/"
    "\nThe page will fail to load — that is expected."
    "\nLook at your browser's address bar and copy ONLY the value of the 'code=' parameter."
    "\ne.g. from:  http://localhost:8080/?code=4/0Ab...xyz&scope=..."
    "\ncopy just:  4/0Ab...xyz"
    "\n\nThen run the next cell and paste it when prompted."
)


# ── Cell 3: exchange code → tokens, write credentials.json ───────────────────
auth_code = input("Paste the 'code' value from the redirect URL: ").strip()

flow.fetch_token(code=auth_code)
creds = flow.credentials

if not creds.refresh_token:
    raise RuntimeError(
        "No refresh token returned. "
        "Ensure prompt='consent' and access_type='offline' were set, "
        "and that you completed the full sign-in."
    )

creds_data = {
    "token": creds.token,
    "refresh_token": creds.refresh_token,
    "token_uri": creds.token_uri,
    "client_id": creds.client_id,
    "client_secret": creds.client_secret,
    "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    "expiry": creds.expiry.isoformat() if creds.expiry else None,
}

with open(CREDENTIALS_OUTPUT_PATH, "w") as f:
    json.dump(creds_data, f, indent=2)
print(f"  credentials.json written → {CREDENTIALS_OUTPUT_PATH}")

upload_gcp_secret("youtube_refresh_token_testing_env", creds.refresh_token)
print("\nDone. Access token and weekly refresh token are ready.")