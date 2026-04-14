import { useState, useEffect, useRef, useCallback, useMemo } from "react";

const _viteApi = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const API_BASE =
  _viteApi.startsWith("/") && typeof window !== "undefined"
    ? `${window.location.origin}${_viteApi.replace(/\/$/, "")}`
    : _viteApi.replace(/\/$/, "");

const DEFAULT_SETTINGS = {
  serverApiKey: "",
  providerName: "",
  modelName: "",
  apiKey: "",
  baseUrl: "",
  useServerDefault: true,
};

const authFetch = (url, options = {}) => {
  const headers = { ...(options.headers || {}) };
  if (options.body != null && headers["Content-Type"] == null) {
    headers["Content-Type"] = "application/json";
  }
  return fetch(url, { ...options, headers });
};
const TERMINAL = new Set(["completed", "rejected", "error", "max_steps_reached", "killed"]);
const DIFF_VISIBLE = new Set(["awaiting_approval", "completed", "rejected"]);

const TOOL_COLOR = {
  list_directory: "#6B8CFF",
  read_file:      "#A78BFA",
  write_file:     "#4ADE80",
  run_tests:      "#F472B6",
  git_diff:       "#D97706",
  git_commit:     "#22D3EE",
  reviewer_agent: "#FB923C",
};
const toolColor = (t) => TOOL_COLOR[(t || "").toLowerCase()] || "#52504A";

const STATUS_COLOR = {
  running:           "#D97706",
  completed:         "#4ADE80",
  awaiting_approval: "#FBBF24",
  rejected:          "#F87171",
  error:             "#F87171",
  max_steps_reached: "#A78BFA",
  killed:            "#F87171",
  pending:           "#52504A",
};
const statusColor = (s) => STATUS_COLOR[s] || "#52504A";

