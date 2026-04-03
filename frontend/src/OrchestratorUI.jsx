import { useState, useEffect, useRef, useCallback, useMemo } from "react";

const API_BASE = process.env.REACT_APP_API_BASE || "http://localhost:8000";
const TERMINAL = new Set(["completed", "rejected", "error", "max_steps_reached", "killed"]);
const DIFF_VISIBLE = new Set(["awaiting_approval", "completed", "rejected"]);

const TOOL_COLOR = {
  list_directory: "#4C9BE8",
  read_file:      "#7C6AF7",
  write_file:     "#3DD68C",
  run_tests:      "#C678DD",
  git_diff:       "#E5C07B",
  git_commit:     "#56B6C2",
  reviewer_agent: "#E06C75",
};
const toolColor = (t) => TOOL_COLOR[(t||"").toLowerCase()] || "#5A6472";

const STATUS_COLOR = {
  running:           "#4C9BE8",
  completed:         "#3DD68C",
  awaiting_approval: "#E5C07B",
  rejected:          "#E06C75",
  error:             "#E06C75",
  max_steps_reached: "#C678DD",
  killed:            "#E06C75",
  pending:           "#5A6472",
};
const statusColor = (s) => STATUS_COLOR[s] || "#5A6472";

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
    verdictType === "approved" ? "#3DD68C"
    : verdictType === "needs_changes" ? "#E5C07B"
    : verdictType === "escalate_to_human" ? "#E06C75"
    : "#5A6472";

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
        <span className="step-st" style={{ color: status==="success"?"#3DD68C" : status==="error"?"#E06C75" : "#5A6472" }}>
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
      <div className="sum-note">
        Tokens used: {promptTokens.toLocaleString()} prompt / {completionTokens.toLocaleString()} completion
      </div>
    </div>
  );
}

