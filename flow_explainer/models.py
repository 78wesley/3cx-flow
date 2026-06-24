"""Internal data model: the DN type taxonomy used across the directory."""
from __future__ import annotations

from enum import Enum


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
