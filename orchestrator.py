"""
LangGraph sequential pipeline — 7 steps, runs in background.

Step 1: fetch_tree         → list all code files
Step 2: fetch_content      → read key files + dep file
Step 3: analyze_hotspots   → git churn × size risk scores
Step 4: scan_deps          → OSV.dev vulnerability check
Step 5: embed_codebase     → chunk + store in pgvector
Step 6: generate_wiki      → LLM writes structured wiki
Step 7: generate_plan      → LLM writes 7-day onboarding plan
Step 8: generate_diagram   → LLM infers mermaid architecture diagram
"""
import os
import traceback
from typing import TypedDict

from groq import AsyncGroq
from langgraph.graph import END, START, StateGraph

from db import save_error, save_report
from dep_scanner import format_vuln_report, scan_vulnerabilities
from embedder import embed_and_store
from github_client import (
    CODE_EXTENSIONS,
    MAX_FILES_TO_EMBED,
    get_commit_churn,
    get_dependency_file,
    get_file_content,
    get_key_files,
    get_readme,
    get_repo_tree,
)
from hotspot import compute_hotspots, summarize_hotspots

groq = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])


# ── State ─────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    repo: str
    branch: str
    readme: str
    key_files: dict[str, str]
    dep_filename: str
    dep_content: str
    hotspots: list[dict]
    hotspot_table: str
    dep_vulns: list[dict]
    dep_report: str
    wiki: str
    onboarding_plan: str
    arch_diagram: str
    error: str
    _tree: list  # internal: file tree from fetch_tree, used by subsequent steps


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def fetch_tree(state: AgentState) -> AgentState:
    print(f"[step 1] Fetching file tree for {state['repo']}")
    tree = await get_repo_tree(state["repo"], state["branch"])
    readme = await get_readme(state["repo"])
    state["_tree"] = tree  # store for later steps
    state["readme"] = readme
    return state


async def fetch_content(state: AgentState) -> AgentState:
    print(f"[step 2] Fetching key file contents")
    tree = state.get("_tree", [])
    key_files = await get_key_files(state["repo"], tree)
    dep_filename, dep_content = await get_dependency_file(state["repo"])
    state["key_files"] = key_files
    state["dep_filename"] = dep_filename
    state["dep_content"] = dep_content
    return state


async def analyze_hotspots(state: AgentState) -> AgentState:
    print(f"[step 3] Computing hotspots via git churn")
    churn = await get_commit_churn(state["repo"])
    hotspots = compute_hotspots(state.get("_tree", []), churn)
    state["hotspots"] = hotspots
    state["hotspot_table"] = summarize_hotspots(hotspots)
    return state


async def scan_deps(state: AgentState) -> AgentState:
    print(f"[step 4] Scanning dependencies via OSV.dev")
    vulns = []
    if state["dep_filename"] and state["dep_content"]:
        vulns = await scan_vulnerabilities(state["dep_filename"], state["dep_content"])
    state["dep_vulns"] = vulns
    state["dep_report"] = format_vuln_report(vulns)
    return state


async def embed_codebase(state: AgentState) -> AgentState:
    print(f"[step 5] Chunking and embedding codebase")
    tree = state.get("_tree", [])
    # Filter to code files only, then cap at limit
    code_files = [
        f for f in tree
        if "." in f.path and f".{f.path.rsplit('.', 1)[-1]}" in CODE_EXTENSIONS
    ]
    files_to_embed = {}
    for f in code_files[:MAX_FILES_TO_EMBED]:
        content = await get_file_content(state["repo"], f.path)
        if content:
            files_to_embed[f.path] = content
    count = await embed_and_store(state["repo"], files_to_embed)
    print(f"[step 5] Stored {count} chunks")
    return state


async def generate_wiki(state: AgentState) -> AgentState:
    print(f"[step 6] Generating wiki")
    key_files_text = "\n\n".join(
        f"### `{path}`\n```\n{content[:1500]}\n```"
        for path, content in state["key_files"].items()
    )
    prompt = f"""
You are a senior engineer writing the onboarding wiki for `{state['repo']}`.

README:
{state['readme'][:3000]}

Key source files:
{key_files_text[:6000]}

Dependency file ({state['dep_filename']}):
{state['dep_content'][:1000]}

Write a structured wiki in markdown with these exact sections:
1. **What this project does** (2-3 sentences, plain English)
2. **Tech stack** (bullet list: language, frameworks, key libraries)
3. **Architecture overview** (2-4 paragraphs explaining the system structure)
4. **Key entry points** (table: file → what it does)
5. **How to run locally** (numbered steps inferred from README/config)
6. **Important patterns** (naming conventions, patterns used, things that are non-obvious)
7. **Files to read first** (ordered list — the 5 most important files to read as a new dev)

Be specific. Reference actual file names, function names, and patterns you see in the code.
Do not make up details. If unsure, say "likely" or "check the README".
"""
    resp = await groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=2000,
    )
    state["wiki"] = resp.choices[0].message.content
    return state


