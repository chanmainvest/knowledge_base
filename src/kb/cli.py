"""`kb` CLI."""
from __future__ import annotations

import asyncio
import json
import urllib.parse
from pathlib import Path

import typer
from rich import print
from sqlalchemy import column, func, select, table, text

from . import extract as extract_mod
from . import ingest as ingest_mod
from . import leaderboard as lb_mod
from . import links as links_mod
from .api.main import main as api_main
from .config import DATA_DIR, ROOT, settings
from .db import engine
from .logging_setup import get_logger
from .scrapers import SCRAPERS, get as get_scraper

app = typer.Typer(no_args_is_help=True, add_completion=False)
db_app = typer.Typer(no_args_is_help=True)
scrape_app = typer.Typer(no_args_is_help=True)
ext_app = typer.Typer(no_args_is_help=True)
lb_app = typer.Typer(no_args_is_help=True)
youtube_app = typer.Typer(no_args_is_help=True, help="YouTube channel management")
hkej_app = typer.Typer(no_args_is_help=True, help="HKEJ author management")
hkej_browser_app = typer.Typer(no_args_is_help=True, help="Persistent browser session")
hkej_docker_app = typer.Typer(no_args_is_help=True, help="Camoufox Docker container (default browser mode)")
master_insight_app = typer.Typer(no_args_is_help=True, help="Master Insight author management")
businessfocus_app = typer.Typer(no_args_is_help=True, help="BusinessFocus author management")
patreon_app = typer.Typer(no_args_is_help=True, help="Patreon session helpers")
patreon_browser_app = typer.Typer(no_args_is_help=True, help="Persistent Patreon browser")
substack_app = typer.Typer(no_args_is_help=True, help="Substack session helpers")
blog_app = typer.Typer(no_args_is_help=True, help="Blog site scrapers (macrovoices, madxcap, …)")
progress_app = typer.Typer(no_args_is_help=True, help="Pipeline progress tracking (dashboard data)")
market_app = typer.Typer(no_args_is_help=True, help="Market data (daily price store)")
app.add_typer(db_app, name="db")
app.add_typer(scrape_app, name="scrape")
app.add_typer(ext_app, name="extract")
app.add_typer(lb_app, name="leaderboard")
app.add_typer(youtube_app, name="youtube")
app.add_typer(hkej_app, name="hkej")
hkej_app.add_typer(hkej_browser_app, name="browser")
hkej_app.add_typer(hkej_docker_app, name="docker")
app.add_typer(master_insight_app, name="master-insight")
app.add_typer(businessfocus_app, name="businessfocus")
app.add_typer(patreon_app, name="patreon")
patreon_app.add_typer(patreon_browser_app, name="browser")
app.add_typer(substack_app, name="substack")
app.add_typer(blog_app, name="blog")
app.add_typer(progress_app, name="progress")
app.add_typer(market_app, name="market")

log = get_logger("cli")


@db_app.command("migrate")
def db_migrate() -> None:
    """Run init.sql against the configured Postgres."""
    init_sql = (ROOT / "docker" / "postgres" / "init.sql").read_text(encoding="utf-8")
    eng = engine()
    for stmt in _split_sql(init_sql):
        try:
            with eng.begin() as c:
                c.execute(text(stmt))
        except Exception as exc:
            log.warning("stmt failed (continuing): %s :: %s", exc, stmt[:80])
    print("[green]migrated[/green]")


@db_app.command("status")
def db_status() -> None:
    with engine().connect() as c:
        for tbl in ("source", "channel", "item", "extraction_run", "prediction",
                    "view_market", "chunk", "entity", "leaderboard_weekly",
                    "leaderboard_speaker", "provider_model_leaderboard",
                    "asset_price", "asset_ticker", "source_progress"):
            # %I inside format() quotes the identifier server-side, so the
            # table name rides as a bind parameter instead of interpolation.
            n = c.execute(text("""
                SELECT (xpath('/row/c/text()', query_to_xml(
                           format('SELECT COUNT(*) AS c FROM %I', CAST(:t AS text)),
                           false, true, '')))[1]::text::int
            """), {"t": tbl}).scalar()
            print(f"  {tbl:26s} {n}")


@progress_app.command("recompute")
def progress_recompute() -> None:
    """Recompute per-source pipeline counters from authoritative sources.

    `n_ingested`/`n_extracted`/`n_extract_pending`/`n_extract_error` are
    recounted from the `item` table; `n_downloaded` is set from an actual
    filesystem scan of `DATA_DIR/<source>/**/*.md`. Safe to run any time —
    use to seed correct values or recover from drift.
    """
    from . import progress as prog
    prog.recompute()
    print("[green]recomputed[/green]")


@progress_app.command("status")
def progress_status() -> None:
    """Print the per-source pipeline progress table."""
    from . import progress as prog
    rows = prog.snapshot()
    if not rows:
        print("[yellow]no sources[/yellow]")
        return
    print(f"{'source':16s} {'dl':>6s} {'ingest':>7s} {'extr':>6s} {'pend':>6s} "
          f"{'err':>5s}  last_scrape          last_ingest         last_extract")
    for r in rows:
        def _ts(v):
            return v.strftime("%Y-%m-%d %H:%M") if v else "—"
        print(f"{(r['code'] or ''):16s} "
              f"{(r.get('n_downloaded') or 0):>6d} "
              f"{(r.get('n_ingested') or 0):>7d} "
              f"{(r.get('n_extracted') or 0):>6d} "
              f"{(r.get('n_extract_pending') or 0):>6d} "
              f"{(r.get('n_extract_error') or 0):>5d}  "
              f"{_ts(r.get('last_scrape_at')):19s}  "
              f"{_ts(r.get('last_ingest_at')):19s}  "
              f"{_ts(r.get('last_extract_at'))}")


def _split_sql(s: str) -> list[str]:
    out, buf, in_dollar = [], "", False
    for line in s.splitlines():
        buf += line + "\n"
        if "$$" in line:
            in_dollar = not in_dollar
        if line.rstrip().endswith(";") and not in_dollar:
            out.append(buf.strip()); buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [x for x in out if x]


@scrape_app.command("list")
def scrape_list(
    kind: str | None = typer.Option(
        None, "--kind",
        help="Filter by source category, e.g. blog, newspaper, youtube, membership"
    ),
) -> None:
    """List registered scrapers, grouped by source category (`source.kind`).

    'blog' groups one-off, homepage-discovery website scrapers with no
    per-author crawl/catalog state (macrovoices, madxcap). 'newspaper' groups
    resumable multi-author crawlers that track discovery state in their own
    catalog tables (hkej, yahoohk, master-insight).
    """
    with engine().connect() as conn:
        kinds = dict(conn.execute(text("SELECT code, kind FROM source")).fetchall())
    groups: dict[str, list[tuple[str, str]]] = {}
    for code, cls in SCRAPERS.items():
        k = kinds.get(code, "unknown")
        if kind and k != kind:
            continue
        groups.setdefault(k, []).append((code, cls.name))
    if not groups:
        print(f"[yellow]No scrapers found for kind={kind!r}.[/yellow]")
        return
    for k in sorted(groups):
        print(f"[bold]{k}[/bold]")
        for code, name in sorted(groups[k]):
            print(f"  {code:14s} {name}")


@scrape_app.command("list-channels")
def scrape_list_channels(source: str = typer.Argument("youtube")) -> None:
    """List channels registered for a source."""
    _list_channels(source)


def _list_channels(source: str) -> None:
    with engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT c.handle, c.name FROM channel c "
            "JOIN source s ON c.source_id = s.id WHERE s.code = :src ORDER BY c.name"
        ), {"src": source}).fetchall()
    if not rows:
        print(f"[yellow]No channels for {source!r}. (Run `kb scrape run {source}` to seed.)[/yellow]")
        return
    for handle, name in rows:
        print(f"  {handle:40s} {name}")


@scrape_app.command("add-channel")
def scrape_add_channel(
    source: str = typer.Argument(..., help="Source code, e.g. youtube"),
    handle: str = typer.Argument(..., help="Channel handle or URL, e.g. @MyChannel"),
    name: str = typer.Argument(..., help="Display name"),
) -> None:
    """Add a new channel to the scrape list (stored in DB)."""
    _add_channel(source, handle, name)


def _add_channel(
    source: str,
    handle: str,
    name: str,
    run_hint: str | None = None,
) -> None:
    if source == "youtube":
        from .scrapers.youtube import normalize_youtube_handle

        handle = normalize_youtube_handle(handle)
    with engine().begin() as conn:
        sid = conn.execute(text("SELECT id FROM source WHERE code=:c"),
                           {"c": source}).scalar_one_or_none()
        if sid is None:
            print(f"[red]Unknown source: {source!r}. "
                  f"Valid sources: {list(SCRAPERS)}[/red]")
            raise typer.Exit(1)
        conn.execute(text(
            "INSERT INTO channel(source_id, handle, name) VALUES (:s,:h,:n) "
            "ON CONFLICT (source_id, handle) DO UPDATE SET name=EXCLUDED.name"
        ), {"s": sid, "h": handle, "n": name})
        print(f"[green]Added[/green] {handle!r} ({name}) to {source!r}. "
            f"Run `{run_hint or f'kb scrape run {source}'}` to scrape it.")


@youtube_app.command("list-channels")
def youtube_list_channels() -> None:
    """List registered YouTube channels."""
    _list_channels("youtube")


@youtube_app.command("add-channel")
def youtube_add_channel(
    handle: str | None = typer.Argument(
        None,
        help="Channel handle or URL (@ optional; omit @ on PowerShell)",
    ),
    name: str | None = typer.Argument(
        None, help="Display name (resolved from YouTube via yt-dlp if omitted)"
    ),
    handle_flag: str | None = typer.Option(
        None,
        "--handle",
        "-H",
        help="Channel handle (alternative to positional; omit @ on PowerShell)",
    ),
) -> None:
    """Add a YouTube channel to the scrape list (stored in DB)."""
    from .scrapers.youtube import YouTubeScraper, normalize_youtube_handle

    raw = handle_flag or handle
    if not raw:
        print("[red]Missing channel handle.[/red]")
        print(
            "PowerShell treats bare @Handle as splatting — do not pass a leading @ unquoted."
        )
        print("  kb youtube add-channel BloorStreetCapital")
        print("  kb youtube add-channel --handle BloorStreetCapital")
        print("  kb youtube add-channel '@BloorStreetCapital'")
        raise typer.Exit(1)
    handle = normalize_youtube_handle(raw)
    if name is None:
        scraper = YouTubeScraper()
        name = scraper.resolve_channel_display_name(handle)
        if not name:
            print(f"[red]Could not resolve channel name for {handle!r}. "
                  "Pass an explicit NAME or check the handle/URL.[/red]")
            raise typer.Exit(1)
        print(f"[dim]Resolved display name:[/dim] {name!r}")
    _add_channel("youtube", handle, name, run_hint="kb youtube scrape")


