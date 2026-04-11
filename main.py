import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from db import get_pool, init_schema
from orchestrator import run_analysis
from rag import answer_question


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_schema()
    yield
    pool = await get_pool()
    await pool.close()


app = FastAPI(title="Codebase Onboarding Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


class AnalyzeRequest(BaseModel):
    repo: str          # e.g. "facebook/react"
    branch: str = "main"


class ChatRequest(BaseModel):
    repo: str
    question: str


def _normalize_repo(repo: str) -> str:
    """Accept full GitHub URLs or owner/repo shorthand."""
    import re
    repo = repo.strip().rstrip("/").removesuffix(".git")
    m = re.search(r"github\.com/([^/]+/[^/]+)", repo)
    return m.group(1) if m else repo


@app.post("/analyze")
async def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks):
    """
    Kick off full analysis for a GitHub repo.
    Returns immediately — poll /report/{repo_slug} for results.
    """
    req.repo = _normalize_repo(req.repo)
    slug = req.repo.replace("/", "__")
    pool = await get_pool()

    existing = await pool.fetchrow(
        "SELECT status FROM analyses WHERE repo = $1 AND branch = $2",
        req.repo, req.branch,
    )
    if existing and existing["status"] == "running":
        return {"status": "already_running", "repo": req.repo}

    await pool.execute(
        """
        INSERT INTO analyses (repo, branch, status)
        VALUES ($1, $2, 'running')
        ON CONFLICT (repo, branch) DO UPDATE SET status = 'running', created_at = NOW()
        """,
        req.repo, req.branch,
    )

    background_tasks.add_task(run_analysis, req.repo, req.branch)
    return {"status": "started", "repo": req.repo, "poll": f"/report/{slug}"}


@app.get("/report/{repo_slug}")
async def get_report(repo_slug: str):
    """Get the full analysis report once complete."""
    repo = repo_slug.replace("__", "/")
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM analyses WHERE repo = $1 ORDER BY created_at DESC LIMIT 1",
        repo,
    )
    if not row:
        raise HTTPException(404, "No analysis found for this repo. Run /analyze first.")

    return {
        "repo": row["repo"],
        "status": row["status"],
        "wiki": row["wiki"],
        "onboarding_plan": row["onboarding_plan"],
        "arch_diagram": row["arch_diagram"],
        "hotspots": row["hotspots"],
        "dep_vulnerabilities": row["dep_vulnerabilities"],
        "error": row["error_message"],
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    """Ask a question about an already-analyzed repo."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT status FROM analyses WHERE repo = $1 AND status = 'complete'",
        req.repo,
    )
    if not row:
        raise HTTPException(400, "Repo not yet analyzed or analysis still running.")

    answer = await answer_question(req.repo, req.question)
    return {"question": req.question, "answer": answer}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
