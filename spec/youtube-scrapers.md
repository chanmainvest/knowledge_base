# Spec — YouTube scraping, transcripts, Whisper ASR, proxy pool

Read this when touching `src/kb/scrapers/youtube.py`, `src/kb/transcribe.py`,
`src/kb/scrapers/proxy.py`, `scripts/backfill_youtube_transcripts.py`, or any
YouTube backfill script.

- **YouTube folder stability + cross-name dedup.** Channel folders are named
  from the slugified display name, which YouTube can change (``MacroVoices``
  → ``Macro Voices``), historically forking channels into two folders and
  re-downloading everything. Two guards prevent this: (1) the folder slug is
  **pinned** in `channel.metadata['dir_slug']` on first discovery
  (`_channel_dir_slugs`/`_pin_channel_dir` in `youtube.py`) and reused even
  if the display name later changes; (2) `already_scraped()` first consults
  the `item` table by `external_id` (cached per run in
  `_known_video_ids()`, filtered to items whose md file still exists so
  vanished files get re-fetched instead of skipped) — the DB key is immune
  to name drift, unlike the disk-path check. If duplicate folders already
  exist, `scripts/fix_youtube_dup_folders.py` merges them into the canonical
  folder (slug of the current DB name), also deduping same-folder
  `undated/` vs dated copies, and re-points stale `md_path` rows.
- **Transcript paragraphing happens at scrape time.** VTT captions are
  5-8-word display lines; stored verbatim they render as one giant
  paragraph (markdown joins single newlines). `_vtt_to_text` therefore
  parses cues with their timings and builds paragraphs from the VTT's own
  signals — `>>` speaker changes, cue gaps > 2.5 s, and a ~600-char cap
  split at sentence boundaries (`_vtt_to_paragraphs` in `youtube.py`).
  Whisper transcriptions get the same shape via `_assemble_paragraphs`.
  Paragraphed markdown is canonical: it feeds FTS snippets and the LLM
  chunker (paragraph-aware `_chunks`) better than a wall of text.
  Historical files are repaired by `kb youtube reformat-transcripts`
  (offline, idempotent — text-only heuristics since timings are gone).
  The Item page renders the transcript as a collapsible section (long
  ones default collapsed) inside a 72ch reading column.
- **YouTube missing dates (undated scars).** When yt-dlp's metadata fetch
  fails (dead tunnel / 429) the video is saved with `published_at=None`;
  `fetch()` resolves the upload date through a four-step chain so this
  (almost) never happens: info-json → proxied `--dump-json` → **direct**
  `--dump-json` (no proxy; `_polite_ytdlp_direct`) → the `/watch` page's
  embedded `uploadDate`/`publishDate` scraped direct
  (`_lookup_upload_date_direct`, same residential-IP design as the
  transcript fallback) → a date parsed from the video title
  (`_date_from_title`, e.g. "…| 15May2022"; YouTube-era range enforced).
  Historical scars: files under `<channel>/undated/` plus files the dedup
  script promoted to dated filenames whose front-matter/DB row still said
  null. All repaired by `kb youtube backfill-dates` (offline filename pass
  stamped 1,586 items; the online pass dated the remaining 291 — 288 via
  yt-dlp, 3 via the watch-page/title fallbacks; also recovers NULL
  `prediction.made_at` and re-points `md_path`). Resumable and safe to
  re-run; videos deleted before YouTube served `uploadDate` in the watch
  page are the only ones that can end up undated.
