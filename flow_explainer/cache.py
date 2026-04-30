"""
Local JSON cache for 3CX API data.

Saves all loaded DN objects to a single JSON file after the first API fetch.
Subsequent runs load from the file and skip all API calls.

Cache is invalidated when:
  - The file is older than --cache-ttl seconds (default: 3600 = 1 hour)
  - The stored server URL doesn't match the currently selected server
  - The file is missing or corrupt
  - --no-cache is passed on the command line
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from threecx.models._generated import (
    CallFlowApp,
    Fxs,
    Group,
    Queue,
    Receptionist,
    RingGroup,
    SystemExtensionStatus,
    Trunk,
    User,
)

logger = logging.getLogger(__name__)

DEFAULT_TTL = 3600  # seconds

# Maps the attribute names used in ThreeCXAdapter to their Pydantic model class.
# Key is the dict name in the adapter; value is the Pydantic model used for round-trip.
# The dict key in cache can be any string identifier (number, MAC address, etc.)
_CATEGORY_MODELS: dict[str, type] = {
    "users": User,
    "queues": Queue,
    "ring_groups": RingGroup,
    "receptionists": Receptionist,
    "groups": Group,
    "call_flow_apps": CallFlowApp,
    "trunks": Trunk,
    "fxs_devices": Fxs,
    "system_extensions": SystemExtensionStatus,
}


def _serialize(obj_dict: dict[str, Any]) -> dict[str, Any]:
    """Serialize a {number: PydanticModel} dict to a {number: plain_dict} form."""
    out: dict[str, Any] = {}
    for number, obj in obj_dict.items():
        try:
            # mode="json" coerces enums, datetimes, UUIDs to JSON-safe types
            out[number] = obj.model_dump(mode="json", by_alias=True, exclude_none=True)
        except Exception as exc:
            logger.debug("Could not serialize %r: %s", number, exc)
    return out


def _deserialize(raw: dict[str, Any], model_cls: type) -> dict[str, Any]:
    """Deserialize a {number: plain_dict} back to {number: PydanticModel}."""
    out: dict[str, Any] = {}
    for number, data in raw.items():
        try:
            out[number] = model_cls.model_validate(data)
        except Exception as exc:
            logger.debug("Could not deserialize %r as %s: %s", number, model_cls.__name__, exc)
    return out


class CacheManager:
    """Read/write a local JSON cache of all DN data for one server."""

    def __init__(self, path: Path, ttl: int = DEFAULT_TTL) -> None:
        self.path = path
        self.ttl = ttl

    # ------------------------------------------------------------------

    def is_fresh(self, server_url: str) -> bool:
        """Return True if the cache exists, matches the server, and is within TTL."""
        if not self.path.exists():
            return False
        try:
            meta = json.loads(self.path.read_text(encoding="utf-8"))
            if meta.get("server_url") != server_url:
                logger.debug("Cache is for '%s', not '%s' — ignoring.", meta.get("server_url"), server_url)
                return False
            age = time.time() - float(meta.get("saved_at", 0))
            if age > self.ttl:
                logger.debug("Cache is %.0f s old (TTL=%d s) — stale.", age, self.ttl)
                return False
            return True
        except Exception as exc:
            logger.warning("Could not check cache freshness: %s", exc)
            return False

    def load(self, server_url: str) -> Optional[dict[str, dict[str, Any]]]:
        """
        Deserialize cached data.
        Returns {category_name: {number: sdk_object}} or None if unusable.
        """
        if not self.is_fresh(server_url):
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            result: dict[str, dict[str, Any]] = {}
            for category, model_cls in _CATEGORY_MODELS.items():
                result[category] = _deserialize(raw.get(category, {}), model_cls)
            total = sum(len(v) for v in result.values())
            age_min = (time.time() - float(raw.get("saved_at", 0))) / 60
            logger.info("Cache loaded: %d objects (%.0f min old).", total, age_min)
            return result
        except Exception as exc:
            logger.warning("Failed to load cache from %s: %s", self.path, exc)
            return None

    def save(self, server_url: str, data: dict[str, dict[str, Any]]) -> None:
        """
        Serialize all DN dicts and write the cache file.
        `data` must be {category_name: {number: sdk_object}}.
        """
        payload: dict[str, Any] = {
            "server_url": server_url,
            "saved_at": time.time(),
        }
        for category in _CATEGORY_MODELS:
            payload[category] = _serialize(data.get(category, {}))

        try:
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            total = sum(len(payload.get(c, {})) for c in _CATEGORY_MODELS)
            size_kb = self.path.stat().st_size // 1024
            logger.info("Cache saved to %s (%d objects, %d KB).", self.path, total, size_kb)
        except Exception as exc:
            logger.warning("Failed to save cache to %s: %s", self.path, exc)
