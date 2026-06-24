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


def _department_value(number: str, adapter: ThreeCXAdapter) -> Optional[str]:
    """
    Render the department(s) a DN (queue, ring group, IVR, CFA) belongs to as
    a comma-separated list of links to each group's section.
    """
    groups = adapter.groups_for(number)
    if not groups:
        return None
    ordered = sorted(groups, key=lambda g: (getattr(g, "name", None) or getattr(g, "number", "") or "").lower())
    return ", ".join(_group_link(g) for g in ordered)


def _group_link(group: Any) -> str:
    gname = getattr(group, "name", None) or getattr(group, "number", None) or "?"
    gnum = getattr(group, "number", None) or ""
    return f"[🏢 {gname}](#{_anchor(DnType.GROUP, gnum)})" if gnum else f"🏢 {gname}"


def _peer_link(number: str, name: Optional[str], adapter: ThreeCXAdapter) -> str:
    """
    Render a member/agent (name + number) as a clickable link to its DN
    section, falling back to plain text when the number is not a known DN.
    """
    result = adapter.find_dn(number)
    if result:
        dn_type, sdk = result
        label = name or _sdk_name(sdk)
        return f"[{label} ({number})](#{_anchor(dn_type, number)})"
    return f"{name} ({number})" if name else number


# 3CX group role names -> human-readable labels.
_ROLE_LABELS: dict[str, str] = {
    "system": "System",
    "users": "User",
    "observers": "Observer",
    "receptionists": "Receptionist",
    "supervisors": "Supervisor",
    "group_admins": "Department Administrator",
    "managers": "Manager",
    "group_owners": "Owner",
    "system_admins": "System Administrator",
    "system_owners": "System Owner",
}


def _role_label(role_name: Optional[str]) -> Optional[str]:
    if not role_name:
        return None
    return _ROLE_LABELS.get(role_name, role_name.replace("_", " ").title())


def _membership_role(user_group: Any) -> Optional[str]:
    rights = getattr(user_group, "group_rights", None) or getattr(user_group, "rights", None)
    return getattr(rights, "role_name", None) if rights else None


def _user_department_value(sdk_obj: Any, adapter: ThreeCXAdapter) -> Optional[str]:
    """
    Render a user's department(s) from its own expanded ``Groups`` collection.

    Only genuine memberships are shown — "observers" entries are monitoring
    rights, not real department membership, so they are dropped. The primary
    (main) department is highlighted (bold + ★) and listed first; each entry
    notes the user's role in that department.
    """
    primary_id = getattr(sdk_obj, "primary_group_id", None)
    memberships = getattr(sdk_obj, "groups", None) or []

    # (group_id, role_name) for each genuine (non-observer) membership.
    genuine = [
        (getattr(ug, "group_id", None), _membership_role(ug))
        for ug in memberships
        if _membership_role(ug) != "observers"
    ]

    # Fall back to the primary group alone if no genuine membership is present.
    if not genuine:
        group = adapter.group_by_id(primary_id)
        return f"**{_group_link(group)} ★**" if group is not None else None

    def _sort_key(item: tuple[Optional[int], Optional[str]]) -> tuple[int, str]:
        gid, _ = item
        group = adapter.group_by_id(gid)
        name = (getattr(group, "name", None) or "") if group else ""
        return (0 if gid == primary_id else 1, name.lower())

    parts: list[str] = []
    for gid, role in sorted(genuine, key=_sort_key):
        group = adapter.group_by_id(gid)
        if group is None:
            continue
        rl = _role_label(role)
        text = _group_link(group) + (f" ({rl})" if rl else "")
        if gid == primary_id:
            text = f"**{text} ★**"
        parts.append(text)

    return ", ".join(parts) if parts else None


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


def _dest_target(dest: Optional[Destination], adapter: ThreeCXAdapter) -> Optional[tuple[DnType, str]]:
    """
    Resolve a destination to the internal DN it points at, as (DnType, number),
    or None for external / unset / unresolvable destinations. Used to build the
    reverse "referenced by" index.
    """
    if not _dest_is_set(dest) or dest.to == DestinationType.external:
        return None
    number = dest.number or ""
    if not number:
        return None
    result = adapter.find_dn(number)
    return (result[0], number) if result else None


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

