"""SQLite persistence layer.

Two tables:
  requests — one row per uploaded email file, holding raw email, AI extraction,
             deterministic validation results and lifecycle status.
  events   — append-only activity timeline per request (audit trail).
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

DB_PATH = os.environ.get("ACOA_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "acoa.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    status TEXT NOT NULL,
    approved_at TEXT,
    rejected_at TEXT,
    clarification_requested_at TEXT,
    raw_email TEXT,
    extraction TEXT,
    validation TEXT,
    clarification_draft TEXT,
    error TEXT,
    content_hash TEXT,
    source_mode TEXT,
    source_path TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT,
    FOREIGN KEY (request_id) REFERENCES requests (id)
);
"""

REQUEST_OPTIONAL_COLUMNS = {
    "approved_at": "TEXT",
    "rejected_at": "TEXT",
    "clarification_requested_at": "TEXT",
    "content_hash": "TEXT",
    "source_mode": "TEXT",
    "source_path": "TEXT",
}


def _ensure_request_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(requests)").fetchall()
    }
    for col, col_type in REQUEST_OPTIONAL_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE requests ADD COLUMN {col} {col_type}")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _ensure_request_columns(conn)
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_request(
    filename: str,
    file_type: str,
    raw_email: dict,
    content_hash: str | None = None,
    source_mode: str = "upload",
    source_path: str | None = None,
) -> str:
    req_id = uuid.uuid4().hex[:12]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO requests (id, filename, file_type, uploaded_at, status, raw_email, content_hash, source_mode, source_path) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                req_id,
                filename,
                file_type,
                now_iso(),
                "Processing",
                json.dumps(raw_email),
                content_hash,
                source_mode,
                source_path,
            ),
        )
    add_event(req_id, "uploaded", f"File '{filename}' received and parsed")
    return req_id


def update_request(req_id: str, **fields):
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k} = ?")
        vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
    vals.append(req_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE requests SET {', '.join(cols)} WHERE id = ?", vals)


def add_event(req_id: str, event_type: str, detail: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO events (request_id, ts, event_type, detail) VALUES (?,?,?,?)",
            (req_id, now_iso(), event_type, detail),
        )


def has_content_hash(content_hash: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM requests WHERE content_hash = ? LIMIT 1",
            (content_hash,),
        ).fetchone()
    return bool(row)


def _row_to_dict(row: sqlite3.Row, full: bool = True) -> dict:
    d = dict(row)
    for key in ("raw_email", "extraction", "validation"):
        if key in d and d[key]:
            d[key] = json.loads(d[key])
    if not full:
        # summary view for the history list
        ext = d.get("extraction") or {}
        actioned_at = d.get("approved_at") or d.get("rejected_at") or d.get("clarification_requested_at")
        return {
            "id": d["id"],
            "filename": d["filename"],
            "file_type": d["file_type"],
            "uploaded_at": d["uploaded_at"],
            "received_at": d["uploaded_at"],
            "status": d["status"],
            "classification": ext.get("request_type"),
            "request_type": ext.get("request_type"),
            "subject": (d.get("raw_email") or {}).get("subject"),
            "overall_confidence": ext.get("overall_confidence"),
            "approved_at": d.get("approved_at"),
            "rejected_at": d.get("rejected_at"),
            "clarification_requested_at": d.get("clarification_requested_at"),
            "actioned_at": actioned_at,
            "source_mode": d.get("source_mode"),
        }
    return d


def _extract_note_from_detail(detail: str | None) -> str | None:
    if not detail:
        return None
    marker = "Note:"
    idx = detail.find(marker)
    if idx == -1:
        return None
    note = detail[idx + len(marker):].strip()
    return note or None


def list_requests() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM requests ORDER BY uploaded_at DESC").fetchall()
        summaries = [_row_to_dict(r, full=False) for r in rows]

        if not summaries:
            return summaries

        for item in summaries:
            item["latest_rejection_note"] = None
            item["latest_ask_customer_note"] = None

        req_ids = [item["id"] for item in summaries]
        placeholders = ",".join("?" for _ in req_ids)
        event_rows = conn.execute(
            f"""
            SELECT request_id, event_type, detail, id
            FROM events
            WHERE request_id IN ({placeholders})
              AND event_type IN (
                'rejected',
                'case_rejected',
                'clarification_drafted',
                'case_clarification_drafted'
              )
            ORDER BY id ASC
            """,
            req_ids,
        ).fetchall()

    by_id = {item["id"]: item for item in summaries}
    for row in event_rows:
        item = by_id.get(row["request_id"])
        if not item:
            continue

        note = _extract_note_from_detail(row["detail"])
        if not note:
            continue

        if row["event_type"] in ("rejected", "case_rejected"):
            item["latest_rejection_note"] = note
        else:
            item["latest_ask_customer_note"] = note

    return summaries


def get_request(req_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM requests WHERE id = ?", (req_id,)).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        events = conn.execute(
            "SELECT ts, event_type, detail FROM events WHERE request_id = ? ORDER BY id ASC", (req_id,)
        ).fetchall()
    d["events"] = [dict(e) for e in events]
    return d


def list_audit_events(limit: int = 1000) -> list[dict]:
    limit = max(1, min(int(limit), 5000))
    query = """
    SELECT
        e.id,
        e.ts,
        e.event_type,
        e.detail,
        r.id AS request_id,
        r.filename,
        r.status,
        r.source_mode
    FROM events e
    JOIN requests r ON r.id = e.request_id
    ORDER BY e.id DESC
    LIMIT ?
    """
    with get_conn() as conn:
        rows = conn.execute(query, (limit,)).fetchall()
    return [dict(row) for row in rows]
