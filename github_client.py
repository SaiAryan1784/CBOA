import os
from dataclasses import dataclass
from collections import defaultdict

import httpx

TOKEN = os.environ.get("GITHUB_TOKEN", "")
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
    "Accept": "application/vnd.github.v3+json",
}

# File extensions we care about for analysis
CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
    ".cpp", ".c", ".cs", ".rb", ".php", ".swift", ".kt", ".scala",
}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".next", "vendor"}
MAX_FILE_SIZE = 80_000  # bytes — skip huge generated files
MAX_FILES_TO_EMBED = 60  # keep free tier sane


@dataclass
class RepoFile:
    path: str
    size: int
    sha: str


async def get_repo_tree(repo: str, branch: str = "main") -> list[RepoFile]:
    """Fetch the full file tree of a repo (recursive)."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1",
            headers=HEADERS,
            timeout=20,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub tree fetch failed: {resp.status_code} {resp.text[:200]}")

    data = resp.json()
    files = []
    for item in data.get("tree", []):
        if item["type"] != "blob":
            continue
        path = item["path"]
        if any(skip in path.split("/") for skip in SKIP_DIRS):
            continue
        ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        if ext in CODE_EXTENSIONS and item.get("size", 0) < MAX_FILE_SIZE:
            files.append(RepoFile(path=path, size=item.get("size", 0), sha=item["sha"]))
    return files


async def get_file_content(repo: str, path: str) -> str:
    """Fetch raw file content from GitHub."""
    import base64
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/contents/{path}",
            headers=HEADERS,
            timeout=15,
        )
    if resp.status_code != 200:
        return ""
    data = resp.json()
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return data.get("content", "")


async def get_readme(repo: str) -> str:
    """Fetch the repo README."""
    for name in ["README.md", "readme.md", "README.rst", "README"]:
        content = await get_file_content(repo, name)
        if content:
            return content[:6000]
    return ""


async def get_dependency_file(repo: str) -> tuple[str, str]:
    """
    Return (filename, content) of the first dependency file found.
    Checks: requirements.txt, package.json, pyproject.toml, go.mod, Cargo.toml
    """
    candidates = [
        "requirements.txt", "package.json", "pyproject.toml",
        "go.mod", "Cargo.toml", "Gemfile",
    ]
    for name in candidates:
        content = await get_file_content(repo, name)
        if content:
            return name, content[:8000]
    return "", ""


async def get_commit_churn(repo: str, max_commits: int = 40) -> dict[str, int]:
    """
    Returns {filepath: commit_count} — how many commits touched each file.
    This is the "churn" signal for hotspot detection.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/commits?per_page={max_commits}",
            headers=HEADERS,
            timeout=20,
        )
    if resp.status_code != 200:
        return {}

    commits = resp.json()
    churn: dict[str, int] = defaultdict(int)

    # Fetch each commit's file list — stop early if rate limit is running low
    async with httpx.AsyncClient() as client:
        for commit in commits[:30]:  # cap at 30 to avoid rate limits
            sha = commit["sha"]
            cr = await client.get(
                f"https://api.github.com/repos/{repo}/commits/{sha}",
                headers=HEADERS,
                timeout=10,
            )
            if cr.status_code in (403, 429):
                print(f"[github] Rate limited fetching commits, using partial churn data")
                break
            remaining = int(cr.headers.get("X-RateLimit-Remaining", 100))
            if remaining < 5:
                print(f"[github] Rate limit low ({remaining} left), stopping churn fetch early")
                break
            if cr.status_code == 200:
                for f in cr.json().get("files", []):
                    churn[f["filename"]] += 1

    return dict(churn)


async def get_key_files(repo: str, tree: list[RepoFile]) -> dict[str, str]:
    """
    Fetch content of the most important files:
    entry points, config files, main modules.
    Returns {path: content}.
    """
    priority_names = {
        "main.py", "app.py", "server.py", "index.ts", "index.js",
        "index.tsx", "main.ts", "main.go", "main.rs", "app.ts",
        "settings.py", "config.py", "config.ts", "Dockerfile",
    }
    priority_dirs = {"src", "app", "lib", "core", "api"}

    selected = []
    for f in tree:
        name = f.path.split("/")[-1]
        parts = f.path.split("/")
        if name in priority_names:
            selected.append(f)
        elif len(parts) <= 2 and f.path.endswith(tuple(CODE_EXTENSIONS)):
            selected.append(f)
        elif parts[0] in priority_dirs and len(parts) == 2:
            selected.append(f)
        if len(selected) >= 20:
            break

    result = {}
    for f in selected[:15]:
        content = await get_file_content(repo, f.path)
        if content:
            result[f.path] = content[:3000]  # first 3k chars per file
    return result
