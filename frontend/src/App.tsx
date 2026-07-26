import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  AppConfig,
  AuditEvent,
  BatchStatus,
  ExtractionCase,
  RequestDetail,
  RequestSummary,
} from "./api";
import { formatAmountDisplay } from "./amountFormat";
import { DetailView } from "./components/DetailView";

const PILL_CLASS: Record<string, string> = {
  "Ready for Review": "ready",
  Approved: "approved",
  "Missing Mandatory Fields": "missing",
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
  instruction_details: "Instruction details",
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
  field: "latest_ask_customer_note" | "latest_rejection_note";
};

type TableExpandMode = "inline" | "review-open";

function classificationOf(item: RequestSummary): string {
  return item.classification || item.request_type || "Unclassified";
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

function pct(value: number, total: number): string {
  if (total <= 0) return "0%";
  return `${Math.round((value / total) * 100)}%`;
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
  return <span className={`pill ${PILL_CLASS[status] || "processing"}`}>{status}</span>;
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

  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [auditBusy, setAuditBusy] = useState(false);

  const [config, setConfig] = useState<AppConfig | null>(null);
  const [configBusy, setConfigBusy] = useState(false);
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
      fileToUpload = new File([body], "pasted_email.txt", { type: "text/plain" });
    } else {
      setError("Use batch controls to run folder processing.");
      return;
    }

    if (!fileToUpload) return;

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

    return (
      <div className="inline-structured">
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
              {Object.entries(caseItem.entities || {}).map(([key, field]) => (
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

  const queueRequests = useMemo(
    () => history.filter((item) => QUEUE_STATUSES.has(item.status)),
    [history]
  );
  const awaitingRequests = useMemo(
    () => history.filter((item) => item.status === "Awaiting Customer"),
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
      counts[item.status] = (counts[item.status] || 0) + 1;
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
    showRowAction = true
  ) => {
    if (rows.length === 0) {
      return <div className="empty-history">No records match the current filters.</div>;
    }

    const actionVisible = inlineExpand || showRowAction;
    const colCount = noteColumn
      ? actionVisible
        ? 9
        : 8
      : actionVisible
        ? 8
        : 7;

    const singleOpen = expandMode === "review-open";

    return (
      <div className="history-table-wrap history-table-wrap-main">
        <table className={`history-table ${noteColumn ? "history-table-note" : "history-table-base"}`}>
          <colgroup>
            <col className="col-file-name" />
            <col className="col-request-id" />
            <col className="col-classification" />
            <col className="col-status" />
            <col className="col-received" />
            <col className="col-confidence" />
            {noteColumn && <col className="col-note" />}
            <col className="col-source" />
            {actionVisible && <col className="col-chevron" />}
          </colgroup>
          <thead>
            <tr>
              <th>File name</th>
              <th>Request ID</th>
              <th>Classification</th>
              <th>Status</th>
              <th>Received</th>
              <th>Confidence</th>
              {noteColumn && <th>{noteColumn.header}</th>}
              <th>Source</th>
              {actionVisible && <th />}
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => {
              const expanded = Boolean(expandedRows[item.id]);
              const loading = Boolean(inlineLoading[item.id]);
              const detail = inlineDetails[item.id];
              const inlineError = inlineErrors[item.id];
              const showFullReviewInExpanded =
                expandMode === "review-open" && QUEUE_STATUSES.has(item.status);

              return (
                <Fragment key={item.id}>
                  <tr
                    className={inlineExpand ? "clickable-row" : ""}
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
                    <td>
                      <StatusPill status={item.status} />
                    </td>
                    <td>{formatDateTime(item.received_at || item.uploaded_at)}</td>
                    <td>{formatConfidence(item.overall_confidence)}</td>
                    {noteColumn && (
                      <td className="note-cell">{item[noteColumn.field] || "-"}</td>
                    )}
                    <td>{item.source_mode || "upload"}</td>
                    {actionVisible && (
                      <td className="chevron-cell">
                        {inlineExpand ? (
                          expanded ? "▾" : "▸"
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
                              <details className="preview-block inline-email-preview">
                                <summary>Preview extracted body</summary>
                                <pre>
                                  {detail.raw_email?.body?.trim() ||
                                    "No parsed email body available for this record."}
                                </pre>
                              </details>
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
          <span>Ask Customer</span>
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
                Ask Customer, Approved, or Rejected.
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
    expandMode: TableExpandMode = "inline"
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
          {renderRequestTable(rows, noteColumn, true, expandMode)}
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
    </section>
  );

  const navItems: { key: ViewKey; label: string; count?: number }[] = [
    { key: "extraction", label: "Data Extraction Layer" },
    { key: "dashboard", label: "Operational Dashboard" },
    { key: "queue", label: "My Queue", count: queueRequests.length },
    { key: "all", label: "All Requests", count: history.length },
    { key: "ask", label: "Ask Customer", count: awaitingRequests.length },
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
          "review-open"
        );
      case "all":
        return renderStatusPage(
          "All Requests",
          "Full searchable request inventory.",
          filteredAll,
          undefined,
          "review-open"
        );
      case "ask":
        return renderStatusPage(
          "Ask Customer",
          "Requests waiting for client clarification.",
          filteredAwaiting,
          { header: "Ask note", field: "latest_ask_customer_note" }
        );
      case "approved":
        return renderStatusPage("Approved", "Completed approvals.", filteredApproved);
      case "rejected":
        return renderStatusPage(
          "Rejected",
          "Rejected requests with audit trace.",
          filteredRejected,
          { header: "Rejection note", field: "latest_rejection_note" }
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
          {error && <div className="banner error">{error}</div>}
          {renderActiveView()}
        </main>
      </div>
    </div>
  );
}
