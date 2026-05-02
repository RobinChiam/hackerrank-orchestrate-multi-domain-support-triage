from __future__ import annotations

import csv
import json
from pathlib import Path

from config import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RESPONSE_MODEL,
    DEFAULT_TRIAGE_MODEL,
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
from vector_store import SQLiteVectorStore


class SupportTriageAgent:
    def __init__(self, *, index_path: Path, verbose: bool = True) -> None:
        load_env_file()
        self.verbose = verbose
        self.triage_schema = json.loads(TRIAGE_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.client = GeminiClient(
            api_key=resolve_gemini_api_key(),
            triage_model=resolve_model("GEMINI_TRIAGE_MODEL", DEFAULT_TRIAGE_MODEL),
            response_model=resolve_model("GEMINI_RESPONSE_MODEL", DEFAULT_RESPONSE_MODEL),
            embedding_model=resolve_model("GEMINI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        )
        self.store = SQLiteVectorStore(Path(index_path))
        self.retriever = RetrievalEngine(
            client=self.client, store=self.store, verbose=verbose
        )

    def ensure_index(self, force_rebuild: bool = False) -> None:
        self.retriever.ensure_index(force_rebuild=force_rebuild)

    def triage_ticket(self, issue: str, subject: str, company: str) -> TriageDecision:
        ticket = Ticket(issue=issue.strip(), subject=subject.strip(), company=company.strip())
        payload = self.client.generate_json(
            model=self.client.triage_model,
            system_instruction=TRIAGE_SYSTEM_PROMPT,
            prompt=build_triage_prompt(ticket),
            schema=self.triage_schema,
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
        hardened, _reason = harden_triage(ticket, decision)
        return hardened

    def process_csv(
        self,
        *,
        input_path: Path,
        output_path: Path,
        top_k: int,
        limit: int | None,
        force_rebuild_index: bool,
    ) -> list[AgentOutput]:
        self.ensure_index(force_rebuild=force_rebuild_index)
        rows = self._read_tickets(input_path)
        if limit is not None:
            rows = rows[:limit]

        outputs = []
        for index, ticket in enumerate(rows, start=1):
            if self.verbose:
                print(f"[{index}/{len(rows)}] Processing ticket for {ticket.company or 'None'}")
            outputs.append(self.process_ticket(ticket, top_k=top_k))

        self._write_outputs(output_path, outputs)
        return outputs

    def process_ticket(self, ticket: Ticket, *, top_k: int) -> AgentOutput:
        payload = self.client.generate_json(
            model=self.client.triage_model,
            system_instruction=TRIAGE_SYSTEM_PROMPT,
            prompt=build_triage_prompt(ticket),
            schema=self.triage_schema,
        )
        raw_decision = TriageDecision(
            sentiment_analysis=str(payload["sentiment_analysis"]).strip(),
            risk_level=str(payload["risk_level"]).strip(),
            malicious_intent=bool(payload["malicious_intent"]),
            inferred_company=str(payload["inferred_company"]).strip(),
            request_type=str(payload["request_type"]).strip(),
            product_area=str(payload["product_area"]).strip(),
            status=str(payload["status"]).strip(),
        )
        decision, escalation_reason = harden_triage(ticket, raw_decision)

        if decision.status == "escalated":
            company = effective_company(ticket, decision)
            return AgentOutput(
                issue=ticket.issue,
                subject=ticket.subject,
                company=ticket.company,
                response=safe_escalation_response(company),
                product_area=slugify_product_area(decision.product_area),
                status="escalated",
                request_type=decision.request_type,
                justification=escalation_justification(escalation_reason),
            )

        if decision.request_type == "invalid":
            return AgentOutput(
                issue=ticket.issue,
                subject=ticket.subject,
                company=ticket.company,
                response=invalid_reply_response(),
                product_area=slugify_product_area(decision.product_area),
                status="replied",
                request_type="invalid",
                justification=invalid_reply_justification(),
            )

        company = effective_company(ticket, decision)
        hits = self.retriever.search(
            company=company,
            product_area=slugify_product_area(decision.product_area),
            subject=ticket.subject,
            issue=ticket.issue,
            top_k=top_k,
        )
        if not hits or hits[0].score < 0.32:
            return AgentOutput(
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

        response_payload = self.client.generate_json(
            model=self.client.response_model,
            system_instruction=RESPONSE_SYSTEM_PROMPT,
            prompt=build_response_prompt(ticket, decision, hits),
            schema=FINAL_RESPONSE_SCHEMA,
        )

        grounded = bool(response_payload["grounded"])
        if not grounded:
            return AgentOutput(
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

        return AgentOutput(
            issue=ticket.issue,
            subject=ticket.subject,
            company=ticket.company,
            response=str(response_payload["response"]).strip(),
            product_area=slugify_product_area(response_payload["product_area"]),
            status="replied",
            request_type=decision.request_type,
            justification=str(response_payload["justification"]).strip(),
        )

    @staticmethod
    def _read_tickets(input_path: Path) -> list[Ticket]:
        with input_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            tickets = []
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
