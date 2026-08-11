from __future__ import annotations

import hashlib
import os
from typing import Any


class ResponseCache:
    """Tiny in-process response cache used for the cost-optimization bonus.

    Repeated user questions are served from cache, skipping the (expensive)
    LLM call entirely — a cache hit costs ~0 tokens. Enabled via
    ``RESPONSE_CACHE=1`` so it never changes default lab behavior.
    """

    def __init__(self, enabled: bool | None = None, max_size: int = 256) -> None:
        self.enabled = os.getenv("RESPONSE_CACHE") == "1" if enabled is None else enabled
        self._store: dict[str, dict[str, Any]] = {}
        self.max_size = max_size

    @staticmethod
    def key(feature: str, message: str) -> str:
        raw = f"{feature}\x00{message}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def get(self, key: str) -> dict[str, Any] | None:
        return self._store.get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        if len(self._store) >= self.max_size:
            self._store.clear()
        self._store[key] = value

    def __len__(self) -> int:
        return len(self._store)
