from github_client import RepoFile


def compute_hotspots(
    tree: list[RepoFile],
    churn: dict[str, int],
    top_n: int = 15,
) -> list[dict]:
    """
    Risk score = (churn_rank × 0.6) + (size_rank × 0.4)
    Higher score = riskier file to touch without deep understanding.

    Returns top_n files sorted by risk, with a human-readable risk level.
    """
    if not tree:
        return []

    # Normalize churn and size to 0-1
    max_churn = max(churn.values(), default=1)
    max_size = max(f.size for f in tree) or 1

    scored = []
    for f in tree:
        churn_score = churn.get(f.path, 0) / max_churn
        size_score = f.size / max_size
        risk = round(churn_score * 0.6 + size_score * 0.4, 3)
        scored.append({
            "path": f.path,
            "size_bytes": f.size,
            "commit_count": churn.get(f.path, 0),
            "risk_score": risk,
            "risk_level": _risk_label(risk),
        })

    scored.sort(key=lambda x: x["risk_score"], reverse=True)
    return scored[:top_n]


def _risk_label(score: float) -> str:
    if score >= 0.7:
        return "critical"   # don't touch without a buddy
    if score >= 0.4:
        return "high"       # read carefully before editing
    if score >= 0.2:
        return "medium"
    return "low"


def summarize_hotspots(hotspots: list[dict]) -> str:
    """
    Returns a markdown table of hotspots for inclusion in the wiki.
    """
    if not hotspots:
        return "_No hotspot data available (repo may be too new or private commit history unavailable)_"

    lines = [
        "| Risk | File | Commits | Size |",
        "|------|------|---------|------|",
    ]
    icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
    for h in hotspots:
        lines.append(
            f"| {icon.get(h['risk_level'], '⚪')} {h['risk_level'].capitalize()} "
            f"| `{h['path']}` "
            f"| {h['commit_count']} "
            f"| {h['size_bytes']:,} bytes |"
        )
    return "\n".join(lines)
