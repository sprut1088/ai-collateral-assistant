import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  AppConfig,
  AuditEvent,
  BatchStatus,
  ExtractionCase,
  ResetDataResult,
  RequestDetail,
  RequestSummary,
} from "./api";
import { formatAmountDisplay } from "./amountFormat";
import { DetailView } from "./components/DetailView";

const PILL_CLASS: Record<string, string> = {
  "Ready for Review": "ready",
  Approved: "approved",
  "Missing Mandatory Fields": "missing",
  "Awaiting Clarifications": "awaiting",
  "Awaiting Customer": "awaiting",
  "Low Confidence": "lowconf",
  Rejected: "rejected",
  "Not a collateral request": "notcollateral",
  "Processing Failed": "failed",
  Processing: "processing",
};

const QUEUE_STATUSES = new Set([
  "Ready for Review",
  "Missing Mandatory Fields",
  "Low Confidence",
  "Processing Failed",
  "Processing",
]);

const ALL_CLASSIFICATIONS = "All classifications";
const MAX_TEXT_CHARS = 5000;
const STATUS_PIE_COLORS = [
  "#1f78d4",
  "#16a270",
  "#df8b07",
  "#cc4a54",
  "#6e7f95",
  "#6e58c2",
  "#2ca8a8",
  "#d06414",
];
const CLASSIFICATION_PIE_COLORS = [
  "#0f5baf",
  "#39a089",
  "#a56a06",
  "#ad3c64",
  "#5f7f9a",
  "#2e8fce",
  "#5b60cf",
  "#2c9982",
  "#c45324",
  "#8960b0",
];

const EXTRACTION_PROGRESS_STEPS = [
  {
    title: "Parsing inbound email envelope",
    detail:
      "Backend is reading sender, recipients, subject, body, and attachment metadata from the selected input.",
  },
  {
    title: "Classifying collateral request intent",
    detail:
      "AI layer is identifying request type, tone, and operational summary candidates.",
  },
  {
    title: "Extracting structured entities with evidence",
    detail:
      "Entity fields and confidence scores are being extracted with traceable evidence snippets.",
  },
  {
    title: "Normalizing operational values",
    detail:
      "Amounts and relative value dates are converted into canonical values for downstream validation.",
  },
  {
    title: "Running deterministic controls",
    detail:
      "Mandatory field checks, confidence thresholds, and rule-based routing are being applied.",
  },
  {
    title: "Persisting request and timeline",
    detail:
      "Validated extraction output is being stored and the operational queue is being refreshed.",
  },
];
const EXTRACTION_PROGRESS_MAX_VISIBLE = 98;

const INLINE_FIELD_LABELS: Record<string, string> = {
  counterparty: "Counterparty",
  account: "Account",
  amount: "Amount",
  currency: "Currency",
  value_date: "Value date",
  deadline: "Deadline",
  isin_cusip: "ISIN / CUSIP",
  collateral_type: "Existing collateral",
  replacement_asset: "Replacement asset",
  agreement_reference: "Agreement reference",
};

type IngestMode = "upload" | "text" | "batch";
type ViewKey =
  | "extraction"
  | "dashboard"
  | "queue"
  | "all"
  | "ask"
  | "approved"
  | "rejected"
  | "reports"
  | "audit"
  | "config";

type ThemeMode = "light" | "dark";

type PieSlice = {
  label: string;
  count: number;
  color: string;
  percent: number;
  startPercent: number;
  endPercent: number;
};

type PieChartData = {
  total: number;
  gradient: string;
  slices: PieSlice[];
};

type NoteColumnConfig = {
  header: string;
  field: "latest_ask_customer_note" | "latest_rejection_note" | "latest_approval_note";
};

type TableExpandMode = "inline" | "review-open";

type TableSortDirection = "asc" | "desc";
type TableSortField =
  | "filename"
  | "requestId"
  | "classification"
  | "received"
  | "confidence"
  | "source"
  | "note";

type TableSortState = {
  field: TableSortField;
  direction: TableSortDirection;
};

type TablePaginationState = {
  page: number;
  pageSize: number;
};

const SORTABLE_VIEWS = new Set<ViewKey>(["queue", "all", "approved", "rejected"]);
const TABLE_PAGE_SIZE_OPTIONS = [5, 10, 20, 50];
const DEFAULT_TABLE_SORT: TableSortState = { field: "received", direction: "desc" };
const DEFAULT_TABLE_PAGINATION: TablePaginationState = { page: 1, pageSize: 10 };

function classificationOf(item: RequestSummary): string {
  return item.classification || item.request_type || "Unclassified";
}

function normalizeStatusLabel(status: string): string {
  return status === "Awaiting Customer" ? "Awaiting Clarifications" : status;
}

function extractNoteFromEventDetail(detail: string | undefined): string | null {
  if (!detail) return null;
  const marker = "Note:";
  const idx = detail.indexOf(marker);
  if (idx === -1) return null;
  const note = detail.slice(idx + marker.length).trim();
  return note || null;
}

function latestNoteByEventType(detail: RequestDetail, eventTypes: string[]): string | null {
  const events = Array.isArray(detail.events) ? detail.events : [];
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const evt = events[i];
    if (!eventTypes.includes(evt.event_type)) continue;
    const note = extractNoteFromEventDetail(evt.detail);
    if (note) return note;
  }
  return null;
}

function noteForExpandedStatus(detail: RequestDetail): string | null {
  const status = normalizeStatusLabel(detail.status);
  if (status === "Awaiting Clarifications") {
    return latestNoteByEventType(detail, ["clarification_drafted", "case_clarification_drafted"]);
  }
  if (status === "Rejected") {
    return latestNoteByEventType(detail, ["rejected", "case_rejected"]);
  }
  if (status === "Approved") {
    return latestNoteByEventType(detail, ["approved", "case_approved"]);
  }
  return null;
}

function formatDateTime(ts?: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleString();
}

function formatConfidence(score?: number | null): string {
  if (score == null) return "-";
  return `${Math.round(score * 100)}%`;
}

function confidenceBand(score?: number | null): "red" | "amber" | "green" | "none" {
  if (score == null) return "none";
  const percentage = Math.round(score * 100);
  if (percentage < 75) return "red";
  if (percentage <= 89) return "amber";
  return "green";
}

function pct(value: number, total: number): string {
  if (total <= 0) return "0%";
  return `${Math.round((value / total) * 100)}%`;
}

function buildPastedEmailFilename(history: RequestSummary[], now = new Date()): string {
  const dd = String(now.getDate()).padStart(2, "0");
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const yy = String(now.getFullYear()).slice(-2);
  const dateToken = `${dd}${mm}${yy}`;
  const pattern = new RegExp(`^Pasted_Email_${dateToken}(\\d{2,})\\.txt$`, "i");

  let maxSeq = 0;
  for (const item of history) {
    const match = (item.filename || "").match(pattern);
    if (!match) continue;
    const seq = Number.parseInt(match[1], 10);
    if (!Number.isNaN(seq)) {
      maxSeq = Math.max(maxSeq, seq);
    }
  }

  const nextSeq = String(maxSeq + 1).padStart(2, "0");
  return `Pasted_Email_${dateToken}${nextSeq}.txt`;
}

function buildPieChartData(
  points: { label: string; count: number }[],
  palette: string[]
): PieChartData {
  const total = points.reduce((sum, point) => sum + point.count, 0);
  if (total <= 0) {
    return {
      total: 0,
      gradient: "conic-gradient(#e3ebf7 0% 100%)",
      slices: [],
    };
  }

  let runningCount = 0;
  const slices = points.map((point, index) => {
    const startPercent = (runningCount / total) * 100;
    runningCount += point.count;
    const endPercent = (runningCount / total) * 100;
    return {
      label: point.label,
      count: point.count,
      color: palette[index % palette.length],
      percent: Math.round((point.count / total) * 100),
      startPercent,
      endPercent,
    };
  });

  const gradient = `conic-gradient(${slices
    .map((slice) => `${slice.color} ${slice.startPercent}% ${slice.endPercent}%`)
    .join(", ")})`;

  return {
    total,
    gradient,
    slices,
  };
}

