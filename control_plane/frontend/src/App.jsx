import { useState, useEffect, useCallback } from "react";

// ── API client ────────────────────────────────────────────────────────────────
const API_BASE = window.location.hostname === "localhost"
  ? "http://localhost:8000"
  : "";  // same origin on Cloud Run

async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `API error ${res.status}`);
  }
  return res.json();
}

// ── Utility ───────────────────────────────────────────────────────────────────
const fmt = {
  date: (iso) => iso ? new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "—",
  time: (iso) => iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "",
  pct:  (n) => `${(+n).toFixed(1)}%`,
  currency: (n) => `$${(+n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
  reportLabel: (type) => ({
    weekly_supplier_overview:  "Weekly Overview",
    monthly_supplier_overview: "Monthly Overview",
    monthly_supplier_account:  "Supplier Account",
    adhoc_business:            "Ad-hoc Business",
    adhoc_supplier:            "Ad-hoc Supplier",
  }[type] || type),
};

const confidenceColor = (c) => c >= 0.85 ? "#22c55e" : c >= 0.75 ? "#f59e0b" : "#ef4444";

// ── Components ────────────────────────────────────────────────────────────────

function Badge({ children, variant = "default" }) {
  const styles = {
    default:  { background: "rgba(255,255,255,0.08)", color: "#94a3b8" },
    pending:  { background: "rgba(245,158,11,0.15)",  color: "#f59e0b" },
    approved: { background: "rgba(34,197,94,0.15)",   color: "#22c55e" },
    rejected: { background: "rgba(239,68,68,0.15)",   color: "#ef4444" },
    escalated:{ background: "rgba(168,85,247,0.15)",  color: "#a855f7" },
    business: { background: "rgba(59,130,246,0.15)",  color: "#60a5fa" },
    supplier: { background: "rgba(20,184,166,0.15)",  color: "#2dd4bf" },
    pass:     { background: "rgba(34,197,94,0.12)",   color: "#22c55e" },
    fail:     { background: "rgba(239,68,68,0.12)",   color: "#ef4444" },
  };
  const s = styles[variant] || styles.default;
  return (
    <span style={{
      ...s, padding: "2px 8px", borderRadius: "4px", fontSize: "11px",
      fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase", whiteSpace: "nowrap",
    }}>{children}</span>
  );
}

function ConfidenceMeter({ value }) {
  const color = confidenceColor(value);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ width: 80, height: 4, background: "rgba(255,255,255,0.1)", borderRadius: 2, overflow: "hidden" }}>
        <div style={{ width: `${value * 100}%`, height: "100%", background: color, borderRadius: 2 }} />
      </div>
      <span style={{ fontSize: 12, color, fontWeight: 700, fontFamily: "monospace" }}>{(value * 100).toFixed(0)}%</span>
    </div>
  );
}

function MetricCard({ label, value, sub }) {
  return (
    <div style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 8, padding: "14px 18px" }}>
      <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: "#f1f5f9", fontFamily: "monospace" }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: "#475569", marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

function Spinner() {
  return (
    <div style={{ display: "flex", justifyContent: "center", padding: 60 }}>
      <div style={{
        width: 24, height: 24, border: "2px solid rgba(255,255,255,0.1)",
        borderTop: "2px solid #60a5fa", borderRadius: "50%",
        animation: "spin 0.8s linear infinite",
      }} />
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

function ErrorBanner({ message, onRetry }) {
  return (
    <div style={{ padding: "12px 16px", background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.2)", borderRadius: 8, display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
      <span style={{ color: "#ef4444" }}>✗</span>
      <span style={{ fontSize: 13, color: "#fca5a5", flex: 1 }}>{message}</span>
      {onRetry && <button onClick={onRetry} style={{ background: "none", border: "1px solid rgba(239,68,68,0.3)", color: "#ef4444", borderRadius: 6, padding: "4px 12px", cursor: "pointer", fontSize: 12 }}>Retry</button>}
    </div>
  );
}

// ── View 1: Run Queue ─────────────────────────────────────────────────────────
function RunQueue({ onSelect }) {
  const [runs, setRuns]     = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch("/api/queue");
      setRuns(data.queue || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <Spinner />;

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, color: "#f1f5f9", margin: 0 }}>Pending Review</h2>
        <p style={{ fontSize: 13, color: "#64748b", margin: "4px 0 0" }}>{runs.length} report{runs.length !== 1 ? "s" : ""} awaiting decision</p>
      </div>

      {error && <ErrorBanner message={error} onRetry={load} />}

      <div style={{ display: "grid", gap: 12 }}>
        {runs.map((run) => (
          <div
            key={run.runID}
            onClick={() => onSelect(run)}
            style={{
              background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 10, padding: "18px 22px", cursor: "pointer",
              transition: "all 0.15s ease", display: "grid",
              gridTemplateColumns: "1fr auto", gap: 16, alignItems: "center",
            }}
            onMouseEnter={e => e.currentTarget.style.background = "rgba(255,255,255,0.06)"}
            onMouseLeave={e => e.currentTarget.style.background = "rgba(255,255,255,0.03)"}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontSize: 15, fontWeight: 600, color: "#e2e8f0" }}>{fmt.reportLabel(run.reportType)}</span>
                <Badge variant={run.audience}>{run.audience}</Badge>
                {run.supplierID && <Badge>{run.supplierID}</Badge>}
                <Badge variant="pending">Pending</Badge>
                {run.hallucinationFlags > 0 && <Badge variant="escalated">⚠ {run.hallucinationFlags} hallucination</Badge>}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 20, flexWrap: "wrap" }}>
                <ConfidenceMeter value={run.confidence || 0} />
                <span style={{ fontSize: 12, color: "#475569" }}>
                  {run.validationPassed}/{(run.validationPassed || 0) + (run.validationFailed || 0)} checks passed
                </span>
                <span style={{ fontSize: 12, color: "#475569" }}>
                  Queued {fmt.date(run.queuedAt)} at {fmt.time(run.queuedAt)}
                </span>
              </div>
              {run.softFailures?.length > 0 && (
                <div style={{ fontSize: 12, color: "#f59e0b" }}>⚠ {run.softFailures.join(" · ")}</div>
              )}
            </div>
            <div style={{ color: "#475569", fontSize: 13 }}>Review →</div>
          </div>
        ))}
        {!error && runs.length === 0 && (
          <div style={{ textAlign: "center", padding: 60, color: "#475569", fontSize: 14 }}>
            No pending reports. All caught up.
          </div>
        )}
      </div>
    </div>
  );
}

// ── View 2+3: Audit + Decision ────────────────────────────────────────────────
function AuditView({ runSummary, onDecision, onBack }) {
  const [run, setRun]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState(null);
  const [tab, setTab]       = useState("report");
  const [decision, setDecision] = useState(null);
  const [reason, setReason] = useState("");
  const [editedNarrative, setEditedNarrative] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted]   = useState(false);
  const [reviewer, setReviewer]     = useState("f.trindade");

  useEffect(() => {
    apiFetch(`/api/runs/${runSummary.runID}`)
      .then(data => {
        setRun(data);
        setEditedNarrative(data.reportNarrative || "");
        setLoading(false);
      })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [runSummary.runID]);

  const handleSubmit = async () => {
    if (!decision) return;
    if (decision === "rejected" && !reason.trim()) return;
    setSubmitting(true);
    try {
      await apiFetch("/api/decisions", {
        method: "POST",
        body: JSON.stringify({
          runID:           run.runID,
          decision,
          reviewer,
          reason:          reason.trim() || null,
          editedNarrative: decision === "edited_and_approved" ? editedNarrative : null,
        }),
      });
      setSubmitted(true);
      onDecision({ runID: run.runID, decision });
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <Spinner />;
  if (error && !run) return <ErrorBanner message={error} onRetry={() => window.location.reload()} />;

  if (submitted) {
    return (
      <div style={{ textAlign: "center", padding: "80px 40px" }}>
        <div style={{ fontSize: 48, marginBottom: 16 }}>{decision === "rejected" ? "✗" : "✓"}</div>
        <div style={{ fontSize: 20, fontWeight: 700, color: "#f1f5f9", marginBottom: 8 }}>
          {decision === "approved" ? "Report Approved" : decision === "edited_and_approved" ? "Edited & Approved" : "Report Rejected"}
        </div>
        <div style={{ fontSize: 14, color: "#64748b", marginBottom: 32 }}>Decision recorded. Run ID: {run.runID.slice(0, 8)}...</div>
        <button onClick={onBack} style={{ ...btnStyle, background: "rgba(255,255,255,0.08)", color: "#94a3b8", width: "auto", padding: "10px 24px" }}>← Back to Queue</button>
      </div>
    );
  }

  const tabs = [
    { id: "report",     label: "Report" },
    { id: "validation", label: `Validation (${run?.validationPassed || 0}/${(run?.validationPassed || 0) + (run?.validationFailed || 0)})` },
    { id: "policy",     label: "Policy" },
    { id: "data",       label: "Data" },
  ];

  const policyOutcome = run?.policyOutcome || runSummary.policyOutcome || {};
  const policyRules   = policyOutcome?.rule_results || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 24, flexWrap: "wrap" }}>
        <button onClick={onBack} style={{ background: "none", border: "none", color: "#64748b", cursor: "pointer", fontSize: 13, padding: 0 }}>← Queue</button>
        <div style={{ width: 1, height: 16, background: "rgba(255,255,255,0.1)" }} />
        <span style={{ fontSize: 16, fontWeight: 700, color: "#f1f5f9" }}>{fmt.reportLabel(run?.reportType)}</span>
        <Badge variant={run?.audience}>{run?.audience}</Badge>
        {run?.supplierID && <Badge>{run.supplierID}</Badge>}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
          <ConfidenceMeter value={run?.confidence || 0} />
          <span style={{ fontSize: 12, color: "#64748b" }}>
            {run?.validationPassed}/{(run?.validationPassed || 0) + (run?.validationFailed || 0)} checks
          </span>
        </div>
      </div>

      {error && <ErrorBanner message={error} />}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 20 }}>
        {/* Left panel */}
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", gap: 2, borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
            {tabs.map(t => (
              <button key={t.id} onClick={() => setTab(t.id)} style={{
                background: "none", border: "none",
                borderBottom: tab === t.id ? "2px solid #60a5fa" : "2px solid transparent",
                color: tab === t.id ? "#60a5fa" : "#64748b",
                padding: "10px 16px", cursor: "pointer", fontSize: 13,
                fontWeight: tab === t.id ? 600 : 400, transition: "all 0.15s",
              }}>{t.label}</button>
            ))}
          </div>

          <div style={{
            background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.06)",
            borderTop: "none", borderRadius: "0 0 10px 10px", padding: 20,
            overflowY: "auto", maxHeight: 560,
          }}>
            {tab === "report" && (
              decision === "edited_and_approved" ? (
                <textarea
                  value={editedNarrative}
                  onChange={e => setEditedNarrative(e.target.value)}
                  style={{
                    width: "100%", minHeight: 480,
                    background: "rgba(255,255,255,0.04)", border: "1px solid rgba(96,165,250,0.3)",
                    borderRadius: 8, padding: 16, color: "#e2e8f0", fontSize: 13,
                    lineHeight: 1.7, fontFamily: "monospace", resize: "vertical", boxSizing: "border-box",
                  }}
                />
              ) : (
                <div style={{ fontSize: 13, lineHeight: 1.8, color: "#cbd5e1", whiteSpace: "pre-wrap", fontFamily: "'Georgia', serif" }}>
                  {run?.reportNarrative || "No narrative available."}
                </div>
              )
            )}

            {tab === "validation" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 16 }}>
                  <MetricCard label="Passed" value={run?.validationPassed || 0} />
                  <MetricCard label="Failed" value={run?.validationFailed || 0} />
                  <MetricCard label="Hallucinations" value={run?.hallucinationFlags || 0} />
                  <MetricCard label="Pass Rate" value={
                    run?.validationPassed != null
                      ? fmt.pct(run.validationPassed / Math.max((run.validationPassed + run.validationFailed), 1) * 100)
                      : "—"
                  } />
                </div>
                {(run?.validationResults || []).map((r, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "flex-start", gap: 10, padding: "10px 14px",
                    background: r.passed ? "rgba(34,197,94,0.05)" : "rgba(239,68,68,0.05)",
                    border: `1px solid ${r.passed ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)"}`,
                    borderRadius: 6,
                  }}>
                    <span style={{ color: r.passed ? "#22c55e" : "#ef4444", fontSize: 14, marginTop: 1 }}>{r.passed ? "✓" : "✗"}</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "#e2e8f0", fontFamily: "monospace" }}>{r.metricName}</div>
                      {r.expectedValue != null && (
                        <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>
                          Expected: <span style={{ color: "#94a3b8" }}>{typeof r.expectedValue === "number" ? r.expectedValue.toLocaleString() : r.expectedValue}</span>
                          {" · "}Reported: <span style={{ color: "#94a3b8" }}>{typeof r.reportedValue === "number" ? r.reportedValue.toLocaleString() : r.reportedValue}</span>
                          {r.deviationPct != null && (
                            <> · Dev: <span style={{ color: r.deviationPct > 10 ? "#ef4444" : "#22c55e" }}>{(+r.deviationPct).toFixed(1)}%</span></>
                          )}
                        </div>
                      )}
                      {r.details && <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>{r.details}</div>}
                      {r.hallucinationFlag && <span style={{ marginTop: 4, display: "inline-block" }}><Badge variant="escalated">Hallucination candidate</Badge></span>}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {tab === "policy" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16, padding: "12px 16px", background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.2)", borderRadius: 8 }}>
                  <span style={{ fontSize: 14, color: "#f59e0b" }}>⚡</span>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "#f59e0b" }}>
                      Policy Decision: {(policyOutcome.decision || "ROUTE_TO_QUEUE").toUpperCase().replace("_", " ")}
                    </div>
                    <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>
                      {policyOutcome.rules_passed}/{policyOutcome.rules_evaluated} rules passed
                    </div>
                  </div>
                </div>
                {policyRules.map((r, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
                    background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 6,
                  }}>
                    <span style={{ color: r.passed ? "#22c55e" : "#f59e0b", fontSize: 14 }}>{r.passed ? "✓" : "✗"}</span>
                    <div style={{ flex: 1 }}>
                      <span style={{ fontSize: 12, fontWeight: 600, color: "#e2e8f0", fontFamily: "monospace" }}>{r.rule}</span>
                      {!r.passed && r.message && <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>{r.message}</div>}
                    </div>
                    <div style={{ fontSize: 11, color: "#64748b", fontFamily: "monospace" }}>
                      {r.actual} {r.threshold && `/ ${r.threshold}`}
                    </div>
                    <Badge variant={r.passed ? "pass" : "fail"}>{r.passed ? "pass" : "fail"}</Badge>
                  </div>
                ))}
                {policyRules.length === 0 && (
                  <div style={{ fontSize: 13, color: "#475569", padding: 20, textAlign: "center" }}>
                    Policy outcome not available for this run.
                  </div>
                )}
              </div>
            )}

            {tab === "data" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
                  {Object.entries(run?.rowCounts || {}).map(([table, count]) => (
                    <MetricCard key={table} label={table} value={(+count).toLocaleString()} sub="rows" />
                  ))}
                </div>
                {Object.entries(run?.queries || {}).map(([table, sql]) => (
                  <div key={table}>
                    <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6 }}>{table} query</div>
                    <pre style={{
                      background: "rgba(0,0,0,0.3)", border: "1px solid rgba(255,255,255,0.06)",
                      borderRadius: 6, padding: 12, fontSize: 11, color: "#94a3b8",
                      overflow: "auto", whiteSpace: "pre-wrap", margin: 0, fontFamily: "monospace",
                    }}>{typeof sql === "string" ? sql : JSON.stringify(sql, null, 2)}</pre>
                  </div>
                ))}
                {(run?.flags || []).length > 0 && (
                  <div>
                    <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>Agent Flags</div>
                    {(run.flags).map((f, i) => (
                      <div key={i} style={{ fontSize: 12, color: "#f59e0b", padding: "6px 0", borderBottom: "1px solid rgba(255,255,255,0.04)" }}>⚠ {f}</div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Right panel — decision */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.06em" }}>Decision</div>

          {[
            { id: "approved",            label: "Approve",         desc: "Publish as generated",       color: "#22c55e" },
            { id: "edited_and_approved", label: "Edit & Approve",  desc: "Modify report then publish", color: "#60a5fa" },
            { id: "rejected",            label: "Reject",          desc: "Send back — reason required",color: "#ef4444" },
          ].map(opt => (
            <div key={opt.id} onClick={() => setDecision(opt.id)} style={{
              padding: "14px 16px",
              border: `1px solid ${decision === opt.id ? opt.color : "rgba(255,255,255,0.08)"}`,
              borderRadius: 8, cursor: "pointer",
              background: decision === opt.id ? `${opt.color}18` : "rgba(255,255,255,0.02)",
              transition: "all 0.15s",
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: decision === opt.id ? opt.color : "#e2e8f0" }}>{opt.label}</div>
              <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>{opt.desc}</div>
            </div>
          ))}

          <div>
            <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 6 }}>Reviewer</div>
            <input
              value={reviewer}
              onChange={e => setReviewer(e.target.value)}
              style={{
                width: "100%", background: "rgba(255,255,255,0.04)",
                border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6,
                padding: "8px 10px", color: "#e2e8f0", fontSize: 12,
                fontFamily: "inherit", boxSizing: "border-box",
              }}
            />
          </div>

          {(decision === "rejected" || decision === "edited_and_approved") && (
            <div>
              <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 6 }}>
                {decision === "rejected" ? "Rejection reason *" : "Edit notes (optional)"}
              </div>
              <textarea
                value={reason}
                onChange={e => setReason(e.target.value)}
                placeholder={decision === "rejected" ? "What needs to change?" : "Notes on edits made..."}
                style={{
                  width: "100%", minHeight: 80,
                  background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: 6, padding: 10, color: "#e2e8f0", fontSize: 12,
                  resize: "vertical", fontFamily: "inherit", boxSizing: "border-box",
                }}
              />
            </div>
          )}

          <button
            onClick={handleSubmit}
            disabled={submitting || !decision || (decision === "rejected" && !reason.trim())}
            style={{
              ...btnStyle, marginTop: 8,
              background: decision === "approved" ? "rgba(34,197,94,0.2)"
                : decision === "edited_and_approved" ? "rgba(96,165,250,0.2)"
                : decision === "rejected" ? "rgba(239,68,68,0.2)"
                : "rgba(255,255,255,0.06)",
              color: decision === "approved" ? "#22c55e"
                : decision === "edited_and_approved" ? "#60a5fa"
                : decision === "rejected" ? "#ef4444"
                : "#475569",
              opacity: (submitting || !decision || (decision === "rejected" && !reason.trim())) ? 0.4 : 1,
              cursor: (submitting || !decision || (decision === "rejected" && !reason.trim())) ? "not-allowed" : "pointer",
              border: "1px solid currentColor",
            }}
          >
            {submitting ? "Recording..." : "Confirm Decision"}
          </button>

          <div style={{ marginTop: 8, padding: 12, background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 8 }}>
            <div style={{ fontSize: 11, color: "#475569", lineHeight: 1.6 }}>
              Run ID: <span style={{ fontFamily: "monospace", color: "#64748b" }}>{run?.runID?.slice(0, 16)}...</span><br />
              Queued: {fmt.date(runSummary.queuedAt)} {fmt.time(runSummary.queuedAt)}<br />
              Policy: {runSummary.policyDecision}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── View 4: Observability ─────────────────────────────────────────────────────
function ObservabilityDashboard() {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch("/api/history");
      setHistory(data.history || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <Spinner />;

  const approved      = history.filter(r => ["approved", "edited_and_approved", "auto_approved"].includes(r.decision)).length;
  const rejected      = history.filter(r => r.decision === "rejected").length;
  const autoApproved  = history.filter(r => r.decision === "auto_approved").length;
  const avgConf       = history.length ? history.reduce((s, r) => s + (r.confidence || 0), 0) / history.length : 0;

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, color: "#f1f5f9", margin: 0 }}>Observability</h2>
        <p style={{ fontSize: 13, color: "#64748b", margin: "4px 0 0" }}>Run history and system health</p>
      </div>

      {error && <ErrorBanner message={error} onRetry={load} />}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 24 }}>
        <MetricCard label="Total Runs" value={history.length} />
        <MetricCard label="Approved" value={approved} sub={`${autoApproved} auto-approved`} />
        <MetricCard label="Rejected" value={rejected} />
        <MetricCard label="Avg Confidence" value={fmt.pct(avgConf * 100)} />
      </div>

      <div style={{ fontSize: 13, fontWeight: 600, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 12 }}>Run History</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {history.map((run) => (
          <div key={run.runID} style={{
            display: "grid", gridTemplateColumns: "1fr auto auto auto",
            gap: 16, alignItems: "center", padding: "12px 16px",
            background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 8,
          }}>
            <div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: "#e2e8f0" }}>{fmt.reportLabel(run.reportType)}</span>
                {run.supplierID && <Badge>{run.supplierID}</Badge>}
              </div>
              <div style={{ fontSize: 11, color: "#475569", marginTop: 3 }}>
                {fmt.date(run.decidedAt || run.startedAt)} · {run.reviewer || "system"}
              </div>
            </div>
            <ConfidenceMeter value={run.confidence || 0} />
            <Badge variant={run.decision === "rejected" ? "rejected" : "approved"}>
              {run.decision === "auto_approved" ? "Auto" : run.decision === "edited_and_approved" ? "Edited" : run.decision || run.status}
            </Badge>
            <span style={{ fontSize: 11, fontFamily: "monospace", color: "#475569" }}>{(run.runID || "").slice(0, 8)}</span>
          </div>
        ))}
        {!error && history.length === 0 && (
          <div style={{ textAlign: "center", padding: 60, color: "#475569", fontSize: 14 }}>No run history yet.</div>
        )}
      </div>
    </div>
  );
}

