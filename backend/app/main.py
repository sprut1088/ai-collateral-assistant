"""AI Collateral Operations Assistant — API.

Lifecycle: upload -> parse -> Claude extraction -> deterministic validation
-> HITL review (approve / edit / reject / ask customer) -> (future) Colline
integration layer.
"""
import json
import hashlib
import os

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .settings import load_environment

load_environment()

from . import database as db
from . import email_parser, extractor, validation
from .batch_runner import BatchRunner

app = FastAPI(title="AI Collateral Operations Assistant", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

TERMINAL = {"Approved", "Rejected", "Not a collateral request"}
BATCH_RUNTIME_ROOT = os.environ.get(
    "ACOA_BATCH_RUNTIME_ROOT",
    r"C:\git_repos\ai-collateral-assistant\samples",
)
BATCH_INTERVAL_SECONDS = int(os.environ.get("ACOA_BATCH_INTERVAL_SECONDS", "30"))


class EntitiesPatch(BaseModel):
    entities: dict  # {field_name: new_value}
    case_index: int | None = None


class ActionNote(BaseModel):
    note: str


class ConfigUpdate(BaseModel):
    llm_model: str | None = None
    anthropic_api_key: str | None = None
    batch_interval_seconds: int | None = None
    batch_enabled: bool | None = None
    use_truststore: bool | None = None


def _mask_api_key(value: str | None) -> str:
    if not value:
        return "Not configured"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _multi_case_extraction(extraction: dict | None) -> list[dict]:
    if not extraction:
        return []
    cases = extraction.get("requests")
    return cases if isinstance(cases, list) else []


def _derive_request_status_for_cases(validation_status: str, cases: list[dict]) -> str:
    if not cases:
        return validation_status

    decisions = [str(case.get("decision_status") or "Pending Review") for case in cases]
    if decisions and all(d == "Approved" for d in decisions):
        return "Approved"
    if decisions and all(d == "Rejected" for d in decisions):
        return "Rejected"
    if any(d == "Awaiting Customer" for d in decisions):
        return "Awaiting Customer"
    return validation_status


def _case_validation(validation_result: dict | None, case_index: int) -> dict | None:
    if not validation_result:
        return None
    case_rows = validation_result.get("cases")
    if not isinstance(case_rows, list):
        return None
    for row in case_rows:
        if row.get("case_index") == case_index:
            return row
    return None


def _update_request_after_case_action(req_id: str, extraction: dict, validation_result: dict) -> dict:
    status = _derive_request_status_for_cases(validation_result.get("status", "Ready for Review"), _multi_case_extraction(extraction))
    update_fields = {
        "extraction": extraction,
        "validation": validation_result,
        "status": status,
    }
    if status == "Approved":
        update_fields["approved_at"] = db.now_iso()
        update_fields["rejected_at"] = None
    elif status == "Rejected":
        update_fields["rejected_at"] = db.now_iso()
        update_fields["approved_at"] = None
    elif status == "Awaiting Customer":
        update_fields["clarification_requested_at"] = db.now_iso()
    db.update_request(req_id, **update_fields)
    return db.get_request(req_id)


def _process_email_content(
    filename: str,
    content: bytes,
    source_mode: str = "upload",
    source_path: str | None = None,
    content_hash: str | None = None,
) -> dict:
    file_type, raw_email = email_parser.parse_email_file(filename or "upload", content)
    if not content_hash:
        content_hash = hashlib.sha256(content).hexdigest()

    req_id = db.create_request(
        filename or "upload",
        file_type,
        raw_email,
        content_hash=content_hash,
        source_mode=source_mode,
        source_path=source_path,
    )
    try:
        extraction = extractor.extract(raw_email)
        if not extraction.get("collateral_request_detected", True):
            db.add_event(req_id, "ai_extraction", "Not a collateral request")
        elif extraction.get("multiple_requests_detected"):
            db.add_event(
                req_id,
                "ai_extraction",
                f"Detected {extraction.get('request_count', 0)} collateral requests in one email",
            )
        else:
            db.add_event(
                req_id,
                "ai_extraction",
                f"Classified as {extraction['request_type']} "
                f"({extraction.get('request_type_confidence', 0):.0%} confidence)",
            )
        result = validation.validate(raw_email, extraction)
        db.update_request(req_id, extraction=extraction, validation=result, status=result["status"])
        db.add_event(req_id, "validation", f"Validation result: {result['status']}")
    except Exception as e:
        db.update_request(req_id, status="Processing Failed", error=str(e))
        db.add_event(req_id, "error", str(e))
    return db.get_request(req_id)


batch_runner = BatchRunner(
    runtime_root=BATCH_RUNTIME_ROOT,
    interval_seconds=BATCH_INTERVAL_SECONDS,
    process_callback=_process_email_content,
    duplicate_check_callback=db.has_content_hash,
)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "batch_running": batch_runner.status()["running"],
    }


