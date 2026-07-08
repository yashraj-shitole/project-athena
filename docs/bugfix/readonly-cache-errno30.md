# Read-only container: `[Errno 30] Read-only file system: '/home/athena/.cache'` on file indexing

_Reported 2026-07-06: indexing a file failed with
`Error: [Errno 30] Read-only file system: '/home/athena/.cache'`.
Root cause adversarially verified by a 4-dimension Workflow (root-cause /
cache-writers / fix-correctness / regression-edges, 4 agents, each reading
the real code incl. the installed `huggingface_hub/constants.py` and
`tiktoken/load.py`)._

## Root cause (confirmed)

The `api` container is hardened `read_only: true` and runs the process as
the non-root user **`athena`** (uid 1000, `HOME=/home/athena`):

- `Dockerfile:102` — `USER athena`; `infra/entrypoint-api.sh` re-execs as
  `athena` via `gosu`. So the uvicorn process's home is `/home/athena`.
- `infra/docker-compose.yml` — `read_only: true`; tmpfs mounts were
  `/tmp`, `/var/run`, **`/root/.cache`**, and `/home/athena/.local`. **No
  tmpfs for `/home/athena/.cache`** → it sits on the read-only rootfs.

Indexing loads the embedding model: `app/services/embedding.py:50`
`SentenceTransformer(settings.EMBED_MODEL_NAME)` on first use.
`sentence-transformers` / `huggingface_hub` resolve their cache to
`$HF_HOME` or, by default, `~/.cache/huggingface`. No `HF_*` env was set
anywhere in the repo, so it defaulted to **`/home/athena/.cache/huggingface`**
→ on the read-only rootfs → the download/verify write failed with **errno 30**.

A second defect compounded it: the **build-time** model pre-download
(`Dockerfile` line 63) ran as **root** (the `api-base` stage had no `USER`
directive), so it baked the model into **`/root/.cache/huggingface`** in the
image layer. At runtime the `athena` process never looks there (different
`HOME`) and the `/root/.cache` tmpfs *shadowed* that baked layer anyway — so
even though the model was pre-downloaded, it was unreachable, and the
runtime tried to re-download into the read-only `/home/athena/.cache`.

The compose comment claiming `/root/.cache` was "the sentence-transformers
model cache" was wrong: the runtime user is `athena`, not root.

## A second cache writer: tiktoken

`tiktoken`'s `cl100k_base` BPE encoding is loaded at **app startup**, not
just at ingestion: `app/services/text.py:16` runs
`_ENCODER = tiktoken.get_encoding("cl100k_base")` at module import, and
`app/services/__init__.py` imports `text` unconditionally. tiktoken's
default cache (per the installed `tiktoken/load.py`) is
`$TMPDIR/data-gym-cache` → `/tmp/data-gym-cache`, which the existing `/tmp`
tmpfs already covers, so tiktoken was *not* the source of the reported
`/home/athena/.cache` error — but it did require a network fetch on every
cold start (the tmpfs is wiped on restart).

Other libs were checked and are **not** runtime cache writers under `~/.cache`:
`nltk` (not even imported anywhere in `backend/`; no `nltk.download`),
`pandas`/`scikit-learn` (not imported in the indexing path), `pdfplumber`
(not imported; `extractors.py` uses `pypdf`), `fontconfig`/`matplotlib`
(absent).

## Fix

Bake both caches into the image at **non-home, non-tmpfs** paths and read
them read-only at runtime.

### `Dockerfile` — `api-base`
- `ENV HF_HOME=/opt/hf-cache` and `ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache`
  set **after** the `pip install` layer (so they don't invalidate the
  cached pip layer — they only need to be visible to the pre-download
  `RUN`s below and to the runtime `api` stage). The existing
  sentence-transformers pre-download `RUN` now lands in `/opt/hf-cache/hub`;
  a new `RUN` pre-bakes tiktoken's `cl100k_base` into `/opt/tiktoken-cache`.
  Both are best-effort (`|| echo WARN`) so hermetic builds still succeed.
- `RUN chown -R athena:athena /opt/hf-cache /opt/tiktoken-cache 2>/dev/null || true`
  after the pre-downloads (they ran as root) so the runtime `athena` user
  can read them. Guarded so an absent dir (best-effort download failed)
  doesn't fail the build.

