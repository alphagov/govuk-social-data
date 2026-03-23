# -*- coding: utf-8 -*-

import json
import logging
import os
from datetime import datetime
from google.cloud import secretmanager

import requests
from pyfacebook import GraphAPI

logger = logging.getLogger(__name__)

# Facebook OAuth2 endpoints
FB_GRAPH_API_BASE = "https://graph.facebook.com/v25.0"
FB_TOKEN_EXCHANGE_URL = f"{FB_GRAPH_API_BASE}/oauth/access_token"

# GCP + Facebook secret names
PROJECT_ID = "gds-social-data"
APP_ID_SECRET = "facebook_app_id"
APP_SECRET_SECRET = "facebook_app_secret"
TOKEN_JSON_SECRET = "facebook_long_live_token_json"
TOKEN_SECRET_VERSION = "latest"
PAGE_ID_SECRET = "facebook_page_id"

# Local cache for the access token
TOKEN_CACHE_PATH = os.path.expanduser("~/.config/facebook_exploration/token_cache.json")


def get_gcp_secret(secret_name: str, secret_version: str = "latest", project_id: str = PROJECT_ID) -> str:
    """Retrieves a secret value from GCP Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/{secret_version}"
    payload = client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")
    return payload


def _save_token_cache(access_token: str, expires_at: str | None = None) -> None:
    """Persists the Facebook access token to a local cache file."""
    os.makedirs(os.path.dirname(TOKEN_CACHE_PATH), exist_ok=True)
    with open(TOKEN_CACHE_PATH, "w") as f:
        json.dump({
            "access_token": access_token,
            "expires_at": expires_at,
        }, f)


def _load_token_cache() -> str | None:
    """Loads a cached Facebook access token if it exists and hasn't expired."""
    if not os.path.exists(TOKEN_CACHE_PATH):
        return None
    with open(TOKEN_CACHE_PATH) as f:
        data = json.load(f)
    expires_at = data.get("expires_at")
    if expires_at and datetime.fromisoformat(expires_at) < datetime.now():
        logger.info("Cached token has expired")
        return None
    return data.get("access_token")


def _refresh_access_token(app_id: str, app_secret: str, long_lived_token: str) -> str:
    """Exchanges a long-lived token for a new access token via the Facebook Graph API.

    Returns the new access token string.
    """
    response = requests.get(
        FB_TOKEN_EXCHANGE_URL,
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": long_lived_token,
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    access_token = data["access_token"]
    expires_in = data.get("expires_in")
    expires_at = None
    if expires_in:
        expires_at = datetime.fromtimestamp(
            datetime.now().timestamp() + expires_in
        ).isoformat()

    _save_token_cache(access_token, expires_at)
    logger.info("Refreshed access token (expires_in=%s seconds)", expires_in)
    return access_token


def _build_service() -> GraphAPI:
    """Builds an authenticated Facebook GraphAPI client.
    """
    cached_token = _load_token_cache()
    if cached_token:
        logger.info("Using cached access token")
        return GraphAPI(access_token=cached_token)

    # Cold start: pull from Secret Manager
    app_id = get_gcp_secret(APP_ID_SECRET)
    app_secret = get_gcp_secret(APP_SECRET_SECRET)
    token_info = json.loads(get_gcp_secret(TOKEN_JSON_SECRET, TOKEN_SECRET_VERSION))
    long_lived_token = token_info["access_token"]

    try:
        access_token = _refresh_access_token(app_id, app_secret, long_lived_token)
    except Exception:
        logger.warning(
            "Refresh failed with secret version %s, retrying with latest version",
            TOKEN_SECRET_VERSION,
        )
        token_info = json.loads(get_gcp_secret(TOKEN_JSON_SECRET, "latest"))
        long_lived_token = token_info["access_token"]
        access_token = _refresh_access_token(app_id, app_secret, long_lived_token)

    return GraphAPI(access_token=access_token)


def get_facebook_page_info(api: GraphAPI, page_id: str) -> dict:
    """Fetches basic page/user info using pyfacebook GraphAPI."""
    return api.get_object(
        object_id=page_id,
        fields="id,name,about,fan_count,category",
    )


def get_page_posts(api: GraphAPI, page_id: str, limit: int = 10) -> list[dict]:
    """Fetches recent posts from a Facebook page using pyfacebook GraphAPI."""
    response = api.get_connection(
        object_id=page_id,
        connection="posts",
        count=limit,
        fields="id,message,created_time,shares,likes.summary(true),comments.summary(true)",
    )
    return response.get("data", [])


def get_post_insights(api: GraphAPI, post_id: str) -> list[dict]:
    """Fetches engagement insights for a specific post using pyfacebook GraphAPI."""
    response = api.get_connection(
        object_id=post_id,
        connection="insights",
        metric="post_impressions,post_engaged_users,post_clicks,post_reactions_by_type_total",
    )
    return response.get("data", [])


def main() -> None:
    """Entry point: builds the service and queries the Facebook Graph API."""
    logging.basicConfig(level=logging.INFO)

    try:
        api = _build_service()
        logger.info("Authenticated successfully")

        page_id = get_gcp_secret(PAGE_ID_SECRET)

        page_info = get_facebook_page_info(api, page_id)
        logger.info("Page info: %s", json.dumps(page_info, indent=2))

        # # Fetch recent posts
        # posts = get_page_posts(api, page_id, limit=5)
        # for post in posts:
        #     logger.info("Post %s: %s", post.get("id"), post.get("message", "")[:80])

        #     # Fetch insights per post
        #     try:
        #         insights = get_post_insights(api, post["id"])
        #         for insight in insights:
        #             logger.info("  %s: %s", insight.get("name"), insight.get("values"))
        #     except Exception as e:
        #         logger.warning("  Could not fetch insights for %s: %s", post["id"], e)

    except Exception:
        logger.exception("Pipeline failed")
        raise


if __name__ == "__main__":
    main()
