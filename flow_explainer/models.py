"""Internal data model for the call-flow graph."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DnType(Enum):
    USER = "Extension"
    QUEUE = "Queue"
    RING_GROUP = "Ring Group"
    IVR = "IVR / Digital Receptionist"
    GROUP = "Group"
    CALL_FLOW_APP = "Call Flow App"
    TRUNK = "Trunk"
    FXS = "FXS Device"
    SYSTEM_EXTENSION = "System Extension"
    EXTERNAL = "External Number"
    VOICEMAIL = "Voicemail"
    VOICEMAIL_OF_DN = "Voicemail of Extension"
    UNKNOWN = "Unknown / Unresolved"


RECURSABLE_DN_TYPES: frozenset[DnType] = frozenset(
    [DnType.USER, DnType.QUEUE, DnType.RING_GROUP, DnType.IVR, DnType.GROUP, DnType.CALL_FLOW_APP]
)


@dataclass
class FlowEdge:
    """A directed edge in the flow graph (one possible call destination)."""

    label: str        # Human-readable condition, e.g. "Holidays", "Key: 1", "Busy (External)"
    target_id: str    # node_id of the destination FlowNode


@dataclass
class FlowNode:
    """A single node in the call-flow graph (a DN or terminal destination)."""

    node_id: str            # Unique key, e.g. "user:100", "queue:200", "external:+31612345"
    dn_type: DnType
    number: str             # DN number or external phone number
    name: str               # Display name
    properties: dict[str, Any] = field(default_factory=dict)
    edges: list[FlowEdge] = field(default_factory=list)
    raw: Any = None         # Original SDK object, populated only with --include-raw


@dataclass
class FlowGraph:
    """The complete call-flow graph starting from a root DN."""

    root_id: str
    nodes: dict[str, FlowNode] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def get_root(self) -> FlowNode | None:
        return self.nodes.get(self.root_id)

    def sorted_nodes(self) -> list[FlowNode]:
        """Root first, then all other nodes in insertion order."""
        result = []
        if self.root_id in self.nodes:
            result.append(self.nodes[self.root_id])
        for nid, node in self.nodes.items():
            if nid != self.root_id:
                result.append(node)
        return result