async def generate_onboarding_plan(state: AgentState) -> AgentState:
    print(f"[step 7] Generating 7-day onboarding plan")
    hotspot_list = ", ".join(
        f"`{h['path']}`" for h in state["hotspots"][:5] if h["risk_level"] in ("critical", "high")
    )
    prompt = f"""
You are an engineering manager onboarding a new developer to `{state['repo']}`.

Context you have:
- Wiki summary: {state['wiki'][:1500]}
- High-risk files to avoid early: {hotspot_list or 'none identified'}
- Dependency vulnerabilities count: {len(state['dep_vulns'])}

Write a concrete 7-day onboarding plan in markdown. Format:

## 7-Day Onboarding Plan

### Day 1 — Orientation
- [ ] task
- [ ] task

### Day 2–3 — Core Understanding
...

### Day 4–5 — First Contribution
...

### Day 6–7 — Full Autonomy
...

## First week pitfalls
List 3-5 specific mistakes new devs make in THIS codebase based on its structure.

## Good first issues to tackle
Suggest 3 types of safe, low-risk contributions based on the codebase structure.

Be specific. Mention actual files, patterns, and commands where relevant.
Do NOT list avoid high-risk files (the ones you identified) as Day 1 reading.
"""
    resp = await groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1200,
    )
    state["onboarding_plan"] = resp.choices[0].message.content
    return state


async def generate_arch_diagram(state: AgentState) -> AgentState:
    print(f"[step 8] Inferring architecture diagram")
    key_files_text = "\n".join(
        f"- `{p}`: {c[:300]}" for p, c in list(state["key_files"].items())[:10]
    )
    prompt = f"""
Based on this codebase structure, generate a Mermaid.js architecture diagram.

Files:
{key_files_text}

README excerpt:
{state['readme'][:1000]}

Output ONLY valid Mermaid diagram syntax. Use `graph TD` or `graph LR`.
Show the main components and how they connect. 
Label edges with the type of interaction (HTTP, DB query, event, import, etc.).
Maximum 12 nodes. Keep labels short (3-5 words).

Example format:
```mermaid
graph TD
    A[Client Browser] -->|HTTP| B[FastAPI Server]
    B -->|SQL| C[(PostgreSQL)]
```

Output only the mermaid code block, nothing else.
"""
    resp = await groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=500,
    )
    state["arch_diagram"] = resp.choices[0].message.content
    return state


# ── Graph ─────────────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("fetch_tree", fetch_tree)
    g.add_node("fetch_content", fetch_content)
    g.add_node("analyze_hotspots", analyze_hotspots)
    g.add_node("scan_deps", scan_deps)
    g.add_node("embed_codebase", embed_codebase)
    g.add_node("generate_wiki", generate_wiki)
    g.add_node("generate_onboarding_plan", generate_onboarding_plan)
    g.add_node("generate_arch_diagram", generate_arch_diagram)

    g.add_edge(START, "fetch_tree")
    g.add_edge("fetch_tree", "fetch_content")
    g.add_edge("fetch_content", "analyze_hotspots")
    g.add_edge("analyze_hotspots", "scan_deps")
    g.add_edge("scan_deps", "embed_codebase")
    g.add_edge("embed_codebase", "generate_wiki")
    g.add_edge("generate_wiki", "generate_onboarding_plan")
    g.add_edge("generate_onboarding_plan", "generate_arch_diagram")
    g.add_edge("generate_arch_diagram", END)

    return g.compile()


_graph = build_graph()


async def run_analysis(repo: str, branch: str):
    try:
        state: AgentState = {
            "repo": repo,
            "branch": branch,
            "readme": "",
            "key_files": {},
            "dep_filename": "",
            "dep_content": "",
            "hotspots": [],
            "hotspot_table": "",
            "dep_vulns": [],
            "dep_report": "",
            "wiki": "",
            "onboarding_plan": "",
            "arch_diagram": "",
            "error": "",
            "_tree": [],
        }
        result = await _graph.ainvoke(state)
        await save_report(
            repo=repo,
            branch=branch,
            wiki=result["wiki"],
            onboarding_plan=result["onboarding_plan"],
            arch_diagram=result["arch_diagram"],
            hotspots=result["hotspots"],
            dep_vulnerabilities=result["dep_vulns"],
        )
        print(f"[orchestrator] Analysis complete for {repo}")
    except Exception:
        err = traceback.format_exc()
        print(f"[orchestrator] Error:\n{err}")
        await save_error(repo, branch, err[-1000:])
