# Support Triage Agent

This directory contains a terminal-based Python application for the HackerRank Orchestrate support-triage challenge.

## Architecture

The agent has three layers:

1. Retrieval layer
   - Walks the local `data/` corpus.
   - Cleans and chunks markdown articles.
   - Builds a local SQLite-backed vector index.
   - Uses Gemini Embedding 2 (`gemini-embedding-2`) to embed corpus chunks and retrieval queries.

2. Reasoning layer
   - Phase 1 triage uses Gemini 3.1 Pro Preview (`gemini-3.1-pro-preview`) with structured JSON output.
   - The triage prompt enforces:
     - critical-risk escalation boundaries
     - company inference when `company=None`
     - prompt-injection and malicious-intent detection
     - highest-watermark handling for multi-issue tickets
   - Phase 2 drafts grounded replies only when the ticket is safe to answer.

3. Response/output layer
   - Escalations bypass AI response generation and use hardcoded safe templates.
   - Replied tickets use retrieved corpus evidence plus Gemini structured output.
   - Writes the evaluator-ready CSV to `support_tickets/output.csv`.

## Requirements

- Python 3.11+ (tested in this workspace with Python 3.14)
- A repo-root `.env` file containing one of:
  - `GEMINI_API_KEY=...`
  - `GOOGLE_API_KEY=...`

No third-party Python packages are required. The application uses only the Python standard library.

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