export function StatusPill({ status }: { status: string }) {
  const normalizedStatus = normalizeStatusLabel(status);
  return (
    <span className={`pill ${PILL_CLASS[normalizedStatus] || "processing"}`}>
      {normalizedStatus}
    </span>
  );
}

export default function App() {
  const [theme, setTheme] = useState<ThemeMode>(() => {
    if (typeof window === "undefined") return "light";
    const stored = window.localStorage.getItem("acoa-theme");
    if (stored === "light" || stored === "dark") {
      return stored;
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  const [view, setView] = useState<ViewKey>("dashboard");
  const [ingestMode, setIngestMode] = useState<IngestMode>("upload");
  const [history, setHistory] = useState<RequestSummary[]>([]);
  const [selected, setSelected] = useState<RequestDetail | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [filePreviewText, setFilePreviewText] = useState("");
  const [emailText, setEmailText] = useState("");
  const [classificationFilter, setClassificationFilter] = useState<string>(ALL_CLASSIFICATIONS);
  const [search, setSearch] = useState("");
  const [uploading, setUploading] = useState(false);
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [keyOk, setKeyOk] = useState<boolean | null>(null);

  const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null);
  const [batchBusy, setBatchBusy] = useState<"start" | "stop" | "run" | "refresh" | null>(null);
  const [extractionRunMode, setExtractionRunMode] = useState<IngestMode | null>(null);
  const [extractionStepIndex, setExtractionStepIndex] = useState(0);

  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [auditBusy, setAuditBusy] = useState(false);

  const [config, setConfig] = useState<AppConfig | null>(null);
  const [configBusy, setConfigBusy] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetPromptOpen, setResetPromptOpen] = useState(false);
  const [resetResult, setResetResult] = useState<ResetDataResult | null>(null);
  const [configDraft, setConfigDraft] = useState({
    llm_model: "",
    anthropic_api_key: "",
    batch_interval_seconds: 30,
    batch_enabled: false,
    use_truststore: false,
  });
  const [expandedRows, setExpandedRows] = useState<Record<string, boolean>>({});
  const [inlineDetails, setInlineDetails] = useState<Record<string, RequestDetail>>({});
  const [inlineLoading, setInlineLoading] = useState<Record<string, boolean>>({});
  const [inlineErrors, setInlineErrors] = useState<Record<string, string>>({});
  const [tableSortByView, setTableSortByView] = useState<Partial<Record<ViewKey, TableSortState>>>({});
  const [tablePaginationByView, setTablePaginationByView] = useState<
    Partial<Record<ViewKey, TablePaginationState>>
  >({});

  const fileInput = useRef<HTMLInputElement>(null);

  const refreshBatchStatus = useCallback(async () => {
    try {
      setBatchStatus(await api.batchStatus());
    } catch {
      /* backend not up yet */
    }
  }, []);

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await api.listFiles());
    } catch {
      /* backend not up yet */
    }
  }, []);

  const refreshAudit = useCallback(async () => {
    setAuditBusy(true);
    try {
      setAudit(await api.listAudit(1000));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAuditBusy(false);
    }
  }, []);

  const refreshConfig = useCallback(async () => {
    try {
      const cfg = await api.getConfig();
      setConfig(cfg);
      setConfigDraft({
        llm_model: cfg.llm_model,
        anthropic_api_key: "",
        batch_interval_seconds: cfg.batch_interval_seconds,
        batch_enabled: cfg.batch_enabled,
        use_truststore: cfg.truststore_enabled,
      });
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    window.localStorage.setItem("acoa-theme", theme);
  }, [theme]);

  useEffect(() => {
    if (!resetResult) return;
    const timer = window.setTimeout(() => {
      setResetResult(null);
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [resetResult]);

  useEffect(() => {
    refreshHistory();
    refreshBatchStatus();
    fetch("/api/health")
      .then((r) => r.json())
      .then((h) => setKeyOk(h.api_key_configured))
      .catch(() => setKeyOk(null));
  }, [refreshHistory, refreshBatchStatus]);

  useEffect(() => {
    if (view === "audit") {
      refreshAudit();
    }
    if (view === "config") {
      refreshConfig();
    }
  }, [view, refreshAudit, refreshConfig]);

  useEffect(() => {
    setExpandedRows({});
  }, [view]);

  useEffect(() => {
    if (!batchStatus?.running) return;
    const timer = setInterval(() => {
      refreshBatchStatus();
      refreshHistory();
    }, 5000);
    return () => clearInterval(timer);
  }, [batchStatus?.running, refreshBatchStatus, refreshHistory]);

  useEffect(() => {
    const isInteractiveExtractionRun =
      extractionRunMode === "upload" || extractionRunMode === "text";
    if (!uploading || !isInteractiveExtractionRun) {
      setExtractionStepIndex(0);
      return;
    }

    setExtractionStepIndex(0);
    const timer = window.setInterval(() => {
      setExtractionStepIndex((prev) =>
        Math.min(prev + 1, EXTRACTION_PROGRESS_STEPS.length - 1)
      );
    }, 1200);

    return () => window.clearInterval(timer);
  }, [uploading, extractionRunMode]);

  const handleFiles = async (files: FileList | null) => {
    if (!files || !files.length) return;
    const file = files[0];
    if (!/\.(txt|msg)$/i.test(file.name)) {
      setError("Only .txt and .msg email files are supported.");
      return;
    }
    setIngestMode("upload");
    setError(null);

    setSelectedFile(file);
    if (file.name.toLowerCase().endsWith(".txt")) {
      try {
        const text = await file.text();
        setFilePreviewText(text);
      } catch {
        setFilePreviewText("");
      }
    } else {
      setFilePreviewText("");
    }
  };

  const switchIngestMode = (mode: IngestMode) => {
    setIngestMode(mode);
    if (mode === "text") {
      const syncedText = selected?.raw_email?.body || filePreviewText;
      if (syncedText) {
        setEmailText(syncedText.slice(0, MAX_TEXT_CHARS));
      }
      return;
    }
    if (mode === "batch") {
      refreshBatchStatus();
    }
  };

  const runExtraction = async () => {
    setError(null);
    let fileToUpload: File | undefined;

    if (ingestMode === "upload") {
      if (!selectedFile) {
        setError("Choose a .txt or .msg file first.");
        return;
      }
      fileToUpload = selectedFile;
    } else if (ingestMode === "text") {
      const trimmed = emailText.trim();
      if (!trimmed) {
        setError("Paste email text first.");
        return;
      }
      const body = trimmed.slice(0, MAX_TEXT_CHARS);
      const pastedFileName = buildPastedEmailFilename(history);
      fileToUpload = new File([body], pastedFileName, { type: "text/plain" });
    } else {
      setError("Use batch controls to run folder processing.");
      return;
    }

    if (!fileToUpload) return;

    setExtractionRunMode(ingestMode);
    setUploading(true);
    try {
      const detail = await api.upload(fileToUpload);
      setSelected(detail);
      setView("extraction");
      const parsedBody = detail.raw_email?.body || "";
      if (parsedBody) {
        setEmailText(parsedBody.slice(0, MAX_TEXT_CHARS));
        setFilePreviewText(parsedBody);
      }
      await refreshHistory();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setUploading(false);
      setExtractionRunMode(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const clearIngest = () => {
    setSelectedFile(null);
    setFilePreviewText("");
    setEmailText("");
    setError(null);
    if (fileInput.current) fileInput.current.value = "";
  };

  const selectRequest = async (id: string) => {
    try {
      setSelected(await api.getFile(id));
    } catch (e: any) {
      setError(e.message);
    }
  };

  const openInExtraction = async (id: string) => {
    await selectRequest(id);
    setView("extraction");
  };

  const toggleRowExpansion = async (id: string, singleOpen = false) => {
    const currentlyExpanded = Boolean(expandedRows[id]);
    if (currentlyExpanded) {
      if (singleOpen) {
        setExpandedRows({});
      } else {
        setExpandedRows((prev) => ({ ...prev, [id]: false }));
      }
      return;
    }

    if (singleOpen) {
      setExpandedRows({ [id]: true });
    } else {
      setExpandedRows((prev) => ({ ...prev, [id]: true }));
    }

    if (inlineDetails[id] || inlineLoading[id]) {
      return;
    }

    setInlineLoading((prev) => ({ ...prev, [id]: true }));
    setInlineErrors((prev) => ({ ...prev, [id]: "" }));

    try {
      const detail = await api.getFile(id);
      setInlineDetails((prev) => ({ ...prev, [id]: detail }));
    } catch (e: any) {
      setInlineErrors((prev) => ({ ...prev, [id]: e.message || "Failed to load details." }));
    } finally {
      setInlineLoading((prev) => ({ ...prev, [id]: false }));
    }
  };

  const renderInlineStructuredData = (detail: RequestDetail) => {
    const renderInlineEmailPreview = () => {
      const body = detail.raw_email?.body?.trim();
      return (
        <details className="preview-block inline-email-preview">
          <summary>Preview extracted body</summary>
          <pre>{body || "No parsed email body available for this record."}</pre>
        </details>
      );
    };

    const extraction = detail.extraction;
    const userNote = noteForExpandedStatus(detail);
    if (!extraction) {
      return (
        <div className="inline-structured">
          <div className="inline-empty">No extraction data available.</div>
          {renderInlineEmailPreview()}
        </div>
      );
    }

    if (
      extraction.collateral_request_detected === false ||
      extraction.request_type === "Not a collateral request"
    ) {
      return (
        <div className="inline-structured">
          <div className="inline-empty">
            This item is classified as not a collateral request. No structured entities were extracted.
          </div>
          {renderInlineEmailPreview()}
        </div>
      );
    }

    const cases: ExtractionCase[] =
      Array.isArray(extraction.requests) && extraction.requests.length > 0
        ? extraction.requests
        : [
            {
              request_type: extraction.request_type,
              request_type_confidence: extraction.request_type_confidence,
              summary: extraction.summary,
              entities: extraction.entities,
              ambiguities: extraction.ambiguities,
              suggested_action: extraction.suggested_action,
              overall_confidence: extraction.overall_confidence,
            },
          ];

    const suggestedClarificationEmail =
      detail.clarification_draft ||
      cases
        .map((caseItem) => caseItem.clarification_draft)
        .find((draft) => typeof draft === "string" && draft.trim().length > 0) ||
      null;

    return (
      <div className="inline-structured">
        {userNote && (
          <div className="preview-block inline-user-note">
            <strong>Note added by User:</strong>
            <p>{userNote}</p>
          </div>
        )}
        {cases.map((caseItem, index) => (
          <section className="inline-case" key={`${detail.id}-case-${index}`}>
            <div className="inline-case-head">
              <strong>
                Case {index + 1}: {caseItem.request_type}
              </strong>
              <span>{Math.round((caseItem.overall_confidence || 0) * 100)}% confidence</span>
            </div>
            {caseItem.summary && <p className="inline-case-summary">{caseItem.summary}</p>}
            <div className="inline-field-grid">
              {Object.entries(caseItem.entities || {})
                .filter(([key]) => key !== "instruction_details")
                .map(([key, field]) => (
                <div className="inline-field" key={`${detail.id}-${index}-${key}`}>
                  <span>{INLINE_FIELD_LABELS[key] || key}</span>
                  <strong>
                    {field.value
                      ? key === "amount"
                        ? formatAmountDisplay(field.value)
                        : field.value
                      : "Not extracted"}
                  </strong>
                </div>
                ))}
            </div>
          </section>
        ))}
        {suggestedClarificationEmail && (
          <details className="preview-block inline-email-preview">
            <summary>Suggested Clarification Email</summary>
            <pre>{suggestedClarificationEmail}</pre>
          </details>
        )}
        {renderInlineEmailPreview()}
      </div>
    );
  };

  const runBatchNow = async () => {
    setError(null);
    setBatchBusy("run");
    try {
      setBatchStatus(await api.batchRunNow());
      await refreshHistory();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBatchBusy(null);
    }
  };

  const toggleBatchPolling = async () => {
    setError(null);
    const action = batchStatus?.running ? "stop" : "start";
    setBatchBusy(action);
    try {
      setBatchStatus(action === "start" ? await api.batchStart() : await api.batchStop());
      await refreshHistory();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBatchBusy(null);
    }
  };

  const refreshBatch = async () => {
    setBatchBusy("refresh");
    try {
      await refreshBatchStatus();
      await refreshHistory();
    } finally {
      setBatchBusy(null);
    }
  };

  const saveConfig = async () => {
    setConfigBusy(true);
    setError(null);
    try {
      const payload = {
        llm_model: configDraft.llm_model,
        anthropic_api_key: configDraft.anthropic_api_key || undefined,
        batch_interval_seconds: Math.max(1, Number(configDraft.batch_interval_seconds || 30)),
        batch_enabled: configDraft.batch_enabled,
        use_truststore: configDraft.use_truststore,
      };
      const next = await api.updateConfig(payload);
      setConfig(next);
      setConfigDraft((prev) => ({ ...prev, anthropic_api_key: "" }));
      await refreshBatchStatus();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setConfigBusy(false);
    }
  };

  const runAppReset = async () => {
    setResetBusy(true);
    setError(null);
    try {
      const result = await api.resetData();
      setResetResult(result);

      setSelected(null);
      setSelectedFile(null);
      setFilePreviewText("");
      setEmailText("");
      setExpandedRows({});
      setInlineDetails({});
      setInlineLoading({});
      setInlineErrors({});
      setHistory([]);
      setAudit([]);
      setClassificationFilter(ALL_CLASSIFICATIONS);
      setSearch("");

      await refreshHistory();
      await refreshBatchStatus();
      await refreshConfig();
      setResetPromptOpen(false);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setResetBusy(false);
    }
  };

  const queueRequests = useMemo(
    () => history.filter((item) => QUEUE_STATUSES.has(item.status)),
    [history]
  );
  const awaitingRequests = useMemo(
    () =>
      history.filter(
        (item) =>
          item.status === "Awaiting Clarifications" || item.status === "Awaiting Customer"
      ),
    [history]
  );
  const approvedRequests = useMemo(
    () => history.filter((item) => item.status === "Approved"),
    [history]
  );
  const rejectedRequests = useMemo(
    () => history.filter((item) => item.status === "Rejected"),
    [history]
  );

  const classificationOptions = useMemo(() => {
    const labels = Array.from(new Set(history.map((item) => classificationOf(item))));
    labels.sort((a, b) => a.localeCompare(b));
    return [ALL_CLASSIFICATIONS, ...labels];
  }, [history]);

  const applyFilters = useCallback(
    (items: RequestSummary[]) => {
      const q = search.trim().toLowerCase();
      return items.filter((item) => {
        const cls = classificationOf(item);
        const matchesClass =
          classificationFilter === ALL_CLASSIFICATIONS || cls === classificationFilter;
        if (!matchesClass) return false;
        if (!q) return true;
        return [item.filename, item.id, item.subject || "", cls, item.status]
          .join(" ")
          .toLowerCase()
          .includes(q);
      });
    },
    [classificationFilter, search]
  );

  const filteredQueue = useMemo(() => applyFilters(queueRequests), [applyFilters, queueRequests]);
  const filteredAll = useMemo(() => applyFilters(history), [applyFilters, history]);
  const filteredAwaiting = useMemo(
    () => applyFilters(awaitingRequests),
    [applyFilters, awaitingRequests]
  );
  const filteredApproved = useMemo(
    () => applyFilters(approvedRequests),
    [applyFilters, approvedRequests]
  );
  const filteredRejected = useMemo(
    () => applyFilters(rejectedRequests),
    [applyFilters, rejectedRequests]
  );

  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of history) {
      const normalizedStatus = normalizeStatusLabel(item.status);
      counts[normalizedStatus] = (counts[normalizedStatus] || 0) + 1;
    }
    return counts;
  }, [history]);

  const classificationCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of history) {
      const cls = classificationOf(item);
      counts[cls] = (counts[cls] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [history]);

  const statusDistribution = useMemo(
    () =>
      Object.entries(statusCounts)
        .sort((a, b) => b[1] - a[1])
        .map(([status, count]) => ({ status, count })),
    [statusCounts]
  );

  const statusPieData = useMemo(
    () =>
      buildPieChartData(
        statusDistribution.map((item) => ({ label: item.status, count: item.count })),
        STATUS_PIE_COLORS
      ),
    [statusDistribution]
  );

  const classificationPieData = useMemo(
    () =>
      buildPieChartData(
        classificationCounts.map(([label, count]) => ({ label, count })),
        CLASSIFICATION_PIE_COLORS
      ),
    [classificationCounts]
  );

  const dailyProcessedAverage = useMemo(() => {
    const processedByDay = new Map<string, number>();

    for (const item of history) {
      const timestamp =
        item.actioned_at ||
        item.approved_at ||
        item.rejected_at ||
        item.clarification_requested_at;
      if (!timestamp) continue;

      const date = new Date(timestamp);
      if (Number.isNaN(date.getTime())) continue;

      const dayKey = date.toISOString().slice(0, 10);
      processedByDay.set(dayKey, (processedByDay.get(dayKey) || 0) + 1);
    }

    const totalProcessed = Array.from(processedByDay.values()).reduce(
      (sum, count) => sum + count,
      0
    );
    const activeDays = processedByDay.size;
    const averagePerDay = activeDays > 0 ? totalProcessed / activeDays : 0;

    return {
      totalProcessed,
      activeDays,
      averagePerDay,
    };
  }, [history]);

  const lastBatchProcessed = batchStatus?.last_batch.processed ?? 0;
  const batchProcessingCount =
    statusCounts["Processing"] || 0;
  const showExtractionLoader =
    uploading && (extractionRunMode === "upload" || extractionRunMode === "text");
  const extractionStep =
    EXTRACTION_PROGRESS_STEPS[
      Math.min(extractionStepIndex, EXTRACTION_PROGRESS_STEPS.length - 1)
    ];
  const extractionProgressPercentRaw = Math.round(
    ((Math.min(extractionStepIndex, EXTRACTION_PROGRESS_STEPS.length - 1) + 1) /
      EXTRACTION_PROGRESS_STEPS.length) *
      100
  );
  const extractionProgressPercent = Math.min(
    EXTRACTION_PROGRESS_MAX_VISIBLE,
    extractionProgressPercentRaw
  );

  const renderFilters = (
    <div className="history-toolbar">
      <label>
        Classification
        <select
          value={classificationFilter}
          onChange={(e) => setClassificationFilter(e.target.value)}
        >
          {classificationOptions.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </label>
      <label>
        Search file, ID or subject
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Type filename, request id or subject"
        />
      </label>
    </div>
  );

  const renderRequestTable = (
    rows: RequestSummary[],
    noteColumn?: NoteColumnConfig,
    inlineExpand = false,
    expandMode: TableExpandMode = "inline",
    showRowAction = true,
    tableKey?: ViewKey,
    enableSortAndPagination = false
  ) => {
    if (rows.length === 0) {
      return <div className="empty-history">No records match the current filters.</div>;
    }

    const sortingEnabled = Boolean(
      enableSortAndPagination && tableKey && SORTABLE_VIEWS.has(tableKey)
    );
    const sortState = sortingEnabled
      ? tableSortByView[tableKey!] || DEFAULT_TABLE_SORT
      : DEFAULT_TABLE_SORT;
    const paginationState = sortingEnabled
      ? tablePaginationByView[tableKey!] || DEFAULT_TABLE_PAGINATION
      : DEFAULT_TABLE_PAGINATION;

    const valueForSort = (item: RequestSummary, field: TableSortField): string | number => {
      switch (field) {
        case "filename":
          return item.filename || "";
        case "requestId":
          return item.id || "";
        case "classification":
          return classificationOf(item);
        case "received": {
          const ts = item.received_at || item.uploaded_at;
          const time = ts ? new Date(ts).getTime() : Number.NEGATIVE_INFINITY;
          return Number.isNaN(time) ? Number.NEGATIVE_INFINITY : time;
        }
        case "confidence":
          return item.overall_confidence ?? Number.NEGATIVE_INFINITY;
        case "source":
          return item.source_mode || "upload";
        case "note":
          return noteColumn ? item[noteColumn.field] || "" : "";
        default:
          return "";
      }
    };

    const sortedRows = sortingEnabled
      ? [...rows].sort((a, b) => {
          const left = valueForSort(a, sortState.field);
          const right = valueForSort(b, sortState.field);

          let comparison = 0;
          if (typeof left === "number" && typeof right === "number") {
            comparison = left - right;
          } else {
            comparison = String(left).localeCompare(String(right), undefined, {
              numeric: true,
              sensitivity: "base",
            });
          }

          return sortState.direction === "asc" ? comparison : -comparison;
        })
      : rows;

    const totalItems = sortedRows.length;
    const pageSize = sortingEnabled ? paginationState.pageSize : totalItems;
    const totalPages = sortingEnabled ? Math.max(1, Math.ceil(totalItems / pageSize)) : 1;
    const currentPage = sortingEnabled
      ? Math.min(Math.max(1, paginationState.page), totalPages)
      : 1;
    const pageStartIndex = sortingEnabled ? (currentPage - 1) * pageSize : 0;
    const pagedRows = sortingEnabled
      ? sortedRows.slice(pageStartIndex, pageStartIndex + pageSize)
      : sortedRows;

    const updateSort = (field: TableSortField) => {
      if (!sortingEnabled || !tableKey) return;
      setTableSortByView((prev) => {
        const existing = prev[tableKey] || DEFAULT_TABLE_SORT;
        const next: TableSortState =
          existing.field === field
            ? { field, direction: existing.direction === "asc" ? "desc" : "asc" }
            : { field, direction: "asc" };
        return { ...prev, [tableKey]: next };
      });
      setTablePaginationByView((prev) => ({
        ...prev,
        [tableKey]: {
          ...(prev[tableKey] || DEFAULT_TABLE_PAGINATION),
          page: 1,
        },
      }));
    };

    const updatePage = (nextPage: number) => {
      if (!sortingEnabled || !tableKey) return;
      const clamped = Math.min(Math.max(1, nextPage), totalPages);
      setTablePaginationByView((prev) => ({
        ...prev,
        [tableKey]: {
          ...(prev[tableKey] || DEFAULT_TABLE_PAGINATION),
          page: clamped,
        },
      }));
    };

    const updatePageSize = (nextSize: number) => {
      if (!sortingEnabled || !tableKey) return;
      setTablePaginationByView((prev) => ({
        ...prev,
        [tableKey]: {
          page: 1,
          pageSize: nextSize,
        },
      }));
    };

    const renderHeaderCell = (label: string, field: TableSortField) => {
      if (!sortingEnabled) {
        return <th>{label}</th>;
      }
      const isActive = sortState.field === field;
      const indicator = isActive ? (sortState.direction === "asc" ? "▲" : "▼") : "↕";
      return (
        <th>
          <button type="button" className={`table-sort-btn${isActive ? " active" : ""}`} onClick={() => updateSort(field)}>
            <span>{label}</span>
            <span className="table-sort-indicator">{indicator}</span>
          </button>
        </th>
      );
    };

    const actionVisible = inlineExpand || showRowAction;
    const colCount = 6 + (noteColumn ? 1 : 0) + (actionVisible ? 1 : 0);

    const singleOpen = expandMode === "review-open";

    return (
      <>
        <div className="history-table-wrap history-table-wrap-main">
        <table className={`history-table ${noteColumn ? "history-table-note" : "history-table-base"}`}>
          <colgroup>
            <col className="col-file-name" />
            <col className="col-request-id" />
            <col className="col-classification" />
            <col className="col-received" />
            <col className="col-confidence" />
            {noteColumn && <col className="col-note" />}
            <col className="col-source" />
            {actionVisible && <col className="col-chevron" />}
          </colgroup>
          <thead>
            <tr>
              {renderHeaderCell("File name", "filename")}
              {renderHeaderCell("Request ID", "requestId")}
              {renderHeaderCell("Classification", "classification")}
              {renderHeaderCell("Received", "received")}
              {renderHeaderCell("Confidence", "confidence")}
              {noteColumn
                ? renderHeaderCell(noteColumn.header, "note")
                : null}
              {renderHeaderCell("Source", "source")}
              {actionVisible && <th />}
            </tr>
          </thead>
          <tbody>
            {pagedRows.map((item) => {
              const expanded = Boolean(expandedRows[item.id]);
              const loading = Boolean(inlineLoading[item.id]);
              const detail = inlineDetails[item.id];
              const inlineError = inlineErrors[item.id];
              const showFullReviewInExpanded =
                expandMode === "review-open" && QUEUE_STATUSES.has(item.status);

              return (
                <Fragment key={item.id}>
                  <tr
                    className={
                      inlineExpand
                        ? `clickable-row${expanded ? " clickable-row-open" : ""}`
                        : ""
                    }
                    onClick={
                      inlineExpand
                        ? () => {
                            toggleRowExpansion(item.id, singleOpen);
                          }
                        : undefined
                    }
                    onKeyDown={
                      inlineExpand
                        ? (e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              toggleRowExpansion(item.id, singleOpen);
                            }
                          }
                        : undefined
                    }
                    tabIndex={inlineExpand ? 0 : undefined}
                  >
                    <td>{item.filename}</td>
                    <td className="mono">{item.id}</td>
                    <td>{classificationOf(item)}</td>
                    <td>{formatDateTime(item.received_at || item.uploaded_at)}</td>
                    <td>
                      {item.overall_confidence == null ? (
                        "-"
                      ) : (
                        <div className={`table-confidence-rag ${confidenceBand(item.overall_confidence)}`}>
                          <div className="table-confidence-rag-score">
                            {formatConfidence(item.overall_confidence)}
                          </div>
                          <div className="table-confidence-rag-line" aria-hidden="true" />
                        </div>
                      )}
                    </td>
                    {noteColumn && (
                      <td className="note-cell">{item[noteColumn.field] || "-"}</td>
                    )}
                    <td>{item.source_mode || "upload"}</td>
                    {actionVisible && (
                      <td className={`chevron-cell${expanded ? " open" : ""}`}>
                        {inlineExpand ? (
                          <span className="chevron-glyph">{expanded ? "▾" : "▸"}</span>
                        ) : (
                          <button className="btn edit" onClick={() => openInExtraction(item.id)}>
                            Open
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                  {inlineExpand && expanded && (
                    <tr className="inline-detail-row">
                      <td colSpan={colCount}>
                        {loading && <div className="inline-empty">Loading extracted data...</div>}
                        {!loading && inlineError && <div className="inline-empty">{inlineError}</div>}
                        {!loading &&
                          !inlineError &&
                          detail &&
                          (showFullReviewInExpanded ? (
                            <div className="inline-review-shell">
                              <DetailView
                                detail={detail}
                                onChange={(updated) => {
                                  setInlineDetails((prev) => ({
                                    ...prev,
                                    [item.id]: updated,
                                  }));
                                  if (selected?.id === updated.id) {
                                    setSelected(updated);
                                  }
                                  refreshHistory();
                                }}
                                onError={setError}
                              />
                            </div>
                          ) : (
                            renderInlineStructuredData(detail)
                          ))}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
          </table>
        </div>
        {sortingEnabled && (
          <div className="table-pagination" role="navigation" aria-label="Request table pagination">
            <div className="table-pagination-meta">
              <span>
                Showing {pageStartIndex + 1}-{Math.min(pageStartIndex + pageSize, totalItems)} of {totalItems}
              </span>
              <label>
                Rows per page
                <select
                  value={pageSize}
                  onChange={(e) => updatePageSize(Number(e.target.value))}
                >
                  {TABLE_PAGE_SIZE_OPTIONS.map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="table-pagination-controls">
              <button
                type="button"
                className="table-page-btn"
                disabled={currentPage <= 1}
                onClick={() => updatePage(currentPage - 1)}
              >
                Previous
              </button>
              <span>
                Page {currentPage} of {totalPages}
              </span>
              <button
                type="button"
                className="table-page-btn"
                disabled={currentPage >= totalPages}
                onClick={() => updatePage(currentPage + 1)}
              >
                Next
              </button>
            </div>
          </div>
        )}
      </>
    );
  };

  const renderDashboard = () => (
    <section className="ops-page">
      <div className="ops-page-head">
        <div>
          <h2>Operational Dashboard</h2>
          <p>Live workload, queue health, and batch processing posture.</p>
        </div>
        <button className="btn cancel" onClick={refreshHistory}>
          Refresh
        </button>
      </div>

      <div className="metric-grid">
        <button type="button" className="metric-card metric-card-nav" onClick={() => setView("all")}>
          <span>Total requests</span>
          <strong>{history.length}</strong>
        </button>
        <button type="button" className="metric-card metric-card-nav" onClick={() => setView("queue")}>
          <span>My Queue</span>
          <strong>{queueRequests.length}</strong>
        </button>
        <button type="button" className="metric-card metric-card-nav" onClick={() => setView("ask")}>
          <span>Ask Clarifications</span>
          <strong>{awaitingRequests.length}</strong>
        </button>
        <button type="button" className="metric-card metric-card-nav" onClick={() => setView("approved")}>
          <span>Approved</span>
          <strong>{approvedRequests.length}</strong>
        </button>
        <button type="button" className="metric-card metric-card-nav" onClick={() => setView("rejected")}>
          <span>Rejected</span>
          <strong>{rejectedRequests.length}</strong>
        </button>
        <button type="button" className="metric-card metric-card-nav" onClick={() => setView("config")}>
          <span>Batch polling</span>
          <strong>{batchStatus?.running ? "ON" : "OFF"}</strong>
        </button>
      </div>

      <div className="dashboard-grid">
        <section className="card">
          <div className="card-head">
            <span>Pipeline status</span>
          </div>
          <div className="card-body">
            <div className="stack-bars">
              {statusDistribution.length === 0 && <div className="empty-history">No data yet.</div>}
              {statusDistribution.map((item) => (
                <div className="stack-bar-row" key={item.status}>
                  <div className="stack-bar-label">{item.status}</div>
                  <div className="stack-bar-track">
                    <div
                      className="stack-bar-fill"
                      style={{ width: pct(item.count, history.length) }}
                    />
                  </div>
                  <div className="stack-bar-value">{item.count}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <span>Batch activity</span>
          </div>
          <div className="card-body">
            <div className="batch-metrics">
              <span className="ingest-chip">running: {batchStatus?.running ? "yes" : "no"}</span>
              <span className="ingest-chip">interval: {batchStatus?.interval_seconds ?? 30}s</span>
              <span className="ingest-chip">inbox files: {batchStatus?.inbox_file_count ?? 0}</span>
              <span className="ingest-chip">processed (last run): {lastBatchProcessed}</span>
              <span className="ingest-chip">duplicates (last run): {batchStatus?.last_batch.duplicates ?? 0}</span>
              <span className="ingest-chip">failed (last run): {batchStatus?.last_batch.failed ?? 0}</span>
            </div>
            <div className="ingest-actions" style={{ marginTop: 10 }}>
              <button
                className="btn ask"
                disabled={batchBusy !== null || !batchStatus?.running}
                onClick={runBatchNow}
              >
                {batchBusy === "run" ? "Running..." : "Run batch now"}
              </button>
              <button className="btn cancel" disabled={batchBusy !== null} onClick={toggleBatchPolling}>
                {batchStatus?.running ? "Stop polling" : "Start polling"}
              </button>
            </div>
          </div>
        </section>
      </div>

      <section className="card">
        <div className="card-head">
          <span>Recent requests</span>
        </div>
        <div className="card-body">{renderRequestTable(history.slice(0, 8), undefined, false, "inline", false)}</div>
      </section>
    </section>
  );

  const renderExtractionLayer = () => (
    <section className="ops-page">
      <div className="ops-page-head">
        <div>
          <h2>Data Extraction Layer</h2>
          <p>Ingest client emails, run extraction, and complete human review actions.</p>
        </div>
      </div>

      <div className="review-layout">
        <section className="card ingest-panel">
          <div className="card-head ingest-card-head">
            <div>
              <span>Ingest email</span>
              <p className="ingest-sub">
                Drop a file or paste the email body. Max 5000 chars for text, 5 MB for uploads.
              </p>
            </div>
            <div className="mode-toggle">
              <button
                className={ingestMode === "upload" ? "active" : ""}
                onClick={() => switchIngestMode("upload")}
              >
                Upload file
              </button>
              <button
                className={ingestMode === "text" ? "active" : ""}
                onClick={() => switchIngestMode("text")}
              >
                Paste text
              </button>
              <button
                className={ingestMode === "batch" ? "active" : ""}
                onClick={() => switchIngestMode("batch")}
              >
                Batch run
              </button>
            </div>
          </div>
          <div className="card-body">
            {ingestMode === "upload" ? (
              <>
                <div
                  className={`dropzone dropzone-wide ${drag ? "drag" : ""}`}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDrag(true);
                  }}
                  onDragLeave={() => setDrag(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDrag(false);
                    handleFiles(e.dataTransfer.files);
                  }}
                >
                  <strong>Drop a .msg or .txt file</strong>
                  <div className="hint">or browse from your computer</div>
                  <button onClick={() => fileInput.current?.click()} disabled={uploading}>
                    Choose file
                  </button>
                  <input
                    ref={fileInput}
                    type="file"
                    accept=".txt,.msg"
                    hidden
                    onChange={(e) => handleFiles(e.target.files)}
                  />
                </div>

                {selectedFile && (
                  <div className="selected-upload">
                    <div>
                      <strong>{selectedFile.name}</strong>
                      <div className="hint">
                        {selectedFile.type || "text/plain"} . {(selectedFile.size / 1024).toFixed(1)} KB
                      </div>
                    </div>
                    <button className="btn reject" onClick={() => setSelectedFile(null)}>
                      Remove
                    </button>
                  </div>
                )}

                {(filePreviewText || selected?.raw_email?.body) && (
                  <details className="preview-block">
                    <summary>Preview extracted body</summary>
                    <pre>{filePreviewText || selected?.raw_email?.body}</pre>
                  </details>
                )}
              </>
            ) : ingestMode === "text" ? (
              <div className="text-ingest">
                <label htmlFor="email-body-input">Email body</label>
                <textarea
                  id="email-body-input"
                  value={emailText}
                  onChange={(e) => setEmailText(e.target.value.slice(0, MAX_TEXT_CHARS))}
                  placeholder="Paste the client email text here"
                />
                <div className="text-count">
                  {emailText.length} / {MAX_TEXT_CHARS}
                </div>
              </div>
            ) : (
              <div className="batch-panel">
                <div className="batch-row">
                  <span className="batch-label">Runtime root</span>
                  <span className="batch-value">{batchStatus?.runtime_root || "Loading..."}</span>
                </div>
                <div className="batch-row">
                  <span className="batch-label">Inbox</span>
                  <span className="batch-value">{batchStatus?.inbox_dir || "-"}</span>
                </div>
                <div className="batch-row">
                  <span className="batch-label">Processed</span>
                  <span className="batch-value">{batchStatus?.processed_dir || "-"}</span>
                </div>
                <div className="batch-row">
                  <span className="batch-label">Failed</span>
                  <span className="batch-value">{batchStatus?.failed_dir || "-"}</span>
                </div>
                <div className="batch-row">
                  <span className="batch-label">Duplicates</span>
                  <span className="batch-value">{batchStatus?.duplicates_dir || "-"}</span>
                </div>

                <div className="batch-toggle-row">
                  <div className="batch-toggle-copy">
                    <strong>Batch processing</strong>
                    <span>
                      Turn on to poll inbox every {batchStatus?.interval_seconds ?? 30} seconds.
                    </span>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={Boolean(batchStatus?.running)}
                    aria-label="Toggle batch processing"
                    className={`batch-toggle ${batchStatus?.running ? "on" : "off"}`}
                    onClick={toggleBatchPolling}
                    disabled={batchBusy !== null}
                  >
                    <span className="batch-toggle-thumb" />
                  </button>
                </div>

                <div className="batch-metrics">
                  <span className="ingest-chip">running: {batchStatus?.running ? "yes" : "no"}</span>
                  <span className="ingest-chip">interval: {batchStatus?.interval_seconds ?? 30}s</span>
                  <span className="ingest-chip">inbox files: {batchStatus?.inbox_file_count ?? 0}</span>
                  <span className="ingest-chip">last batch processed: {batchStatus?.last_batch.processed ?? 0}</span>
                  <span className="ingest-chip">last batch failed: {batchStatus?.last_batch.failed ?? 0}</span>
                  <span className="ingest-chip">
                    last batch duplicates: {batchStatus?.last_batch.duplicates ?? 0}
                  </span>
                </div>
              </div>
            )}

            <div className="ingest-footer">
              <div className="ingest-meta">
                <span className="ingest-chip">mode: {ingestMode}</span>
                <span className="ingest-chip">requests: {history.length}</span>
                <span className="ingest-chip">max: {MAX_TEXT_CHARS} chars</span>
              </div>
              {ingestMode === "batch" ? (
                <div className="ingest-actions">
                  <button className="btn cancel" onClick={refreshBatch} disabled={batchBusy !== null}>
                    {batchBusy === "refresh" ? "Refreshing..." : "Refresh status"}
                  </button>
                  <button
                    className="btn ask"
                    onClick={runBatchNow}
                    disabled={batchBusy !== null || !batchStatus?.running}
                  >
                    {batchBusy === "run" ? "Running..." : "Run batch now"}
                  </button>
                </div>
              ) : (
                <div className="ingest-actions">
                  <button className="btn cancel" onClick={clearIngest} disabled={uploading}>
                    Clear
                  </button>
                  <button className="btn save ingest-run" onClick={runExtraction} disabled={uploading}>
                    {uploading ? "Running..." : "Run validation and extraction"}
                  </button>
                </div>
              )}
            </div>
          </div>
        </section>

        <section className="review-shell">
          {batchProcessingCount > 0 && (
            <div className="banner amber batch-activity-banner" role="status" aria-live="polite">
              <span className="sand-timer" aria-hidden="true" />
              <span>
                {batchProcessingCount} file{batchProcessingCount === 1 ? " is" : "s are"} processing
              </span>
            </div>
          )}
          {batchProcessingCount === 0 && lastBatchProcessed > 0 && (
            <div className="banner success" role="status" aria-live="polite">
              {lastBatchProcessed} file{lastBatchProcessed === 1 ? " has" : "s have"} been processed.
            </div>
          )}
          <div className="review-head">
            <h2>Validated output and human review</h2>
            {selected && <StatusPill status={selected.status} />}
          </div>

          {queueRequests.length > 0 && (
            <div className="review-queue">
              <div className="queue-title">My queue</div>
              <div className="queue-list">
                {queueRequests.map((item) => (
                  <button
                    key={item.id}
                    className={`queue-item ${selected?.id === item.id ? "active" : ""}`}
                    onClick={() => selectRequest(item.id)}
                  >
                    <strong>{item.filename}</strong>
                    <span className="queue-meta">{classificationOf(item)}</span>
                    <span className="queue-meta">{formatDateTime(item.received_at || item.uploaded_at)}</span>
                    <span className="queue-meta">status: {item.status}</span>
                    {item.source_mode && <span className="queue-meta">source: {item.source_mode}</span>}
                  </button>
                ))}
              </div>
            </div>
          )}

          {!selected ? (
            <div className="placeholder">
              <h2>No request selected</h2>
              <p>
                Upload an email to run extraction, or open any request from My Queue, All Requests,
                Ask Clarifications, Approved, or Rejected.
              </p>
            </div>
          ) : (
            <DetailView
              detail={selected}
              onChange={(d: RequestDetail) => {
                setSelected(d);
                refreshHistory();
              }}
              onError={setError}
            />
          )}
        </section>
      </div>
    </section>
  );

  const renderStatusPage = (
    title: string,
    subtitle: string,
    rows: RequestSummary[],
    noteColumn?: NoteColumnConfig,
    expandMode: TableExpandMode = "inline",
    tableKey?: ViewKey
  ) => (
    <section className="ops-page">
      <div className="ops-page-head">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
      </div>
      <section className="card history-shell">
        <div className="card-body">
          {renderFilters}
          {renderRequestTable(
            rows,
            noteColumn,
            true,
            expandMode,
            true,
            tableKey,
            Boolean(tableKey && SORTABLE_VIEWS.has(tableKey))
          )}
        </div>
      </section>
    </section>
  );

  const renderReports = () => (
    <section className="ops-page">
      <div className="ops-page-head">
        <div>
          <h2>Reports</h2>
          <p>Status and classification trend snapshots from your request history.</p>
        </div>
      </div>

      <section className="card report-kpi-card">
        <div className="card-head">
          <span>Processing throughput</span>
        </div>
        <div className="card-body">
          <div className="report-kpi-value">{dailyProcessedAverage.averagePerDay.toFixed(1)}</div>
          <div className="report-kpi-label">Average cases processed per day</div>
          <div className="report-kpi-detail">
            Based on {dailyProcessedAverage.totalProcessed} actioned cases across {dailyProcessedAverage.activeDays} active day
            {dailyProcessedAverage.activeDays === 1 ? "" : "s"}.
          </div>
        </div>
      </section>

      <div className="dashboard-grid">
        <section className="card">
          <div className="card-head">
            <span>Status distribution</span>
          </div>
          <div className="card-body">
            {statusDistribution.length === 0 && <div className="empty-history">No requests yet.</div>}
            <div className="chart-stack">
              {statusDistribution.map((item) => (
                <div className="chart-row" key={item.status}>
                  <span>{item.status}</span>
                  <div className="chart-track">
                    <div
                      className="chart-fill status"
                      style={{ width: pct(item.count, history.length) }}
                    />
                  </div>
                  <strong>{item.count}</strong>
                </div>
              ))}
            </div>

            {statusPieData.total > 0 && (
              <div className="report-pie-wrap">
                <div className="report-pie-shell">
                  <div
                    className="report-pie"
                    style={{ background: statusPieData.gradient }}
                    role="img"
                    aria-label="Pie chart for status distribution"
                  >
                    <span>{statusPieData.total}</span>
                  </div>
                  <div className="report-pie-caption">Total status records</div>
                </div>
                <ul className="report-pie-legend">
                  {statusPieData.slices.map((slice) => (
                    <li key={slice.label}>
                      <span className="dot" style={{ background: slice.color }} />
                      <span>{slice.label}</span>
                      <strong>
                        {slice.count} ({slice.percent}%)
                      </strong>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <span>Classification distribution</span>
          </div>
          <div className="card-body">
            {classificationCounts.length === 0 && <div className="empty-history">No requests yet.</div>}
            <div className="chart-stack">
              {classificationCounts.map(([label, count]) => (
                <div className="chart-row" key={label}>
                  <span>{label}</span>
                  <div className="chart-track">
                    <div
                      className="chart-fill class"
                      style={{ width: pct(count, history.length) }}
                    />
                  </div>
                  <strong>{count}</strong>
                </div>
              ))}
            </div>

            {classificationPieData.total > 0 && (
              <div className="report-pie-wrap">
                <div className="report-pie-shell">
                  <div
                    className="report-pie"
                    style={{ background: classificationPieData.gradient }}
                    role="img"
                    aria-label="Pie chart for classification distribution"
                  >
                    <span>{classificationPieData.total}</span>
                  </div>
                  <div className="report-pie-caption">Total classification records</div>
                </div>
                <ul className="report-pie-legend">
                  {classificationPieData.slices.map((slice) => (
                    <li key={slice.label}>
                      <span className="dot" style={{ background: slice.color }} />
                      <span>{slice.label}</span>
                      <strong>
                        {slice.count} ({slice.percent}%)
                      </strong>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>
      </div>
    </section>
  );

  const renderAudit = () => (
    <section className="ops-page">
      <div className="ops-page-head">
        <div>
          <h2>Audit Trail</h2>
          <p>Append-only event stream across all requests.</p>
        </div>
        <button className="btn cancel" onClick={refreshAudit} disabled={auditBusy}>
          {auditBusy ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      <section className="card history-shell">
        <div className="card-body">
          <div className="history-table-wrap">
            <table className="history-table history-table-audit">
              <colgroup>
                <col className="col-audit-time" />
                <col className="col-audit-event" />
                <col className="col-audit-request" />
                <col className="col-audit-file" />
                <col className="col-audit-status" />
                <col className="col-audit-detail" />
              </colgroup>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                  <th>Request ID</th>
                  <th>File</th>
                  <th>Status</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((evt) => (
                  <tr key={evt.id}>
                    <td>{formatDateTime(evt.ts)}</td>
                    <td>{evt.event_type}</td>
                    <td className="mono">{evt.request_id}</td>
                    <td>{evt.filename}</td>
                    <td>
                      <StatusPill status={evt.status} />
                    </td>
                    <td>{evt.detail || "-"}</td>
                  </tr>
                ))}
                {audit.length === 0 && (
                  <tr>
                    <td colSpan={6}>
                      <div className="empty-history">No audit events available.</div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </section>
  );

  const renderConfig = () => (
    <section className="ops-page">
      <div className="ops-page-head">
        <div>
          <h2>Configuration</h2>
          <p>Model and batch runtime controls, with masked API key visibility.</p>
        </div>
      </div>

      <section className="card config-card">
        <div className="card-head">
          <span>Runtime settings</span>
        </div>
        <div className="card-body config-grid">
          <label>
            LLM model
            <input
              type="text"
              value={configDraft.llm_model}
              onChange={(e) =>
                setConfigDraft((prev) => ({
                  ...prev,
                  llm_model: e.target.value,
                }))
              }
              placeholder="claude-sonnet-4-6"
            />
          </label>

          <label>
            API key (masked)
            <input type="text" value={config?.llm_api_key_masked || "Not configured"} disabled />
          </label>

          <label>
            Replace API key (optional)
            <input
              type="password"
              value={configDraft.anthropic_api_key}
              onChange={(e) =>
                setConfigDraft((prev) => ({
                  ...prev,
                  anthropic_api_key: e.target.value,
                }))
              }
              placeholder="Enter only if rotating key"
            />
          </label>

          <label>
            Batch interval (seconds)
            <input
              type="number"
              min={1}
              value={configDraft.batch_interval_seconds}
              onChange={(e) =>
                setConfigDraft((prev) => ({
                  ...prev,
                  batch_interval_seconds: Number(e.target.value || 30),
                }))
              }
            />
          </label>

          <label className="config-toggle-line">
            <input
              type="checkbox"
              checked={configDraft.batch_enabled}
              onChange={(e) =>
                setConfigDraft((prev) => ({
                  ...prev,
                  batch_enabled: e.target.checked,
                }))
              }
            />
            <span>Enable batch polling</span>
          </label>

          <label className="config-toggle-line">
            <input
              type="checkbox"
              checked={configDraft.use_truststore}
              onChange={(e) =>
                setConfigDraft((prev) => ({
                  ...prev,
                  use_truststore: e.target.checked,
                }))
              }
            />
            <span>Enable truststore for enterprise TLS</span>
          </label>

          <div className="config-meta-row">
            <span>API key configured: {config?.api_key_configured ? "Yes" : "No"}</span>
            <span>Batch runtime root: {config?.batch_runtime_root || "-"}</span>
            <span>Cached evaluations: {config?.cache_entries ?? 0}</span>
          </div>

          <div className="ingest-actions">
            <button className="btn cancel" onClick={refreshConfig} disabled={configBusy}>
              Reload
            </button>
            <button className="btn save" onClick={saveConfig} disabled={configBusy}>
              {configBusy ? "Saving..." : "Save configuration"}
            </button>
          </div>
        </div>
      </section>

      <section className="card config-card config-danger-zone">
        <div className="card-head">
          <span>Data reset - ONLY FOR DEMO MODE</span>
        </div>
        <div className="card-body config-grid">
          <p className="danger-copy">
            Clear all request history, audit events, batch runtime files, and cached evaluations.
            The app returns to a blank slate.
          </p>
          <div className="ingest-actions">
            <button
              className="btn reject"
              onClick={() => setResetPromptOpen(true)}
              disabled={resetBusy}
            >
              Clear cache and all data
            </button>
          </div>
        </div>
      </section>
    </section>
  );

  const navItems: { key: ViewKey; label: string; count?: number }[] = [
    { key: "extraction", label: "Data Extraction Layer" },
    { key: "dashboard", label: "Operational Dashboard" },
    { key: "queue", label: "My Queue", count: queueRequests.length },
    { key: "all", label: "All Requests", count: history.length },
    { key: "ask", label: "Ask Clarifications", count: awaitingRequests.length },
    { key: "approved", label: "Approved", count: approvedRequests.length },
    { key: "rejected", label: "Rejected", count: rejectedRequests.length },
    { key: "reports", label: "Reports" },
    { key: "audit", label: "Audit Trail" },
    { key: "config", label: "Configuration" },
  ];

  const renderActiveView = () => {
    switch (view) {
      case "extraction":
        return renderExtractionLayer();
      case "dashboard":
        return renderDashboard();
      case "queue":
        return renderStatusPage(
          "My Queue",
          "Requests that still need operational review.",
          filteredQueue,
          undefined,
          "review-open",
          "queue"
        );
      case "all":
        return renderStatusPage(
          "All Requests",
          "Full searchable request inventory.",
          filteredAll,
          undefined,
          "review-open",
          "all"
        );
      case "ask":
        return renderStatusPage(
          "Ask Clarifications",
          "Requests waiting for client clarification.",
          filteredAwaiting,
          { header: "Clarification note", field: "latest_ask_customer_note" }
        );
      case "approved":
        return renderStatusPage(
          "Approved",
          "Completed approvals.",
          filteredApproved,
          { header: "Approval note", field: "latest_approval_note" },
          "inline",
          "approved"
        );
      case "rejected":
        return renderStatusPage(
          "Rejected",
          "Rejected requests with audit trace.",
          filteredRejected,
          { header: "Rejection note", field: "latest_rejection_note" },
          "inline",
          "rejected"
        );
      case "reports":
        return renderReports();
      case "audit":
        return renderAudit();
      case "config":
        return renderConfig();
      default:
        return renderDashboard();
    }
  };

  return (
    <div className="app ops-shell">
      <header className="topbar ops-topbar">
        <div className="ops-title-wrap">
          <h1>Collateral Operations Command Center</h1>
          <span className="sub">AI extraction layer and operational workflow cockpit</span>
        </div>
        <div className="topbar-controls">
          {keyOk !== null && (
            <span className={`keybadge ${keyOk ? "ok" : "missing"}`}>
              {keyOk ? "AI engine connected" : "ANTHROPIC_API_KEY not set"}
            </span>
          )}
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme((prev) => (prev === "light" ? "dark" : "light"))}
            aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
          >
            {theme === "light" ? "Dark theme" : "Light theme"}
          </button>
        </div>
      </header>

      <div className="ops-body">
        <aside className="ops-nav" aria-label="Navigation">
          {navItems.map((item) => (
            <button
              key={item.key}
              className={`ops-nav-item ${view === item.key ? "active" : ""}`}
              onClick={() => setView(item.key)}
            >
              <span>{item.label}</span>
              {typeof item.count === "number" && <strong>{item.count}</strong>}
            </button>
          ))}
        </aside>

        <main className="ops-main">
          {resetResult && (
            <div className="banner success auto-dismiss" role="status" aria-live="polite">
              Reset complete: deleted {resetResult.requests_deleted} requests, {resetResult.events_deleted} events, {resetResult.cache_entries_deleted} cached evaluations, and {resetResult.runtime_entries_deleted} runtime files.
            </div>
          )}
          {error && <div className="banner error">{error}</div>}
          {renderActiveView()}
        </main>
      </div>

      {resetPromptOpen && (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Clear all application data"
          onClick={() => {
            if (!resetBusy) setResetPromptOpen(false);
          }}
        >
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h3>Clear all cached and stored data?</h3>
            <p>This will wipe the application state and return to a blank slate.</p>
            <ul className="modal-list">
              <li>Deletes all requests and extracted records.</li>
              <li>Deletes all audit timeline events.</li>
              <li>Deletes cached evaluations used for token/cost savings.</li>
              <li>Clears batch runtime folders (inbox, processed, failed, duplicates).</li>
            </ul>
            <p>This action cannot be undone.</p>
            <div className="modal-actions">
              <button className="btn reject" onClick={runAppReset} disabled={resetBusy}>
                {resetBusy ? "Clearing..." : "Yes, clear everything"}
              </button>
              <button
                className="btn cancel"
                onClick={() => setResetPromptOpen(false)}
                disabled={resetBusy}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {showExtractionLoader && (
        <div
          className="modal-overlay extraction-loader-overlay"
          role="status"
          aria-live="polite"
          aria-label="Running AI extraction"
        >
          <div className="modal-card extraction-loader-card">
            <div className="loader-kicker">AI extraction in progress</div>
            <h3>{extractionStep.title}</h3>
            <p>{extractionStep.detail}</p>

            <div className="extraction-progress-track" aria-hidden="true">
              <span style={{ width: `${extractionProgressPercent}%` }} />
            </div>
            <div className="extraction-progress-meta">
              <span>
                Step {Math.min(extractionStepIndex + 1, EXTRACTION_PROGRESS_STEPS.length)} of{" "}
                {EXTRACTION_PROGRESS_STEPS.length}
              </span>
              <strong>{extractionProgressPercent}%</strong>
            </div>

            <ul className="extraction-step-list" aria-label="Extraction processing stages">
              {EXTRACTION_PROGRESS_STEPS.map((step, index) => {
                const state =
                  index < extractionStepIndex
                    ? "done"
                    : index === extractionStepIndex
                      ? "active"
                      : "queued";
                return (
                  <li key={step.title} className={`extraction-step ${state}`}>
                    <span className="step-marker" aria-hidden="true" />
                    <span>{step.title}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
