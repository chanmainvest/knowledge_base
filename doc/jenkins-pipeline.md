# Nightly Jenkins scrape pipeline

This repo ships a [`Jenkinsfile`](../Jenkinsfile) that runs the full scrape →
ingest → extract pipeline every night at 03:00, with **one stage per major
source category**. Because the Jenkins controller has no Python / `uv` /
Playwright / `yt-dlp`, each stage shells out to a self-contained **`kb` Docker
image** (see [`Dockerfile`](../Dockerfile) and the `kb` service in
[`docker-compose.yml`](../docker-compose.yml)) that has every tool baked in.

```
Jenkins (cron 03:00) ──► docker compose run --rm kb <cmd> ──► host Postgres + data/
       │                              │
       │                              └─ kb:latest image (Python 3.12, uv,
       │                                 yt-dlp, Playwright chromium, ssh client)
       └─ also talks to camoufox container (HKEJ) over the host port
```

The container mounts the host `data/` directory and reaches the host's existing
Postgres via `host.docker.internal`, so your 26k items and 2.2 GB of scraped
content are shared with the local `uv run kb` workflow — no second database.

> **The data/logs mounts must use absolute host paths** (`DATA_DIR_HOST` /
> `LOGS_DIR_HOST` in `.env`, e.g. `/host_mnt/b/chanmainvest/knowledge_base/data`).
> The Jenkins container bind-mounts `B:\` at `/work`, but the kb sibling
> containers it spawns via `docker.sock` resolve their bind sources in the
> **Docker Desktop VM's** filesystem — not the Jenkins container's. With the
> old relative `./data` source, the daemon silently created an orphan
> `/work/chanmainvest/knowledge_base/data` inside the VM and every nightly
> scrape vanished there while Postgres recorded `/app/data/...` paths
> (discovered and rescued 2026-08-14). Same rule as `SSH_KEY_DIR` below.

---

## 0. Prerequisite: give the Jenkins controller Docker access

The pipeline's `sh 'docker compose run ...'` steps execute **on the Jenkins
controller**, so Jenkins itself must be able to drive the host Docker daemon.
On the sidonia host, Jenkins runs as a container (`jenkins/jenkins:lts` in
`docker-sidonia`) with **no Docker CLI and no socket by default** — so a custom
image + a socket mount are required. This is a one-time change to
`B:\system_setup\docker-sidonia`:

1. **Custom Jenkins image** (`docker-sidonia/jenkins/Dockerfile`): layers the
   Docker CLI (client only) + the compose v2 plugin onto `jenkins/jenkins:lts`,
   and adds the `jenkins` user to the `root` group so it can read the
   bind-mounted socket (Docker Desktop mounts it `root:root` mode 0660).
2. **Socket mount** (in `docker-sidonia/docker-compose.yml`, `jenkins` service):
   ```yaml
   volumes:
     - /var/run/docker.sock:/var/run/docker.sock   # host daemon → Jenkins
   ```
   Docker Desktop for Windows transparently bridges the host daemon to this
   Linux-side socket path. *(Do NOT use the `//./pipe/docker_engine` source
   form — on Docker Desktop that creates an empty directory, not a socket.)*

Apply with: `cd B:\system_setup\docker-sidonia && docker compose build jenkins
&& docker compose up -d jenkins`. Verify from inside the controller:

```bash
docker exec jenkins docker ps    # should list the host's containers
```

If Jenkins runs **natively** (not in a container), this prerequisite is already
satisfied — `docker` is on the host PATH.

---

## 1. Build the kb image (once, then on code changes)

```bash
docker compose build kb
```

This is also the first stage of the pipeline, so Jenkins rebuilds automatically
when `Dockerfile` / `pyproject.toml` / source change.

Smoke-test it talks to Postgres:

```bash
docker compose run --rm kb status          # prints source/channel counts
docker compose run --rm kb db migrate       # idempotent schema apply
```

---

## 2. One-time priming (login-gated sources)

Three sources need an interactive login *once*; the session is then stored in
`data/` and reused by every nightly run. Do these from the host shell (not
Jenkins), where a real browser window can open:

| Source   | Priming command (host)                        | Session file                              |
|----------|-----------------------------------------------|-------------------------------------------|
| HKEJ     | `uv run kb hkej docker up`, then solve any Cloudflare challenge at <http://localhost:7900> and log in (or set `HKEJ_LOGIN_MODE=auto` + `HKEJ_USER`/`HKEJ_PASS`) | `data/hkej/.browser_state.json` |
| Patreon  | `uv run kb patreon prime-session`             | `data/patreon/.session.json`              |
| Substack | `uv run kb substack prime-session`            | `data/substack/.session.json`             |

