# Phase 5 — React UI Full Implementation Brief

## What we're building

A single-page React dashboard that connects to the existing FastAPI backend. It replaces manual `curl` commands and `test-task.py` scripts with a real UI. Everything the backend already does — submitting tasks, polling status, viewing logs, approving/rejecting — gets a proper interface.

The component file `OrchestratorUI.jsx` already exists and is fully written. The job here is scaffolding the React project, wiring it to the backend, and making one small addition to the backend to support it.

---

## Step 1 — Scaffold the React app

From the project root `C:\Projects\ai-orchistrator`:

```bash
npx create-react-app frontend
cd frontend
npm install
```

This creates `C:\Projects\ai-orchistrator\frontend\` with the standard CRA structure.

Then install the Google Fonts the UI uses — these are loaded via `@import` in the component's style tag so no npm install needed, but confirm internet access is available when the dev server runs.

---

## Step 2 — Drop in the component

Copy `OrchestratorUI.jsx` into `frontend/src/OrchestratorUI.jsx`.

Open `frontend/src/index.js` and replace the default content with:

```js
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './OrchestratorUI';

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
```

Open `frontend/src/index.css` and replace everything with:

```css
* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  background: #0D1012;
  overflow: hidden;
  height: 100vh;
}

#root {
  height: 100vh;
  overflow: hidden;
}
```

Delete `frontend/src/App.js`, `frontend/src/App.css`, `frontend/src/App.test.js`, `frontend/src/logo.svg`, `frontend/src/reportWebVitals.js` — none of them are needed.

Open `frontend/public/index.html` and change the `<title>` tag to:

```html
<title>orchestrator</title>
```

---

## Step 3 — Backend changes

Three things need to happen on the FastAPI side.

### 3a. CORS middleware

Open `backend/app/main.py`. Add this import at the top:

```python
from fastapi.middleware.cors import CORSMiddleware
```

After the `app = FastAPI(...)` line, add:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Without this, every request from the React dev server will be blocked by the browser's CORS policy.

### 3b. GET /tasks endpoint

The UI needs to list all tasks in the sidebar. Open `backend/app/api/routes.py` and add this endpoint:

```python
@router.get("/tasks")
def list_tasks():
    return list(tasks.values())
```

Where `tasks` is whatever your in-memory dict is called in that file — it's the same dict used by the existing `POST /tasks` and `GET /tasks/{task_id}` endpoints. This just returns all values as a list.

### 3c. Confirm the logs endpoint response shape

The UI expects `GET /tasks/{task_id}/logs` to return a JSON object with a `steps` key containing an array. Each step object should have at minimum:

```json
{
  "tool": "run_tests",
  "input": null,
  "result": {
    "status": "success",
    "stdout": "...",
    "verdict": "...",
    "verdict_type": "approved",
    "suggestions": "..."
  }
}
```

The `verdict`, `verdict_type`, and `suggestions` fields only matter for `reviewer_agent` steps — the UI checks `step.tool === "reviewer_agent"` before rendering them. Check what the existing logs endpoint actually returns and confirm the shape matches. If the replay store returns steps in a different structure, either adapt the endpoint to reshape them or update the field references in `OrchestratorUI.jsx` — specifically in the `StepRow` component around `step.result?.verdict`, `step.result?.verdict_type`, and `step.result?.suggestions`.

---

## Step 4 — Understand what the UI does so you can verify it works

**Sidebar — task list**

Polls `GET /tasks` every 3 seconds. Renders each task as a card with a colored status dot — blue and pulsing when running, green for completed, yellow for awaiting approval, red for error. Click a task to open it in the main panel. Most recent tasks appear at the top.

**Sidebar — new task input**

Textarea that submits on Enter (Shift+Enter for newline). Calls `POST /tasks` with `{ "goal": "..." }`. On success, automatically selects the new task and opens it in the main panel. Shows an error message if the API is unreachable.

**Main panel — log tab**

Polls `GET /tasks/{id}/logs` every 1.5 seconds while the task is running. Stops polling when status hits `completed`, `rejected`, `error`, or `max_steps_reached`. Each step renders as a row with a colored left border (color matches the tool type), a tool badge, truncated input, and result status. Click any row to expand it and see the full stdout. Reviewer agent steps expand to show the verdict, reason, and suggestions with colored backgrounds matching the verdict type. New steps animate in with a fade-slide. The log auto-scrolls to the bottom as steps arrive. A bouncing dots indicator shows while the task is running.

**Main panel — diff tab**

Only appears when task status is `awaiting_approval`. Fetches `GET /tasks/{id}/diff` once when the tab first becomes available. Renders the git diff with syntax highlighting — green background for added lines, red for removed, blue for hunk headers. All other lines are neutral.

**Main panel — review tab**

Only appears when task status is `awaiting_approval`. Shows the full reviewer feedback history from `diffData.reviewer_feedback` — each cycle gets its own card with the cycle number, verdict, reason, and suggestions. If the reviewer approved on the first pass, shows a message saying no review cycles occurred.

**Approval banner**

When status is `awaiting_approval`, a banner renders above the tabs. If `task.escalation_reason` is set, it shows the escalation message in amber. Two buttons: approve calls `POST /tasks/{id}/approve`, reject opens an inline input for a reason then calls `POST /tasks/{id}/reject` with `{ "reason": "..." }`. Both buttons refetch the task after the call completes so the status updates immediately.

**Footer bar**

When a task reaches a terminal state, shows total step count and review cycle count if any cycles occurred.

---

## Step 5 — Running both servers together

You'll have two terminals running simultaneously.

**Terminal 1 — FastAPI backend:**

```bash
cd C:\Projects\ai-orchistrator\backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 — React frontend:**

