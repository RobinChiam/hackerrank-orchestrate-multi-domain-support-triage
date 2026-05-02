from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Ticket:
    issue: str
    subject: str
    company: str

    @property
    def normalized_company(self) -> str:
        raw = (self.company or "").strip()
        if not raw or raw.lower() == "none":
            return "Unknown"
        return raw


@dataclass(frozen=True)
class TriageDecision:
    sentiment_analysis: str
    risk_level: str
    malicious_intent: bool
    inferred_company: str
    request_type: str
    product_area: str
    status: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=True)


@dataclass(frozen=True)
class CorpusChunk:
    chunk_id: str
    company: str
    category: str
    title: str
    path: str
    text: str
    snippet: str
    embedding_text: str


@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: str
    company: str
    category: str
    title: str
    path: str
    text: str
    snippet: str
    score: float


@dataclass(frozen=True)
class AgentOutput:
    issue: str
    subject: str
    company: str
    response: str
    product_area: str
    status: str
    request_type: str
    justification: str

    def to_csv_row(self) -> dict[str, str]:
        return {
            "issue": self.issue,
            "subject": self.subject,
            "company": self.company,
            "response": self.response,
            "product_area": self.product_area,
            "status": self.status,
            "request_type": self.request_type,
            "justification": self.justification,
        }
