"""Generate per-dimension bugfix markdown from _findings.json.

Outputs one md file per dimension into docs/bugfix/, plus INDEX.md.
Run: python _gen_docs.py
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).parent
DATA = json.loads((ROOT / "_findings.json").read_text(encoding="utf-8-sig"))
FINDINGS = DATA["findings"]

# Status overrides for findings that are not a straight "Fixed in code".
STATUS = {
    "token-localstorage-xss-theft": "Mitigated (CSP) - Phase 2 tracked in SUMMARY.md",
    "secrets-in-plaintext-env": "Mitigated (env-parameterized) - production secret manager is an ops concern; see SUMMARY.md",
    "unpinned-ml-deps": "Verified - all ML deps are pinned in requirements.txt (no floating pins)",
}

DIM_TITLES = {
    "rls-isolation": "Row-Level Security & Tenant Isolation",
    "ssrf-mcp-tools": "SSRF & MCP / Tool Handler Surface",
    "auth-jwt": "Authentication & JWT",
    "orchestrator-logic": "Agent Orchestrator Logic",
    "upload-ingestion": "Upload & Ingestion Pipeline",
    "retrieval-injection": "Retrieval & Prompt-Injection",
    "async-cache-db": "Async, Cache & Database Session Hygiene",
    "frontend": "Frontend (SPA / API Client)",
    "infra-secrets": "Infrastructure, Secrets & Deployment Hardening",
}

DIM_SUMMARY = {
    "rls-isolation": (
        "Findings where the application's multi-tenant isolation depends on the "
        "Postgres Row-Level Security GUC `app.current_user_id` being bound to the "
        "*authenticated principal* and never re-bound from a caller/argument-supplied "
        "value. The cluster of CRITICALs here centered on `retrieval.retrieve()` "
        "calling `set_rls_user(session, user_id)` with the function argument, and "
        "the lexical/vector SQL having no app-layer `WHERE user_id = :uid` predicate "
        "- so any path that could influence that argument (the debug "
        "`/tools/{id}/invoke` endpoint, or the LLM via prompt injection) could read "
        "another tenant's chunks. Fixed by removing the re-bind, adding explicit "
        "predicates, force-overwriting (not `setdefault`) the `user_id` tool "
        "argument, and refusing caller-supplied `user_id`."
    ),
    "ssrf-mcp-tools": (
        "Findings on the tool-handler attack surface: arbitrary-URL HTTP handlers, "
        "MCP server attach, internal-handler import, and the admin/ownership model "
        "around tool upsert/enable/invoke. Fixed by introducing an SSRF guard "
        "(`app/core/ssrf.py`), validating every handler URL/server URL against it, "
        "admin-gating all tool mutations, refusing to overwrite builtin handlers, "
        "and allowlisting internal-tool implementation paths."
    ),
    "auth-jwt": (
        "Findings on the auth lifecycle: JWT secret fail-fast, token revocation "
        "(`token_version`), refresh-token rotation, login enumeration (timing + "
        "inactive-status leakage), and the bcrypt 72-byte truncation collision. "
        "Fixed by adding `model_post_init` fail-fast for insecure secrets, "
        "embedding `ver` in tokens and checking it on every request + refresh, "
        "adding `/auth/logout` (bumps `token_version`), rotating refresh tokens, "
        "timing-equalizing login against a dummy hash, capping passwords at 72 "
        "bytes, and using a generic anti-enumeration register message."
    ),
    "orchestrator-logic": (
        "Findings in the per-turn agent loop: duplicate `TEXT_MESSAGE_START` SSE "
        "events, swallowed Ollama errors (silent empty 200), a retry that ignored "
        "the corrected tool name, tool-failure text never streamed, the token "
        "budget not reserving room for the answer, raw-string argument fallback, "
        "and prompt-injection framing. Fixed by consolidating the stream emit "
        "shape, re-raising `OllamaError`, using the retry's emitted tool name, "
        "streaming a graceful tool-error message, reserving the answer budget, "
        "rejecting non-object tool arguments, and framing retrieved chunks as "
        "untrusted reference data."
    ),
    "upload-ingestion": (
        "Findings in document upload + ingestion: extension-only allowlist with "
        "no content validation, no `.doc` extractor, request body not capped "
        "before buffering, files written before DB commit (orphans on failure), "
        "no page/row/chunk caps (resource exhaustion / decompression bombs), "
        "binary accepted by text extractors, and background ingest errors "
        "swallowed. Fixed by magic-byte validation, Content-Length + streaming "
        "size caps, an explicit `.doc` rejection, orphan cleanup on commit "
        "failure, marking failed docs, NUL-byte guards, and hard caps on pages, "
        "rows, and extracted characters."
    ),
    "retrieval-injection": (
        "Findings in retrieval correctness / hygiene: a dead bigram guard, a cache "
        "key that omitted `top_k`, an unvalidated embedding dimension, and a dead "
        "`to_tsquery` helper. Fixed by correcting the bigram gap check, hashing "
        "`top_k` into the cache key, validating the query embedding dimension "
        "against the configured `embedding_dim`, and removing the dead helper."
    ),
    "async-cache-db": (
        "Findings in async/event-loop hygiene and DB session lifecycle: a blocking "
        "`encode()` call on the event loop, cache calls that turned a Redis outage "
        "into a 500, and `reset_rls_user` silently swallowing all exceptions. Fixed "
        "by offloading `encode` to a thread, making cache get/set/delete fail-open, "
        "and logging `reset_rls_user` failures."
    ),
    "frontend": (
        "Findings in the SPA / API client: JWTs stored in `localStorage` (XSS "
        "exfiltration risk), the SSE `stream()` path bypassing 401 handling, and a "
        "`setTimeout`-driven `AbortController` timer that was never cleared (leak). "
        "Fixed (mitigated) by adding a strict CSP via nginx, routing `stream()` 401s "
        "through the auth-failed path, and clearing the timeout timer once the fetch "
        "settles. The full httpOnly-cookie migration is tracked as Phase 2."
    ),
    "infra-secrets": (
        "Findings in deployment hardening: default Postgres creds, a shipped JWT "
        "secret, RLS not forced (owner-bypass), ports published on all interfaces, "
        "unpinned image tags, no TLS, missing security headers, an unauth `/metrics`, "
        "containers running as root, ReDoS-vulnerable `python-multipart`/`fastapi` "
        "versions, and localhost-CORS in prod. Fixed by parameterizing secrets with "
        "dev-only defaults + a config fail-fast, adding `FORCE ROW LEVEL SECURITY` + "
        "`WITH CHECK`, binding every internal port to `127.0.0.1`, pinning image "
        "tags, adding TLS + security headers + CSP to nginx, admin-gating `/metrics`, "
        "dropping privileges in the entrypoint, bumping the vulnerable pins, and "
        "rejecting localhost-CORS in prod."
    ),
}

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# Mojibake sequences (cp1252 renderings of UTF-8 punctuation) that appear in
# the double-encoded source JSON. Defined via chr() so this file itself
# stays pure ASCII and is not subject to editor normalization.
#   em dash  U+2014  -> UTF-8 E2 80 94 -> cp1252 -> a Euro "
#   en dash  U+2013  -> UTF-8 E2 80 93 -> cp1252 -> a Euro -
#   rsquo    U+2019  -> UTF-8 E2 80 99 -> cp1252 -> a Euro (tm)
#   lsquo    U+2018  -> UTF-8 E2 80 98 -> cp1252 -> a Euro (
#   ldquo    U+201C  -> UTF-8 E2 80 9C -> cp1252 -> a Euro <<
#   rdquo    U+201D  -> UTF-8 E2 80 9D -> cp1252 -> a Euro (char 0x9D)
#   bullet   U+2022  -> UTF-8 E2 80 A2 -> cp1252 -> a Euro (cents)
#   hellip   U+2026  -> UTF-8 E2 80 A6 -> cp1252 -> a Euro (broken bar)
_A = chr(0xE2)   # a-tilde
_EU = chr(0x20AC)  # euro
_9D = chr(0x9D)
MOJI = {
    _A + _EU + chr(0x94): "---",   # em dash
    _A + _EU + chr(0x93): "-",     # en dash
    _A + _EU + chr(0x99): "'",     # rsquo
    _A + _EU + chr(0x98): "'",     # lsquo
    _A + _EU + chr(0x9C): '"',      # ldquo
    _A + _EU + _9D: '"',            # rdquo
    _A + _EU + chr(0xA2): "*",     # bullet
    _A + _EU + chr(0xA6): "...",   # hellip
    chr(0xC2) + chr(0xA0): " ",     # nbsp (UTF-8 C2 A0 read as cp1252 -> Â + nbsp)
}
# Genuine Unicode punctuation -> ASCII (in case any survived unmangled).
UNI = {
    chr(0x2014): "---",  # em dash
    chr(0x2013): "-",    # en dash
    chr(0x2018): "'",    # lsquo
    chr(0x2019): "'",    # rsquo
    chr(0x201C): '"',     # ldquo
    chr(0x201D): '"',     # rdquo
    chr(0x2022): "*",    # bullet
    chr(0x2026): "...",  # hellip
    chr(0x00A0): " ",    # nbsp
    chr(0xC2): "",       # stray leftover
}


def md_escape(s: str) -> str:
    s = (s or "").replace("|", "\\|")
    for k, v in MOJI.items():
        s = s.replace(k, v)
    for k, v in UNI.items():
        s = s.replace(k, v)
    # The source JSON was multiply double-encoded in places, so some
    # decorative-punctuation mojibake survives the targeted maps. The prose
    # is English; any remaining non-ASCII is stray mojibake, so fold it to a
    # space and collapse runs. This guarantees clean, portable output.
    s = "".join(ch if ord(ch) < 128 else " " for ch in s)
    while "  " in s:
        s = s.replace("  ", " ")
    return s


def finding_block(f: dict) -> str:
    status = STATUS.get(f["id"], "Fixed")
    loc = f"{f['file']}:{f['line']}"
    out = []
    out.append(f"### `{f['id']}`\n")
    out.append("| Field | Value |")
    out.append("|---|---|")
    out.append(f"| Severity | **{f['severity'].upper()}** |")
    out.append(f"| Confidence | {f.get('confidence', '-')} |")
    out.append(f"| Category | {f.get('category', '-')} |")
    out.append(f"| Location | `{loc}` |")
    out.append(f"| Status | **{status}** |")
    out.append("")
    out.append(f"**Summary.** {md_escape(f['summary'])}\n")
    out.append(f"**Failure scenario.** {md_escape(f['failure_scenario'])}\n")
    out.append(f"**Evidence.** {md_escape(f['evidence'])}\n")
    out.append(f"**Suggested fix.** {md_escape(f['suggested_fix'])}\n")
    out.append(f"**Verification rationale.** {md_escape(f['verify_reason'])}\n")
    if f.get("notes"):
        out.append(f"**Notes.** {md_escape(f['notes'])}\n")
    return "\n".join(out)


def write_dimension(dim: str, findings: list) -> Path:
    findings = sorted(findings, key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["id"]))
    p = ROOT / f"{dim}.md"
    lines = []
    lines.append(f"# {DIM_TITLES[dim]}\n")
    lines.append(f"_{len(findings)} finding(s) in this dimension._\n")
    lines.append(DIM_SUMMARY[dim] + "\n")
    lines.append("---\n")
    for f in findings:
        lines.append(finding_block(f))
        lines.append("\n---\n")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def write_index(all_findings: list) -> Path:
    p = ROOT / "INDEX.md"
    lines = ["# Bug & Vulnerability Index\n"]
    lines.append(f"_{len(all_findings)} confirmed findings across "
                 f"{len(DIM_TITLES)} dimensions. Sorted by severity then dimension then id._\n")
    lines.append("| # | Sev | Dimension | ID | Location | Status |")
    lines.append("|---|---|---|---|---|---|")
    ordered = sorted(all_findings, key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["dimension"], f["id"]))
    for i, f in enumerate(ordered, 1):
        sev = f["severity"].upper()
        status_short = STATUS.get(f["id"], "Fixed").split(" - ")[0].split(" (")[0]
        loc = f"{f['file']}:{f['line']}"
        lines.append(f"| {i} | {sev} | {f['dimension']} | [`{f['id']}`](./{f['dimension']}.md) | `{loc}` | {status_short} |")
    lines.append("\n## By dimension\n")
    for dim, title in DIM_TITLES.items():
        cnt = sum(1 for f in all_findings if f["dimension"] == dim)
        lines.append(f"- [{title}](./{dim}.md) - {cnt} finding(s)")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def main():
    by_dim = {}
    for f in FINDINGS:
        by_dim.setdefault(f["dimension"], []).append(f)
    written = []
    for dim in DIM_TITLES:
        written.append(write_dimension(dim, by_dim.get(dim, [])))
    idx = write_index(FINDINGS)
    print(f"Wrote {len(written)} dimension files + {idx.name}")
    print(f"Total findings: {len(FINDINGS)}")


if __name__ == "__main__":
    main()