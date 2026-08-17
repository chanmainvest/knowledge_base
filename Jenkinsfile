// =============================================================================
// Nightly knowledge-base scrape pipeline.
//
// Runs every source as its own stage (one major category per stage), then a
// catch-up ingest, an LLM extraction batch, and a progress recompute. Each
// stage shells out to the `kb` Docker image (see Dockerfile / docker-compose.yml
// `kb` service) — Jenkins itself has no Python/uv/Playwright/yt-dlp.
//
// Schedule: 03:00 local time, daily.
// Manual run: "Build Now" in Jenkins, or trigger via the REST API.
//
// Design notes
// ------------
// * Scrapes run in PARALLEL (one stage per source inside `stage('Scrape')`).
//   Each is wrapped in catchError → the build goes UNSTABLE (yellow) on a
//   failed source, not FAILED, so Ingest + Extract always run on whatever was
//   scraped. A failing branch never aborts its siblings (failFast stays off).
// * Login-gated sources (HKEJ, Patreon, Substack) need one-time interactive
//   session priming on the host; an expired session degrades to UNSTABLE.
//   `kb patreon scrape-creator` exits 2 when the session needs a human re-login.
// * Yahoo HK is removed — nothing left to download there.
// * DB migrate is NOT part of the nightly run — the schema is created once
//   from init.sql and only changes on a code update; run it manually then.
// * Secrets (HKEJ_*, PATREON_*, ZAI_API_KEY, …) live in the repo's gitignored
//   .env, which the `kb` docker-compose service loads via `env_file:` and passes
//   straight through to every `docker compose run`. To harden, bind them from
//   the Jenkins Credentials store (see doc/jenkins-pipeline.md).
//
// Working directory: the repo is bind-mounted into the Jenkins container at
// /work/chanmainvest/knowledge_base (host B:\). `customWorkspace` makes every
// `sh` step run there, so `docker compose run` picks up docker-compose.yml +
// .env without an explicit `cd`. Override with the KB_WORKSPACE env var when
// creating the job (e.g. for a checked-out SCM workspace).
// =============================================================================

