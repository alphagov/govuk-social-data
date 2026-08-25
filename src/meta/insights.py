"""Per-item insight fetching with partial-failure tolerance.

Insight edges are fetched one entity at a time (one request per post, video or media item),
so a single unavailable metric on a single item should degrade that item rather than abort
the run. This loop keeps whatever each item managed to fetch and reports the failures once
at the end instead of once per item.

Moved verbatim from facebook/main.py._run_insight_fetch.
"""

import logging

import requests

from src.meta.errors import InsightMetricError

logger = logging.getLogger(__name__)


def run_insight_fetch(items: list, fetch_one, get_id, label: str) -> list:
    """
    Runs fetch_one(item) for each item, tolerating per-metric insight failures.

    On InsightMetricError, keeps the item's partial_result (every metric that DID
    succeed) instead of discarding its insights entirely, logs the specific metric(s)
    that failed, and tallies them for one consolidated summary line at the end instead
    of one generic warning per item.

        PARAMETERS

            items: the entities whose insights are being fetched, e.g. Post or Video objects.
            fetch_one: a callable on the client that fetches insights for one entity, such as
                client.get_video_insights or client.get_post_insights.
            get_id: callable returning the entity's ID string, used for logging.
            label: human-readable entity name for log lines, e.g. "Post" or "Video".

        RETURNS
            One entry per input item, in input order. Entries are whatever fetch_one
            returned, or the partial result when only some metrics failed, or the
            un-enriched item when the whole request failed.
    """
    results = []
    failed_metrics: dict[str, int] = {}
    for i, item in enumerate(items, start=1):
        item_id = get_id(item)
        logger.info("%s %d/%d: %s", label, i, len(items), item_id)
        try:
            results.append(fetch_one(item))
        except InsightMetricError as e:
            logger.error("%s %s: %s", label, item_id, e)
            # `or item` because a partial_result the client failed to attach would otherwise put
            # None in the results list, and the arrow converters dereference each entry — turning
            # a handled degradation into an AttributeError.
            results.append(e.partial_result or item)
            for m in e.metrics:
                failed_metrics[m] = failed_metrics.get(m, 0) + 1
        except requests.RequestException as e:
            logger.error("%s %s: %s", label, item_id, e)
            results.append(item)
    if failed_metrics:
        logger.warning(
            "%s insight metric failures across %d item(s): %s",
            label,
            len(items),
            ", ".join(f"{m} ({c})" for m, c in sorted(failed_metrics.items(), key=lambda kv: -kv[1])),
        )
    return results
