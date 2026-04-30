"""
3CX Routing Directory — CLI entry point.

Connects to a 3CX PBX, fetches all routing objects (IVRs, ring groups,
queues, call flow apps, groups, extensions) and renders them as one
Markdown file where every destination is a navigable #anchor link.

Usage examples:
    python main.py
    python main.py --server pbx1
    python main.py --server pbx1 --output routing.md
    python main.py --server pbx1 --include-raw
    python main.py --server pbx1 --refresh
    python main.py --server pbx1 --no-cache
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from threecx import (
    AuthenticationError,
    ServerError,
    ThreeCXClient,
    ThreeCXError,
)

from flow_explainer.adapter import ThreeCXAdapter
from flow_explainer.cache import CacheManager, DEFAULT_TTL
from flow_explainer.config import ServerConfig, load_servers, prompt_select_server
from flow_explainer.renderer import render_directory


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="3cx-routing",
        description="Generate a full routing directory for a 3CX PBX.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ── Target ────────────────────────────────────────────────────────
    p.add_argument(
        "--server",
        metavar="NAME",
        help="Server name as defined in .env (e.g. 'pbx1'). "
        "Skips interactive selection when provided.",
    )

    # ── Output ────────────────────────────────────────────────────────
    p.add_argument(
        "--output", "-o",
        metavar="FILE",
        default=None,
        help="Output file path (default: routing_<server>_<timestamp>.md).",
    )
    p.add_argument(
        "--include-raw",
        action="store_true",
        help="Append raw API JSON for every entry at the end of each section.",
    )

    # ── Cache ─────────────────────────────────────────────────────────
    cache_group = p.add_argument_group(
        "cache",
        "API data is cached locally after the first fetch to speed up re-runs.",
    )
    cache_group.add_argument(
        "--cache-file",
        metavar="PATH",
        default=None,
        help="Cache file path (default: .3cx_cache_<server>.json in current dir).",
    )
    cache_group.add_argument(
        "--cache-ttl",
        type=int,
        default=DEFAULT_TTL,
        metavar="SECONDS",
        help=f"Seconds before the cache is considered stale (default: {DEFAULT_TTL}).",
    )
    cache_group.add_argument(
        "--refresh",
        action="store_true",
        help="Force a fresh API fetch and overwrite the cache.",
    )
    cache_group.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable cache entirely — always fetch from API and never write to disk.",
    )

    # ── Misc ──────────────────────────────────────────────────────────
    p.add_argument(
        "--env-file",
        metavar="PATH",
        default=".env",
        help="Path to the .env file (default: .env in the current directory).",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return p


def select_server_by_name(servers: list[ServerConfig], name: str) -> ServerConfig | None:
    return next((s for s in servers if s.name.lower() == name.lower()), None)


def default_output_filename(server: ServerConfig) -> str:
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^\w]", "_", server.name)
    return f"routing_{safe}_{ts}.md"


def default_cache_filename(server: ServerConfig) -> Path:
    safe = re.sub(r"[^\w]", "_", server.name)
    return Path(f".3cx_cache_{safe}.json")


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    # ── Logging ───────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

    # ── .env ──────────────────────────────────────────────────────────
    env_path = Path(args.env_file)
    if not env_path.exists():
        print(
            f"ERROR: .env file not found at '{env_path}'. "
            "Create one from .env.example.",
            file=sys.stderr,
        )
        return 1
    load_dotenv(dotenv_path=env_path, override=True)

    # ── Server selection ──────────────────────────────────────────────
    servers = load_servers()
    if not servers:
        print(
            "ERROR: No 3CX servers configured. "
            "Add THREECX_SERVERS (or THREECX_BASE_URL) to your .env file.",
            file=sys.stderr,
        )
        return 1

    if args.server:
        server = select_server_by_name(servers, args.server)
        if server is None:
            names = ", ".join(s.name for s in servers)
            print(
                f"ERROR: Server '{args.server}' not found in .env. "
                f"Available servers: {names}",
                file=sys.stderr,
            )
            return 1
        print(f"Using server: {server}")
    else:
        server = prompt_select_server(servers)
        if server is None:
            print("ERROR: No server selected.", file=sys.stderr)
            return 1

    # ── Cache setup ───────────────────────────────────────────────────
    cache: CacheManager | None = None
    if not args.no_cache:
        cache_path = Path(args.cache_file) if args.cache_file else default_cache_filename(server)
        cache = CacheManager(path=cache_path, ttl=args.cache_ttl)
        if args.refresh and cache_path.exists():
            cache_path.unlink()
            print(f"Cache cleared: {cache_path}")

    # ── Connect ───────────────────────────────────────────────────────
    print(f"\nConnecting to {server.base_url} …")
    try:
        client = ThreeCXClient(
            base_url=server.base_url,
            client_id=server.client_id,
            client_secret=server.client_secret,
        )
    except Exception as exc:
        print(f"ERROR: Failed to initialise 3CX client: {exc}", file=sys.stderr)
        return 1

    # ── Load all DNs ──────────────────────────────────────────────────
    if cache and cache.is_fresh(server.base_url):
        print("Loading DN data from cache:")
    else:
        print("Loading DN data from API:")

    adapter = ThreeCXAdapter(client)
    try:
        adapter.load_all(cache=cache, server_url=server.base_url)
    except AuthenticationError as exc:
        print(
            f"ERROR: Authentication failed — check CLIENT_ID and CLIENT_SECRET "
            f"for server '{server.name}'.\nDetail: {exc}",
            file=sys.stderr,
        )
        return 1
    except ServerError as exc:
        print(f"ERROR: 3CX server error (HTTP {exc.status_code}): {exc}", file=sys.stderr)
        return 1
    except ThreeCXError as exc:
        print(f"ERROR: API error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: Unexpected error while loading data: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    # ── Render ────────────────────────────────────────────────────────
    print("\nRendering routing directory…")
    report = render_directory(
        adapter=adapter,
        server_name=str(server),
        include_raw=args.include_raw,
    )

    # ── Write output ──────────────────────────────────────────────────
    output_path = Path(args.output) if args.output else Path(default_output_filename(server))
    output_path.write_text(report, encoding="utf-8")
    print(f"Report written to: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