def _queue_edges(sdk_obj: Any) -> list[tuple[str, Destination]]:
    edges: list[tuple[str, Optional[Destination]]] = [
        ("Holidays", _route_dest(sdk_obj.holidays_route)),
        ("Out of Office", _route_dest(sdk_obj.out_of_office_route)),
        ("Break", _route_dest(sdk_obj.break_route)),
        ("No Answer / Timeout", sdk_obj.forward_no_answer),
    ]
    return [(label, dest) for label, dest in edges if _dest_is_set(dest)]


def _ring_group_edges(sdk_obj: Any) -> list[tuple[str, Destination]]:
    edges: list[tuple[str, Optional[Destination]]] = [
        ("Holidays", _route_dest(sdk_obj.holidays_route)),
        ("Out of Office", _route_dest(sdk_obj.out_of_office_route)),
        ("Break", _route_dest(sdk_obj.break_route)),
        ("No Answer / Timeout", sdk_obj.forward_no_answer),
    ]
    return [(label, dest) for label, dest in edges if _dest_is_set(dest)]


def _ivr_edges(sdk_obj: Any) -> list[tuple[str, Destination]]:
    edges: list[tuple[str, Optional[Destination]]] = [
        ("Holidays", _route_dest(sdk_obj.holidays_route)),
        ("Out of Office", _route_dest(sdk_obj.out_of_office_route)),
        ("Break", _route_dest(sdk_obj.break_route)),
    ]

    for fwd in sdk_obj.forwards or []:
        edges.append((f"Key {fwd.input or '?'}", _fwd_to_dest(fwd)))

    if sdk_obj.timeout_forward_dn:
        dest_type = _fwd_type_to_dest_type(sdk_obj.timeout_forward_type)
        edges.append((
            "Timeout",
            Destination(
                number=sdk_obj.timeout_forward_dn,
                to=dest_type,
                type=sdk_obj.timeout_forward_peer_type,
                external=sdk_obj.timeout_forward_dn if dest_type == DestinationType.external else None,
            ),
        ))

    if sdk_obj.invalid_key_forward_dn:
        edges.append((
            "Invalid Key",
            Destination(
                number=sdk_obj.invalid_key_forward_dn,
                to=DestinationType.extension,
                type=PeerType.extension,
            ),
        ))

    return [(label, dest) for label, dest in edges if _dest_is_set(dest)]


def _group_edges(sdk_obj: Any) -> list[tuple[str, Destination]]:
    edges: list[tuple[str, Optional[Destination]]] = [
        ("Office Hours", _route_dest(sdk_obj.office_route)),
        ("Out of Office", _route_dest(sdk_obj.out_of_office_route)),
        ("Holidays", _route_dest(sdk_obj.holidays_route)),
        ("Break", _route_dest(sdk_obj.break_route)),
    ]
    return [(label, dest) for label, dest in edges if _dest_is_set(dest)]


def _user_edges(sdk_obj: Any) -> list[tuple[str, Destination]]:
    """Outbound forwarding destinations of a user, across all profiles + exceptions."""
    current = getattr(sdk_obj, "current_profile_name", None) or "Available"
    edges: list[tuple[str, Optional[Destination]]] = []

    for profile in sdk_obj.forwarding_profiles or []:
        pname = profile.name or "Profile"
        pname_str = pname.value if hasattr(pname, "value") else str(pname)
        pfx = f"[{pname_str}{' ★' if pname_str == current else ''}]"

        avail: Optional[AvailableRouting] = profile.available_route
        if avail:
            edges += [
                (f"{pfx} Busy (External)", avail.busy_external),
                (f"{pfx} Busy (Internal)", avail.busy_internal),
                (f"{pfx} No Answer (External)", avail.no_answer_external),
                (f"{pfx} No Answer (Internal)", avail.no_answer_internal),
                (f"{pfx} Not Registered (Ext)", avail.not_registered_external),
                (f"{pfx} Not Registered (Int)", avail.not_registered_internal),
            ]

        away: Optional[AwayRouting] = profile.away_route
        if away:
            edges += [
                (f"{pfx} Away (External)", away.external),
                (f"{pfx} Away (Internal)", away.internal),
            ]

    for rule in sdk_obj.forwarding_exceptions or []:
        if not getattr(rule, "enabled", False):
            continue
        cond = rule.condition.value if rule.condition else "?"
        ctype = f" ({rule.call_type.value})" if rule.call_type else ""
        edges.append((f"[Exception] {cond}{ctype}", rule.destination))

    return [(label, dest) for label, dest in edges if _dest_is_set(dest)]


