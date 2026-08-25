"""HTTP transport for the Meta Graph API, shared by every platform client.

The Graph API is a node/edge graph: a node (page, account, video, media item) is fetched by
id, and its edges (posts, videos, insights) hang off it. Every platform client is therefore
the same three operations — fetch one node, walk a paginated edge, fetch an insights edge —
differing only in base URL, metric constants and model builders.

    PlatformClient(GraphClient)
    |
    |-- <edge listings>     _paginate              _get
    |-- <single node>                              _get
    |-- <insight edges>     _fetch_insight_items   _get

Subclasses set BASE_URL and nothing else; none of these methods is intended to be overridden.

This module came from facebook/api.py, which was the only client with a worked-out failure
process. It is behaviour-identical for Facebook, established two ways: _fetch_insight_items is
unchanged from its source, while _get and _paginate were edited and their equivalence rests on
case analysis instead —

  _get       BASE URL is now self.BASE_URL, and an absolute URL is passed through untouched.
             Facebook never passes one, so only the first edit is reachable for it.
  _paginate  next-page resolution moved into _next_page, which additionally falls back to the
             absolute paging.next link on edges that page without cursors. Facebook's /posts
             and /videos always return both, so they always take the cursor branch; see
             _next_page for the one shape that differs and why it is safe.

Some rough edges were carried across deliberately rather than fixed here, so this module
changes no behaviour: _get sleeps on its final attempt before exiting the loop, and
_fetch_insight_items re-issues an identical request when metric_names holds a single entry.
"""

import logging
import time
from http import HTTPStatus

import requests
from requests.exceptions import HTTPError

from src.meta.errors import InsightMetricError, PaginationError

logger = logging.getLogger(__name__)


