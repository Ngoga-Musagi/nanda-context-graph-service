import { useState } from "react";

const API = import.meta.env.VITE_API_URL || (
  window.location.port === "5173" ? "http://localhost:7201" : ""
);

export default function App() {
  const [agentId, setAgentId] = useState("");
  const [decision, setDecision] = useState(null);
  const [history, setHistory] = useState([]);
  const [causalChain, setCausalChain] = useState(null);
  const [selectedTrace, setSelectedTrace] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  async function fetchWhy() {
    if (!agentId.trim()) return;
    setLoading(true);
    setError(null);
    setCausalChain(null);
    setSelectedTrace(null);
    try {
      const [whyRes, histRes] = await Promise.all([
        fetch(`${API}/api/v1/why?agent_id=${encodeURIComponent(agentId)}`),
        fetch(`${API}/api/v1/agent/${encodeURIComponent(agentId)}/history?limit=20`),
      ]);
      if (whyRes.ok) {
        setDecision(await whyRes.json());
      } else {
        setDecision(null);
        setError(`No traces found for agent "${agentId}"`);
      }
      if (histRes.ok) {
        const data = await histRes.json();
        setHistory(data.traces || []);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function fetchTrace(traceId) {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/v1/trace/${traceId}`);
      if (r.ok) {
        setSelectedTrace(await r.json());
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function fetchCausalChain(traceId) {
    setLoading(true);
    setCausalChain(null);
    try {
      const r = await fetch(`${API}/api/v1/chain/${traceId}/causal`);
      if (r.ok) {
        const data = await r.json();
        const chain = data.chain || [];
        // Fetch full trace for each node in the chain
        const traces = await Promise.all(
          chain.map(async (tid) => {
            const tr = await fetch(`${API}/api/v1/trace/${tid}`);
            return tr.ok ? await tr.json() : { trace_id: tid, agent_id: "?", steps: [] };
          })
        );
        setCausalChain(traces);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <h1 style={styles.title}>NANDA Context Graph</h1>
        <p style={styles.subtitle}>Decision Trace Explorer</p>
      </div>

      <div style={styles.controls}>
        <input
          style={styles.input}
          type="text"
          placeholder="Enter Agent ID (e.g. rental-broker)"
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && fetchWhy()}
        />
        <button style={styles.button} onClick={fetchWhy} disabled={loading}>
          {loading ? "Loading..." : "Why did this agent act?"}
        </button>
      </div>

      <div style={styles.quickLinks}>
        {["rental-broker", "rental-pricing", "rental-approval"].map((id) => (
          <button
            key={id}
            style={styles.chipButton}
            onClick={() => { setAgentId(id); setTimeout(() => {
              document.querySelector("button[class]")?.click();
            }, 50); setAgentId(id); }}
            onClickCapture={() => { setAgentId(id); }}
          >
            @{id}
          </button>
        ))}
      </div>

      {error && <div style={styles.error}>{error}</div>}

      {/* Causal Chain View */}
      {causalChain && <CausalChainView chain={causalChain} onClose={() => setCausalChain(null)} />}

      {/* Selected Trace Detail */}
      {selectedTrace && !causalChain && (
        <TraceDetail
          trace={selectedTrace}
          onClose={() => setSelectedTrace(null)}
          onChain={() => fetchCausalChain(selectedTrace.trace_id)}
        />
      )}

      {/* Latest Decision */}
      {decision && !selectedTrace && !causalChain && (
        <DecisionTree
          decision={decision}
          onChain={() => fetchCausalChain(decision.decision.trace_id)}
        />
      )}

      {/* History Table */}
      {history.length > 0 && (
        <HistoryTable
          traces={history}
          onSelect={(tid) => fetchTrace(tid)}
          onChain={(tid) => fetchCausalChain(tid)}
        />
      )}
    </div>
  );
}

/* ── Causal Chain View ───────────────────────────────────────── */

function CausalChainView({ chain, onClose }) {
  return (
    <div style={styles.card}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={styles.cardTitle}>Causal Chain ({chain.length} hops)</h2>
        <button style={styles.closeButton} onClick={onClose}>Close</button>
      </div>
      <p style={styles.muted}>
        Follow the chain from the latest decision back to the root trigger.
        Each node shows the agent's reasoning at that step.
      </p>
      {chain.map((trace, i) => (
        <div key={trace.trace_id} style={{ marginBottom: 16 }}>
          <div style={styles.chainNode}>
            <div style={styles.chainHeader}>
              <span style={styles.chainIndex}>Hop {i + 1}</span>
              <strong>{trace.agent_id}</strong>
              <Badge outcome={trace.outcome} />
              {trace.duration_ms != null && (
                <span style={styles.muted}>{trace.duration_ms}ms</span>
              )}
            </div>
            <div style={{ fontSize: 12, color: "#6b7280", marginBottom: 8 }}>
              {trace.trace_id}
            </div>
            {(trace.steps || []).map((s, j) => (
              <StepItem key={s.step_id || j} step={s} index={j} defaultOpen={true} />
            ))}
            {(!trace.steps || trace.steps.length === 0) && (
              <p style={styles.muted}>No reasoning steps recorded.</p>
            )}
          </div>
          {i < chain.length - 1 && (
            <div style={styles.chainArrow}>PRECEDED BY</div>
          )}
        </div>
      ))}
    </div>
  );
}

/* ── Trace Detail View ───────────────────────────────────────── */

function TraceDetail({ trace, onClose, onChain }) {
  return (
    <div style={styles.card}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={styles.cardTitle}>Trace Detail</h2>
        <div style={{ display: "flex", gap: 8 }}>
          <button style={styles.chainButton} onClick={onChain}>View Causal Chain</button>
          <button style={styles.closeButton} onClick={onClose}>Close</button>
        </div>
      </div>
      <Row label="Trace ID" value={trace.trace_id} />
      <Row label="Agent" value={trace.agent_id} />
      <Row label="Outcome" value={<Badge outcome={trace.outcome} />} />
      <Row label="Duration" value={trace.duration_ms != null ? `${trace.duration_ms}ms` : "-"} />
      <Row label="Timestamp" value={new Date(trace.timestamp_ms).toISOString()} />

      {(trace.steps || []).length > 0 && (
        <div style={{ marginTop: 12 }}>
          <strong>Reasoning Steps ({trace.steps.length})</strong>
          {trace.steps.map((s, i) => (
            <StepItem key={s.step_id || i} step={s} index={i} defaultOpen={true} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Latest Decision ─────────────────────────────────────────── */

function DecisionTree({ decision, onChain }) {
  const [open, setOpen] = useState(true);
  const d = decision.decision;
  const steps = decision.steps || [];

  return (
    <div style={styles.card}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2
          style={{ ...styles.cardTitle, cursor: "pointer", margin: 0 }}
          onClick={() => setOpen(!open)}
        >
          {open ? "\u25BC" : "\u25B6"} Latest Decision
        </h2>
        <button style={styles.chainButton} onClick={onChain}>View Causal Chain</button>
      </div>
      {open && (
        <div style={{ marginTop: 12 }}>
          <Row label="Trace ID" value={d.trace_id} />
          <Row label="Outcome" value={<Badge outcome={d.outcome} />} />
          <Row label="Timestamp" value={new Date(d.timestamp_ms).toISOString()} />
          <Row label="Duration" value={d.duration_ms != null ? `${d.duration_ms}ms` : "-"} />

          {steps.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <strong>Reasoning Steps ({steps.length})</strong>
              {steps.map((s, i) => (
                <StepItem key={s.step_id || i} step={s} index={i} />
              ))}
            </div>
          )}
          {steps.length === 0 && (
            <p style={styles.muted}>No reasoning steps recorded.</p>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Step Item ───────────────────────────────────────────────── */

function StepItem({ step, index, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);

  const typeColors = {
    retrieve: "#8b5cf6",
    evaluate: "#f59e0b",
    decide: "#22c55e",
    delegate: "#3b82f6",
    execute: "#6366f1",
    error: "#ef4444",
  };

  return (
    <div style={styles.step}>
      <div
        style={{ cursor: "pointer", fontWeight: 500, display: "flex", alignItems: "center", gap: 8 }}
        onClick={() => setOpen(!open)}
      >
        {open ? "\u25BC" : "\u25B6"}
        <span
          style={{
            background: typeColors[step.step_type] || "#6b7280",
            color: "#fff",
            padding: "1px 6px",
            borderRadius: 3,
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          {step.step_type}
        </span>
        <span>Step {index + 1}</span>
        {step.tool_name && <span style={{ color: "#6b7280", fontSize: 12 }}>{step.tool_name}</span>}
        {step.confidence != null && step.confidence < 1 && (
          <span style={{ color: "#9ca3af", fontSize: 11 }}>
            {Math.round(step.confidence * 100)}%
          </span>
        )}
      </div>
      {open && (
        <div style={styles.stepDetail}>
          {step.thought && (
            <div style={styles.thoughtBox}>{step.thought}</div>
          )}
          {step.tool_name && <Row label="Tool" value={step.tool_name} />}
          {step.confidence != null && (
            <Row label="Confidence" value={`${(step.confidence * 100).toFixed(0)}%`} />
          )}
          {step.duration_ms != null && (
            <Row label="Duration" value={`${step.duration_ms}ms`} />
          )}
        </div>
      )}
    </div>
  );
}

/* ── History Table ───────────────────────────────────────────── */

function HistoryTable({ traces, onSelect, onChain }) {
  return (
    <div style={styles.card}>
      <h2 style={styles.cardTitle}>Decision History ({traces.length} traces)</h2>
      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>Trace ID</th>
            <th style={styles.th}>Outcome</th>
            <th style={styles.th}>Timestamp</th>
            <th style={styles.th}>Duration</th>
            <th style={styles.th}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {traces.map((t) => (
            <tr key={t.trace_id} style={styles.tableRow}>
              <td style={styles.td}>
                <code
                  style={{ cursor: "pointer", color: "#2563eb" }}
                  onClick={() => onSelect(t.trace_id)}
                >
                  {t.trace_id.slice(0, 12)}...
                </code>
              </td>
              <td style={styles.td}>
                <Badge outcome={t.outcome} />
              </td>
              <td style={styles.td}>
                {new Date(t.timestamp_ms).toLocaleString()}
              </td>
              <td style={styles.td}>
                {t.duration_ms != null ? `${t.duration_ms}ms` : "-"}
              </td>
              <td style={styles.td}>
                <button
                  style={styles.smallButton}
                  onClick={() => onChain(t.trace_id)}
                >
                  Chain
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Shared Components ───────────────────────────────────────── */

function Badge({ outcome }) {
  const colors = {
    success: "#22c55e",
    failure: "#ef4444",
    error: "#f97316",
    delegated: "#3b82f6",
  };
  return (
    <span
      style={{
        background: colors[outcome] || "#6b7280",
        color: "#fff",
        padding: "2px 8px",
        borderRadius: 4,
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      {outcome}
    </span>
  );
}

function Row({ label, value }) {
  return (
    <div style={{ display: "flex", gap: 8, padding: "3px 0" }}>
      <span style={{ color: "#6b7280", minWidth: 90, fontSize: 13 }}>{label}:</span>
      <span style={{ fontSize: 13 }}>{value}</span>
    </div>
  );
}

/* ── Styles ──────────────────────────────────────────────────── */

const styles = {
  container: {
    maxWidth: 900,
    margin: "0 auto",
    padding: 24,
    fontFamily: "system-ui, -apple-system, sans-serif",
    color: "#1f2937",
    background: "#fafafa",
    minHeight: "100vh",
  },
  header: { marginBottom: 24 },
  title: { fontSize: 28, fontWeight: 700, marginBottom: 4, color: "#111827" },
  subtitle: { color: "#6b7280", fontSize: 14, margin: 0 },
  controls: { display: "flex", gap: 8, marginBottom: 8 },
  quickLinks: { display: "flex", gap: 6, marginBottom: 16 },
  chipButton: {
    padding: "4px 12px",
    background: "#f3f4f6",
    color: "#374151",
    border: "1px solid #d1d5db",
    borderRadius: 16,
    fontSize: 12,
    cursor: "pointer",
  },
  input: {
    flex: 1,
    padding: "10px 14px",
    border: "1px solid #d1d5db",
    borderRadius: 8,
    fontSize: 14,
    background: "#fff",
  },
  button: {
    padding: "10px 20px",
    background: "#2563eb",
    color: "#fff",
    border: "none",
    borderRadius: 8,
    fontSize: 14,
    cursor: "pointer",
    fontWeight: 500,
  },
  chainButton: {
    padding: "6px 12px",
    background: "#7c3aed",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    fontSize: 12,
    cursor: "pointer",
    fontWeight: 500,
  },
  closeButton: {
    padding: "6px 12px",
    background: "#f3f4f6",
    color: "#374151",
    border: "1px solid #d1d5db",
    borderRadius: 6,
    fontSize: 12,
    cursor: "pointer",
  },
  smallButton: {
    padding: "3px 8px",
    background: "#ede9fe",
    color: "#7c3aed",
    border: "1px solid #c4b5fd",
    borderRadius: 4,
    fontSize: 11,
    cursor: "pointer",
    fontWeight: 500,
  },
  error: {
    background: "#fef2f2",
    color: "#dc2626",
    padding: "10px 14px",
    borderRadius: 8,
    marginBottom: 16,
    fontSize: 13,
  },
  card: {
    border: "1px solid #e5e7eb",
    borderRadius: 10,
    padding: 20,
    marginBottom: 16,
    background: "#fff",
    boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
  },
  cardTitle: { fontSize: 16, fontWeight: 600, marginBottom: 12 },
  step: {
    marginLeft: 16,
    padding: "8px 0",
    borderLeft: "3px solid #e5e7eb",
    paddingLeft: 14,
    marginTop: 6,
  },
  stepDetail: { paddingLeft: 8, marginTop: 6, fontSize: 13 },
  thoughtBox: {
    background: "#f9fafb",
    border: "1px solid #e5e7eb",
    borderRadius: 6,
    padding: "8px 12px",
    fontSize: 13,
    lineHeight: 1.5,
    marginBottom: 6,
    whiteSpace: "pre-wrap",
  },
  muted: { color: "#9ca3af", fontStyle: "italic", fontSize: 13 },
  chainNode: {
    border: "1px solid #e5e7eb",
    borderRadius: 8,
    padding: 14,
    background: "#fff",
  },
  chainHeader: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    marginBottom: 4,
    fontSize: 14,
  },
  chainIndex: {
    background: "#7c3aed",
    color: "#fff",
    padding: "2px 8px",
    borderRadius: 10,
    fontSize: 11,
    fontWeight: 600,
  },
  chainArrow: {
    textAlign: "center",
    padding: "6px 0",
    color: "#7c3aed",
    fontWeight: 600,
    fontSize: 12,
    letterSpacing: 1,
  },
  table: { width: "100%", borderCollapse: "collapse", fontSize: 13 },
  th: {
    textAlign: "left",
    padding: "8px 10px",
    borderBottom: "2px solid #e5e7eb",
    fontSize: 11,
    color: "#6b7280",
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  td: { padding: "8px 10px", borderBottom: "1px solid #f3f4f6" },
  tableRow: { transition: "background 0.1s" },
};