def _trunk_edges(sdk_obj: Any) -> list[tuple[str, Destination]]:
    """Inbound-rule destinations of a trunk, one edge per (rule, time condition)."""
    edges: list[tuple[str, Optional[Destination]]] = []
    for rule in getattr(sdk_obj, "routing_rules", None) or []:
        did = f"`{rule.data}`" if getattr(rule, "data", None) else "default"
        name = getattr(rule, "rule_name", None) or did
        edges += [
            (f"{name} — Office Hours", rule.office_hours_destination),
            (f"{name} — Out of Office", rule.out_of_office_hours_destination),
            (f"{name} — Holidays", rule.holidays_destination),
        ]
    return [(label, dest) for label, dest in edges if _dest_is_set(dest)]


# ---------------------------------------------------------------------------
# Reverse "referenced by" index — who routes calls toward each DN
# ---------------------------------------------------------------------------

# An inbound reference: (source_dn_type, source_number, source_name, trigger_label)
Reference = tuple[DnType, str, str, str]
ReferenceIndex = dict[str, list[Reference]]


def build_reference_index(adapter: ThreeCXAdapter) -> ReferenceIndex:
    """
    Walk every routing-capable entity's outbound edges and invert them into a
    map of target anchor → list of inbound references. This lets each entity's
    section list who sends calls toward it, making the directory navigable in
    both directions.
    """
    index: ReferenceIndex = {}

    def collect(source_type: DnType, source_obj: Any, edges: list[tuple[str, Destination]]) -> None:
        snum = getattr(source_obj, "number", "") or ""
        sname = _sdk_name(source_obj)
        for label, dest in edges:
            target = _dest_target(dest, adapter)
            if target is None:
                continue
            t_type, t_num = target
            # Skip pure self-references (e.g. a queue's own voicemail).
            if t_type == source_type and t_num == snum:
                continue
            index.setdefault(_anchor(t_type, t_num), []).append((source_type, snum, sname, label))

    for obj in adapter.all_queues.values():
        collect(DnType.QUEUE, obj, _queue_edges(obj))
    for obj in adapter.all_ring_groups.values():
        collect(DnType.RING_GROUP, obj, _ring_group_edges(obj))
    for obj in adapter.all_receptionists.values():
        collect(DnType.IVR, obj, _ivr_edges(obj))
    for obj in adapter.all_groups.values():
        collect(DnType.GROUP, obj, _group_edges(obj))
    for obj in adapter.all_users.values():
        collect(DnType.USER, obj, _user_edges(obj))
    for obj in adapter.all_trunks.values():
        collect(DnType.TRUNK, obj, _trunk_edges(obj))

    return index


def _referenced_by_section(anchor: str, references: ReferenceIndex) -> str:
    """Render the 'Referenced By' table for the entity at the given anchor."""
    refs = references.get(anchor)
    if not refs:
        return ""
    # Deduplicate and order deterministically by source type, number, then trigger.
    ordered = sorted(set(refs), key=lambda r: (r[0].value, r[1].zfill(20), r[3]))
    rows = ["| Source | Trigger |", "|---|---|"]
    for s_type, s_num, s_name, label in ordered:
        icon = _ICONS.get(s_type, "•")
        if s_num:
            link = f"[{icon} {s_name} ({s_num})](#{_anchor(s_type, s_num)})"
        else:
            link = f"{icon} {s_name}"
        rows.append(f"| {link} | {label} |")
    return "#### Referenced By\n\n" + "\n".join(rows)


# ---------------------------------------------------------------------------
# Group office-hours schedule and holidays
# ---------------------------------------------------------------------------

_WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _fmt_time(t: Any) -> str:
    if t is None:
        return "?"
    return t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)


def _period_range(start: Any, stop: Any) -> str:
    return f"{_fmt_time(start)}–{_fmt_time(stop)}"


