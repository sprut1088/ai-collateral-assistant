"""API tests with the Claude call mocked (deterministic CI-safe suite)."""
import os, tempfile
from pathlib import Path
from datetime import date
from uuid import uuid4
os.environ["ACOA_DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["ACOA_BATCH_RUNTIME_ROOT"] = tempfile.mkdtemp(prefix="acoa-runtime-")
os.environ["ACOA_ENABLE_EVAL_CACHE"] = "0"

from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app import email_parser, validation, extractor

client = TestClient(app)

SAMPLE_TXT = b"""From: john.smith@acmecapital.com
To: collateral.ops@bank.com
Subject: Collateral substitution request
Date: 24 July 2026

Hi Team, please substitute USD 5M cash collateral with US Treasury Bond ISIN US91282CJK92 by tomorrow EOD.
Agreement ref: CSA-ACME-2024-001. Thanks, John
"""

MOCK_EXTRACTION = {
    "request_type": "Collateral Substitution", "request_type_confidence": 0.94,
    "customer_tone": "Standard",
    "summary": "Client requests substitution of USD 5M cash collateral with a US Treasury Bond.",
    "entities": {
        "counterparty": {"value": "Acme Capital", "confidence": 0.85, "evidence": "acmecapital.com"},
        "account": {"value": None, "confidence": 0},
        "amount": {"value": "5000000", "confidence": 0.99, "evidence": "USD 5M"},
        "currency": {"value": "USD", "confidence": 0.99, "evidence": "USD 5M"},
        "value_date": {"value": None, "confidence": 0},
        "deadline": {"value": "Tomorrow EOD", "confidence": 0.88, "evidence": "by tomorrow EOD"},
        "isin_cusip": {"value": "US91282CJK92", "confidence": 0.98, "evidence": "ISIN US91282CJK92"},
        "collateral_type": {"value": "Cash", "confidence": 0.9, "evidence": "cash collateral"},
        "replacement_asset": {"value": "US Treasury Bond", "confidence": 0.87, "evidence": "US Treasury Bond"},
        "agreement_reference": {"value": "CSA-ACME-2024-001", "confidence": 0.97, "evidence": "Agreement ref"},
        "instruction_details": {"value": "Substitute by tomorrow EOD", "confidence": 0.8, "evidence": "by tomorrow EOD"},
    },
    "ambiguities": ["Deadline is relative"], "suggested_action": "Review and approve substitution",
    "overall_confidence": 0.92,
}


def _empty_entities():
    return {
        "counterparty": {"value": None, "confidence": 0},
        "account": {"value": None, "confidence": 0},
        "amount": {"value": None, "confidence": 0},
        "currency": {"value": None, "confidence": 0},
        "value_date": {"value": None, "confidence": 0},
        "deadline": {"value": None, "confidence": 0},
        "isin_cusip": {"value": None, "confidence": 0},
        "collateral_type": {"value": None, "confidence": 0},
        "replacement_asset": {"value": None, "confidence": 0},
        "agreement_reference": {"value": None, "confidence": 0},
        "instruction_details": {"value": None, "confidence": 0},
    }


MOCK_MULTI_EXTRACTION = {
    "collateral_request_detected": True,
    "multiple_requests_detected": True,
    "request_count": 2,
    "customer_tone": "Standard",
    "summary": "Client asks for two distinct collateral actions: return cash and pledge gilts.",
    "requests": [
        {
            "request_type": "Collateral Substitution",
            "request_type_confidence": 0.93,
            "summary": "Return part of existing cash collateral.",
            "entities": {
                "counterparty": {"value": "Priya", "confidence": 0.7, "evidence": "Cheers, Priya"},
                "account": {"value": None, "confidence": 0},
                "amount": {"value": "3000000", "confidence": 0.95, "evidence": "GBP 3,000,000"},
                "currency": {"value": "GBP", "confidence": 0.99, "evidence": "GBP"},
                "value_date": {"value": "Same-day", "confidence": 0.7, "evidence": "Same-day if you can"},
                "deadline": {"value": "Tomorrow", "confidence": 0.5, "evidence": "tomorrow"},
                "isin_cusip": {"value": None, "confidence": 0},
                "collateral_type": {"value": "Cash", "confidence": 0.96, "evidence": "cash collateral"},
                "replacement_asset": {"value": "Cash return", "confidence": 0.72, "evidence": "return GBP"},
                "agreement_reference": {"value": "ALD-CSA-1182", "confidence": 0.99, "evidence": "ref ALD-CSA-1182"},
                "instruction_details": {"value": "Return GBP 3,000,000 cash collateral", "confidence": 0.9, "evidence": "Please return GBP 3,000,000"},
            },
            "overall_confidence": 0.86,
        },
        {
            "request_type": "Collateral Substitution",
            "request_type_confidence": 0.95,
            "summary": "Pledge UK Gilts as replacement collateral on T+2.",
            "entities": {
                "counterparty": {"value": "Priya", "confidence": 0.7, "evidence": "Cheers, Priya"},
                "account": {"value": None, "confidence": 0},
                "amount": {"value": "2500000", "confidence": 0.95, "evidence": "GBP 2,500,000"},
                "currency": {"value": "GBP", "confidence": 0.99, "evidence": "GBP"},
                "value_date": {"value": "T+2", "confidence": 0.9, "evidence": "value T+2"},
                "deadline": {"value": None, "confidence": 0},
                "isin_cusip": {"value": "GB00BBJNQY21", "confidence": 0.99, "evidence": "ISIN GB00BBJNQY21"},
                "collateral_type": {"value": "Cash", "confidence": 0.9, "evidence": "in place of cash"},
                "replacement_asset": {"value": "UK Gilts", "confidence": 0.95, "evidence": "pledge UK Gilts"},
                "agreement_reference": {"value": "ALD-CSA-1182", "confidence": 0.9, "evidence": "CSA (ref ALD-CSA-1182)"},
                "instruction_details": {"value": "Pledge UK Gilts ISIN GB00BBJNQY21 for value T+2", "confidence": 0.9, "evidence": "set that up for value T+2"},
            },
            "overall_confidence": 0.91,
        },
    ],
    "ambiguities": ["Same-day is best-efforts"],
    "suggested_action": "Review both requests separately.",
    "request_type": "Collateral Substitution",
    "request_type_confidence": 0.93,
    "entities": _empty_entities(),
    "overall_confidence": 0.885,
}

MOCK_NON_COLLATERAL_EXTRACTION = {
    "collateral_request_detected": False,
    "multiple_requests_detected": False,
    "request_count": 0,
    "customer_tone": "Standard",
    "summary": "General relationship note with no collateral instruction.",
    "requests": [],
    "ambiguities": [],
    "suggested_action": "No collateral action required.",
    "request_type": "Not a collateral request",
    "request_type_confidence": 1.0,
    "entities": _empty_entities(),
    "overall_confidence": 1.0,
}


def _runtime_root() -> Path:
    return Path(os.environ["ACOA_BATCH_RUNTIME_ROOT"])


def _clear_runtime_dirs():
    root = _runtime_root()
    for name in ("inbox", "processed", "failed", "duplicates"):
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        for child in folder.iterdir():
            if child.is_file():
                child.unlink()

def _upload():
    with patch("app.extractor.extract", return_value=dict(MOCK_EXTRACTION)):
        r = client.post("/api/files", files={"file": ("substitution.txt", SAMPLE_TXT, "text/plain")})
    assert r.status_code == 200
    return r.json()

def test_txt_parsing():
    _, raw = email_parser.parse_email_file("a.txt", SAMPLE_TXT)
    assert raw["sender"] == "john.smith@acmecapital.com"
    assert raw["subject"] == "Collateral substitution request"
    assert "substitute USD 5M" in raw["body"]

def test_upload_and_history():
    d = _upload()
    assert d["status"] == "Ready for Review"
    assert d["extraction"]["request_type"] == "Collateral Substitution"
    hist = client.get("/api/files").json()
    assert any(h["id"] == d["id"] for h in hist)
    assert hist[0]["request_type"] is not None


def test_identical_upload_reuses_cached_evaluation_without_extra_ai_call():
    with patch.dict(os.environ, {"ACOA_ENABLE_EVAL_CACHE": "1"}, clear=False):
        with patch("app.extractor.extract", return_value=dict(MOCK_EXTRACTION)) as mocked_extract:
            first = client.post("/api/files", files={"file": ("cached-1.txt", SAMPLE_TXT, "text/plain")})
            second = client.post("/api/files", files={"file": ("cached-2.txt", SAMPLE_TXT, "text/plain")})

    assert first.status_code == 200
    assert second.status_code == 200
    assert mocked_extract.call_count == 1

    payload = second.json()
    assert payload["extraction"]["request_type"] == "Collateral Substitution"
    assert any(evt["event_type"] == "cache_hit" for evt in payload["events"])


def test_malformed_cached_evaluation_is_skipped_and_recomputed():
    malformed = {
        "collateral_request_detected": True,
        "multiple_requests_detected": False,
        "request_count": 1,
        "customer_tone": "Standard",
        "summary": "Model summary without structured cases.",
        "requests": [],
        "ambiguities": [],
        "suggested_action": "",
        "overall_confidence": 0.0,
        "request_type": "General Inquiry",
        "request_type_confidence": 0.0,
        "entities": _empty_entities(),
    }

    marker = uuid4().hex
    sample_bytes = SAMPLE_TXT + f"\nCache marker: {marker}\n".encode("utf-8")

    with patch.dict(os.environ, {"ACOA_ENABLE_EVAL_CACHE": "1"}, clear=False):
        with patch("app.extractor.extract", side_effect=[malformed, dict(MOCK_EXTRACTION)]) as mocked_extract:
            first = client.post("/api/files", files={"file": ("stale-cache-1.txt", sample_bytes, "text/plain")})
            second = client.post("/api/files", files={"file": ("stale-cache-2.txt", sample_bytes, "text/plain")})

    assert first.status_code == 200
    assert second.status_code == 200
    assert mocked_extract.call_count == 2

    payload = second.json()
    assert payload["extraction"]["request_type"] == "Collateral Substitution"
    assert any(evt["event_type"] == "cache_skip" for evt in payload["events"])
    assert not any(evt["event_type"] == "cache_hit" for evt in payload["events"])


def test_cache_hit_normalizes_relative_deadline_value_without_new_llm_call():
    marker = uuid4().hex
    sample_bytes = SAMPLE_TXT + f"\nDeadline cache marker: {marker}\n".encode("utf-8")

    with patch.dict(os.environ, {"ACOA_ENABLE_EVAL_CACHE": "1"}, clear=False):
        with patch("app.extractor._today", return_value=date(2026, 7, 27)):
            with patch("app.extractor.extract", return_value=dict(MOCK_EXTRACTION)) as mocked_extract:
                first = client.post("/api/files", files={"file": ("cached-deadline-1.txt", sample_bytes, "text/plain")})
                second = client.post("/api/files", files={"file": ("cached-deadline-2.txt", sample_bytes, "text/plain")})

    assert first.status_code == 200
    assert second.status_code == 200
    assert mocked_extract.call_count == 1

    payload = second.json()
    assert any(evt["event_type"] == "cache_hit" for evt in payload["events"])
    assert payload["extraction"]["entities"]["deadline"]["value"] == "28-July"
    assert payload["extraction"]["entities"]["deadline"]["evidence"] == "by tomorrow EOD"

def test_history_has_classification_and_lifecycle_timestamps():
    d = _upload()
    hist = client.get("/api/files").json()
    row = next(item for item in hist if item["id"] == d["id"])
    assert row["classification"] == "Collateral Substitution"
    assert row["received_at"] == row["uploaded_at"]
    assert row["approved_at"] is None
    assert row["clarification_requested_at"] is None

    client.post(f"/api/files/{d['id']}/approve", json={"note": "Validated and approved by ops."})
    hist2 = client.get("/api/files").json()
    row2 = next(item for item in hist2 if item["id"] == d["id"])
    assert row2["approved_at"] is not None

def test_detail_view_and_timeline():
    d = _upload()
    full = client.get(f"/api/files/{d['id']}").json()
    assert full["extraction"]["entities"]["isin_cusip"]["value"] == "US91282CJK92"
    assert [e["event_type"] for e in full["events"]][:3] == ["uploaded", "ai_extraction", "validation"]

def test_hitl_edit_and_approve():
    d = _upload()
    r = client.patch(f"/api/files/{d['id']}/entities", json={"entities": {"account": "ACC-778812"}})
    assert r.json()["extraction"]["entities"]["account"]["value"] == "ACC-778812"
    r = client.post(f"/api/files/{d['id']}/approve", json={"note": "Validated and approved by ops."})
    assert r.json()["status"] == "Approved"
    assert client.post(
        f"/api/files/{d['id']}/reject",
        json={"note": "Late validation outcome"},
    ).status_code == 409  # terminal

def test_missing_fields_can_still_be_approved():
    ext = dict(MOCK_EXTRACTION); ext["entities"] = dict(ext["entities"])
    ext["entities"]["amount"] = {"value": None, "confidence": 0}
    with patch("app.extractor.extract", return_value=ext):
        d = client.post("/api/files", files={"file": ("x.txt", SAMPLE_TXT, "text/plain")}).json()
    assert d["status"] == "Missing Mandatory Fields"
    with patch("app.extractor.draft_clarification", return_value="Subject: Info needed\n\nPlease confirm the amount."):
        r = client.post(
            f"/api/files/{d['id']}/ask-clarifications",
            json={"note": "Please request missing amount details"},
        )
    assert r.json()["status"] == "Awaiting Clarifications"
    assert "amount" in r.json()["clarification_draft"].lower()

    with patch("app.extractor.extract", return_value=ext):
        d2 = client.post("/api/files", files={"file": ("x2.txt", SAMPLE_TXT, "text/plain")}).json()
    assert client.post(
        f"/api/files/{d2['id']}/approve",
        json={"note": "Approving with mandatory override."},
    ).status_code == 200

def test_validation_isin_format():
    ext = dict(MOCK_EXTRACTION); ext["entities"] = dict(ext["entities"])
    ext["entities"]["isin_cusip"] = {"value": "BAD-ISIN", "confidence": 0.9}
    raw = email_parser.parse_txt(SAMPLE_TXT)
    result = validation.validate(raw, ext)
    assert result["status"] == "Low Confidence"
    assert any(c["name"] == "ISIN/CUSIP format" and not c["passed"] for c in result["checks"])

def test_unsupported_file_type():
    r = client.post("/api/files", files={"file": ("mail.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 400


def test_multi_request_email_is_split_into_cases():
    with patch("app.extractor.extract", return_value=dict(MOCK_MULTI_EXTRACTION)):
        r = client.post("/api/files", files={"file": ("multi.txt", SAMPLE_TXT, "text/plain")})
    assert r.status_code == 200
    payload = r.json()

    assert payload["extraction"]["multiple_requests_detected"] is True
    assert payload["extraction"]["request_count"] == 2
    assert len(payload["extraction"]["requests"]) == 2
    assert len(payload["validation"]["cases"]) == 2
    assert payload["status"] in {"Ready for Review", "Low Confidence", "Missing Mandatory Fields"}


def test_multi_case_edit_updates_requested_case_only():
    with patch("app.extractor.extract", return_value=dict(MOCK_MULTI_EXTRACTION)):
        d = client.post("/api/files", files={"file": ("multi-edit.txt", SAMPLE_TXT, "text/plain")}).json()

    before_case_0 = d["extraction"]["requests"][0]["entities"]["replacement_asset"]["value"]
    patch_res = client.patch(
        f"/api/files/{d['id']}/entities",
        json={"case_index": 1, "entities": {"replacement_asset": "UK Gilts Basket"}},
    )
    assert patch_res.status_code == 200
    after = patch_res.json()
    assert after["extraction"]["requests"][1]["entities"]["replacement_asset"]["value"] == "UK Gilts Basket"
    assert after["extraction"]["requests"][0]["entities"]["replacement_asset"]["value"] == before_case_0


def test_multi_case_request_level_actions_apply_to_all_cases():
    with patch("app.extractor.extract", return_value=dict(MOCK_MULTI_EXTRACTION)):
        ask_seed = client.post("/api/files", files={"file": ("multi-actions-ask.txt", SAMPLE_TXT, "text/plain")}).json()

    with patch("app.extractor.draft_clarification", return_value="Subject: Clarification\n\nPlease confirm value date."):
        ask_res = client.post(
            f"/api/files/{ask_seed['id']}/ask-clarifications",
            json={"note": "Value date is ambiguous"},
        )
    assert ask_res.status_code == 200
    asked = ask_res.json()
    assert asked["status"] == "Awaiting Clarifications"
    assert all(
        case["decision_status"] == "Awaiting Clarifications"
        for case in asked["extraction"]["requests"]
    )

    with patch("app.extractor.extract", return_value=dict(MOCK_MULTI_EXTRACTION)):
        approve_seed = client.post("/api/files", files={"file": ("multi-actions-approve.txt", SAMPLE_TXT, "text/plain")}).json()

    approve_res = client.post(
        f"/api/files/{approve_seed['id']}/approve",
        json={"note": "Approving all cases from one client email."},
    )
    assert approve_res.status_code == 200
    approved = approve_res.json()
    assert approved["status"] == "Approved"
    assert all(case["decision_status"] == "Approved" for case in approved["extraction"]["requests"])

    with patch("app.extractor.extract", return_value=dict(MOCK_MULTI_EXTRACTION)):
        reject_seed = client.post("/api/files", files={"file": ("multi-actions-reject.txt", SAMPLE_TXT, "text/plain")}).json()

    reject_res = client.post(
        f"/api/files/{reject_seed['id']}/reject",
        json={"note": "Rejecting full email due to instruction conflict."},
    )
    assert reject_res.status_code == 200
    rejected = reject_res.json()
    assert rejected["status"] == "Rejected"
    assert all(case["decision_status"] == "Rejected" for case in rejected["extraction"]["requests"])


def test_reject_and_ask_require_mandatory_notes():
    d = _upload()
    assert client.post(f"/api/files/{d['id']}/approve", json={"note": ""}).status_code == 400
    assert client.post(f"/api/files/{d['id']}/reject", json={"note": ""}).status_code == 400

    with patch("app.extractor.draft_clarification", return_value="Subject: Info needed\n\nPlease clarify."):
        assert client.post(
            f"/api/files/{d['id']}/ask-clarifications",
            json={"note": "Need account confirmation"},
        ).status_code == 200


def test_request_ask_clarifications_uses_fallback_draft_when_ai_unavailable():
    with patch("app.extractor.extract", return_value=dict(MOCK_EXTRACTION)):
        d = client.post("/api/files", files={"file": ("ask-fallback.txt", SAMPLE_TXT, "text/plain")}).json()

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
        res = client.post(
            f"/api/files/{d['id']}/ask-clarifications",
            json={"note": "Please confirm settlement date"},
        )

    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "Awaiting Clarifications"
    assert payload["clarification_draft"]
    assert "Subject:" in payload["clarification_draft"]
    assert "Collateral Operations Team" in payload["clarification_draft"]
    assert "Please confirm settlement date" in payload["clarification_draft"]


def test_case_ask_clarifications_uses_fallback_draft_when_ai_unavailable():
    with patch("app.extractor.extract", return_value=dict(MOCK_MULTI_EXTRACTION)):
        d = client.post("/api/files", files={"file": ("case-ask-fallback.txt", SAMPLE_TXT, "text/plain")}).json()

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
        res = client.post(
            f"/api/files/{d['id']}/cases/1/ask-clarifications",
            json={"note": "Please confirm value date"},
        )

    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "Awaiting Clarifications"
    case = payload["extraction"]["requests"][1]
    assert case["decision_status"] == "Awaiting Clarifications"
    assert "Subject:" in (case.get("clarification_draft") or "")
    assert "Please confirm value date" in (case.get("clarification_draft") or "")


def test_multi_case_approve_allows_missing_mandatory_with_override_flow():
    ext = dict(MOCK_MULTI_EXTRACTION)
    ext["requests"] = [dict(r) for r in MOCK_MULTI_EXTRACTION["requests"]]
    ext["requests"][0] = dict(ext["requests"][0])
    ext["requests"][0]["entities"] = dict(ext["requests"][0]["entities"])
    ext["requests"][0]["entities"]["amount"] = {"value": None, "confidence": 0}

    with patch("app.extractor.extract", return_value=ext):
        d = client.post("/api/files", files={"file": ("multi-missing-approve.txt", SAMPLE_TXT, "text/plain")}).json()

    before = client.get(f"/api/files/{d['id']}").json()
    case0_before = next(c for c in before["validation"]["cases"] if c["case_index"] == 0)
    assert case0_before["status"] == "Missing Mandatory Fields"

    r = client.post(
        f"/api/files/{d['id']}/cases/0/approve",
        json={"note": "Approving with mandatory override."},
    )
    assert r.status_code == 200
    after = r.json()
    assert after["extraction"]["requests"][0]["decision_status"] == "Approved"


def test_non_collateral_email_is_flagged_and_not_extracted():
    with patch("app.extractor.extract", return_value=dict(MOCK_NON_COLLATERAL_EXTRACTION)):
        r = client.post("/api/files", files={"file": ("general.txt", SAMPLE_TXT, "text/plain")})
    assert r.status_code == 200
    payload = r.json()

    assert payload["status"] == "Not a collateral request"
    assert payload["validation"]["status"] == "Not a collateral request"
    assert payload["extraction"]["request_count"] == 0
    assert payload["extraction"]["requests"] == []
    assert all(v.get("value") is None for v in payload["extraction"]["entities"].values())


def test_batch_run_moves_files_by_outcome():
    _clear_runtime_dirs()
    root = _runtime_root()
    inbox = root / "inbox"

    marker = uuid4().hex
    sample = (
        f"From: batch.user@client.com\n"
        f"To: collateral.ops@bank.com\n"
        f"Subject: Batch substitution {marker}\n\n"
        f"Please substitute USD 1,000,000 cash collateral with US Treasury Bond ISIN US91282CJK92."
    ).encode("utf-8")
    (inbox / "batch_ok_1.txt").write_bytes(sample)
    (inbox / "batch_ok_2_duplicate.txt").write_bytes(sample)
    (inbox / "batch_bad.pdf").write_bytes(b"%PDF")

    with patch("app.extractor.extract", return_value=dict(MOCK_EXTRACTION)):
        r = client.post("/api/batch/run-now")

    assert r.status_code == 200
    status = r.json()
    assert status["last_batch"]["processed"] == 1
    assert status["last_batch"]["duplicates"] == 1
    assert status["last_batch"]["failed"] == 1
    assert status["last_batch"]["total_scanned"] == 3

    assert any((root / "processed").iterdir())
    assert any((root / "duplicates").iterdir())
    assert any((root / "failed").iterdir())


def test_batch_start_and_stop_controls():
    r1 = client.post("/api/batch/start")
    assert r1.status_code == 200
    assert r1.json()["running"] is True

    r2 = client.post("/api/batch/stop")
    assert r2.status_code == 200
    assert r2.json()["running"] is False


def test_audit_and_config_endpoints():
    d = _upload()

    audit = client.get("/api/audit?limit=20")
    assert audit.status_code == 200
    events = audit.json()
    assert any(evt["request_id"] == d["id"] for evt in events)

    cfg = client.get("/api/config")
    assert cfg.status_code == 200
    payload = cfg.json()
    assert "llm_api_key_masked" in payload
    assert "batch_interval_seconds" in payload
    assert "batch_enabled" in payload
    assert "cache_entries" in payload

    update = client.put(
        "/api/config",
        json={"batch_interval_seconds": 45, "batch_enabled": False},
    )
    assert update.status_code == 200
    updated = update.json()
    assert updated["batch_interval_seconds"] == 45
    assert updated["batch_enabled"] is False


def test_admin_reset_clears_requests_events_cache_and_runtime_files():
    _clear_runtime_dirs()
    root = _runtime_root()

    (root / "inbox" / "reset_me.txt").write_bytes(SAMPLE_TXT)
    (root / "processed" / "processed.txt").write_bytes(b"done")

    with patch.dict(os.environ, {"ACOA_ENABLE_EVAL_CACHE": "1"}, clear=False):
        with patch("app.extractor.extract", return_value=dict(MOCK_EXTRACTION)):
            upload = client.post("/api/files", files={"file": ("reset-source.txt", SAMPLE_TXT, "text/plain")})
        assert upload.status_code == 200

        reset = client.post("/api/admin/reset-data")
    assert reset.status_code == 200
    body = reset.json()
    assert body["requests_deleted"] >= 1
    assert body["events_deleted"] >= 1
    assert body["cache_entries_deleted"] >= 1
    assert body["cache_entries_remaining"] == 0

    assert client.get("/api/files").json() == []
    assert client.get("/api/audit?limit=20").json() == []

    cfg = client.get("/api/config")
    assert cfg.status_code == 200
    assert cfg.json()["cache_entries"] == 0

    for folder_name in ("inbox", "processed", "failed", "duplicates"):
        folder = root / folder_name
        assert folder.exists()
        assert not any(folder.iterdir())


def test_history_summary_includes_latest_action_notes():
    d1 = _upload()
    with patch("app.extractor.draft_clarification", return_value="Subject: Info needed\n\nPlease clarify."):
        ask_res = client.post(
            f"/api/files/{d1['id']}/ask-clarifications",
            json={"note": "Need value date confirmation"},
        )
    assert ask_res.status_code == 200

    d2 = _upload()
    reject_res = client.post(
        f"/api/files/{d2['id']}/reject",
        json={"note": "Instruction conflicts with previous agreement"},
    )
    assert reject_res.status_code == 200

    d3 = _upload()
    approve_res = client.post(
        f"/api/files/{d3['id']}/approve",
        json={"note": "Settlement details verified and approved"},
    )
    assert approve_res.status_code == 200

    history = client.get("/api/files").json()
    row_ask = next(item for item in history if item["id"] == d1["id"])
    row_reject = next(item for item in history if item["id"] == d2["id"])
    row_approve = next(item for item in history if item["id"] == d3["id"])

    assert row_ask["latest_ask_customer_note"] == "Need value date confirmation"
    assert row_reject["latest_rejection_note"] == "Instruction conflicts with previous agreement"
    assert row_approve["latest_approval_note"] == "Settlement details verified and approved"


def test_amount_abbreviation_normalization_for_million_and_billion_variants():
    payload = {
        "collateral_request_detected": True,
        "multiple_requests_detected": True,
        "request_count": 2,
        "customer_tone": "Standard",
        "summary": "Two requests.",
        "requests": [
            {
                "request_type": "Collateral Substitution",
                "request_type_confidence": 0.9,
                "summary": "First",
                "entities": {
                    **_empty_entities(),
                    "amount": {"value": "USD 1.2 milion", "confidence": 0.9, "evidence": "USD 1.2 milion"},
                    "currency": {"value": "USD", "confidence": 0.9},
                    "collateral_type": {"value": "Cash", "confidence": 0.9},
                    "replacement_asset": {"value": "UST", "confidence": 0.9},
                },
            },
            {
                "request_type": "Collateral Substitution",
                "request_type_confidence": 0.9,
                "summary": "Second",
                "entities": {
                    **_empty_entities(),
                    "amount": {"value": "2.5 bilion", "confidence": 0.9, "evidence": "2.5 bilion"},
                    "currency": {"value": "USD", "confidence": 0.9},
                    "collateral_type": {"value": "Cash", "confidence": 0.9},
                    "replacement_asset": {"value": "UST", "confidence": 0.9},
                },
            },
        ],
        "ambiguities": [],
        "suggested_action": "Review",
    }

    normalized = extractor._normalize_extraction_result(payload)
    assert normalized["requests"][0]["entities"]["amount"]["value"] == "1200000"
    assert normalized["requests"][1]["entities"]["amount"]["value"] == "2500000000"


def test_value_date_relative_terms_normalize_to_concrete_calendar_dates():
    payload = {
        "collateral_request_detected": True,
        "multiple_requests_detected": True,
        "request_count": 3,
        "customer_tone": "Standard",
        "summary": "Three requests.",
        "requests": [
            {
                "request_type": "Settlement Instruction",
                "request_type_confidence": 0.9,
                "summary": "Same day settlement",
                "entities": {
                    **_empty_entities(),
                    "counterparty": {"value": "Acme", "confidence": 0.9},
                    "amount": {"value": "1000000", "confidence": 0.9},
                    "currency": {"value": "USD", "confidence": 0.9},
                    "value_date": {"value": "same day", "confidence": 0.8, "evidence": "same day"},
                },
            },
            {
                "request_type": "Settlement Instruction",
                "request_type_confidence": 0.9,
                "summary": "Tomorrow settlement",
                "entities": {
                    **_empty_entities(),
                    "counterparty": {"value": "Acme", "confidence": 0.9},
                    "amount": {"value": "1000000", "confidence": 0.9},
                    "currency": {"value": "USD", "confidence": 0.9},
                    "value_date": {"value": "tomorrow EOD", "confidence": 0.8, "evidence": "tomorrow EOD"},
                },
            },
            {
                "request_type": "Settlement Instruction",
                "request_type_confidence": 0.9,
                "summary": "T+2 settlement",
                "entities": {
                    **_empty_entities(),
                    "counterparty": {"value": "Acme", "confidence": 0.9},
                    "amount": {"value": "1000000", "confidence": 0.9},
                    "currency": {"value": "USD", "confidence": 0.9},
                    "value_date": {"value": "T+2", "confidence": 0.8, "evidence": "T+2"},
                },
            },
        ],
        "ambiguities": [],
        "suggested_action": "Review",
    }

    with patch("app.extractor._today", return_value=date(2026, 7, 27)):
        normalized = extractor._normalize_extraction_result(payload)

    assert normalized["requests"][0]["entities"]["value_date"]["value"] == "27-July"
    assert normalized["requests"][1]["entities"]["value_date"]["value"] == "28-July"
    assert normalized["requests"][2]["entities"]["value_date"]["value"] == "29-July"


def test_deadline_relative_terms_normalize_to_concrete_dates_and_keep_evidence_verbatim():
    payload = {
        "collateral_request_detected": True,
        "multiple_requests_detected": True,
        "request_count": 5,
        "customer_tone": "Standard",
        "summary": "Deadline timeline checks.",
        "requests": [
            {
                "request_type": "Collateral Substitution",
                "request_type_confidence": 0.9,
                "summary": "Today",
                "entities": {
                    **_empty_entities(),
                    "amount": {"value": "1000000", "confidence": 0.9},
                    "currency": {"value": "USD", "confidence": 0.9},
                    "collateral_type": {"value": "Cash", "confidence": 0.9},
                    "replacement_asset": {"value": "UST", "confidence": 0.9},
                    "deadline": {"value": "today", "confidence": 0.8, "evidence": "today"},
                },
            },
            {
                "request_type": "Collateral Substitution",
                "request_type_confidence": 0.9,
                "summary": "Tomorrow EOD",
                "entities": {
                    **_empty_entities(),
                    "amount": {"value": "1000000", "confidence": 0.9},
                    "currency": {"value": "USD", "confidence": 0.9},
                    "collateral_type": {"value": "Cash", "confidence": 0.9},
                    "replacement_asset": {"value": "UST", "confidence": 0.9},
                    "deadline": {"value": "Tomorrow EOD", "confidence": 0.8, "evidence": "by tomorrow EOD"},
                },
            },
            {
                "request_type": "Collateral Substitution",
                "request_type_confidence": 0.9,
                "summary": "Yesterday",
                "entities": {
                    **_empty_entities(),
                    "amount": {"value": "1000000", "confidence": 0.9},
                    "currency": {"value": "USD", "confidence": 0.9},
                    "collateral_type": {"value": "Cash", "confidence": 0.9},
                    "replacement_asset": {"value": "UST", "confidence": 0.9},
                    "deadline": {"value": "yesterday", "confidence": 0.8, "evidence": "as of yesterday"},
                },
            },
            {
                "request_type": "Collateral Substitution",
                "request_type_confidence": 0.9,
                "summary": "Day after tomorrow",
                "entities": {
                    **_empty_entities(),
                    "amount": {"value": "1000000", "confidence": 0.9},
                    "currency": {"value": "USD", "confidence": 0.9},
                    "collateral_type": {"value": "Cash", "confidence": 0.9},
                    "replacement_asset": {"value": "UST", "confidence": 0.9},
                    "deadline": {
                        "value": "day after tomorrow",
                        "confidence": 0.8,
                        "evidence": "day after tomorrow",
                    },
                },
            },
            {
                "request_type": "Collateral Substitution",
                "request_type_confidence": 0.9,
                "summary": "Next week same day",
                "entities": {
                    **_empty_entities(),
                    "amount": {"value": "1000000", "confidence": 0.9},
                    "currency": {"value": "USD", "confidence": 0.9},
                    "collateral_type": {"value": "Cash", "confidence": 0.9},
                    "replacement_asset": {"value": "UST", "confidence": 0.9},
                    "deadline": {
                        "value": "next week this day",
                        "confidence": 0.8,
                        "evidence": "next week this day",
                    },
                },
            },
        ],
        "ambiguities": [],
        "suggested_action": "Review",
    }

    with patch("app.extractor._today", return_value=date(2026, 7, 27)):
        normalized = extractor._normalize_extraction_result(payload)

    assert normalized["requests"][0]["entities"]["deadline"]["value"] == "27-July"
    assert normalized["requests"][1]["entities"]["deadline"]["value"] == "28-July"
    assert normalized["requests"][2]["entities"]["deadline"]["value"] == "26-July"
    assert normalized["requests"][3]["entities"]["deadline"]["value"] == "29-July"
    assert normalized["requests"][4]["entities"]["deadline"]["value"] == "3-August"

    assert normalized["requests"][1]["entities"]["deadline"]["evidence"] == "by tomorrow EOD"
    assert normalized["requests"][2]["entities"]["deadline"]["evidence"] == "as of yesterday"
    assert normalized["requests"][4]["entities"]["deadline"]["evidence"] == "next week this day"


def test_timeline_phrases_cover_next_week_last_week_and_week_end_variants():
    with patch("app.extractor._today", return_value=date(2026, 7, 27)):
        assert extractor._normalize_value_date_value("this day of next week") == "3-August"
        assert extractor._normalize_value_date_value("this day last week") == "20-July"
        assert extractor._normalize_value_date_value("end of current week") == "31-July"
        assert extractor._normalize_value_date_value("end of week") == "31-July"


def test_missing_deadline_and_relative_value_date_are_backfilled_from_context_language():
    payload = {
        "collateral_request_detected": True,
        "multiple_requests_detected": True,
        "request_count": 2,
        "customer_tone": "Standard",
        "summary": "Two requests.",
        "requests": [
            {
                "request_type": "Settlement Instruction",
                "request_type_confidence": 0.9,
                "summary": (
                    "Please settle on value date of this day last week. "
                    "This needs to happen this day of next week."
                ),
                "entities": {
                    **_empty_entities(),
                    "counterparty": {"value": "Nexus Investments", "confidence": 0.9},
                    "amount": {"value": "4000000", "confidence": 0.9},
                    "currency": {"value": "USD", "confidence": 0.9},
                    "instruction_details": {
                        "value": "Settle this leg this day of next week.",
                        "confidence": 0.8,
                        "evidence": "this day of next week",
                    },
                },
            },
            {
                "request_type": "Collateral Transfer",
                "request_type_confidence": 0.9,
                "summary": "Release cash collateral and close by end of current week.",
                "entities": {
                    **_empty_entities(),
                    "counterparty": {"value": "Nexus Investments", "confidence": 0.9},
                    "amount": {"value": "2500000", "confidence": 0.9},
                    "currency": {"value": "USD", "confidence": 0.9},
                },
            },
        ],
        "ambiguities": [],
        "suggested_action": "Review",
    }

    with patch("app.extractor._today", return_value=date(2026, 7, 27)):
        normalized = extractor._normalize_extraction_result(payload)

    first = normalized["requests"][0]["entities"]
    second = normalized["requests"][1]["entities"]

    assert first["value_date"]["value"] == "20-July"
    assert first["value_date"]["evidence"] == "this day last week"
    assert first["deadline"]["value"] == "3-August"
    assert first["deadline"]["evidence"] == "this day of next week"
    assert second["deadline"]["value"] == "31-July"
    assert second["deadline"]["evidence"] == "end of current week"


def test_non_date_phrase_is_not_written_as_value_date_or_deadline():
    payload = {
        "collateral_request_detected": True,
        "multiple_requests_detected": False,
        "request_count": 1,
        "customer_tone": "Standard",
        "summary": (
            "Client is requesting release of USD 5,000,000 in US Government Treasury Bonds, "
            "identified by Collateral ID XYZ12345. No account, counterparty, value date, "
            "deadline, or agreement reference details were provided."
        ),
        "requests": [
            {
                "request_type": "Collateral Transfer",
                "request_type_confidence": 0.9,
                "summary": (
                    "No account, counterparty, value date, deadline, "
                    "or agreement reference details were provided."
                ),
                "entities": {
                    **_empty_entities(),
                    "amount": {"value": "5000000", "confidence": 0.9, "evidence": "USD 5 Mil"},
                    "currency": {"value": "USD", "confidence": 0.9, "evidence": "USD 5 Mil"},
                    "instruction_details": {
                        "value": "Please release my collateral T Bonds of US Govt for USD 5 Mil",
                        "confidence": 0.8,
                        "evidence": "Please release my collateral T Bonds of US Govt for USD 5 Mil",
                    },
                },
            }
        ],
        "ambiguities": [],
        "suggested_action": "Review",
    }

    normalized = extractor._normalize_extraction_result(payload)
    entities = normalized["requests"][0]["entities"]
    assert entities["value_date"]["value"] is None
    assert entities["deadline"]["value"] is None


def test_short_release_email_without_timeline_keeps_value_date_non_extracted():
    sample = b"""Hi Collateral Team
Hope you had a nice weekend
Please release my collateral T Bonds of US Govt for USD 5 Mil
Collateral Id XYZ12345
"""

    raw = email_parser.parse_txt(sample)
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
        extracted = extractor.extract(raw)

    entities = extracted["entities"]
    assert entities["value_date"]["value"] is None
    assert entities["deadline"]["value"] is None


def test_extractor_fallback_handles_missing_api_key_for_pasted_substitution_email():
    pasted_body = b"""Dear Team,
We would like to substitute the USD 4,000,000 cash collateral currently posted under account NEX-CSA-8854 with US Treasury Notes.
Details:
Current collateral: USD 4,000,000 cash
Substitute collateral: US Treasury Notes
ISIN: US91282CJZ59
Nominal Amount: USD 4,000,000
Requested Value Date: 15 July 2026
Please process on a DvP basis and advise if any additional documentation is required.
"""

    raw = email_parser.parse_txt(pasted_body)
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
        extracted = extractor.extract(raw)

    assert extracted["request_type"] == "Collateral Substitution"
    assert extracted["entities"]["account"]["value"] == "NEX-CSA-8854"
    assert extracted["entities"]["amount"]["value"] == "4000000"
    assert extracted["entities"]["currency"]["value"] == "USD"
    assert extracted["entities"]["value_date"]["value"] == "15-July"
    assert extracted["entities"]["replacement_asset"]["value"] == "US Treasury Notes"
    assert extracted["entities"]["amount"]["confidence"] >= 0.95
    assert extracted["entities"]["currency"]["confidence"] >= 0.95
    assert extracted["entities"]["value_date"]["confidence"] >= 0.95
    assert extracted["entities"]["isin_cusip"]["confidence"] >= 0.95
    assert extracted["request_type_confidence"] >= 0.9

    validated = validation.validate(raw, extracted)
    assert validated["status"] in {"Ready for Review", "Low Confidence", "Missing Mandatory Fields"}


def test_extractor_recovers_when_model_returns_collateral_summary_without_cases():
    sample = b"""Subject: Settlement Instructions for Account NEX-CSA-8854

Dear Team,

Following our latest collateral review, please arrange for US Treasury Notes (ISIN: US91282CJZ59) with a nominal amount of USD 4,000,000 to be delivered against our obligations under account NEX-CSA-8854. We would like the transaction to settle on a DvP basis with a value date of 15 July 2026.

At the same time, as our exposure has reduced, the USD 2,500,000 cash collateral currently held under the same agreement is no longer required. Please arrange for this amount to be released and returned to our standard settlement account on the same value date, where possible.

Please confirm once the instructions have been booked and let us know if any further documentation or approvals are required.

Kind regards,
Linda Garcia
Collateral Management
Nexus Investments
"""

    malformed_tool_output = {
        "collateral_request_detected": True,
        "multiple_requests_detected": False,
        "request_count": 1,
        "customer_tone": "Standard",
        "summary": "Two collateral instructions were identified.",
        "requests": [],
        "ambiguities": [],
        "suggested_action": "",
    }

    class _FakeBlock:
        type = "tool_use"

        def __init__(self, payload):
            self.input = payload

    class _FakeResponse:
        def __init__(self, payload):
            self.content = [_FakeBlock(payload)]

    class _FakeMessages:
        def __init__(self, payload):
            self._payload = payload

        def create(self, **kwargs):
            return _FakeResponse(self._payload)

    class _FakeClient:
        def __init__(self, payload):
            self.messages = _FakeMessages(payload)

    raw = email_parser.parse_txt(sample)
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=False):
        with patch("app.extractor._new_anthropic_client", return_value=_FakeClient(malformed_tool_output)):
            extracted = extractor.extract(raw)

    assert extracted["collateral_request_detected"] is True
    assert extracted["request_count"] >= 1
    assert extracted["overall_confidence"] > 0
    assert any(case["entities"]["amount"]["value"] for case in extracted["requests"])
    assert extracted["request_type"] != "General Inquiry"

    validated = validation.validate(raw, extracted)
    assert validated["status"] != "Low Confidence"
