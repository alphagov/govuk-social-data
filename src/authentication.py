import json
import logging
import os
from datetime import datetime

from google.cloud import secretmanager

logger = logging.getLogger(__name__)

DEFAULT_PROJECT_ID = "gds-social-data"


def get_gcp_secret(secret_name: str, secret_version: str = "latest", project_id: str = DEFAULT_PROJECT_ID) -> str:
    """Retrieves a secret value from GCP Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/{secret_version}"
    return client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")


class FileTokenCache:
    """
    Generic file-based JSON token cache.

    Stores an arbitrary dict of token data. If the dict contains an 'expires_at' key
    (ISO-format datetime string), it is used for expiry checks.

    Use load() when the caller wants to handle expiry itself (e.g. Google OAuth2
    which can refresh an expired token via a refresh_token).
    Use load_valid() when an expired token should be treated as absent (e.g. Facebook
    long-lived tokens that require a full cold-start on expiry).
    """

    def __init__(self, cache_path: str) -> None:
        self.path = os.path.expanduser(cache_path)

    def load(self) -> dict | None:
        """Returns cached data if the file exists, regardless of expiry."""
        if not os.path.exists(self.path):
            return None
        with open(self.path) as f:
            return json.load(f)

    def load_valid(self) -> dict | None:
        """Returns cached data only if present and not expired. Returns None if expired."""
        data = self.load()
        if data is None:
            return None
        expires_at = data.get("expires_at")
        if expires_at and datetime.fromisoformat(expires_at) < datetime.now():
            logger.info("Cached token at %s has expired", self.path)
            return None
        return data

    def save(self, data: dict) -> None:
        """Persists token data dict to the cache file."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(data, f)
