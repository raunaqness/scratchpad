"""Ingestion subsystem: bring outside material into a knowledge graph.

Lives on its own route namespace (``/api/ingest``, mounted from ``backend/agent.py``)
and its own Postgres tables (``scratchpad.ingest_runs`` / ``scratchpad.ingest_items``),
separate from the main chat turn. ``blog/`` is the first source (scrape a blog URL);
future sources (file upload, paste-text) share the same run/confirm/progress model.
"""