@youtube_app.command("migrate-folders")
def youtube_migrate_folders(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show renames without moving directories"
    ),
    ingest: bool = typer.Option(
        False, "--ingest", help="Re-ingest moved markdown to refresh md_path in DB"
    ),
) -> None:
    """Rename data/youtube/<handle-slug>/ to slugified channel display names."""
    from .scrapers.youtube import migrate_youtube_folders

    moves = migrate_youtube_folders(dry_run=dry_run)
    if not moves:
        print("[yellow]No YouTube folder renames needed.[/yellow]")
        return
    for old, new in moves:
        action = "would rename" if dry_run else "renamed"
        print(f"  {action}: {old.relative_to(DATA_DIR)} -> {new.relative_to(DATA_DIR)}")
    print(f"[green]{len(moves)}[/green] folder(s) {'planned' if dry_run else 'updated'}.")
    if ingest and not dry_run:
        from . import ingest as ingest_mod
        n = 0
        for _, new in moves:
            if not new.is_dir():
                continue
            for md in new.rglob("*.md"):
                if ingest_mod.ingest_file(md):
                    n += 1
        print(f"[green]Re-ingested {n}[/green] markdown file(s).")


@youtube_app.command("backfill-dates")
def youtube_backfill_dates(
    limit: int = typer.Option(
        0, "--limit",
        help="Max undated videos to look up online (0 = all remaining)"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would change without touching files/DB"),
    fetch: bool = typer.Option(
        True, "--fetch/--no-fetch",
        help="After the offline filename pass, look up still-undated videos "
             "online (direct yt-dlp, one at a time, polite)"),
) -> None:
    """Repair missing published_at on YouTube items.

    Two passes: (1) offline — items whose markdown filename already carries
    a YYYY-MM-DD- prefix (promoted by the folder dedup) get their front-matter
    and DB row stamped from it; (2) online — items still under ``undated/``
    get their upload date looked up per video, the file moved to its dated
    path, and the DB re-pointed. Also recovers prediction.made_at for any
    calls that were unscoreable due to the missing date. Resumable — rerun
    after interruptions."""
    from .scrapers.youtube import (
        backfill_dates_from_filenames,
        backfill_undated_metadata,
    )

    n = backfill_dates_from_filenames(dry_run=dry_run)
    print(f"[green]{n}[/green] item(s) dated from filenames"
          f"{' (dry run)' if dry_run else ''}")
    if fetch:
        stats = asyncio.run(backfill_undated_metadata(
            limit=limit or 0, dry_run=dry_run))
        print(f"[green]{stats['dated']}[/green] undated item(s) resolved online "
              f"of {stats['candidates']} candidates "
              f"({stats['unknown']} unknown/deleted, {stats['missing_file']} missing files)"
              f"{' (dry run)' if dry_run else ''}")


@youtube_app.command("reformat-transcripts")
def youtube_reformat_transcripts(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would change without touching files/DB"),
    limit: int = typer.Option(
        0, "--limit", help="Only process the first N files (0 = all)"),
) -> None:
    """Re-paragraph stored YouTube transcripts (one-time repair).

    Transcript markdown scraped before the paragraph-aware `_vtt_to_text`
    is a wall of 5-8-word caption lines. This joins them into flowing text
    with paragraphs at >> speaker changes (timing gaps are gone from stored
    text), rewrites each file and re-ingests it. Offline, idempotent —
    already-readable files are skipped."""
    from .scrapers.youtube import reformat_transcripts
    stats = reformat_transcripts(dry_run=dry_run, limit=limit or 0)
    print(f"files scanned: {stats['files']}")
    print(f"[green]{stats['reformatted']}[/green] transcript(s) re-paragraphed"
          f"{' (dry run)' if dry_run else ''}, "
          f"{stats['unchanged']} already readable, "
          f"{stats['no_transcript']} without transcript, "
          f"{stats['missing_file']} missing")


@youtube_app.command("backfill-metadata")
def youtube_backfill_metadata(
    limit: int = typer.Option(
        0, "--limit",
        help="Max videos to re-fetch online (0 = all remaining)"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would change without touching files/DB"),
    proxy_hosts: str = typer.Option(
        "", "--proxy-hosts",
        help="Comma-separated SSH host aliases for SOCKS5 tunnels — runs one "
             "parallel worker per tunnel (e.g. oc1.hevangel.com,horace.org). "
             "Falls back to the YT_DLP_PROXY_HOSTS env var; without tunnels "
             "the run is direct and sequential."),
) -> None:
    """Backfill video descriptions and other lost metadata on YouTube items.

    Videos scraped while yt-dlp's metadata fetch was failing (dead SOCKS
    tunnel / HTTP 429 during the 2026-07/08 bulk scrapes) were saved from an
    id+title-only stub: empty `## Description` section, null
    duration/uploader/view-count. This re-fetches metadata per video, rewrites
    the description/duration/published lines and front-matter, and re-ingests
    each file. With --proxy-hosts it runs in parallel over the SSH SOCKS
    tunnels (one worker each); otherwise direct, one at a time. Resumable —
    processed files carry `metadata_synced_at` in front-matter. Rate-limit
    aware: blocked fetches trigger an exponential cooldown (5 min → doubling,
    capped 1 h) and retry the same video; repeated blocks retire a worker
    (or abort a direct run)."""
    from .scrapers.youtube import backfill_metadata

    stats = asyncio.run(backfill_metadata(
        limit=limit or 0, dry_run=dry_run, proxy_hosts=proxy_hosts))
    print(f"candidates: {stats['candidates']}")
    if stats.get("workers"):
        print(f"parallel workers: {stats['workers']} (SSH SOCKS tunnels)")
    print(f"[green]{stats['updated']}[/green] item(s) repaired"
          f"{' (dry run)' if dry_run else ''}, "
          f"{stats['empty']} with genuinely empty description, "
          f"{stats['unavailable']} unavailable/deleted, "
          f"{stats['failed']} failed, "
          f"{stats['blocked']} rate-limited (cooled down + retried)"
          + (" — some workers retired or the run aborted; rerun resumes"
             if stats.get("aborted") else ""))


@youtube_app.command("scrape")
def youtube_scrape(
    limit: int = typer.Option(0, help="Max videos to inspect per channel (0 = all)"),
    proxy_hosts: str = typer.Option(
        "", help="Comma-separated SSH host aliases to open SOCKS5 tunnels to, "
                 "distributing requests round-robin (e.g. oc1.hevangel.com,serv00). "
                 "Falls back to YT_DLP_PROXY_HOSTS env var."),
    transcribe: bool = typer.Option(
        None, "--transcribe/--no-transcribe",
        help="After scraping, transcribe the newly fetched videos that have no "
             "subtitle, using faster-whisper (one at a time, GPU). "
             "Default: WHISPER_ENABLED from .env (= disabled).",
    ),
) -> None:
    """Scrape registered YouTube channels."""
    new_paths = scrape_run(code="youtube", limit=limit, proxy_hosts=proxy_hosts)
    if transcribe is None:
        transcribe = settings().whisper_enabled
    if transcribe:
        from .io_md import load_md
        from . import transcribe as transcribe_mod

        # Only the videos just written by this run (external_id from the new
        # files' front-matter) — not the whole pending backlog.
        vids: list[str] = []
        for p in new_paths:
            try:
                doc = load_md(p)
                if not doc.front.get("has_transcript"):
                    vid = doc.front.get("external_id")
                    if vid:
                        vids.append(vid)
            except Exception:  # noqa: BLE001
                continue
        if vids:
            print(f"[cyan]transcribe[/cyan] {len(vids)} new video(s) without subtitles")
            transcribe_mod.run_transcribe(external_ids=vids)


@youtube_app.command("transcribe")
def youtube_transcribe(
    limit: int = typer.Option(0, help="Max items to transcribe (0 = all pending)"),
    channel: str = typer.Option(
        "", help="Only transcribe this channel handle/name (substring match)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List candidates but don't download/transcribe",
    ),
    list_only: bool = typer.Option(
        False, "--list", help="List candidates and a status summary, then exit",
    ),
    retry_failed: bool = typer.Option(
        False, "--retry-failed", help="Also re-attempt items previously marked 'failed'",
    ),
    reset_stuck: bool = typer.Option(
        False, "--reset-stuck",
        help="Re-queue items stuck in 'transcribing'/'audio_downloaded' back "
             "to 'pending' and exit",
    ),
    model: str = typer.Option(None, help="Override ASR model (qwen: HF id, whisper: model size; default: from config)"),
    device: str = typer.Option(None, help="Override device, e.g. cuda|cuda:0|cpu (default: from config)"),
    engine: str = typer.Option(
        None, "--engine",
        help="ASR engine for this run: qwen | whisper "
             "(default: TRANSCRIBE_ENGINE, currently qwen)",
    ),
) -> None:
    """Transcribe YouTube videos missing subtitles (opt-in, qwen engine).

    Covers items where no subtitle/transcript could be fetched
    (`has_transcript=false`): downloads the audio track, runs ASR on GPU one
    video at a time — Qwen3-ASR (verbatim Cantonese, audio chunked at
    QWEN_CHUNK_SEC) by default, faster-whisper via --engine whisper — writes
    the transcript into the item's markdown and re-ingests it.
    """
    from . import transcribe as transcribe_mod

    if reset_stuck:
        n = transcribe_mod.reset_stuck()
        print(f"Re-queued {n} item(s) from 'transcribing'/'audio_downloaded' → 'pending'.")
        return

    if list_only:
        candidates = transcribe_mod.gather_candidates(limit, channel or None, retry_failed)
        print(f"\n{len(candidates)} transcription candidate(s):\n")
        for row in candidates:
            print(f"  {row['external_id']} | {row.get('channel_name', '?')} | "
                  f"{row.get('transcription_status') or 'pending'} | "
                  f"{row.get('duration_sec') or '?'}s | {(row['title'] or '')[:60]}")
        counts = transcribe_mod.count_by_status()
        print("\nStatus summary (has_transcript=false YouTube items):")
        for st, n in counts.items():
            print(f"  {st}: {n}")
        return

    transcribe_mod.run_transcribe(
        limit=limit,
        channel=channel or None,
        retry_failed=retry_failed,
        dry_run=dry_run,
        model=model,
        device=device,
        engine=engine,
    )


@scrape_app.command("run")
def scrape_run(
    code: str,
    limit: int = typer.Option(0, help="0 = unlimited"),
    source_type: str = typer.Option(None, help="Filter by content type (e.g. dcard, facebook for madxcap)"),
    proxy_hosts: str = typer.Option(
        "", help="Comma-separated SSH host aliases for SOCKS5 round-robin proxying "
                 "(YouTube only). Falls back to YT_DLP_PROXY_HOSTS env var."),
) -> None:
    # When called directly (e.g. from scrape_all) rather than via the Typer CLI,
    # unset Option/Argument params keep Typer's OptionInfo/ArgumentInfo sentinel
    # instead of their declared default — and that object is truthy, which would
    # break the str ops below. Coerce them to the documented defaults.
    if not isinstance(proxy_hosts, str):
        proxy_hosts = ""
    if not isinstance(source_type, str):
        source_type = None
    sc = get_scraper(code)
    # Resolve the proxy host list: CLI flag → env default → none (direct).
    # The SOCKS5 pool is YouTube-only (it's what hits yt-dlp's per-IP rate
    # limits); don't pay the SSH-tunnel setup cost for the other sources.
    hosts_raw = proxy_hosts or (settings().yt_dlp_proxy_hosts if code == "youtube" else "")
    hosts = [h.strip() for h in hosts_raw.split(",") if h.strip()] if hosts_raw else []
    pool_cm = None
    if hosts:
        from .scrapers.proxy import ProxyPool
        pool_cm = ProxyPool(hosts)
        pool_cm.start()
        if pool_cm.urls:
            sc.proxy_pool = pool_cm  # type: ignore[attr-defined]
            print(f"[cyan]proxy[/cyan] round-robin across {len(pool_cm.urls)} tunnel(s): "
                  + ", ".join(pool_cm.urls))
        else:
            print("[yellow]proxy[/yellow] no tunnels came up; continuing direct")
    try:
        kwargs = {"limit": limit or None}
        if isinstance(source_type, str):
            kwargs["source_type"] = source_type
        paths = asyncio.run(sc.run(**kwargs))
    except Exception as exc:  # noqa: BLE001
        log.exception("scrape crashed: %s", exc)
        paths = []
    finally:
        if pool_cm is not None:
            pool_cm.stop()
    print(f"[green]{len(paths)}[/green] new files for {code}")
    for p in paths:
        try:
            ingest_mod.ingest_file(p)
        except Exception as exc:  # noqa: BLE001
            log.exception("ingest failed for %s :: %s", p, exc)
    return paths


@scrape_app.command("all")
def scrape_all(limit: int = 5) -> None:
    for code in SCRAPERS:
        try:
            scrape_run(code=code, limit=limit)
        except Exception as exc:  # noqa: BLE001
            log.exception("scrape %s failed: %s", code, exc)


@scrape_app.command("resume")
def scrape_resume(
    code: str = typer.Argument(..., help="Scraper code (e.g. youtube, blog, hkej)"),
    limit: int = typer.Option(0, help="0 = unlimited"),
) -> None:
    """Re-attempt items a scraper discovered but never finished downloading.

    Reads pending catalog rows (``downloaded=false``) and re-runs fetch+write
    for each, so a scrape that died halfway can continue without re-discovering
    the whole source. Already-downloaded items found on disk are reconciled to
    ``downloaded=true``.
    """
    sc = get_scraper(code)
    try:
        paths = asyncio.run(sc.resume(limit=limit or None))
    except Exception as exc:  # noqa: BLE001
        log.exception("resume crashed: %s", exc)
        paths = []
    print(f"[green]{len(paths)}[/green] files resumed for {code}")
    for p in paths:
        try:
            ingest_mod.ingest_file(p)
        except Exception as exc:  # noqa: BLE001
            log.exception("ingest failed for %s: %s", p, exc)


@app.command("ingest")
def ingest_all() -> None:
    n = ingest_mod.ingest_all()
    print(f"[green]ingested[/green] {n}")


@ext_app.command("run")
def extract_run(
    limit: int = 50,
    provider: str | None = typer.Option(
        None, help="Override LLM_PROVIDER for this run (openai|github|anthropic|zai)"),
    model: str | None = typer.Option(None, help="Override the provider's default model"),
    prompt_version: str | None = typer.Option(
        None, help="Prompt/schema version (dir under src/kb/prompts/extraction/); "
                   "default = EXTRACTION_PROMPT_VERSION or the highest present"),
) -> None:
    n = extract_mod.run(limit, provider=provider, model=model,
                        prompt_version=prompt_version)
    print(f"[green]extracted[/green] {n}")


@ext_app.command("compare")
def extract_compare(
    item_id: int,
    providers: str = typer.Option(
        "openai,github,anthropic,zai",
        help="Comma-separated providers to run on this item, e.g. 'openai,anthropic'. "
             "Repeat a provider to compare its models: 'zai,zai'."),
    model: str = typer.Option(
        "", help="Comma-separated models, applied to the providers in order. "
                 "One entry = same model for every provider; otherwise the "
                 "counts must match --providers. "
                 "E.g. --providers zai,zai --model glm-4.6,glm-5.3"),
    prompt_version: str | None = typer.Option(
        None, help="Prompt/schema version (dir under src/kb/prompts/extraction/); "
                   "default = EXTRACTION_PROMPT_VERSION or the highest present"),
) -> None:
    """Extract one item with several provider/model combos side by side,
    without disturbing the item's existing primary (canonical) extraction.
    Useful for judging which provider/model reads a given author most
    reliably before committing to it as LLM_PROVIDER."""
    provider_list = [p.strip() for p in providers.split(",") if p.strip()]
    model_list = [m.strip() for m in model.split(",") if m.strip()] if model else []
    if not model_list:
        combos: list[tuple[str, str | None]] = [(p, None) for p in provider_list]
    elif len(model_list) == 1:
        combos = [(p, model_list[0]) for p in provider_list]
    elif len(model_list) == len(provider_list):
        combos = list(zip(provider_list, model_list))
    else:
        raise typer.BadParameter(
            f"--model has {len(model_list)} entries but --providers has "
            f"{len(provider_list)}; give one model (applied to all) or one "
            "per provider")
    results = extract_mod.compare_item(item_id, combos,
                                       prompt_version=prompt_version)
    for combo, stats in results.items():
        print(f"[bold]{combo}[/bold]: {stats}")


@ext_app.command("prompts")
def extract_prompts() -> None:
    """List the registered extraction prompt/schema versions
    (src/kb/prompts/extraction/<version>/ = one system.md + schema.json pair)."""
    from . import prompts as prompts_mod
    versions = prompts_mod.list_versions()
    if not versions:
        print("[yellow]no prompt versions registered[/yellow]")
        return
    default = prompts_mod.default_version()
    for v in versions:
        pair = prompts_mod.load(v)
        marker = "[green] (default)[/green]" if v == default else ""
        print(f"{v:<8} {pair.name:<24} {pair.path}{marker}")


@ext_app.command("runs")
def extract_runs(item_id: int) -> None:
    """List every extraction_run recorded for an item (one per provider/model/prompt_version)."""
    rows = extract_mod.list_runs(item_id)
    if not rows:
        print(f"[yellow]no extraction runs for item {item_id}[/yellow]")
        return
    for r in rows:
        marker = "[green]primary[/green]" if r.get("is_primary") else "       "
        toks = ""
        if r.get("prompt_tokens") is not None:
            toks = (f" tok={r['prompt_tokens']}/{r.get('cached_tokens') or 0}"
                    f"/{r.get('completion_tokens') or 0}")
        print(f"{marker} run={r['id']:<6} {r['provider']:<10} {(r['model'] or '(default)'):<28} "
              f"{(r.get('prompt_version') or '-'):<5} "
              f"status={r['status']:<7} views={r['n_market_views']} preds={r['n_predictions']} "
              f"{r['duration_ms'] or '-'}ms{toks}")


@ext_app.command("cost")
def extract_cost() -> None:
    """Aggregate token usage per provider/model/prompt_version and estimate
    USD cost from OpenRouter's public reference prices (a yardstick, not
    actual billing — e.g. GLM Coding Plan usage is subscription quota).
    Runs predating usage capture (or from the copilot CLI) show as no-usage."""
    from . import pricing

    er = table("extraction_run",
               column("provider"), column("model"), column("prompt_version"),
               column("status"), column("prompt_tokens"),
               column("cached_tokens"), column("completion_tokens"))
    with engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(
            select(er.c.provider, er.c.model, er.c.prompt_version,
                   func.count().label("runs"),
                   func.count(er.c.prompt_tokens).label("with_usage"),
                   func.sum(er.c.prompt_tokens).label("prompt"),
                   func.sum(er.c.cached_tokens).label("cached"),
                   func.sum(er.c.completion_tokens).label("completion"))
            .where(er.c.status == "done")
            .group_by(er.c.provider, er.c.model, er.c.prompt_version)
        ).mappings().all()]
    if not rows:
        print("[yellow]no completed extraction runs yet[/yellow]")
        return
    try:
        prices = pricing.load_prices()
    except Exception as exc:  # noqa: BLE001 — network/API trouble: tokens still shown
        print(f"[yellow]OpenRouter price list unavailable ({exc}) — tokens only[/yellow]")
        prices = {}

    print(f"{'provider':<10} {'model':<26} {'pv':<5} {'runs':>6} {'in':>13} "
          f"{'cached':>13} {'out':>12} {'est. cost':>12}")
    tot_in = tot_cached = tot_out = 0
    tot_cost = 0.0
    cost_known = True
    for r in sorted(rows, key=lambda r: (r["provider"], r["model"], r["prompt_version"])):
        price = pricing.lookup(prices, r["provider"], r["model"] or "")
        cost = None
        if price is not None and r["prompt"] is not None:
            try:
                cost = pricing.cost_usd(price, r["prompt"] or 0, r["cached"] or 0,
                                        r["completion"] or 0)
            except ValueError:
                pass
        no_usage = r["runs"] - r["with_usage"]
        print(f"{r['provider']:<10} {(r['model'] or '(default)'):<26} "
              f"{(r['prompt_version'] or '-'):<5} {r['runs']:>6} "
              f"{r['prompt'] or 0:>13,} {r['cached'] or 0:>13,} "
              f"{r['completion'] or 0:>12,} "
              f"{('$' + f'{cost:,.4f}') if cost is not None else 'n/a':>12}"
              f"{f'  (+{no_usage} no-usage)' if no_usage else ''}")
        tot_in += r["prompt"] or 0
        tot_cached += r["cached"] or 0
        tot_out += r["completion"] or 0
        if cost is not None:
            tot_cost += cost
        else:
            cost_known = False
    print(f"{'TOTAL':<10} {'':<26} {'':<5} "
          f"{sum(r['runs'] for r in rows):>6} "
          f"{tot_in:>13,} {tot_cached:>13,} {tot_out:>12,} "
          f"{('$' + f'{tot_cost:,.4f}') if cost_known else 'partial':>12}")
    print("[dim]cost = OpenRouter reference price (cached tokens at the "
          "cache-read rate when published); runs without usage are counted "
          "but excluded from the cost.[/dim]")


@lb_app.command("rebuild")
def leaderboard_rebuild(
    rescore: bool = typer.Option(
        False, "--rescore",
        help="Re-score every prediction, including ones whose horizon already "
             "elapsed (default only updates unscored/running ones)"),
    no_sync: bool = typer.Option(
        False, "--no-sync",
        help="Skip the price-store sync step (score from cached prices only)"),
) -> None:
    """Sync market prices, score predictions, rebuild channel/speaker/model
    leaderboard rollups."""
    from . import marketdata as market_mod
    stats = lb_mod.rebuild(rescore=rescore, sync_prices=not no_sync)
    sc = stats.get("scoring", {})
    print(f"[green]scored[/green] {sc.get('scored', 0)}/{sc.get('candidates', 0)} "
          f"predictions ({sc.get('no_price', 0)} without price data)")
    sy = stats.get("sync") or {}
    if sy:
        print(f"[green]market sync[/green] {sy.get('fetched', 0)}/{sy.get('tickers', 0)} "
              f"tickers, {sy.get('rows', 0)} rows, {sy.get('skipped', 0)} skipped, "
              f"{len(sy.get('no_data', []))} no-data, {len(sy.get('errors', []))} errors")
    print("[green]leaderboard updated[/green]")


# --- Market data (price store) ---------------------------------------------

@market_app.command("sync")
def market_sync(
    ticker: list[str] = typer.Option(
        None, "--ticker", "-t",
        help="Sync only this ticker (repeatable). Default: every ticker "
             "referenced by extracted predictions."),
    full: bool = typer.Option(
        False, "--full",
        help="Re-download full history and retry no-data/error tickers "
             "instead of topping up the recent tail"),
) -> None:
    """Bulk-fetch daily prices into the `asset_price` store (Yahoo Finance)."""
    from . import marketdata as market_mod
    stats = market_mod.sync(tickers=ticker or None, full=full)
    print(f"[green]synced[/green] {stats.get('fetched', 0)}/{stats.get('tickers', 0)} "
          f"tickers ({stats.get('rows', 0)} rows, {stats.get('skipped', 0)} up-to-date)")
    if stats.get("no_data"):
        print(f"[yellow]no data on Yahoo:[/yellow] {', '.join(stats['no_data'][:20])}"
              + (" …" if len(stats["no_data"]) > 20 else ""))
    if stats.get("errors"):
        print(f"[red]fetch errors:[/red] {', '.join(stats['errors'][:20])}"
              + (" …" if len(stats["errors"]) > 20 else ""))


@market_app.command("status")
def market_status() -> None:
    """Per-ticker price coverage + prediction call counts."""
    from . import marketdata as market_mod
    rows = market_mod.ticker_stats()
    if not rows:
        print("[yellow]price store empty — run [bold]kb market sync[/bold][/yellow]")
        return
    print(f"{'ticker':12s} {'calls':>5s} {'scored':>6s} {'avg':>6s} "
          f"{'sync':>8s} {'days':>5s}  first        last         name")
    for r in rows:
        avg = "—" if r["avg_score"] is None else f'{r["avg_score"]:+.2f}'
        print(f"{r['ticker']:12s} "
              f"{(r['n_calls'] or 0):>5d} "
              f"{(r['n_scored'] or 0):>6d} "
              f"{avg:>6s} "
              f"{(r['sync_status'] or '—'):>8s} "
              f"{(r['n_days'] or 0):>5d}  "
              f"{str(r['first_day'] or '—'):12s} {str(r['last_day'] or '—'):12s} "
              f"{r['asset_name'] or ''}"[:110])


@app.command("links")
def links_rebuild(k: int = 10) -> None:
    n = links_mod.rebuild(top_k=k)
    print(f"[green]links: {n}[/green]")


@app.command("api")
def api() -> None:
    api_main()


@app.command("status")
def status() -> None:
    print({"data_dir": str(DATA_DIR)})
    db_status()


@hkej_app.command("prime")
def hkej_prime(
    handle: str = typer.Argument("李聲揚", help="Author name used to open a real HKEJ search page"),
    login_wait_minutes: int = typer.Option(15, help="Minutes to wait for manual login"),
) -> None:
    """Prepare one HKEJ browser session for search and subscriber article access."""
    from .scrapers.hkej import BROWSER_PROFILE_DIR, HKEJScraper

    print(
        "\n[bold]HKEJ priming[/bold]\n"
        "  Opens one real author search page to clear search.hkej.com,\n"
        "  then opens subscriber login so paid articles can be fetched.\n"
        "  Header must show [bold]歡迎（我的賬戶｜登出）[/bold] before continuing.\n"
        f"\nProfile: {BROWSER_PROFILE_DIR}\n"
    )
    sc = HKEJScraper()
    ok = asyncio.run(
        sc.prime_session(handle, login_wait_sec=login_wait_minutes * 60)
    )
    if ok:
        print("[green]Primed.[/green] Run:")
        print(f'  kb hkej scrape-author "{handle}" --limit 0')
    else:
        print("[yellow]Priming incomplete.[/yellow] Complete Cloudflare/login in the browser.")
        raise typer.Exit(1)


@hkej_browser_app.command("start")
def hkej_browser_start(
    login_wait_minutes: int = typer.Option(
        15, help="Minutes to wait for Cloudflare + login on first open",
    ),
) -> None:
    """Keep one Camoufox window open across scrapes (no repeated Cloudflare)."""
    from .scrapers.hkej_daemon import (
        is_daemon_alive,
        start_daemon_process,
        wait_for_daemon,
    )

    if is_daemon_alive():
        print("[green]Browser daemon already running.[/green]")
        return
    print(
        "\n[bold]Starting HKEJ browser daemon[/bold]\n"
        "  Complete Cloudflare + login once — the window stays open.\n"
        "  Later scrapes reuse this session: [bold]kb hkej scrape-author …[/bold]\n"
    )
    start_daemon_process(login_wait_minutes=login_wait_minutes)
    if not asyncio.run(wait_for_daemon(60.0)):
        print("[yellow]Daemon did not respond in time.[/yellow]")
        raise typer.Exit(1)
    print("[green]Browser daemon ready.[/green]")


@hkej_browser_app.command("stop")
def hkej_browser_stop() -> None:
    """Close the persistent HKEJ browser daemon."""
    from .scrapers.hkej_daemon import daemon_shutdown, is_daemon_alive

    if not is_daemon_alive():
        print("Browser daemon is not running.")
        return
    if asyncio.run(daemon_shutdown()):
        print("[green]Browser daemon stopped.[/green]")
    else:
        print("[yellow]Could not stop daemon cleanly.[/yellow]")


@hkej_browser_app.command("login")
def hkej_browser_login(
    wait_minutes: int = typer.Option(15, help="Minutes to wait for manual login"),
) -> None:
    """Open subscribe.hkej.com in the daemon browser and wait for you to log in."""
    from .scrapers.hkej import BROWSER_PROFILE_DIR, HKEJScraper
    from .scrapers.hkej_daemon import daemon_prime_login, is_daemon_alive

    print(
        "\n[bold]HKEJ login[/bold]\n"
        "  1. Stay on Cloudflare until verification completes\n"
        "  2. Enter email/password and click green [bold]登入[/bold]\n"
        "  3. Wait for header [bold]歡迎（我的賬戶｜登出）[/bold]\n"
        f"\nProfile: {BROWSER_PROFILE_DIR}\n"
    )
    wait_sec = wait_minutes * 60
    if is_daemon_alive():
        resp = asyncio.run(daemon_prime_login(wait_sec=wait_sec))
        if resp and resp.get("ok"):
            print("[green]Logged in.[/green]")
            return
        print("[yellow]Login not detected in time.[/yellow]")
        raise typer.Exit(1)

    print("[dim]Daemon not running — opening one-off browser.[/dim]\n")
    sc = HKEJScraper()
    ok = asyncio.run(sc.prime_login_session(wait_sec=wait_sec))
    if ok:
        print("[green]Logged in.[/green]")
    else:
        print("[yellow]Login not detected in time.[/yellow]")
        raise typer.Exit(1)


@hkej_browser_app.command("status")
def hkej_browser_status() -> None:
    """Check whether the persistent browser daemon is running."""
    from .scrapers.hkej_daemon import DAEMON_INFO_PATH, is_daemon_alive

    if is_daemon_alive():
        print(f"[green]Browser daemon running[/green] ({DAEMON_INFO_PATH})")
    else:
        print("[dim]Browser daemon not running[/dim] — start with: kb hkej browser start")


# --------------------------------------------------------------------------- #
# kb hkej docker — Camoufox container (the default browser mode)
# --------------------------------------------------------------------------- #

# Docker image references and container names are constrained to a safe charset
# (no shell metacharacters) before being passed to `docker` as argv elements.
# Every `docker` invocation below uses a list-literal argv with shell=False, so
# nothing is ever interpreted by a shell; the allowlist is defence-in-depth.
import re as _re

_DOCKER_NAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/:]*$")


