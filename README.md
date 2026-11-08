# FinAlly — AI Trading Workstation

An AI-powered trading workstation that streams live market data, lets you trade a simulated $10,000 portfolio, and includes a chat assistant that can analyze your positions and place trades for you. It looks and works like a Bloomberg-style terminal with an AI copilot.

Built entirely by coding agents as the capstone project for an agentic AI coding course.

> **Status:** the market data layer is complete. The rest of the platform is in progress. See [`planning/PLAN.md`](planning/PLAN.md).

## Features

- **Live prices** streamed over SSE, with green/red flash animations and sparklines
- **Simulated trading:** market orders, instant fills, fractional shares, no fees
- **Portfolio views:** P&L heatmap (treemap), portfolio value chart, positions table
- **AI assistant** (DeepSeek via LiteLLM) that analyzes holdings and runs trades and watchlist changes when you ask
- **Watchlist management**, by hand or through chat
- **One-click reset** back to the first-launch state

## Architecture

Everything runs in one Docker container on port 8000:

| Layer | Tech |
|---|---|
| Frontend | Next.js + TypeScript (static export), Tailwind, Lightweight Charts |
| Backend | FastAPI (Python, managed with `uv`), SSE streaming |
| Database | SQLite in `db/finally.db`, created and seeded on startup |
| AI | LiteLLM → DeepSeek (`deepseek/deepseek-flash`), JSON mode validated with Pydantic |
| Market data | Built-in GBM simulator (default) or the Massive/Polygon.io REST API |

## Quick Start

```bash
cp .env.example .env          # add DEEP_SEEK_API_KEY to enable chat
./scripts/start_mac.sh        # or: docker compose up -d --build
# open http://localhost:8000

./scripts/stop_mac.sh         # or: docker compose down (data is kept)
```

Your data lives in the `finally-data` Docker volume. To wipe it, run `docker compose down -v`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DEEP_SEEK_API_KEY` | — | Turns on AI chat. Without it, `/api/chat` returns 503 |
| `LLM_MODEL` | `deepseek/deepseek-flash` | Overrides the LiteLLM model |
| `MASSIVE_API_KEY` | — | Uses real market data. Leave empty to use the simulator |
| `MASSIVE_POLL_SECONDS` | `15` | How often to poll Massive, in seconds |
| `DB_PATH` | `/app/db/finally.db` | Location of the SQLite file |
| `FAKE_LLM` | `false` | Scripted fake LLM for E2E tests |

## Development

```bash
cd backend
uv sync
uv run pytest                 # backend unit tests
uv run market_data_demo.py    # terminal demo of the price simulator
```

## Project Layout

```
finally/
├── frontend/   # Next.js app (static export)
├── backend/    # FastAPI uv project (app/market, app/db, ...)
├── planning/   # Specs and agent contracts (start with PLAN.md)
├── test/       # Playwright E2E tests
├── scripts/    # Start/stop wrappers around docker compose
└── db/         # SQLite volume mount (runtime)
```

## License

See [LICENSE](LICENSE).
