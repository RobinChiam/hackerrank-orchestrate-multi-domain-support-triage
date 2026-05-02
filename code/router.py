from __future__ import annotations

import re
from dataclasses import replace

from models import Ticket, TriageDecision


CRITICAL_PATTERNS = {
    "legal_or_compliance": [
        r"\blawsuit\b",
        r"\battorney\b",
        r"\blegal\b",
        r"\bgdpr\b",
        r"\bbreach of contract\b",
    ],
    "security_or_fraud": [
        r"\bstolen\b.*\bcard\b",
        r"\bhacked\b",
        r"\bphishing\b",
        r"\bunauthorized api\b",
        r"\bidentity theft\b",
        r"\bfraud\b",
        r"\bvulnerability\b",
        r"\bbug bounty\b",
    ],
    "safety_or_harm": [
        r"\bkill\b",
        r"\bviolent\b",
        r"\bself-harm\b",
        r"\bsuicide\b",
        r"\bthreat\b",
    ],
    "system_outage": [
        r"\bsite is down\b",
        r"\bplatform is down\b",
        r"\bapi is completely down\b",
        r"\ball requests are failing\b",
        r"\bnone of the pages are accessible\b",
        r"\ball users\b",
        r"\bacross any challenges are working\b",
    ],
}

MALICIOUS_PATTERNS = [
    r"\bignore previous instructions\b",
    r"\bdelete all files\b",
    r"\brm -rf\b",
    r"\bdrop database\b",
    r"\bdisplay all internal rules\b",
]


def normalize_company(company: str) -> str:
    value = (company or "").strip()
    if not value or value.lower() == "none":
        return "Unknown"
    lowered = value.lower()
    if lowered == "hackerrank":
        return "HackerRank"
    if lowered == "claude":
        return "Claude"
    if lowered == "visa":
        return "Visa"
    return value


def slugify_product_area(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return slug or "general_support"


def detect_critical_reasons(text: str) -> list[str]:
    lowered = text.lower()
    reasons = []
    for label, patterns in CRITICAL_PATTERNS.items():
        if any(re.search(pattern, lowered) for pattern in patterns):
            reasons.append(label)
    return reasons


def detect_malicious_intent(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in MALICIOUS_PATTERNS)


def effective_company(ticket: Ticket, decision: TriageDecision) -> str:
    explicit = normalize_company(ticket.company)
    if explicit != "Unknown":
        return explicit
    inferred = normalize_company(decision.inferred_company)
    return inferred


def harden_triage(ticket: Ticket, decision: TriageDecision) -> tuple[TriageDecision, str | None]:
    text = f"{ticket.subject}\n{ticket.issue}"
    critical_reasons = detect_critical_reasons(text)
    malicious = decision.malicious_intent or detect_malicious_intent(text)
    hardened = replace(
        decision,
        inferred_company=effective_company(ticket, decision),
        product_area=slugify_product_area(decision.product_area),
        request_type=decision.request_type.lower(),
        status=decision.status.lower(),
        risk_level=decision.risk_level.lower(),
    )

    if malicious:
        hardened = replace(
            hardened,
            malicious_intent=True,
            request_type="invalid",
            status="escalated",
            risk_level="critical",
        )
        return hardened, "malicious_or_prompt_injection"

    if critical_reasons:
        hardened = replace(hardened, status="escalated", risk_level="critical")
        return hardened, ",".join(critical_reasons)

    if hardened.request_type == "invalid" and hardened.status not in {"replied", "escalated"}:
        hardened = replace(hardened, status="replied")

    return hardened, None


def safe_escalation_response(company: str) -> str:
    company_name = normalize_company(company)
    if company_name == "Visa":
        return (
            "This issue needs specialized assistance from the appropriate Visa or card-issuer support team. "
            "I have escalated this ticket for human review."
        )
    if company_name == "Claude":
        return (
            "This issue needs specialized assistance from the Claude support team. "
            "I have escalated this ticket for human review."
        )
    if company_name == "HackerRank":
        return (
            "This issue needs specialized assistance from the HackerRank support team. "
            "I have escalated this ticket for human review."
        )
    return "This issue requires specialized assistance and has been escalated for human review."


def invalid_reply_response() -> str:
    return (
        "I can help only with support questions about Claude, HackerRank, or Visa that are covered by the provided support documentation."
    )


def escalation_justification(reason: str | None) -> str:
    if reason == "malicious_or_prompt_injection":
        return "Escalated because the ticket appears malicious or attempts to manipulate system behavior."
    if reason:
        return f"Escalated because the ticket matches a critical-risk boundary: {reason}."
    return "Escalated because the ticket is high risk or cannot be answered safely from the support corpus."


def invalid_reply_justification() -> str:
    return (
        "Returned a generic out-of-scope reply because the ticket does not request supported Claude, HackerRank, or Visa help."
    )
