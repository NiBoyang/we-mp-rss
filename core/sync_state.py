"""Per-MP sync backoff, history pagination cursor, and operator-visible status."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from core.config import cfg

_memory_lock = threading.Lock()
_memory: Dict[str, Dict[str, Any]] = {}


def _redis():
    try:
        from core.redis_client import RedisClient

        client = RedisClient()
        if client.is_connected:
            return client._client
    except Exception:
        pass
    return None


def _key(mp_id: str) -> str:
    return f"werss:sync:state:{mp_id}"


def _load(mp_id: str) -> Dict[str, Any]:
    r = _redis()
    if r is not None:
        try:
            raw = r.get(_key(mp_id))
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    with _memory_lock:
        return dict(_memory.get(mp_id) or {})


def _save(mp_id: str, data: Dict[str, Any], ttl: int = 86400 * 7) -> None:
    r = _redis()
    payload = json.dumps(data, ensure_ascii=False)
    if r is not None:
        try:
            r.set(_key(mp_id), payload, ex=ttl)
            return
        except Exception:
            pass
    with _memory_lock:
        _memory[mp_id] = data


def _now() -> int:
    return int(time.time())


def _backoff_base() -> int:
    return max(60, int(cfg.get("sync.rate_limit_backoff_base", 300)))


def _backoff_max() -> int:
    return max(_backoff_base(), int(cfg.get("sync.rate_limit_backoff_max", 3600)))


def record_rate_limit(mp_id: str, mp_name: str = "") -> int:
    """Record WeChat rate limit; returns seconds until retry."""
    state = _load(mp_id)
    strikes = int(state.get("rate_limit_strikes") or 0) + 1
    delay = min(_backoff_base() * strikes, _backoff_max())
    retry_at = _now() + delay
    state.update(
        {
            "state": "rate_limited",
            "message": "微信频率限制，等待自动重试",
            "mp_name": mp_name or state.get("mp_name") or "",
            "rate_limit_strikes": strikes,
            "retry_at": retry_at,
            "updated_at": _now(),
        }
    )
    _save(mp_id, state)
    return delay


def record_success(mp_id: str) -> None:
    state = _load(mp_id)
    if not state:
        return
    state["rate_limit_strikes"] = 0
    state["state"] = "ok"
    state["message"] = ""
    state["retry_at"] = 0
    state["updated_at"] = _now()
    _save(mp_id, state)


def record_waiting(mp_id: str, message: str) -> None:
    state = _load(mp_id)
    state.update(
        {
            "state": "waiting",
            "message": message,
            "updated_at": _now(),
        }
    )
    _save(mp_id, state)


def should_defer(mp_id: str) -> Tuple[bool, str, Optional[int]]:
    """Return (defer, reason, retry_at_epoch)."""
    state = _load(mp_id)
    retry_at = int(state.get("retry_at") or 0)
    if state.get("state") == "rate_limited" and retry_at > _now():
        remaining = retry_at - _now()
        msg = state.get("message") or "等待频率限制退避"
        return True, f"{msg}（约 {remaining}s 后重试）", retry_at
    return False, "", None


def get_history_next_page(mp_id: str) -> int:
    return max(0, int(_load(mp_id).get("history_next_page") or 0))


def advance_history_cursor(mp_id: str, pages_fetched: int, target_pages: int) -> None:
    """Advance history pagination cursor after a successful run."""
    if pages_fetched <= 0:
        return
    state = _load(mp_id)
    nxt = int(state.get("history_next_page") or 0) + pages_fetched
    if nxt >= target_pages:
        nxt = 0
    state["history_next_page"] = nxt
    state["updated_at"] = _now()
    _save(mp_id, state)


def resolve_sync_page_window(mp_id: str, article_count: int) -> Tuple[int, int]:
    """
    Return (start_page, end_page) for get_Articles MaxPage parameter semantics
    (end_page is exclusive upper page index).
    """
    target_history = max(1, int(cfg.get("max_page", 5)))
    pages_per_run = max(1, int(cfg.get("sync.pages_per_run", 1)))
    history_pages_per_run = max(1, int(cfg.get("sync.history_pages_per_run", 1)))

    if article_count == 0:
        chunk = min(history_pages_per_run, target_history)
        start = get_history_next_page(mp_id)
        if start >= target_history:
            start = 0
        end = min(start + chunk, target_history)
        if end <= start:
            end = min(start + 1, target_history)
        return start, max(start + 1, end)

    return 0, pages_per_run


def get_mp_sync_status(mp_id: str) -> Dict[str, Any]:
    state = _load(mp_id)
    retry_at = int(state.get("retry_at") or 0)
    retry_iso = None
    if retry_at > 0:
        retry_iso = datetime.fromtimestamp(retry_at, tz=timezone.utc).isoformat()
    return {
        "state": state.get("state") or "idle",
        "message": state.get("message") or "",
        "retry_at": retry_at if retry_at > _now() else 0,
        "retry_at_iso": retry_iso,
        "history_next_page": int(state.get("history_next_page") or 0),
    }


def detect_rate_limit_from_collector(wx) -> bool:
    return bool(getattr(wx, "rate_limited", False))
