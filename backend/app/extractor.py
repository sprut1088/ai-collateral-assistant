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
from datetime import date, datetime, timedelta
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
ANTHROPIC_TIMEOUT_SECONDS = float(os.environ.get("ACOA_ANTHROPIC_TIMEOUT_SECONDS", "25"))
ANTHROPIC_MAX_RETRIES = int(os.environ.get("ACOA_ANTHROPIC_MAX_RETRIES", "1"))
EXTRACTION_MAX_TOKENS = int(os.environ.get("ACOA_EXTRACTION_MAX_TOKENS", "1600"))
SEGMENT_EXTRACTION_MAX_TOKENS = int(os.environ.get("ACOA_SEGMENT_EXTRACTION_MAX_TOKENS", "900"))
CLARIFICATION_MAX_TOKENS = int(os.environ.get("ACOA_CLARIFICATION_MAX_TOKENS", "500"))
MULTI_CASE_FALLBACK_MAX_SEGMENTS = int(
    os.environ.get("ACOA_MULTI_CASE_FALLBACK_MAX_SEGMENTS", "2")
)

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

REQUEST_TYPE_HINTS = (
    ("Collateral Substitution", ("substitute", "substitution", "replacement collateral", "replace collateral")),
    ("Margin Call", ("margin call",)),
    (
        "Collateral Transfer",
        (
            "collateral transfer",
            "transfer collateral",
            "transfer",
            "return collateral",
            "release collateral",
            "released",
            "returned",
            "return this amount",
        ),
    ),
    ("Settlement Instruction", ("settlement", "dvp", "delivery versus payment")),
    ("Dispute", ("dispute", "contest")),
    ("Exposure Inquiry", ("exposure", "inquiry")),
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
RELATIVE_TPLUS_RE = re.compile(r"\bt\s*\+\s*(\d{1,2})\b", re.IGNORECASE)
RELATIVE_IN_DAYS_RE = re.compile(r"\bin\s+(\d{1,2})\s+days?\b", re.IGNORECASE)
RELATIVE_IN_WEEKS_RE = re.compile(r"\bin\s+(\d{1,2})\s+weeks?\b", re.IGNORECASE)
NEXT_WEEKDAY_RE = re.compile(
    r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
VALUE_DATE_RE = re.compile(
    r"\bvalue\s+date\s*(?:of|is|[:\-])?\s*([^\n\r]+)",
    re.IGNORECASE,
)

RELATIVE_TIMELINE_PATTERNS = [
    re.compile(r"\bday before yesterday\b", re.IGNORECASE),
    re.compile(r"\bday after tomorrow\b", re.IGNORECASE),
    re.compile(r"\bthis day of next week\b", re.IGNORECASE),
    re.compile(r"\bthis day next week\b", re.IGNORECASE),
    re.compile(r"\bnext week this day\b", re.IGNORECASE),
    re.compile(r"\bsame day next week\b", re.IGNORECASE),
    re.compile(r"\bthis day of last week\b", re.IGNORECASE),
    re.compile(r"\bthis day last week\b", re.IGNORECASE),
    re.compile(r"\blast week this day\b", re.IGNORECASE),
    re.compile(r"\bsame day last week\b", re.IGNORECASE),
    re.compile(r"\bend of current week\b", re.IGNORECASE),
    re.compile(r"\bend of this week\b", re.IGNORECASE),
    re.compile(r"\bend of the week\b", re.IGNORECASE),
    re.compile(r"\bend of week\b", re.IGNORECASE),
    re.compile(r"\bclose of current week\b", re.IGNORECASE),
    re.compile(r"\bclose of week\b", re.IGNORECASE),
    re.compile(r"\bcurrent week end\b", re.IGNORECASE),
    re.compile(r"\bweek end\b", re.IGNORECASE),
    NEXT_WEEKDAY_RE,
    RELATIVE_TPLUS_RE,
    RELATIVE_IN_DAYS_RE,
    RELATIVE_IN_WEEKS_RE,
    re.compile(r"\byesterday\b", re.IGNORECASE),
    re.compile(r"\btoday\b", re.IGNORECASE),
    re.compile(r"\btomorrow(?:\s+eod)?\b", re.IGNORECASE),
    re.compile(r"\bnext week\b", re.IGNORECASE),
    re.compile(r"\blast week\b", re.IGNORECASE),
    re.compile(r"\bsame day\b|\bsame-date\b|\bsame date\b", re.IGNORECASE),
]

WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

DATE_LITERAL_RE = re.compile(
    r"\b\d{1,2}\s+[A-Za-z]{3,9}(?:\s+\d{4})?\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b",
    re.IGNORECASE,
)
NORMALIZED_CALENDAR_DATE_RE = re.compile(r"^\d{1,2}-[A-Za-z]{3,9}$", re.IGNORECASE)

_FIELD_PROPS = {
    "value": {"type": ["string", "null"], "description": "Extracted value, or null if absent from the email"},
    "confidence": {"type": "number", "description": "0 to 1"},
    "evidence": {"type": ["string", "null"], "description": "Short verbatim snippet from the email supporting this value"},
}
_FIELD = {"type": "object", "properties": _FIELD_PROPS, "required": ["value", "confidence"]}

ENTITY_FIELDS = [
    "counterparty", "account", "amount", "currency", "value_date",
    "deadline", "isin_cusip", "collateral_type", "replacement_asset",
    "agreement_reference",
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


def _today() -> date:
    return datetime.now().date()


def _new_anthropic_client():
    import anthropic

    try:
        return anthropic.Anthropic(
            timeout=ANTHROPIC_TIMEOUT_SECONDS,
            max_retries=ANTHROPIC_MAX_RETRIES,
        )
    except TypeError:
        # Compatibility fallback for SDK versions without explicit timeout/retry kwargs.
        return anthropic.Anthropic()


def _format_value_date(value: date) -> str:
    return f"{value.day}-{value.strftime('%B')}"


def _try_parse_absolute_date(text: str) -> date | None:
    normalized = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", text, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    today = _today()
    candidates = [
        "%d %B %Y",
        "%d %b %Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d %B",
        "%d %b",
        "%d/%m",
        "%d-%m",
    ]
    for fmt in candidates:
        try:
            parse_text = normalized
            parse_fmt = fmt
            if "%Y" not in fmt:
                if fmt in ("%d %B", "%d %b"):
                    parse_text = f"{normalized} {today.year}"
                    parse_fmt = f"{fmt} %Y"
                elif fmt == "%d/%m":
                    parse_text = f"{normalized}/{today.year}"
                    parse_fmt = "%d/%m/%Y"
                elif fmt == "%d-%m":
                    parse_text = f"{normalized}-{today.year}"
                    parse_fmt = "%d-%m-%Y"

            parsed = datetime.strptime(parse_text, parse_fmt)
            return parsed.date()
        except ValueError:
            continue
    return None


def _normalize_value_date_value(value: Any) -> str | None:
    """Normalizes absolute and relative timeline phrases to a concrete date string."""
    text = _string_or_none(value)
    if text is None:
        return None

    if NORMALIZED_CALENDAR_DATE_RE.match(text):
        return text

    lowered = text.lower()
    today = _today()

    def _shift(days: int) -> str:
        return _format_value_date(today + timedelta(days=days))

    def _next_weekday(target_name: str) -> str:
        target = WEEKDAY_INDEX[target_name]
        days_ahead = (target - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return _format_value_date(today + timedelta(days=days_ahead))

    def _current_week_end() -> str:
        # Bank-style week end defaults to Friday of the current week.
        delta = 4 - today.weekday()
        return _format_value_date(today + timedelta(days=delta))

    def _looks_date_semantic(text_value: str) -> bool:
        semantic_tokens = (
            "today",
            "tomorrow",
            "yesterday",
            "day after tomorrow",
            "day before yesterday",
            "next week",
            "last week",
            "week end",
            "end of current week",
            "end of this week",
            "end of week",
            "close of week",
            "same day",
            "same date",
            "following day",
        )
        if any(token in text_value for token in semantic_tokens):
            return True
        if RELATIVE_TPLUS_RE.search(text_value):
            return True
        if RELATIVE_IN_DAYS_RE.search(text_value) or RELATIVE_IN_WEEKS_RE.search(text_value):
            return True
        if NEXT_WEEKDAY_RE.search(text_value):
            return True
        if DATE_LITERAL_RE.search(text_value):
            return True
        if any(day in text_value for day in WEEKDAY_INDEX.keys()):
            return True
        return False

    if "day before yesterday" in lowered:
        return _shift(-2)

    if "yesterday" in lowered:
        return _shift(-1)

    if "day after tomorrow" in lowered:
        return _shift(2)

    if "this day of next week" in lowered:
        return _shift(7)

    if "next week this day" in lowered or "this day next week" in lowered or "same day next week" in lowered:
        return _shift(7)

    if "this day of last week" in lowered:
        return _shift(-7)

    if "this day last week" in lowered or "last week this day" in lowered or "same day last week" in lowered:
        return _shift(-7)

    if any(
        token in lowered
        for token in (
            "end of current week",
            "end of this week",
            "end of the week",
            "end of week",
            "close of current week",
            "close of week",
            "current week end",
            "week end",
        )
    ):
        return _current_week_end()

    if "next week" in lowered:
        return _shift(7)

    if "last week" in lowered:
        return _shift(-7)

    next_weekday_match = NEXT_WEEKDAY_RE.search(lowered)
    if next_weekday_match:
        return _next_weekday(next_weekday_match.group(1).lower())

    tplus_match = RELATIVE_TPLUS_RE.search(lowered)
    if tplus_match:
        return _shift(int(tplus_match.group(1)))

    in_days_match = RELATIVE_IN_DAYS_RE.search(lowered)
    if in_days_match:
        return _shift(int(in_days_match.group(1)))

    in_weeks_match = RELATIVE_IN_WEEKS_RE.search(lowered)
    if in_weeks_match:
        return _shift(int(in_weeks_match.group(1)) * 7)

    if any(token in lowered for token in ("same day", "same-day", "same date", "today")):
        return _shift(0)

    if any(token in lowered for token in ("tomorrow", "next day", "next-date", "next date", "following day")):
        return _shift(1)

    parsed_absolute = _try_parse_absolute_date(text)
    if parsed_absolute is not None:
        return _format_value_date(parsed_absolute)

    if not _looks_date_semantic(lowered):
        return None

    return text


def _extract_relative_timeline_phrase(text: str | None) -> str | None:
    raw = _string_or_none(text)
    if not raw:
        return None

    for pattern in RELATIVE_TIMELINE_PATTERNS:
        match = pattern.search(raw)
        if match:
            return raw[match.start():match.end()].strip(" ,.;")
    return None


def _extract_value_date_phrase(text: str | None) -> str | None:
    raw = _string_or_none(text)
    if not raw:
        return None

    match = VALUE_DATE_RE.search(raw)
    if not match:
        return None
    # Prefer the first clause after "value date" to avoid swallowing trailing
    # deadline sentences in the same paragraph.
    raw_segment = (match.group(1) or "").strip()
    candidate = re.split(r"[\n\.;,]", raw_segment, maxsplit=1)[0].strip(" ,.;")
    if not candidate:
        return None

    if _normalize_value_date_value(candidate) is None:
        return None
    return candidate


def _backfill_case_timeline_fields(case: dict, raw_case: dict | None = None) -> None:
    entities = case.get("entities") or {}
    raw_case = raw_case if isinstance(raw_case, dict) else {}
    raw_entities = raw_case.get("entities") if isinstance(raw_case.get("entities"), dict) else {}

    context_parts = [
        raw_case.get("summary"),
        case.get("summary"),
        raw_case.get("suggested_action"),
        case.get("suggested_action"),
    ]

    for source_entities in (raw_entities, entities):
        instruction_slot = source_entities.get("instruction_details")
        if isinstance(instruction_slot, dict):
            context_parts.append(instruction_slot.get("value"))
            context_parts.append(instruction_slot.get("evidence"))

    context_text = "\n".join(str(part) for part in context_parts if _string_or_none(part))

    value_slot = entities.get("value_date") or {"value": None, "confidence": 0.0, "evidence": None}
    if value_slot.get("value") in (None, ""):
        value_phrase = _extract_value_date_phrase(context_text)
        if value_phrase:
            normalized_value = _normalize_value_date_value(value_phrase)
            if normalized_value:
                value_slot["value"] = normalized_value
                value_slot["confidence"] = max(_clamp_conf(value_slot.get("confidence")), 0.72)
                value_slot["evidence"] = value_phrase
                entities["value_date"] = value_slot

    deadline_slot = entities.get("deadline") or {"value": None, "confidence": 0.0, "evidence": None}
    if deadline_slot.get("value") in (None, ""):
        deadline_phrase = _extract_relative_timeline_phrase(context_text)
        if deadline_phrase:
            normalized_deadline = _normalize_value_date_value(deadline_phrase)
            if normalized_deadline:
                deadline_slot["value"] = normalized_deadline
                deadline_slot["confidence"] = max(_clamp_conf(deadline_slot.get("confidence")), 0.72)
                deadline_slot["evidence"] = deadline_phrase
                entities["deadline"] = deadline_slot

    case["entities"] = entities


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
        if field in {"value_date", "deadline"}:
            entities[field]["value"] = _normalize_value_date_value(entities[field].get("value"))

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
    _backfill_case_timeline_fields(case, raw)
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


def _looks_instruction_paragraph(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:usd|eur|gbp|jpy|isin|cusip|value\s+date|account|settle|settlement|dvp|transfer|return|release|substitute|pledge|margin)\b",
            text,
            re.IGNORECASE,
        )
    )


def _extract_collateral_paragraph_sections(body: str) -> list[str]:
    paragraphs = [segment.strip() for segment in re.split(r"\n\s*\n", body) if segment.strip()]
    sections = [
        segment
        for segment in paragraphs
        if _looks_collateral_text(segment) and _looks_instruction_paragraph(segment)
    ]
    # Use paragraph segmentation only when there are clearly multiple collateral sections.
    return sections if len(sections) >= 2 else []


def _detect_request_type(body_text: str) -> str:
    lowered = body_text.lower()
    for req_type, hints in REQUEST_TYPE_HINTS:
        if any(hint in lowered for hint in hints):
            return req_type
    return "General Inquiry"


def _infer_counterparty_from_signature(body_text: str) -> tuple[str | None, str | None]:
    lines = [line.strip(" ,.;") for line in body_text.splitlines() if line.strip()]
    if not lines:
        return None, None

    blocked = {
        "dear team",
        "kind regards",
        "regards",
        "thanks",
        "thank you",
        "collateral management",
    }
    for candidate in reversed(lines[-6:]):
        lowered = candidate.lower()
        if lowered in blocked:
            continue
        if len(candidate) < 3 or len(candidate) > 80:
            continue
        if any(ch.isdigit() for ch in candidate):
            continue
        if "@" in candidate:
            continue
        return candidate, candidate

    return None, None


def _is_likely_account_identifier(value: str) -> bool:
    text = value.strip()
    if len(text) < 4:
        return False
    if text.lower() in {"on", "the", "same", "account"}:
        return False
    return any(ch.isdigit() for ch in text) or "-" in text or "_" in text


def _set_entity_value(
    entities: dict,
    field: str,
    value: str | None,
    confidence: float,
    evidence: str | None,
) -> None:
    if value in (None, ""):
        return
    slot = entities.get(field) or {"value": None, "confidence": 0.0, "evidence": None}
    slot["value"] = value
    slot["confidence"] = _fallback_confidence_from_evidence(field, value, evidence, confidence)
    slot["evidence"] = evidence
    entities[field] = slot


def _fallback_confidence_from_evidence(
    field: str,
    value: str | None,
    evidence: str | None,
    base_confidence: float,
) -> float:
    conf = _clamp_conf(base_confidence)
    val = _string_or_none(value)
    ev = _string_or_none(evidence)
    if not val or not ev:
        return conf

    val_lower = val.lower()
    ev_lower = ev.lower()
    contains_match = val_lower in ev_lower

    if field == "amount":
        normalized_amount = _normalize_amount_value(val)
        normalized_evidence_amount = _normalize_amount_value(ev)
        if normalized_amount and normalized_evidence_amount and normalized_amount == normalized_evidence_amount:
            conf = max(conf, 0.96)
        elif contains_match:
            conf = max(conf, 0.92)

    elif field == "currency":
        token = re.search(r"\b([A-Z]{3})\b", ev.upper())
        if token and token.group(1).upper() == val.upper():
            conf = max(conf, 0.95)
        elif contains_match:
            conf = max(conf, 0.9)

    elif field == "value_date":
        date_matched = False
        normalized_evidence_date = _normalize_value_date_value(ev)
        if normalized_evidence_date and normalized_evidence_date == val:
            conf = max(conf, 0.95)
            date_matched = True
        else:
            date_candidates = re.findall(
                r"\b\d{1,2}\s+[A-Za-z]{3,9}(?:\s+\d{4})?\b|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b|\b\d{4}-\d{2}-\d{2}\b",
                ev,
            )
            for candidate in date_candidates:
                normalized_candidate = _normalize_value_date_value(candidate)
                if normalized_candidate and normalized_candidate == val:
                    conf = max(conf, 0.95)
                    date_matched = True
                    break
        if not date_matched and contains_match:
            conf = max(conf, 0.9)

    elif field == "isin_cusip":
        if re.search(rf"\b{re.escape(val)}\b", ev, re.IGNORECASE):
            conf = max(conf, 0.97)

    elif field in {"account", "agreement_reference"}:
        if contains_match:
            conf = max(conf, 0.93)

    elif field in {"collateral_type", "replacement_asset", "counterparty"}:
        if contains_match:
            conf = max(conf, 0.9)

    return round(conf, 2)


def _fallback_request_type_confidence(request_type: str) -> float:
    if request_type == "General Inquiry":
        return 0.7
    return 0.92


def _rule_based_case_payload(
    body_text: str,
    reason: str,
    fallback_counterparty: str | None = None,
) -> dict:
    entities = _empty_entities()
    request_type = _detect_request_type(body_text)

    amount_match = re.search(r"\b([A-Z]{3})\s*([0-9][0-9,]*(?:\.\d+)?)\b", body_text)
    if amount_match:
        ccy = (amount_match.group(1) or "").upper()
        raw_amount = amount_match.group(2) or ""
        _set_entity_value(entities, "currency", ccy, 0.76, amount_match.group(0))
        _set_entity_value(entities, "amount", _normalize_amount_value(raw_amount), 0.78, amount_match.group(0))

    account_match = re.search(r"\baccount\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9_\-\/]*)", body_text, re.IGNORECASE)
    if account_match:
        candidate_account = account_match.group(1)
        if _is_likely_account_identifier(candidate_account):
            _set_entity_value(entities, "account", candidate_account, 0.74, account_match.group(0))

    isin_match = re.search(r"\b(?:ISIN|CUSIP)\s*[:\-]?\s*([A-Z0-9]{9,12})\b", body_text, re.IGNORECASE)
    if isin_match:
        _set_entity_value(entities, "isin_cusip", isin_match.group(1).upper(), 0.82, isin_match.group(0))

    value_date_match = re.search(
        r"\bvalue\s+date\s*(?:of|[:\-])?\s*("
        r"\d{1,2}\s+[A-Za-z]{3,9}(?:\s+\d{4})?"
        r"|\d{4}-\d{2}-\d{2}"
        r"|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
        r"|t\s*\+\s*\d{1,2}"
        r"|same\s+day"
        r"|tomorrow"
        r")\b",
        body_text,
        re.IGNORECASE,
    )
    if value_date_match:
        value_date_text = value_date_match.group(1).strip().rstrip(".,;")
        _set_entity_value(
            entities,
            "value_date",
            _normalize_value_date_value(value_date_text),
            0.73,
            value_date_text,
        )

    deadline_phrase = _extract_relative_timeline_phrase(body_text)
    if deadline_phrase:
        _set_entity_value(
            entities,
            "deadline",
            _normalize_value_date_value(deadline_phrase),
            0.74,
            deadline_phrase,
        )

    current_collateral_match = re.search(r"\bcurrent\s+collateral\s*:\s*([^\n\r]+)", body_text, re.IGNORECASE)
    if current_collateral_match:
        current_collateral = current_collateral_match.group(1).strip().rstrip(".,;")
        collateral_type = "Cash" if "cash" in current_collateral.lower() else current_collateral
        _set_entity_value(entities, "collateral_type", collateral_type, 0.74, current_collateral_match.group(0))
    elif re.search(r"\bcash\s+collateral\b", body_text, re.IGNORECASE):
        _set_entity_value(entities, "collateral_type", "Cash", 0.72, "cash collateral")

    replacement_match = re.search(r"\bsubstitute\s+collateral\s*:\s*([^\n\r]+)", body_text, re.IGNORECASE)
    if replacement_match:
        replacement_asset = replacement_match.group(1).strip().rstrip(".,;")
        _set_entity_value(entities, "replacement_asset", replacement_asset, 0.76, replacement_match.group(0))
    elif request_type == "Collateral Substitution":
        short_replacement = re.search(r"\bwith\s+([^\.\n\r]+)", body_text, re.IGNORECASE)
        if short_replacement:
            _set_entity_value(
                entities,
                "replacement_asset",
                short_replacement.group(1).strip().rstrip(".,;"),
                0.64,
                short_replacement.group(0),
            )

    counterparty, counterparty_evidence = _infer_counterparty_from_signature(body_text)
    if not counterparty and fallback_counterparty:
        counterparty = fallback_counterparty
        counterparty_evidence = fallback_counterparty
    _set_entity_value(entities, "counterparty", counterparty, 0.68, counterparty_evidence)

    summary = (
        f"Fallback extraction identified a {request_type} request while AI connectivity was unavailable."
    )
    return {
        "request_type": request_type,
        "request_type_confidence": _fallback_request_type_confidence(request_type),
        "summary": summary,
        "entities": entities,
        "ambiguities": [f"AI extraction unavailable. Applied deterministic fallback. Reason: {reason}"],
        "suggested_action": "Review extracted fields and proceed with standard validation workflow.",
    }


def _rule_based_fallback_extraction(raw_email: dict, reason: str) -> dict:
    body = str(raw_email.get("body") or "")
    detected = _looks_collateral_text(body)
    if not detected:
        return _normalize_extraction_result(
            {
                "collateral_request_detected": False,
                "multiple_requests_detected": False,
                "request_count": 0,
                "customer_tone": "Standard",
                "summary": "No collateral-related instruction detected in the email text.",
                "requests": [],
                "ambiguities": [f"AI extraction unavailable. Applied deterministic fallback. Reason: {reason}"],
                "suggested_action": "No collateral action required.",
            }
        )

    fallback_counterparty, _ = _infer_counterparty_from_signature(body)
    sections = _extract_collateral_paragraph_sections(body)
    section_cases: list[tuple[str, dict]] = []
    if sections:
        for section in sections[:4]:
            case_payload = _rule_based_case_payload(section, reason, fallback_counterparty)
            case = _normalize_case(case_payload)
            if _has_any_extracted_value(case):
                section_cases.append((section, case))

    if len(section_cases) > 1:
        first_entities = section_cases[0][1].get("entities") or {}
        base_account = (first_entities.get("account") or {}).get("value")
        base_value_date = (first_entities.get("value_date") or {}).get("value")

        for idx in range(1, len(section_cases)):
            section_text, case = section_cases[idx]
            entities = case.get("entities") or {}

            if (
                base_account
                and (entities.get("account") or {}).get("value") in (None, "")
                and re.search(r"\bsame\s+agreement\b", section_text, re.IGNORECASE)
            ):
                _set_entity_value(entities, "account", base_account, 0.84, "same agreement")

            if (
                base_value_date
                and (entities.get("value_date") or {}).get("value") in (None, "")
                and re.search(r"\bsame\s+value\s+date\b", section_text, re.IGNORECASE)
            ):
                _set_entity_value(entities, "value_date", base_value_date, 0.86, "same value date")

            case["entities"] = entities
            case["overall_confidence"] = _compute_case_confidence(case)

    cases = [case for _, case in section_cases]

    if not cases:
        single_case_payload = _rule_based_case_payload(body, reason, fallback_counterparty)
        cases = [_normalize_case(single_case_payload)]

    tone = "Urgent" if re.search(r"\burgent|immediate|asap\b", body, re.IGNORECASE) else "Standard"
    if len(cases) > 1:
        summary = (
            f"Fallback extraction identified {len(cases)} distinct collateral instructions while AI connectivity was unavailable."
        )
    else:
        summary = cases[0].get("summary") or (
            "Fallback extraction identified a collateral request while AI connectivity was unavailable."
        )

    payload = {
        "collateral_request_detected": True,
        "multiple_requests_detected": len(cases) > 1,
        "request_count": len(cases),
        "customer_tone": tone,
        "summary": summary,
        "requests": cases,
        "ambiguities": [f"AI extraction unavailable. Applied deterministic fallback. Reason: {reason}"],
        "suggested_action": "Review extracted fields and proceed with standard validation workflow.",
    }
    return _normalize_extraction_result(payload)


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
        max_tokens=SEGMENT_EXTRACTION_MAX_TOKENS,
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

    if MULTI_CASE_FALLBACK_MAX_SEGMENTS > 0:
        sections = sections[:MULTI_CASE_FALLBACK_MAX_SEGMENTS]

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
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _rule_based_fallback_extraction(
            raw_email,
            "ANTHROPIC_API_KEY is not set",
        )

    try:
        client = _new_anthropic_client()
        response = client.messages.create(
            model=MODEL,
            max_tokens=EXTRACTION_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tool_choice={"type": "tool", "name": "record_collateral_request"},
            tools=[EXTRACTION_TOOL],
            messages=[{"role": "user", "content": build_email_prompt(raw_email)}],
        )
    except Exception as exc:
        return _rule_based_fallback_extraction(raw_email, str(exc))

    for block in response.content:
        if block.type == "tool_use":
            result = dict(block.input)
            normalized = _normalize_extraction_result(result)
            if _needs_multi_case_fallback(normalized, raw_email):
                normalized = _apply_multi_case_fallback(client, normalized, raw_email)
            requests = normalized.get("requests") if isinstance(normalized.get("requests"), list) else []
            if normalized.get("collateral_request_detected") and (
                not requests or all(not _has_any_extracted_value(case) for case in requests)
            ):
                return _rule_based_fallback_extraction(
                    raw_email,
                    "Model returned collateral summary without structured case entities",
                )
            return normalized
    return _rule_based_fallback_extraction(raw_email, "Model returned no structured output")


def _fallback_clarification_draft(raw_email: dict, missing_fields: list[str], reason: str) -> str:
    subject = str(raw_email.get("subject") or "Collateral request")
    sender_name = str(raw_email.get("sender") or "Client Team")
    cleaned = [str(item).strip() for item in (missing_fields or []) if str(item).strip()]
    if not cleaned:
        cleaned = ["confirmation of the request details"]

    lines = [
        f"Subject: Clarification required - {subject}",
        "",
        f"Dear {sender_name},",
        "",
        "Thank you for your request. To proceed, please confirm the following details:",
    ]
    lines.extend(f"- {field}" for field in cleaned)
    lines.extend(
        [
            "",
            "Once we receive the clarifications, we will continue processing promptly.",
            "",
            "Regards,",
            "Collateral Operations Team",
            "",
            f"[Fallback draft generated because AI drafting was unavailable: {reason}]",
        ]
    )
    return "\n".join(lines)


def draft_clarification(raw_email: dict, extraction: dict, missing_fields: list[str]) -> str:
    """Drafts a professional clarification email for missing mandatory data."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _fallback_clarification_draft(
            raw_email,
            missing_fields,
            "ANTHROPIC_API_KEY is not set",
        )

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

    try:
        client = _new_anthropic_client()
        response = client.messages.create(
            model=MODEL,
            max_tokens=CLARIFICATION_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        drafted = "".join(b.text for b in response.content if b.type == "text").strip()
        if drafted:
            return drafted
    except Exception as exc:
        return _fallback_clarification_draft(raw_email, missing_fields, str(exc))

    return _fallback_clarification_draft(
        raw_email,
        missing_fields,
        "Model returned empty clarification draft",
    )
