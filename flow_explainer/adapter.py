"""
Thin adapter around ThreeCXClient.

Bulk-loads all DN objects once from the API and provides fast number-keyed
lookups. All method names and field names are derived directly from the
installed 3cx-xapi-python-sdk source — marked with ASSUMPTION where uncertain.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from threecx import ODataQuery, ThreeCXClient, ThreeCXError
from threecx.models._generated import SystemExtensionStatus

from .cache import CacheManager
from .models import DnType

logger = logging.getLogger(__name__)


class ThreeCXAdapter:
    """
    Caching wrapper around ThreeCXClient.

    Call ``load_all()`` once to fetch all DN types (or restore from cache),
    then use ``find_dn(number)`` for O(1) lookups.
    """

    def __init__(self, client: ThreeCXClient) -> None:
        self._client = client
        self._users: dict[str, Any] = {}
        self._queues: dict[str, Any] = {}
        self._ring_groups: dict[str, Any] = {}
        self._receptionists: dict[str, Any] = {}
        self._groups: dict[str, Any] = {}
        self._call_flow_apps: dict[str, Any] = {}
        self._trunks: dict[str, Any] = {}
        self._fxs_devices: dict[str, Any] = {}        # keyed by mac_address
        self._system_extensions: dict[str, Any] = {}  # keyed by number
        self._loaded = False

    # ------------------------------------------------------------------
    # Bulk loading (with optional cache)
    # ------------------------------------------------------------------

    def load_all(
        self,
        cache: Optional[CacheManager] = None,
        server_url: str = "",
    ) -> None:
        """
        Populate all DN dicts, either from a local cache file or from the API.

        If ``cache`` is provided and is fresh for ``server_url``, all API calls
        are skipped. Otherwise the API is queried and the result is saved to
        the cache file for next time.
        """
        # ── Try cache first ───────────────────────────────────────────
        if cache is not None and server_url:
            cached = cache.load(server_url)
            if cached is not None:
                self._users = cached["users"]
                self._queues = cached["queues"]
                self._ring_groups = cached["ring_groups"]
                self._receptionists = cached["receptionists"]
                self._groups = cached["groups"]
                self._call_flow_apps = cached.get("call_flow_apps", {})
                self._trunks = cached.get("trunks", {})
                self._fxs_devices = cached.get("fxs_devices", {})
                self._system_extensions = cached.get("system_extensions", {})
                self._loaded = True
                total = self._total_count()
                print(f"  Loaded {total} objects from cache: {cache.path}")
                return

        # ── Fetch from API ────────────────────────────────────────────
        self._load_users()
        self._load_queues()
        self._load_ring_groups()
        self._load_receptionists()
        self._load_groups()
        self._load_call_flow_apps()
        self._load_trunks()
        self._load_fxs_devices()
        self._load_system_extensions()
        self._loaded = True

        # ── Persist to cache ──────────────────────────────────────────
        if cache is not None and server_url:
            cache.save(server_url, {
                "users": self._users,
                "queues": self._queues,
                "ring_groups": self._ring_groups,
                "receptionists": self._receptionists,
                "groups": self._groups,
                "call_flow_apps": self._call_flow_apps,
                "trunks": self._trunks,
                "fxs_devices": self._fxs_devices,
                "system_extensions": self._system_extensions,
            })
            print(f"  Saved {self._total_count()} objects to cache: {cache.path}")

    def _total_count(self) -> int:
        return sum(len(d) for d in [
            self._users, self._queues, self._ring_groups,
            self._receptionists, self._groups, self._call_flow_apps,
            self._trunks, self._fxs_devices, self._system_extensions,
        ])

    # ------------------------------------------------------------------
    # Private loaders
    # ------------------------------------------------------------------

    def _load_users(self) -> None:
        print("  Loading extensions/users…")
        try:
            items = self._client.users.list(
                ODataQuery()
                .expand("ForwardingProfiles,ForwardingExceptions,Greetings")
                .filter("not startsWith(Number,'HD')")
            )
            self._users = {u.number: u for u in items if u.number}
            logger.info("Loaded %d users.", len(self._users))
        except ThreeCXError as exc:
            logger.warning("Could not load users: %s", exc)

    def _load_queues(self) -> None:
        print("  Loading queues…")
        try:
            items = self._client.queues.list(
                ODataQuery().expand("Agents,Managers")
            )
            self._queues = {q.number: q for q in items if q.number}
            logger.info("Loaded %d queues.", len(self._queues))
        except ThreeCXError as exc:
            logger.warning("Could not load queues: %s", exc)

    def _load_ring_groups(self) -> None:
        print("  Loading ring groups…")
        try:
            items = self._client.ring_groups.list(
                ODataQuery().expand("Members")
            )
            self._ring_groups = {rg.number: rg for rg in items if rg.number}
            logger.info("Loaded %d ring groups.", len(self._ring_groups))
        except ThreeCXError as exc:
            logger.warning("Could not load ring groups: %s", exc)

    def _load_receptionists(self) -> None:
        print("  Loading IVRs / digital receptionists…")
        try:
            items = self._client.receptionists.list(
                ODataQuery().expand("Forwards")
            )
            self._receptionists = {r.number: r for r in items if r.number}
            logger.info("Loaded %d receptionists.", len(self._receptionists))
        except ThreeCXError as exc:
            logger.warning("Could not load receptionists: %s", exc)

    def _load_groups(self) -> None:
        print("  Loading groups…")
        try:
            items = self._client.groups.list(
                ODataQuery().filter("not startsWith(Name, '___FAVORITES___')")
            )
            self._groups = {g.number: g for g in items if g.number}
            logger.info("Loaded %d groups.", len(self._groups))
        except ThreeCXError as exc:
            logger.warning("Could not load groups: %s", exc)

    def _load_call_flow_apps(self) -> None:
        print("  Loading Call Flow Apps…")
        try:
            items = self._client.call_flow.list()
            self._call_flow_apps = {a.number: a for a in items if a.number}
            logger.info("Loaded %d call flow apps.", len(self._call_flow_apps))
        except ThreeCXError as exc:
            logger.warning("Could not load call flow apps: %s", exc)

    def _load_trunks(self) -> None:
        print("  Loading trunks…")
        try:
            items = self._client.trunks.list_trunks(
                ODataQuery().expand("RoutingRules")
            )
            self._trunks = {t.number: t for t in items if t.number}
            logger.info("Loaded %d trunks.", len(self._trunks))
        except ThreeCXError as exc:
            logger.warning("Could not load trunks: %s", exc)

    def _load_fxs_devices(self) -> None:
        print("  Loading FXS devices…")
        try:
            items = self._client.phones.list_fxs()
            self._fxs_devices = {f.mac_address: f for f in items if f.mac_address}
            logger.info("Loaded %d FXS devices.", len(self._fxs_devices))
        except ThreeCXError as exc:
            logger.warning("Could not load FXS devices: %s", exc)

    def _load_system_extensions(self) -> None:
        print("  Loading system extensions…")
        try:
            raw = self._client.system.get_system_extensions()
            entries = raw.get("value", []) if isinstance(raw, dict) else []
            self._system_extensions = {}
            for item in entries:
                try:
                    obj = SystemExtensionStatus.model_validate(item)
                    if obj.number:
                        self._system_extensions[obj.number] = obj
                except Exception:
                    pass
            logger.info("Loaded %d system extensions.", len(self._system_extensions))
        except ThreeCXError as exc:
            logger.warning("Could not load system extensions: %s", exc)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def find_dn(self, number: str) -> Optional[tuple[DnType, Any]]:
        """
        Find a DN by number across all loaded routing types.
        Returns (DnType, sdk_object) or None if not found.
        Trunks, FXS, and system extensions are excluded — they are not
        routing destinations that other DNs point to.
        """
        if not self._loaded:
            self.load_all()

        if number in self._users:
            return DnType.USER, self._users[number]
        if number in self._queues:
            return DnType.QUEUE, self._queues[number]
        if number in self._ring_groups:
            return DnType.RING_GROUP, self._ring_groups[number]
        if number in self._receptionists:
            return DnType.IVR, self._receptionists[number]
        if number in self._groups:
            return DnType.GROUP, self._groups[number]
        if number in self._call_flow_apps:
            return DnType.CALL_FLOW_APP, self._call_flow_apps[number]
        return None

    def is_known_dn(self, number: str) -> bool:
        """True if number is already listed in any other category."""
        return self.find_dn(number) is not None

    # ------------------------------------------------------------------
    # Pass-through properties
    # ------------------------------------------------------------------

    @property
    def all_users(self) -> dict[str, Any]:
        return self._users

    @property
    def all_queues(self) -> dict[str, Any]:
        return self._queues

    @property
    def all_ring_groups(self) -> dict[str, Any]:
        return self._ring_groups

    @property
    def all_receptionists(self) -> dict[str, Any]:
        return self._receptionists

    @property
    def all_groups(self) -> dict[str, Any]:
        return self._groups

    @property
    def all_call_flow_apps(self) -> dict[str, Any]:
        return self._call_flow_apps

    @property
    def all_trunks(self) -> dict[str, Any]:
        return self._trunks

    @property
    def all_fxs_devices(self) -> dict[str, Any]:
        return self._fxs_devices

    @property
    def all_system_extensions(self) -> dict[str, Any]:
        return self._system_extensions
