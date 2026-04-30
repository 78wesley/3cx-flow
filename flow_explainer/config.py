"""Server configuration: loads and validates multi-server .env entries."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerConfig:
    name: str
    base_url: str
    client_id: str
    client_secret: str

    def __str__(self) -> str:
        return f"{self.name} ({self.base_url})"


def load_servers() -> list[ServerConfig]:
    """
    Load server configs from environment variables.

    Multi-server format (preferred):
        THREECX_SERVERS=server1,server2
        THREECX_SERVER1_BASE_URL=https://pbx1.example.com
        THREECX_SERVER1_CLIENT_ID=xxx
        THREECX_SERVER1_CLIENT_SECRET=xxx

    Legacy single-server fallback:
        THREECX_BASE_URL=https://pbx.example.com
        THREECX_CLIENT_ID=xxx
        THREECX_CLIENT_SECRET=xxx
    """
    servers: list[ServerConfig] = []

    server_names_raw = os.getenv("THREECX_SERVERS", "").strip()
    if server_names_raw:
        names = [n.strip() for n in server_names_raw.split(",") if n.strip()]
        for name in names:
            prefix = f"THREECX_{name.upper()}_"
            base_url = os.getenv(f"{prefix}BASE_URL", "").strip()
            client_id = os.getenv(f"{prefix}CLIENT_ID", "").strip()
            client_secret = os.getenv(f"{prefix}CLIENT_SECRET", "").strip()
            if not base_url:
                logger.warning("Server %r: no BASE_URL configured — skipping.", name)
                continue
            if not client_id:
                logger.warning("Server %r: no CLIENT_ID configured.", name)
            servers.append(
                ServerConfig(
                    name=name,
                    base_url=base_url,
                    client_id=client_id,
                    client_secret=client_secret,
                )
            )

    # Fallback: legacy single-server vars
    if not servers:
        base_url = os.getenv("THREECX_BASE_URL", "").strip()
        client_id = os.getenv("THREECX_CLIENT_ID", "").strip()
        client_secret = os.getenv("THREECX_CLIENT_SECRET", "").strip()
        if base_url:
            logger.debug("Using legacy single-server env vars.")
            servers.append(
                ServerConfig(
                    name="default",
                    base_url=base_url,
                    client_id=client_id,
                    client_secret=client_secret,
                )
            )

    return servers


def prompt_select_server(servers: list[ServerConfig]) -> Optional[ServerConfig]:
    """
    Interactively ask the user to pick a server from the list.
    Returns the chosen ServerConfig, or None if the list is empty.
    """
    if not servers:
        return None
    if len(servers) == 1:
        print(f"Connecting to: {servers[0]}")
        return servers[0]

    print("\nAvailable 3CX servers:")
    for i, srv in enumerate(servers, 1):
        print(f"  [{i}] {srv.name}  —  {srv.base_url}")
    print()

    while True:
        raw = input(f"Select server [1–{len(servers)}]: ").strip()
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(servers):
                return servers[idx]
        print(f"  Please enter a number between 1 and {len(servers)}.")