def _safe_name(value: str, field: str) -> str:
    """Validate a docker image/container identifier; exit on anything unsafe."""
    if not value or not _DOCKER_NAME_RE.match(value):
        print(f"[red]Invalid {field}:[/red] {value!r}")
        raise typer.Exit(1)
    return value


def _container_state(container: str) -> str | None:
    """Return the container's state (e.g. 'running', 'exited') or None if absent."""
    import subprocess

    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", _safe_name(container, "container")],
            capture_output=True, text=True, check=False, shell=False,
        )
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


@hkej_docker_app.command("up")
def hkej_docker_up(
    novnc: bool = typer.Option(
        True, "--no-vnc/--no-novnc",
        help="Enable the noVNC web UI (http://localhost:<port>) for interactive Cloudflare/login",
    ),
    image: str = typer.Option(
        None, "--image", help="Docker image (default: HKEJ_DOCKER_IMAGE from .env)",
    ),
) -> None:
    """Start the Camoufox browser container (the default browser mode)."""
    import subprocess

    s = settings()
    img = _safe_name(image or s.hkej_docker_image, "image")
    container = _safe_name(s.hkej_docker_container, "container")
    ws_port = int(s.hkej_camoufox_port)
    novnc_port = int(s.hkej_docker_novnc_port)

    state = _container_state(container)
    if state == "running":
        print(f"[green]Container already running[/green] ({container})")
        return
    if state is not None:
        # Exists but stopped/exited — remove so we can recreate cleanly.
        subprocess.run(
            ["docker", "rm", "-f", container], capture_output=True, check=False, shell=False,
        )

    print(f"[bold]Starting Camoufox container[/bold] ({img})…")
    if novnc:
        res = subprocess.run(
            ["docker", "run", "-d", "--name", container, "--restart", "unless-stopped",
             "-p", f"{ws_port}:9222", "-p", f"{novnc_port}:7900",
             "-e", "CAMOUFOX_NOVNC=1", "-e", "CAMOUFOX_WS_PATH=hkej",
             "-e", "CAMOUFOX_PORT=9222", img],
            capture_output=True, text=True, shell=False,
        )
    else:
        res = subprocess.run(
            ["docker", "run", "-d", "--name", container, "--restart", "unless-stopped",
             "-p", f"{ws_port}:9222",
             "-e", "CAMOUFOX_NOVNC=0", "-e", "CAMOUFOX_WS_PATH=hkej",
             "-e", "CAMOUFOX_PORT=9222", img],
            capture_output=True, text=True, shell=False,
        )
    if res.returncode != 0:
        print(f"[red]docker run failed:[/red]\n{res.stderr.strip()}")
        print(
            "\nBuild the image first with:  [bold]docker compose build camoufox[/bold]\n"
            "or set HKEJ_DOCKER_IMAGE to a prebuilt one."
        )
        raise typer.Exit(1)

    print(f"[green]Container started[/green] ({container})")
    print(f"  Playwright WS: ws://127.0.0.1:{ws_port}/hkej")
    if novnc:
        print(f"  noVNC web UI:  http://localhost:{novnc_port}")
        print("  Open the noVNC URL if Cloudflare/login needs a human.")