def _schedule_section(schedule: Any, heading: str) -> str:
    """Render a Schedule (office hours / break time) as a per-day table."""
    periods = getattr(schedule, "periods", None) or [] if schedule else []
    if not periods:
        return ""

    # Group time ranges by weekday.
    by_day: dict[str, list[str]] = {}
    for p in periods:
        dow = getattr(p, "day_of_week", None)
        day = dow.value if hasattr(dow, "value") else str(dow or "?")
        by_day.setdefault(day, []).append(_period_range(getattr(p, "start", None), getattr(p, "stop", None)))

    stype = getattr(schedule, "type", None)
    stype_str = stype.value if hasattr(stype, "value") else (str(stype) if stype else "")
    header = heading + (f" ({stype_str})" if stype_str else "")

    rows = ["| Day | Hours |", "|---|---|"]
    for day in _WEEKDAY_ORDER:
        rows.append(f"| {day} | {', '.join(by_day[day]) if day in by_day else 'Closed'} |")
    # Any non-standard day labels not in the canonical week order.
    for day in by_day:
        if day not in _WEEKDAY_ORDER:
            rows.append(f"| {day} | {', '.join(by_day[day])} |")

    return header + "\n\n" + "\n".join(rows)


def _group_hours_section(sdk_obj: Any) -> str:
    return _schedule_section(getattr(sdk_obj, "hours", None), "#### Office Hours")


def _group_break_section(sdk_obj: Any) -> str:
    return _schedule_section(getattr(sdk_obj, "break_time", None), "#### Break Hours")


def _holiday_dates(hd: Any) -> str:
    d, m = getattr(hd, "day", None), getattr(hd, "month", None)
    de, me = getattr(hd, "day_end", None), getattr(hd, "month_end", None)

    def _one(day: Any, month: Any) -> str:
        if not month:
            return "?"
        mon = _MONTHS[month] if 1 <= month < len(_MONTHS) else str(month)
        return f"{day:02d} {mon}" if day else mon

    start = _one(d, m)
    end = _one(de, me)
    label = start if end in (start, "?") else f"{start} – {end}"

    if not getattr(hd, "is_recurrent", False):
        year = getattr(hd, "year", None)
        if year and year > 1:
            label += f" {year}"
    return label


def _group_holidays_section(sdk_obj: Any) -> str:
    """Render the department's configured holidays, if any."""
    holidays = getattr(sdk_obj, "office_holidays", None) or []
    if not holidays:
        return ""

    rows = ["| Holiday | Dates | Recurring |", "|---|---|---|"]
    for hd in holidays:
        name = getattr(hd, "name", None) or "—"
        dates = _holiday_dates(hd)
        recurring = "Yes" if getattr(hd, "is_recurrent", False) else "No"
        rows.append(f"| {name} | {dates} | {recurring} |")

    return "#### Holidays\n\n" + "\n".join(rows)


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


def _queue_props(sdk_obj: Any, adapter: ThreeCXAdapter) -> dict[str, Any]:
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
            _peer_link(a.number, a.name, adapter) for a in agents if a.number
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


def _edges_to_rows(
    edges: list[tuple[str, Destination]], adapter: ThreeCXAdapter
) -> list[tuple[str, str]]:
    """Render each (label, Destination) edge to a (label, markdown-link) row."""
    rows: list[tuple[str, str]] = []
    for label, dest in edges:
        link = _dest_link(dest, adapter)
        if link:
            rows.append((label, link))
    return rows


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

def _render_trunk(
    sdk_obj: Any,
    adapter: ThreeCXAdapter,
    include_raw: bool,
    references: ReferenceIndex,
) -> str:
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

    ref_section = _referenced_by_section(anc, references)
    if ref_section:
        parts.append("")
        parts.append(ref_section)

    if include_raw:
        try:
            data = sdk_obj.model_dump(by_alias=True, exclude_none=True)
            raw_json = json.dumps(data, indent=2, default=str)
        except Exception as exc:
            raw_json = f"(could not serialize: {exc})"
        parts += ["", "<details>", "<summary>Raw API data</summary>", "", f"```json\n{raw_json}\n```", "", "</details>"]

    return "\n".join(parts)


def _render_trunk_section(adapter: ThreeCXAdapter, include_raw: bool, references: ReferenceIndex) -> str:
    trunks = adapter.all_trunks
    if not trunks:
        return ""
    sorted_items = sorted(trunks.items(), key=lambda kv: kv[0].zfill(20))
    parts = ["## 📡 Trunks\n"]
    for _number, sdk_obj in sorted_items:
        parts.append(_render_trunk(sdk_obj, adapter, include_raw, references))
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
# User-specific rich rendering
# ---------------------------------------------------------------------------

def _schedule_label(schedule: Any) -> str:
    if schedule is None:
        return "—"
    stype = getattr(schedule, "type", None)
    if stype is None:
        return "—"
    return stype.value if hasattr(stype, "value") else str(stype)


