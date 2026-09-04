# AutoQA — Autonomous Test Orchestration Agent

Paste a target web-app URL (+ optional login creds, PRD, or natural-language intent) and an autonomous
meta-agent explores it, plans meaningful test flows, audits coverage gaps, generates Playwright specs,
runs them, self-heals broken scripts vs. classifies real app defects — streaming every decision live and
producing an exportable test-quality report.

**Pipeline (state machine):** `EXPLORE → PLAN → EVALUATE → GENERATE → RUN → HEAL → REPORT`

**Stack:** React + Tailwind + shadcn/ui · FastAPI (async) · MongoDB · Gemini 3.5 Flash (via Emergent Universal Key)

---

## 1. Prerequisites

Install these on your machine first:

| Tool | Version | Check |
|------|---------|-------|
| **Python** | 3.11+ | `python3 --version` |
| **Node.js** | 18+ | `node --version` |
| **Yarn** | 1.22+ (classic) | `yarn --version` — install with `npm i -g yarn` |
| **MongoDB** | 6.0+ (Community) | `mongod --version` |
| **Git** | any | `git --version` |

> MongoDB options: install locally, **or** use a free [MongoDB Atlas](https://www.mongodb.com/atlas) cluster,
> **or** run it in Docker: `docker run -d -p 27017:27017 --name mongo mongo:6`

---

## 2. Get the code

Download / clone your project (see the platform "Save to GitHub" option), then:

```bash
cd autoqa            # the project root that contains /backend and /frontend
```

Project layout:
```
/backend      FastAPI app (server.py, orchestrator.py, event_bus.py, report_export.py)
/frontend     React app (src/, package.json)
```

---

## 3. Backend setup (FastAPI)

```bash
cd backend

# create & activate a virtual env
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# install dependencies
pip install -r requirements.txt

# if emergentintegrations is not found, install it from the Emergent index:
pip install emergentintegrations --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/
```

Create `backend/.env`:

```env
MONGO_URL="mongodb://localhost:27017"
DB_NAME="autoqa"
CORS_ORIGINS="*"
EMERGENT_LLM_KEY=sk-emergent-xxxxxxxxxxxxxxxx
```

> **EMERGENT_LLM_KEY** — the Universal Key that powers Gemini. Copy the value from your Emergent
> environment (Profile → Manage plan → Universal Key). You can also swap in your own Google Gemini
> key by editing `orchestrator.py` (`.with_model("gemini", ...)`), but the Universal Key is easiest.

Run the backend:

```bash
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

Backend is now at **http://localhost:8001** (health check: http://localhost:8001/api/).

---

## 4. Frontend setup (React)

Open a **second terminal**:

```bash
cd frontend
yarn install
```

Create `frontend/.env`:

```env
REACT_APP_BACKEND_URL=http://localhost:8001
WDS_SOCKET_PORT=3000
```

> **Important:** the frontend calls `${REACT_APP_BACKEND_URL}/api/...`, and every backend route is
> prefixed with `/api`. Do not remove that prefix.

Run the frontend:

```bash
yarn start
```

App opens at **http://localhost:3000**.

---

## 5. Use it

1. Make sure MongoDB is running (`mongod`, Docker, or Atlas URL in `.env`).
2. Backend running on `:8001`, frontend on `:3000`.
3. Open http://localhost:3000, click a **preset** (or paste a URL like `https://example.com`), and hit
   **Run Autonomous Pipeline**.
4. Watch the live DAG + decision stream; explore the Plan / Audit / Code / Runner / Healer / Report tabs;
   export the report as HTML or JSON.

---

## 6. Configuration notes

- **LLM model / per-agent model** — choose in the run form ("Per-agent model config"), or change defaults
  in `backend/orchestrator.py` (`DEFAULT_MODEL`).
- **Run budget** caps the number of flows (quick=4 / standard=5 / thorough=7) to stay fast and within LLM
  rate limits.
- **Runner is a deterministic simulation** (no real browser is launched) by design — exploration and code
  generation are real. To wire a real Playwright runner, replace `_stage_run` in `orchestrator.py` and add
  `pip install playwright && playwright install chromium`.
- **Artifacts** (screenshots/traces/videos) are referenced as metadata only; no files are written locally.

---

## 7. Common issues

| Symptom | Fix |
|--------|-----|
| `ModuleNotFoundError: emergentintegrations` | Run the `--extra-index-url` pip install in step 3 |
| Frontend can't reach backend / CORS error | Check `REACT_APP_BACKEND_URL=http://localhost:8001` and that backend is running |
| `pymongo.errors.ServerSelectionTimeoutError` | MongoDB isn't running / wrong `MONGO_URL` |
| Runs stall in `PLAN`/`GENERATE` | LLM rate limiting — wait and retry; runs still complete via deterministic fallback |
| `EMERGENT_LLM_KEY` errors | Key missing/expired — top up balance in Emergent (Profile → Manage plan → Universal Key) |

---

## 8. Production build (optional)

```bash
# frontend static build
cd frontend && yarn build      # outputs to frontend/build

# backend (no --reload) behind a process manager
cd backend && uvicorn server:app --host 0.0.0.0 --port 8001
```

Serve `frontend/build` with any static host (Nginx, Vercel, Netlify) and point
`REACT_APP_BACKEND_URL` at your deployed backend URL.
