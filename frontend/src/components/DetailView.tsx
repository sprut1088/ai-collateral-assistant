import { useEffect, useMemo, useState } from "react";
import { api, Extraction, ExtractionCase, RequestDetail } from "../api";
import { formatAmountDisplay, normalizeAmountForSave } from "../amountFormat";

const FIELD_LABELS: Record<string, string> = {
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

const HIDDEN_ENTITY_KEYS = new Set(["instruction_details"]);

const MANDATORY_BY_TYPE: Record<string, string[]> = {
  "Margin Call": ["counterparty", "amount", "currency"],
  "Collateral Substitution": ["amount", "currency", "collateral_type", "replacement_asset"],
  "Collateral Transfer": ["counterparty", "amount", "currency", "account"],
  "Settlement Instruction": ["counterparty", "amount", "currency", "value_date"],
  Dispute: ["counterparty", "amount", "currency"],
  "Exposure Inquiry": ["counterparty"],
  "General Inquiry": [],
};

const LOCKED_DECISION_STATES = new Set([
  "Approved",
  "Rejected",
  "Awaiting Clarifications",
  "Awaiting Customer",
]);

function confidenceBand(conf: number): string {
  if (conf >= 0.9) return "High";
  if (conf >= 0.75) return "Medium";
  return "Low";
}

function overallConfidenceRag(confPercent: number): "green" | "amber" | "red" {
  if (confPercent > 90) return "green";
  if (confPercent >= 75) return "amber";
  return "red";
}

function isNotCollateral(extraction: Extraction): boolean {
  return (
    extraction.collateral_request_detected === false ||
    extraction.request_type === "Not a collateral request"
  );
}

function buildFallbackCase(extraction: Extraction): ExtractionCase {
  return {
    request_type: extraction.request_type,
    request_type_confidence: extraction.request_type_confidence,
    summary: extraction.summary,
    entities: extraction.entities,
    ambiguities: extraction.ambiguities,
    suggested_action: extraction.suggested_action,
    overall_confidence: extraction.overall_confidence,
  };
}

function extractCases(extraction: Extraction): ExtractionCase[] {
  const requests = extraction.requests;
  if (Array.isArray(requests) && requests.length > 0) {
    return requests;
  }
  return [buildFallbackCase(extraction)];
}

function isEditedEvidence(evidence: string | null | undefined): boolean {
  if (!evidence) return false;
  return evidence.includes("Old Value:") && evidence.includes("New Value:");
}

function renderEvidenceCell(evidence: string | null | undefined) {
  if (!evidence) return "-";
  if (!isEditedEvidence(evidence)) return evidence;

  const parts = evidence.split(" | ").map((part) => part.trim());
  const oldPart = parts.find((part) => part.startsWith("Old Value:"));
  const newPart = parts.find((part) => part.startsWith("New Value:"));
  const editPart = parts.find((part) => part.startsWith("Edited by"));

  return (
    <>
      {oldPart && <span className="evidence-edit-line">{oldPart}</span>}
      {newPart && <span className="evidence-edit-line">{newPart}</span>}
      <span className="evidence-edit-line">{editPart || "Edited by Operation User"}</span>
    </>
  );
}

export function DetailView({
  detail,
  onChange,
  onError,
}: {
  detail: RequestDetail;
  onChange: (d: RequestDetail) => void;
  onError: (msg: string | null) => void;
}) {
  const [approvePrompt, setApprovePrompt] = useState<{
    classification: string;
    missingFields: string[];
  } | null>(null);
  const [actionPrompt, setActionPrompt] = useState<{
    action: "ask" | "reject" | "approve";
    note: string;
  } | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [showUnavailableByCase, setShowUnavailableByCase] = useState<Record<number, boolean>>({});
  const [successNotice, setSuccessNotice] = useState<{ title: string; message: string } | null>(null);

  const ext = detail.extraction;
  const val = detail.validation;
  const terminal =
    detail.status === "Approved" ||
    detail.status === "Rejected" ||
    detail.status === "Not a collateral request";
  const requestLocked =
    terminal ||
    detail.status === "Awaiting Clarifications" ||
    detail.status === "Awaiting Customer";

  const cases = useMemo(() => {
    if (!ext) return [];
    if (isNotCollateral(ext)) return [];
    return extractCases(ext);
  }, [ext]);

  useEffect(() => {
    if (!ext || cases.length === 0) {
      setDrafts({});
      return;
    }

    const next: Record<string, string> = {};
    cases.forEach((caseItem, caseIndex) => {
      for (const [key, field] of Object.entries(caseItem.entities || {})) {
        if (HIDDEN_ENTITY_KEYS.has(key)) continue;
        const raw = field.value ?? "";
        next[`${caseIndex}:${key}`] = key === "amount" ? formatAmountDisplay(raw) : raw;
      }
    });
    setDrafts(next);
  }, [detail.id, ext, cases]);

  useEffect(() => {
    setShowUnavailableByCase({});
  }, [detail.id]);

  const caseRows = useMemo(() => {
    return cases.map((caseItem, caseIndex) => {
      const showUnavailable = Boolean(showUnavailableByCase[caseIndex]);
      const mandatory = new Set(MANDATORY_BY_TYPE[caseItem.request_type] || []);
      const rows = Object.entries(caseItem.entities || {})
        .filter(([key]) => !HIDDEN_ENTITY_KEYS.has(key))
        .map(([key, field]) => {
        const absent = field.value == null || field.value === "";
        return {
          key,
          field,
          absent,
          mandatory: mandatory.has(key),
          draftKey: `${caseIndex}:${key}`,
        };
        });
      return {
        caseItem,
        caseIndex,
        showUnavailable,
        rows,
        visibleRows: rows.filter((r) => showUnavailable || !r.absent),
        hiddenCount: rows.filter((r) => r.absent).length,
        hasEditedRows: rows.some((r) => isEditedEvidence(r.field.evidence)),
      };
    });
  }, [cases, showUnavailableByCase]);

  const run = async (
    label: string,
    fn: () => Promise<RequestDetail>,
    successMessage?: string
  ) => {
    setBusy(label);
    onError(null);
    try {
      onChange(await fn());
      if (successMessage) {
        setSuccessNotice({
          title: "Action completed",
          message: successMessage,
        });
      }
    } catch (e: any) {
      onError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const normalizedEntityValue = (fieldName: string, value: string): string =>
    fieldName === "amount" ? normalizeAmountForSave(value) : value;

  const hasFieldUpdate = (
    caseIndex: number,
    fieldName: string,
    caseItem: ExtractionCase
  ): boolean => {
    const key = `${caseIndex}:${fieldName}`;
    const currentValue = caseItem.entities?.[fieldName]?.value ?? "";
    const draftValue = drafts[key] ?? "";
    return (
      normalizedEntityValue(fieldName, draftValue) !==
      normalizedEntityValue(fieldName, currentValue)
    );
  };

  const updatesForCase = (caseIndex: number, caseItem: ExtractionCase): Record<string, string> => {
    const updates: Record<string, string> = {};
    for (const [fieldName, field] of Object.entries(caseItem.entities || {})) {
      if (HIDDEN_ENTITY_KEYS.has(fieldName)) continue;
      const key = `${caseIndex}:${fieldName}`;
      const currentValue = field.value ?? "";
      const draftValue = drafts[key] ?? "";
      const normalizedCurrent =
        fieldName === "amount" ? normalizeAmountForSave(currentValue) : currentValue;
      const normalizedDraft =
        fieldName === "amount" ? normalizeAmountForSave(draftValue) : draftValue;
      if (normalizedDraft !== normalizedCurrent) {
        updates[fieldName] = normalizedDraft;
      }
    }
    return updates;
  };

  const saveCaseEdit = (caseIndex: number) =>
    run(`save-${caseIndex}`, async () => {
      if (!ext) return detail;
      const caseItem = cases[caseIndex];
      if (!caseItem) return detail;

      const updates = updatesForCase(caseIndex, caseItem);
      if (Object.keys(updates).length === 0) {
        return detail;
      }

      await api.editEntities(detail.id, updates, caseIndex);
      return api.getFile(detail.id);
    }, "Case updates saved successfully.");

  const saveFieldEdit = (caseIndex: number, fieldName: string, caseItem: ExtractionCase) =>
    run(`save-field-${caseIndex}:${fieldName}`, async () => {
      if (!ext) return detail;
      const draftKey = `${caseIndex}:${fieldName}`;
      const currentValue = caseItem.entities?.[fieldName]?.value ?? "";
      const draftValue = drafts[draftKey] ?? "";
      const normalizedCurrent = normalizedEntityValue(fieldName, currentValue);
      const normalizedDraft = normalizedEntityValue(fieldName, draftValue);
      if (normalizedDraft === normalizedCurrent) {
        return detail;
      }

      await api.editEntities(detail.id, { [fieldName]: normalizedDraft }, caseIndex);
      return api.getFile(detail.id);
    }, `${FIELD_LABELS[fieldName] || fieldName} saved successfully.`);

  const requestApproval = () => {
    if (!ext) return;
    const missingFields = val?.missing_fields || [];
    if (missingFields.length > 0) {
      setApprovePrompt({
        classification: ext.request_type,
        missingFields,
      });
      return;
    }
    openActionPrompt("approve");
  };

  const openActionPrompt = (action: "ask" | "reject" | "approve") => {
    setActionPrompt({ action, note: "" });
  };

  const confirmActionPrompt = async () => {
    if (!actionPrompt) return;
    const note = actionPrompt.note.trim();
    if (!note) {
      if (actionPrompt.action === "ask") {
        onError("Ask clarifications note is required.");
      } else if (actionPrompt.action === "approve") {
        onError("Approval note is required.");
      } else {
        onError("Rejection note is required.");
      }
      return;
    }

    const { action } = actionPrompt;
    setActionPrompt(null);
    if (action === "ask") {
      await run(
        "ask",
        () => api.askClarifications(detail.id, note),
        "Request marked for clarification successfully."
      );
    } else if (action === "approve") {
      await run("approve", () => api.approve(detail.id, note), "Request approved successfully.");
    } else {
      await run("reject", () => api.reject(detail.id, note), "Request rejected successfully.");
    }
  };

  const confirmCaseApproval = async () => {
    if (!approvePrompt) return;
    setApprovePrompt(null);
    openActionPrompt("approve");
  };

  if (!ext) {
    return (
      <div className="placeholder compact">
        <h2>No extraction available</h2>
        <p>Run validation and extraction first to review structured output.</p>
      </div>
    );
  }

  if (isNotCollateral(ext)) {
    return (
      <div className="placeholder compact non-collateral-state">
        <h2>Not a collateral request</h2>
        <p className="non-collateral-intro">
          This email does not contain a collateral operations instruction, so no extraction fields
          were produced.
        </p>
        {ext.summary && <p className="summary-line non-collateral-summary">{ext.summary}</p>}
      </div>
    );
  }

  const overallPercent = Math.round(ext.overall_confidence * 100);
  const rag = overallConfidenceRag(overallPercent);
  const multiCase = cases.length > 1;

  return (
    <div className="review-table-shell">
      <div className="review-table-head">
        <div className="review-title-block">
          <h2>Case: {ext.request_type}</h2>
          <p>{ext.summary}</p>
          <div className="confidence-row">
            <div className={`case-confidence-rag ${rag}`}>
              <span className="rag-dot" />
              <span className="rag-label">Overall confidence</span>
              <strong>{overallPercent}%</strong>
              <span className="tone-chip">{ext.customer_tone}</span>
            </div>
            {ext.multiple_requests_detected && (
              <span className="case-count-chip">Multiple requests detected ({cases.length})</span>
            )}
          </div>
        </div>
      </div>

      <details className="preview-block" style={{ marginTop: 4 }}>
        <summary>Preview extracted body</summary>
        <pre>{detail.raw_email?.body?.trim() || "No parsed email body available for this record."}</pre>
      </details>

      {caseRows.map(({ caseItem, caseIndex, visibleRows, showUnavailable, hiddenCount, hasEditedRows }) => {
        const caseValidation = val?.cases?.find((c) => c.case_index === caseIndex);
        const decision = caseItem.decision_status || "Pending Review";
        const caseLocked = LOCKED_DECISION_STATES.has(decision);
        return (
          <section className="case-section" key={`case-${caseIndex}`}>
            <div className="case-section-head">
              <h3>
                Case {caseIndex + 1}: {caseItem.request_type}
              </h3>
              <div className="case-section-meta">
                <span>
                  {Math.round((caseItem.overall_confidence || 0) * 100)}% confidence
                </span>
                <span className="case-decision-chip">{decision}</span>
                {caseValidation && <span className="case-status-chip">{caseValidation.status}</span>}
                <button
                  className="toggle-link"
                  onClick={() =>
                    setShowUnavailableByCase((prev) => ({
                      ...prev,
                      [caseIndex]: !showUnavailable,
                    }))
                  }
                >
                  {showUnavailable
                    ? "Hide unavailable fields"
                    : `Show unavailable fields${hiddenCount ? ` (${hiddenCount})` : ""}`}
                </button>
              </div>
            </div>
            {caseItem.summary && <p className="case-summary">{caseItem.summary}</p>}

            <div className="review-table-wrap">
              <table className="review-grid-table">
                <colgroup>
                  <col className="col-key" />
                  <col className="col-value" />
                  <col className="col-confidence" />
                  <col className="col-evidence" />
                  <col className="col-edit" />
                </colgroup>
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Value</th>
                    <th>Confidence</th>
                    <th>Evidence</th>
                    <th>Edit</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row) => {
                    const confPercent = Math.round((row.field.confidence || 0) * 100);
                    return (
                      <tr key={`${caseIndex}:${row.key}`}>
                        <td className="key-cell">
                          <div className="field-name">{FIELD_LABELS[row.key] || row.key}</div>
                          <div className="field-meta">{row.mandatory ? "Mandatory" : "Optional"}</div>
                        </td>
                        <td className="value-cell">
                          {row.absent
                            ? "Not extracted"
                            : row.key === "amount"
                              ? formatAmountDisplay(row.field.value)
                              : row.field.value}
                        </td>
                        <td className="confidence-cell">
                          <div className="table-confidence">
                            <div className="table-confidence-bar">
                              <span style={{ width: `${confPercent}%` }} />
                            </div>
                            <div>
                              {confPercent}% ({confidenceBand(row.field.confidence || 0)})
                            </div>
                          </div>
                        </td>
                        <td className="mono evidence-cell">{renderEvidenceCell(row.field.evidence)}</td>
                        <td className="edit-cell">
                          <div className="inline-edit-control">
                            <input
                              className="table-edit-input"
                              value={drafts[row.draftKey] ?? ""}
                              onChange={(e) =>
                                setDrafts((d) => ({
                                  ...d,
                                  [row.draftKey]: e.target.value,
                                }))
                              }
                              onBlur={() => {
                                if (row.key !== "amount") return;
                                setDrafts((d) => ({
                                  ...d,
                                  [row.draftKey]: formatAmountDisplay(d[row.draftKey] ?? ""),
                                }));
                              }}
                              disabled={requestLocked || busy !== null || caseLocked}
                            />
                            <button
                              type="button"
                              className="field-save-btn"
                              title={`Save ${FIELD_LABELS[row.key] || row.key}`}
                              aria-label={`Save ${FIELD_LABELS[row.key] || row.key}`}
                              onClick={() => saveFieldEdit(caseIndex, row.key, caseItem)}
                              disabled={
                                requestLocked ||
                                busy !== null ||
                                caseLocked ||
                                !hasFieldUpdate(caseIndex, row.key, caseItem)
                              }
                            >
                              {busy === `save-field-${caseIndex}:${row.key}` ? "..." : "✓"}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {hasEditedRows && <div className="edit-remark">Edited by operations user</div>}

            {caseItem.clarification_draft && (
              <details className="preview-block" style={{ marginTop: 8 }}>
                <summary>Suggested Clarification Email</summary>
                <pre>{caseItem.clarification_draft}</pre>
              </details>
            )}
          </section>
        );
      })}

      {detail.clarification_draft && (
        <details className="preview-block" style={{ marginTop: 12 }}>
          <summary>Suggested Clarification Email</summary>
          <pre>{detail.clarification_draft}</pre>
        </details>
      )}

      {approvePrompt && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Approve with missing mandatory fields">
          <div className="modal-card">
            <h3>Approve with missing details?</h3>
            <p>
              This is a <strong>{approvePrompt.classification}</strong> case and needs the
              following mandatory fields prior to approval:
            </p>
            <ul className="modal-list">
              {approvePrompt.missingFields.map((field) => (
                <li key={field}>{field}</li>
              ))}
            </ul>
            <p>
              Do you want to approve anyway, or go back and provide the missing details first?
            </p>
            <div className="modal-actions">
              <button className="btn approve" onClick={confirmCaseApproval} disabled={busy !== null}>
                Approve
              </button>
              <button className="btn cancel" onClick={() => setApprovePrompt(null)} disabled={busy !== null}>
                Go back
              </button>
            </div>
          </div>
        </div>
      )}

      {actionPrompt && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Provide action note">
          <div className="modal-card">
            <h3>
              {actionPrompt.action === "ask"
                ? "Add clarification note"
                : actionPrompt.action === "approve"
                  ? "Add approval note"
                  : "Add rejection note"}
            </h3>
            <p>
              {actionPrompt.action === "ask"
                ? "A note is mandatory before sending this item to Ask Clarifications."
                : actionPrompt.action === "approve"
                  ? "A note is mandatory before approving this item."
                : "A note is mandatory before rejecting this item."}
            </p>
            <textarea
              className="modal-note"
              value={actionPrompt.note}
              onChange={(e) =>
                setActionPrompt((prev) => (prev ? { ...prev, note: e.target.value } : prev))
              }
              placeholder="Enter your note"
              rows={4}
              autoFocus
            />
            <div className="modal-actions">
              <button className="btn save" onClick={confirmActionPrompt} disabled={busy !== null}>
                Continue
              </button>
              <button className="btn cancel" onClick={() => setActionPrompt(null)} disabled={busy !== null}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {successNotice && (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Action success notification"
          onClick={() => setSuccessNotice(null)}
        >
          <div className="modal-card notice-card" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="notice-close"
              aria-label="Close notification"
              onClick={() => setSuccessNotice(null)}
            >
              ×
            </button>
            <h3>{successNotice.title}</h3>
            <p>{successNotice.message}</p>
          </div>
        </div>
      )}

      <div className="review-actions-row">
        <div className="review-actions">
          <button
            className="btn approve"
            disabled={
              requestLocked ||
              busy !== null ||
              detail.status === "Not a collateral request"
            }
            onClick={requestApproval}
          >
            {busy === "approve" ? "Approving..." : multiCase ? "Approve Case" : "Approve"}
          </button>
          <button
            className="btn ask"
            disabled={requestLocked || busy !== null}
            onClick={() => openActionPrompt("ask")}
          >
            {busy === "ask" ? "Drafting..." : "Ask Clarifications"}
          </button>
          <button
            className="btn reject"
            disabled={requestLocked || busy !== null}
            onClick={() => openActionPrompt("reject")}
          >
            {multiCase ? "Reject Case" : "Reject"}
          </button>
        </div>
      </div>
    </div>
  );
}