@hkej_docker_app.command("down")
def hkej_docker_down() -> None:
    """Stop and remove the Camoufox browser container."""
    import subprocess

    container = _safe_name(settings().hkej_docker_container, "container")
    state = _container_state(container)
    if state is None:
        print(f"[dim]Container not present[/dim] ({container})")
        return
    res = subprocess.run(
        ["docker", "rm", "-f", container], capture_output=True, text=True, shell=False,
    )
    if res.returncode != 0:
        print(f"[red]docker rm failed:[/red]\n{res.stderr.strip()}")
        raise typer.Exit(1)
    print(f"[green]Container removed[/green] ({container})")


@hkej_docker_app.command("status")
def hkej_docker_status() -> None:
    """Show container state, the WS endpoint, and the noVNC URL."""
    import socket

    s = settings()
    container = s.hkej_docker_container
    state = _container_state(container)
    if state is None:
        print(f"[dim]Container not present[/dim] ({container}) — start with: kb hkej docker up")
        return
    print(f"Container: [bold]{container}[/bold] — state: {state}")
    # Reachability of the mapped WS port on the host.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.connect(("127.0.0.1", int(s.hkej_camoufox_port)))
        reachable = True
    except OSError:
        reachable = False
    finally:
        sock.close()
    ep = f"ws://127.0.0.1:{int(s.hkej_camoufox_port)}/hkej"
    print(f"  WS endpoint {ep}: {'[green]reachable[/green]' if reachable else '[red]not reachable[/red]'}")
    print(f"  noVNC:       http://localhost:{int(s.hkej_docker_novnc_port)}")