### `Dockerfile` — `api` (runtime)
- `ENV HF_HUB_OFFLINE=1` and `ENV TRANSFORMERS_OFFLINE=1`. With the model
  baked, offline mode makes the load a **pure read** from `/opt/hf-cache`:
  no hub verification, no metadata write, no network — read-only-safe. (Inherited
  `HF_HOME` / `TIKTOKEN_CACHE_DIR` point at the baked caches.) Without
  offline, `huggingface_hub` would still reach the hub to verify the
  snapshot and attempt a metadata write into the read-only cache → errno 30
  *even with the model present*.

### `infra/docker-compose.yml`
- Replaced the `/root/.cache` tmpfs with `/home/athena/.cache` (uid=1000,
  gid=1000, mode=0755, 512M) — the runtime user's actual cache home. With
  HF and tiktoken relocated to baked `/opt` caches, no current writer
  strictly needs it; it is retained as cheap defense-in-depth for any
  future library that writes to `~/.cache`. `/tmp`, `/var/run`, and
  `/home/athena/.local` (debugpy) are unchanged.

## Tradeoffs / edge cases (verified)

- **Hermetic build** (pre-download failed, no network): `/opt/hf-cache` is
  empty → at runtime `HF_HUB_OFFLINE=1` yields a clear "model not found in
  offline cache" error instead of errno 30. No model means no embeddings —
  inherent, not a regression. The rejected alternative (writable tmpfs
  `HF_HOME`, no offline) would re-download ~80MB on every start and still
  risk metadata-write errno 30 on a read-only bake.
- **Local non-container dev** (uvicorn on host): `HF_HOME`/`TIKTOKEN_CACHE_DIR`
  are unset → host `~/.cache` (writable) — no regression.
- **`EMBED_MODEL_NAME` is not dynamic**: it is a process-global settings
  default, set only via env at startup, never per-connector/per-user
  (connectors are for chat LLMs, not embeddings; `document.embedding_model`
  is index-time metadata, not a model selector). So `HF_HUB_OFFLINE=1`
  blocks nothing legitimate.
- No `config.py` or `entrypoint-api.sh` change needed: HF env vars are read
  by the libraries directly; the entrypoint only chowns `STORAGE_DIR`.

## Verification
- `docker compose config` → valid.
- `docker buildx build --check` → `Check complete, no warnings found`.
- Adversarial Workflow (4 dimensions) → all **CONFIRMED**, 0 errors.
- **End-to-end build + read-only load test (definitive):** after moving the
  `HF_HOME`/`TIKTOKEN_CACHE_DIR` ENV to *after* the `pip install` layer (so
  the cached pip layer survives and the flaky transient
  `sqlalchemy==2.0.35 ... from versions: none` PyPI hiccup is no longer
  re-triggered), `docker buildx build --target api` → **exit 0**. Then ran
  the built image **`--read-only`** (the real compose `read_only: true`),
  as the `athena` runtime user, with only the `/tmp`, `/var/run`,
  `/home/athena/.cache`, `/home/athena/.local` tmpfs mounts:
  `tiktoken.get_encoding('cl100k_base')` → `TIKTOKEN_OK`;
  `SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')` →
  `EMBED_MODEL_OK dim=384`; **no `[Errno 30]`**, exit 0. This reproduces
  the exact indexing-time load path (`app/services/text.py:16` +
  `app/services/embedding.py:50`) under the exact runtime filesystem
  constraints, proving the caches are readable offline with zero writes to
  `~/.cache`.

## Files changed
- `Dockerfile` — bake HF + tiktoken caches to `/opt/hf-cache` +
  `/opt/tiktoken-cache`; `chown athena`; runtime `HF_HUB_OFFLINE=1` +
  `TRANSFORMERS_OFFLINE=1`.
- `infra/docker-compose.yml` — tmpfs `/root/.cache` → `/home/athena/.cache`;
  corrected the stale comment.

## To apply
`docker compose up -d --build` (the rebuild re-runs the pre-downloads into
the new `/opt` paths). Existing `athena_pgdata` / `athena_api_storage`
volumes are unaffected.