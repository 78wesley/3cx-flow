# 3CX Routing Directory

A CLI tool that connects to a [3CX](https://www.3cx.com/) PBX, fetches every
routing object (trunks, IVRs/digital receptionists, ring groups, queues, call
flow apps, groups/departments, extensions, FXS devices and system extensions),
and renders them into a single self-contained Markdown file.

Every destination is a clickable in-page anchor link, so you can follow a call
from a trunk's inbound rule all the way down to the extension that finally rings
— and, thanks to the **Referenced By** index, jump back up to see everything
that routes *toward* any given entity.

## Features

- **Full PBX inventory** in one navigable Markdown document.
- **Bidirectional navigation** — each entity lists both its outbound routing
  (where calls go) and a *Referenced By* table (who sends calls to it).
- **Rich per-entity detail** — forwarding profiles, exceptions, greetings,
  office-hours schedules, holidays, agents/members, department membership,
  and account properties.
- **Multi-server** support with interactive or `--server` selection.
- **Local caching** so repeated runs don't hammer the API.
- Optional **raw API JSON** dump per entry for debugging (`--include-raw`).

## Setup

Requires Python ≥ 3.14. This project uses [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

Copy the example environment file and fill in your 3CX API credentials:

```bash
cp .env.example .env
```

```ini
# Multi-server (preferred): a comma-separated list of short names…
THREECX_SERVERS=pbx1,pbx2

# …each with its own credential block (prefix = THREECX_<NAME>_):
THREECX_PBX1_BASE_URL=https://pbx1.example.com
THREECX_PBX1_CLIENT_ID=your-client-id
THREECX_PBX1_CLIENT_SECRET=your-client-secret

# Single-server legacy fallback (used when THREECX_SERVERS is unset):
# THREECX_BASE_URL=https://pbx.example.com
# THREECX_CLIENT_ID=...
# THREECX_CLIENT_SECRET=...
```

API credentials are created in the 3CX admin console under
**Admin → API** (Client ID + Client Secret).

## Usage

```bash
# Interactive server selection, output to routing_<server>_<timestamp>.md
uv run python main.py

# Pick a server non-interactively and choose the output file
uv run python main.py --server pbx1 --output routing.md

# Force a fresh API fetch (ignore/overwrite the cache)
uv run python main.py --server pbx1 --refresh

# Append raw API JSON under each entry
uv run python main.py --server pbx1 --include-raw
```

### Options

| Flag | Description |
|---|---|
| `--server NAME` | Server name from `.env`; skips interactive selection. |
| `--output, -o FILE` | Output path (default: `routing_<server>_<timestamp>.md`). |
| `--include-raw` | Append raw API JSON for every entry. |
| `--cache-file PATH` | Cache file path (default: `.3cx_cache_<server>.json`). |
| `--cache-ttl SECONDS` | Seconds before the cache is stale (default: 3600). |
| `--refresh` | Force a fresh API fetch and overwrite the cache. |
| `--no-cache` | Disable caching entirely. |
| `--env-file PATH` | Path to the `.env` file (default: `.env`). |
| `--verbose, -v` | Enable DEBUG logging to stderr. |

## How it works

```
main.py                  # argparse CLI entry point + server/cache wiring
flow_explainer/
  config.py              # multi-server .env parsing & interactive selection
  cache.py               # local JSON cache (Pydantic round-trip, TTL + URL check)
  adapter.py             # ThreeCXAdapter: bulk-loads all DNs once, O(1) lookups
  models.py              # DnType taxonomy
  renderer.py            # Markdown generation: per-entity sections, routing
                         #   tables, and the reverse "Referenced By" index
```

The adapter loads every DN type once (from the API or cache) and exposes
number-keyed lookups. The renderer extracts each entity's outbound routing
**edges**, renders them as tables, and inverts them into a reference index that
powers the *Referenced By* sections — so the routing graph is fully traversable
in both directions from any anchor.
