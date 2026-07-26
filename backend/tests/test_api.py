"""API tests with the Claude call mocked (deterministic CI-safe suite)."""
import os, tempfile
from pathlib import Path
from uuid import uuid4
os.environ["ACOA_DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["ACOA_BATCH_RUNTIME_ROOT"] = tempfile.mkdtemp(prefix="acoa-runtime-")

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

def test_history_has_classification_and_lifecycle_timestamps():
    d = _upload()
    hist = client.get("/api/files").json()
    row = next(item for item in hist if item["id"] == d["id"])
    assert row["classification"] == "Collateral Substitution"
    assert row["received_at"] == row["uploaded_at"]
    assert row["approved_at"] is None
    assert row["clarification_requested_at"] is None

    client.post(f"/api/files/{d['id']}/approve")
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
    r = client.post(f"/api/files/{d['id']}/approve")
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
            f"/api/files/{d['id']}/ask-customer",
            json={"note": "Please request missing amount details"},
        )
    assert r.json()["status"] == "Awaiting Customer"
    assert "amount" in r.json()["clarification_draft"].lower()

    with patch("app.extractor.extract", return_value=ext):
        d2 = client.post("/api/files", files={"file": ("x2.txt", SAMPLE_TXT, "text/plain")}).json()
    assert client.post(f"/api/files/{d2['id']}/approve").status_code == 200

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


def test_multi_case_actions_are_per_case_and_request_level_approve_is_blocked():
    with patch("app.extractor.extract", return_value=dict(MOCK_MULTI_EXTRACTION)):
        d = client.post("/api/files", files={"file": ("multi-actions.txt", SAMPLE_TXT, "text/plain")}).json()

    blocked = client.post(f"/api/files/{d['id']}/approve")
    assert blocked.status_code == 409

    with patch("app.extractor.draft_clarification", return_value="Subject: Clarification\n\nPlease confirm value date."):
        ask_res = client.post(
            f"/api/files/{d['id']}/cases/1/ask-customer",
            json={"note": "Value date is ambiguous"},
        )
    assert ask_res.status_code == 200
    asked = ask_res.json()
    assert asked["extraction"]["requests"][1]["decision_status"] == "Awaiting Customer"
    assert asked["status"] == "Awaiting Customer"

    approve_case0 = client.post(f"/api/files/{d['id']}/cases/0/approve")
    assert approve_case0.status_code == 200
    approved0 = approve_case0.json()
    assert approved0["extraction"]["requests"][0]["decision_status"] == "Approved"

    reject_case1 = client.post(
        f"/api/files/{d['id']}/cases/1/reject",
        json={"note": "Client instruction conflict across lines"},
    )
    assert reject_case1.status_code == 200
    rejected1 = reject_case1.json()
    assert rejected1["extraction"]["requests"][1]["decision_status"] == "Rejected"


def test_reject_and_ask_require_mandatory_notes():
    d = _upload()
    assert client.post(f"/api/files/{d['id']}/reject", json={"note": ""}).status_code == 400

    with patch("app.extractor.draft_clarification", return_value="Subject: Info needed\n\nPlease clarify."):
        assert client.post(
            f"/api/files/{d['id']}/ask-customer",
            json={"note": "Need account confirmation"},
        ).status_code == 200


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

    r = client.post(f"/api/files/{d['id']}/cases/0/approve")
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

    update = client.put(
        "/api/config",
        json={"batch_interval_seconds": 45, "batch_enabled": False},
    )
    assert update.status_code == 200
    updated = update.json()
    assert updated["batch_interval_seconds"] == 45
    assert updated["batch_enabled"] is False


def test_history_summary_includes_latest_action_notes():
    d1 = _upload()
    with patch("app.extractor.draft_clarification", return_value="Subject: Info needed\n\nPlease clarify."):
        ask_res = client.post(
            f"/api/files/{d1['id']}/ask-customer",
            json={"note": "Need value date confirmation"},
        )
    assert ask_res.status_code == 200

    d2 = _upload()
    reject_res = client.post(
        f"/api/files/{d2['id']}/reject",
        json={"note": "Instruction conflicts with previous agreement"},
    )
    assert reject_res.status_code == 200

    history = client.get("/api/files").json()
    row_ask = next(item for item in history if item["id"] == d1["id"])
    row_reject = next(item for item in history if item["id"] == d2["id"])

    assert row_ask["latest_ask_customer_note"] == "Need value date confirmation"
    assert row_reject["latest_rejection_note"] == "Instruction conflicts with previous agreement"


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
