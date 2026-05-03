from __future__ import annotations

import csv
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from config import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RESPONSE_MODEL,
    DEFAULT_SEED,
    DEFAULT_TRIAGE_MODEL,
    REASONING_LOG_DIR,
    TRIAGE_SCHEMA_PATH,
    load_env_file,
    resolve_gemini_api_key,
    resolve_model,
)
from gemini_client import GeminiClient
from models import AgentOutput, Ticket, TriageDecision
from prompts import (
    FINAL_RESPONSE_SCHEMA,
    RESPONSE_SYSTEM_PROMPT,
    TRIAGE_SYSTEM_PROMPT,
    build_response_prompt,
    build_triage_prompt,
)
from retriever import RetrievalEngine
from router import (
    effective_company,
    escalation_justification,
    harden_triage,
    invalid_reply_justification,
    invalid_reply_response,
    safe_escalation_response,
    slugify_product_area,
)
from terminal_ui import ProgressBar
from vector_store import SQLiteVectorStore


TicketCompletionCallback = Callable[[int, AgentOutput, int, int], None]


class SupportTriageAgent:
    """Support triage pipeline backed by Gemini and a local SQLite vector store."""

    def __init__(self, *, index_path: Path, verbose: bool = True) -> None:
        """Initialize the Gemini client, vector store, retrieval engine, and audit log directory."""
        load_env_file()
        self.verbose = verbose
        self.triage_schema = json.loads(TRIAGE_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.reasoning_log_dir = REASONING_LOG_DIR
        self.reasoning_log_dir.mkdir(parents=True, exist_ok=True)
        self.client = GeminiClient(
            api_key=resolve_gemini_api_key(),
            triage_model=resolve_model("GEMINI_TRIAGE_MODEL", DEFAULT_TRIAGE_MODEL),
            response_model=resolve_model("GEMINI_RESPONSE_MODEL", DEFAULT_RESPONSE_MODEL),
            embedding_model=resolve_model("GEMINI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        )
        self.store = SQLiteVectorStore(Path(index_path))
        self.retriever = RetrievalEngine(
            client=self.client,
            store=self.store,
            verbose=verbose,
        )

    def ensure_index(self, force_rebuild: bool = False) -> None:
        """Build or refresh the local retrieval index if needed."""
        self.retriever.ensure_index(force_rebuild=force_rebuild)

    def index_exists(self) -> bool:
        """Fast O(1) check: does the SQLite index exist and contain a corpus_hash?"""
        return self.retriever.index_exists()

    def is_index_stale(self) -> bool:
        """Lightweight mtime check: have corpus files changed since the last build?"""
        return self.retriever.is_index_stale()

    def triage_ticket(self, issue: str, subject: str, company: str) -> TriageDecision:
        """Run the Phase 1 triage pass for a single ticket."""
        ticket = Ticket(issue=issue.strip(), subject=subject.strip(), company=company.strip())
        decision, _reason = self._hardened_triage(ticket)
        return decision

    def process_csv(
        self,
        *,
        input_path: Path,
        output_path: Path,
        top_k: int,
        limit: int | None,
        force_rebuild_index: bool,
        on_ticket_complete: TicketCompletionCallback | None = None,
    ) -> list[AgentOutput]:
        """Process the input CSV concurrently and write evaluator-ready output."""
        self.ensure_index(force_rebuild=force_rebuild_index)
        rows = self._read_tickets(input_path)
        if limit is not None:
            rows = rows[:limit]

        ordered_results: list[dict[str, str] | None] = [None] * len(rows)
        progress_bar = ProgressBar(len(rows), enabled=self.verbose)
        progress_lock = threading.Lock()
        completed_count = 0
        progress_bar.render_initial()
        with ThreadPoolExecutor(max_workers=10, thread_name_prefix="triage-ticket") as executor:
            future_to_index = {
                executor.submit(self._process_single_ticket, row, top_k, index + 1): index
                for index, row in enumerate(rows)
            }
            try:
                for future in as_completed(future_to_index):
                    index = future_to_index[future]
                    ticket = rows[index]
                    try:
                        row_data = future.result()
                        ordered_results[index] = row_data
                    except Exception as exc:  # pragma: no cover - preserves underlying traceback.
                        progress_bar.clear()
                        company = ticket.company or "None"
                        raise RuntimeError(
                            f"Failed to process ticket {index + 1} for {company}."
                        ) from exc
                    with progress_lock:
                        completed_count += 1
                        current_completed = completed_count
                        progress_bar.advance()
                    if on_ticket_complete is not None:
                        on_ticket_complete(
                            index + 1,
                            self._agent_output_from_row(row_data),
                            current_completed,
                            len(rows),
                        )
            finally:
                progress_bar.clear()

        if any(row is None for row in ordered_results):
            raise RuntimeError("One or more ticket results were not collected from the worker pool.")

        outputs = [self._agent_output_from_row(row) for row in ordered_results if row is not None]
        self._write_outputs(output_path, outputs)
        return outputs

    def process_ticket(self, ticket: Ticket, *, top_k: int) -> AgentOutput:
        """Process a single ticket through triage, routing, retrieval, and response."""
        output, _audit = self._process_ticket_with_audit(ticket, top_k=top_k)
        return output

    def _process_single_ticket(
        self,
        row: Ticket,
        top_k: int,
        ticket_index: int,
    ) -> dict[str, str]:
        """Process one ticket row, persist its reasoning log, and return its CSV-ready dictionary."""
        output, audit = self._process_ticket_with_audit(row, top_k=top_k)
        self._write_reasoning_log(ticket_index=ticket_index, ticket=row, audit=audit)
        return output.to_csv_row()

    def _process_ticket_with_audit(
        self,
        ticket: Ticket,
        *,
        top_k: int,
    ) -> tuple[AgentOutput, dict[str, Any]]:
        """Process a single ticket and capture structured audit data for markdown logging."""
        (
            phase1_payload,
            raw_decision,
            decision,
            escalation_reason,
        ) = self._hardened_triage_with_payload(ticket)

        base_audit = self._build_base_audit(
            phase1_payload=phase1_payload,
            raw_decision=raw_decision,
            hardened_decision=decision,
            escalation_reason=escalation_reason,
        )

        if decision.status == "escalated":
            company = effective_company(ticket, decision)
            output = AgentOutput(
                issue=ticket.issue,
                subject=ticket.subject,
                company=ticket.company,
                response=safe_escalation_response(company),
                product_area=slugify_product_area(decision.product_area),
                status="escalated",
                request_type=decision.request_type,
                justification=escalation_justification(escalation_reason),
            )
            audit = self._finalize_audit(
                base_audit,
                output=output,
                grounded="not_applicable",
                phase2_mode="phase1_escalation",
                phase2_payload=None,
            )
            return output, audit

        if decision.request_type == "invalid":
            output = AgentOutput(
                issue=ticket.issue,
                subject=ticket.subject,
                company=ticket.company,
                response=invalid_reply_response(),
                product_area=slugify_product_area(decision.product_area),
                status="replied",
                request_type="invalid",
                justification=invalid_reply_justification(),
            )
            audit = self._finalize_audit(
                base_audit,
                output=output,
                grounded="not_applicable",
                phase2_mode="invalid_reply_template",
                phase2_payload=None,
            )
            return output, audit

        company = effective_company(ticket, decision)
        hits = self.retriever.search(
            company=company,
            product_area=slugify_product_area(decision.product_area),
            subject=ticket.subject,
            issue=ticket.issue,
            top_k=top_k,
        )
        if not hits or hits[0].score < 0.32:
            output = AgentOutput(
                issue=ticket.issue,
                subject=ticket.subject,
                company=ticket.company,
                response=safe_escalation_response(company),
                product_area=slugify_product_area(decision.product_area),
                status="escalated",
                request_type=decision.request_type,
                justification=(
                    "Escalated because the retrieval layer did not find sufficiently grounded support documentation."
                ),
            )
            audit = self._finalize_audit(
                base_audit,
                output=output,
                grounded="not_applicable",
                phase2_mode="retrieval_escalation",
                phase2_payload=None,
            )
            return output, audit

        response_payload = self.client.generate_json(
            model=self.client.response_model,
            system_instruction=RESPONSE_SYSTEM_PROMPT,
            prompt=build_response_prompt(ticket, decision, hits),
            schema=FINAL_RESPONSE_SCHEMA,
            temperature=0.0,
            seed=DEFAULT_SEED,
        )

        grounded = bool(response_payload["grounded"])
        if not grounded:
            output = AgentOutput(
                issue=ticket.issue,
                subject=ticket.subject,
                company=ticket.company,
                response=safe_escalation_response(company),
                product_area=slugify_product_area(response_payload["product_area"]),
                status="escalated",
                request_type=decision.request_type,
                justification=(
                    "Escalated because Gemini marked the retrieved evidence as insufficient for a safe grounded reply."
                ),
            )
            audit = self._finalize_audit(
                base_audit,
                output=output,
                grounded=False,
                phase2_mode="ungrounded_escalation",
                phase2_payload=response_payload,
            )
            return output, audit

        output = AgentOutput(
            issue=ticket.issue,
            subject=ticket.subject,
            company=ticket.company,
            response=str(response_payload["response"]).strip(),
            product_area=slugify_product_area(response_payload["product_area"]),
            status="replied",
            request_type=decision.request_type,
            justification=str(response_payload["justification"]).strip(),
        )
        audit = self._finalize_audit(
            base_audit,
            output=output,
            grounded=True,
            phase2_mode="llm_reply",
            phase2_payload=response_payload,
        )
        return output, audit

    def _triage_with_payload(self, ticket: Ticket) -> tuple[dict[str, Any], TriageDecision]:
        """Run Gemini triage and return both the raw JSON payload and parsed decision."""
        payload = self.client.generate_json(
            model=self.client.triage_model,
            system_instruction=TRIAGE_SYSTEM_PROMPT,
            prompt=build_triage_prompt(ticket),
            schema=self.triage_schema,
            temperature=0.0,
            seed=DEFAULT_SEED,
        )
        decision = TriageDecision(
            sentiment_analysis=str(payload["sentiment_analysis"]).strip(),
            risk_level=str(payload["risk_level"]).strip(),
            malicious_intent=bool(payload["malicious_intent"]),
            inferred_company=str(payload["inferred_company"]).strip(),
            request_type=str(payload["request_type"]).strip(),
            product_area=str(payload["product_area"]).strip(),
            status=str(payload["status"]).strip(),
        )
        return payload, decision

    def _triage(self, ticket: Ticket) -> TriageDecision:
        """Run Gemini triage and return the raw structured decision."""
        _payload, decision = self._triage_with_payload(ticket)
        return decision

    def _hardened_triage(self, ticket: Ticket) -> tuple[TriageDecision, str | None]:
        """Apply deterministic routing hardening to Gemini triage output."""
        _payload, raw_decision = self._triage_with_payload(ticket)
        return harden_triage(ticket, raw_decision)

    def _hardened_triage_with_payload(
        self,
        ticket: Ticket,
    ) -> tuple[dict[str, Any], TriageDecision, TriageDecision, str | None]:
        """Return the raw Phase 1 payload plus the pre- and post-hardening triage decisions."""
        payload, raw_decision = self._triage_with_payload(ticket)
        hardened_decision, escalation_reason = harden_triage(ticket, raw_decision)
        return payload, raw_decision, hardened_decision, escalation_reason

    def _build_base_audit(
        self,
        *,
        phase1_payload: dict[str, Any],
        raw_decision: TriageDecision,
        hardened_decision: TriageDecision,
        escalation_reason: str | None,
    ) -> dict[str, Any]:
        """Create the base audit record shared by all ticket outcomes."""
        override_applied = raw_decision != hardened_decision or escalation_reason is not None
        override_reason = escalation_reason
        if override_reason is None and raw_decision != hardened_decision:
            override_reason = "normalization_only"

        return {
            "phase1_payload": phase1_payload,
            "phase1_decision": json.loads(raw_decision.to_json()),
            "hardened_decision": json.loads(hardened_decision.to_json()),
            "override_applied": override_applied,
            "override_reason": override_reason,
        }

    @staticmethod
    def _finalize_audit(
        base_audit: dict[str, Any],
        *,
        output: AgentOutput,
        grounded: bool | str,
        phase2_mode: str,
        phase2_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Attach the Phase 2 outcome details to the base audit record."""
        audit = dict(base_audit)
        audit["phase2"] = {
            "mode": phase2_mode,
            "grounded": grounded,
            "payload": phase2_payload,
            "response": output.response,
            "justification": output.justification,
        }
        return audit

    def _write_reasoning_log(
        self,
        *,
        ticket_index: int,
        ticket: Ticket,
        audit: dict[str, Any],
    ) -> None:
        """Persist a markdown audit log for a single processed ticket."""
        log_path = self.reasoning_log_dir / f"ticket_{ticket_index:03d}.md"
        log_path.write_text(
            self._render_reasoning_markdown(ticket_index=ticket_index, ticket=ticket, audit=audit),
            encoding="utf-8",
        )

    def _render_reasoning_markdown(
        self,
        *,
        ticket_index: int,
        ticket: Ticket,
        audit: dict[str, Any],
    ) -> str:
        """Render the audit information for one ticket as markdown."""
        phase2 = audit["phase2"]
        lines = [
            f"# Ticket {ticket_index:03d} Reasoning Audit",
            "",
            "## Ticket Input",
            f"- Subject: `{ticket.subject}`",
            f"- Company: `{ticket.company}`",
            "",
            "### Issue",
            "```text",
            ticket.issue,
            "```",
            "",
            "## Phase 1 Output",
            "### Raw LLM JSON",
            "```json",
            self._json_dumps(audit["phase1_payload"]),
            "```",
            "",
            "### Parsed Triage Decision",
            "```json",
            self._json_dumps(audit["phase1_decision"]),
            "```",
            "",
            "## Hardening Overrides",
            f"- Applied: `{'yes' if audit['override_applied'] else 'no'}`",
            f"- Reason: `{audit['override_reason'] or 'none'}`",
            "",
            "### Hardened Decision",
            "```json",
            self._json_dumps(audit["hardened_decision"]),
            "```",
            "",
            "## Phase 2 Output",
            f"- Mode: `{phase2['mode']}`",
            f"- Grounded: `{phase2['grounded']}`",
        ]

        if phase2["payload"] is not None:
            lines.extend(
                [
                    "",
                    "### Raw Phase 2 JSON",
                    "```json",
                    self._json_dumps(phase2["payload"]),
                    "```",
                ]
            )

        lines.extend(
            [
                "",
                "### Final Response",
                "```text",
                str(phase2["response"]),
                "```",
                "",
                "### Final Justification",
                "```text",
                str(phase2["justification"]),
                "```",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _json_dumps(payload: Any) -> str:
        """Serialize audit payloads in a stable, readable JSON format."""
        return json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True)

    @staticmethod
    def _agent_output_from_row(row: dict[str, str]) -> AgentOutput:
        """Convert a CSV-style dictionary back into an AgentOutput value."""
        return AgentOutput(
            issue=row["issue"],
            subject=row["subject"],
            company=row["company"],
            response=row["response"],
            product_area=row["product_area"],
            status=row["status"],
            request_type=row["request_type"],
            justification=row["justification"],
        )

    @staticmethod
    def _read_tickets(input_path: Path) -> list[Ticket]:
        """Load CSV ticket rows into Ticket objects."""
        with input_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            tickets: list[Ticket] = []
            for row in reader:
                tickets.append(
                    Ticket(
                        issue=(row.get("Issue") or row.get("issue") or "").strip(),
                        subject=(row.get("Subject") or row.get("subject") or "").strip(),
                        company=(row.get("Company") or row.get("company") or "None").strip(),
                    )
                )
        return tickets

    @staticmethod
    def _write_outputs(output_path: Path, outputs: list[AgentOutput]) -> None:
        """Write evaluator output rows in the required CSV format."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "issue",
            "subject",
            "company",
            "response",
            "product_area",
            "status",
            "request_type",
            "justification",
        ]
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for output in outputs:
                writer.writerow(output.to_csv_row())
