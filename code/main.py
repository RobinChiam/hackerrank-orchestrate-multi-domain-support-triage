from __future__ import annotations

import argparse
from pathlib import Path

from agent import SupportTriageAgent
from config import DEFAULT_INPUT_CSV, DEFAULT_OUTPUT_CSV, INDEX_DB_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Terminal-based support triage agent for Claude, HackerRank, and Visa."
    )
    subparsers = parser.add_subparsers(dest="command")

    index_parser = subparsers.add_parser(
        "index", help="Build or refresh the local vector index for the support corpus."
    )
    index_parser.add_argument(
        "--index-path",
        type=Path,
        default=INDEX_DB_PATH,
        help=f"SQLite index path (default: {INDEX_DB_PATH})",
    )
    index_parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force a full index rebuild even if the corpus hash is unchanged.",
    )
    index_parser.add_argument(
        "--quiet", action="store_true", help="Suppress progress output."
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Process a CSV of support tickets and write the output CSV required by the evaluator.",
    )
    run_parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_CSV,
        help=f"Input CSV path (default: {DEFAULT_INPUT_CSV})",
    )
    run_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_CSV,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT_CSV})",
    )
    run_parser.add_argument(
        "--index-path",
        type=Path,
        default=INDEX_DB_PATH,
        help=f"SQLite index path (default: {INDEX_DB_PATH})",
    )
    run_parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="Force a full index rebuild before processing tickets.",
    )
    run_parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of retrieved corpus chunks to pass to the response layer.",
    )
    run_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional row limit for smoke tests.",
    )
    run_parser.add_argument(
        "--quiet", action="store_true", help="Suppress progress output."
    )

    triage_parser = subparsers.add_parser(
        "triage", help="Run the Phase 1 triage pass on a single ticket."
    )
    triage_parser.add_argument("--issue", required=True, help="Ticket issue/body text.")
    triage_parser.add_argument("--subject", default="", help="Ticket subject line.")
    triage_parser.add_argument(
        "--company",
        default="None",
        help="Ticket company: Claude, HackerRank, Visa, or None.",
    )
    triage_parser.add_argument(
        "--index-path",
        type=Path,
        default=INDEX_DB_PATH,
        help=f"SQLite index path (default: {INDEX_DB_PATH})",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    command = args.command or "run"
    if args.command is None:
        fallback_args = ["run", *(argv or [])]
        args = parser.parse_args(fallback_args)

    verbose = not getattr(args, "quiet", False)
    agent = SupportTriageAgent(index_path=args.index_path, verbose=verbose)

    if command == "index":
        agent.ensure_index(force_rebuild=args.rebuild)
        if verbose:
            print(f"Index ready at {args.index_path}")
        return 0

    if command == "triage":
        decision = agent.triage_ticket(args.issue, args.subject, args.company)
        print(decision.to_json())
        return 0

    if command == "run":
        outputs = agent.process_csv(
            input_path=args.input,
            output_path=args.output,
            top_k=args.top_k,
            limit=args.limit,
            force_rebuild_index=args.rebuild_index,
        )
        if verbose:
            print(f"Wrote {len(outputs)} rows to {args.output}")
        return 0

    parser.error(f"Unsupported command: {command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