const btnStyle = {
  padding: "10px 18px", borderRadius: 7, border: "none",
  fontSize: 13, fontWeight: 600, cursor: "pointer",
  transition: "all 0.15s", width: "100%",
};

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
  const [view, setView]       = useState("queue");
  const [selected, setSelected] = useState(null);
  const [queueCount, setQueueCount] = useState(null);
  const [decisions, setDecisions]   = useState({});

  // Load queue count for nav badge
  useEffect(() => {
    apiFetch("/api/queue")
      .then(data => setQueueCount(data.total || 0))
      .catch(() => setQueueCount(null));
  }, [decisions]);

  const handleDecision = (dec) => {
    setDecisions(prev => ({ ...prev, [dec.runID]: dec }));
  };

  const navItems = [
    { id: "queue",         label: "Queue",         badge: queueCount },
    { id: "observability", label: "Observability", badge: null },
  ];

  return (
    <div style={{ minHeight: "100vh", background: "#0a0e1a", color: "#e2e8f0", fontFamily: "'DM Sans', 'Helvetica Neue', sans-serif" }}>
      <div style={{
        borderBottom: "1px solid rgba(255,255,255,0.07)", padding: "0 28px",
        display: "flex", alignItems: "center", gap: 0, height: 52,
        background: "rgba(255,255,255,0.02)", position: "sticky", top: 0, zIndex: 100,
      }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: "#f1f5f9", letterSpacing: "-0.02em", marginRight: 32 }}>
          BI Agent <span style={{ color: "#3b82f6" }}>Control</span>
        </div>
        {navItems.map(item => (
          <button key={item.id} onClick={() => { setView(item.id); setSelected(null); }} style={{
            background: "none", border: "none",
            borderBottom: view === item.id ? "2px solid #3b82f6" : "2px solid transparent",
            color: view === item.id ? "#60a5fa" : "#64748b",
            padding: "0 16px", height: "100%", cursor: "pointer", fontSize: 13,
            fontWeight: view === item.id ? 600 : 400,
            display: "flex", alignItems: "center", gap: 8, transition: "all 0.15s",
          }}>
            {item.label}
            {item.badge != null && (
              <span style={{ background: "#3b82f6", color: "#fff", borderRadius: "10px", padding: "1px 7px", fontSize: 11, fontWeight: 700 }}>
                {item.badge}
              </span>
            )}
          </button>
        ))}
        <div style={{ marginLeft: "auto", fontSize: 12, color: "#334155" }}>
          {new Date().toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" })}
        </div>
      </div>

      <div style={{ padding: "28px", maxWidth: 1200, margin: "0 auto" }}>
        {view === "queue" && !selected && (
          <RunQueue onSelect={(run) => { setSelected(run); setView("audit"); }} />
        )}
        {view === "audit" && selected && (
          <AuditView
            runSummary={selected}
            onDecision={(dec) => { handleDecision(dec); }}
            onBack={() => { setSelected(null); setView("queue"); }}
          />
        )}
        {view === "observability" && (
          <ObservabilityDashboard />
        )}
      </div>
    </div>
  );
}