@app.post("/api/files")
async def upload_file(file: UploadFile = File(...)):
    content = await file.read()
    try:
        return _process_email_content(
            filename=file.filename or "upload",
            content=content,
            source_mode="upload",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse the email file: {e}")


@app.get("/api/batch/status")
def batch_status():
    return batch_runner.status()


@app.post("/api/batch/start")
def batch_start():
    return batch_runner.start()


@app.post("/api/batch/stop")
def batch_stop():
    return batch_runner.stop()


@app.post("/api/batch/run-now")
def batch_run_now():
    return batch_runner.run_once()


@app.get("/api/audit")
def audit_trail(limit: int = 1000):
    return db.list_audit_events(limit=limit)


@app.get("/api/config")
def get_config():
    batch_state = batch_runner.status()
    return {
        "llm_model": os.environ.get("ACOA_MODEL", "claude-sonnet-4-6"),
        "llm_api_key_masked": _mask_api_key(os.environ.get("ANTHROPIC_API_KEY")),
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "batch_interval_seconds": batch_state["interval_seconds"],
        "batch_enabled": batch_state["running"],
        "batch_runtime_root": batch_state["runtime_root"],
        "truststore_enabled": os.environ.get("ACOA_USE_TRUSTSTORE") == "1",
    }


@app.put("/api/config")
def update_config(update: ConfigUpdate):
    if update.llm_model is not None:
        os.environ["ACOA_MODEL"] = update.llm_model.strip() or "claude-sonnet-4-6"

    if update.anthropic_api_key is not None:
        key = update.anthropic_api_key.strip()
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key

    if update.batch_interval_seconds is not None:
        batch_runner.set_interval(update.batch_interval_seconds)

    if update.batch_enabled is not None:
        if update.batch_enabled:
            batch_runner.start()
        else:
            batch_runner.stop()

    if update.use_truststore is not None:
        os.environ["ACOA_USE_TRUSTSTORE"] = "1" if update.use_truststore else "0"

    return get_config()


@app.get("/api/files")
def list_files():
    return db.list_requests()


@app.get("/api/files/{req_id}")
def get_file(req_id: str):
    req = db.get_request(req_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


def _require_open(req_id: str) -> dict:
    req = db.get_request(req_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["status"] in TERMINAL:
        raise HTTPException(status_code=409, detail=f"Request is already {req['status'].lower()}")
    return req


def _require_case(req_id: str, case_index: int) -> tuple[dict, dict, list[dict]]:
    req = _require_open(req_id)
    extraction = req.get("extraction")
    if not extraction:
        raise HTTPException(status_code=409, detail="No extraction to edit")

    cases = _multi_case_extraction(extraction)
    if len(cases) <= 1:
        raise HTTPException(status_code=409, detail="Case-level actions require multiple extracted cases")
    if case_index < 0 or case_index >= len(cases):
        raise HTTPException(status_code=400, detail="case_index is out of range")
    return req, extraction, cases


@app.patch("/api/files/{req_id}/entities")
def edit_entities(req_id: str, patch: EntitiesPatch):
    """HITL edit: ops user corrects extracted values; validation is re-run."""
    req = _require_open(req_id)
    extraction = req.get("extraction")
    if not extraction:
        raise HTTPException(status_code=409, detail="No extraction to edit")

    cases = extraction.get("requests") if isinstance(extraction.get("requests"), list) else []
    case_index = patch.case_index
    if cases:
        if case_index is None:
            case_index = 0
        if case_index < 0 or case_index >= len(cases):
            raise HTTPException(status_code=400, detail="case_index is out of range")
        target_entities = cases[case_index].setdefault("entities", {})
    else:
        target_entities = extraction.setdefault("entities", {})

    changed = []
    for field, new_value in patch.entities.items():
        if field not in extractor.ENTITY_FIELDS:
            continue
        slot = target_entities.setdefault(field, {"value": None, "confidence": 0})
        if slot.get("value") != new_value:
            changed.append(f"{field}: '{slot.get('value')}' → '{new_value}'")
            slot["value"] = new_value or None
            slot["confidence"] = 1.0  # human-confirmed
            slot["evidence"] = "Edited by operations user"

    extraction = extractor.recalculate_confidence(extraction)
    result = validation.validate(req["raw_email"], extraction)
    db.update_request(req_id, extraction=extraction, validation=result, status=result["status"])
    if changed:
        db.add_event(req_id, "edited", "; ".join(changed))
        db.add_event(req_id, "validation", f"Re-validated after edit: {result['status']}")
    return db.get_request(req_id)


@app.post("/api/files/{req_id}/approve")
def approve(req_id: str):
    req = _require_open(req_id)
    if len(_multi_case_extraction(req.get("extraction"))) > 1:
        raise HTTPException(status_code=409,
                            detail="Use /cases/{case_index}/approve for multi-case emails.")
    db.update_request(
        req_id,
        status="Approved",
        approved_at=db.now_iso(),
        rejected_at=None,
    )
    db.add_event(req_id, "approved",
                 "Approved by operations user — structured request ready for Colline submission (draft mode)")
    return db.get_request(req_id)


@app.post("/api/files/{req_id}/reject")
def reject(req_id: str, action: ActionNote):
    req = _require_open(req_id)
    if len(_multi_case_extraction(req.get("extraction"))) > 1:
        raise HTTPException(status_code=409,
                            detail="Use /cases/{case_index}/reject for multi-case emails.")
    note = (action.note or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Rejection note is required.")
    db.update_request(req_id, status="Rejected", rejected_at=db.now_iso(), approved_at=None)
    db.add_event(req_id, "rejected", "Rejected by operations user. Note: " + note)
    return db.get_request(req_id)


@app.post("/api/files/{req_id}/ask-customer")
def ask_customer(req_id: str, action: ActionNote):
    req = _require_open(req_id)
    if len(_multi_case_extraction(req.get("extraction"))) > 1:
        raise HTTPException(status_code=409,
                            detail="Use /cases/{case_index}/ask-customer for multi-case emails.")
    note = (action.note or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Ask-customer note is required.")
    extraction = req.get("extraction") or {}
    missing = (req.get("validation") or {}).get("missing_fields") or []
    if not missing:
        missing = ["confirmation of the request details"]
    missing = [*missing, f"Operations note: {note}"]
    try:
        draft = extractor.draft_clarification(req["raw_email"], extraction, missing)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not draft clarification email: {e}")
    db.update_request(
        req_id,
        clarification_draft=draft,
        status="Awaiting Customer",
        clarification_requested_at=db.now_iso(),
        approved_at=None,
    )
    db.add_event(
        req_id,
        "clarification_drafted",
        "Clarification email drafted. Note: " + note,
    )
    return db.get_request(req_id)


@app.post("/api/files/{req_id}/cases/{case_index}/approve")
def approve_case(req_id: str, case_index: int):
    req, extraction, cases = _require_case(req_id, case_index)
    validation_result = req.get("validation") or validation.validate(req["raw_email"], extraction)

    case = cases[case_index]
    case["decision_status"] = "Approved"
    case["approved_at"] = db.now_iso()
    case["rejected_at"] = None

    updated = _update_request_after_case_action(req_id, extraction, validation_result)
    db.add_event(req_id, "case_approved", f"Case {case_index + 1} approved by operations user")
    return updated


@app.post("/api/files/{req_id}/cases/{case_index}/reject")
def reject_case(req_id: str, case_index: int, action: ActionNote):
    req, extraction, cases = _require_case(req_id, case_index)
    validation_result = req.get("validation") or validation.validate(req["raw_email"], extraction)
    note = (action.note or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Rejection note is required.")

    case = cases[case_index]
    case["decision_status"] = "Rejected"
    case["rejected_at"] = db.now_iso()
    case["approved_at"] = None
    case["rejection_note"] = note

    updated = _update_request_after_case_action(req_id, extraction, validation_result)
    db.add_event(
        req_id,
        "case_rejected",
        f"Case {case_index + 1} rejected by operations user. Note: {note}",
    )
    return updated


@app.post("/api/files/{req_id}/cases/{case_index}/ask-customer")
def ask_customer_case(req_id: str, case_index: int, action: ActionNote):
    req, extraction, cases = _require_case(req_id, case_index)
    validation_result = req.get("validation") or validation.validate(req["raw_email"], extraction)
    case_val = _case_validation(validation_result, case_index) or {}
    missing = case_val.get("missing_fields") or ["confirmation of the request details"]
    note = (action.note or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Ask-customer note is required.")
    missing = [*missing, f"Operations note: {note}"]

    case = cases[case_index]
    try:
        draft = extractor.draft_clarification(req["raw_email"], case, missing)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not draft clarification email: {e}")

    case["decision_status"] = "Awaiting Customer"
    case["clarification_draft"] = draft
    case["clarification_note"] = note
    extraction["clarification_draft"] = draft

    updated = _update_request_after_case_action(req_id, extraction, validation_result)
    db.add_event(
        req_id,
        "case_clarification_drafted",
        f"Case {case_index + 1} clarification drafted. Note: {note}",
    )
    return updated


@app.on_event("shutdown")
def shutdown_batch_runner():
    batch_runner.shutdown()
