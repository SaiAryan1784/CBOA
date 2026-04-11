"""
Checks dependencies against OSV.dev — completely free, no API key required.
Supports: requirements.txt, package.json, pyproject.toml, go.mod
"""
import json
import re

import httpx

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
MAX_PACKAGES = 40  # keep request size sane


def parse_dependencies(filename: str, content: str) -> list[dict]:
    """Parse a dependency file into [{name, version, ecosystem}]"""
    deps = []

    if filename == "requirements.txt":
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # strip extras like requests[security]>=2.0
            m = re.match(r"^([A-Za-z0-9_\-\.]+).*?([0-9][0-9\.\-a-zA-Z]*)?", line)
            if m:
                deps.append({
                    "name": m.group(1),
                    "version": m.group(2) or "",
                    "ecosystem": "PyPI",
                })

    elif filename == "package.json":
        try:
            data = json.loads(content)
            for section in ("dependencies", "devDependencies"):
                for name, ver in data.get(section, {}).items():
                    # strip ^ ~ from semver
                    clean_ver = re.sub(r"[^\d\.]", "", ver.lstrip("^~>="))
                    deps.append({"name": name, "version": clean_ver, "ecosystem": "npm"})
        except json.JSONDecodeError:
            pass

    elif filename == "pyproject.toml":
        for line in content.splitlines():
            m = re.search(r'"([A-Za-z0-9_\-\.]+)\s*>=?\s*([0-9][^\s"]*)"', line)
            if m:
                deps.append({"name": m.group(1), "version": m.group(2), "ecosystem": "PyPI"})

    elif filename == "go.mod":
        for line in content.splitlines():
            m = re.match(r"\s+(\S+)\s+v([0-9][^\s]*)", line)
            if m:
                deps.append({"name": m.group(1), "version": m.group(2), "ecosystem": "Go"})

    return deps[:MAX_PACKAGES]


async def scan_vulnerabilities(filename: str, content: str) -> list[dict]:
    """
    Returns a list of vulnerability findings:
    [{package, version, ecosystem, vuln_id, summary, severity}]
    """
    deps = parse_dependencies(filename, content)
    if not deps:
        return []

    # OSV batch query — one HTTP call for all packages
    queries = []
    for d in deps:
        q = {"package": {"name": d["name"], "ecosystem": d["ecosystem"]}}
        if d["version"]:
            q["version"] = d["version"]
        queries.append(q)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                OSV_BATCH_URL,
                json={"queries": queries},
                timeout=20,
            )
        if resp.status_code != 200:
            return []
        results = resp.json().get("results", [])
    except Exception:
        return []

    findings = []
    for dep, result in zip(deps, results):
        for vuln in result.get("vulns", [])[:3]:  # max 3 CVEs per package
            severity = _extract_severity(vuln)
            findings.append({
                "package": dep["name"],
                "version": dep["version"] or "unknown",
                "ecosystem": dep["ecosystem"],
                "vuln_id": vuln.get("id", ""),
                "summary": vuln.get("summary", "")[:200],
                "severity": severity,
                "osv_url": f"https://osv.dev/vulnerability/{vuln.get('id', '')}",
            })

    # Sort: critical first
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}
    findings.sort(key=lambda x: order.get(x["severity"], 4))
    return findings


def _extract_severity(vuln: dict) -> str:
    for sev in vuln.get("severity", []):
        score = sev.get("score", "")
        if "CRITICAL" in score:
            return "CRITICAL"
        if "HIGH" in score:
            return "HIGH"
        if "MEDIUM" in score:
            return "MEDIUM"
        if "LOW" in score:
            return "LOW"
    # Fallback: check CVSS score in database_specific
    for entry in vuln.get("database_specific", {}).get("severity", []):
        if isinstance(entry, str):
            return entry.upper()
    return "UNKNOWN"


def format_vuln_report(findings: list[dict]) -> str:
    if not findings:
        return "_No known vulnerabilities found in declared dependencies._"

    icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢", "UNKNOWN": "⚪"}
    lines = [
        f"**{len(findings)} vulnerabilities found**\n",
        "| Severity | Package | Version | CVE | Summary |",
        "|----------|---------|---------|-----|---------|",
    ]
    for f in findings:
        lines.append(
            f"| {icon.get(f['severity'], '⚪')} {f['severity']} "
            f"| `{f['package']}` "
            f"| {f['version']} "
            f"| [{f['vuln_id']}]({f['osv_url']}) "
            f"| {f['summary'][:80]} |"
        )
    return "\n".join(lines)
