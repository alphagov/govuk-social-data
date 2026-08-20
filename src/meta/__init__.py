"""Shared building blocks for the Meta Graph API pipelines (Facebook, Instagram, Threads).

Every Meta platform uses the same node/edge Graph API, so the transport layer, the
error-collation types, token retrieval and the per-item insight componenets can be reused.
They live here so a new platform supplies only its base URL, its metric constants
and its model builders.

This new approach is lifted from the Facebook pipeline, which was the only one with proper
failure handling. See src/meta/client.py for the transport and src/meta/errors.py for the
partial-result contract those errors carry.

Deliberately empty of imports: re-exporting here would make `src.meta.client` drag in
src.meta.auth and therefore google.cloud.secretmanager, coupling the pure-transport layer to
the GCP SDK. Import from the specific module instead.
"""
