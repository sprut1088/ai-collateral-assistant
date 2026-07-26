"""Deterministic validation engine — no LLM involvement.

Checks mandatory fields per request type, format rules (ISIN, currency,
amount) and confidence thresholds, then routes the case to a lifecycle
status exactly as defined in the solution deck (slide 9):

  Ready for Review · Missing Mandatory Fields · Low Confidence
"""
import re

CONFIDENCE_THRESHOLD = 0.70
NOT_COLLATERAL_STATUS = "Not a collateral request"

# Mandatory entity fields per request type (minimal baseline for any email:
# sender + subject are checked separately at parse level).
MANDATORY = {
    "Margin Call":             ["counterparty", "amount", "currency"],
    "Collateral Substitution": ["amount", "currency", "collateral_type", "replacement_asset"],
    "Collateral Transfer":     ["counterparty", "amount", "currency", "account"],
    "Settlement Instruction":  ["counterparty", "amount", "currency", "value_date"],
    "Dispute":                 ["counterparty", "amount", "currency"],
    "Exposure Inquiry":        ["counterparty"],
    "General Inquiry":         [],
}

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9X]$", re.IGNORECASE)
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _value(entities: dict, field: str):
    return (entities.get(field) or {}).get("value")


def _validate_case(req_type: str, entities: dict, overall: float) -> dict:
    checks, missing = [], []

    for field in MANDATORY.get(req_type, []):
        if _value(entities, field) in (None, ""):
            missing.append(field)
    checks.append({
        "name": f"Mandatory fields for {req_type}",
        "passed": not missing,
        "detail": "All present" if not missing else "Missing: " + ", ".join(missing),
    })

    isin = _value(entities, "isin_cusip")
    if isin:
        ok = bool(ISIN_RE.match(str(isin).strip()))
        checks.append({
            "name": "ISIN/CUSIP format",
            "passed": ok,
            "detail": str(isin) + ("" if ok else " — does not match ISIN pattern"),
        })

    ccy = _value(entities, "currency")
    if ccy:
        ok = bool(CURRENCY_RE.match(str(ccy).strip().upper()))
        checks.append({
            "name": "Currency code format",
            "passed": ok,
            "detail": str(ccy).upper() + ("" if ok else " — not a 3-letter ISO code"),
        })

    amt = _value(entities, "amount")
    if amt is not None:
        try:
            ok = float(str(amt).replace(",", "")) > 0
        except ValueError:
            ok = False
        checks.append({"name": "Amount is a positive number", "passed": ok, "detail": str(amt)})

    conf_ok = overall >= CONFIDENCE_THRESHOLD
    checks.append({
        "name": f"Overall confidence ≥ {CONFIDENCE_THRESHOLD:.0%}",
        "passed": conf_ok,
        "detail": f"{overall:.0%}",
    })

    if missing:
        status = "Missing Mandatory Fields"
    elif not conf_ok or any(not c["passed"] for c in checks):
        status = "Low Confidence"
    else:
        status = "Ready for Review"

    return {
        "request_type": req_type,
        "status": status,
        "checks": checks,
        "missing_fields": missing,
        "overall_confidence": overall,
    }


def validate(raw_email: dict, extraction: dict) -> dict:
    checks = []

    # 1. Sender present (authorization check is a Colline lookup in production;
    #    here we verify the instruction is attributable at all).
    sender_ok = bool(raw_email.get("sender"))
    checks.append({
        "name": "Sender identified",
        "passed": sender_ok,
        "detail": raw_email.get("sender") or "No sender found in the email file",
    })

    collateral_detected = extraction.get("collateral_request_detected")
    if collateral_detected is False or extraction.get("request_type") == NOT_COLLATERAL_STATUS:
        checks.append({
            "name": "Collateral instruction detected",
            "passed": False,
            "detail": "No collateral-related instruction found.",
        })
        return {
            "status": NOT_COLLATERAL_STATUS,
            "checks": checks,
            "missing_fields": [],
            "confidence_threshold": CONFIDENCE_THRESHOLD,
            "cases": [],
            "multiple_requests_detected": False,
        }

    extraction_cases = extraction.get("requests")
    if isinstance(extraction_cases, list) and extraction_cases:
        cases_in = extraction_cases
    else:
        cases_in = [{
            "request_type": extraction.get("request_type", "General Inquiry"),
            "entities": extraction.get("entities", {}),
            "overall_confidence": extraction.get("overall_confidence", 0),
        }]

    case_results = []
    all_missing = []
    multi = len(cases_in) > 1

    for idx, case in enumerate(cases_in):
        req_type = case.get("request_type", "General Inquiry")
        entities = case.get("entities", {}) or {}
        overall = case.get("overall_confidence")
        if overall is None:
            overall = extraction.get("overall_confidence", 0)
        result = _validate_case(req_type, entities, overall)
        result["case_index"] = idx
        result["summary"] = case.get("summary")
        case_results.append(result)

        if multi:
            checks.extend(
                {
                    "name": f"Case {idx + 1} · {c['name']}",
                    "passed": c["passed"],
                    "detail": c["detail"],
                }
                for c in result["checks"]
            )
            all_missing.extend(f"Case {idx + 1}: {field}" for field in result["missing_fields"])
        else:
            checks.extend(result["checks"])
            all_missing.extend(result["missing_fields"])

    if any(c["status"] == "Missing Mandatory Fields" for c in case_results):
        status = "Missing Mandatory Fields"
    elif any(c["status"] == "Low Confidence" for c in case_results):
        status = "Low Confidence"
    else:
        status = "Ready for Review"

    return {
        "status": status,
        "checks": checks,
        "missing_fields": all_missing,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "cases": case_results,
        "multiple_requests_detected": multi,
    }