When a session expires, the corresponding pipeline stage marks the build
**UNSTABLE** (yellow) with a clear "needs interactive re-login" message — it
does **not** fail the whole run. Re-run the priming command above to refresh.

Also keep these services up on the host so the nightly container can reach
them:

```bash
docker compose up -d postgres camoufox
```

The HKEJ stage connects to the camoufox Playwright WS endpoint at
`ws://host.docker.internal:9222/hkej` (set via `HKEJ_CAMOUFOX_ENDPOINT` in
`docker-compose.yml`).

---

## 3. Configure secrets

The pipeline reads all credentials from **environment variables**, never from
hardcoded literals. Two layers — use one or both:

### Option A — `.env` (current setup, simplest)

The gitignored `.env` holds every secret: `HKEJ_USER/PASS`, `PATREON_*`,
`MACROVOICES_*`, `ZAI_API_KEY`, etc. The `kb` docker-compose service sets
`env_file: .env`, so `docker compose run` passes them straight through to the
container. **No Jenkins-side secret config is required** — this is how the job
runs today. `.env` also carries `JENKINS_URL` / `JENKINS_USER` / `JENKINS_PASS`
for the local Jenkins at `http://localhost/jenkins`.

### Option B — Jenkins Credentials store (hardening)

If you'd rather not keep secrets in `.env` on the Jenkins agent, define them in
Jenkins → **Manage Jenkins → Credentials → System → Global** as *Secret text*,
and bind them in the `Jenkinsfile` with an `environment {}` block or
`withCredentials` step (an env var set there overrides the `.env` value):

| Credential ID         | Value source           |
|-----------------------|------------------------|
| `kb-hkej-user`        | `HKEJ_USER` from `.env`|
| `kb-hkej-pass`        | `HKEJ_PASS`            |
| `kb-patreon-user`     | `PATREON_USER`         |
| `kb-patreon-pass`     | `PATREON_PASS`         |
| `kb-zai-api-key`      | `ZAI_API_KEY`          |

---

## 4. Create the Jenkins job

The job `knowledge-base-nightly` already exists (created via the REST API) and
runs the pipeline inlined from the repo's `Jenkinsfile`. Its key settings:

- **Definition**: Pipeline script (the `Jenkinsfile` content, embedded).
- **Sandbox**: enabled (the pipeline only uses whitelisted `sh`/`echo`/
  `catchError`/`stage` steps — no script approval needed).
- **Agent**: `node { label 'built-in'; customWorkspace
  '/work/chanmainvest/knowledge_base' }` — runs on the controller from the
  bind-mounted repo root so `docker compose run` finds `docker-compose.yml`.
- **Trigger**: `cron('0 3 * * *')` in the `Jenkinsfile` (03:00 daily).

To switch to **Pipeline script from SCM** once the `Jenkinsfile` is committed
to git (cleaner — edits to the file take effect without re-posting config):

1. Jenkins → the job → **Configure**.
2. **Pipeline → Definition** = *Pipeline script from SCM*; point at the repo,
   script path `Jenkinsfile`.
3. (Optional) add *Build periodically* `0 3 * * *` at the job level too.

### Recreating the job via the REST API

With `JENKINS_USER` / `JENKINS_PASS` in `.env` (and the repo's `Jenkinsfile`
in place), the job can be (re)created programmatically. The config XML embeds
the `Jenkinsfile` in a CDATA section with `sandbox=true`:

```bash
# fetch a crumb (CSRF) using the same session
COOKIE=$(mktemp)
CRUMB=$(curl -s -c "$COOKIE" -b "$COOKIE" -u "$JENKINS_USER:$JENKINS_PASS" \
        "$JENKINS_URL/crumbIssuer/api/json" | grep -o '"crumb":"[^"]*"' | cut -d'"' -f4)

# build config.xml: <flow-definition> with <script>CDATA[Jenkinsfile]</script>
#   + <sandbox>true</sandbox>, then POST it
curl -s -b "$COOKIE" -u "$JENKINS_USER:$JENKINS_PASS" \
     -H "Jenkins-Crumb: $CRUMB" -H "Content-Type: application/xml" \
     --data-binary @config.xml \
     "$JENKINS_URL/createItem?name=knowledge-base-nightly"
```