def _render_forwarding_profile(
    profile: Any,
    current_profile_name: str,
    adapter: ThreeCXAdapter,
) -> str:
    pname = getattr(profile, "name", None) or "Profile"
    pname_str = pname.value if hasattr(pname, "value") else str(pname)
    marker = " ★" if pname_str == current_profile_name else ""

    meta_parts: list[str] = []
    timeout = getattr(profile, "no_answer_timeout", None)
    if timeout:
        meta_parts.append(f"No Answer: {timeout}s")
    ring_mobile = getattr(profile, "ring_my_mobile", None)
    if ring_mobile:
        meta_parts.append("Ring Mobile: Yes")
    multi = getattr(profile, "accept_multiple_calls", None)
    if multi:
        meta_parts.append("Multiple Calls: Yes")
    meta = " | ".join(meta_parts) if meta_parts else ""

    header = f"**{pname_str}{marker}**"
    if meta:
        header += f" — {meta}"

    rows: list[tuple[str, str]] = []
    avail = getattr(profile, "available_route", None)
    if avail:
        for label, dest in [
            ("Busy (External)", getattr(avail, "busy_external", None)),
            ("Busy (Internal)", getattr(avail, "busy_internal", None)),
            ("No Answer (External)", getattr(avail, "no_answer_external", None)),
            ("No Answer (Internal)", getattr(avail, "no_answer_internal", None)),
            ("Not Registered (Ext)", getattr(avail, "not_registered_external", None)),
            ("Not Registered (Int)", getattr(avail, "not_registered_internal", None)),
        ]:
            if _dest_is_set(dest):
                link = _dest_link(dest, adapter)
                if link:
                    rows.append((label, link))

    away = getattr(profile, "away_route", None)
    if away:
        all_ext = getattr(away, "all_hours_external", False)
        all_int = getattr(away, "all_hours_internal", False)
        for label, dest in [
            ("Away — All (External)" if all_ext else "Away (External)", getattr(away, "external", None)),
            ("Away — All (Internal)" if all_int else "Away (Internal)", getattr(away, "internal", None)),
        ]:
            if _dest_is_set(dest):
                link = _dest_link(dest, adapter)
                if link:
                    rows.append((label, link))

    parts = [header, ""]
    if rows:
        trows = ["| Trigger | Destination |", "|---|---|"]
        for label, link in rows:
            trows.append(f"| {label} | {link} |")
        parts.append("\n".join(trows))
    else:
        parts.append("*Phone rings normally — no special routing.*")

    return "\n".join(parts)


def _user_exceptions_section(sdk_obj: Any, adapter: ThreeCXAdapter) -> str:
    exceptions = getattr(sdk_obj, "forwarding_exceptions", None) or []
    active = [r for r in exceptions if getattr(r, "enabled", False)]
    if not active:
        return ""

    rows = ["| Condition | Call Type | Hours | Destination |", "|---|---|---|---|"]
    for rule in active:
        cond = getattr(rule, "condition", None)
        cond_str = cond.value if hasattr(cond, "value") else str(cond or "?")
        ctype = getattr(rule, "call_type", None)
        ctype_str = ctype.value if hasattr(ctype, "value") else (str(ctype) if ctype else "All")
        hours_sched = getattr(rule, "hours", None)
        hours_str = _schedule_label(hours_sched)
        dest = getattr(rule, "destination", None)
        link = _dest_cell(dest, adapter)
        rows.append(f"| {cond_str} | {ctype_str} | {hours_str} | {link} |")

    return "#### Forwarding Exceptions\n\n" + "\n".join(rows)


def _user_greetings_section(sdk_obj: Any) -> str:
    greetings = getattr(sdk_obj, "greetings", None) or []
    if not greetings:
        return ""

    rows = ["| Type | Name | File |", "|---|---|---|"]
    for g in greetings:
        gtype = getattr(g, "type", None)
        gtype_str = gtype.value if hasattr(gtype, "value") else str(gtype or "—")
        gname = getattr(g, "name", None) or "—"
        gfile = getattr(g, "file_name", None) or getattr(g, "filename", None) or "—"
        rows.append(f"| {gtype_str} | {gname} | `{gfile}` |")

    return "#### Greetings\n\n" + "\n".join(rows)