- **YouTube stub-metadata scars (empty descriptions).** The same failed
  yt-dlp fetches also dropped *all* metadata, not just dates: when both
  dump-json attempts fail, `fetch()` saves from a `{id, title, upload_date}`
  stub (`youtube.py`), so the file's `## Description` section is empty and
  `duration_sec`/`extra.uploader`/`view_count`/`tags` are null (2,585 of
  20,976 files, from the 2026-07/08 bulk-scrape degradation windows). The
  DB has no description column — description lives only in `item.content`
  as that markdown section. `kb youtube backfill-metadata` re-fetches
  metadata per video (direct, one at a time, polite) and rewrites the
  description/duration/published lines + front-matter (`_apply_metadata_to_md`),
  re-ingesting each file. Resumable via a `metadata_synced_at` front-matter
  marker. Two run modes: `--proxy-hosts <aliases>` opens the SSH SOCKS pool
  with one asyncio worker per tunnel (`_pooled_metadata_loop`; yt-dlp and
  HTTP calls run via `asyncio.to_thread` — `_ytdlp` is a blocking
  `subprocess.run` that would otherwise serialize the workers); without
  hosts it goes direct and sequential at the larger direct interval.
  **Cloud-egress gotcha (2026-08-16):** the four `oc*.hevangel.com` Oracle
  IPs (and `hevangel.com`, also Oracle) get "Sign in to confirm you're not
  a bot" on yt-dlp's innertube calls cookie-less, on every player client —
  only `horace.org` works. The pooled workers therefore fetch via
  `_watch_page_info` (one plain GET of the /watch HTML, parsing the embedded
  `ytInitialPlayerResponse.videoDetails` — description, duration, view
  count; the watch page is served where innertube is bot-challenged) with
  yt-dlp as fallback. Rate-limit-aware like
  `scripts/backfill_youtube_transcripts.py`: blocked fetches (429 /
  bot-check signatures, `_err_class` — the "sign in to confirm" phrase must
  stay full-length or yt-dlp's private-video "Sign in if you've been
  granted access" misclassifies) trigger an exponential cooldown
  (5 min → doubling, capped 1 h) and retry the same video; repeated blocks
  retire a worker (or abort a direct run); private/deleted videos are
  skipped without counting toward the abort.
- **YouTube proxy** (optional): to avoid YouTube's per-IP rate limiting (HTTP
  429), yt-dlp can route through SOCKS5 tunnels over SSH. `--proxy-hosts
  oc1.hevangel.com,horace.org` opens one `ssh -D` tunnel per host and
  round-robins each yt-dlp call across them; falls back to the
  `YT_DLP_PROXY_HOSTS` env var if the flag is omitted, and to a direct
  connection if neither is set. A single manual tunnel is also supported via
  `YT_DLP_PROXY=socks5://127.0.0.1:1080`. yt-dlp calls get `--proxy` (also
  `--force-ipv4` + `--retries 8 --socket-timeout 30` to survive a tunnel
  dropping mid-transfer). The `youtube-transcript-api` fallback deliberately
  goes **direct** (residential IP), not through the proxy — see the split
  below. Available SSH host aliases (configured in `~/.ssh/config`):
  `hevangel.com`, `oc1/2/3/4.hevangel.com`, `horace.org`, `serv00`. The
  `ProxyPool` tunnel manager lives in `src/kb/scrapers/proxy.py`.
- **Proxy vs transcript split** (important): yt-dlp routes through the SOCKS5
  pool because it uses the innertube API, which tolerates cloud IPs. But
  `youtube-transcript-api` scrapes the `/watch` page, which YouTube blocks
  from cloud-provider IP ranges (Oracle Cloud / AWS / GCP / Azure) with
  `RequestBlocked`. So the transcript fallback goes direct from the
  residential IP, where it works (verified: 16k-char transcript fetched
  direct vs `RequestBlocked` through every proxy egress). Do NOT add the
  proxy to `_fetch_transcript_api`.
