"""Cache-first contextual autocomplete for the Redis API.

All Redis reads/writes live here. The TOC backend performs ONLY an authenticated
Oracle lookup on cache misses. Existing org-scoped Redis keys are not modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_CACHE_PREFIX = "aeris:contextual:v3"
_NORM = re.compile(r"[^a-z0-9]+")
_LOCK_SCRIPT = "if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"


class ContextualLookupError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _id(value: object) -> str:
    return _NORM.sub("_", str(value or "").strip().casefold()).strip("_")


def _backend_json(path: str, authorization: str, *, body: dict | None = None) -> dict:
    base = os.getenv("TOC_BACKEND_INTERNAL_URL", "http://127.0.0.1:8002").rstrip("/")
    if not (base.startswith("http://127.0.0.1:") or base.startswith("http://localhost:") or base.startswith("https://")):
        raise ContextualLookupError(503, "Internal autocomplete backend URL is not configured securely.")
    headers = {"Authorization": authorization, "Accept": "application/json"}
    if body is not None:
        secret = os.getenv("TOC_AUTOCOMPLETE_INTERNAL_KEY", "").strip()
        if not secret:
            raise ContextualLookupError(503, "Internal autocomplete service is not configured.")
        headers["X-TOC-Internal-Key"] = secret
        headers["Content-Type"] = "application/json"
    request = Request(
        base + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(request, timeout=8) as reply:
            return json.loads(reply.read(1024 * 1024).decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 401:
            raise ContextualLookupError(401, "Please sign in again.") from exc
        if exc.code == 403:
            raise ContextualLookupError(403, "Autocomplete access denied.") from exc
        if exc.code == 422:
            # Context validation error is safe and useful (no SQL, no raw exception).
            try:
                detail = json.loads(exc.read(4096)).get("detail", "Invalid autocomplete filters.")
            except (ValueError, UnicodeError):
                detail = "Invalid autocomplete filters."
            raise ContextualLookupError(422, str(detail)) from exc
        raise ContextualLookupError(503, "Autocomplete lookup is temporarily unavailable.") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise ContextualLookupError(503, "Autocomplete lookup is temporarily unavailable.") from exc


def _canonical_filters(filters: dict[str, list[str]], target: str) -> dict[str, list[str]]:
    if not isinstance(filters, dict) or len(filters) > 12:
        raise ContextualLookupError(422, "Invalid autocomplete filters.")
    result = {}
    for name, values in filters.items():
        key = _id(name)
        if not key or key == _id(target):
            continue
        if not isinstance(values, list) or len(values) > 10:
            raise ContextualLookupError(422, "Invalid autocomplete filter values.")
        canonical = sorted({str(v).strip() for v in values if str(v).strip()}, key=str.casefold)
        if any(len(v) > 256 for v in canonical):
            raise ContextualLookupError(422, "Autocomplete filter value is too long.")
        if canonical:
            result[key] = canonical
    return dict(sorted(result.items()))


def query_contextual_cached(*, cache: Any, payload: dict, authorization: str, mapping_version: int) -> dict:
    """Verify auth, check Redis, call TOC Oracle-only service on miss, cache result."""
    if not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise ContextualLookupError(401, "Authentication required.")
    if not os.getenv("TOC_AUTOCOMPLETE_INTERNAL_KEY", "").strip():
        raise ContextualLookupError(503, "Internal autocomplete service is not configured.")

    # Important: always verify the login token BEFORE returning even a cache hit.
    current_user = _backend_json("/auth/me", authorization)
    user_payload = current_user.get("user") or {}
    email = str(user_payload.get("email") or "").strip().casefold()
    if not email:
        raise ContextualLookupError(401, "Authentication required.")

    report = str(payload.get("report_name", "")).strip()
    target = str(payload.get("target_variable", "")).strip()
    search = str(payload.get("search_text", "")).strip()
    limit = payload.get("limit", 40)
    offset = payload.get("offset", 0)
    if (not report or not target or len(search) > 100
            or type(limit) is not int or not 1 <= limit <= 100
            or type(offset) is not int or not 0 <= offset <= 1000000):
        raise ContextualLookupError(422, "Invalid autocomplete request.")
    filters = _canonical_filters(payload.get("filters", {}), target)

    fingerprint = json.dumps({
        "report": _id(report), "target": _id(target), "filters": filters,
        "search": search.casefold(), "limit": limit, "offset": offset, "mapping_version": mapping_version,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    user_scope = hashlib.sha256(email.encode("utf-8")).hexdigest()[:20]
    version = datetime.now(timezone.utc).strftime("%Y%m%d")
    key = f"{_CACHE_PREFIX}:{version}:{user_scope}:{digest}"
    cached = cache.get(key)
    if cached is not None:
        result = json.loads(cached)
        return {**result, "source": "redis_context_cache"}

    lock_key = key + ":lock"
    # Use unique lock tokens to avoid deleting another request's lock.
    lock_token = hashlib.sha256(f"{time.monotonic_ns()}:{digest}".encode()).hexdigest()
    acquired = bool(cache.set(lock_key, lock_token, ex=12, nx=True))
    if not acquired:
        for _ in range(60):
            time.sleep(0.1)
            cached = cache.get(key)
            if cached is not None:
                return {**json.loads(cached), "source": "redis_context_cache"}
        raise ContextualLookupError(503, "Autocomplete lookup is busy. Please retry.")

    try:
        # Another worker may have filled the cache just before lock acquisition.
        cached = cache.get(key)
        if cached is not None:
            return {**json.loads(cached), "source": "redis_context_cache"}
        query = {
            "report_name": report, "target_variable": target,
            "filters": filters, "search_text": search, "limit": limit, "offset": offset,
        }
        oracle = _backend_json("/internal/autocomplete/oracle", authorization, body=query)
        values = oracle.get("values")
        if not isinstance(values, list):
            raise ContextualLookupError(503, "Autocomplete lookup returned an invalid response.")
        result = {
            "values": [str(x) for x in values][:limit],
            "has_more": bool(oracle.get("has_more", False)),
            "next_offset": (offset + min(len(values), limit))
            if oracle.get("has_more", False) else None,
        }
        ttl = int(os.getenv("CONTEXT_AUTOCOMPLETE_TTL_SECONDS", "86400"))
        cache.set(key, json.dumps(result, ensure_ascii=False), ex=max(60, min(ttl, 86400)))
        return {**result, "source": "oracle"}
    finally:
        cache.eval(_LOCK_SCRIPT, 1, lock_key, lock_token)