export default function App() {
  const [tasks,       setTasks]       = useState([]);
  const [selectedId,  setSelectedId]  = useState(null);
  const [goal,        setGoal]        = useState("");
  const [repoUrl,     setRepoUrl]     = useState("");
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

  const fetchTasks = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/tasks`);
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
  }, [historyLoaded]);

  useEffect(() => {
    fetchTasks();
    const id = setInterval(fetchTasks, 2500);
    return () => clearInterval(id);
  }, [fetchTasks]);

  const loadHistory = async () => {
    try {
      const r = await fetch(`${API_BASE}/tasks/history`);
      if (!r.ok) return;
      const data = await r.json();
      const list = Array.isArray(data) ? data : [];
      setTasks(prev => {
        const ids = new Set(prev.map(t => t.id));
        const newTasks = list.filter(t => t.id && !ids.has(t.id));
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
    }
  };

  const fetchLogs = useCallback(async (id) => {
    if (!id) return;
    try {
      const r = await fetch(`${API_BASE}/tasks/${id}/logs`);
      if (!r.ok) return;
      setLogs(await r.json());
    } catch {}
  }, []);

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
        const es = new EventSource(`${API_BASE}/tasks/${selectedId}/stream`);
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
  }, [selectedId, selected?.status, fetchLogs, fetchTasks]);

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
        const r = await fetch(`${API_BASE}/tasks/${selected.id}/diff`);
        if (r.ok) { setDiffData(await r.json()); setDiffFor(selected.id); }
      } catch {}
    })();
  }, [selected?.id, selected?.status, diffFor]);

  const submitTask = async () => {
    const g = goal.trim();
    if (!g || submitting) return;
    setSubmitting(true); setFormErr("");
    try {
      const r = await fetch(`${API_BASE}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
    await fetch(`${API_BASE}/tasks/${selected.id}/approve`, { method: "POST" });
    fetchTasks(); fetchLogs(selected.id);
  };

  const reject = async () => {
    if (!selected) return;
    await fetch(`${API_BASE}/tasks/${selected.id}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: rejectText }),
    });
    setRejectOpen(false); setRejectText("");
    fetchTasks(); fetchLogs(selected.id);
  };

  const killTask = async () => {
    if (!selected) return;
    if (!window.confirm("Are you sure you want to terminate this agent?")) return;
    try {
      const r = await fetch(`${API_BASE}/tasks/${selected.id}/kill`, { method: "POST" });
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
        await fetch(`${API_BASE}/tasks/${selected.id}/approve`, { method: "POST" });
        appendCommandLog(selected.id, "Approved current task.", "success");
        await fetchTasks();
        await fetchLogs(selected.id);
      } else if (cmd === "/reject") {
        await fetch(`${API_BASE}/tasks/${selected.id}/reject`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: arg }),
        });
        appendCommandLog(selected.id, `Rejected current task${arg ? `: ${arg}` : "."}`, "success");
        await fetchTasks();
        await fetchLogs(selected.id);
      } else if (cmd === "/stop") {
        await fetch(`${API_BASE}/tasks/${selected.id}/kill`, { method: "POST" });
        appendCommandLog(selected.id, "Stop requested for current task.", "success");
        await fetchTasks();
        await fetchLogs(selected.id);
      } else if (cmd === "/retry") {
        const r = await fetch(`${API_BASE}/tasks`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ goal: selected.goal || "", repo_url: selected.repo_url || "" }),
        });
        if (!r.ok) throw new Error("retry failed");
        const d = await r.json();
        appendCommandLog(selected.id, `Retried as new task ${String(d.id || "").slice(0, 7)}.`, "success");
        await fetchTasks();
        setSelectedId(d.id);
        setTab("log");
      } else if (cmd === "/status") {
        const promptTokens = Number(selected.total_prompt_tokens || 0).toLocaleString();
        const completionTokens = Number(selected.total_completion_tokens || 0).toLocaleString();
        appendCommandLog(
          selected.id,
          `Status: ${selected.status} | Steps: ${steps.length} | Tokens: ${promptTokens} prompt / ${completionTokens} completion`,
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
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        :root {
          --bg0: #0e1117; --bg1: #161b22; --bg2: #1c2128; --bg3: #262d36;
          --bd: #30363d; --bd2: #3d444d;
          --t1: #e6edf3; --t2: #8b949e; --t3: #6e7681;
          --acc: #388bfd;
          --font: 'Inter', system-ui, sans-serif;
          --mono: 'JetBrains Mono', monospace;
        }
        html, body, #root {
          height: 100%; width: 100%; overflow: hidden;
          background: var(--bg0); color: var(--t1);
          font-family: var(--font); font-size: 13px; line-height: 1.5;
          -webkit-font-smoothing: antialiased;
        }
        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--bd2); border-radius: 3px; }
        button { font-family: var(--font); cursor: pointer; }
        input, textarea { font-family: var(--font); }

        .shell {
          display: grid;
          grid-template-columns: 240px 1fr;
          height: 100vh;
          overflow: hidden;
        }

        /* SIDEBAR */
        .sidebar {
          display: grid;
          grid-template-rows: auto auto 1fr;
          height: 100vh;
          overflow: hidden;
          border-right: 1px solid var(--bd);
          background: var(--bg1);
        }
        .sb-main {
          display: flex;
          flex-direction: column;
          min-height: 0;
          overflow: hidden;
        }
        .sb-brand {
          padding: 12px 14px;
          border-bottom: 1px solid var(--bd);
          display: flex; align-items: baseline; gap: 7px;
        }
        .sb-brand-name { font-size: 13px; font-weight: 600; color: var(--t1); letter-spacing: -0.01em; }
        .sb-brand-ver {
          font-size: 10px; font-family: var(--mono); color: var(--t3);
          background: var(--bg3); padding: 1px 5px; border-radius: 3px;
        }
        .sb-input { padding: 10px; border-bottom: 1px solid var(--bd); }
        .sb-input textarea {
          width: 100%; background: var(--bg0); border: 1px solid var(--bd);
          border-radius: 6px; padding: 8px 10px; color: var(--t1);
          font-size: 12px; resize: none; line-height: 1.5; height: 76px;
          transition: border-color 0.15s;
        }
        .sb-input textarea:focus { outline: none; border-color: var(--acc); }
        .sb-input textarea::placeholder { color: var(--t3); }
        .repo-input {
          width: 100%;
          margin-top: 5px;
          background: var(--bg0);
          border: 1px solid var(--bd);
          border-radius: 6px;
          padding: 6px 10px;
          color: var(--t1);
          font-size: 11px;
          font-family: var(--mono);
          transition: border-color 0.15s;
        }
        .repo-input:focus { outline: none; border-color: var(--acc); }
        .repo-input::placeholder { color: var(--t3); }
        .repo-hint {
          display: block;
          font-size: 10px;
          color: var(--t3);
          margin-top: 3px;
          font-family: var(--mono);
        }
        .sb-submit {
          margin-top: 6px; width: 100%; padding: 7px 0;
          background: var(--acc); border: none; border-radius: 6px;
          color: #fff; font-size: 12px; font-weight: 500;
          transition: opacity 0.15s;
        }
        .sb-submit:disabled { opacity: 0.4; cursor: not-allowed; }
        .sb-submit:hover:not(:disabled) { opacity: 0.88; }
        .sb-err { margin-top: 5px; font-size: 11px; color: #E06C75; }
        .sb-list { flex: 1; overflow-y: auto; padding: 6px; min-height: 0; }
        .load-history-btn {
          width: calc(100% - 12px);
          margin: 4px 6px 8px;
          padding: 6px 0;
          background: transparent;
          border: 1px solid var(--bd);
          border-radius: 6px;
          color: var(--t3);
          font-size: 11px;
          font-family: var(--mono);
          cursor: pointer;
          transition: border-color 0.15s, color 0.15s;
        }
        .load-history-btn:hover:not(:disabled) { border-color: var(--bd2); color: var(--t2); }
        .load-history-btn:disabled { opacity: 0.55; cursor: default; }
        .no-tasks { padding: 20px 10px; text-align: center; color: var(--t3); font-size: 12px; }

        .task-item {
          padding: 8px 10px; border-radius: 6px; border: 1px solid transparent;
          cursor: pointer; background: transparent; text-align: left;
          width: 100%; color: var(--t1); display: block; margin-bottom: 2px;
          transition: background 0.1s, border-color 0.1s;
        }
        .task-item:hover { background: var(--bg2); }
        .task-item.sel { background: var(--bg2); border-color: var(--bd2); }
        .task-goal {
          font-size: 12px; line-height: 1.35; color: var(--t1);
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 4px;
        }
        .task-meta {
          display: flex; align-items: center; gap: 5px;
          font-size: 10px; font-family: var(--mono); color: var(--t3);
        }
        .task-repo {
          font-size: 10px;
          font-family: var(--mono);
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
          padding: 9px 16px; border-bottom: 1px solid var(--bd);
          background: var(--bg1); display: flex; align-items: center;
          gap: 10px; min-height: 42px;
        }
        .task-hdr-goal {
          font-size: 13px; font-weight: 500; flex: 1;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .status-pill {
          font-size: 10px; font-family: var(--mono);
          padding: 2px 7px; border-radius: 3px; border: 1px solid;
          white-space: nowrap; flex-shrink: 0;
        }
        .hdr-pulse { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }

        .banner {
          padding: 7px 16px; border-bottom: 1px solid #E5C07B33;
          background: #E5C07B08; display: flex; align-items: center;
          gap: 10px; flex-wrap: wrap;
        }
        .banner-esc { font-size: 11px; color: #E5C07B; flex: 1; min-width: 0; }
        .banner-btns { display: flex; gap: 6px; align-items: center; margin-left: auto; }
        .btn-approve {
          padding: 5px 12px; border-radius: 5px;
          border: 1px solid #3DD68C55; background: #3DD68C18;
          color: #3DD68C; font-size: 11px; font-weight: 500;
        }
        .btn-fallback { opacity: 0.55; }
        .btn-fallback:hover { opacity: 0.8; }
        .btn-approve:hover { background: #3DD68C28; }
        .btn-reject {
          padding: 5px 12px; border-radius: 5px;
          border: 1px solid #E06C7555; background: #E06C7518;
          color: #E06C75; font-size: 11px; font-weight: 500;
        }
        .btn-reject:hover { background: #E06C7528; }
        .btn-ghost {
          padding: 5px 10px; border-radius: 5px;
          border: 1px solid var(--bd); background: transparent;
          color: var(--t2); font-size: 11px;
        }
        .btn-kill {
          padding: 5px 10px; border-radius: 5px;
          border: 1px solid #E06C7555; background: #E06C7512;
          color: #E06C75; font-size: 11px; font-weight: 500;
          flex-shrink: 0;
        }
        .btn-kill:hover { background: #E06C7524; }
        .reject-row { width: 100%; display: flex; gap: 6px; margin-top: 4px; }
        .reject-row input {
          flex: 1; background: var(--bg0); border: 1px solid var(--bd);
          border-radius: 5px; padding: 5px 8px; color: var(--t1); font-size: 11px;
        }
        .reject-row input:focus { outline: none; border-color: var(--acc); }

        .tabs {
          display: flex; border-bottom: 1px solid var(--bd);
          background: var(--bg1); padding: 0 12px;
        }
        .tab-btn {
          padding: 8px 12px; background: none; border: none;
          border-bottom: 2px solid transparent; color: var(--t3);
          font-size: 12px; font-weight: 500; transition: color 0.1s; margin-bottom: -1px;
        }
        .tab-btn:hover:not(:disabled) { color: var(--t2); }
        .tab-btn.active { color: var(--t1); border-bottom-color: var(--acc); }
        .tab-btn:disabled { opacity: 0.3; cursor: not-allowed; }

        .panel { overflow: hidden; display: flex; flex-direction: column; min-height: 0; }
        .log-scroll { flex: 1; overflow-y: auto; padding-bottom: 8px; }

        /* STEPS */
        .step {
          border-left: 2px solid var(--bc, #5A6472);
          margin: 0 12px 1px; border-radius: 0 4px 4px 0;
          background: var(--bg1);
          animation: fadeIn .18s ease;
        }
        .step:first-child { margin-top: 8px; }
        @keyframes fadeIn { from{opacity:0;transform:translateX(-3px)} to{opacity:1;transform:none} }
        .step-hd {
          width: 100%; display: flex; align-items: center; gap: 7px;
          padding: 5px 10px; background: none; border: none; color: var(--t1); text-align: left;
        }
        .step-hd:hover { background: var(--bg2); border-radius: 0 4px 4px 0; }
        .step-num { font-family: var(--mono); font-size: 10px; color: var(--t3); min-width: 20px; }
        .step-tag {
          font-family: var(--mono); font-size: 10px; font-weight: 500;
          padding: 2px 6px; border-radius: 3px; white-space: nowrap; flex-shrink: 0;
        }
        .step-inp {
          font-family: var(--mono); font-size: 11px; color: var(--t2);
          min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .step-spacer { flex: 1; }
        .verdict-chip {
          font-size: 10px; font-family: var(--mono);
          padding: 2px 7px; border-radius: 3px; white-space: nowrap; flex-shrink: 0;
        }
        .step-st {
          font-family: var(--mono); font-size: 10px;
          white-space: nowrap; flex-shrink: 0; min-width: 48px; text-align: right;
        }
        .step-arr { color: var(--t3); font-size: 14px; transition: transform 0.15s; flex-shrink: 0; line-height: 1; }
        .step-body { padding: 0 10px 10px; border-top: 1px solid var(--bd); }
        .review-detail {
          padding: 8px 10px; margin: 8px 0 4px;
          border-radius: 3px; background: var(--bg2);
        }
        .rv-reason { font-size: 12px; color: var(--t1); line-height: 1.5; }
        .rv-sug { font-size: 11px; color: var(--t2); margin-top: 4px; line-height: 1.4; }
        .rv-it {
          display: inline-block; margin-top: 6px; font-size: 10px;
          font-family: var(--mono); color: var(--t3);
          background: var(--bg3); padding: 1px 6px; border-radius: 3px;
        }
        .step-out {
          font-family: var(--mono); font-size: 11px; line-height: 1.55;
          color: #adbac7; white-space: pre-wrap; word-break: break-word;
          margin-top: 8px; max-height: 260px; overflow-y: auto;
        }

        /* RUNNING */
        .running-row {
          display: flex; align-items: center; gap: 5px;
          padding: 8px 14px; color: var(--t3); font-size: 11px; font-family: var(--mono);
        }
        @keyframes blink { 0%,100%{opacity:.2} 50%{opacity:1} }
        .dot-a { width:4px;height:4px;border-radius:50%;background:var(--acc);animation:blink 1s 0s infinite; }
        .dot-b { width:4px;height:4px;border-radius:50%;background:var(--acc);animation:blink 1s .2s infinite; }
        .dot-c { width:4px;height:4px;border-radius:50%;background:var(--acc);animation:blink 1s .4s infinite; }

        /* DIFF */
        .diff-wrap { font-family: var(--mono); font-size: 11.5px; line-height: 1.6; padding: 6px 0; }
        .diff-line { padding: 0 16px; white-space: pre-wrap; word-break: break-all; }
        .dl-add  { background: #2ea04326; color: #7ee787; }
        .dl-rem  { background: #f8514926; color: #ff7b72; }
        .dl-hunk { background: #388bfd18; color: #79c0ff; }
        .dl-meta { color: var(--t3); }
        .dl-ctx  { color: #adbac7; }

        /* REVIEW CARDS */
        .review-scroll { padding: 10px 12px; }
        .rcard {
          background: var(--bg1); border: 1px solid var(--bd);
          border-radius: 6px; padding: 12px 14px; margin-bottom: 8px;
        }
        .rcard-hdr { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
        .rcard-cycle {
          font-size: 10px; font-family: var(--mono); color: var(--t3);
          background: var(--bg3); padding: 1px 6px; border-radius: 3px;
        }
        .rcard-verdict {
          font-size: 11px; font-family: var(--mono);
          padding: 1px 7px; border-radius: 3px;
        }
        .rcard p { font-size: 12px; color: var(--t2); line-height: 1.5; }
        .rcard p + p { margin-top: 5px; color: var(--t3); }

        /* SUMMARY */
        .summary {
          margin: 10px 12px 12px; padding: 14px 16px;
          background: var(--bg1); border: 1px solid var(--bd); border-radius: 8px;
        }
        .sum-status {
          display: flex; align-items: center; gap: 7px;
          font-size: 11px; font-family: var(--mono); font-weight: 500;
          letter-spacing: 0.06em; margin-bottom: 12px;
        }
        .sum-dot { width:7px; height:7px; border-radius:50%; flex-shrink:0; }
        .sum-stats { display:flex; gap:28px; }
        .sum-stat { display:flex; flex-direction:column; gap:1px; }
        .sum-val { font-size:26px; font-family:var(--mono); font-weight:500; color:var(--t1); line-height:1.1; }
        .sum-pass { font-size:18px; }
        .sum-lbl { font-size:9px; font-family:var(--mono); color:var(--t3); letter-spacing:0.08em; }
        .sum-note { margin-top:10px; font-size:11px; line-height:1.4; }
        .sum-warn { color:#E5C07B; }
        .sum-err  { color:#E06C75; }

        /* EMPTY */
        .empty-main {
          display:flex; align-items:center; justify-content:center;
          height:100%; flex-direction:column; gap:8px; color:var(--t3);
        }
        .empty-main span { font-size:12px; }
        .empty-msg { padding:20px 16px; color:var(--t3); font-size:12px; }
        .cmd-wrap {
          border-top: 1px solid var(--bd);
          background: var(--bg1);
          padding: 8px 12px;
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
          border-radius: 6px;
          color: var(--t1);
          font-family: var(--mono);
          font-size: 12px;
          padding: 7px 10px;
        }
        .cmd-input:focus { outline: none; border-color: var(--acc); }
        .cmd-send {
          border: 1px solid var(--bd2);
          background: var(--bg2);
          color: var(--t1);
          border-radius: 6px;
          padding: 7px 10px;
          font-size: 11px;
        }
        .cmd-hint {
          margin-top: 6px;
          font-size: 10px;
          color: var(--t3);
          font-family: var(--mono);
        }
        .cmd-log {
          margin: 8px 12px 0;
          padding: 6px 8px;
          border-radius: 6px;
          background: var(--bg1);
          border: 1px solid var(--bd);
          font-family: var(--mono);
          font-size: 11px;
          color: var(--t2);
        }
        .cmd-log.error { color: #E06C75; }
        .cmd-log.success { color: #3DD68C; }
      `}</style>

      <div className="shell">
        {/* SIDEBAR */}
        <aside className="sidebar">
          <div className="sb-brand">
            <span className="sb-brand-name">orchestrator</span>
            <span className="sb-brand-ver">v0.4</span>
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
            <span className="repo-hint">public repos only · leave blank for demo workspace</span>
            {formErr && <div className="sb-err">{formErr}</div>}
            <button className="sb-submit" onClick={submitTask} disabled={!goal.trim()||submitting}>
              {submitting ? "Submitting…" : "Run task"}
            </button>
          </div>

          <div className="sb-main">
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
            <button
              type="button"
              className="load-history-btn"
              onClick={loadHistory}
              disabled={historyLoaded}
            >
              {historyLoaded ? "History loaded" : "Load history"}
            </button>
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
                {(selected.status === "running" || selected.status === "pending") && (
                  <button type="button" className="btn-kill btn-fallback" onClick={killTask}>
                    Kill task
                  </button>
                )}
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
                    {t==="log" ? "Log" : t==="diff" ? "Diff" : "Review"}
                    {t!=="log" && !diffAvail ? " (pending)" : ""}
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
                      <div className="empty-msg" style={{ margin: "12px 16px", color: "#E06C75" }}>
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
                    <div className="cmd-hint">Type / for commands</div>
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
                          const vc = fb.verdict==="approved"?"#3DD68C":fb.verdict==="needs_changes"?"#E5C07B":"#E06C75";
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