function repoDisplayLabel(repoUrl) {
  if (!repoUrl) return null;
  const u = String(repoUrl).trim();
  if (!u) return null;
  if (u.startsWith("https://github.com/") || u.startsWith("git@github.com:")) {
    return u
      .replace(/^https:\/\/github\.com\//, "")
      .replace(/^git@github\.com:/, "")
      .replace(/\.git$/i, "")
      .split("/")
      .filter(Boolean)
      .slice(0, 2)
      .join("/");
  }
  const parts = u.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.slice(-2).join("/") || u;
}

function getStepTool(step) {
  return step?.tool || step?.decision?.tool || null;
}
function getStepInput(step) {
  const d = step?.decision;
  if (d && typeof d === "object" && d.input != null) return String(d.input);
  return step?.input ? String(step.input) : "";
}
function getStepStatus(step) {
  const r = step?.result;
  if (!r) return "";
  if (typeof r === "string") return r;
  return String(r?.status ?? r?.exit_code ?? "");
}
function getStepStdout(step) {
  const r = step?.result;
  if (!r || typeof r === "string") return "";
  return String(r?.stdout ?? "");
}

function DiffView({ diff }) {
  if (!diff) return <div className="empty-msg">No diff available.</div>;
  return (
    <div className="diff-wrap">
      {diff.split("\n").map((line, i) => {
        const cls =
          line.startsWith("+") && !line.startsWith("+++") ? "dl-add"
          : line.startsWith("-") && !line.startsWith("---") ? "dl-rem"
          : line.startsWith("@@") ? "dl-hunk"
          : line.startsWith("diff ") || line.startsWith("index ") ? "dl-meta"
          : "dl-ctx";
        return <div key={i} className={`diff-line ${cls}`}>{line || " "}</div>;
      })}
    </div>
  );
}

function StepRow({ step, index }) {
  const [open, setOpen] = useState(false);
  const tool    = getStepTool(step);
  const input   = getStepInput(step);
  const status  = getStepStatus(step);
  const stdout  = getStepStdout(step);
  const color   = toolColor(tool);
  const isReview = tool === "reviewer_agent";
  const res = step?.result;

  const verdictType = res?.verdict_type || "";
  const verdictColor =
    verdictType === "approved" ? "#4ADE80"
    : verdictType === "needs_changes" ? "#FBBF24"
    : verdictType === "escalate_to_human" ? "#F87171"
    : "#52504A";

  return (
    <div className="step" style={{ "--bc": color }}>
      <button className="step-hd" onClick={() => setOpen(!open)}>
        <span className="step-num">{String(index).padStart(2,"0")}</span>
        <span className="step-tag" style={{ background: color + "22", color, border: `1px solid ${color}44` }}>
          {(tool || "?").replace(/_/g,"_")}
        </span>
        {input && <span className="step-inp">{input.length > 60 ? input.slice(0,60)+"…" : input}</span>}
        <span className="step-spacer" />
        {isReview && verdictType && (
          <span className="verdict-chip" style={{ color: verdictColor, background: verdictColor+"18", border:`1px solid ${verdictColor}33` }}>
            {verdictType.replace(/_/g," ")}
          </span>
        )}
        <span className="step-st" style={{ color: status==="success"?"#4ADE80" : status==="error"?"#F87171" : "#52504A" }}>
          {status}
        </span>
        <span className="step-arr" style={{ transform: open?"rotate(90deg)":"none" }}>›</span>
      </button>

      {open && (
        <div className="step-body">
          {isReview && res && (
            <div className="review-detail" style={{ borderLeft: `2px solid ${verdictColor}` }}>
              {res.reason && <p className="rv-reason">{res.reason}</p>}
              {res.suggestions && res.suggestions !== res.reason && (
                <p className="rv-sug">↳ {res.suggestions}</p>
              )}
              {res.iteration && <span className="rv-it">Cycle {res.iteration}</span>}
            </div>
          )}
          {stdout && <pre className="step-out">{stdout}</pre>}
          {!stdout && !isReview && res && typeof res === "object" && (
            <pre className="step-out">{JSON.stringify(res, null, 2)}</pre>
          )}
        </div>
      )}
    </div>
  );
}

function SummaryCard({ task, stepCount }) {
  const sc = statusColor(task.status);
  const label = task.status.replace(/_/g," ").toUpperCase();
  const cycles = task.review_iterations || 0;
  const promptTokens = Number(task.total_prompt_tokens || 0);
  const completionTokens = Number(task.total_completion_tokens || 0);
  return (
    <div className="summary">
      <div className="sum-status" style={{ color: sc }}>
        <span className="sum-dot" style={{ background: sc }} />
        {label}
      </div>
      <div className="sum-stats">
        <div className="sum-stat">
          <span className="sum-val">{stepCount}</span>
          <span className="sum-lbl">STEPS</span>
        </div>
        {cycles > 0 ? (
          <div className="sum-stat">
            <span className="sum-val">{cycles}</span>
            <span className="sum-lbl">REVIEW CYCLES</span>
          </div>
        ) : (
          <div className="sum-stat">
            <span className="sum-val sum-pass">1st pass</span>
            <span className="sum-lbl">REVIEWER APPROVED</span>
          </div>
        )}
      </div>
      {task.escalation_reason && (
        <div className="sum-note sum-warn">⚠ {task.escalation_reason}</div>
      )}
      {task.status === "error" && task.error_message && (
        <div className="sum-note sum-err">{task.error_message}</div>
      )}
      {task.rejection_reason && (
        <div className="sum-note sum-err">✕ Rejected: {task.rejection_reason}</div>
      )}
      {(promptTokens > 0 || completionTokens > 0) && (
        <div className="sum-token-row">
          <div className="sum-stat">
            <span className="sum-val sum-val-sm">{promptTokens.toLocaleString()}</span>
            <span className="sum-lbl">PROMPT TOK</span>
          </div>
          <div className="sum-stat">
            <span className="sum-val sum-val-sm">{completionTokens.toLocaleString()}</span>
            <span className="sum-lbl">COMPLETION TOK</span>
          </div>
          <div className="sum-stat">
            <span className="sum-val sum-val-sm" style={{ color: "var(--acc)" }}>
              {(promptTokens + completionTokens).toLocaleString()}
            </span>
            <span className="sum-lbl">TOTAL</span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [tasks,       setTasks]       = useState([]);
  const [selectedId,  setSelectedId]  = useState(null);
  const [goal,        setGoal]        = useState("");
  const [repoUrl,     setRepoUrl]     = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState(() => {
    try {
      const stored = localStorage.getItem("vulcan_settings");
      return stored ? { ...DEFAULT_SETTINGS, ...JSON.parse(stored) } : DEFAULT_SETTINGS;
    } catch {
      return DEFAULT_SETTINGS;
    }
  });
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [submitting,  setSubmitting]  = useState(false);
  const [formErr,     setFormErr]     = useState("");
  const [logs,        setLogs]        = useState(null);
  const [diffData,    setDiffData]    = useState(null);
  const [diffFor,     setDiffFor]     = useState(null);
  const [tab,         setTab]         = useState("log");
  const [rejectOpen,  setRejectOpen]  = useState(false);
  const [rejectText,  setRejectText]  = useState("");
  const [commandText, setCommandText] = useState("");
  const [commandLogByTask, setCommandLogByTask] = useState({});
  const logEndRef  = useRef(null);
  const logPollRef = useRef(null);
  const eventSourceRef = useRef(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const historyFetchLock = useRef(false);
  const [backendOnline, setBackendOnline] = useState(null);

  const saveSettings = useCallback((newSettings) => {
    const merged = { ...DEFAULT_SETTINGS, ...newSettings };
    setSettings(merged);
    localStorage.setItem("vulcan_settings", JSON.stringify(merged));
  }, []);

  const resetSettings = useCallback(() => {
    setSettings(DEFAULT_SETTINGS);
    localStorage.removeItem("vulcan_settings");
  }, []);

  const providerReady = settings.useServerDefault || Boolean((settings.apiKey || "").trim());
  const providerIndicatorLabel = settings.useServerDefault
    ? "Server default"
    : (settings.apiKey || "").trim()
      ? `${settings.providerName || "Custom"} · ${settings.modelName || "?"}`
      : "No provider configured";
  const settingsActiveLabel = settings.useServerDefault
    ? "Server default"
    : (settings.apiKey || "").trim()
      ? `${settings.providerName || "Custom"} · ${settings.modelName || "unknown model"}`
      : "No key configured";

  const buildTaskHeaders = useCallback(() => {
    const headers = { "Content-Type": "application/json" };
    const serverApiKey = (settings.serverApiKey || "").trim();
    if (serverApiKey) headers["X-API-Key"] = serverApiKey;

    const apiKey = (settings.apiKey || "").trim();
    const modelName = (settings.modelName || "").trim();
    const baseUrl = (settings.baseUrl || "").trim();
    if (!settings.useServerDefault && apiKey) {
      headers["X-LLM-Key"] = apiKey;
      if (modelName) headers["X-LLM-Model"] = modelName;
      if (baseUrl) headers["X-LLM-Base-URL"] = baseUrl;
    }
    return headers;
  }, [settings]);

  const selected = useMemo(
    () => tasks.find(t => t.id === selectedId) || null,
    [tasks, selectedId]
  );

  const appendCommandLog = useCallback((taskId, text, kind = "info") => {
    if (!taskId) return;
    setCommandLogByTask((prev) => {
      const list = Array.isArray(prev[taskId]) ? prev[taskId] : [];
      const next = [...list, { ts: Date.now(), text, kind }];
      return { ...prev, [taskId]: next.slice(-50) };
    });
  }, []);

  const checkBackendHealth = useCallback(async () => {
    try {
      const ac = new AbortController();
      const t = setTimeout(() => ac.abort(), 5000);
      const r = await fetch(`${API_BASE}/health`, { signal: ac.signal });
      clearTimeout(t);
      setBackendOnline(r.ok);
    } catch {
      setBackendOnline(false);
    }
  }, []);

  useEffect(() => {
    checkBackendHealth();
    const id = setInterval(checkBackendHealth, 30000);
    return () => clearInterval(id);
  }, [checkBackendHealth]);

  const fetchTasks = useCallback(async () => {
    try {
      const headers = {};
      const serverApiKey = (settings.serverApiKey || "").trim();
      if (serverApiKey) headers["X-API-Key"] = serverApiKey;
      const r = await fetch(`${API_BASE}/tasks`, { headers });
      if (!r.ok) return;
      const data = await r.json();
      const sessionList = Array.isArray(data) ? data : [];
      sessionList.sort((a,b) => {
        const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
        const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
        return tb - ta;
      });
      setTasks(prev => {
        if (!historyLoaded) return sessionList;
        const sid = new Set(sessionList.map(t => t.id));
        const fromHistory = prev.filter(t => !sid.has(t.id));
        const merged = [...sessionList, ...fromHistory];
        return merged.sort((a,b) => {
          const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
          const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
          return tb - ta;
        });
      });
    } catch {}
  }, [historyLoaded, settings.serverApiKey]);

  useEffect(() => {
    void fetchTasks();
    const id = setInterval(fetchTasks, 2500);
    return () => clearInterval(id);
  }, [fetchTasks]);

  const loadHistory = async () => {
    if (historyLoaded || historyFetchLock.current) return;
    historyFetchLock.current = true;
    setHistoryLoading(true);
    try {
      const headers = {};
      const serverApiKey = (settings.serverApiKey || "").trim();
      if (serverApiKey) headers["X-API-Key"] = serverApiKey;
      const r = await fetch(`${API_BASE}/tasks/history`, { headers });
      if (!r.ok) return;
      const data = await r.json();
      const list = Array.isArray(data) ? data : [];
      setTasks((prev) => {
        const ids = new Set(prev.map((t) => t.id));
        const newTasks = list.filter((t) => t.id && !ids.has(t.id));
        const merged = [...prev, ...newTasks];
        return merged.sort((a, b) => {
          const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
          const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
          return tb - ta;
        });
      });
      setHistoryLoaded(true);
    } catch (e) {
      console.error("History load failed:", e);
    } finally {
      historyFetchLock.current = false;
      setHistoryLoading(false);
    }
  };

  const fetchLogs = useCallback(async (id) => {
    if (!id) return;
    try {
      const headers = {};
      const serverApiKey = (settings.serverApiKey || "").trim();
      if (serverApiKey) headers["X-API-Key"] = serverApiKey;
      const r = await fetch(`${API_BASE}/tasks/${id}/logs`, { headers });
      if (!r.ok) return;
      setLogs(await r.json());
    } catch {}
  }, [settings.serverApiKey]);

  useEffect(() => {
    clearInterval(logPollRef.current);
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (!selectedId) { setLogs(null); return; }
    fetchLogs(selectedId);
    if (selected?.status === "running") {
      const supportsSSE = typeof window !== "undefined" && "EventSource" in window;
      if (supportsSSE) {
        const streamUrl = new URL(`${API_BASE}/tasks/${selectedId}/stream`);
        const serverApiKey = (settings.serverApiKey || "").trim();
        if (serverApiKey) streamUrl.searchParams.set("api_key", serverApiKey);
        const es = new EventSource(streamUrl.toString());
        eventSourceRef.current = es;

        es.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data || "{}");
            fetchLogs(selectedId);
            fetchTasks();
            if (payload?.status === "completed" || payload?.status === "failed") {
              es.close();
              if (eventSourceRef.current === es) eventSourceRef.current = null;
            }
          } catch {
            // Keep stream alive; malformed event should not break UI.
          }
        };
        es.onerror = () => {
          es.close();
          if (eventSourceRef.current === es) eventSourceRef.current = null;
          logPollRef.current = setInterval(() => fetchLogs(selectedId), 1200);
        };
      } else {
        logPollRef.current = setInterval(() => fetchLogs(selectedId), 1200);
      }
    } else if (!selected || !TERMINAL.has(selected.status)) {
      logPollRef.current = setInterval(() => fetchLogs(selectedId), 1200);
    }
    return () => {
      clearInterval(logPollRef.current);
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [selectedId, selected?.status, fetchLogs, fetchTasks, settings.serverApiKey]);

  useEffect(() => {
    if (selected?.status === "running") {
      logEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, selected?.status]);

  useEffect(() => {
    if (!selected || !DIFF_VISIBLE.has(selected.status)) return;
    if (diffFor === selected.id) return;
    (async () => {
      try {
        const headers = {};
        const serverApiKey = (settings.serverApiKey || "").trim();
        if (serverApiKey) headers["X-API-Key"] = serverApiKey;
        const r = await fetch(`${API_BASE}/tasks/${selected.id}/diff`, { headers });
        if (r.ok) { setDiffData(await r.json()); setDiffFor(selected.id); }
      } catch {}
    })();
  }, [selected?.id, selected?.status, diffFor, settings.serverApiKey]);

  const submitTask = async () => {
    const g = goal.trim();
    if (!g || submitting) return;
    setSubmitting(true); setFormErr("");
    try {
      const r = await fetch(`${API_BASE}/tasks`, {
        method: "POST",
        headers: buildTaskHeaders(),
        body: JSON.stringify({ goal: g, repo_url: repoUrl.trim() }),
      });
      if (!r.ok) throw new Error();
      const d = await r.json();
      setGoal(""); setRepoUrl(""); setTab("log");
      await fetchTasks();
      setSelectedId(d.id);
    } catch {
      setFormErr("Cannot reach API — is the backend running?");
    }
    setSubmitting(false);
  };

  const approve = async () => {
    if (!selected) return;
    await authFetch(`${API_BASE}/tasks/${selected.id}/approve`, { method: "POST" });
    fetchTasks(); fetchLogs(selected.id);
  };

  const reject = async () => {
    if (!selected) return;
    await authFetch(`${API_BASE}/tasks/${selected.id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason: rejectText }),
    });
    setRejectOpen(false); setRejectText("");
    fetchTasks(); fetchLogs(selected.id);
  };

  const killTask = async () => {
    if (!selected) return;
    if (!window.confirm("Are you sure you want to terminate this agent?")) return;
    try {
      const r = await authFetch(`${API_BASE}/tasks/${selected.id}/kill`, { method: "POST" });
      if (!r.ok) return;
      setTasks((prev) =>
        prev.map((t) => (t.id === selected.id ? { ...t, status: "killed" } : t))
      );
      clearInterval(logPollRef.current);
      logPollRef.current = null;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      setLogs((prev) => {
        if (!prev || !Array.isArray(prev.steps)) return prev;
        const msg = "Task terminated by user.";
        const has = prev.steps.some(
          (s) => String(s?.result?.stdout || "").trim() === msg
        );
        if (has) return { ...prev, status: "killed" };
        return {
          ...prev,
          status: "killed",
          steps: [
            ...prev.steps,
            {
              step: prev.steps.length,
              decision: {
                reasoning: "UI: kill requested",
                tool: "_user_kill",
                input: null,
                done: false,
              },
              result: { status: "killed", stdout: msg, stderr: "", exit_code: -1 },
            },
          ],
        };
      });
      await fetchTasks();
      await fetchLogs(selected.id);
    } catch {}
  };

  const runSlashCommand = async () => {
    const raw = commandText.trim();
    if (!raw || !selected) return;
    if (!raw.startsWith("/")) {
      appendCommandLog(selected.id, `Unknown input: ${raw}. Start with /.`, "error");
      return;
    }
    const [cmd, ...rest] = raw.split(" ");
    const arg = rest.join(" ").trim();

    try {
      if (cmd === "/approve") {
        await authFetch(`${API_BASE}/tasks/${selected.id}/approve`, { method: "POST" });
        appendCommandLog(selected.id, "Approved current task.", "success");
        await fetchTasks();
        await fetchLogs(selected.id);
      } else if (cmd === "/reject") {
        await authFetch(`${API_BASE}/tasks/${selected.id}/reject`, {
          method: "POST",
          body: JSON.stringify({ reason: arg }),
        });
        appendCommandLog(selected.id, `Rejected current task${arg ? `: ${arg}` : "."}`, "success");
        await fetchTasks();
        await fetchLogs(selected.id);
      } else if (cmd === "/stop") {
        await authFetch(`${API_BASE}/tasks/${selected.id}/kill`, { method: "POST" });
        appendCommandLog(selected.id, "Stop requested for current task.", "success");
        await fetchTasks();
        await fetchLogs(selected.id);
      } else if (cmd === "/retry") {
        const r = await fetch(`${API_BASE}/tasks`, {
          method: "POST",
          headers: buildTaskHeaders(),
          body: JSON.stringify({ goal: selected.goal || "", repo_url: selected.repo_url || "" }),
        });
        if (!r.ok) throw new Error("retry failed");
        const d = await r.json();
        appendCommandLog(selected.id, `Retried as new task ${String(d.id || "").slice(0, 7)}.`, "success");
        await fetchTasks();
        setSelectedId(d.id);
        setTab("log");
      } else if (cmd === "/status") {
        const prompt = Number(selected.total_prompt_tokens || 0);
        const completion = Number(selected.total_completion_tokens || 0);
        const total = prompt + completion;
        const dailyRemaining = Math.max(0, 100000 - total);
        appendCommandLog(
          selected.id,
          `status: ${selected.status} | steps: ${steps.length} | prompt: ${prompt.toLocaleString()} | completion: ${completion.toLocaleString()} | total: ${total.toLocaleString()} | ~daily remaining: ${dailyRemaining.toLocaleString()}`,
          "info"
        );
      } else {
        appendCommandLog(
          selected.id,
          `Unknown command: ${cmd}. Try /approve, /reject <reason>, /stop, /retry, /status`,
          "error"
        );
      }
    } catch {
      appendCommandLog(selected.id, `Command failed: ${raw}`, "error");
    } finally {
      setCommandText("");
    }
  };

  const steps = logs?.steps || [];
  const diffAvail = selected && DIFF_VISIBLE.has(selected.status);

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Geist+Mono:wght@300;400;500;600&family=DM+Serif+Display:ital@0;1&display=swap');
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        :root {
          --bg0: #0C0C0E;
          --bg1: #111114;
          --bg2: #18181C;
          --bg3: #1F1F24;
          --bd: #26262C;
          --bd2: #32323A;
          --t1: #F0EEE8;
          --t2: #8A887E;
          --t3: #52504A;
          --acc: #D97706;
          --acc-dim: #92400E;
          --font-mono: 'Geist Mono', monospace;
          --font-display: 'DM Serif Display', serif;
        }
        html, body, #root {
          height: 100%; width: 100%; overflow: hidden;
          background: var(--bg0); color: var(--t1);
          font-family: var(--font-mono); font-size: 13px; line-height: 1.5;
          -webkit-font-smoothing: antialiased;
        }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--bd2); border-radius: 2px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--acc-dim); }
        button { font-family: var(--font-mono); cursor: pointer; }
        input, textarea { font-family: var(--font-mono); }

        .shell {
          display: grid;
          grid-template-columns: 260px 1fr;
          height: 100vh;
          overflow: hidden;
        }

        /* SIDEBAR */
        .sidebar {
          position: relative;
          display: grid;
          grid-template-rows: auto auto 1fr;
          height: 100vh;
          overflow: hidden;
          border-right: 1px solid var(--bd);
          background: var(--bg1);
        }
        .sidebar::after {
          content: '';
          position: absolute;
          inset: 0;
          pointer-events: none;
          background: repeating-linear-gradient(
            0deg,
            transparent,
            transparent 2px,
            rgba(255,255,255,0.012) 2px,
            rgba(255,255,255,0.012) 3px
          );
          z-index: 0;
        }
        .sidebar > * { position: relative; z-index: 1; }
        .sb-main {
          display: flex;
          flex-direction: column;
          min-height: 0;
          overflow: hidden;
        }
        .sb-brand {
          padding: 14px 16px;
          border-bottom: 1px solid var(--bd);
          display: flex; align-items: baseline; gap: 7px;
        }
        .sb-brand-name {
          font-family: var(--font-mono);
          font-size: 11px;
          font-weight: 600;
          letter-spacing: 0.15em;
          text-transform: uppercase;
          color: var(--t1);
        }
        .sb-brand-ver {
          font-family: var(--font-mono);
          font-size: 9px;
          color: var(--acc);
          background: #92400E22;
          border: 1px solid var(--acc-dim);
          padding: 1px 5px;
          border-radius: 2px;
          letter-spacing: 0.05em;
        }
        .sb-input { padding: 12px; border-bottom: 1px solid var(--bd); }
        .sb-input textarea {
          width: 100%;
          background: var(--bg0);
          border: 1px solid var(--bd);
          border-radius: 4px;
          padding: 10px 12px;
          color: var(--t1);
          font-family: var(--font-mono);
          font-size: 12px;
          resize: none;
          line-height: 1.5;
          height: 80px;
          transition: border-color 0.15s, box-shadow 0.15s;
        }
        .sb-input textarea:focus {
          outline: none;
          border-color: var(--acc);
          box-shadow: 0 0 0 2px #92400E33;
        }
        .sb-input textarea::placeholder { color: var(--t3); }
        .repo-input {
          width: 100%;
          margin-top: 6px;
          background: var(--bg0);
          border: 1px solid var(--bd);
          border-radius: 4px;
          padding: 7px 12px;
          color: var(--t1);
          font-family: var(--font-mono);
          font-size: 11px;
          transition: border-color 0.15s, box-shadow 0.15s;
        }
        .repo-input:focus {
          outline: none;
          border-color: var(--acc);
          box-shadow: 0 0 0 2px #92400E33;
        }
        .repo-input::placeholder { color: var(--t3); }
        .sb-submit {
          margin-top: 8px;
          width: 100%;
          padding: 8px 0;
          background: var(--acc);
          color: #0C0C0E;
          font-family: var(--font-mono);
          font-size: 11px;
          font-weight: 600;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          border: none;
          border-radius: 4px;
          transition: opacity 0.15s, transform 0.15s;
        }
        .sb-submit:disabled { opacity: 0.35; cursor: not-allowed; }
        .sb-submit:hover:not(:disabled) { opacity: 0.88; transform: translateY(-1px); }
        .sb-err { margin-top: 5px; font-size: 11px; color: #F87171; }
        .sb-list { flex: 1; overflow-y: auto; padding: 6px; min-height: 0; }
        .no-tasks {
          padding: 20px 10px;
          text-align: center;
          font-family: var(--font-mono);
          color: var(--t3);
          font-size: 12px;
        }
        .load-history-btn {
          flex-shrink: 0;
          margin: 8px 10px 10px;
          padding: 8px 10px;
          width: calc(100% - 20px);
          box-sizing: border-box;
          background: transparent;
          border: 1px solid var(--bd2);
          border-radius: 4px;
          color: var(--t2);
          font-family: var(--font-mono);
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          cursor: pointer;
          transition: border-color 0.15s, color 0.15s, background 0.15s;
        }
        .load-history-btn:hover:not(:disabled) {
          border-color: var(--acc);
          color: var(--acc);
          background: #92400E14;
        }
        .load-history-btn:disabled {
          opacity: 0.45;
          cursor: not-allowed;
        }

        /* SETTINGS PANEL */
        .settings-panel {
          display: flex;
          flex-direction: column;
          flex: 1;
          min-height: 0;
          overflow: hidden;
        }
        .settings-hdr {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 10px 12px;
          border-bottom: 1px solid var(--bd);
        }
        .settings-back {
          background: none;
          border: none;
          color: var(--t3);
          font-family: var(--font-mono);
          font-size: 10px;
          cursor: pointer;
          padding: 3px 6px;
          border-radius: 3px;
          letter-spacing: 0.05em;
        }
        .settings-back:hover { color: var(--t2); background: var(--bg2); }
        .settings-title {
          font-family: var(--font-mono);
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          color: var(--t2);
        }
        .settings-body {
          flex: 1;
          overflow-y: auto;
          padding: 12px;
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .settings-toggle-row {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .settings-toggle-label {
          display: flex;
          align-items: center;
          gap: 8px;
          cursor: pointer;
          font-family: var(--font-mono);
          font-size: 11px;
          color: var(--t1);
        }
        .settings-toggle-label input[type="checkbox"] {
          width: 14px;
          height: 14px;
          accent-color: var(--acc);
          cursor: pointer;
        }
        .settings-fields {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }
        .settings-field {
          display: flex;
          flex-direction: column;
          gap: 5px;
        }
        .settings-label {
          font-family: var(--font-mono);
          font-size: 9px;
          font-weight: 600;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: var(--t3);
        }
        .settings-optional {
          font-weight: 400;
          text-transform: none;
          letter-spacing: 0;
          color: var(--t3);
          opacity: 0.6;
        }
        .settings-input {
          width: 100%;
          background: var(--bg0);
          border: 1px solid var(--bd);
          border-radius: 3px;
          padding: 6px 10px;
          color: var(--t1);
          font-family: var(--font-mono);
          font-size: 11px;
          transition: border-color 0.15s;
        }
        .settings-input:focus {
          outline: none;
          border-color: var(--acc);
          box-shadow: 0 0 0 2px #92400E22;
        }
        .settings-input::placeholder { color: var(--t3); }
        .settings-input-secret { letter-spacing: 0.08em; }
        .settings-hint {
          font-family: var(--font-mono);
          font-size: 9px;
          color: var(--t3);
          line-height: 1.5;
        }
        .settings-quickfill {
          display: flex;
          flex-wrap: wrap;
          gap: 5px;
        }
        .settings-quickfill-btn {
          font-family: var(--font-mono);
          font-size: 9px;
          font-weight: 600;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          padding: 4px 9px;
          border-radius: 3px;
          border: 1px solid var(--bd2);
          background: var(--bg2);
          color: var(--t2);
          cursor: pointer;
          transition: border-color 0.1s, color 0.1s;
        }
        .settings-quickfill-btn:hover {
          border-color: var(--acc);
          color: var(--acc);
        }
        .settings-active {
          display: flex;
          align-items: center;
          gap: 7px;
          padding: 8px 10px;
          background: var(--bg2);
          border: 1px solid var(--bd);
          border-radius: 3px;
        }
        .settings-active-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          flex-shrink: 0;
        }
        .settings-active-label {
          font-family: var(--font-mono);
          font-size: 10px;
          color: var(--t2);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .settings-clear {
          width: 100%;
          padding: 7px 0;
          background: transparent;
          border: 1px solid var(--bd);
          border-radius: 3px;
          color: var(--t3);
          font-family: var(--font-mono);
          font-size: 10px;
          cursor: pointer;
          transition: border-color 0.15s, color 0.15s;
        }
        .settings-clear:hover { border-color: var(--bd2); color: var(--t2); }
        .settings-gear-btn {
          display: flex;
          align-items: center;
          gap: 7px;
          width: calc(100% - 12px);
          margin: 4px 6px 8px;
          padding: 7px 10px;
          background: transparent;
          border: 1px solid var(--bd);
          border-radius: 3px;
          color: var(--t3);
          font-family: var(--font-mono);
          font-size: 10px;
          cursor: pointer;
          transition: border-color 0.15s, color 0.15s;
          letter-spacing: 0.05em;
        }
        .settings-gear-btn:hover { border-color: var(--bd2); color: var(--t2); }
        .settings-gear-btn svg { flex-shrink: 0; }
        .provider-indicator {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-top: 5px;
          padding: 0 2px;
        }
        .provider-dot {
          width: 5px;
          height: 5px;
          border-radius: 50%;
          flex-shrink: 0;
        }
        .provider-name {
          font-family: var(--font-mono);
          font-size: 9px;
          color: var(--t3);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .task-item {
          padding: 9px 12px;
          border-radius: 4px;
          border: 1px solid transparent;
          cursor: pointer;
          background: transparent;
          text-align: left;
          width: 100%;
          color: var(--t1);
          display: block;
          margin-bottom: 1px;
          transition: background 0.1s, border-color 0.1s;
        }
        .task-item:hover { background: var(--bg2); border-color: var(--bd); }
        .task-item.sel { background: var(--bg2); border-color: var(--bd2); }
        .task-goal {
          font-family: var(--font-mono);
          font-size: 11.5px;
          line-height: 1.4;
          color: var(--t1);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          margin-bottom: 5px;
        }
        .task-meta {
          display: flex;
          align-items: center;
          gap: 5px;
          font-family: var(--font-mono);
          font-size: 10px;
          color: var(--t3);
        }
        .task-repo {
          font-family: var(--font-mono);
          font-size: 10px;
          color: var(--t3);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          max-width: 100%;
          margin-top: 2px;
        }
        .tdot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
        .tdot-running { animation: pulse 1.2s infinite; }

        /* MAIN */
        .main {
          display: grid;
          grid-template-rows: auto auto auto 1fr;
          height: 100vh;
          overflow: hidden;
          background: var(--bg0);
        }
        .task-hdr {
          padding: 10px 20px;
          border-bottom: 1px solid var(--bd);
          background: var(--bg1);
          display: flex;
          align-items: center;
          gap: 10px;
          min-height: 48px;
        }
        .task-hdr-goal {
          font-family: var(--font-display);
          font-size: 17px;
          font-weight: normal;
          font-style: italic;
          color: var(--t1);
          flex: 1;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .status-pill {
          font-family: var(--font-mono);
          font-size: 9px;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          padding: 3px 8px;
          border-radius: 2px;
          border: 1px solid;
          white-space: nowrap;
          flex-shrink: 0;
        }
        .token-counter {
          display: flex;
          align-items: center;
          gap: 4px;
          font-family: var(--font-mono);
          font-size: 10px;
          color: var(--t3);
          background: var(--bg3);
          border: 1px solid var(--bd);
          border-radius: 3px;
          padding: 2px 8px;
          white-space: nowrap;
          flex-shrink: 0;
          transition: color 0.3s, border-color 0.3s;
        }
        .token-counter.ticking {
          color: var(--acc);
          border-color: var(--acc-dim);
        }
        .token-icon {
          font-size: 9px;
          opacity: 0.6;
        }
        .token-val {
          font-weight: 600;
          letter-spacing: 0.03em;
        }
        .token-lbl {
          color: var(--t3);
          font-size: 9px;
        }
        .hdr-pulse { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }

        .banner {
          padding: 8px 20px;
          background: #FBBF2408;
          border-bottom: 1px solid #FBBF2422;
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
        }
        .banner-esc {
          font-family: var(--font-mono);
          font-size: 11px;
          color: #FBBF24;
          flex: 1;
          min-width: 0;
        }
        .banner-btns { display: flex; gap: 6px; align-items: center; margin-left: auto; }
        .btn-approve {
          font-family: var(--font-mono);
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          padding: 6px 14px;
          border-radius: 3px;
          border: 1px solid #4ADE8055;
          background: #4ADE8012;
          color: #4ADE80;
        }
        .btn-fallback { opacity: 0.55; }
        .btn-fallback:hover { opacity: 0.8; }
        .btn-approve:hover { background: #4ADE8022; }
        .btn-reject {
          font-family: var(--font-mono);
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          padding: 6px 14px;
          border-radius: 3px;
          border: 1px solid #F8717155;
          background: #F8717112;
          color: #F87171;
        }
        .btn-reject:hover { background: #F8717122; }
        .btn-ghost {
          font-family: var(--font-mono);
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          padding: 6px 14px;
          border-radius: 3px;
          border: 1px solid var(--bd2);
          background: transparent;
          color: var(--t2);
        }
        .reject-row { width: 100%; display: flex; gap: 6px; margin-top: 4px; }
        .reject-row input {
          flex: 1;
          background: var(--bg0);
          border: 1px solid var(--bd);
          border-radius: 5px;
          padding: 5px 8px;
          color: var(--t1);
          font-family: var(--font-mono);
          font-size: 11px;
        }
        .reject-row input:focus {
          outline: none;
          border-color: var(--acc);
          box-shadow: 0 0 0 2px #92400E33;
        }

        .tabs {
          display: flex;
          border-bottom: 1px solid var(--bd);
          background: var(--bg1);
          padding: 0 16px;
        }
        .tab-btn {
          padding: 10px 14px;
          background: none;
          border: none;
          border-bottom: 2px solid transparent;
          color: var(--t3);
          font-family: var(--font-mono);
          font-size: 11px;
          font-weight: 500;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          transition: color 0.1s;
          margin-bottom: -1px;
        }
        .tab-btn:hover:not(:disabled) { color: var(--t2); }
        .tab-btn.active { color: var(--t1); border-bottom: 2px solid var(--acc); }
        .tab-btn:disabled { opacity: 0.25; cursor: not-allowed; }

        .panel { overflow: hidden; display: flex; flex-direction: column; min-height: 0; }
        .log-scroll { flex: 1; overflow-y: auto; padding-bottom: 8px; }

        /* STEPS */
        .step {
          border-left: 2px solid var(--bc, #4B5563);
          margin: 0 16px 1px;
          border-radius: 0 3px 3px 0;
          background: var(--bg1);
          animation: fadeIn .18s ease;
        }
        .step:first-child { margin-top: 8px; }
        @keyframes fadeIn { from{opacity:0;transform:translateX(-3px)} to{opacity:1;transform:none} }
        .step-hd {
          width: 100%;
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 5px 12px;
          background: none;
          border: none;
          color: var(--t1);
          text-align: left;
        }
        .step-hd:hover { background: var(--bg2); border-radius: 0 3px 3px 0; }
        .step-num {
          font-family: var(--font-mono);
          font-size: 9px;
          color: var(--t3);
          min-width: 22px;
        }
        .step-tag {
          font-family: var(--font-mono);
          font-size: 9px;
          font-weight: 600;
          letter-spacing: 0.05em;
          padding: 2px 7px;
          border-radius: 2px;
          white-space: nowrap;
          flex-shrink: 0;
        }
        .step-inp {
          font-family: var(--font-mono);
          font-size: 10.5px;
          color: var(--t2);
          min-width: 0;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .step-spacer { flex: 1; }
        .verdict-chip {
          font-family: var(--font-mono);
          font-size: 9px;
          letter-spacing: 0.05em;
          text-transform: uppercase;
          padding: 2px 7px;
          border-radius: 2px;
          white-space: nowrap;
          flex-shrink: 0;
        }
        .step-st {
          font-family: var(--font-mono);
          font-size: 9px;
          white-space: nowrap;
          flex-shrink: 0;
          min-width: 44px;
          text-align: right;
        }
        .step-arr { color: var(--t3); font-size: 14px; transition: transform 0.15s; flex-shrink: 0; line-height: 1; }
        .step-body { padding: 0 12px 10px; border-top: 1px solid var(--bd); }
        .review-detail {
          padding: 8px 10px;
          margin: 8px 0 4px;
          border-radius: 3px;
          background: var(--bg2);
        }
        .rv-reason { font-size: 12px; color: var(--t1); line-height: 1.5; }
        .rv-sug { font-size: 11px; color: var(--t2); margin-top: 4px; line-height: 1.4; }
        .rv-it {
          display: inline-block;
          margin-top: 6px;
          font-size: 10px;
          font-family: var(--font-mono);
          color: var(--t3);
          background: var(--bg3);
          padding: 1px 6px;
          border-radius: 3px;
        }
        .step-out {
          font-family: var(--font-mono);
          font-size: 10.5px;
          line-height: 1.6;
          color: var(--t2);
          white-space: pre-wrap;
          word-break: break-word;
          margin-top: 8px;
          max-height: 280px;
          overflow-y: auto;
        }

        /* RUNNING */
        .running-row {
          display: flex;
          align-items: center;
          gap: 5px;
          font-family: var(--font-mono);
          font-size: 10px;
          color: var(--t3);
          padding: 10px 18px;
        }
        @keyframes blink { 0%,100%{opacity:.2} 50%{opacity:1} }
        .dot-a { width:4px;height:4px;border-radius:50%;background:var(--acc);animation:blink 1s 0s infinite; }
        .dot-b { width:4px;height:4px;border-radius:50%;background:var(--acc);animation:blink 1s .2s infinite; }
        .dot-c { width:4px;height:4px;border-radius:50%;background:var(--acc);animation:blink 1s .4s infinite; }

        /* DIFF */
        .diff-wrap {
          font-family: var(--font-mono);
          font-size: 11px;
          line-height: 1.65;
          padding: 8px 0;
        }
        .diff-line { padding: 1px 20px; white-space: pre-wrap; word-break: break-all; }
        .dl-add  { background: #4ADE8014; color: #86EFAC; }
        .dl-rem  { background: #F8717114; color: #FCA5A5; }
        .dl-hunk { background: #D9770614; color: #FCD34D; }
        .dl-meta { color: var(--t3); }
        .dl-ctx  { color: var(--t2); }

        /* REVIEW CARDS */
        .review-scroll { padding: 10px 12px; }
        .rcard {
          background: var(--bg1);
          border: 1px solid var(--bd);
          border-radius: 4px;
          padding: 14px 16px;
          margin-bottom: 8px;
        }
        .rcard-hdr { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
        .rcard-cycle {
          font-family: var(--font-mono);
          font-size: 9px;
          background: var(--bg3);
          color: var(--t3);
          border-radius: 2px;
          padding: 2px 7px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }
        .rcard-verdict {
          font-family: var(--font-mono);
          font-size: 9px;
          border-radius: 2px;
          padding: 2px 7px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }
        .rcard p { font-size: 12px; color: var(--t2); line-height: 1.5; }
        .rcard p + p { margin-top: 5px; color: var(--t3); }

        /* SUMMARY */
        .summary {
          margin: 12px 16px;
          padding: 16px 18px;
          background: var(--bg1);
          border: 1px solid var(--bd);
          border-radius: 4px;
          border-left: 3px solid var(--acc);
        }
        .sum-status {
          display: flex;
          align-items: center;
          gap: 7px;
          font-family: var(--font-mono);
          font-size: 10px;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          margin-bottom: 14px;
        }
        .sum-dot { width:7px; height:7px; border-radius:50%; flex-shrink:0; }
        .sum-stats { display:flex; gap:28px; }
        .sum-stat { display:flex; flex-direction:column; gap:1px; }
        .sum-val {
          font-family: var(--font-mono);
          font-size: 28px;
          font-weight: 500;
          color: var(--t1);
          line-height: 1.1;
        }
        .sum-pass { font-size: 18px; }
        .sum-lbl {
          font-family: var(--font-mono);
          font-size: 9px;
          color: var(--t3);
          letter-spacing: 0.1em;
          text-transform: uppercase;
        }
        .sum-note {
          margin-top: 10px;
          font-family: var(--font-mono);
          font-size: 11px;
          line-height: 1.4;
        }
        .sum-warn { color: #FBBF24; }
        .sum-err  { color: #F87171; }
        .sum-token-row {
          display: flex;
          gap: 24px;
          margin-top: 14px;
          padding-top: 12px;
          border-top: 1px solid var(--bd);
        }
        .sum-val-sm {
          font-size: 18px !important;
        }

        /* EMPTY */
        .empty-main {
          display:flex;
          align-items:center;
          justify-content:center;
          height:100%;
          flex-direction:column;
          gap:8px;
          color: var(--t3);
        }
        .empty-main svg { color: var(--t3); stroke: var(--t3); }
        .empty-main span {
          font-family: var(--font-mono);
          font-size: 11px;
          letter-spacing: 0.05em;
          color: var(--t3);
        }
        .empty-msg { padding:20px 16px; color:var(--t3); font-size:12px; font-family: var(--font-mono); }
        .cmd-wrap {
          border-top: 1px solid var(--bd);
          background: var(--bg1);
          padding: 8px 14px;
        }
        .cmd-row {
          display: flex;
          gap: 8px;
          align-items: center;
        }
        .cmd-input {
          flex: 1;
          background: var(--bg0);
          border: 1px solid var(--bd);
          border-radius: 3px;
          color: var(--t1);
          font-family: var(--font-mono);
          font-size: 11.5px;
          padding: 7px 12px;
        }
        .cmd-input:focus {
          outline: none;
          border-color: var(--acc);
          box-shadow: 0 0 0 2px #92400E22;
        }
        .cmd-send {
          font-family: var(--font-mono);
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          border: 1px solid var(--bd2);
          background: var(--bg2);
          color: var(--t2);
          border-radius: 3px;
          padding: 7px 12px;
          transition: border-color 0.15s, color 0.15s;
        }
        .cmd-send:hover { border-color: var(--acc); color: var(--acc); }
        .cmd-hint {
          font-family: var(--font-mono);
          font-size: 10px;
          color: var(--t3);
          margin-top: 5px;
        }
        .cmd-log {
          font-family: var(--font-mono);
          font-size: 10.5px;
          border-radius: 3px;
          margin: 4px 0;
          color: var(--t2);
        }
        .cmd-log.error { color: #F87171; }
        .cmd-log.success { color: #4ADE80; }

        .offline-banner {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          z-index: 1000;
          background: #F8717122;
          border-bottom: 1px solid #F8717166;
          color: #FCA5A5;
          font-family: var(--font-mono);
          font-size: 11px;
          padding: 8px 16px;
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .offline-icon { font-size: 13px; }
        .offline-retry {
          margin-left: auto;
          background: transparent;
          border: 1px solid #F8717166;
          color: #FCA5A5;
          font-family: var(--font-mono);
          font-size: 10px;
          padding: 3px 10px;
          border-radius: 3px;
          cursor: pointer;
        }
        .offline-retry:hover { background: #F8717122; }
      `}</style>

      {backendOnline === false && (
        <div className="offline-banner">
          <span className="offline-icon">&#9888;</span>
          <span>Cannot reach backend at {API_BASE} — is the server running?</span>
          <button
            type="button"
            className="offline-retry"
            onClick={() => {
              setBackendOnline(null);
              checkBackendHealth();
            }}
          >
            Retry
          </button>
        </div>
      )}

      <div className="shell">
        {/* SIDEBAR */}
        <aside className="sidebar">
          <div className="sb-brand">
            <span className="sb-brand-name">VULCAN FORGE</span>
            <span className="sb-brand-ver">v1.0</span>
          </div>

          <div className="sb-input">
            <textarea
              placeholder="Describe the task…"
              value={goal}
              onChange={e => setGoal(e.target.value)}
              onKeyDown={e => { if (e.key==="Enter"&&!e.shiftKey){e.preventDefault();submitTask();}}}
              rows={3}
            />
            <input
              type="text"
              className="repo-input"
              placeholder="GitHub URL or local path (optional)"
              value={repoUrl}
              onChange={e => setRepoUrl(e.target.value)}
            />
            {formErr && <div className="sb-err">{formErr}</div>}
            <button
              className="sb-submit"
              onClick={submitTask}
              disabled={!goal.trim() || submitting || backendOnline === false}
            >
              {backendOnline === false                ? "Backend offline"
                : submitting
                  ? "Submitting…"
                  : "Run task"}
            </button>
            <div className="provider-indicator">
              <span
                className="provider-dot"
                style={{ background: providerReady ? "#4ADE80" : "#F87171" }}
              />
              <span className="provider-name">{providerIndicatorLabel}</span>
            </div>
          </div>

          <div className="sb-main">
            {showSettings ? (
              <div className="settings-panel">
                <div className="settings-hdr">
                  <button className="settings-back" onClick={() => setShowSettings(false)}>
                    ← Back
                  </button>
                  <span className="settings-title">Settings</span>
                </div>

                <div className="settings-body">
                  <div className="settings-field">
                    <label className="settings-label">Server API key</label>
                    <input
                      className="settings-input settings-input-secret"
                      type="password"
                      placeholder="X-API-Key for protected endpoints"
                      value={settings.serverApiKey}
                      onChange={e => saveSettings({
                        ...settings,
                        serverApiKey: e.target.value,
                      })}
                      autoComplete="off"
                    />
                    <p className="settings-hint">
                      Stored in browser local storage. Not embedded in frontend build output.
                    </p>
                  </div>

                  <div className="settings-toggle-row">
                    <label className="settings-toggle-label">
                      <input
                        type="checkbox"
                        checked={settings.useServerDefault}
                        onChange={e => saveSettings({
                          ...settings,
                          useServerDefault: e.target.checked,
                        })}
                      />
                      <span>Use server default API key</span>
                    </label>
                    <p className="settings-hint">
                      {settings.useServerDefault
                        ? "Using the server's configured Groq key"
                        : "Your key is sent directly to the provider — never stored on the server"}
                    </p>
                  </div>

                  {!settings.useServerDefault && (
                    <div className="settings-fields">
                      <div className="settings-field">
                        <label className="settings-label">Provider name</label>
                        <input
                          className="settings-input"
                          placeholder="e.g. Groq, OpenAI, DeepSeek"
                          value={settings.providerName}
                          onChange={e => saveSettings({
                            ...settings,
                            providerName: e.target.value,
                          })}
                        />
                      </div>

                      <div className="settings-field">
                        <label className="settings-label">Model</label>
                        <input
                          className="settings-input"
                          placeholder="e.g. llama-3.3-70b-versatile, gpt-4o, claude-opus-4-5"
                          value={settings.modelName}
                          onChange={e => saveSettings({
                            ...settings,
                            modelName: e.target.value,
                          })}
                        />
                      </div>

                      <div className="settings-field">
                        <label className="settings-label">API Key</label>
                        <input
                          className="settings-input settings-input-secret"
                          type="password"
                          placeholder="sk-..."
                          value={settings.apiKey}
                          onChange={e => saveSettings({
                            ...settings,
                            apiKey: e.target.value,
                          })}
                          autoComplete="off"
                        />
                      </div>

                      <div className="settings-field">
                        <label className="settings-label">
                          Base URL
                          <span className="settings-optional"> (optional)</span>
                        </label>
                        <input
                          className="settings-input"
                          placeholder="https://api.groq.com/openai/v1"
                          value={settings.baseUrl}
                          onChange={e => saveSettings({
                            ...settings,
                            baseUrl: e.target.value,
                          })}
                        />
                        <p className="settings-hint">
                          Leave blank for Groq. Any OpenAI-compatible endpoint works.
                        </p>
                      </div>

                      <div className="settings-field">
                        <label className="settings-label">Quick fill</label>
                        <div className="settings-quickfill">
                          {[
                            {
                              name: "Groq",
                              baseUrl: "https://api.groq.com/openai/v1",
                              model: "llama-3.3-70b-versatile",
                            },
                            {
                              name: "OpenRouter",
                              baseUrl: "https://openrouter.ai/api/v1",
                              model: "qwen/qwen3-coder:free",
                            },
                            {
                              name: "OpenAI",
                              baseUrl: "https://api.openai.com/v1",
                              model: "gpt-4o",
                            },
                            {
                              name: "DeepSeek",
                              baseUrl: "https://api.deepseek.com/v1",
                              model: "deepseek-chat",
                            },
                          ].map((provider) => (
                            <button
                              key={provider.name}
                              type="button"
                              className="settings-quickfill-btn"
                              onClick={() => saveSettings({
                                ...settings,
                                providerName: provider.name,
                                baseUrl: provider.baseUrl,
                                modelName: provider.model,
                              })}
                            >
                              {provider.name}
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}

                  <div className="settings-active">
                    <span
                      className="settings-active-dot"
                      style={{ background: providerReady ? "#4ADE80" : "#F87171" }}
                    />
                    <span className="settings-active-label">{settingsActiveLabel}</span>
                  </div>

                  {!settings.useServerDefault && (
                    <button className="settings-clear" onClick={resetSettings}>
                      Clear & use server default
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <>
                <div className="sb-list">
                  {tasks.length===0 && <div className="no-tasks">No tasks yet</div>}
                  {tasks.map(t => {
                    const sc = statusColor(t.status);
                    const repoLabel = repoDisplayLabel(t.repo_url);
                    return (
                      <button
                        key={t.id}
                        className={`task-item ${t.id===selectedId?"sel":""}`}
                        onClick={() => { setSelectedId(t.id); setTab("log"); }}
                      >
                        <div className="task-goal">{t.goal || "(no goal)"}</div>
                        <div className="task-meta">
                          <span className={`tdot ${t.status==="running"?"tdot-running":""}`} style={{ background:sc }} />
                          <span style={{ color:sc }}>{t.status}</span>
                          <span style={{ marginLeft:"auto" }}>{t.id?.slice(0,7)}</span>
                        </div>
                        {repoLabel && (
                          <div className="task-repo" title={t.repo_url}>{repoLabel}</div>
                        )}
                      </button>
                    );
                  })}
                </div>
                {!historyLoaded && (
                  <button
                    type="button"
                    className="load-history-btn"
                    onClick={loadHistory}
                    disabled={historyLoading || backendOnline === false}
                  >
                    {historyLoading ? "Loading history…" : "Load history"}
                  </button>
                )}
                <button
                  type="button"
                  className="settings-gear-btn"
                  onClick={() => setShowSettings(true)}
                  title="Settings"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <circle cx="12" cy="12" r="3" />
                    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                  </svg>
                  <span>Settings</span>
                </button>
              </>
            )}
          </div>
        </aside>

        {/* MAIN */}
        <main className="main">
          {!selected ? (
            <div className="empty-main" style={{ gridRow:"1/-1" }}>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>
              </svg>
              <span>Select a task or submit a new one</span>
            </div>
          ) : (
            <>
              <div className="task-hdr">
                {selected.status==="running" && (
                  <span className="hdr-pulse" style={{ background:statusColor("running"), animation:"pulse 1.2s infinite" }} />
                )}
                <span className="task-hdr-goal">{selected.goal || "(no goal)"}</span>
                <span
                  className="status-pill"
                  style={{
                    color: statusColor(selected.status),
                    borderColor: statusColor(selected.status)+"44",
                    background: statusColor(selected.status)+"11"
                  }}
                >
                  {selected.status.replace(/_/g," ")}
                </span>
                {(Number(selected.total_tokens_used || 0) > 0 || Number(selected.total_prompt_tokens || 0) > 0) && (
                  <div className={`token-counter${selected.status === "running" ? " ticking" : ""}`}>
                    <span className="token-icon">⬡</span>
                    <span className="token-val">
                      {(Number(selected.total_tokens_used || 0)).toLocaleString()}
                    </span>
                    <span className="token-lbl">tok</span>
                  </div>
                )}
              </div>

              {selected.status==="awaiting_approval" && (
                <div className="banner">
                  {selected.escalation_reason && (
                    <span className="banner-esc">⚠ {selected.escalation_reason}</span>
                  )}
                  <div className="banner-btns">
                    {!rejectOpen ? (
                      <>
                        <button className="btn-approve btn-fallback" onClick={approve}>✓ Approve & commit</button>
                        <button className="btn-reject btn-fallback" onClick={() => setRejectOpen(true)}>✕ Reject</button>
                      </>
                    ) : (
                      <div className="reject-row">
                        <input
                          placeholder="Reason (optional)"
                          value={rejectText}
                          onChange={e => setRejectText(e.target.value)}
                          onKeyDown={e => { if(e.key==="Enter") reject(); }}
                          autoFocus
                        />
                        <button className="btn-reject" onClick={reject}>Confirm</button>
                        <button className="btn-ghost" onClick={() => setRejectOpen(false)}>Cancel</button>
                      </div>
                    )}
                  </div>
                </div>
              )}

              <div className="tabs">
                {["log","diff","review"].map(t => (
                  <button
                    key={t}
                    className={`tab-btn ${tab===t?"active":""}`}
                    onClick={() => setTab(t)}
                    disabled={t!=="log" && !diffAvail}
                  >
                    {t==="log" ? "TRACE" : t==="diff" ? "DIFF" : "REVIEW"}
                  </button>
                ))}
              </div>

              <div className="panel">
                {tab==="log" && (
                  <div className="log-scroll">
                    {steps.map((s,i) => <StepRow key={i} step={s} index={i} />)}
                    {(commandLogByTask[selected?.id] || []).map((c, i) => (
                      <div key={`${c.ts}-${i}`} className={`cmd-log ${c.kind || "info"}`}>
                        {c.text}
                      </div>
                    ))}
                    {selected.status==="running" && (
                      <div className="running-row">
                        <div className="dot-a"/><div className="dot-b"/><div className="dot-c"/>
                        <span>agent running</span>
                      </div>
                    )}
                    {selected.status==="killed" && (
                      <div className="empty-msg" style={{ margin: "12px 16px", color: "#F87171" }}>
                        Task terminated by user.
                      </div>
                    )}
                    {TERMINAL.has(selected.status) && (
                      <SummaryCard task={selected} stepCount={steps.length} />
                    )}
                    <div ref={logEndRef} style={{ height:1 }} />
                  </div>
                )}
                {tab === "log" && selected && (
                  <div className="cmd-wrap">
                    <div className="cmd-row">
                      <input
                        className="cmd-input"
                        placeholder="/status"
                        value={commandText}
                        onChange={(e) => setCommandText(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            runSlashCommand();
                          }
                        }}
                      />
                      <button className="cmd-send" onClick={runSlashCommand}>Run</button>
                    </div>
                    <div className="cmd-hint">{"/approve · /reject <reason> · /stop · /retry · /status"}</div>
                  </div>
                )}

                {tab==="diff" && (
                  <div className="log-scroll">
                    {diffData?.diff
                      ? <DiffView diff={diffData.diff} />
                      : <div className="empty-msg">Loading diff…</div>
                    }
                  </div>
                )}

                {tab==="review" && (
                  <div className="log-scroll review-scroll">
                    {!diffData?.reviewer_feedback?.length
                      ? <div className="empty-msg">No review cycles — reviewer approved on first pass.</div>
                      : diffData.reviewer_feedback.map((fb,i) => {
                          const vc = fb.verdict==="approved"?"#4ADE80":fb.verdict==="needs_changes"?"#FBBF24":"#F87171";
                          return (
                            <div key={i} className="rcard">
                              <div className="rcard-hdr">
                                <span className="rcard-cycle">Cycle {fb.iteration ?? i+1}</span>
                                <span className="rcard-verdict" style={{ color:vc, background:vc+"18", border:`1px solid ${vc}33` }}>
                                  {fb.verdict || "—"}
                                </span>
                              </div>
                              {fb.reason && <p>{fb.reason}</p>}
                              {fb.suggestions && <p>{fb.suggestions}</p>}
                            </div>
                          );
                        })
                    }
                  </div>
                )}
              </div>
            </>
          )}
        </main>
      </div>
    </>
  );
}
