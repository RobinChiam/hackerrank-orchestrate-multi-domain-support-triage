# Support Triage Agent

This directory contains a terminal-based Python application for the HackerRank Orchestrate support-triage challenge.

## Architecture

The agent has three layers:

1. Retrieval layer
   - Walks the local `data/` corpus.
   - Cleans and chunks markdown articles.
   - Builds a local SQLite-backed vector index.
   - Uses the Gemini embedding model (`gemini-embedding-2`) to embed corpus chunks and retrieval queries.

2. Reasoning layer
   - Phase 1 triage uses Gemini 3.1 Flash-Lite Preview (`gemini-3.1-flash-lite-preview`) with structured JSON output.
   - The triage prompt enforces:
     - critical-risk escalation boundaries
     - company inference when `company=None`
     - prompt-injection and malicious-intent detection
     - highest-watermark handling for multi-issue tickets
   - Phase 2 also uses Gemini 3.1 Flash-Lite Preview (`gemini-3.1-flash-lite-preview`) to draft grounded replies only when the ticket is safe to answer.

3. Response/output layer
   - Escalations bypass AI response generation and use hardcoded safe templates.
   - Replied tickets use retrieved corpus evidence plus Gemini structured output.
   - Writes the evaluator-ready CSV to `support_tickets/output.csv`.

## Requirements

- Python 3.11+
- Repo-root Python dependencies installed from `requirements.txt`
  - `textual`
  - `rich`
  - `python-dotenv`
- A repo-root `.env` file containing `GEMINI_API_KEY=...`

The application runtime still relies mostly on the Python standard library, with `textual` and `rich` used for the TUI and `python-dotenv` included for environment tooling.

Model overrides can still be provided in the repo-root `.env` file:

- `GEMINI_TRIAGE_MODEL=...`
- `GEMINI_RESPONSE_MODEL=...`
- `GEMINI_EMBEDDING_MODEL=...`

## Getting Started

From the repo root:

```bash
make setup
source venv/bin/activate
python3 check_env.py
```

Then launch either interface:

```bash
python3 code/main.py --help
python3 code/tui.py
```

## Commands

Build or refresh the local vector index:

```bash
python3 code/main.py index
```

Run the full batch job:

```bash
python3 code/main.py run
```

Run against a custom input/output path:

```bash
python3 code/main.py run \
  --input support_tickets/support_tickets.csv \
  --output support_tickets/output.csv
```

Smoke-test only a few rows:

```bash
python3 code/main.py run --limit 3
```

Inspect only the triage layer for one ticket:

```bash
python3 code/main.py triage \
  --company HackerRank \
  --subject "Subscription pause" \
  --issue "Please pause our subscription for now."
```

## Files

- `main.py`: CLI entry point
- `agent.py`: orchestration across triage, retrieval, routing, and CSV writing
- `retriever.py`: index build and vector search
- `vector_store.py`: SQLite-backed vector storage and cosine search
- `gemini_client.py`: direct Gemini REST client using `urllib`
- `corpus.py`: corpus loading, markdown cleaning, and chunking
- `router.py`: risk hardening, escalation rules, and safe templates
- `prompts.py`: Phase 1 and Phase 2 prompts plus structured output schemas

## Notes

- The SQLite index is created under `code/.triage_index/`.
- If the corpus has not changed, repeated runs reuse the cached index.
- If retrieval confidence is weak, the agent escalates rather than guessing.
