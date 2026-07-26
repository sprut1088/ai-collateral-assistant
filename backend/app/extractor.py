"""AI classification + extraction layer.

Design invariant (per solution spec): the LLM classifies, extracts and
narrates. It never decides. All validation, completeness checks and status
routing are handled deterministically in validation.py.

Uses forced tool-calling against the Anthropic API so the model must return
schema-compliant JSON. Each extracted field carries a confidence (0–1) and an
evidence snippet quoted from the source email for auditability.

Corporate TLS proxy note: if ACOA_USE_TRUSTSTORE=1 is set, the OS trust store
is injected into Python's SSL context before any HTTPS call (fixes
self-signed-in-chain errors behind corporate proxies).
"""
import os
import re
from typing import Any

from .settings import load_environment

load_environment()

if os.environ.get("ACOA_USE_TRUSTSTORE") == "1":
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass

MODEL = os.environ.get("ACOA_MODEL", "claude-sonnet-4-6")

NOT_COLLATERAL_REQUEST = "Not a collateral request"

REQUEST_TYPES = [
    "Margin Call", "Collateral Substitution", "Collateral Transfer",
    "Settlement Instruction", "Dispute", "Exposure Inquiry", "General Inquiry",
]

COLLATERAL_HINTS = (
    "collateral",
    "margin",
    "substitute",
    "substitution",
    "return",
    "pledge",
    "transfer",
    "settlement",
    "csa",
    "isin",
)

AMOUNT_SUFFIX_MULTIPLIERS = {
    "m": 1_000_000,
    "mn": 1_000_000,
    "mln": 1_000_000,
    "mil": 1_000_000,
    "milion": 1_000_000,
    "million": 1_000_000,
    "mm": 1_000_000,
    "b": 1_000_000_000,
    "bn": 1_000_000_000,
    "bln": 1_000_000_000,
    "bil": 1_000_000_000,
    "bilion": 1_000_000_000,
    "billion": 1_000_000_000,
}
AMOUNT_TOKEN_RE = re.compile(r"([-+]?\d[\d,]*(?:\.\d+)?)(?:\s*([a-zA-Z]+))?")

_FIELD_PROPS = {
    "value": {"type": ["string", "null"], "description": "Extracted value, or null if absent from the email"},
    "confidence": {"type": "number", "description": "0 to 1"},
    "evidence": {"type": ["string", "null"], "description": "Short verbatim snippet from the email supporting this value"},
}
_FIELD = {"type": "object", "properties": _FIELD_PROPS, "required": ["value", "confidence"]}

ENTITY_FIELDS = [
    "counterparty", "account", "amount", "currency", "value_date",
    "deadline", "isin_cusip", "collateral_type", "replacement_asset",
    "agreement_reference", "instruction_details",
]

CASE_SCHEMA = {
    "type": "object",
    "properties": {
        "request_type": {"type": "string", "enum": REQUEST_TYPES},
        "request_type_confidence": {"type": "number"},
        "summary": {
            "type": "string",
            "description": "Operational summary specific to this case only",
        },
        "entities": {
            "type": "object",
            "properties": {f: _FIELD for f in ENTITY_FIELDS},
            "required": ENTITY_FIELDS,
        },
        "ambiguities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Anything unclear, conflicting or assumed for this case",
        },
        "suggested_action": {
            "type": "string",
            "description": "Recommended next operational step for this case",
        },
    },
    "required": ["request_type", "request_type_confidence", "summary", "entities"],
}

EXTRACTION_TOOL = {
    "name": "record_collateral_request",
    "description": "Record the structured interpretation of a collateral operations email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "collateral_request_detected": {
                "type": "boolean",
                "description": "False if the email is general and has no collateral action request.",
            },
            "multiple_requests_detected": {
                "type": "boolean",
                "description": "True when the email contains two or more distinct collateral requests.",
            },
            "request_count": {
                "type": "integer",
                "minimum": 0,
                "description": "Number of distinct collateral requests found.",
            },
            "customer_tone": {
                "type": "string",
                "enum": ["Standard", "Follow-up", "Escalation", "Urgent"],
                "description": "Customer mode detected from language and thread context",
            },
            "summary": {
                "type": "string",
                "description": "One or two sentence operational summary of the email and request structure.",
            },
            "requests": {
                "type": "array",
                "items": CASE_SCHEMA,
                "description": "Ordered request list. Keep empty when collateral_request_detected is false.",
            },
            "ambiguities": {
                "type": "array", "items": {"type": "string"},
                "description": "Anything unclear, conflicting or assumed",
            },
            "suggested_action": {"type": "string", "description": "Recommended next operational step"},
        },
        "required": [
            "collateral_request_detected",
            "multiple_requests_detected",
            "request_count",
            "customer_tone",
            "summary",
            "requests",
            "suggested_action",
        ],
    },
}

