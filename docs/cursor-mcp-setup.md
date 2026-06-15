# Cursor MCP setup — GitNexus + trading-agents

Two MCP servers, two jobs:

| Server | Purpose | When to use |
|--------|---------|-------------|
| **gitnexus** | Code intelligence (graph, blast radius, traces) | Before refactors and wiring changes |
| **trading-agents** | Trading ops (agents, levels, risk, watchlist) | Live/debug the running system |

Cursor rule: `.cursor/rules/mcp-gitnexus-trading-split.mdc` (applies when editing pipeline/agents/chart_watcher/live/risk).

---

## 1. GitNexus (code intelligence)

### One-time machine setup

Requires Node.js 18+.

```bash
# Optional: auto-configure global Cursor MCP + skills
npx gitnexus setup

# Index this repo (run from repo root after clone/pull with big changes)
cd /path/to/AI-trading-bot
npx gitnexus analyze
```

Verify:

```bash
node --version          # >= 18
npx gitnexus --version
ls .gitnexus/meta.json  # index exists after analyze
```

In Cursor, ask: *"List all indexed repositories"* (uses GitNexus `list_repos`).

### MCP config

GitNexus official config (works globally or in project `.cursor/mcp.json`):

```json
"gitnexus": {
  "command": "npx",
  "args": ["-y", "gitnexus@latest", "mcp"]
}
```

macOS/Linux — already merged in `.cursor/mcp.json` at repo root.

**Windows:** use `"command": "cmd"`, `"args": ["/c", "npx", "-y", "gitnexus@latest", "mcp"]`.

Re-index after large refactors: `npx gitnexus analyze`.

### GitNexus tools (understanding code)

- `query` — hybrid search across the indexed graph
- `context` — symbol-centric view (callers/callees)
- `impact` — blast radius before a change
- `detect_changes` — git-diff impact
- `list_repos` — indexed repositories

### Pre-edit workflow (recommended)

Before touching supervisor, registry, level gate, or execution:

```
Use GitNexus to trace every caller and dependency related to
TradingPipelineSupervisor, get_all_method_agents_from_registry,
LevelEntryGate, and the execution bridge. Summarize blast radius,
then edit the minimum necessary files.
```

---

## 2. trading-agents (runtime ops)

Python stdio server — domain tools for the bot, not code graph.

### MCP config (project)

File: `.cursor/mcp.json` (repo root). Copy `DATABASE_URL` from `trading-ai-model/backend/.env`.

Key env vars:

| Variable | Purpose |
|----------|---------|
| `AGENT_CONFIG_PATH` | Path to `config/agents.yaml` |
| `DATABASE_URL` | Level watchlist / touches queries |
| `DATABASE_SSL_DISABLE` | `true` for Render DB from local Mac |
| `PAPER_MODE` | Paper vs live semantics in status tools |
| `RISK_KILL_SWITCH` | Kill switch state |
| `LEVEL_GATE_TOLERANCE_PCT` | Proximity for `check_level_gate` |
| `LEVEL_GATE_MIN_TOUCHES` | Min touches for gate |

Restart MCP in **Cursor → Settings → MCP** after edits.

### trading-agents tools

**Agent admin:** `list_agents`, `set_agent_config`, `reload_config`

**Levels:** `get_level_watchlist`, `check_level_gate`, `get_recent_touches`

**Risk:** `get_risk_summary`, `set_kill_switch`, `get_pipeline_status`

**Resource:** `file:///agents.yaml` (read-only manifest)

---

## 3. Combined `.cursor/mcp.json` example

Both servers in one file (see repo `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "gitnexus": {
      "command": "npx",
      "args": ["-y", "gitnexus@latest", "mcp"]
    },
    "trading-agents": {
      "command": "/absolute/path/to/backend/.venv/bin/python",
      "args": ["-m", "trading_mcp.trading_server"],
      "cwd": "/absolute/path/to/trading-ai-model/backend",
      "env": {
        "PYTHONPATH": "/absolute/path/to/trading-ai-model/backend",
        "AGENT_CONFIG_PATH": "/absolute/path/to/trading-ai-model/backend/config/agents.yaml",
        "DATABASE_URL": "<from backend/.env>",
        "DATABASE_SSL_DISABLE": "true",
        "PAPER_MODE": "true",
        "RISK_KILL_SWITCH": "false"
      }
    }
  }
}
```

---

## 4. Architecture diagram

```
Cursor Agent
├── GitNexus MCP          → understand code (before edits)
│   ├── dependency graph
│   ├── caller/callee map
│   ├── blast-radius / impact
│   └── cross-file traces
│
└── trading-agents MCP    → operate the bot (runtime)
    ├── agent registry / agents.yaml
    ├── level watchlist & gate
    ├── risk / kill switch
    └── pipeline status
```

---

## 5. Troubleshooting

| Issue | Fix |
|-------|-----|
| GitNexus "no repos indexed" | Run `npx gitnexus analyze` at repo root |
| GitNexus tools missing | Restart Cursor; check `node --version` |
| trading-agents DB errors | Set real `DATABASE_URL` in mcp.json env |
| Stale graph after refactor | `npx gitnexus analyze` again |

Docs: [GitNexus Cursor setup](https://abhigyanpatwari-gitnexus.mintlify.app/mcp/cursor)