pipeline {
    // Run on the built-in node (the sidonia Jenkins container, which has the
    // docker CLI + host socket mount). `customWorkspace` pins every stage to
    // the repo root — bind-mounted at /work/chanmainvest/knowledge_base
    // (host B:\) — so `docker compose run` finds docker-compose.yml + .env
    // without an explicit `cd`. `label ''` = any available node.
    agent {
        node {
            label 'built-in'
            customWorkspace '/work/chanmainvest/knowledge_base'
        }
    }

    // 03:00 daily. Jenkins cron fields: MINUTE HOUR DOM MONTH DOW.
    triggers { cron('0 3 * * *') }

    options {
        timestamps()                 // prefix every log line with a timestamp
        buildDiscarder(logRotator(numToKeepStr: '14'))  // keep 2 weeks of runs
        timeout(time: 8, unit: 'HOURS')   // first-time backfills are long; 8h headroom
        disableConcurrentBuilds()    // never two nightly runs at once
    }

    // Secrets: the kb container's docker-compose `kb` service sets
    // `env_file: .env`, so HKEJ_USER/PASS, PATREON_*, MACROVOICES_*, ZAI_API_KEY,
    // etc. are read from the repo's gitignored .env and passed straight through
    // to every `docker compose run`. No Jenkins-side secret config is required
    // for the job to run.
    //
    // Optional hardening: if you'd rather not keep secrets in .env on the
    // Jenkins agent, define them in Jenkins → Manage → Credentials and bind them
    // here as environment{} entries (or with the `withCredentials` step), e.g.:
    //     environment { ZAI_API_KEY = credentials('kb-zai-api-key') }
    // An env var set here overrides the .env value for the compose run.

    stages {

        // -------------------------------------------------------------------------
        // 0. Build / refresh the kb image.
        //
        // `docker compose build kb` checks the registry for base-image metadata
        // (python:3.12-slim) on every run. That lookup is the only network
        // dependency in the whole pipeline, so a transient DNS failure here must
        // not tank the nightly run. We retry the build a few times, and — since
        // the scrapers only need *some* kb:latest to exist, not the newest base
        // image — we treat a registry failure as success when a local kb:latest
        // is already present (it just won't pick up base-image security updates
        // that night). A genuine Dockerfile/source error still fails the build.
        // -------------------------------------------------------------------------
        stage('Build kb image') {
            steps {
                sh '''set +e
for attempt in 1 2 3 4 5; do
    echo "--- build attempt $attempt/5 ---"
    docker compose build kb && exit 0
    echo "build attempt $attempt failed (exit $?), waiting 15s before retry..."
    sleep 15
done
# All attempts failed. Proceed only if a kb:latest already exists locally —
# the scrape stages reuse it. Otherwise this is a real failure.
if docker image inspect kb:latest >/dev/null 2>&1; then
    echo "WARN: image build failed (likely a transient registry/DNS error), but kb:latest exists — proceeding with the cached image."
    exit 0
fi
echo "ERROR: image build failed and no kb:latest is cached. Cannot proceed."
exit 1
'''
            }
        }

        // -------------------------------------------------------------------------
        // 1. Scrape — all sources in PARALLEL.
        //
        // Each branch runs its own `docker compose run --rm kb` container; they
        // share the host Postgres (concurrent connections are fine) and write to
        // disjoint subdirs of data/, so there are no file conflicts. Running
        // concurrently cuts wall time to ~the slowest source instead of the sum.
        //
        // Every branch is wrapped in catchError(buildResult:'UNSTABLE',
        // stageResult:'FAILURE'): a failed source surfaces as a red stage but
        // marks the build UNSTABLE (not FAILED), so the downstream Ingest +
        // Extract + recompute stages still run on whatever was scraped. This is
        // the right semantics for a parallel scrape — one flaky source must not
        // cancel the LLM pass over everything else that succeeded.
        //
        // failFast is left at its default (false): a failing/slow branch never
        // aborts its siblings.
        //
        // Bounded nightly budget: every scrape uses `--limit 10` — the 10
        // NEWEST items per channel (YouTube /videos tabs, Substack archives,
        // HKEJ catalogs and blog indices all list newest-first, so the cap
        // always covers the newest content). This keeps the whole pipeline
        // inside its timeout and lets Ingest + Extract run every night;
        // backlogs drain at ≤10/channel/night instead of starving Extract.
        //
        // (Yahoo HK is intentionally absent — nothing left to download there.)
        // -------------------------------------------------------------------------
        stage('Scrape') {
            parallel {
                stage('Blog: MacroVoices') {
                    steps {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                            sh 'docker compose run --rm kb blog scrape macrovoices --limit 10'
                        }
                    }
                }

                stage('Blog: MadX 狂徒') {
                    steps {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                            sh 'docker compose run --rm kb blog scrape madxcap --limit 10'
                        }
                    }
                }

                stage('Blog: Gorozen') {
                    steps {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                            sh 'docker compose run --rm kb blog scrape gorozen --limit 10'
                        }
                    }
                }

                // YouTube — uses the SSH SOCKS5 proxy pool (YT_DLP_PROXY_HOSTS
                // in .env); falls back to a direct connection (with a larger
                // proactive rate limit) if the pool is down. --limit 10 = the
                // 10 newest videos per registered channel (yt-dlp --playlist-end
                // against the newest-first /videos tab).
                stage('YouTube') {
                    steps {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                            sh 'docker compose run --rm kb youtube scrape --limit 10'
                        }
                    }
                }

                stage('Master Insight') {
                    steps {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                            sh 'docker compose run --rm kb scrape run master-insight --limit 10'
                        }
                    }
                }

                // HKEJ — login-gated behind Cloudflare. Needs the camoufox
                // container up + a primed session in data/hkej/.browser_state.json.
                // Author list is read from the DB by `kb hkej list-authors`.
                stage('HKEJ') {
                    steps {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                            sh '''
                                # list-authors prints a table:
                                #   Handle               Name                 Discovery
                                #   --------------------------------------------------
                                #     李聲揚              李聲揚               search
                                # Skip the header + separator, strip the 2-space indent,
                                # and take column 1 (the handle, which scrape-author wants).
                                authors=$(docker compose run --rm kb hkej list-authors \
                                    | sed -e '1,2d' -e 's/^[[:space:]]*//' | awk '{print $1}')
                                if [ -z "$authors" ]; then
                                    echo "No HKEJ authors registered — skipping (run: kb hkej add-author <handle>)"
                                    exit 0
                                fi
                                while IFS= read -r author; do
                                    [ -z "$author" ] && continue
                                    echo "=== HKEJ author: $author ==="
                                    docker compose run --rm kb hkej scrape-author "$author" --limit 10 \
                                        || echo "WARN: HKEJ scrape failed for '$author' (likely Cloudflare/login) — continuing"
                                done <<< "$authors"
                            '''
                        }
                    }
                }

                // Substack — public posts unattended; paid posts need a primed
                // data/substack/.session.json. Channels listed by
                // `kb substack list-channels`.
                stage('Substack') {
                    steps {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                            sh '''
                                # list-channels prints (no header, 2-space indent):
                                #     dampedspring           Damped Spring
                                handles=$(docker compose run --rm kb substack list-channels \
                                    | sed -e 's/^[[:space:]]*//' | awk '{print $1}')
                                if [ -z "$handles" ]; then
                                    echo "No Substack channels registered — skipping"
                                    exit 0
                                fi
                                while IFS= read -r handle; do
                                    [ -z "$handle" ] && continue
                                    echo "=== Substack: $handle ==="
                                    docker compose run --rm kb substack scrape "$handle" --limit 10 \
                                        || echo "WARN: Substack scrape failed for '$handle' — continuing"
                                done <<< "$handles"
                            '''
                        }
                    }
                }

                // Patreon — batch over every registered creator. Exits 2 when
                // the session cookie has expired and needs a human re-login
                // (`kb patreon prime-session`); downgraded to UNSTABLE.
                stage('Patreon') {
                    steps {
                        catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                            sh '''
                                set +e
                                docker compose run --rm kb patreon scrape-creator --limit 10
                                rc=$?
                                if [ "$rc" -eq 2 ]; then
                                    echo "Patreon session expired — needs interactive: kb patreon prime-session"
                                    exit 1   # catchError downgrades to UNSTABLE
                                elif [ "$rc" -ne 0 ]; then
                                    echo "Patreon scrape reported failures (exit $rc)"
                                    exit 1
                                fi
                            '''
                        }
                    }
                }
            }
        }

        // -------------------------------------------------------------------------
        // 2. Catch-up ingest — every scrape command already auto-ingests inline,
        //    but this is an idempotent safety net for any file written outside
        //    the CLI (e.g. a manual edit, or a crashed mid-scrape). Runs after
        //    all parallel scrapes finish.
        //
        //    DB migrate is NOT run nightly: the schema is created once from
        //    docker/postgres/init.sql (CREATE TABLE IF NOT EXISTS …) and only
        //    changes when init.sql is edited — a manual, code-tied event. Re-run
        //    it yourself after a schema change:  docker compose run --rm kb db migrate
        // -------------------------------------------------------------------------

        stage('Ingest') {
            steps {
                sh 'docker compose run --rm kb ingest'
            }
        }

        // -------------------------------------------------------------------------
        // 3. LLM extraction — runs after all content is in. Provider/model come
        //    from LLM_PROVIDER / LLM_MODEL in .env. Batch of 200 so a nightly
        //    run makes real progress on the backlog. Hard-fails (red) on error,
        //    since that usually means an LLM credential/config problem worth
        //    noticing. Unstable scrapes above do NOT block this stage.
        // -------------------------------------------------------------------------

        stage('Extract') {
            steps {
                sh 'docker compose run --rm kb extract run --limit 200'
            }
        }

        // -------------------------------------------------------------------------
        // 4. Market data + scoring — top up the asset_price store from Yahoo
        //    (incremental; only tickers whose last day lags today get fetched),
        //    score new/elapsed-horizon predictions, rebuild the channel/
        //    speaker/model leaderboards. Network-dependent, so UNSTABLE-not-
        //    FAILED on error — a flaky Yahoo night shouldn't mark the pipeline
        //    red; scores refresh the next successful run.
        // -------------------------------------------------------------------------

        stage('Score') {
            steps {
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    sh 'docker compose run --rm kb leaderboard rebuild'
                }
            }
        }

        // -------------------------------------------------------------------------
        // 5. Recompute pipeline-progress counters for the dashboard
        //    (/api/dashboard). Cheap; always worth running after a batch.
        // -------------------------------------------------------------------------

        stage('Progress recompute') {
            steps {
                sh 'docker compose run --rm kb progress recompute'
            }
        }
    }

    // -------------------------------------------------------------------------
    // Always run, regardless of build outcome.
    // -------------------------------------------------------------------------
    post {
        always {
            echo "Pipeline finished: ${currentBuild.currentResult}"
            // Clean up any one-shot kb containers the run stages spawned.
            // (Compose `--rm` handles the normal case; this catches strays.)
            sh 'docker compose rm -fsv kb 2>/dev/null || true'
        }
        success {
            echo 'All stages completed.'
        }
        unstable {
            echo 'One or more login-gated stages need attention (HKEJ/Patreon/Substack). See stage logs.'
        }
        failure {
            echo 'A required stage failed (image build, Ingest, or Extract). See stage logs.'
        }
    }
}