Two gotchas hit during setup:
- The config must declare `<?xml version="1.0" encoding="UTF-8"?>` — the
  `Jenkinsfile` contains em-dashes/CJK; without an explicit UTF-8 declaration
  Jenkins's parser rejects byte `0x80` inside CDATA.
- `sandbox=false` causes builds to be dropped silently (no log). The pipeline
  is sandbox-safe, so use `<sandbox>true</sandbox>`.

---

## 5. Pipeline stages

The scrapes run **in parallel** inside one `stage('Scrape')`; the post-scrape
stages (Ingest → Extract → recompute) run sequentially after all branches finish.

| # | Stage              | Command                                         | Outcome on failure |
|---|--------------------|-------------------------------------------------|--------------------|
| 0 | Build kb image     | `docker compose build kb` (5× retry / fallback) | FAILED (red)       |
| 1 | Scrape (parallel)  | — all sources below run concurrently —          | —                  |
|   | · Blog: MacroVoices| `kb blog scrape macrovoices --limit 10`         | UNSTABLE           |
|   | · Blog: MadX 狂徒  | `kb blog scrape madxcap --limit 10`             | UNSTABLE           |
|   | · Blog: Gorozen    | `kb blog scrape gorozen --limit 10`             | UNSTABLE           |
|   | · YouTube          | `kb youtube scrape --limit 10`                  | UNSTABLE           |
|   | · Master Insight   | `kb scrape run master-insight --limit 10`       | UNSTABLE           |
|   | · HKEJ             | loop `kb hkej scrape-author <handle> --limit 10`| UNSTABLE           |
|   | · Substack         | loop `kb substack scrape <handle> --limit 10`   | UNSTABLE           |
|   | · Patreon          | `kb patreon scrape-creator --limit 10`          | UNSTABLE           |
| 2 | Ingest             | `kb ingest`                                     | FAILED             |
| 3 | Extract            | `kb extract run --limit 200`                    | FAILED             |
| 4 | Progress recompute | `kb progress recompute`                         | FAILED             |

Every scrape branch is wrapped in `catchError(buildResult: 'UNSTABLE')`, so a
failed source marks the build **UNSTABLE** (yellow) but **never blocks Ingest /
Extract** — the LLM pass runs on whatever was scraped. `failFast` stays off, so
a failing/slow branch never aborts its siblings.

**Removed:** Yahoo HK (nothing left to download) and the nightly `kb db migrate`
(the schema is created once from `init.sql`; re-run it manually only after a
schema change: `docker compose run --rm kb db migrate`).

---

## 6. Notes & gotchas

- **`--limit 10` = the 10 newest items per channel per night.** All discovery
  surfaces (YouTube /videos tabs, Substack archives, HKEJ catalogs, blog
  indices) list newest-first, so the cap always covers the newest content
  first. Backlogs drain at ≤10/channel/night, which keeps the pipeline inside
  its timeout and lets Extract run every night. Each scrape command is
  idempotent — it skips items whose markdown file already exists — so a
  nightly run only fetches what's new.
- **YouTube proxy pool.** `YT_DLP_PROXY_HOSTS` in `.env` drives the SOCKS5
  round-robin over SSH tunnels. The container mounts `~/.ssh` read-only so the
  SSH aliases + keys are available. If you don't want proxying, clear the var
  and yt-dlp connects directly.
- **HKEJ author list is dynamic.** The HKEJ stage parses `kb hkej list-authors`
  output, so adding an author (`kb hkej add-author <handle>`) is automatically
  picked up by the next nightly run — no Jenkinsfile edit needed. Same for
  Substack (`kb substack list-channels`).
- **Extract provider.** `kb extract run` uses `LLM_PROVIDER` from `.env`. This
  repo is configured for `zai` (Z.ai / Zhipu GLM) — set `ZAI_API_KEY` in `.env`
  before the first run. Any of `openai`, `anthropic`, or `zai` works unattended;
  **not** `github`, which shells out to a local `copilot` CLI not present in the
  container. Override per-run with `kb extract run <n> --provider anthropic`.
- **Whisper transcription is out of scope.** `scripts/transcribe_missing.py`
  needs CUDA and runs on the GPU host; it is not part of the nightly pipeline.
- **Why a Docker image, not installing tools in Jenkins.** Baking
  Python/uv/Playwright/yt-dlp into the Jenkins controller is fragile and
  pollutes it. A self-contained `kb:latest` mirrors how this repo already ships
  Postgres and the Camoufox browser as containers, and keeps the host `uv run`
  workflow and the Jenkins workflow on identical, locked dependencies.
