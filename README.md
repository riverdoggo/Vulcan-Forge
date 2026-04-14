# 🔥 Vulcan Forge

**Autonomous AI coding agent** — submit a task, the agent reads your codebase,
writes targeted fixes, runs tests, gets reviewed by a second AI, and commits.
All inside a Docker sandbox. Bring your own API key or use the server default.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-5-646CFF?logo=vite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Sandboxed-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

## Demo

> Placeholder: Demo GIF and run metrics will be updated after the next recorded run.

![Vulcan Forge solving a bug](vulcan_forge_demo.gif)

*Demo placeholder: Vulcan Forge run preview will appear here.*
---

## How it works

1. Submit a task goal and optional GitHub repo URL via dashboard or API
2. Agent lists the workspace and runs the test suite to find failures
3. LLM reads the failing code and writes a targeted fix
4. Tests run automatically after every successful write — no manual trigger
5. A second **Reviewer Agent** reads the diff, approves, requests changes, or escalates
6. On approval → auto-commits. On escalation → human approval gate with full diff view
7. All execution inside a **Docker sandbox** (network-isolated, memory + CPU limited)
8. Full trace, diff, and review history in the Vulcan Forge dashboard

---

## Architecture

```mermaid
flowchart TD
    A[User - POST /tasks] --> B[FastAPI]
    B --> C[Vulcan Forge Runtime]
    C --> D[Workspace Manager]
    D --> E[Docker Sandbox]
    E --> F[Agent Loop]
    F --> G[LLM Decision Engine]
    G --> H[Executor]
    H --> I[Tools]
    I --> F
    F -->|tests pass| J[git_diff]
    J --> K[Reviewer Agent]
    K -->|approved| L[git_commit - auto]
    K -->|needs_changes| F
    K -->|escalate or 3 cycles| M[awaiting_approval]
    M --> N[Human - /approve or /reject]
    N -->|approve| L
    N -->|reject| O[git checkout rollback]
```

---

## Features

| Feature | Detail |
|---------|--------|
| **Multi-agent pipeline** | Coder LLM + independent Reviewer LLM with structured JSON verdicts |
| **Docker sandbox** | `--network none`, `--memory 512m`, `--cpus 1.0` per task |
| **Human approval gate** | Escalated tasks pause for human review — approve to commit, reject to rollback |
| **Regression guard** | Edits that worsen test results auto-revert via git |
| **Token compression** | Caveman compression on reasoning, observation condensing, structured test output |
| **Loop detection** | Blocks repeated reads, idle list loops, no-op writes, full rewrites |
| **Live dashboard** | React + Vite UI with TRACE/DIFF/REVIEW tabs, live token counter, slash commands |
| **Custom providers** | Bring your own API key — Groq, OpenAI, OpenRouter, DeepSeek, any OpenAI-compatible endpoint |
| **OpenRouter fallback** | Auto-switches to OpenRouter when Groq daily limit hits |
| **SSE streaming** | Real-time step updates via Server-Sent Events, polling fallback |
| **API auth + rate limiting** | Optional `X-API-Key` header, 10 submissions/minute per IP |
| **Input sanitization** | Goal and repo URL sanitized before LLM or git use |
| **Structured logging** | JSON logs with task_id, daily rotation, 30-day retention |
| **SQLite persistence** | Tasks survive server restarts, history loads on startup |
| **Task timeout** | 10-minute hard timeout per task — hung LLM calls don't block forever |
| **Workspace cleanup** | Docker containers and directories auto-removed after task completion |

---

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI 0.111, Python 3.11, Pydantic v2 |
| LLM | Groq (`llama-3.3-70b-versatile`) + OpenRouter fallback |
| Sandbox | Docker (`--network none`, `--memory 512m`, `--cpus 1.0`) |
| Frontend | React 18, Vite 5, Geist Mono, DM Serif Display |
| Persistence | SQLite |
| Deployment | Azure VM (B2s), Docker Compose, Nginx |
| Agent tools | `list_directory` `read_file` `write_file` `run_tests` `git_diff` `git_commit` `run_command` |

---

## Quick Start

**Requirements:** Python 3.11+, Node.js 18+, Docker

**1. Clone and install**
```bash
git clone https://github.com/riverdoggo/vulcan-forge
cd vulcan-forge
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
```

**2. Configure environment**
```bash
cp backend/.env.example backend/.env
# Edit backend/.env — add your GROQ_API_KEY at minimum
```

**3. Build sandbox image**
```bash
docker build -t agent-sandbox sandbox/docker
```

**4. Start everything**
```bash
# One command — starts backend + frontend
./start.sh        # Linux/macOS
start.bat         # Windows

# Or with Docker Compose (production)
docker compose up --build
```

Dashboard: `http://localhost:3000` · API docs: `http://localhost:8000/docs`

---

## API

```http
POST /tasks
Content-Type: application/json
X-API-Key: your_key          (if VULCAN_API_KEY is set)
X-LLM-Key: sk-...            (optional — use your own LLM key)
X-LLM-Model: gpt-4o          (optional — any model name)
X-LLM-Base-URL: https://...  (optional — any OpenAI-compatible endpoint)

{ "goal": "fix failing tests", "repo_url": "https://github.com/user/repo" }
```

