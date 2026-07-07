"""Publish eval / coverage artifacts after a release.

Walks the report directories, copies HTML/JSON reports to a stable
`testing/reports/published/` location, and writes a small
`index.html` with a table of contents.
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    src_dirs = [
        Path("testing/llm_evals/reports"),
        Path("testing/reports"),
        Path("testing/coverage"),
    ]
    out = Path("testing/reports/published")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    index_lines = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'><title>Athena CI artifacts</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:24px;}",
        "a{color:#0a66c2;}h1{margin-bottom:4px;}</style></head><body>",
        f"<h1>Athena CI artifacts</h1>",
        f"<p>Published {datetime.now(timezone.utc).isoformat()}</p>",
        "<ul>",
    ]

    for src in src_dirs:
        if not src.exists():
            continue
        for p in src.rglob("*"):
            if p.is_dir() or p.name.startswith("."):
                continue
            rel = p.relative_to(src)
            dest = out / src.name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            index_lines.append(
                f'<li><a href="{src.name}/{rel.as_posix()}">{p.name}</a></li>'
            )

    index_lines.append("</ul></body></html>")
    (out / "index.html").write_text("\n".join(index_lines), encoding="utf-8")
    print(f"Published to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
