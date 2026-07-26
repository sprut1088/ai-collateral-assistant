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
  instruction_details: "Instruction details",
};

const MANDATORY_BY_TYPE: Record<string, string[]> = {
  "Margin Call": ["counterparty", "amount", "currency"],
  "Collateral Substitution": ["amount", "currency", "collateral_type", "replacement_asset"],
  "Collateral Transfer": ["counterparty", "amount", "currency", "account"],
  "Settlement Instruction": ["counterparty", "amount", "currency", "value_date"],
  Dispute: ["counterparty", "amount", "currency"],
  "Exposure Inquiry": ["counterparty"],
  "General Inquiry": [],
};

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
    scope: "request" | "case";
    caseIndex: number;
    classification: string;
    missingFields: string[];
  } | null>(null);
  const [actionPrompt, setActionPrompt] = useState<{
    scope: "request" | "case";
    action: "ask" | "reject";
    caseIndex: number;
    note: string;
  } | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [showUnavailable, setShowUnavailable] = useState(false);

  const ext = detail.extraction;
  const val = detail.validation;
  const terminal =
    detail.status === "Approved" ||
    detail.status === "Rejected" ||
    detail.status === "Not a collateral request";

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
        const raw = field.value ?? "";
        next[`${caseIndex}:${key}`] = key === "amount" ? formatAmountDisplay(raw) : raw;
      }
    });
    setDrafts(next);
  }, [detail.id, ext, cases]);

  const caseRows = useMemo(() => {
    return cases.map((caseItem, caseIndex) => {
      const mandatory = new Set(MANDATORY_BY_TYPE[caseItem.request_type] || []);
      const rows = Object.entries(caseItem.entities || {}).map(([key, field]) => {
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
        rows,
        visibleRows: rows.filter((r) => showUnavailable || !r.absent),
      };
    });
  }, [cases, showUnavailable]);

  const hiddenCount = caseRows.reduce(
    (acc, item) => acc + (item.rows.length - item.visibleRows.length),
    0
  );

  const run = async (label: string, fn: () => Promise<RequestDetail>) => {
    setBusy(label);
    onError(null);
    try {
      onChange(await fn());
    } catch (e: any) {
      onError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const saveEdit = () =>
    run("save", async () => {
      if (!ext) return detail;
      let hasChanges = false;

      for (const [caseIndex, caseItem] of cases.entries()) {
        const updates: Record<string, string> = {};
        for (const [fieldName, field] of Object.entries(caseItem.entities || {})) {
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
        if (Object.keys(updates).length > 0) {
          hasChanges = true;
          await api.editEntities(detail.id, updates, caseIndex);
        }
      }

      if (!hasChanges) {
        return detail;
      }
      return api.getFile(detail.id);
    });

  const requestCaseApproval = (
    caseIndex: number,
    classification: string,
    missingFields: string[]
  ) => {
    if (missingFields.length > 0) {
      setApprovePrompt({ scope: "case", caseIndex, classification, missingFields });
      return;
    }
    run(`approve-${caseIndex}`, () => api.approveCase(detail.id, caseIndex));
  };

  const requestApproval = () => {
    if (!ext) return;
    const missingFields = val?.missing_fields || [];
    if (missingFields.length > 0) {
      setApprovePrompt({
        scope: "request",
        caseIndex: -1,
        classification: ext.request_type,
        missingFields,
      });
      return;
    }
    run("approve", () => api.approve(detail.id));
  };

  const openActionPrompt = (
    scope: "request" | "case",
    action: "ask" | "reject",
    caseIndex = -1
  ) => {
    setActionPrompt({ scope, action, caseIndex, note: "" });
  };

  const confirmActionPrompt = async () => {
    if (!actionPrompt) return;
    const note = actionPrompt.note.trim();
    if (!note) {
      onError(actionPrompt.action === "ask" ? "Ask-customer note is required." : "Rejection note is required.");
      return;
    }

    const { scope, action, caseIndex } = actionPrompt;
    setActionPrompt(null);
    if (scope === "case") {
      if (action === "ask") {
        await run(`ask-${caseIndex}`, () => api.askCustomerCase(detail.id, caseIndex, note));
      } else {
        await run(`reject-${caseIndex}`, () => api.rejectCase(detail.id, caseIndex, note));
      }
      return;
    }

    if (action === "ask") {
      await run("ask", () => api.askCustomer(detail.id, note));
    } else {
      await run("reject", () => api.reject(detail.id, note));
    }
  };

  const confirmCaseApproval = async () => {
    if (!approvePrompt) return;
    const { caseIndex: idx, scope } = approvePrompt;
    setApprovePrompt(null);
    if (scope === "case") {
      await run(`approve-${idx}`, () => api.approveCase(detail.id, idx));
      return;
    }
    await run("approve", () => api.approve(detail.id));
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
            <button className="toggle-link" onClick={() => setShowUnavailable((v) => !v)}>
              {showUnavailable
                ? "Hide unavailable fields"
                : `Show unavailable fields${hiddenCount ? ` (${hiddenCount})` : ""}`}
            </button>
          </div>
        </div>
      </div>

      {caseRows.map(({ caseItem, caseIndex, visibleRows }) => {
        const caseValidation = val?.cases?.find((c) => c.case_index === caseIndex);
        const decision = caseItem.decision_status || "Pending Review";
        const caseTerminal = decision === "Approved" || decision === "Rejected";
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
                        <td className="mono evidence-cell">{row.field.evidence || "-"}</td>
                        <td className="edit-cell">
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
                            disabled={terminal || busy !== null}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {caseItem.clarification_draft && (
              <details className="preview-block" style={{ marginTop: 8 }}>
                <summary>Case clarification email draft</summary>
                <pre>{caseItem.clarification_draft}</pre>
              </details>
            )}

            {multiCase && (
              <div className="case-actions-row">
                <button
                  className="btn approve"
                  disabled={
                    terminal ||
                    busy !== null ||
                    caseTerminal
                  }
                  onClick={() =>
                    requestCaseApproval(
                      caseIndex,
                      caseItem.request_type,
                      caseValidation?.missing_fields || []
                    )
                  }
                >
                  {busy === `approve-${caseIndex}` ? "Approving..." : "Approve case"}
                </button>
                <button
                  className="btn ask"
                  disabled={terminal || busy !== null || caseTerminal}
                  onClick={() => openActionPrompt("case", "ask", caseIndex)}
                >
                  {busy === `ask-${caseIndex}` ? "Drafting..." : "Ask clarification"}
                </button>
                <button
                  className="btn reject"
                  disabled={terminal || busy !== null || caseTerminal}
                  onClick={() => openActionPrompt("case", "reject", caseIndex)}
                >
                  Reject case
                </button>
              </div>
            )}
          </section>
        );
      })}

      {detail.clarification_draft && (
        <details className="preview-block" style={{ marginTop: 12 }}>
          <summary>Clarification email draft</summary>
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
              {actionPrompt.action === "ask" ? "Add clarification note" : "Add rejection note"}
            </h3>
            <p>
              {actionPrompt.action === "ask"
                ? "A note is mandatory before sending this item to Ask Customer."
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

      <div className="review-actions-row">
        <div className="review-actions">
          <button className="btn save" disabled={terminal || busy !== null} onClick={saveEdit}>
            {busy === "save" ? "Saving..." : "Save changes"}
          </button>
          {!multiCase && (
            <>
              <button
                className="btn approve"
                disabled={
                  terminal ||
                  busy !== null ||
                  detail.status === "Not a collateral request"
                }
                onClick={requestApproval}
              >
                {busy === "approve" ? "Approving..." : "Approve"}
              </button>
              <button
                className="btn ask"
                disabled={terminal || busy !== null}
                onClick={() => openActionPrompt("request", "ask")}
              >
                {busy === "ask" ? "Drafting..." : "Ask clarification"}
              </button>
              <button
                className="btn reject"
                disabled={terminal || busy !== null}
                onClick={() => openActionPrompt("request", "reject")}
              >
                Reject
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