def _render_user_entity(
    sdk_obj: Any,
    adapter: ThreeCXAdapter,
    include_raw: bool,
    references: ReferenceIndex,
) -> str:
    number = getattr(sdk_obj, "number", "") or ""
    name = _sdk_name(sdk_obj)
    anc = _anchor(DnType.USER, number)

    parts: list[str] = [
        f'<a id="{anc}"></a>',
        "",
        f"### 👤 {name} ({number})",
        "",
    ]

    # ── Properties ────────────────────────────────────────────────────
    props: dict[str, Any] = {}
    dept = _user_department_value(sdk_obj, adapter)
    if dept:
        props["Department"] = dept

    # Account status — only flag the non-default (disabled) case.
    if getattr(sdk_obj, "enabled", None) is False:
        props["Status"] = "Disabled"

    for key, attr in [
        ("Email", "email_address"),
        ("Mobile", "mobile"),
        ("Outbound Caller ID", "outbound_caller_id"),
    ]:
        v = getattr(sdk_obj, attr, None)
        if v:
            props[key] = v

    lang = getattr(sdk_obj, "language", None)
    if lang:
        props["Language"] = str(lang).upper()

    current_profile = getattr(sdk_obj, "current_profile_name", None)
    if current_profile:
        props["Current Profile"] = current_profile

    qs = getattr(sdk_obj, "queue_status", None)
    if qs is not None:
        qs_val = qs.value if hasattr(qs, "value") else str(qs)
        props["Queue Status"] = {"LoggedIn": "Logged in", "LoggedOut": "Logged out"}.get(qs_val, qs_val)

    prompt_set = getattr(sdk_obj, "prompt_set", None)
    if prompt_set:
        props["Prompt Set"] = adapter.prompt_set_name(prompt_set) or prompt_set

    if getattr(sdk_obj, "is_registered", None) is False:
        props["Registered"] = "No"

    vm = getattr(sdk_obj, "vm_enabled", None)
    if vm is not None:
        props["Voicemail"] = vm
    vm_email = getattr(sdk_obj, "vm_email_options", None)
    if vm_email is not None:
        props["VM Email"] = vm_email

    # Call recording.
    if getattr(sdk_obj, "record_calls", None):
        props["Call Recording"] = (
            "External calls only" if getattr(sdk_obj, "record_external_calls_only", None) else "Yes"
        )

    # Notable on/off features — shown only when enabled.
    for key, attr in [
        ("Call Screening", "call_screening"),
        ("Hidden in Phonebook", "hide_in_phonebook"),
        ("Hotdesking", "enable_hotdesking"),
        ("AI Agent", "ai_agent"),
        ("Email on Missed Calls", "send_email_missed_calls"),
    ]:
        if getattr(sdk_obj, attr, None) is True:
            props[key] = "Yes"

    # Two-factor authentication.
    if getattr(sdk_obj, "require2_fa", None) is True:
        props["2FA"] = "Required"
    elif getattr(sdk_obj, "enable2_fa", None) is True:
        props["2FA"] = "Enabled"

    # Tags.
    tags = getattr(sdk_obj, "tags", None) or []
    if tags:
        tag_names = [t.value if hasattr(t, "value") else str(t) for t in tags]
        props["Tags"] = ", ".join(n for n in tag_names if n)

    # Provisioned phones / devices.
    phones = getattr(sdk_obj, "phones", None) or []
    if phones:
        labels = []
        for p in phones:
            label = getattr(p, "template_name", None) or getattr(p, "name", None) or getattr(p, "mac_address", None)
            if label:
                labels.append(str(label))
        props["Phones"] = ", ".join(labels) if labels else str(len(phones))

    hours_sched = getattr(sdk_obj, "hours", None)
    if hours_sched is not None:
        label = _schedule_label(hours_sched)
        if label != "—":
            props["Office Hours"] = label
    break_sched = getattr(sdk_obj, "break_time", None)
    if break_sched is not None:
        label = _schedule_label(break_sched)
        if label != "—":
            props["Break Time"] = label

    if props:
        parts.append(_props_table(props))
        parts.append("")

    # ── Greetings ─────────────────────────────────────────────────────
    greetings_section = _user_greetings_section(sdk_obj)
    if greetings_section:
        parts.append(greetings_section)
        parts.append("")

    # ── Forwarding profiles ───────────────────────────────────────────
    profiles = getattr(sdk_obj, "forwarding_profiles", None) or []
    current = getattr(sdk_obj, "current_profile_name", None) or ""
    if profiles:
        parts.append("#### Forwarding Profiles")
        parts.append("")
        for profile in profiles:
            parts.append(_render_forwarding_profile(profile, current, adapter))
            parts.append("")

    # ── Forwarding exceptions ─────────────────────────────────────────
    exc_section = _user_exceptions_section(sdk_obj, adapter)
    if exc_section:
        parts.append(exc_section)
        parts.append("")

    # ── Referenced by ─────────────────────────────────────────────────
    ref_section = _referenced_by_section(anc, references)
    if ref_section:
        parts.append(ref_section)
        parts.append("")

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
            "<details>", "<summary>Raw API data</summary>",
            "", f"```json\n{raw_json}\n```", "", "</details>", "",
        ]

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Generic per-entity rendering (IVR, queue, ring group, CFA, group, user)
# ---------------------------------------------------------------------------