@hkej_docker_app.command("logs")
def hkej_docker_logs(
    follow: bool = typer.Option(False, "--follow/-f", help="Follow log output"),
) -> None:
    """Show the Camoufox container logs (useful to see the WS endpoint / errors)."""
    import subprocess

    container = _safe_name(settings().hkej_docker_container, "container")
    args = ["--follow"] if follow else []
    try:
        subprocess.run(["docker", "logs", container, *args], check=False, shell=False)
    except FileNotFoundError:
        print("[red]docker not found on PATH[/red]")


@hkej_app.command("scrape-author")
def hkej_scrape_author(
    handle: str = typer.Argument(..., help="Author handle, e.g. 李聲揚"),
    limit: int = typer.Option(0, help="Max new articles to fetch (0 = all)"),
    keep_browser: bool = typer.Option(
        False,
        help="Leave browser open after scrape (local one-off mode only)",
    ),
    login_wait_minutes: int = typer.Option(
        15, help="Minutes to wait for you to log in at the start",
    ),
    use_daemon: bool = typer.Option(
        True,
        "--daemon/--no-daemon",
        help="Reuse persistent browser (kb hkej browser start) — local mode only",
    ),
    browser_mode: str = typer.Option(
        None,
        "--browser-mode",
        help="docker|local (default: HKEJ_BROWSER_MODE from .env, =docker)",
    ),
    login: str = typer.Option(
        None,
        "--login",
        help="auto|manual (default: HKEJ_LOGIN_MODE from .env). "
        "auto fills the form from HKEJ_USER/HKEJ_PASS; manual waits for you",
    ),
) -> None:
    """Scrape articles for one author — Docker browser by default."""
    from .config import settings
    from .scrapers.hkej import HKEJScraper
    from . import ingest as ingest_mod

    mode = browser_mode or settings().hkej_browser_mode

    if mode == "docker":
        print(
            "\n[bold]HKEJ scrape (Docker mode)[/bold] — connecting to the Camoufox container\n"
            "  If Cloudflare/login needs a human, complete it in the noVNC window:\n"
            f"  [bold]http://localhost:{settings().hkej_docker_novnc_port}[/bold]\n"
            "  (start it first with [bold]kb hkej docker up[/bold] if not running)\n"
        )
    elif use_daemon:
        from .scrapers.hkej_daemon import (
            is_daemon_alive,
            start_daemon_process,
            wait_for_daemon,
        )

        if not is_daemon_alive():
            print(
                "\n[bold]Starting browser daemon[/bold] "
                "(complete Cloudflare + login once; window stays open)\n"
            )
            start_daemon_process(login_wait_minutes=login_wait_minutes)
            if not asyncio.run(wait_for_daemon(120.0)):
                print(
                    "[red]Browser daemon did not become ready in 2 minutes.[/red]\n"
                    "  Complete Cloudflare/login in the Camoufox window, then retry.\n"
                    "  Or run: [bold]kb hkej browser start[/bold] first."
                )
                raise typer.Exit(1)
        else:
            print("\n[dim]Using persistent browser session (no Cloudflare redo).[/dim]\n")
    else:
        print(
            "\n[bold]HKEJ scrape[/bold] — one browser: prime → login → fetch\n"
            "  1. [bold]search.hkej.com[/bold] — stay on Cloudflare until results load\n"
            "  2. [bold]subscribe.hkej.com[/bold] — Cloudflare, then log in (green 登入)\n"
            "  3. Wait for [bold]歡迎（我的賬戶｜登出）[/bold], then scraping continues\n"
        )

    sc = HKEJScraper()
    paths = asyncio.run(
        sc.run(
            limit=limit or None,
            author_handle=handle,
            keep_browser_open=keep_browser,
            login_wait_sec=login_wait_minutes * 60,
            use_daemon=use_daemon,
            browser_mode=mode,
            login_mode=login,
        )
    )
    s = sc.last_stats
    print(f"\n[bold]Summary for {handle!r}[/bold]")
    print(f"  Search lists:     {s.get('search_total', '?')} articles")
    print(f"  Search pages:     {s.get('pages_crawled', 0)} crawled, {s.get('pages_reused', 0)} reused")
    print(f"  URLs discovered:  {s.get('discovered', '?')}")
    print(f"  Skipped (cached): {s.get('skipped', 0)}")
    print(f"  Fetched new:      {s.get('fetched', len(paths))}")
    print(f"  Failed:           {s.get('failed', 0)}")
    print(
        f"  On disk:          {s.get('on_disk_before', '?')}"
        f" → {s.get('on_disk_after', '?')}"
    )
    total = s.get("search_total")
    on_disk = s.get("on_disk_after")
    if total and on_disk is not None and on_disk < total:
        print(
            f"  [yellow]Still missing ~{total - on_disk} — re-run to resume "
            f"(cached articles are skipped)[/yellow]"
        )
    print(f"\n[green]{len(paths)}[/green] new files written")
    for p in paths:
        try:
            ingest_mod.ingest_file(p)
        except Exception as exc:  # noqa: BLE001
            log.exception("ingest failed for %s :: %s", p, exc)


@hkej_app.command("add-author")
def hkej_add_author(
    author_name: str = typer.Argument(
        ..., help="Author name, e.g. 李聲揚"
    ),
) -> None:
    """Register an HKEJ author name for search-based scraping."""
    handle = author_name.strip()
    if not handle:
        print("[red]Author name cannot be empty.[/red]")
        raise typer.Exit(1)
    if handle.isdigit():
        print("[red]Use the HKEJ author name; numeric identifiers are not supported.[/red]")
        raise typer.Exit(1)

    disc_url = (
        "https://search.hkej.com/template/fulltextsearch/php/search.php?author="
        + urllib.parse.quote(handle)
    )
    metadata = {"discovery": "search"}

    with engine().begin() as conn:
        sid = conn.execute(
            text("SELECT id FROM source WHERE code='hkej'")
        ).scalar_one_or_none()
        if sid is None:
            print("[red]HKEJ source not found in DB. Run: kb db migrate[/red]")
            raise typer.Exit(1)
        conn.execute(
            text(
                "INSERT INTO channel(source_id, handle, name, url, metadata) "
                "VALUES (:s,:h,:n,:u,CAST(:m AS jsonb)) "
                "ON CONFLICT (source_id, handle) "
                "DO UPDATE SET name=EXCLUDED.name, url=EXCLUDED.url, metadata=EXCLUDED.metadata"
            ),
            {
                "s": sid,
                "h": handle,
                "n": handle,
                "u": disc_url,
                "m": json.dumps(metadata),
            },
        )
    print(f"[green]Added[/green] HKEJ author by name '{handle}'")
    print(f"  Discovery URL: {disc_url}")
    print(f"  Run [bold]kb scrape run hkej[/bold] to fetch their articles.")


@hkej_app.command("list-authors")
def hkej_list_authors() -> None:
    """List all registered HKEJ authors."""
    with engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.handle, c.name, c.metadata->>'discovery' AS disc "
                "FROM channel c JOIN source s ON c.source_id=s.id "
                "WHERE s.code='hkej' ORDER BY c.name"
            )
        ).fetchall()
    if not rows:
        print("[yellow]No HKEJ authors registered.[/yellow]")
        print("Add one with: [bold]kb hkej add-author <author_name>[/bold]")
        return
    print(f"{'Handle':<20} {'Name':<20} {'Discovery':<8}")
    print("-" * 50)
    for handle, name, disc in rows:
        print(f"  {handle:<18} {name:<20} {(disc or '?'):<8}")


@hkej_app.command("rm-author")
def hkej_rm_author(
    handle: str = typer.Argument(..., help="Handle (name or ID) to remove"),
) -> None:
    """Remove an HKEJ author from the DB."""
    with engine().begin() as conn:
        n = conn.execute(
            text(
                "DELETE FROM channel USING source "
                "WHERE channel.source_id=source.id AND source.code='hkej' "
                "AND channel.handle=:h"
            ),
            {"h": handle},
        ).rowcount
    if n:
        print(f"[green]Removed[/green] '{handle}'")
    else:
        print(f"[yellow]Not found:[/yellow] '{handle}'")


@master_insight_app.command("add-author")
def master_insight_add_author(
    handle: str = typer.Argument(
        ..., help="Author slug, e.g. tangwenliang"
    ),
) -> None:
    """Register a Master Insight author by slug."""
    handle = handle.strip()
    if not handle:
        print("[red]Author slug cannot be empty.[/red]")
        raise typer.Exit(1)
    # slug-only chars: keeps the handle from altering the URL's
    # scheme/host/path below (no '/', ':', '?', etc.)
    if not _re.fullmatch(r"[A-Za-z0-9._-]+", handle):
        print("[red]Author slug may only contain letters, digits, '.', '_', '-'[/red]")
        raise typer.Exit(1)

    # slug-only chars: keeps the handle from altering the URL's
    # scheme/host/path below (no '/', ':', '?', etc.)
    if not _re.fullmatch(r"[A-Za-z0-9._-]+", handle):
        print("[red]Author slug may only contain letters, digits, '.', '_', '-'[/red]")
        raise typer.Exit(1)

    disc_url = f"https://www.master-insight.com/author/{handle}"
    # SSRF guard: this CLI fetch only ever hits the fixed Master Insight
    # origin — scheme + host are pinned and the slug is charset-validated.
    _parsed = urllib.parse.urlsplit(disc_url)
    if _parsed.scheme != "https" or _parsed.netloc != "www.master-insight.com":
        print("[red]Refusing to fetch an unexpected origin.[/red]")
        raise typer.Exit(1)

    import httpx
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": settings().scrape_user_agent,
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"
    }

    name = handle
    try:
        print(f"Resolving author name for '{handle}' from {disc_url}...")
        with httpx.Client(timeout=15.0,
                          headers=headers, follow_redirects=True) as client:
            r = client.get(disc_url)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "lxml")
            author_a = soup.select_one(".r2-box .author a")
            if author_a:
                name = author_a.text.strip()
            else:
                top_h1 = soup.select_one(".author-top-box h1")
                if top_h1:
                    name = top_h1.text.strip()
                elif soup.title:
                    title_parts = soup.title.text.split(" - ")
                    if title_parts:
                        name = title_parts[0].strip()
            print(f"Resolved name: [green]{name}[/green]")
    except Exception as exc:
        print(f"[yellow]Could not resolve author name: {exc}. Using handle as name.[/yellow]")

    with engine().begin() as conn:
        sid = conn.execute(
            text("SELECT id FROM source WHERE code='master-insight'")
        ).scalar_one_or_none()
        if sid is None:
            conn.execute(
                text(
                    "INSERT INTO source(code, name, url, kind) "
                    "VALUES ('master-insight', 'Master Insight', 'https://www.master-insight.com/', 'newspaper') "
                    "ON CONFLICT (code) DO NOTHING"
                )
            )
            sid = conn.execute(
                text("SELECT id FROM source WHERE code='master-insight'")
            ).scalar_one()

        conn.execute(
            text(
                "INSERT INTO channel(source_id, handle, name, url, metadata) "
                "VALUES (:s,:h,:n,:u,CAST(:m AS jsonb)) "
                "ON CONFLICT (source_id, handle) "
                "DO UPDATE SET name=EXCLUDED.name, url=EXCLUDED.url, metadata=EXCLUDED.metadata"
            ),
            {
                "s": sid,
                "h": handle,
                "n": name,
                "u": disc_url,
                "m": json.dumps({"discovery": "manual"}),
            },
        )
    print(f"[green]Added[/green] Master Insight author '{name}' ({handle})")
    print(f"  Run [bold]kb scrape run master-insight[/bold] to fetch their articles.")


