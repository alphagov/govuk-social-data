"""Error types for partial-failure handling against the Meta Graph API.

Both carry the data that DID succeed (`items`) plus a `partial_result` the caller attaches
before re-raising. The split exists because the low-level helpers only ever see raw dicts —
the caller owns model construction, so it is the caller that can populate `partial_result`
with fully-built objects of the same type the success path returns.

Moved verbatim from facebook/api.py.
"""


class InsightMetricError(RuntimeError):
    """Raised when one or more Graph API insight metrics fail. Carries the metrics that DID
    succeed (`items`) and, once the caller attaches it, the partially-populated result
    (`partial_result`) so failures never discard data for the metrics that worked."""

    def __init__(self, path: str, metrics: list[str], causes: dict[str, Exception], items: list[dict]):
        self.path = path
        self.metrics = metrics
        self.causes = causes
        self.items = items
        self.partial_result = None  # set by get_video_insights/get_post_insights before re-raising
        detail = "; ".join(f"{m}: {causes[m]}" for m in metrics)
        super().__init__(f"{len(metrics)} metric(s) failed for {path} — {detail}")


class PaginationError(RuntimeError):
    """Raised when a paginated Graph API fetch fails before exhausting all pages. Carries the
    pages that DID succeed (`items`) and, once the caller attaches it, the partially-populated
    result (`partial_result`) so failures never discard data for the pages that worked."""

    def __init__(self, path: str, params: dict | None, causes: list[dict], items: list[dict]):
        self.path = path
        self.params = params
        self.causes = causes
        self.items = items
        self.partial_result = None  # set by get_posts/get_videos before re-raising
        detail = "; ".join(str(c["exception"]) for c in causes)
        super().__init__(f"Pagination failed for {path} after {len(items)} result(s) — {detail}")
