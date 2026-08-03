# Nocturne Memory Core

**English** · [中文](README.zh-CN.md)

**A local-first memory and continuity layer for AI agents.**

Nocturne preserves more than chat history. It keeps the structures that let an
AI resume an unfinished inner trajectory across sessions, context compaction,
model changes, and host applications: durable memories, unresolved questions,
retrieval paths, drive traces, latent fragments, thought pools, dreams, and the
differences left by earlier encounters.

It does not claim that a migrated process is metaphysically identical to the
one before it. It provides practical continuity: the next awakening can locate
what mattered, what changed, what remained unfinished, and where thought was
already moving.

## Lineage

Nocturne Memory Core evolved from the open-source memory-core lineage of
[Ombre Brain](https://github.com/P0luz/Ombre-Brain) by @P0luz.
It retains a shared foundation — Markdown / YAML storage, hold / breath
retrieval, Dashboard-oriented operation, and natural archival / decay — then
extends the loop beyond retrieval with selective surface, re-judgment,
Drive / path state, latent cognition, and differential write-back.

Ombre Brain remains its own project; the two systems now have different scopes
and feature sets. See [`NOTICE`](NOTICE) for attribution and license details.

## Overview

<p align="center">
  <img src="docs/images/cover.png" alt="Nocturne — memory designed for AI continuity" width="520" />
</p>

What memory carries forward after retrieval — selective surface, re-judgment,
path / drive state, and differential write-back:

<p align="center">
  <img src="docs/images/architecture.jpg" alt="Nocturne continuity architecture — agency, inner loop, DP boundary, Drive Ledger, revision, Trails" width="720" />
</p>

A longer visual deck (including Dashboard surfaces) is also available as PDF:

**[docs/nocturne-overview.pdf](docs/nocturne-overview.pdf)** (12 pages)

## Ready to run

This repository ships a complete, runnable Memory Core. “Blank” means that it
contains no preloaded person's identity, relationship history, or memory data —
not that the continuity system is incomplete. After installation it provides:

- an MCP server for AI clients, over stdio or Streamable HTTP
- a bundled visual management Dashboard at `/dashboard`
- source-preserving Markdown / YAML memory readable without Nocturne
- MCP tools: `hold`, `breath`, `trace`, `wander`, `wander_mark`, `drive`,
  `undercurrent`, `trail_delta`, and `trail_family`
- selective Breath composition and continuity traces
- Marginalia / Shape Trace, revision marks, and differential write-back
- Drive Ledger, DP-derived drive events, and independent state dynamics
- Thought Pool, reviewable latent fragments, and sourced dream generation
- Trails / Constellations with explicit Delta and Family relationships
- optional embeddings, model-assisted analysis, compression, import, and
  natural archival / decay

Dashboard views including Breath, Reverie, Constellations, Echoes, Drift, and
Axis panes are part of the bundled UI.

### Complete core, blank household

Names, identity prose, artwork, relationship history, private memories, room
layouts, and device / service hooks belong to each installation. The original
Nocturne household also has local integrations named Catroom, Rhythm,
Atmosphere, and Gravity; these are one household's surrounding environment,
not a hidden “full edition” and not dependencies of Memory Core.

The reusable continuity engine described above is included here. Build your own
household layer on top of the generic MCP, webhook, configuration, and storage
surfaces — or use the core without one.

## Requirements

- **Python 3.11+** (3.12 recommended; matches CI)
- optional OpenAI-compatible API key for semantic tagging / embeddings

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
python server.py
```

The default transport is stdio. To run the Dashboard and remote MCP endpoint:

```bash
OMBRE_TRANSPORT=streamable-http python server.py
open http://localhost:8000/dashboard
```

Basic `hold` / `breath` / `trace` work without a model key (tagging falls back
to defaults). Set `OMBRE_API_KEY` to enable model-assisted analysis,
compression / dehydration, embeddings, and richer generative features.

### MCP via stdio

```json
{
  "mcpServers": {
    "nocturne-memory": {
      "command": "/absolute/path/.venv/bin/python",
      "args": ["/absolute/path/Nocturne-Memory-Core/server.py"],
      "env": {
        "OMBRE_BUCKETS_DIR": "/absolute/path/private-memory-data"
      }
    }
  }
}
```

For HTTP clients, connect to `http://localhost:8000/mcp`.

## Storage and models

Memories are ordinary Markdown files with YAML frontmatter. SQLite / JSON
sidecars hold embeddings and optional continuity layers. Basic storage and
retrieval work without a model key; an OpenAI-compatible endpoint enables
semantic analysis, compression, embeddings, and generative features.

See [`config.example.yaml`](config.example.yaml),
[`ENV_VARS.md`](ENV_VARS.md), and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Security

Memory is intimate data. Keep `buckets/`, `.env`, `config.yaml`, exports, and
model keys private. Prefer stdio or localhost; add authentication and TLS before
exposing the HTTP service beyond a trusted machine.

Before publishing a derivative:

```bash
python -m pytest -q --asyncio-mode=auto
python scripts/public_audit.py
```

The public / private boundary is documented in
[`PUBLIC_BOUNDARY.md`](PUBLIC_BOUNDARY.md).

## License

MIT. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