@master_insight_app.command("list-authors")
def master_insight_list_authors() -> None:
    """List all registered Master Insight authors."""
    with engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.handle, c.name, c.metadata->>'discovery' AS disc "
                "FROM channel c JOIN source s ON c.source_id=s.id "
                "WHERE s.code='master-insight' ORDER BY c.name"
            )
        ).fetchall()
    if not rows:
        print("[yellow]No Master Insight authors registered.[/yellow]")
        print("Add one with: [bold]kb master-insight add-author <author_slug>[/bold]")
        return
    print(f"{'Handle':<20} {'Name':<20} {'Discovery':<8}")
    print("-" * 50)
    for handle, name, disc in rows:
        print(f"  {handle:<18} {name:<20} {(disc or '?'):<8}")


@master_insight_app.command("rm-author")
def master_insight_rm_author(
    handle: str = typer.Argument(..., help="Handle (slug) to remove"),
) -> None:
    """Remove a Master Insight author from the DB."""
    with engine().begin() as conn:
        n = conn.execute(
            text(
                "DELETE FROM channel USING source "
                "WHERE channel.source_id=source.id AND source.code='master-insight' "
                "AND channel.handle=:h"
            ),
            {"h": handle},
        ).rowcount
    if n:
        print(f"[green]Removed[/green] '{handle}'")
    else:
        print(f"[yellow]Not found:[/yellow] '{handle}'")


@businessfocus_app.command("add-author")
def businessfocus_add_author(
    handle: str = typer.Argument(..., help="Author slug, e.g. shing"),
) -> None:
    """Register a BusinessFocus author by slug (name resolved via node_api)."""
    import httpx

    from .scrapers.businessfocus import BASE, PAGE_ID

    handle = handle.strip()
    if not _re.fullmatch(r"[A-Za-z0-9._-]+", handle):
        print("[red]Author slug may only contain letters, digits, '.', '_', '-'[/red]")
        raise typer.Exit(1)

    headers = {"User-Agent": settings().scrape_user_agent,
               "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"}
    name, blogger_id = handle, None
    try:
        print(f"Resolving author name for '{handle}' from node_api...")
        with httpx.Client(timeout=30.0, headers=headers,
                          follow_redirects=True) as client:
            r = client.get(f"{BASE}/node_api/v1/authors/blogger/{handle}",
                           params={"pageId": PAGE_ID})
        if r.status_code == 200:
            data = r.json().get("data") or {}
            if data.get("id"):
                name = data.get("display_name") or handle
                blogger_id = data["id"]
                print(f"Resolved name: [green]{name}[/green] (blogger id {blogger_id})")
            else:
                print(f"[red]No BusinessFocus blogger found for '{handle}'.[/red]")
                raise typer.Exit(1)
        else:
            print(f"[red]Author resolve returned HTTP {r.status_code}.[/red]")
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:
        print(f"[red]Could not resolve author name:[/red] {exc}")
        raise typer.Exit(1) from exc

    with engine().begin() as conn:
        sid = conn.execute(
            text("SELECT id FROM source WHERE code='businessfocus'")
        ).scalar_one_or_none()
        if sid is None:
            conn.execute(
                text(
                    "INSERT INTO source(code, name, url, kind) "
                    "VALUES ('businessfocus', 'BusinessFocus', 'https://businessfocus.io/', 'newspaper') "
                    "ON CONFLICT (code) DO NOTHING"
                )
            )
            sid = conn.execute(
                text("SELECT id FROM source WHERE code='businessfocus'")
            ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO channel(source_id, handle, name, url, metadata) "
                "VALUES (:s,:h,:n,:u,CAST(:m AS jsonb)) "
                "ON CONFLICT (source_id, handle) "
                "DO UPDATE SET name=EXCLUDED.name, url=EXCLUDED.url, metadata=EXCLUDED.metadata"
            ),
            {
                "s": sid,
                "h": handle,
                "n": name,
                "u": f"{BASE}/author/{handle}",
                "m": json.dumps({"discovery": "manual",
                                 **({"blogger_id": blogger_id} if blogger_id else {})}),
            },
        )
    print(f"[green]Added[/green] BusinessFocus author '{name}' ({handle})")
    print(f"  Run [bold]kb scrape run businessfocus[/bold] to fetch their articles.")


@businessfocus_app.command("list-authors")
def businessfocus_list_authors() -> None:
    """List registered BusinessFocus authors."""
    with engine().connect() as conn:
        rows = conn.execute(
            text("""
                SELECT c.handle, c.name, c.metadata->>'blogger_id' AS bid
                FROM channel c JOIN source s ON c.source_id=s.id
                WHERE s.code='businessfocus' ORDER BY c.handle
            """)
        ).all()
    if not rows:
        print("No BusinessFocus authors registered.")
        print("Add one with: [bold]kb businessfocus add-author <author_slug>[/bold]")
        return
    for h, n, bid in rows:
        print(f"  {h:<20} {n:<16} blogger_id={bid or '?'}")


@businessfocus_app.command("rm-author")
def businessfocus_rm_author(
    handle: str = typer.Argument(..., help="Handle (slug) to remove"),
) -> None:
    """Remove a BusinessFocus author from the DB."""
    with engine().begin() as conn:
        n = conn.execute(
            text(
                "DELETE FROM channel USING source "
                "WHERE channel.source_id=source.id AND source.code='businessfocus' "
                "AND channel.handle=:h"
            ),
            {"h": handle},
        ).rowcount
    if n:
        print(f"[green]Removed[/green] '{handle}'")
    else:
        print(f"[yellow]Not found:[/yellow] '{handle}'")