```bash
cd C:\Projects\ai-orchistrator\frontend
npm start
```

React dev server runs on `http://localhost:3000`. It proxies nothing — it talks directly to `http://localhost:8000` via the `API_BASE` constant at the top of `OrchestratorUI.jsx`. If your backend runs on a different port, change that constant.

---

## Step 6 — Verify it works end to end

1. Open `http://localhost:3000`
2. Type a task goal in the sidebar textarea, hit Enter
3. Task appears in the sidebar with a pulsing blue dot
4. Main panel shows the log tab, steps appear one by one as the agent runs
5. When tests pass and reviewer fires, a `reviewer_agent` step appears in the log — click it to expand and see the verdict
6. If reviewer approves, a `git_commit` step appears and status goes to `completed`
7. If reviewer escalates, the approval banner appears — click approve to commit or reject to roll back
8. After completion, the step count and review cycle count appear in the footer

---

## Step 7 — Repo structure after Phase 5

```
ai-orchistrator/
├── backend/
│   └── app/
│       ├── main.py          ← CORS middleware added
│       └── api/routes.py    ← GET /tasks added
├── frontend/                ← NEW
│   ├── public/
│   │   └── index.html
│   └── src/
│       ├── index.js         ← simplified
│       ├── index.css        ← reset only
│       └── OrchestratorUI.jsx  ← the whole UI
├── logs/
│   └── last_run.log
└── workspaces/
```

Add `frontend/node_modules` to the root `.gitignore` if it isn't already there.

---

## Known integration points to double-check

The UI references these exact API shapes — if any of these don't match what the backend currently returns, the relevant UI section will silently show nothing or break:

- `POST /tasks` → must return `{ "id": "uuid..." }` — used to auto-select the new task
- `GET /tasks` → must return an array of task objects each with at minimum `id`, `goal`, `status`
- `GET /tasks/{id}` → must return task object with `status`, `goal`, `id`, `review_iterations`, `escalation_reason`
- `GET /tasks/{id}/logs` → must return `{ "steps": [...] }`
- `GET /tasks/{id}/diff` → must return `{ "diff": "...", "reviewer_feedback": [...] }`
- `POST /tasks/{id}/approve` → any 200 response
- `POST /tasks/{id}/reject` → accepts `{ "reason": "..." }` body, any 200 response

---

That's everything Cursor needs. It has the source files, it has this brief, and it has the `OrchestratorUI.jsx` component already written. The only real work is the scaffold, the two backend additions, and verifying the API shapes match.