class GraphClient:
    """Base client for a Meta Graph API surface.

    Subclasses set BASE_URL, e.g. "https://graph.facebook.com/v25.0" for Facebook and
    Instagram, or "https://graph.threads.net/v1.0" for Threads — which is why this is a class
    attribute rather than a module constant.
    """

    BASE_URL: str = ""

    def __init__(self, access_token: str) -> None:
        self._session = requests.Session()
        self._session.params = {"access_token": access_token}  # type: ignore[assignment]

    def _get(self, path: str, params: dict | None = None, retries: int = 3) -> dict:
        # An absolute URL is passed through untouched: _next_page falls back to the absolute
        # paging.next link on edges that do not return cursors. Without this guard the base
        # would be prefixed onto it, producing a syntactically valid but nonsense URL that
        # Meta answers with a 400 — which _paginate would swallow into a `causes` entry.
        url = path if path.startswith("http") else f"{self.BASE_URL}/{path.lstrip('/')}"
        # 429 gets its own fixed 60s cooldown (Graph API rate-limit window), separate from the
        # exponential backoff used for transient 5xx errors and network failures below.
        retry_codes = [
            HTTPStatus.INTERNAL_SERVER_ERROR,
            HTTPStatus.BAD_GATEWAY,
            HTTPStatus.SERVICE_UNAVAILABLE,
            HTTPStatus.GATEWAY_TIMEOUT,
        ]

        last_exception: Exception | None = None
        for attempt in range(retries):
            wait = 2**attempt
            try:
                response = self._session.get(url, params=params or {}, timeout=30)
                response.raise_for_status()
                return response.json()
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exception = exc
                logger.warning("Request error for path %s, params %s, retrying in %ds: %s", path, params, wait, exc)
                time.sleep(wait)
            except HTTPError as exc:
                last_exception = exc
                code = exc.response.status_code
                if code == HTTPStatus.TOO_MANY_REQUESTS:
                    logger.warning("Rate limited for path %s, params %s, waiting 60s", path, params)
                    time.sleep(60)
                elif code in retry_codes:
                    logger.warning("HTTP %d for path %s, params %s, retrying in %ds", code, path, params, wait)
                    time.sleep(wait)
                else:
                    raise

        raise last_exception

    def _paginate(
        self, path: str, params: dict | None = None, max_results: int | None = None, raise_on_failure: bool = False
    ) -> list[dict]:
        """
        Given a target path and params, will query the endpoint which returns an object with paging and data
        objects.

        Parameters:
            path: Endpoint being queried
            params: fields being requested as part of the http call to the endpoint
            max_results: the maximum number of results that can be returned; passing None
                ensures all results are returned
            raise_on_failure: when True a failure raises PaginationError carrying the pages
                collected so far, instead of returning them silently

        Other:
            https://developers.facebook.com/docs/graph-api/results/
            Endpoint query returns an object with Data and Paging mappings
                Data json object:
                    The insights for the video/posts node/path, fields

                Paging json object:
                    Cursors object:
                        before : cursor pointing to the start of the returned page - hash value
                        after : cursor pointing to the end of the returned page - hash value

                    next : The Graph API endpoint that will return the next page of data. If not included,
                        this is the last page of data e.g.
                        "https://graph.facebook.com/{your-user-id}/albums?limit=25&before=NDMyNzQyODI3OTQw"
                    previous : The Graph API endpoint that will return the previous page of data. If not included,
                        this is the first page of data

        Returns:
            results: A list of data objects, provided by all the endpoint calls for the given ids and params

        Pages are walked by re-requesting `path` with the `after` cursor where one is offered,
        so the caller's own fields/limit keep applying rather than whatever Meta baked into the
        absolute next link. See _next_page for the fallback used by edges without cursors.

        Every page request is recorded, and a repeat stops the walk. `max_results` defaults to
        None in production, so without this a response whose paging points back at itself — a
        self-referential next link, or a cursor that stops advancing — would re-request the same
        page forever while `results` grew unbounded.
        """
        results: list[dict] = []
        causes: list[dict] = []
        requested: set[str] = set()
        try:
            paging_json_object = self._get(path, params)  # Returns the json containing data and paging objects
            results.extend(paging_json_object.get("data", []))  # gets the data object for the first object
            logger.debug(f"Data retrieved from endpoint {path} with params {params}")
        except Exception as e:
            logger.warning(f"Failed to return endpoint {path} with params {params}, error {e}")
            causes.append({"exception": e, "path": path, "params": params})
            if raise_on_failure:
                raise PaginationError(path, params, causes, results)
            return results

        while max_results is None or len(results) < max_results:
            paging = paging_json_object.get("paging", {})  # returns paging object, which contains the cursor
            next_page = self._next_page(paging, path, params)
            if next_page is None:
                break
            next_path, next_params = next_page
            fingerprint = f"{next_path}?{sorted((next_params or {}).items(), key=lambda kv: kv[0])}"
            if fingerprint in requested:
                logger.warning(
                    "Pagination for %s pointed back at an already-requested page after %d result(s); "
                    "stopping to avoid an unbounded loop",
                    path,
                    len(results),
                )
                break
            requested.add(fingerprint)
            try:
                paging_json_object = self._get(next_path, next_params)
            except Exception as e:
                logger.warning(
                    "Pagination failed for %s after %d result(s); stopping early: %s", path, len(results), e
                )
                causes.append({"exception": e, "path": path, "params": params})
                if raise_on_failure:
                    raise PaginationError(path, params, causes, results)
                break
            page_data = paging_json_object.get("data", [])
            # An empty page ends the walk. Without this a page that returns no data but still
            # advertises a `next` link loops forever: `results` stops growing, so `max_results`
            # cannot bound it, and the fingerprint guard above does not fire because each link
            # (or each advancing cursor) is distinct. Meta ends an edge with no `next`, so an
            # empty page carrying one is already anomalous and there is nothing left to collect.
            if not page_data:
                break
            results.extend(page_data)

        return results[:max_results] if max_results is not None else results

    def _next_page(self, paging: dict, path: str, params: dict | None) -> tuple[str, dict | None] | None:
        """Resolve the request for the page after `paging`, or None if this was the last one.

        Prefers the `after` cursor, re-requesting our own `path` and `params`, so the caller's
        fields/limit always apply rather than whatever Meta baked into the absolute next link.
        Falls back to that absolute link for edges that page without cursors — some insights
        edges use time-based paging — because cursor-only would stop after page one there, and
        combined with per-partition WRITE_TRUNCATE that silently replaces a full table with a
        single page.

        Absence of `next` is the authoritative "last page" signal on every Meta edge.

        Facebook's /posts and /videos always return both `next` and `cursors.after`, so it
        always takes the cursor branch. The one case that differs from the pre-extraction loop
        is `next` present with `cursors.after` absent: that loop stopped, this one follows the
        link. Unreachable on those two edges, and were it ever reached it fetches more data
        rather than less.
        """
        next_url = paging.get("next")
        if not next_url:
            return None
        after_cursor = paging.get("cursors", {}).get("after")
        if after_cursor:
            return path, {**(params or {}), "after": after_cursor}
        return next_url, None

    def _fetch_insight_items(
        self, path: str, metric_names: list[str], params: dict, raise_on_failure: bool = False
    ) -> list[dict]:
        """Fetches insight items, falling back to per-metric requests on a 400.

        The Graph API rejects the entire batch if any single metric is unavailable
        for the page (e.g. follower metrics require specific permissions). Retrying
        individually lets us collect what is accessible and skip what is not.

        If raise_on_failure is True, any metric(s) that still fail after the per-metric
        retry raise InsightMetricError (naming the specific metric and cause) instead of
        being silently skipped. The metrics that did succeed are still returned/attached.
        """
        try:
            data = self._get(path, {**params, "metric": ",".join(metric_names)})
            return data.get("data", [])
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 400:
                raise
            logger.warning("Batch insights request returned 400, retrying per metric")

        items: list[dict] = []
        skipped: list[str] = []
        causes: dict[str, Exception] = {}
        for metric in metric_names:
            try:
                data = self._get(path, {**params, "metric": metric})
                items.extend(data.get("data", []))
            except requests.HTTPError as e:
                skipped.append(metric)
                causes[metric] = e
        if skipped:
            if raise_on_failure:
                logger.error("Metric(s) failed for %s: %s", path, ", ".join(skipped))
                raise InsightMetricError(path, skipped, causes, items)
            logger.warning("Skipped %d unavailable metric(s): %s", len(skipped), ", ".join(skipped))
        return items