SYSTEM_PROMPT = """You are the extraction engine of an AI Collateral Operations Assistant \
sitting in front of the Colline collateral management platform. You read one client email \
and record a structured, evidence-linked interpretation using the tool provided.

Rules:
- First decide whether the email includes any collateral-related operational instruction.
- If none exist, set collateral_request_detected=false, request_count=0, requests=[],
  multiple_requests_detected=false, and explain briefly in summary.
- If one or more collateral requests exist, split them into distinct requests in order.
  Do not merge multiple instructions into one case.
- Extract only what the email supports. Never invent values. Use null when a field is absent.
- Quote a short verbatim evidence snippet for every extracted value.
- Confidence must reflect genuine certainty; vague or relative dates ("tomorrow EOD") lower confidence.
- Normalise amounts to plain numbers with thousands removed (e.g. "USD 5M" -> amount 5000000, currency USD).
- You classify and extract. You do not approve, reject or execute anything."""


def _clamp_conf(value: Any) -> float:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        conf = 0.0
    return max(0.0, min(1.0, conf))


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _empty_entities() -> dict:
    return {
        field: {"value": None, "confidence": 0.0, "evidence": None}
        for field in ENTITY_FIELDS
    }


def _normalize_field(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {"value": None, "confidence": 0.0, "evidence": None}
    return {
        "value": _string_or_none(payload.get("value")),
        "confidence": _clamp_conf(payload.get("confidence")),
        "evidence": _string_or_none(payload.get("evidence")),
    }


def _normalize_amount_value(value: Any) -> str | None:
    text = _string_or_none(value)
    if text is None:
        return None

    match = AMOUNT_TOKEN_RE.search(text)
    if not match:
        return text

    raw_number = (match.group(1) or "").replace(",", "")
    if not raw_number:
        return text

    try:
        base = float(raw_number)
    except ValueError:
        return text

    raw_suffix = (match.group(2) or "").lower()
    multiplier = AMOUNT_SUFFIX_MULTIPLIERS.get(raw_suffix, 1)
    amount = base * multiplier

    if abs(amount - round(amount)) < 1e-9:
        return str(int(round(amount)))

    return (f"{amount:.6f}").rstrip("0").rstrip(".")


def _compute_case_confidence(case: dict) -> float:
    confs = [_clamp_conf(case.get("request_type_confidence"))]
    entities = case.get("entities") or {}
    for field in ENTITY_FIELDS:
        slot = entities.get(field) or {}
        if slot.get("value") is not None:
            confs.append(_clamp_conf(slot.get("confidence")))
    return round(sum(confs) / max(len(confs), 1), 3)


def _normalize_case(payload: Any) -> dict:
    raw = payload if isinstance(payload, dict) else {}
    req_type = raw.get("request_type")
    if req_type not in REQUEST_TYPES:
        req_type = "General Inquiry"

    entities = _empty_entities()
    raw_entities = raw.get("entities") if isinstance(raw.get("entities"), dict) else {}
    for field in ENTITY_FIELDS:
        entities[field] = _normalize_field(raw_entities.get(field))
        if field == "amount":
            entities[field]["value"] = _normalize_amount_value(entities[field].get("value"))

    ambiguities = raw.get("ambiguities")
    if isinstance(ambiguities, list):
        ambiguities = [str(item) for item in ambiguities if str(item).strip()]
    else:
        ambiguities = []

    case = {
        "request_type": req_type,
        "request_type_confidence": _clamp_conf(raw.get("request_type_confidence")),
        "summary": str(raw.get("summary") or "").strip(),
        "entities": entities,
        "ambiguities": ambiguities,
        "suggested_action": str(raw.get("suggested_action") or "").strip(),
        "decision_status": str(raw.get("decision_status") or "Pending Review"),
        "clarification_draft": _string_or_none(raw.get("clarification_draft")),
    }
    case["overall_confidence"] = _compute_case_confidence(case)
    return case


def sync_legacy_projection(extraction: dict) -> dict:
    """Keeps top-level single-case fields in sync for backward compatibility."""
    detected = bool(extraction.get("collateral_request_detected", True))
    requests = extraction.get("requests") if isinstance(extraction.get("requests"), list) else []
    extraction["requests"] = requests

    if not detected:
        extraction["request_type"] = NOT_COLLATERAL_REQUEST
        extraction["request_type_confidence"] = 1.0
        extraction["entities"] = _empty_entities()
        extraction.setdefault("customer_tone", "Standard")
        extraction.setdefault("summary", "")
        extraction.setdefault("ambiguities", [])
        extraction.setdefault("suggested_action", "")
        extraction["request_count"] = 0
        extraction["multiple_requests_detected"] = False
        extraction.setdefault("overall_confidence", 0.0)
        return extraction

    if not requests:
        extraction["request_type"] = extraction.get("request_type") or "General Inquiry"
        extraction["request_type_confidence"] = _clamp_conf(extraction.get("request_type_confidence"))
        extraction["entities"] = extraction.get("entities") or _empty_entities()
        extraction["request_count"] = 1
        extraction["multiple_requests_detected"] = False
        extraction["collateral_request_detected"] = True
        extraction.setdefault("customer_tone", "Standard")
        extraction.setdefault("summary", "")
        extraction.setdefault("ambiguities", [])
        extraction.setdefault("suggested_action", "")
        return extraction

    primary = requests[0]
    extraction["request_type"] = primary.get("request_type", "General Inquiry")
    extraction["request_type_confidence"] = _clamp_conf(primary.get("request_type_confidence"))
    extraction["entities"] = primary.get("entities", _empty_entities())
    extraction["request_count"] = len(requests)
    extraction["multiple_requests_detected"] = bool(len(requests) > 1)
    extraction["collateral_request_detected"] = True
    if not extraction.get("summary"):
        extraction["summary"] = primary.get("summary") or ""
    return extraction


def recalculate_confidence(extraction: dict) -> dict:
    requests = extraction.get("requests") if isinstance(extraction.get("requests"), list) else []
    for case in requests:
        case["overall_confidence"] = _compute_case_confidence(case)

    if requests:
        extraction["overall_confidence"] = round(
            sum(_clamp_conf(case.get("overall_confidence")) for case in requests) / len(requests),
            3,
        )
    elif not extraction.get("collateral_request_detected", True):
        extraction["overall_confidence"] = 1.0
    else:
        legacy_case = {
            "request_type_confidence": extraction.get("request_type_confidence", 0),
            "entities": extraction.get("entities") or {},
        }
        extraction["overall_confidence"] = _compute_case_confidence(legacy_case)

    return sync_legacy_projection(extraction)


def _normalize_extraction_result(payload: dict) -> dict:
    detected = bool(payload.get("collateral_request_detected", True))
    raw_requests = payload.get("requests") if isinstance(payload.get("requests"), list) else []
    requests = [_normalize_case(item) for item in raw_requests if isinstance(item, dict)]

    if detected and not requests:
        legacy_case_candidate = {
            "request_type": payload.get("request_type"),
            "request_type_confidence": payload.get("request_type_confidence"),
            "summary": payload.get("summary") or "",
            "entities": payload.get("entities") or {},
            "ambiguities": payload.get("ambiguities") or [],
            "suggested_action": payload.get("suggested_action") or "",
        }
        # Backward-compat fallback when the model still returns legacy single-case shape.
        if any((legacy_case_candidate["entities"] or {}).get(f) for f in ENTITY_FIELDS):
            requests = [_normalize_case(legacy_case_candidate)]

    if not detected:
        requests = []

    ambiguities = payload.get("ambiguities")
    if isinstance(ambiguities, list):
        ambiguities = [str(item) for item in ambiguities if str(item).strip()]
    else:
        ambiguities = []

    extraction = {
        "collateral_request_detected": detected,
        "multiple_requests_detected": bool(payload.get("multiple_requests_detected")) or len(requests) > 1,
        "request_count": len(requests),
        "customer_tone": payload.get("customer_tone") if payload.get("customer_tone") in ["Standard", "Follow-up", "Escalation", "Urgent"] else "Standard",
        "summary": str(payload.get("summary") or "").strip(),
        "requests": requests,
        "ambiguities": ambiguities,
        "suggested_action": str(payload.get("suggested_action") or "").strip(),
    }
    return recalculate_confidence(extraction)


def _has_any_extracted_value(case: dict) -> bool:
    entities = case.get("entities") or {}
    return any((entities.get(field) or {}).get("value") not in (None, "") for field in ENTITY_FIELDS)


def _looks_collateral_text(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in COLLATERAL_HINTS)


def _extract_bullet_sections(body: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    marker = re.compile(r"^\s*(?:\d+[\)\.]|[-*])\s+")

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if marker.match(line):
            if current:
                sections.append(" ".join(current).strip())
            current = [marker.sub("", stripped, count=1)]
            continue

        if current and stripped:
            current.append(stripped)
            continue

        if current and not stripped:
            sections.append(" ".join(current).strip())
            current = []

    if current:
        sections.append(" ".join(current).strip())

    return [segment for segment in sections if segment and _looks_collateral_text(segment)]


def _needs_multi_case_fallback(extraction: dict, raw_email: dict) -> bool:
    if extraction.get("collateral_request_detected") is False:
        return False

    sections = _extract_bullet_sections(raw_email.get("body") or "")
    if len(sections) < 2:
        return False

    requests = extraction.get("requests") if isinstance(extraction.get("requests"), list) else []
    if len(requests) > 1:
        return False

    candidate = requests[0] if requests else extraction
    weak_single_case = (
        candidate.get("request_type") == "General Inquiry" or not _has_any_extracted_value(candidate)
    )
    return weak_single_case and _looks_collateral_text(raw_email.get("body") or "")


def _extract_single_case_from_segment(client: Any, raw_email: dict, segment_text: str, index: int, total: int) -> dict | None:
    segment_prompt = (
        f"From: {raw_email.get('sender') or 'unknown'}\n"
        f"To: {raw_email.get('recipients') or 'unknown'}\n"
        f"Subject: {raw_email.get('subject') or '(no subject)'}\n"
        f"Date: {raw_email.get('sent_date') or 'unknown'}\n\n"
        f"This is request segment {index} of {total} from one email. "
        "Extract exactly one collateral request from this segment.\n\n"
        f"Segment:\n{segment_text}"
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        tool_choice={"type": "tool", "name": "record_collateral_request"},
        tools=[EXTRACTION_TOOL],
        messages=[{"role": "user", "content": segment_prompt}],
    )
    for block in response.content:
        if block.type == "tool_use":
            normalized = _normalize_extraction_result(dict(block.input))
            if normalized.get("collateral_request_detected") is False:
                return None
            requests = normalized.get("requests") if isinstance(normalized.get("requests"), list) else []
            if requests:
                return requests[0]
            fallback = _normalize_case(normalized)
            return fallback if _has_any_extracted_value(fallback) else None
    return None


def _apply_multi_case_fallback(client: Any, extraction: dict, raw_email: dict) -> dict:
    sections = _extract_bullet_sections(raw_email.get("body") or "")
    if len(sections) < 2:
        return extraction

    extracted_cases: list[dict] = []
    total = len(sections)
    for idx, segment in enumerate(sections, start=1):
        try:
            case = _extract_single_case_from_segment(client, raw_email, segment, idx, total)
        except Exception:
            case = None
        if case:
            extracted_cases.append(case)

    if len(extracted_cases) < 2:
        return extraction

    extraction["collateral_request_detected"] = True
    extraction["requests"] = extracted_cases
    extraction["request_count"] = len(extracted_cases)
    extraction["multiple_requests_detected"] = True
    extraction["summary"] = (
        f"Detected {len(extracted_cases)} distinct collateral requests from one email "
        "and extracted them as separate review cases."
    )
    return recalculate_confidence(extraction)


def build_email_prompt(raw_email: dict) -> str:
    return (
        f"From: {raw_email.get('sender') or 'unknown'}\n"
        f"To: {raw_email.get('recipients') or 'unknown'}\n"
        f"Subject: {raw_email.get('subject') or '(no subject)'}\n"
        f"Date: {raw_email.get('sent_date') or 'unknown'}\n"
        f"Attachments: {', '.join(raw_email.get('attachments') or []) or 'none'}\n\n"
        f"{raw_email.get('body') or ''}"
    )


def extract(raw_email: dict) -> dict:
    """Calls Claude with a forced tool choice; returns the tool input dict."""
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export your API key before starting the backend."
        )
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tool_choice={"type": "tool", "name": "record_collateral_request"},
        tools=[EXTRACTION_TOOL],
        messages=[{"role": "user", "content": build_email_prompt(raw_email)}],
    )
    for block in response.content:
        if block.type == "tool_use":
            result = dict(block.input)
            normalized = _normalize_extraction_result(result)
            if _needs_multi_case_fallback(normalized, raw_email):
                normalized = _apply_multi_case_fallback(client, normalized, raw_email)
            return normalized
    raise RuntimeError("Model returned no structured output.")


def draft_clarification(raw_email: dict, extraction: dict, missing_fields: list[str]) -> str:
    """Drafts a professional clarification email for missing mandatory data."""
    import anthropic

    client = anthropic.Anthropic()
    prompt = (
        "Draft a short, professional clarification email from a bank's Collateral Operations "
        "team back to the client below. The client's request cannot be processed yet because "
        f"these mandatory details are missing or unclear: {', '.join(missing_fields)}.\n"
        "Reference their original request politely, list exactly what is needed, keep it under "
        "150 words, and sign off as 'Collateral Operations Team'. Return only the email text "
        "(a Subject: line followed by the body).\n\n"
        f"Original client email:\n{build_email_prompt(raw_email)}\n\n"
        f"Our interpretation: {extraction.get('summary', '')}"
    )
    response = client.messages.create(
        model=MODEL, max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()
