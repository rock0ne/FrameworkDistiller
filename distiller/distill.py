"""Transparent, deterministic study-card drafts from pinned source excerpts.

No LLM call, invented quotation or hidden crosswalk is used. Auditor prompts are
labelled as interpretations and must be checked against the linked source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .ingest import Unit


THEMES = (
    "Governance & Assurance", "IAM/PAM", "Change Management", "Logging & SOC", "Patch/Vuln",
    "Backup & Operational Resilience", "Third-Party/Outsourcing", "AI & MCP Governance",
)

KEY_80053 = frozenset({
    "AC-2", "AC-3", "AC-5", "AC-6", "AC-17", "AU-2", "AU-6", "AU-9", "AU-12",
    "CA-2", "CA-7", "CM-2", "CM-3", "CM-6", "CP-9", "CP-10", "IA-2", "IA-5",
    "IR-4", "IR-6", "MA-2", "RA-5", "SA-9", "SI-2", "SI-4", "SR-2", "SR-3", "SR-6",
})

CORE_OVERRIDES = {
    "AC-2": "Manage accounts through creation, review, change and removal.",
    "AC-5": "Separate duties that should not be performed by one person.",
    "AC-6": "Give users and processes only the access needed for their tasks.",
    "AU-2": "Define which events the system must log.",
    "AU-6": "Review and analyse audit records, then report what needs action.",
    "CA-7": "Monitor security controls and system state continuously enough to act on change.",
    "CM-3": "Review and authorize configuration changes, then keep a record of them.",
    "CP-9": "Back up information and protect the backup copies.",
    "CP-10": "Restore and reconstitute the system after disruption.",
    "IA-2": "Identify and authenticate users and processes before granting access.",
    "IR-4": "Handle incidents through a defined, tested process.",
    "RA-5": "Find and assess vulnerabilities, then monitor their remediation.",
    "SI-2": "Identify, test and install fixes for system flaws on a governed timetable.",
    "SI-4": "Monitor systems to detect attacks and indicators of compromise.",
    "SR-3": "Set and operate supply-chain controls for acquired products and services.",
    "SR-6": "Assess suppliers and the risks they introduce.",
}

SP161_CORE = {
    "p.51": "Put supply-chain risk into each step of procurement and contract management.",
    "p.52": "Include acquisition risk in the enterprise's long-term C-SCRM strategy and plan.",
    "p.54": "Bring the relevant experts into procurement decisions so supply-chain risk is assessed.",
    "p.56": "Share supply-chain risk information so decisions use current, relevant evidence.",
    "p.58": "Make C-SCRM responsibilities clear across security, procurement, IT and other roles.",
    "p.60": "Establish consistent foundational supply-chain practices before adding advanced ones.",
    "p.62": "Build sustaining practices on a standardised C-SCRM foundation.",
    "p.63": "Use advanced practices only after sustaining practices are broadly established.",
    "p.66": "Measure C-SCRM results and report them to leadership.",
}

THEME_CUES = {
    "Governance & Assurance": (
        "Could unclear ownership, appetite or oversight leave a material risk without an accountable decision?",
        "TOD: inspect the stated objective, owner, authority and review criteria. TOE: reconcile a dated decision or risk population to challenge, escalation, action and board reporting evidence; check the extract scope.",
        "A board pack showing green status is not evidence that exceptions and missing populations were challenged.",
        "A control owner says the board reviewed the risk. What source population and decision record would you test?",
    ),
    "IAM/PAM": (
        "Could excess or unreviewed access allow changes without accountable authorization?",
        "TOD: inspect the account/privilege model and approval rule. TOE: reconcile a full dated account population to HR, role, MFA, leaver and change evidence; check extract scope.",
        "‘Everyone has MFA’ is a claim, not an owner-complete user/TFA extract.",
        "A manager signs the access review, but no raw account population is attached. What would you request before concluding?",
    ),
    "Change Management": (
        "Could an unapproved or unreconciled change alter a critical service without a reliable rollback trail?",
        "TOD: inspect approval, segregation and emergency paths. TOE: reconcile a full change/task population to tickets, approvals, implementation and rollback evidence.",
        "A green change dashboard may omit direct console actions; ask for the source population.",
        "A change log says all changes were approved. Which independent population would you reconcile to it?",
    ),
    "Logging & SOC": (
        "Could incomplete or stale telemetry hide a material event or delay response?",
        "TOD: inspect logging, coverage and triage design. TOE: test source freshness, reject/failure queues, alert ageing and case linkage with dated extracts.",
        "A large lifetime event count does not demonstrate that every current source is working.",
        "Management cites millions of ingested events. How do you test present coverage and triage timeliness?",
    ),
    "Patch/Vuln": (
        "Could an unassessed or unpatched weakness persist beyond the firm's risk tolerance?",
        "TOD: inspect scan scope, risk ranking and patch deadlines. TOE: reconcile asset population to authenticated scans, exceptions, triage and deployment records.",
        "Zero reported CVEs can mean zero coverage; inspect scan configuration and freshness first.",
        "A report shows no critical vulnerabilities. What denominator and scan evidence would make that meaningful?",
    ),
    "Backup & Operational Resilience": (
        "Could a disruption exceed an important service's impact tolerance because recovery was not proven?",
        "TOD: inspect dependency mapping, backup design and recovery objectives. TOE: inspect dated off-host copies and a witnessed timed restore with data-integrity checks.",
        "Backup job exit 0 is not evidence that a service can be restored inside its tolerance.",
        "Management says the four-hour tolerance passed because backup jobs were green. What test would you need?",
    ),
    "Third-Party/Outsourcing": (
        "Could an ungoverned supplier or subservice dependency undermine a critical control or recovery path?",
        "TOD: inspect supplier classification, ownership, contract and exit criteria. TOE: test current assurance period, exceptions, carve-outs, CUECs and remediation evidence.",
        "A clean provider report does not prove the customer operated its own complementary controls.",
        "A SOC 2 summary is marked reviewed. Which period, carve-out, exception and CUEC checks are still needed?",
    ),
    "AI & MCP Governance": (
        "Could an AI or connected-tool decision be used without a clear owner, boundary or testable risk treatment?",
        "TOD: inspect use-case, data, authority and human-review boundaries. TOE: sample approvals, tool scopes, monitoring, incidents and change evidence.",
        "A read-only label is not proof of every tool's actual permissions or output boundary.",
        "An AI connector is described as harmless. How would you test its data and action boundaries?",
    ),
}


@dataclass(frozen=True)
class CardDraft:
    ref: str
    title: str
    theme: str
    depth: str
    priority: int
    core_rule: str
    risk: str
    audit_test: str
    trap: str
    challenge: str
    citation: str
    source_excerpt: str


def _theme(unit: Unit, source_id: str) -> str:
    ref = unit.ref.upper()
    text = f"{unit.title} {unit.text[:700]}".lower()
    if source_id == "nist-ai-rmf-1" or any(x in text for x in ("artificial intelligence", "ai system", "model risk", "mcp")):
        return "AI & MCP Governance"
    if source_id == "nist-sp-800-161-r1" or ref.startswith(("SR-", "GV.SC", "SA-9")) or any(x in text for x in ("supplier", "supply chain", "third party", "outsourc")):
        return "Third-Party/Outsourcing"
    if ref.startswith(("AC-", "IA-", "PR.AA")) or any(x in text for x in ("privileged access", "authentication", "identity")):
        return "IAM/PAM"
    if ref.startswith("CM-") or "configuration change" in text:
        return "Change Management"
    if ref.startswith(("CP-", "RC.", "PR.IR")) or any(x in text for x in ("recovery", "resilien", "backup")):
        return "Backup & Operational Resilience"
    if ref.startswith(("RA-5", "SI-2", "ID.RA-01")) or "vulnerabilit" in text:
        return "Patch/Vuln"
    if ref.startswith(("AU-", "IR-", "SI-4", "DE.", "RS.")) or any(x in text for x in ("audit log", "incident response", "log collection")):
        return "Logging & SOC"
    return "Governance & Assurance"


def _first_two_sentences(text: str, max_chars: int = 250) -> str:
    clean = re.sub(r"\{\{[^}]+\}\}", "[organisation-defined value]", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", clean)
    picked = " ".join(sentences[:2]).strip()
    if len(picked) <= max_chars:
        return picked
    cut = picked[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(";,: ") + "…"


def make_card(unit: Unit, source_id: str) -> CardDraft | None:
    ref = unit.ref.upper()
    if source_id == "nist-sp-800-53-r5" and ref not in KEY_80053:
        return None
    theme = _theme(unit, source_id)
    risk, audit_test, trap, challenge = THEME_CUES[theme]
    if source_id == "nist-sp-800-53-r5" and ref in CORE_OVERRIDES:
        core = CORE_OVERRIDES[ref]
    elif source_id == "nist-sp-800-161-r1":
        # Human-checked short paraphrases of the referenced NIST PDF sections.
        core = SP161_CORE.get(unit.ref, _first_two_sentences(unit.text))
    else:
        core = _first_two_sentences(unit.text)
    if not core:
        return None
    depth = "Senior / Executive" if theme in {
        "Governance & Assurance", "Third-Party/Outsourcing", "Backup & Operational Resilience", "AI & MCP Governance"
    } or ref.startswith(("GV.", "CA-", "SR-")) else "Core"
    priority = 1 if depth == "Senior / Executive" else 2
    if source_id == "nist-sp-800-161-r1":
        priority = 2
    return CardDraft(ref, unit.title, theme, depth, priority, core, risk,
                     audit_test, trap, challenge, unit.source_link, unit.text[:2100])