```http
GET  /tasks              — list all tasks
GET  /tasks/{id}/logs    — step-by-step trace
GET  /tasks/{id}/diff    — git diff + reviewer history
GET  /tasks/{id}/stream  — SSE stream of live steps
POST /tasks/{id}/approve — approve and commit
POST /tasks/{id}/reject  — rollback changes
POST /tasks/{id}/kill    — terminate running task
GET  /health             — health check
```

---

## Safety & Reliability

| Mechanism | Purpose |
|-----------|---------|
| **Rewrite protection** | `write_file` compares proposed content to current file; excessive `diff_ratio` vs a **size-based** max is rejected; identical content is rejected as a no-op. |
| **Loop guards** | Consecutive duplicate tool+input is blocked with a forced re-decision. Repeated `read_file` on one path (3× in 5 steps) blocks further reads; redundant second read of a cached path is rejected; consecutive same-path reads or list-dir stalls get an override with test context. |
| **File caching** | Reduces tokens by serving cached `read_file` results when `stat` shows the file unchanged; invalidated on writes, patches, and regression revert. |
| **Forced run_tests** | After a successful `write_file` / `apply_patch`, the next step runs `run_tests` without calling the LLM. |
| **Docker I/O** | `docker exec` uses `communicate()` with `bufsize=-1` and `-i` for full binary stdout capture. |
| **Reviewer validation** | Strict JSON schema + confidence; retry once, then safe fallback to human escalation. |
| **Kill switch** | User can stop a running task from the UI; the loop exits cleanly, logs the kill, and removes the sandbox. |
| **Step budget warning** | Near `MAX_AGENT_STEPS`, the model sees an explicit warning to prioritize a concrete code change. |
| **Regression guard** | If tests worsen vs. baseline after edits, the workspace is reverted via git and the coder gets a new instruction prefix. |

---

## Tools

| Tool | Purpose | Status |
|------|---------|--------|
| `list_directory` | Browse the workspace | ✅ |
| `read_file` | Read source files (full content; per-task cache when unchanged on disk) | ✅ |
| `apply_patch` | Apply minimal unified-diff edits to existing files | ✅ |
| `write_file` | Write full file content (guarded against oversized rewrites) | ✅ |
| `run_tests` | Run pytest in container | ✅ |
| `run_command` | Run a shell command in the sandbox (e.g. `pip install` when tests need deps) | ✅ |
| `git_diff` | Capture staged changes for review (bytecode paths unstaged first) | ✅ |
| `git_commit` | Used by the runtime after reviewer/human approval (not chosen by the coder LLM) | ✅ |
| `reviewer_agent` | Automated review step after green tests (invoked by the runtime, not the coder tool registry) | ✅ |

---

## Current Limitations

- **Single-file focus** — multi-file changes across a repo are not yet supported
- **Sandboxed dependencies** — `pip install` inside the container fails (`--network none`); packages must be pre-installed in the sandbox image
- **Groq rate limits** — free tier caps at 100k tokens/day; complex runs use 5-8k tokens. OpenRouter fallback activates automatically
- **File size** — diff truncation can occur on files above ~8KB (cap is configurable)

---

## Roadmap

| Phase | Focus | Status |
|-------|-------|--------|
| Phase 1 | Core loop, Docker sandbox, tool execution | ✅ Complete |
| Phase 2 | Repo awareness — read, list, git tools | ✅ Complete |
| Phase 3 | Human approval gate, git_diff pause, rollback | ✅ Complete |
| Phase 4 | Multi-agent reviewer loop, auto-commit, escalation | ✅ Complete |
| Phase 5 | React dashboard UI (initial) | ✅ Complete |
| Phase 6 | Dynamic repo input — `repo_url` on `POST /tasks`, GitHub clone / local copy / default workspace | ✅ Complete |
| Phase 7 | Vulcan Forge dashboard — branding, TRACE/DIFF/REVIEW, command bar, live token display, summary token stats, auto history load | ✅ Complete |
| Phase 8 | Token efficiency — observation condensing, `<latest_read_file>` dedupe, `run_tests` prompt truncation, skip-LLM `run_tests` after successful write | ✅ Complete |
| Phase 9 | Agent reliability — JSON extraction (string-aware braces, markdown fence strip), write-first prompts, redundant read block, idle read/list overrides + test context, no-op write rejection, size-based rewrite ratio, Docker `Popen` bufsize, successful-write-only double-write `run_tests` force | ✅ Complete |
| Phase 10 | Provider-agnostic settings — custom API key, model, base URL per task | ✅ Complete |
| Phase 11 | Production hardening — auth, rate limiting, sanitization, structured logging, Docker Compose, timeout, cleanup | ✅ Complete |
| Phase 12 | Azure deployment — VM, Nginx, auto-restart, GitHub Actions CI/CD | ✅ Complete |

---

## Deployment

Live at: [https://vulcan-forge.northcentralus.cloudapp.azure.com](https://vulcan-forge.northcentralus.cloudapp.azure.com)

Deployed on Azure VM (Standard_B2s, Ubuntu 22.04) behind Nginx.
See [deployment guide](docs/DEPLOYMENT.md) for full Azure setup instructions.

---

## License

MIT — see [LICENSE](LICENSE)