- **Cantonese auto-CC is `yue`, not `zh`** (2026-08-16 fix). YouTube labels
  the original Cantonese ASR track `yue-orig` (plain `yue` on the
  transcript-api surface); `zh-Hans`/`zh-Hant`/`en` exist only as
  auto-translations of it. The pre-fix `--sub-langs en.*,zh.*` filter and the
  `["en", "zh-Hant", …]` preferred list therefore came home empty for
  Cantonese-first channels (Dr Ng's LATP), and the timedtext endpoint's hard
  429 throttling during the 2026-07/08 bulk scrapes stranded ~6.4k files with
  the `_(no transcript available)_` marker. Both lists now include `yue`,
  `_pick_vtt()` prefers the original over machine translations, and
  `scripts/backfill_youtube_transcripts.py` recovers the scarred files —
  deliberately over-polite (per-item sleep, exponential cooldown on 429,
  abort after 3 consecutive blocks, resumable via `has_transcript`), meant to
  run daily for weeks. The backfill routes yt-dlp through the SSH SOCKS5
  fan-out (`--proxy-hosts` / `YT_DLP_PROXY_HOSTS`): the residential IP alone
  stays timedtext-throttled for days, while the horace.org egress serves
  captions reliably; the oc*/Oracle egresses answer cookie-less yt-dlp with
  the bot-wall and are benched automatically per run. Backfilled files get
  `transcript_source: youtube-captions` front-matter; promo shorts (<60 s)
  are skipped by default since YouTube mostly doesn't auto-caption them at
  all.
- **serv00 excluded from proxy pool**: `serv00` accepts the SSH connection
  and binds the SOCKS port (tunnel appears "up"), but its SOCKS forwarding
  fails for actual requests (curl rc=97 / connection refused). It is omitted
  from the default `YT_DLP_PROXY_HOSTS`; the working hosts are
  `oc1/2/3/4.hevangel.com` and `horace.org`.
- **ProxyPool port & process hygiene** (Windows-specific gotcha): each tunnel
  binds the first *free* port at or above 1081, not a fixed `base_port+i`, and
  `stop()` kills ssh via `taskkill /F /T` (not `terminate()`, which ssh.exe on
  Windows routinely ignores). This matters because: (1) orphaned ssh
  processes from a prior run hold their port, and `ExitOnForwardFailure=yes`
  then kills the new ssh silently — the symptom is every tunnel "dying within
  seconds", which was previously misdiagnosed as host-side instability. (2)
  Without `taskkill`, torn-down tunnels orphan and squat ports, breaking
  the next run. `next()` also calls `_reap()` to skip any tunnel whose ssh has
  exited, so a dead connection is never handed to yt-dlp (the cause of its
  `4 bytes missing` SOCKS5 EOFError).
- **Whisper ASR transcription (`src/kb/transcribe.py`, opt-in).** YouTube
  videos where no subtitle/transcript could be fetched
  (`has_transcript=false`) can be transcribed locally with
  `kb youtube transcribe` using faster-whisper + large-v3 on GPU (RTX
  3060 Ti). The pipeline is **disabled by default**: it only runs via the
  dedicated command, or after `kb youtube scrape --transcribe` (which
  transcribes only the videos that scrape just fetched; `--no-transcribe`
  or unset `WHISPER_ENABLED` keeps scrape transcript-free). It runs **one
  video at a time** (sequential — no parallel GPU load) and downloads audio
  to `data/raw/youtube/tmp/` (`WHISPER_TMP_DIR`, resolved against
  `DATA_DIR` to match the `data/raw/<source>/` layout; gitignored, deleted
  after each item). Language is auto-detected by Whisper (Cantonese → `yue`,
  English → `en`, etc.) when `WHISPER_LANGUAGE` is empty. The full lifecycle
  is tracked in `item.transcription_status`: `NULL` → `pending` →
  `audio_downloaded` → `transcribing` → `done` (or `failed` with
  `transcription_error`). On success, `has_transcript` is flipped to `true`,
  the transcript replaces the `_(no transcript available)_` marker in the
  `.md` file, and `ingest_file()` updates the DB. Use
  `kb youtube transcribe --reset-stuck` to clear stale `transcribing` rows
  after a crash, `--retry-failed` to re-attempt failed items, and `--list`
  to preview candidates without transcribing.