def _render_entity(
    dn_type: DnType,
    sdk_obj: Any,
    adapter: ThreeCXAdapter,
    include_raw: bool,
    references: ReferenceIndex,
) -> str:
    if dn_type == DnType.USER:
        return _render_user_entity(sdk_obj, adapter, include_raw, references)

    number = getattr(sdk_obj, "number", "") or ""
    name = _sdk_name(sdk_obj)
    icon = _ICONS.get(dn_type, "•")
    anc = _anchor(dn_type, number)

    if dn_type == DnType.QUEUE:
        props = _queue_props(sdk_obj, adapter)
        routes = _edges_to_rows(_queue_edges(sdk_obj), adapter)
    elif dn_type == DnType.RING_GROUP:
        props = _ring_group_props(sdk_obj)
        routes = _edges_to_rows(_ring_group_edges(sdk_obj), adapter)
    elif dn_type == DnType.IVR:
        props = _ivr_props(sdk_obj)
        routes = _edges_to_rows(_ivr_edges(sdk_obj), adapter)
    elif dn_type == DnType.GROUP:
        props = _group_props(sdk_obj)
        routes = _edges_to_rows(_group_edges(sdk_obj), adapter)
    elif dn_type == DnType.CALL_FLOW_APP:
        props = _cfa_props(sdk_obj)
        routes = []
    else:
        props = {}
        routes = []

    # Prepend the department(s) this DN belongs to (queue, ring group, IVR, CFA).
    if dn_type != DnType.GROUP:
        dept = _department_value(number, adapter)
        if dept:
            props = {"Department": dept, **props}

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

    # Departments also list their office-hours schedule, break hours and holidays.
    if dn_type == DnType.GROUP:
        for section in (
            _group_hours_section(sdk_obj),
            _group_break_section(sdk_obj),
            _group_holidays_section(sdk_obj),
        ):
            if section:
                parts.append("")
                parts.append(section)

    ref_section = _referenced_by_section(anc, references)
    if ref_section:
        parts.append("")
        parts.append(ref_section)

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
    references: ReferenceIndex,
) -> str:
    if not items:
        return ""
    sorted_items = sorted(items.items(), key=lambda kv: kv[0].zfill(20))
    parts = [f"## {icon} {title}\n"]
    for _number, sdk_obj in sorted_items:
        parts.append(_render_entity(dn_type, sdk_obj, adapter, include_raw, references))
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

    references = build_reference_index(adapter)

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

        _render_trunk_section(adapter, include_raw, references),

        _render_section(
            "IVR / Digital Receptionists", "🎛️", DnType.IVR,
            adapter.all_receptionists, adapter, include_raw, references,
        ),
        _render_section(
            "Ring Groups", "🔔", DnType.RING_GROUP,
            adapter.all_ring_groups, adapter, include_raw, references,
        ),
        _render_section(
            "Queues", "📋", DnType.QUEUE,
            adapter.all_queues, adapter, include_raw, references,
        ),
        _render_section(
            "Call Flow Apps", "⚙️", DnType.CALL_FLOW_APP,
            adapter.all_call_flow_apps, adapter, include_raw, references,
        ),
        _render_section(
            "Groups", "🏢", DnType.GROUP,
            adapter.all_groups, adapter, include_raw, references,
        ),
        _render_section(
            "Extensions", "👤", DnType.USER,
            adapter.all_users, adapter, include_raw, references,
        ),

        _render_fxs_section(adapter, include_raw),
        _render_system_extensions_section(adapter),
    ]

    return "\n".join(s for s in sections if s)
