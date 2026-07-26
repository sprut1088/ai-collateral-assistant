export interface FieldValue {
  value: string | null;
  confidence: number;
  evidence?: string | null;
}

export interface ExtractionCase {
  request_type: string;
  request_type_confidence: number;
  summary: string;
  entities: Record<string, FieldValue>;
  ambiguities?: string[];
  suggested_action?: string;
  overall_confidence: number;
  decision_status?: string;
  clarification_draft?: string | null;
}

export interface Extraction {
  collateral_request_detected?: boolean;
  multiple_requests_detected?: boolean;
  request_count?: number;
  requests?: ExtractionCase[];
  request_type: string;
  request_type_confidence: number;
  customer_tone: string;
  summary: string;
  entities: Record<string, FieldValue>;
  ambiguities?: string[];
  suggested_action: string;
  overall_confidence: number;
}

export interface Check {
  name: string;
  passed: boolean;
  detail: string;
}

export interface ValidationCase {
  case_index: number;
  request_type: string;
  status: string;
  checks: Check[];
  missing_fields: string[];
  overall_confidence: number;
  summary?: string;
}

export interface Validation {
  status: string;
  checks: Check[];
  missing_fields: string[];
  confidence_threshold: number;
  cases?: ValidationCase[];
  multiple_requests_detected?: boolean;
}

export interface RawEmail {
  sender: string | null;
  recipients: string | null;
  subject: string | null;
  sent_date: string | null;
  body: string;
  attachments: string[];
}

export interface RequestSummary {
  id: string;
  filename: string;
  file_type: string;
  uploaded_at: string;
  received_at?: string;
  status: string;
  classification?: string | null;
  request_type: string | null;
  subject: string | null;
  overall_confidence: number | null;
  approved_at?: string | null;
  rejected_at?: string | null;
  clarification_requested_at?: string | null;
  actioned_at?: string | null;
  source_mode?: string | null;
  latest_rejection_note?: string | null;
  latest_ask_customer_note?: string | null;
}

export interface BatchStatus {
  running: boolean;
  interval_seconds: number;
  runtime_root: string;
  inbox_dir: string;
  processed_dir: string;
  failed_dir: string;
  duplicates_dir: string;
  inbox_file_count: number;
  last_run_at: string | null;
  last_error: string | null;
  last_batch: {
    processed: number;
    failed: number;
    duplicates: number;
    total_scanned: number;
  };
  totals: {
    processed: number;
    failed: number;
    duplicates: number;
  };
}

export interface AuditEvent {
  id: number;
  ts: string;
  event_type: string;
  detail: string;
  request_id: string;
  filename: string;
  status: string;
  source_mode: string | null;
}

export interface AppConfig {
  llm_model: string;
  llm_api_key_masked: string;
  api_key_configured: boolean;
  batch_interval_seconds: number;
  batch_enabled: boolean;
  batch_runtime_root: string;
  truststore_enabled: boolean;
}

export interface ConfigUpdate {
  llm_model?: string;
  anthropic_api_key?: string;
  batch_interval_seconds?: number;
  batch_enabled?: boolean;
  use_truststore?: boolean;
}

export interface RequestDetail extends RequestSummary {
  raw_email: RawEmail;
  extraction: Extraction | null;
  validation: Validation | null;
  clarification_draft: string | null;
  error: string | null;
  events: { ts: string; event_type: string; detail: string }[];
}

const BASE = "/api";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || "Request failed");
  }
  return res.json();
}

export const api = {
  listFiles: () => fetch(`${BASE}/files`).then((r) => handle<RequestSummary[]>(r)),
  getFile: (id: string) => fetch(`${BASE}/files/${id}`).then((r) => handle<RequestDetail>(r)),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return fetch(`${BASE}/files`, { method: "POST", body: form }).then((r) =>
      handle<RequestDetail>(r)
    );
  },
  editEntities: (id: string, entities: Record<string, string>, caseIndex?: number) =>
    fetch(`${BASE}/files/${id}/entities`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entities, case_index: caseIndex }),
    }).then((r) => handle<RequestDetail>(r)),
  approve: (id: string) =>
    fetch(`${BASE}/files/${id}/approve`, { method: "POST" }).then((r) => handle<RequestDetail>(r)),
  reject: (id: string, note: string) =>
    fetch(`${BASE}/files/${id}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    }).then((r) => handle<RequestDetail>(r)),
  askCustomer: (id: string, note: string) =>
    fetch(`${BASE}/files/${id}/ask-customer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    }).then((r) =>
      handle<RequestDetail>(r)
    ),
  approveCase: (id: string, caseIndex: number) =>
    fetch(`${BASE}/files/${id}/cases/${caseIndex}/approve`, { method: "POST" }).then((r) =>
      handle<RequestDetail>(r)
    ),
  rejectCase: (id: string, caseIndex: number, note: string) =>
    fetch(`${BASE}/files/${id}/cases/${caseIndex}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    }).then((r) =>
      handle<RequestDetail>(r)
    ),
  askCustomerCase: (id: string, caseIndex: number, note: string) =>
    fetch(`${BASE}/files/${id}/cases/${caseIndex}/ask-customer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note }),
    }).then((r) =>
      handle<RequestDetail>(r)
    ),
  batchStatus: () => fetch(`${BASE}/batch/status`).then((r) => handle<BatchStatus>(r)),
  batchStart: () => fetch(`${BASE}/batch/start`, { method: "POST" }).then((r) => handle<BatchStatus>(r)),
  batchStop: () => fetch(`${BASE}/batch/stop`, { method: "POST" }).then((r) => handle<BatchStatus>(r)),
  batchRunNow: () => fetch(`${BASE}/batch/run-now`, { method: "POST" }).then((r) => handle<BatchStatus>(r)),
  listAudit: (limit = 1000) => fetch(`${BASE}/audit?limit=${limit}`).then((r) => handle<AuditEvent[]>(r)),
  getConfig: () => fetch(`${BASE}/config`).then((r) => handle<AppConfig>(r)),
  updateConfig: (payload: ConfigUpdate) =>
    fetch(`${BASE}/config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then((r) => handle<AppConfig>(r)),
};