@patreon_app.command("check-session")
def patreon_check_session(
    cookies_from_browser: str = typer.Option(
        "", "--cookies-from-browser", help="e.g. chrome, edge (if PATREON_SESSION_ID unset)",
    ),
) -> None:
    """Verify PATREON_SESSION_ID cookie against Patreon."""
    from .scrapers.patreon import PatreonScraper

    sc = PatreonScraper(cookies_from_browser=cookies_from_browser or None)
    try:
        info = asyncio.run(sc.check_session())
    except RuntimeError as exc:
        print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        log.exception("patreon session check failed")
        print(f"[red]Session check failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    name = info.get("full_name") or "(unknown)"
    print(f"[green]OK[/green] — logged in as {name}")
    if info.get("url"):
        print(f"  Profile: {info['url']}")


@patreon_app.command("resolve")
def patreon_resolve(
    vanity: str = typer.Argument(..., help="Patreon vanity slug, e.g. macroalf"),
    cookies_from_browser: str = typer.Option(
        "", "--cookies-from-browser", help="e.g. chrome, edge (if PATREON_SESSION_ID unset)",
    ),
) -> None:
    """Resolve a creator vanity slug to campaign_id (requires valid session)."""
    from .scrapers.patreon import PatreonScraper

    sc = PatreonScraper(cookies_from_browser=cookies_from_browser or None)
    try:
        async def _run() -> str:
            async with await sc.http() as client:
                return await sc.resolve_campaign_id(client, vanity)

        campaign_id = asyncio.run(_run())
    except RuntimeError as exc:
        print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except ValueError as exc:
        print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        log.exception("patreon resolve failed")
        print(f"[red]Resolve failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    print(f"[green]{vanity}[/green] → campaign_id [bold]{campaign_id}[/bold]")
    print("  Cached in channel.metadata when the handle is registered in DB.")


@patreon_app.command("prime-session")
def patreon_prime_session(
    creator: str = typer.Option(
        "aminvest", help="Creator vanity — opens their posts page after login",
    ),
    wait_minutes: int = typer.Option(10, help="Minutes to wait for login"),
) -> None:
    """Open Patreon in a browser; log in manually, then save session_id for API scraping."""
    from .scrapers.patreon import PatreonScraper, SESSION_PATH

    sc = PatreonScraper()
    print(
        "\n[bold]Patreon login[/bold] — a browser window will open.\n"
        "Log into patreon.com if needed; leave the window on the creator posts page.\n"
        f"Session will be saved to [cyan]{SESSION_PATH}[/cyan]\n"
    )
    ok = asyncio.run(sc.prime_session(creator, wait_sec=wait_minutes * 60))
    if ok:
        print("[green]Session saved.[/green] Run:")
        print(f'  kb patreon scrape {creator} --limit 3')
    else:
        print("[red]Timed out waiting for login.[/red]")
        raise typer.Exit(1)


@patreon_app.command("list-years")
def patreon_list_years(
    creator: str = typer.Argument(..., help="Vanity or URL, e.g. aminvest"),
    cookies_from_browser: str = typer.Option(
        "", "--cookies-from-browser", help="e.g. chrome, edge (if PATREON_SESSION_ID unset)",
    ),
) -> None:
    """List post counts per year for a creator (scrolls all pages via API)."""
    from .scrapers.patreon import PatreonScraper, normalize_vanity

    vanity = normalize_vanity(creator)
    sc = PatreonScraper(
        filter_handle=vanity,
        cookies_from_browser=cookies_from_browser or None,
    )
    try:
        years = asyncio.run(sc.list_years(vanity))
    except Exception as exc:
        log.exception("patreon list-years failed")
        print(f"[red]Failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not years:
        print(f"[yellow]No accessible posts found for {vanity!r}.[/yellow]")
        return
    print(f"Posts by year for [bold]{vanity}[/bold]:")
    for year, count in years.items():
        print(f"  {year}: {count}")


@patreon_browser_app.command("start")
def patreon_browser_start() -> None:
    """Keep one logged-in Patreon browser window open across scrapes."""
    from .scrapers.patreon_daemon import (
        is_daemon_alive,
        start_daemon_process,
        wait_for_daemon,
    )

    if is_daemon_alive():
        print("[green]Patreon browser daemon already running.[/green]")
        return
    print(
        "\n[bold]Starting Patreon browser daemon[/bold]\n"
        "  A Chromium window opens. Log into patreon.com once if needed —\n"
        "  the window stays open and later scrapes reuse this session.\n"
    )
    start_daemon_process()
    if not asyncio.run(wait_for_daemon(60.0)):
        print("[yellow]Daemon did not respond in time.[/yellow]")
        raise typer.Exit(1)
    print("[green]Patreon browser daemon ready.[/green]")
    print("  Next: [bold]kb patreon browser login[/bold] (if not signed in)")


@patreon_browser_app.command("stop")
def patreon_browser_stop() -> None:
    """Close the persistent Patreon browser daemon."""
    from .scrapers.patreon_daemon import daemon_shutdown, is_daemon_alive

    if not is_daemon_alive():
        print("Patreon browser daemon is not running.")
        return
    if asyncio.run(daemon_shutdown()):
        print("[green]Patreon browser daemon stopped.[/green]")
    else:
        print("[yellow]Could not stop daemon cleanly.[/yellow]")


@patreon_browser_app.command("status")
def patreon_browser_status() -> None:
    """Check whether the persistent Patreon browser daemon is running."""
    from .scrapers.patreon_daemon import DAEMON_INFO_PATH, is_daemon_alive

    if is_daemon_alive():
        print(f"[green]Patreon browser daemon running[/green] ({DAEMON_INFO_PATH})")
    else:
        print(
            "[dim]Patreon browser daemon not running[/dim] — "
            "start with: kb patreon browser start"
        )


@patreon_browser_app.command("login")
def patreon_browser_login(
    wait_minutes: int = typer.Option(10, help="Minutes to wait for manual login"),
) -> None:
    """Sign into patreon.com in the daemon browser and save the session cookie."""
    from .scrapers.patreon_daemon import (
        daemon_login,
        is_daemon_alive,
        start_daemon_process,
        wait_for_daemon,
    )

    if not is_daemon_alive():
        print("[dim]Daemon not running — starting it.[/dim]")
        start_daemon_process()
        if not asyncio.run(wait_for_daemon(60.0)):
            print("[red]Daemon did not start.[/red]")
            raise typer.Exit(1)

    print(
        "\n[bold]Patreon login[/bold] — log into patreon.com in the browser window.\n"
        "  Waiting for an authenticated session…\n"
    )
    resp = asyncio.run(daemon_login(wait_sec=wait_minutes * 60))
    if resp and resp.get("ok"):
        print(f"[green]Logged in[/green] as {resp.get('full_name') or '(unknown)'}")
    else:
        err = (resp or {}).get("error", "login not detected")
        print(f"[yellow]{err}[/yellow]")
        raise typer.Exit(1)


@patreon_app.command("list-creators")
def patreon_list_creators(
    all_creators: bool = typer.Option(
        False, "--all", help="Include registered creators with no catalog entries",
    ),
) -> None:
    """List Patreon creators in the local scrape catalog."""
    try:
        creators = _registered_patreon_creators(only_crawled=not all_creators)
    except Exception as exc:
        log.exception("patreon list-creators failed")
        print(f"[red]Failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not creators:
        print("[yellow]No Patreon creators found in the scrape catalog.[/yellow]")
        if not all_creators:
            print("Use [bold]--all[/bold] to include registered creators with no catalog entries.")
        print("Add one by running: [bold]kb patreon scrape <creator>[/bold]")
        return
    scope = "registered" if all_creators else "in the scrape catalog"
    print(f"[bold]{len(creators)} Patreon creator(s) {scope}:[/bold]")
    print(f"  {'Vanity':<24} {'Name'}")
    print("  " + "-" * 50)
    for vanity, name in creators:
        print(f"  {vanity:<24} {name}")
    print("\nScrape one with: [bold]kb patreon scrape <vanity>[/bold]")


@patreon_app.command("scrape")
def patreon_scrape(
    creator: str = typer.Argument(
        ..., help="Vanity or URL, e.g. aminvest or patreon.com/c/aminvest/posts",
    ),
    limit: int = typer.Option(
        0, help="Max new posts to download this run (0 = all pending)",
    ),
    year: int | None = typer.Option(
        None, "--year", help="Only download posts published in this calendar year",
    ),
    name: str = typer.Option("", help="Display name (used when not in DB)"),
    cookies_from_browser: str = typer.Option(
        "", "--cookies-from-browser", help="e.g. chrome, edge (if PATREON_SESSION_ID unset)",
    ),
    register: bool = typer.Option(
        True, "--register/--no-register",
        help="Add creator to DB channel table if missing",
    ),
    build_index: bool = typer.Option(
        True, "--index/--no-index",
        help="Refresh the DB crawl catalog before downloading",
    ),
) -> None:
    """Crawl all posts (this month → back per year) then download pending ones.

    Resumable: a DB catalog (``patreon_post_catalog``) records every post that
    exists plus a ``downloaded`` flag, and each crawled API page is stored so an
    interrupted crawl resumes from the next uncrawled page. New posts (which
    shift page alignment) are detected via a page-1 fingerprint; downloads are
    skipped when the markdown file is already on disk.
    """
    from .scrapers.patreon import PatreonScraper, normalize_vanity
    from .scrapers.patreon_daemon import daemon_sync, is_daemon_alive

    vanity = normalize_vanity(creator)
    display = name or vanity

    if register:
        with engine().begin() as conn:
            sid = conn.execute(
                text("SELECT id FROM source WHERE code='patreon'"),
            ).scalar_one_or_none()
            if sid is not None:
                conn.execute(text(
                    "INSERT INTO channel(source_id, handle, name) VALUES (:s,:h,:n) "
                    "ON CONFLICT (source_id, handle) DO UPDATE SET name=EXCLUDED.name"
                ), {"s": sid, "h": vanity, "n": display})

    # Refresh the cookie from the live browser if the daemon is up.
    if is_daemon_alive():
        synced = asyncio.run(daemon_sync())
        if synced and synced.get("ok"):
            print(f"[dim]session refreshed for {synced.get('full_name') or 'user'}[/dim]")
        elif synced:
            print(f"[yellow]session warning: {synced.get('error')}[/yellow]")

    sc = PatreonScraper(
        filter_year=year,
        filter_handle=vanity,
        filter_display_name=display,
        cookies_from_browser=cookies_from_browser or None,
    )
    year_msg = f", year={year}" if year else ""
    print(
        f"[bold]Patreon scrape[/bold] {vanity!r} — "
        f"limit={limit or '∞'}{year_msg}"
    )

    try:
        paths, stats = asyncio.run(
            sc.scrape_creator(
                vanity, display,
                limit=limit or None,
                year=year,
                build=build_index,
            )
        )
    except RuntimeError as exc:
        print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        log.exception("patreon scrape crashed")
        print(f"[red]Scrape failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    idx = stats.get("index") or {}
    dl = stats.get("download") or {}
    years = stats.get("years") or {}
    print(f"\n[bold]Summary for {vanity!r}[/bold]")
    if idx:
        total = idx.get("total_posts", "?")
        new = idx.get("new", 0)
        reused = idx.get("pages_reused", 0)
        line = f"  Catalog:    {total} posts known ({new} new this run"
        if reused:
            line += f", {reused} page(s) reused"
        line += ")"
        print(line)
        prior = idx.get("prior_total")
        if prior is not None and total not in ("?", None) and total > prior:
            print(
                f"  [cyan]New posts detected: total rose {prior} → {total}[/cyan]"
            )
        if not idx.get("complete", True):
            print(
                "  [yellow]Crawl incomplete (interrupted) — re-run to resume "
                "from the saved cursor[/yellow]"
            )
    print(f"  Pending:    {dl.get('pending', 0)}")
    print(f"  Downloaded: {dl.get('downloaded', 0)}")
    print(f"  Skipped:    {dl.get('skipped', 0)} (already on disk)")
    print(f"  Indexed DB: {dl.get('indexed', 0)}")
    print(f"  Failed:     {dl.get('failed', 0)}")
    if years:
        print("  [bold]Per year[/bold] (downloaded/total):")
        for y in sorted(years, reverse=True):
            yc = years[y]
            label = str(y) if y else "undated"
            print(f"    {label:<8} {yc['downloaded']}/{yc['total']}")
    remaining = dl.get("pending", 0) - dl.get("downloaded", 0) - dl.get("skipped", 0)
    if remaining > 0:
        print(
            f"  [yellow]~{remaining} still pending — re-run to continue "
            f"(already-downloaded posts are skipped)[/yellow]"
        )
    print(f"\n[green]{len(paths)}[/green] new files written")


def _registered_patreon_creators(only_crawled: bool = False) -> list[tuple[str, str]]:
    """(handle, display name) for creators in the DB channel table.

    With ``only_crawled`` set, restrict to creators that already have catalog
    entries (i.e. have been scraped at least once) — used for the unattended
    default so leftover/never-crawled rows are not auto-scraped.
    """
    sql = (
        "SELECT c.handle, COALESCE(c.name, c.handle) "
        "FROM channel c JOIN source s ON s.id=c.source_id "
        "WHERE s.code='patreon' "
    )
    if only_crawled:
        sql += (
            "AND EXISTS (SELECT 1 FROM patreon_post_catalog pc "
            "WHERE pc.channel_id=c.id) "
        )
    sql += "ORDER BY c.handle"
    with engine().connect() as conn:
        rows = conn.execute(text(sql)).fetchall()
    return [(r[0], r[1]) for r in rows]


@patreon_app.command("scrape-creator")
def patreon_scrape_creator(
    creators: list[str] = typer.Argument(
        None, help="Creators to scrape (default: all registered in the DB)",
    ),
    limit: int = typer.Option(
        0, "--limit", help="Max new downloads per creator (0 = all pending)",
    ),
    year: int | None = typer.Option(
        None, "--year", help="Only download posts from this calendar year",
    ),
    download: bool = typer.Option(
        True, "--download/--no-download",
        help="Download pending posts (off = only refresh the catalog)",
    ),
    start_browser: bool = typer.Option(
        True, "--start-browser/--no-start-browser",
        help="Start the browser daemon if it is not already running",
    ),
) -> None:
    """Scrape registered Patreon creators — no LLM, schedulable.

    Prefers the browser daemon (which refreshes the session cookie), and falls
    back to the saved data/patreon/.session.json when the daemon can't run —
    e.g. inside the Jenkins Docker container, where a headed browser cannot
    launch. The session file is shared with the container via the data/ mount,
    so a cookie primed locally (`kb patreon browser login` or
    `kb patreon prime-session`) is reused by both local and Jenkins runs.
    Exit codes: 0 ok, 1 nothing to do, 2 no usable session (needs login).
    Schedule it (e.g. Windows Task Scheduler) via scripts/scrape_patreon.ps1.
    """
    from .scrapers.patreon import PatreonScraper, normalize_vanity
    from .scrapers.patreon_daemon import (
        daemon_sync,
        is_daemon_alive,
        start_daemon_process,
        wait_for_daemon,
    )

    # 1. Session: prefer the live daemon browser (refreshes the cookie), fall
    #    back to the saved session file. Docker (Jenkins) can't run the headed
    #    daemon at all, so skip spawning it there.
    in_docker = Path("/.dockerenv").exists()
    daemon_up = is_daemon_alive()
    if not daemon_up and start_browser and not in_docker:
        print("[dim]Starting Patreon browser daemon…[/dim]")
        start_daemon_process()
        daemon_up = asyncio.run(wait_for_daemon(60.0))
        if not daemon_up:
            print("[yellow]Browser daemon did not start; trying saved session…[/yellow]")

    session: dict | None = None
    if daemon_up:
        synced = asyncio.run(daemon_sync())
        if synced and synced.get("ok"):
            session = synced
            print(f"[green]Session OK[/green] for {session.get('full_name') or 'user'}")
        else:
            err = (synced or {}).get("error", "no valid session")
            print(f"[yellow]Daemon session unusable ({err}); trying saved session…[/yellow]")
    if session is None:
        try:
            who = asyncio.run(PatreonScraper().check_session())
            print(
                f"[green]Session OK[/green] (saved cookie) for "
                f"{who.get('full_name') or 'user'}"
            )
        except Exception as exc:  # noqa: BLE001
            daemon_state = "up" if daemon_up else "unavailable"
            print(
                f"[red]No usable Patreon session (daemon {daemon_state}; "
                f"saved session: {exc}).[/red]\n"
                "  Sign in once: [bold]kb patreon browser login[/bold] "
                "(or kb patreon prime-session)"
            )
            raise typer.Exit(2)

    # 2. Decide which creators to scrape.
    if creators:
        registered = dict(_registered_patreon_creators())
        targets = [
            (normalize_vanity(c), registered.get(normalize_vanity(c), normalize_vanity(c)))
            for c in creators
        ]
    else:
        targets = _registered_patreon_creators(only_crawled=True)
    if not targets:
        print("[yellow]No creators registered. Run: kb patreon scrape <vanity>[/yellow]")
        raise typer.Exit(1)

    print(f"[bold]Scrape[/bold] {len(targets)} creator(s): "
          f"{', '.join(h for h, _ in targets)}")

    totals = {"new": 0, "downloaded": 0, "indexed": 0, "failed": 0, "errors": 0}
    for vanity, display in targets:
        print(f"\n[bold]── {vanity} ──[/bold]")
        # Re-sync before each creator so long runs don't outlive the cookie.
        if daemon_up:
            asyncio.run(daemon_sync())
        sc = PatreonScraper(
            filter_year=year, filter_handle=vanity, filter_display_name=display,
        )
        try:
            if download:
                _paths, stats = asyncio.run(
                    sc.scrape_creator(
                        vanity, display, limit=limit or None, year=year,
                        build=True, ingest=True,
                    )
                )
                idx = stats.get("index") or {}
                dl = stats.get("download") or {}
                totals["new"] += idx.get("new", 0)
                totals["downloaded"] += dl.get("downloaded", 0)
                totals["indexed"] += dl.get("indexed", 0)
                totals["failed"] += dl.get("failed", 0)
                print(
                    f"  catalog {idx.get('catalog_count', '?')} "
                    f"(+{idx.get('new', 0)} new) · downloaded {dl.get('downloaded', 0)} "
                    f"· pending {max(dl.get('pending', 0) - dl.get('downloaded', 0) - dl.get('skipped', 0), 0)}"
                )
            else:
                idx = asyncio.run(sc.crawl_index(vanity, display))
                totals["new"] += idx.get("new", 0)
                print(
                    f"  catalog {idx.get('catalog_count', '?')} "
                    f"(+{idx.get('new', 0)} new) · pending {idx.get('pending', 0)}"
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("Patreon creator scrape failed for %s", vanity)
            print(f"  [red]failed: {exc}[/red]")
            totals["errors"] += 1

    print(
        f"\n[bold]Done.[/bold] new={totals['new']} downloaded={totals['downloaded']} "
        f"indexed={totals['indexed']} failed={totals['failed']} errors={totals['errors']}"
    )
    if totals["errors"]:
        raise typer.Exit(1)


@patreon_app.command("status")
def patreon_status(
    creator: str = typer.Argument(..., help="Vanity or URL of the creator"),
) -> None:
    """Show the DB catalog for a creator: totals, downloaded/pending, per year."""
    from .scrapers.patreon import PatreonScraper, normalize_vanity

    vanity = normalize_vanity(creator)
    sc = PatreonScraper(filter_handle=vanity, filter_display_name=vanity)
    st = sc.catalog_status(vanity)
    if not st.get("registered"):
        print(f"[yellow]{vanity!r} is not registered. Run: kb patreon scrape {vanity}[/yellow]")
        raise typer.Exit(1)

    print(f"[bold]Catalog for {vanity!r}[/bold]")
    print(f"  Total posts (site): {st.get('total_posts') if st.get('total_posts') is not None else '?'}")
    print(f"  Catalogued:         {st.get('catalog_count', 0)}")
    print(f"  Downloaded:         {st.get('downloaded', 0)}")
    print(f"  Pending:            {st.get('pending', 0)}")
    last = st.get("last_full_crawl_at")
    print(f"  Last full crawl:    {last or 'never'}")
    years = st.get("years") or {}
    if years:
        print("  [bold]Per year[/bold] (downloaded/total):")
        for y in sorted(years, reverse=True):
            yc = years[y]
            label = str(y) if y else "undated"
            print(f"    {label:<8} {yc['downloaded']}/{yc['total']}")


@substack_app.command("prime-session")
def substack_prime_session(
    wait_minutes: int = typer.Option(10, help="Minutes to wait for login"),
) -> None:
    """Open substack.com in a browser; log in manually, then save the session cookie."""
    from .scrapers.substack import SESSION_PATH, SubstackScraper

    sc = SubstackScraper()
    print(
        "\n[bold]Substack login[/bold] — a browser window will open.\n"
        "Log into substack.com if needed.\n"
        f"Session will be saved to [cyan]{SESSION_PATH}[/cyan]\n"
    )
    ok = asyncio.run(sc.prime_session(wait_sec=wait_minutes * 60))
    if ok:
        print("[green]Session saved.[/green] Run:")
        print('  kb substack scrape <handle> --limit 3')
    else:
        print("[red]Timed out waiting for login.[/red]")
        raise typer.Exit(1)


@substack_app.command("check-session")
def substack_check_session(
    cookies_from_browser: str = typer.Option(
        "", "--cookies-from-browser", help="e.g. chrome, edge (if SUBSTACK_SESSION_COOKIE unset)",
    ),
) -> None:
    """Verify the saved substack.sid cookie against Substack."""
    from .scrapers.substack import SubstackScraper

    sc = SubstackScraper(cookies_from_browser=cookies_from_browser or None)
    try:
        info = asyncio.run(sc.check_session())
    except RuntimeError as exc:
        print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        log.exception("substack session check failed")
        print(f"[red]Session check failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    print(f"[green]OK[/green] — session valid ({info.get('count', 0)} subscription(s))")
    for name in info.get("publications") or []:
        print(f"  - {name}")


@substack_app.command("resolve")
def substack_resolve(
    handle: str = typer.Argument(
        ..., help="Writer handle or profile URL, e.g. michaelwgreen or substack.com/@michaelwgreen",
    ),
) -> None:
    """Resolve a Substack writer handle to their publication subdomain (no login needed)."""
    from .scrapers.substack import SubstackScraper, normalize_handle

    h = normalize_handle(handle)
    sc = SubstackScraper()

    async def _run() -> dict:
        async with await sc.http() as client:
            return await sc.resolve_publication(client, h)

    try:
        pub = asyncio.run(_run())
    except Exception as exc:
        log.exception("substack resolve failed")
        print(f"[red]Resolve failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    print(f"[green]{h}[/green] → subdomain [bold]{pub['subdomain']}[/bold]")
    if pub.get("custom_domain"):
        forced = "" if pub.get("custom_domain_optional", True) else " [yellow](forced redirect)[/yellow]"
        print(f"  Custom domain: {pub['custom_domain']}{forced}")
    if pub.get("publication_name"):
        print(f"  Name: {pub['publication_name']}")
    print("  Cached in channel.metadata when the handle is registered in DB.")


@substack_app.command("list-channels")
def substack_list_channels() -> None:
    """List Substack publications registered in the DB."""
    _list_channels("substack")


@substack_app.command("scrape")
def substack_scrape(
    handle: str = typer.Argument(
        ..., help="Writer handle or profile URL, e.g. michaelwgreen or substack.com/@michaelwgreen",
    ),
    limit: int = typer.Option(0, help="Max new posts to download this run (0 = all pending)"),
    year: int | None = typer.Option(
        None, "--year", help="Only download posts published in this calendar year",
    ),
    name: str = typer.Option("", help="Display name (used when not in DB)"),
    cookies_from_browser: str = typer.Option(
        "", "--cookies-from-browser", help="e.g. chrome, edge (if SUBSTACK_SESSION_COOKIE unset)",
    ),
    register: bool = typer.Option(
        True, "--register/--no-register", help="Add publication to DB channel table if missing",
    ),
) -> None:
    """Scrape a Substack publication's posts (newest first) and ingest them.

    Free posts (and paid posts a creator has opened up as a free preview) are
    read straight from Substack's public archive/post API — no login needed.
    Paid posts that come back truncated fall back to a logged-in, headless
    browser render; run `kb substack prime-session` once first if you have a
    paid subscription you want the full text of.
    """
    from .scrapers.substack import SubstackScraper, normalize_handle

    h = normalize_handle(handle)
    display = name or h

    if register:
        with engine().begin() as conn:
            sid = conn.execute(
                text("SELECT id FROM source WHERE code='substack'"),
            ).scalar_one_or_none()
            if sid is not None:
                conn.execute(text(
                    "INSERT INTO channel(source_id, handle, name) VALUES (:s,:h,:n) "
                    "ON CONFLICT (source_id, handle) DO UPDATE SET name=EXCLUDED.name"
                ), {"s": sid, "h": h, "n": display})

    sc = SubstackScraper(
        filter_year=year,
        filter_handle=h,
        filter_display_name=display,
        cookies_from_browser=cookies_from_browser or None,
    )
    year_msg = f", year={year}" if year else ""
    print(f"[bold]Substack scrape[/bold] {h!r} — limit={limit or '∞'}{year_msg}")

    try:
        paths = asyncio.run(sc.run(limit=limit or None))
    except RuntimeError as exc:
        print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        log.exception("substack scrape crashed")
        print(f"[red]Scrape failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    print(f"[green]{len(paths)}[/green] new file(s) written")
    for p in paths:
        try:
            ingest_mod.ingest_file(p)
        except Exception as exc:  # noqa: BLE001
            log.exception("ingest failed for %s :: %s", p, exc)


# --- Blog sub-app -------------------------------------------------------
# Blog scrapers (macrovoices, madxcap) share one `blog` source row in the DB
# but each keeps its own scraper class. `kb blog list-sites` shows them; the
# generic `kb blog scrape <site>` dispatches to the right scraper and ingests.


@blog_app.command("list-sites")
def blog_list_sites() -> None:
    """List registered blog scrapers (macrovoices, madxcap, …)."""
    from .scrapers.base import BaseScraper

    sites: list[tuple[str, str]] = []
    for code, cls in SCRAPERS.items():
        # Only scrapers that write to the blog source
        if getattr(cls, "source_code", "") == "blog":
            sites.append((code, cls.name))
    if not sites:
        print("[yellow]No blog scrapers registered.[/yellow]")
        return
    print(f"[bold]{len(sites)} blog site(s):[/bold]")
    print(f"  {'code':<14} {'name'}")
    print("  " + "-" * 40)
    for code, name in sorted(sites):
        print(f"  {code:<14} {name}")
    print("\nScrape one with: [bold]kb blog scrape <site>[/bold]")


@blog_app.command("scrape")
def blog_scrape(
    site: str = typer.Argument(..., help="Blog site code, e.g. macrovoices or madxcap"),
    limit: int = typer.Option(0, help="0 = unlimited"),
    source_type: str | None = typer.Option(
        None, help="Filter by content type (e.g. dcard, facebook for madxcap)"
    ),
) -> None:
    """Scrape one blog site and ingest the new markdown files."""
    sc = get_scraper(site)
    if getattr(type(sc), "source_code", "") != "blog":
        print(f"[red]{site!r} is not a blog site (source_code != 'blog').[/red]")
        print("Use `kb blog list-sites` to see blog scrapers.")
        raise typer.Exit(1)
    try:
        kwargs: dict = {"limit": limit or None}
        if isinstance(source_type, str):
            kwargs["source_type"] = source_type
        paths = asyncio.run(sc.run(**kwargs))
    except Exception as exc:  # noqa: BLE001
        log.exception("blog scrape crashed: %s", exc)
        paths = []
    print(f"[green]{len(paths)}[/green] new files for blog/{site}")
    for p in paths:
        try:
            ingest_mod.ingest_file(p)
        except Exception as exc:  # noqa: BLE001
            log.exception("ingest failed for %s :: %s", p, exc)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
