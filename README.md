# AI Collateral Operations Assistant — Email Intake System

An AI add-on layer for collateral operations, designed for future integration with **Colline**. Converts unstructured client emails (`.txt` / `.msg`) into structured, validated, evidence-linked collateral requests with full human-in-the-loop control.

**Design invariant:** the LLM classifies, extracts and narrates — the deterministic engine validates and decides routing. Operations approves. Colline remains the system of record.

## What it does

1. **Upload** a `.txt` or `.msg` email file (drag & drop or file picker).
	- or run **Batch mode** (every 30s) from `C:\git_repos\ai-collateral-assistant\samples\inbox`
	- successful files move to `...\runtime\processed`
	- failed files move to `...\runtime\failed`
	- duplicate files move to `...\runtime\duplicates`
2. **Parse** — sender, recipients, subject, date, body, attachment names (`.msg` via extract-msg, `.txt` with header detection).
3. **AI extraction** — a real Anthropic Claude API call with forced tool-calling returns schema-compliant JSON: request type (Margin Call, Substitution, Transfer, Settlement, Dispute, Inquiry), customer tone, summary, suggested action, and 11 entity fields (counterparty, account, amount, currency, value date, deadline, ISIN/CUSIP, collateral type, replacement asset, agreement reference, instruction details) — each with a confidence score and a verbatim evidence snippet.
4. **Deterministic validation** — mandatory fields per request type, ISIN/currency/amount format checks, confidence threshold (70%). Routes to a status: `Ready for Review`, `Missing Mandatory Fields`, or `Low Confidence`.
5. **File history** — every processed file appears in the left panel with type, status and timestamp; click any file to open its structured data.
6. **HITL review** — Approve (blocked while mandatory fields are missing), Edit fields (human-confirmed values get 100% confidence, validation re-runs), Ask Customer (Claude drafts a clarification email listing exactly what's missing), Reject. Every action lands on the audit timeline.

## Running it

### Windows (PowerShell recommended)

Use two terminals.

Terminal 1 — backend (FastAPI on :8000):

```powershell
cd backend

# 1) Create backend/.env from backend/.env.example (first run only)
Copy-Item .env.example .env

# 2) Set ANTHROPIC_API_KEY in backend/.env
# Optional for corporate TLS proxies: set ACOA_USE_TRUSTSTORE=1 in backend/.env

py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m uvicorn app.main:app --reload --port 8000
```

Terminal 2 — frontend (Vite dev server on :5173, proxies /api to :8000):

```powershell
cd frontend
npm install
npm run dev
```

### Windows (Command Prompt / cmd.exe)

```bat
cd backend
copy .env.example .env
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m uvicorn app.main:app --reload --port 8000
```

In another cmd window:

```bat
cd frontend
npm install
npm run dev
```

### Git Bash / WSL alternative

If you are running Git Bash or WSL, you can use the helper scripts:

```bash
./start_backend.sh
./start_frontend.sh
```

Open http://localhost:5173 and upload one of the files in `samples/`:

| Sample | Demonstrates |
|---|---|
| `01_collateral_substitution.txt` | Clean extraction → Ready for Review → Approve |
| `02_margin_call_dispute.txt` | Dispute classification + escalation tone detection |
| `03_incomplete_transfer.txt` | Missing mandatory fields → Ask Customer clarification draft |

Batch runtime folders are created automatically if missing.

## Configuration

Create `backend/.env` (ignored by git) from `backend/.env.example` and set values there.

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — (required) | Claude API key |
| `ACOA_MODEL` | `claude-sonnet-4-6` | Model used for extraction and drafting |
| `ACOA_USE_TRUSTSTORE` | off | Set `1` to inject OS trust store (corporate TLS proxies) |
| `ACOA_DB_PATH` | `backend/acoa.db` | SQLite location |
| `ACOA_BATCH_RUNTIME_ROOT` | `C:\git_repos\ai-collateral-assistant\samples` | Batch polling root (`inbox`, `processed`, `failed`, `duplicates`) |
| `ACOA_BATCH_INTERVAL_SECONDS` | `30` | Batch polling interval |

## Tests

```bash
cd backend && python -m pytest tests/ -v
```

7 tests, Claude call mocked (CI-safe): parsing, upload + history, detail + timeline, HITL edit + approve, missing-fields gate + clarification flow, ISIN format validation, unsupported file rejection.

## API surface

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/files` | Upload + process an email file |
| GET | `/api/files` | Processed file history |
| GET | `/api/files/{id}` | Full structured detail + timeline |
| PATCH | `/api/files/{id}/entities` | HITL field edits (re-validates) |
| POST | `/api/files/{id}/approve` | Approve for Colline submission (draft mode) |
| POST | `/api/files/{id}/reject` | Reject |
| POST | `/api/files/{id}/ask-customer` | Draft clarification email |
| GET | `/api/batch/status` | Batch runner status + folder stats |
| POST | `/api/batch/start` | Start 30-second inbox polling |
| POST | `/api/batch/stop` | Stop batch polling |
| POST | `/api/batch/run-now` | Run one immediate inbox scan |

## Colline integration path (next phase)

The approve action currently marks the structured request as ready in **draft-only mode** — the recommended PoC posture. The integration layer slots in behind `/approve`: API write-back, file-based interface, or RPA-assisted entry, per the phased options in the solution deck. Sender authorization and reference-data validation (counterparty/account/collateral lookups against Colline) are stubbed as the "Sender identified" check today and are the natural place to wire a Colline read connection.
