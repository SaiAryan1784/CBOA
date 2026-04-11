# Codebase Onboarding Agent

Point it at any public GitHub repo → get a full onboarding package in under 2 minutes. Beyond a basic wiki, it produces a hotspot risk map, a dependency vulnerability report, a personalized 7-day onboarding plan, and an "ask the codebase" chat interface.

## What makes it different

| Feature | What it does |
|---------|-------------|
| **Wiki** | Architecture overview, tech stack, key entry points, how to run locally |
| **Hotspot map** | Git churn × file size = risk score. Tells new devs what NOT to touch first |
| **Dep vulnerability scan** | Hits OSV.dev (free, no key) against your requirements.txt / package.json |
| **7-day onboarding plan** | Personalized week-by-week plan based on the actual codebase structure |
| **Ask the codebase** | RAG chat — pgvector + HuggingFace embeddings, answers grounded in real code |

## Architecture

```
POST /analyze
     │
     ▼
LangGraph pipeline (8 sequential nodes)
     │
     ├─ [1] fetch_tree      → GitHub API: full file tree
     ├─ [2] fetch_content   → GitHub API: key files + dep file
     ├─ [3] analyze_hotspots → git churn × size → risk scores
     ├─ [4] scan_deps       → OSV.dev batch API → CVE findings
     ├─ [5] embed_codebase  → HF embeddings → pgvector (Neon)
     ├─ [6] generate_wiki   → Groq Llama 3.3 → structured markdown
     ├─ [7] generate_plan   → Groq Llama 3.3 → 7-day onboarding
     └─ [8] generate_diagram → Groq Llama 3.3 → Mermaid.js diagram
     │
     ▼
Neon PostgreSQL (analyses table + code_chunks + pgvector index)

GET /report/{repo}
     → returns wiki, plan, diagram, hotspots[], dep_vulns[]

POST /chat  { repo, question }
     │
     ├─ embed question (HF API)
     ├─ pgvector similarity search → top 6 chunks
     └─ Groq Llama 3.3 → grounded answer with file citations
```

## Stack

| Layer | Service | Cost |
|-------|---------|------|
| LLM | Groq Llama 3.3 70B | Free (6k req/day) |
| Embeddings | HuggingFace Inference API | Free |
| Vector DB | Neon + pgvector | Free (0.5 GB) |
| Vuln data | OSV.dev | Free, no key |
| Deploy | Render | Free (750 hrs/month) |

## Setup

```bash
git clone https://github.com/your-username/codebase-agent
cd codebase-agent
pip install -r requirements.txt
cp .env.example .env
# fill in GITHUB_TOKEN, GROQ_API_KEY, HF_TOKEN, DATABASE_URL
uvicorn main:app --reload
```

## API usage

```bash
# Start analysis (returns immediately, runs in background)
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"repo": "tiangolo/fastapi", "branch": "master"}'

# Poll for results (takes ~90 seconds)
curl http://localhost:8000/report/tiangolo__fastapi

# Chat with the codebase
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"repo": "tiangolo/fastapi", "question": "How does dependency injection work here?"}'
```

## File structure

```
codebase-agent/
├── main.py            # FastAPI — /analyze, /report, /chat endpoints
├── orchestrator.py    # LangGraph 8-step pipeline
├── github_client.py   # GitHub REST API (tree, files, commits, deps)
├── hotspot.py         # Churn × size risk scoring
├── dep_scanner.py     # OSV.dev vulnerability scanner
├── embedder.py        # HF embeddings + pgvector storage
├── rag.py             # RAG chat — retrieve + generate
├── db.py              # Neon schema + async queries
├── requirements.txt
├── render.yaml
└── .env.example
```

## What the output looks like

**Hotspot map** — tells new devs which files are minefields:
```
🔴 Critical  | `src/core/auth.py`        | 34 commits | 8,204 bytes
🟠 High      | `src/api/routes.py`       | 28 commits | 6,100 bytes
🟡 Medium    | `src/models/user.py`      | 12 commits | 3,200 bytes
```

**Dep vulnerability report** — from OSV.dev:
```
🔴 CRITICAL | requests | 2.27.0 | CVE-2023-32681 | Unintended leak of Proxy-Auth header
🟠 HIGH     | pydantic | 1.9.0  | GHSA-...       | ReDoS in email validator
```

**Ask the codebase**:
```
Q: "Where does JWT validation happen?"
A: JWT validation occurs in `src/core/auth.py` — specifically the `verify_token()`
   function on line ~42. It uses the `python-jose` library and checks the `sub`
   claim against the users table via `get_user_by_id()` in `src/db/users.py`.
```
