"""Access-token retrieval for the Meta Graph API pipelines.

Each platform stores its long-lived token in Secret Manager, but not in the same shape:
Facebook's `facebook_page_token` is JSON (`{"access_token": "..."}`), Instagram's
`instagram_bearer_token` is a raw string. Facebook also consults a local file cache first.
This covers both, and Threads later, without a third copy of the same logic.
"""

import json
import logging

from src.authentication import FileTokenCache, get_gcp_secret

logger = logging.getLogger(__name__)


def get_graph_token(
    secret_name: str,
    *,
    json_wrapped: bool = False,
    cache: FileTokenCache | None = None,
) -> str:
    """Return a Graph API access token from Secret Manager, optionally via a file cache.

    Args:
        secret_name: Secret Manager secret holding the token.
        json_wrapped: True if the secret is JSON of the form {"access_token": "..."}
            (Facebook); False if it is a bare token string (Instagram).
        cache: Optional FileTokenCache consulted before Secret Manager. An expired entry is
            treated as absent, so callers get a cold read rather than a stale token.

    Returns:
        The access token string.
    """
    if cache is not None:
        cached = cache.load_valid()
        if cached:
            logger.info("Using cached access token from %s", cache.path)
            return cached["access_token"]

    def _read(secret_version: str = "latest") -> str:
        raw = get_gcp_secret(secret_name, secret_version)
        return json.loads(raw)["access_token"] if json_wrapped else raw.strip()

    # Retained from facebook/api.py, including the parse sitting INSIDE the try: a malformed
    # JSON payload or a missing access_token key is caught here and logged, not just a Secret
    # Manager failure. Note get_gcp_secret already defaults secret_version="latest", so the
    # retry requests the identical version and cannot succeed where the first attempt failed.
    #
    # Reproduces Facebook's flow exactly. Instagram had no retry, so it gains one: on an
    # already-failing path that costs one extra doomed call and a warning line. Removing the
    # retry for both is a separate, deliberate change.
    try:
        return _read()
    except Exception:
        logger.warning("Token refresh failed, retrying with latest secret version")
        return _read("latest")
