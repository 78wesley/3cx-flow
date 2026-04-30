"""
Full-directory Markdown renderer.

Renders every object in the PBX into a single Markdown file. Each entity
gets an HTML anchor for cross-linking, a properties table, and (where
applicable) a routing table with clickable #anchor destination links.

Report structure:
  1. Title / metadata
  2. Table of Contents
  3. Trunks (entry points — inbound calls)
  4. IVR / Digital Receptionists
  5. Ring Groups
  6. Queues
  7. Call Flow Apps
  8. Groups
  9. Extensions (Users)
  10. FXS Devices
  11. System Extensions
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from threecx.models._generated import (
    AvailableRouting,
    AwayRouting,
    Destination,
    DestinationType,
    PeerType,
    ReceptionistForward,
    Route,
)

from .adapter import ThreeCXAdapter
from .models import DnType

# ---------------------------------------------------------------------------
# Anchor / link helpers
# ---------------------------------------------------------------------------

_ANCHOR_PREFIX: dict[DnType, str] = {
    DnType.USER: "ext",
    DnType.QUEUE: "queue",
    DnType.RING_GROUP: "ring-group",
    DnType.IVR: "ivr",
    DnType.GROUP: "group",
    DnType.CALL_FLOW_APP: "cfa",
    DnType.TRUNK: "trunk",
    DnType.FXS: "fxs",
    DnType.SYSTEM_EXTENSION: "sysext",
}

_ICONS: dict[DnType, str] = {
    DnType.USER: "👤",
    DnType.QUEUE: "📋",
    DnType.RING_GROUP: "🔔",
    DnType.IVR: "🎛️",
    DnType.GROUP: "🏢",
    DnType.CALL_FLOW_APP: "⚙️",
    DnType.TRUNK: "📡",
    DnType.FXS: "📟",
    DnType.SYSTEM_EXTENSION: "🔧",
    DnType.EXTERNAL: "📞",
    DnType.VOICEMAIL: "📬",
    DnType.VOICEMAIL_OF_DN: "📬",
    DnType.UNKNOWN: "❓",
}

# Map IVRForwardType string values -> DestinationType
_IVR_FWD_TO_DEST: dict[str, DestinationType] = {
    "Extension": DestinationType.extension,
    "RingGroup": DestinationType.ring_group,
    "Queue": DestinationType.queue,
    "IVR": DestinationType.ivr,
    "VoiceMail": DestinationType.voice_mail,
    "EndCall": DestinationType.none,
    "RepeatPrompt": DestinationType.none,
    "CallByName": DestinationType.none,
    "CustomInput": DestinationType.none,
}


def _anchor(dn_type: DnType, number: str) -> str:
    prefix = _ANCHOR_PREFIX.get(dn_type, "dn")
    return f"{prefix}-{number}"


def _sdk_name(sdk_obj: Any) -> str:
    return (
        getattr(sdk_obj, "display_name", None)
        or getattr(sdk_obj, "name", None)
        or getattr(sdk_obj, "number", "?")
    )


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if hasattr(v, "value"):
        return str(v.value)
    return str(v)


def _dest_link(dest: Optional[Destination], adapter: ThreeCXAdapter) -> Optional[str]:
    """
    Resolve a Destination to a Markdown link string, or None to skip.
    Returns None for empty / proceed-normally / no-number destinations.
    """
    if dest is None or dest.to is None or dest.to == DestinationType.none:
        return None
    if dest.to == DestinationType.proceed_with_no_exceptions:
        return None

    if dest.to == DestinationType.external:
        ext = dest.external or dest.number or "?"
        return f"📞 External: `{ext}`"

    if dest.to == DestinationType.voice_mail:
        num = dest.number or ""
        if not num:
            return None
        result = adapter.find_dn(num)
        if result:
            dn_type, sdk = result
            name = _sdk_name(sdk)
            return f"[📬 Voicemail of {name} ({num})](#{_anchor(dn_type, num)})"
        return f"📬 Voicemail (`{num}`)"

    if dest.to == DestinationType.voice_mail_of_destination:
        num = dest.number or ""
        if not num:
            return None
        result = adapter.find_dn(num)
        if result:
            dn_type, sdk = result
            name = _sdk_name(sdk)
            return f"[📬 Voicemail of {name} ({num})](#{_anchor(dn_type, num)})"
        return f"📬 Voicemail of `{dest.name or num}` (`{num}`)"

    # All other types (extension, queue, ring_group, ivr, route_point, …)
    # resolve by number via the adapter.
    number = dest.number or ""
    if not number:
        return None  # "same as office hours" signal or not configured

    result = adapter.find_dn(number)
    if result:
        dn_type, sdk = result
        name = _sdk_name(sdk)
        icon = _ICONS.get(dn_type, "•")
        return f"[{icon} {dn_type.value}: {name} ({number})](#{_anchor(dn_type, number)})"

    return f"❓ `{dest.name or number}` ({number}) — *not found*"


def _dest_cell(dest: Optional[Destination], adapter: ThreeCXAdapter) -> str:
    """Like _dest_link but returns '—' instead of None (for table cells)."""
    link = _dest_link(dest, adapter)
    return link if link is not None else "—"


def _route_dest(route: Optional[Route]) -> Optional[Destination]:
    return route.route if route is not None else None


def _dest_is_set(dest: Optional[Destination]) -> bool:
    return (
        dest is not None
        and dest.to is not None
        and dest.to != DestinationType.none
        and dest.to != DestinationType.proceed_with_no_exceptions
    )


def _fwd_to_dest(fwd: ReceptionistForward) -> Optional[Destination]:
    if not fwd.forward_dn:
        return None
    dest_type = _IVR_FWD_TO_DEST.get(
        fwd.forward_type.value if fwd.forward_type else "",
        DestinationType.extension,
    )
    return Destination(
        number=fwd.forward_dn,
        to=dest_type,
        type=fwd.peer_type,
        external=fwd.forward_dn if dest_type == DestinationType.external else None,
    )


def _fwd_type_to_dest_type(fwd_type: Optional[Any]) -> DestinationType:
    if fwd_type is None:
        return DestinationType.extension
    val = fwd_type.value if hasattr(fwd_type, "value") else str(fwd_type)
    return _IVR_FWD_TO_DEST.get(val, DestinationType.extension)


# ---------------------------------------------------------------------------
# Routing-row extraction per DN type  (returns [(label, link_str), …])
# ---------------------------------------------------------------------------

def _user_routes(sdk_obj: Any, adapter: ThreeCXAdapter) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    current = sdk_obj.current_profile_name or "Available"

    for profile in sdk_obj.forwarding_profiles or []:
        pname = profile.name or "Profile"
        marker = " ★" if pname == current else ""
        pfx = f"[{pname}{marker}]"

        avail: Optional[AvailableRouting] = profile.available_route
        if avail:
            for label, dest in [
                ("Busy (External)", avail.busy_external),
                ("Busy (Internal)", avail.busy_internal),
                ("No Answer (External)", avail.no_answer_external),
                ("No Answer (Internal)", avail.no_answer_internal),
                ("Not Registered (Ext)", avail.not_registered_external),
                ("Not Registered (Int)", avail.not_registered_internal),
            ]:
                if _dest_is_set(dest):
                    link = _dest_link(dest, adapter)
                    if link:
                        rows.append((f"{pfx} {label}", link))

        away: Optional[AwayRouting] = profile.away_route
        if away:
            for label, dest in [
                ("Away (External)", away.external),
                ("Away (Internal)", away.internal),
            ]:
                if _dest_is_set(dest):
                    link = _dest_link(dest, adapter)
                    if link:
                        rows.append((f"{pfx} {label}", link))

    for rule in sdk_obj.forwarding_exceptions or []:
        if not rule.enabled:
            continue
        dest = rule.destination
        if not _dest_is_set(dest):
            continue
        link = _dest_link(dest, adapter)
        if link:
            cond = rule.condition.value if rule.condition else "?"
            ctype = f" ({rule.call_type.value})" if rule.call_type else ""
            rows.append((f"[Exception] {cond}{ctype}", link))

    return rows


def _queue_routes(sdk_obj: Any, adapter: ThreeCXAdapter) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for label, route in [
        ("Holidays", sdk_obj.holidays_route),
        ("Out of Office", sdk_obj.out_of_office_route),
        ("Break", sdk_obj.break_route),
    ]:
        dest = _route_dest(route)
        if _dest_is_set(dest):
            link = _dest_link(dest, adapter)
            if link:
                rows.append((label, link))
    if _dest_is_set(sdk_obj.forward_no_answer):
        link = _dest_link(sdk_obj.forward_no_answer, adapter)
        if link:
            rows.append(("No Answer / Timeout", link))
    return rows


def _ring_group_routes(sdk_obj: Any, adapter: ThreeCXAdapter) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for label, route in [
        ("Holidays", sdk_obj.holidays_route),
        ("Out of Office", sdk_obj.out_of_office_route),
        ("Break", sdk_obj.break_route),
    ]:
        dest = _route_dest(route)
        if _dest_is_set(dest):
            link = _dest_link(dest, adapter)
            if link:
                rows.append((label, link))
    if _dest_is_set(sdk_obj.forward_no_answer):
        link = _dest_link(sdk_obj.forward_no_answer, adapter)
        if link:
            rows.append(("No Answer / Timeout", link))
    return rows


def _ivr_routes(sdk_obj: Any, adapter: ThreeCXAdapter) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for label, route in [
        ("Holidays", sdk_obj.holidays_route),
        ("Out of Office", sdk_obj.out_of_office_route),
        ("Break", sdk_obj.break_route),
    ]:
        dest = _route_dest(route)
        if _dest_is_set(dest):
            link = _dest_link(dest, adapter)
            if link:
                rows.append((label, link))

    for fwd in sdk_obj.forwards or []:
        dest = _fwd_to_dest(fwd)
        if _dest_is_set(dest):
            link = _dest_link(dest, adapter)
            if link:
                key = fwd.input or "?"
                rows.append((f"Key {key}", link))

    if sdk_obj.timeout_forward_dn:
        dest_type = _fwd_type_to_dest_type(sdk_obj.timeout_forward_type)
        dest = Destination(
            number=sdk_obj.timeout_forward_dn,
            to=dest_type,
            type=sdk_obj.timeout_forward_peer_type,
            external=sdk_obj.timeout_forward_dn if dest_type == DestinationType.external else None,
        )
        if _dest_is_set(dest):
            link = _dest_link(dest, adapter)
            if link:
                rows.append(("Timeout", link))

    if sdk_obj.invalid_key_forward_dn:
        dest = Destination(
            number=sdk_obj.invalid_key_forward_dn,
            to=DestinationType.extension,
            type=PeerType.extension,
        )
        if _dest_is_set(dest):
            link = _dest_link(dest, adapter)
            if link:
                rows.append(("Invalid Key", link))

    return rows


def _group_routes(sdk_obj: Any, adapter: ThreeCXAdapter) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for label, route in [
        ("Office Hours", sdk_obj.office_route),
        ("Out of Office", sdk_obj.out_of_office_route),
        ("Holidays", sdk_obj.holidays_route),
        ("Break", sdk_obj.break_route),
    ]:
        dest = _route_dest(route)
        if _dest_is_set(dest):
            link = _dest_link(dest, adapter)
            if link:
                rows.append((label, link))
    return rows


# ---------------------------------------------------------------------------
# Properties extraction per DN type
# ---------------------------------------------------------------------------

def _user_props(sdk_obj: Any) -> dict[str, Any]:
    p: dict[str, Any] = {}
    for key, attr in [
        ("Email", "email_address"),
        ("Mobile", "mobile"),
        ("Current Profile", "current_profile_name"),
        ("Voicemail", "vm_enabled"),
        ("Outbound Caller ID", "outbound_caller_id"),
    ]:
        v = getattr(sdk_obj, attr, None)
        if v:
            p[key] = v
    if getattr(sdk_obj, "is_registered", None) is False:
        p["Registered"] = "No"
    return p


def _queue_props(sdk_obj: Any) -> dict[str, Any]:
    p: dict[str, Any] = {}
    strategy = getattr(sdk_obj, "polling_strategy", None)
    if strategy:
        p["Ring Strategy"] = strategy.value if hasattr(strategy, "value") else strategy
    if getattr(sdk_obj, "ring_timeout", None):
        p["Ring Timeout (s)"] = sdk_obj.ring_timeout
    if getattr(sdk_obj, "master_timeout", None):
        p["Max Wait (s)"] = sdk_obj.master_timeout
    agents = getattr(sdk_obj, "agents", None) or []
    if agents:
        p["Agents"] = ", ".join(
            f"{a.name} ({a.number})" for a in agents if a.number
        )
    return p


def _ring_group_props(sdk_obj: Any) -> dict[str, Any]:
    p: dict[str, Any] = {}
    strategy = getattr(sdk_obj, "ring_strategy", None)
    if strategy:
        p["Ring Strategy"] = strategy.value if hasattr(strategy, "value") else strategy
    if getattr(sdk_obj, "ring_time", None):
        p["Ring Time (s)"] = sdk_obj.ring_time
    members = getattr(sdk_obj, "members", None) or []
    if members:
        p["Members"] = ", ".join(
            f"{m.name} ({m.number})" for m in members if m.number
        )
    return p


def _ivr_props(sdk_obj: Any) -> dict[str, Any]:
    p: dict[str, Any] = {}
    ivr_type = getattr(sdk_obj, "ivr_type", None)
    if ivr_type:
        p["IVR Type"] = ivr_type.value if hasattr(ivr_type, "value") else ivr_type
    if getattr(sdk_obj, "timeout", None):
        p["Timeout (s)"] = sdk_obj.timeout
    if getattr(sdk_obj, "prompt_filename", None):
        p["Prompt File"] = sdk_obj.prompt_filename
    if getattr(sdk_obj, "transfer_enable", None):
        p["Transfer Enabled"] = "Yes"
    return p


def _group_props(sdk_obj: Any) -> dict[str, Any]:
    p: dict[str, Any] = {}
    mode = getattr(sdk_obj, "current_group_hours", None)
    if mode:
        p["Current Hours Mode"] = mode.value if hasattr(mode, "value") else mode
    return p


def _cfa_props(sdk_obj: Any) -> dict[str, Any]:
    p: dict[str, Any] = {}
    routing_type = getattr(sdk_obj, "routing_type", None)
    if routing_type:
        p["Routing Type"] = routing_type.value if hasattr(routing_type, "value") else routing_type
    succeeded = getattr(sdk_obj, "compilation_succeeded", None)
    if succeeded is True:
        p["Compilation"] = "OK"
    elif succeeded is False:
        p["Compilation"] = "Failed"
    trunk = getattr(sdk_obj, "trunk", None)
    if trunk:
        trunk_name = getattr(trunk, "name", None) or getattr(trunk, "number", None)
        if trunk_name:
            p["Trunk"] = trunk_name
    if getattr(sdk_obj, "is_registered", None) is False:
        p["Registered"] = "No"
    return p


def _trunk_props(sdk_obj: Any) -> dict[str, Any]:
    p: dict[str, Any] = {}
    direction = getattr(sdk_obj, "direction", None)
    if direction:
        p["Direction"] = direction.value if hasattr(direction, "value") else direction
    is_online = getattr(sdk_obj, "is_online", None)
    if is_online is not None:
        p["Status"] = "Online" if is_online else "Offline"
    ext = getattr(sdk_obj, "external_number", None)
    if ext:
        p["External Number"] = ext
    did = getattr(sdk_obj, "did_numbers", None) or []
    if did:
        p["DID Count"] = len(did)
    return p


def _fxs_props(sdk_obj: Any) -> dict[str, Any]:
    p: dict[str, Any] = {}
    if getattr(sdk_obj, "brand", None):
        p["Brand"] = sdk_obj.brand
    if getattr(sdk_obj, "model_name", None):
        p["Model"] = sdk_obj.model_name
    if getattr(sdk_obj, "mac_address", None):
        p["MAC Address"] = sdk_obj.mac_address
    if getattr(sdk_obj, "time_zone", None):
        p["Time Zone"] = sdk_obj.time_zone
    return p


# ---------------------------------------------------------------------------
# Shared table helpers
# ---------------------------------------------------------------------------

def _props_table(props: dict[str, Any]) -> str:
    if not props:
        return ""
    rows = ["| Property | Value |", "|---|---|"]
    for k, v in props.items():
        rows.append(f"| {k} | {_fmt(v)} |")
    return "\n".join(rows)


def _routes_table(routes: list[tuple[str, str]]) -> str:
    if not routes:
        return "*No outbound routing configured.*"
    rows = ["| Trigger | Destination |", "|---|---|"]
    for label, link in routes:
        rows.append(f"| {label} | {link} |")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Trunk-specific rendering
# ---------------------------------------------------------------------------

def _render_trunk(sdk_obj: Any, adapter: ThreeCXAdapter, include_raw: bool) -> str:
    number = getattr(sdk_obj, "number", "") or ""
    anc = _anchor(DnType.TRUNK, number)

    parts: list[str] = [
        f'<a id="{anc}"></a>',
        "",
        f"### 📡 Trunk {number}",
    ]

    props = _trunk_props(sdk_obj)
    if props:
        parts.append("")
        parts.append(_props_table(props))

    # DID numbers list (collapsed if long)
    did_numbers: list[str] = getattr(sdk_obj, "did_numbers", None) or []
    if did_numbers:
        parts.append("")
        if len(did_numbers) <= 6:
            parts.append("**DID Numbers:** " + ", ".join(f"`{d}`" for d in did_numbers))
        else:
            inner = ", ".join(f"`{d}`" for d in did_numbers)
            parts.append(
                f"<details><summary><strong>DID Numbers</strong> ({len(did_numbers)} numbers)</summary>\n\n"
                f"{inner}\n\n</details>"
            )

    # Inbound routing rules
    rules = getattr(sdk_obj, "routing_rules", None) or []
    # Skip rules where all three destinations are None / empty
    meaningful_rules = [
        r for r in rules
        if _dest_is_set(r.office_hours_destination)
        or _dest_is_set(r.out_of_office_hours_destination)
        or _dest_is_set(r.holidays_destination)
    ]
    parts.append("")
    parts.append("**Inbound Rules:**")
    parts.append("")
    if meaningful_rules:
        trows = ["| DID | Rule Name | Office Hours | Out of Office | Holidays |", "|---|---|---|---|---|"]
        for rule in meaningful_rules:
            did_label = f"`{rule.data}`" if rule.data else "*(default)*"
            rule_name = rule.rule_name or ""
            oh = _dest_cell(rule.office_hours_destination, adapter)
            ooh = _dest_cell(rule.out_of_office_hours_destination, adapter)
            hol = _dest_cell(rule.holidays_destination, adapter)
            trows.append(f"| {did_label} | {rule_name} | {oh} | {ooh} | {hol} |")
        parts.append("\n".join(trows))
    else:
        parts.append("*No inbound routing rules configured.*")

    if include_raw:
        try:
            data = sdk_obj.model_dump(by_alias=True, exclude_none=True)
            raw_json = json.dumps(data, indent=2, default=str)
        except Exception as exc:
            raw_json = f"(could not serialize: {exc})"
        parts += ["", "<details>", "<summary>Raw API data</summary>", "", f"```json\n{raw_json}\n```", "", "</details>"]

    return "\n".join(parts)


def _render_trunk_section(adapter: ThreeCXAdapter, include_raw: bool) -> str:
    trunks = adapter.all_trunks
    if not trunks:
        return ""
    sorted_items = sorted(trunks.items(), key=lambda kv: kv[0].zfill(20))
    parts = ["## 📡 Trunks\n"]
    for _number, sdk_obj in sorted_items:
        parts.append(_render_trunk(sdk_obj, adapter, include_raw))
        parts.append("\n---\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# FXS-specific rendering
# ---------------------------------------------------------------------------

def _render_fxs(sdk_obj: Any, include_raw: bool) -> str:
    mac = getattr(sdk_obj, "mac_address", "") or ""
    name = _sdk_name(sdk_obj)
    anc = _anchor(DnType.FXS, mac.replace(":", ""))

    parts: list[str] = [
        f'<a id="{anc}"></a>',
        "",
        f"### 📟 {name} ({mac})",
    ]

    props = _fxs_props(sdk_obj)
    if props:
        parts.append("")
        parts.append(_props_table(props))

    lines = getattr(sdk_obj, "fxs_lines", None) or []
    parts.append("")
    parts.append("**Lines:**")
    parts.append("")
    if lines:
        lrows = ["| Line | Extension | Name |", "|---|---|---|"]
        for line in sorted(lines, key=lambda l: l.key or 0):
            lrows.append(f"| {line.key or '?'} | `{line.number or '—'}` | {line.name or '—'} |")
        parts.append("\n".join(lrows))
    else:
        parts.append("*No lines configured.*")

    if include_raw:
        try:
            data = sdk_obj.model_dump(by_alias=True, exclude_none=True)
            raw_json = json.dumps(data, indent=2, default=str)
        except Exception as exc:
            raw_json = f"(could not serialize: {exc})"
        parts += ["", "<details>", "<summary>Raw API data</summary>", "", f"```json\n{raw_json}\n```", "", "</details>"]

    return "\n".join(parts)


def _render_fxs_section(adapter: ThreeCXAdapter, include_raw: bool) -> str:
    fxs = adapter.all_fxs_devices
    if not fxs:
        return ""
    sorted_items = sorted(fxs.items(), key=lambda kv: kv[0])
    parts = ["## 📟 FXS Devices\n"]
    for _mac, sdk_obj in sorted_items:
        parts.append(_render_fxs(sdk_obj, include_raw))
        parts.append("\n---\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# System extensions — rendered as a flat table (no per-entry headings)
# ---------------------------------------------------------------------------

def _render_system_extensions_section(adapter: ThreeCXAdapter) -> str:
    """
    Show system extensions not already listed in another section
    (parking *0/*1, echo test, voicemail system, etc.).
    """
    sysexts = adapter.all_system_extensions
    if not sysexts:
        return ""

    # Filter out entries already covered by another category
    novel = {
        num: obj for num, obj in sysexts.items()
        if not adapter.is_known_dn(num)
    }
    if not novel:
        return ""

    sorted_items = sorted(novel.items(), key=lambda kv: kv[0].zfill(20))
    rows = ["| Number | Name | Type | Registered |", "|---|---|---|---|"]
    for num, obj in sorted_items:
        name = getattr(obj, "name", "") or ""
        ext_type = getattr(obj, "type", "") or ""
        registered = getattr(obj, "is_registered", None)
        reg_str = "Yes" if registered else ("No" if registered is False else "—")
        rows.append(f"| `{num}` | {name} | {ext_type} | {reg_str} |")

    return "## 🔧 System Extensions\n\n" + "\n".join(rows) + "\n"


# ---------------------------------------------------------------------------
# Generic per-entity rendering (IVR, queue, ring group, CFA, group, user)
# ---------------------------------------------------------------------------

def _render_entity(
    dn_type: DnType,
    sdk_obj: Any,
    adapter: ThreeCXAdapter,
    include_raw: bool,
) -> str:
    number = getattr(sdk_obj, "number", "") or ""
    name = _sdk_name(sdk_obj)
    icon = _ICONS.get(dn_type, "•")
    anc = _anchor(dn_type, number)

    if dn_type == DnType.USER:
        props = _user_props(sdk_obj)
        routes = _user_routes(sdk_obj, adapter)
    elif dn_type == DnType.QUEUE:
        props = _queue_props(sdk_obj)
        routes = _queue_routes(sdk_obj, adapter)
    elif dn_type == DnType.RING_GROUP:
        props = _ring_group_props(sdk_obj)
        routes = _ring_group_routes(sdk_obj, adapter)
    elif dn_type == DnType.IVR:
        props = _ivr_props(sdk_obj)
        routes = _ivr_routes(sdk_obj, adapter)
    elif dn_type == DnType.GROUP:
        props = _group_props(sdk_obj)
        routes = _group_routes(sdk_obj, adapter)
    elif dn_type == DnType.CALL_FLOW_APP:
        props = _cfa_props(sdk_obj)
        routes = []
    else:
        props = {}
        routes = []

    parts: list[str] = [
        f'<a id="{anc}"></a>',
        "",
        f"### {icon} {name} ({number})",
    ]

    if props:
        parts.append("")
        parts.append(_props_table(props))

    if dn_type != DnType.CALL_FLOW_APP:
        parts.append("")
        parts.append("**Routing:**")
        parts.append("")
        parts.append(_routes_table(routes))

    if include_raw:
        try:
            if hasattr(sdk_obj, "model_dump"):
                data = sdk_obj.model_dump(by_alias=True, exclude_none=True)
            else:
                data = vars(sdk_obj)
            raw_json = json.dumps(data, indent=2, default=str)
        except Exception as exc:
            raw_json = f"(could not serialize: {exc})"
        parts += [
            "", "<details>", "<summary>Raw API data</summary>",
            "", f"```json\n{raw_json}\n```", "", "</details>",
        ]

    return "\n".join(parts)


def _render_section(
    title: str,
    icon: str,
    dn_type: DnType,
    items: dict[str, Any],
    adapter: ThreeCXAdapter,
    include_raw: bool,
) -> str:
    if not items:
        return ""
    sorted_items = sorted(items.items(), key=lambda kv: kv[0].zfill(20))
    parts = [f"## {icon} {title}\n"]
    for _number, sdk_obj in sorted_items:
        parts.append(_render_entity(dn_type, sdk_obj, adapter, include_raw))
        parts.append("\n---\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Table of Contents
# ---------------------------------------------------------------------------

def _toc_entry_number(dn_type: DnType, sdk_obj: Any) -> str:
    number = getattr(sdk_obj, "number", "") or ""
    name = _sdk_name(sdk_obj)
    anc = _anchor(dn_type, number)
    return f"  - [{name} ({number})](#{anc})"


def _toc_entry_mac(sdk_obj: Any) -> str:
    mac = getattr(sdk_obj, "mac_address", "") or ""
    name = _sdk_name(sdk_obj)
    anc = _anchor(DnType.FXS, mac.replace(":", ""))
    return f"  - [{name} ({mac})](#{anc})"


def _render_toc(adapter: ThreeCXAdapter) -> str:
    lines = ["## Table of Contents\n"]

    if adapter.all_trunks:
        lines.append("- [Trunks](#trunks)")
        for _n, obj in sorted(adapter.all_trunks.items(), key=lambda kv: kv[0].zfill(20)):
            lines.append(_toc_entry_number(DnType.TRUNK, obj))

    plain_sections = [
        ("IVR / Digital Receptionists", "#ivr--digital-receptionists", DnType.IVR, adapter.all_receptionists),
        ("Ring Groups", "#ring-groups", DnType.RING_GROUP, adapter.all_ring_groups),
        ("Queues", "#queues", DnType.QUEUE, adapter.all_queues),
        ("Call Flow Apps", "#call-flow-apps", DnType.CALL_FLOW_APP, adapter.all_call_flow_apps),
        ("Groups", "#groups", DnType.GROUP, adapter.all_groups),
        ("Extensions", "#extensions", DnType.USER, adapter.all_users),
    ]
    for title, section_anchor, dn_type, items in plain_sections:
        if not items:
            continue
        lines.append(f"- [{title}]({section_anchor})")
        for _n, obj in sorted(items.items(), key=lambda kv: kv[0].zfill(20)):
            lines.append(_toc_entry_number(dn_type, obj))

    if adapter.all_fxs_devices:
        lines.append("- [FXS Devices](#fxs-devices)")
        for _mac, obj in sorted(adapter.all_fxs_devices.items()):
            lines.append(_toc_entry_mac(obj))

    # System extensions — only show if there are novel ones
    novel_sysexts = {n: o for n, o in adapter.all_system_extensions.items() if not adapter.is_known_dn(n)}
    if novel_sysexts:
        lines.append("- [System Extensions](#system-extensions)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_directory(
    adapter: ThreeCXAdapter,
    server_name: str,
    include_raw: bool = False,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    total = (
        len(adapter.all_trunks)
        + len(adapter.all_receptionists)
        + len(adapter.all_ring_groups)
        + len(adapter.all_queues)
        + len(adapter.all_call_flow_apps)
        + len(adapter.all_groups)
        + len(adapter.all_users)
        + len(adapter.all_fxs_devices)
        + len(adapter.all_system_extensions)
    )

    sections: list[str] = [
        f"# 3CX Routing Directory\n\n"
        f"**Server:** {server_name}  \n"
        f"**Generated:** {now}  \n"
        f"**Entries:** {total}  \n",

        _render_toc(adapter),

        "---\n",

        _render_trunk_section(adapter, include_raw),

        _render_section(
            "IVR / Digital Receptionists", "🎛️", DnType.IVR,
            adapter.all_receptionists, adapter, include_raw,
        ),
        _render_section(
            "Ring Groups", "🔔", DnType.RING_GROUP,
            adapter.all_ring_groups, adapter, include_raw,
        ),
        _render_section(
            "Queues", "📋", DnType.QUEUE,
            adapter.all_queues, adapter, include_raw,
        ),
        _render_section(
            "Call Flow Apps", "⚙️", DnType.CALL_FLOW_APP,
            adapter.all_call_flow_apps, adapter, include_raw,
        ),
        _render_section(
            "Groups", "🏢", DnType.GROUP,
            adapter.all_groups, adapter, include_raw,
        ),
        _render_section(
            "Extensions", "👤", DnType.USER,
            adapter.all_users, adapter, include_raw,
        ),

        _render_fxs_section(adapter, include_raw),
        _render_system_extensions_section(adapter),
    ]

    return "\n".join(s for s in sections if s)
