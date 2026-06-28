"""`kb` CLI."""
from __future__ import annotations

import asyncio
import json
import urllib.parse
from pathlib import Path

import typer
from rich import print
from sqlalchemy import text

from . import extract as extract_mod
from . import ingest as ingest_mod
from . import leaderboard as lb_mod
from . import links as links_mod
from .api.main import main as api_main
from .config import DATA_DIR, ROOT
from .db import engine
from .logging_setup import get_logger
from .scrapers import SCRAPERS, get as get_scraper

app = typer.Typer(no_args_is_help=True, add_completion=False)
db_app = typer.Typer(no_args_is_help=True)
scrape_app = typer.Typer(no_args_is_help=True)
ext_app = typer.Typer(no_args_is_help=True)
lb_app = typer.Typer(no_args_is_help=True)
hkej_app = typer.Typer(no_args_is_help=True, help="HKEJ author management")
hkej_browser_app = typer.Typer(no_args_is_help=True, help="Persistent browser session")
patreon_app = typer.Typer(no_args_is_help=True, help="Patreon session helpers")
patreon_browser_app = typer.Typer(no_args_is_help=True, help="Persistent Patreon browser")
app.add_typer(db_app, name="db")
app.add_typer(scrape_app, name="scrape")
app.add_typer(ext_app, name="extract")
app.add_typer(lb_app, name="leaderboard")
app.add_typer(hkej_app, name="hkej")
hkej_app.add_typer(hkej_browser_app, name="browser")
app.add_typer(patreon_app, name="patreon")
patreon_app.add_typer(patreon_browser_app, name="browser")

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
        for tbl in ("source", "channel", "item", "prediction",
                    "view_market", "chunk", "entity", "leaderboard_weekly"):
            n = c.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
            print(f"  {tbl:22s} {n}")


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
def scrape_list() -> None:
    for code, cls in SCRAPERS.items():
        print(f"  {code:14s} {cls.name}")


@scrape_app.command("list-channels")
def scrape_list_channels(source: str = typer.Argument("youtube")) -> None:
    """List channels registered for a source."""
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
          f"Run `kb scrape run {source}` to scrape it.")


@scrape_app.command("run")
def scrape_run(code: str, limit: int = typer.Option(0, help="0 = unlimited")) -> None:
    sc = get_scraper(code)
    try:
        paths = asyncio.run(sc.run(limit=limit or None))
    except Exception as exc:  # noqa: BLE001
        log.exception("scrape crashed: %s", exc)
        paths = []
    print(f"[green]{len(paths)}[/green] new files for {code}")
    for p in paths:
        try:
            ingest_mod.ingest_file(p)
        except Exception as exc:  # noqa: BLE001
            log.exception("ingest failed for %s :: %s", p, exc)


@scrape_app.command("all")
def scrape_all(limit: int = 5) -> None:
    for code in SCRAPERS:
        try:
            scrape_run(code=code, limit=limit)
        except Exception as exc:  # noqa: BLE001
            log.exception("scrape %s failed: %s", code, exc)


@app.command("ingest")
def ingest_all() -> None:
    n = ingest_mod.ingest_all()
    print(f"[green]ingested[/green] {n}")


@ext_app.command("run")
def extract_run(limit: int = 50) -> None:
    n = extract_mod.run(limit)
    print(f"[green]extracted[/green] {n}")


@lb_app.command("rebuild")
def leaderboard_rebuild() -> None:
    lb_mod.rebuild()
    print("[green]leaderboard updated[/green]")


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
    handle: str = typer.Argument("李聲揚", help="Author handle for search priming"),
    login_wait_minutes: int = typer.Option(15, help="Minutes to wait for manual login"),
) -> None:
    """Prime search + login in one browser (Cloudflare first, then sign in)."""
    from .scrapers.hkej import BROWSER_PROFILE_DIR, HKEJScraper

    print(
        "\n[bold]HKEJ priming[/bold] — one browser, two steps\n"
        "  1. [bold]search.hkej.com[/bold] — stay on Cloudflare until it clears\n"
        "  2. [bold]subscribe.hkej.com[/bold] — Cloudflare again, then log in (green 登入)\n"
        "  3. Header must show [bold]歡迎（我的賬戶｜登出）[/bold]\n"
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


@hkej_app.command("prime-login")
def hkej_prime_login(
    wait_minutes: int = typer.Option(15, help="Minutes to wait for you to finish login"),
) -> None:
    """Open subscribe.hkej.com — wait on Cloudflare, then log in manually."""
    from .scrapers.hkej import BROWSER_PROFILE_DIR, HKEJScraper

    print(
        "\n[bold]HKEJ login priming[/bold]\n"
        "  1. Stay on the Cloudflare page until verification completes\n"
        "  2. Enter your email/password and click the green [bold]登入[/bold] button\n"
        "  3. Wait until the header shows [bold]歡迎（我的賬戶｜登出）[/bold]\n"
        f"\nProfile: {BROWSER_PROFILE_DIR}\n"
    )
    sc = HKEJScraper()
    ok = asyncio.run(sc.prime_login_session(wait_sec=wait_minutes * 60))
    if ok:
        print("[green]Login primed.[/green] Also run search priming if needed:")
        print('  kb hkej prime-search "李聲揚"')
    else:
        print("[yellow]Login not detected in time.[/yellow]")
        raise typer.Exit(1)


@hkej_app.command("prime-search")
def hkej_prime_search(
    handle: str = typer.Argument("李聲揚", help="Author handle to prime search session"),
) -> None:
    """Open search.hkej.com — stay on Cloudflare until search results load."""
    from .scrapers.hkej import BROWSER_PROFILE_DIR, HKEJScraper

    print(
        "\n[bold]HKEJ search priming[/bold]\n"
        "  Stay on the Cloudflare page until search results appear.\n"
        f"  Profile: {BROWSER_PROFILE_DIR}\n"
    )
    sc = HKEJScraper()
    ok = asyncio.run(sc.prime_search_session(handle))
    if ok:
        print(f"[green]Search primed[/green] for {handle!r}")
        print("  Next: kb hkej prime-login  (or kb hkej prime for both in one go)")
    else:
        print("[yellow]Search challenge did not clear in time.[/yellow]")
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


@hkej_app.command("scrape-author")
def hkej_scrape_author(
    handle: str = typer.Argument(..., help="Author handle, e.g. 李聲揚"),
    limit: int = typer.Option(0, help="Max new articles to fetch (0 = all)"),
    keep_browser: bool = typer.Option(
        False,
        help="Leave browser open after scrape (one-off mode only)",
    ),
    login_wait_minutes: int = typer.Option(
        15, help="Minutes to wait for you to log in at the start",
    ),
    use_daemon: bool = typer.Option(
        True,
        "--daemon/--no-daemon",
        help="Reuse persistent browser (kb hkej browser start)",
    ),
) -> None:
    """Scrape articles for one author — reuses open browser when daemon is running."""
    from .scrapers.hkej import HKEJScraper
    from .scrapers.hkej_daemon import (
        is_daemon_alive,
        start_daemon_process,
        wait_for_daemon,
    )
    from . import ingest as ingest_mod

    if use_daemon and not is_daemon_alive():
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
    elif use_daemon:
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
    name_or_id: str = typer.Argument(
        ..., help="Author name (e.g. 李聲揚) or HKEJ numeric author ID (e.g. 18839)"
    ),
) -> None:
    """Register an HKEJ author for scraping (stored in DB)."""
    is_numeric = name_or_id.strip().isdigit()
    handle = name_or_id.strip()

    if is_numeric:
        display_name = handle          # placeholder; real name shows after first scrape
        disc_url = f"https://www.hkej.com/wm/authordetail/id/{handle}"
        metadata: dict = {"hkej_id": handle, "discovery": "wm"}
    else:
        display_name = handle
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
            {"s": sid, "h": handle, "n": display_name, "u": disc_url,
             "m": json.dumps(metadata)},
        )
    label = f"ID {handle}" if is_numeric else f"name '{handle}'"
    print(f"[green]Added[/green] HKEJ author by {label}")
    print(f"  Discovery URL: {disc_url}")
    print(f"  Run [bold]kb scrape run hkej[/bold] to fetch their articles.")


@hkej_app.command("list-authors")
def hkej_list_authors() -> None:
    """List all registered HKEJ authors."""
    with engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.handle, c.name, c.metadata->>'discovery' AS disc, "
                "c.metadata->>'hkej_id' AS hkej_id "
                "FROM channel c JOIN source s ON c.source_id=s.id "
                "WHERE s.code='hkej' ORDER BY c.name"
            )
        ).fetchall()
    if not rows:
        print("[yellow]No HKEJ authors registered.[/yellow]")
        print("Add one with: [bold]kb hkej add-author <name_or_id>[/bold]")
        return
    print(f"{'Handle':<20} {'Name':<20} {'Discovery':<8} {'HKEJ ID'}")
    print("-" * 60)
    for handle, name, disc, hkej_id in rows:
        print(f"  {handle:<18} {name:<20} {(disc or '?'):<8} {hkej_id or ''}")


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


@patreon_app.command("list-subscriptions")
def patreon_list_subscriptions(
    cookies_from_browser: str = typer.Option(
        "", "--cookies-from-browser", help="e.g. chrome, edge (if PATREON_SESSION_ID unset)",
    ),
) -> None:
    """List the creators you are subscribed to (a patron of)."""
    from .scrapers.patreon import PatreonScraper
    from .scrapers.patreon_daemon import daemon_sync, is_daemon_alive

    if is_daemon_alive():
        asyncio.run(daemon_sync())

    sc = PatreonScraper(cookies_from_browser=cookies_from_browser or None)
    try:
        subs = asyncio.run(sc.list_subscriptions())
    except RuntimeError as exc:
        print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        log.exception("patreon list-subscriptions failed")
        print(f"[red]Failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not subs:
        print("[yellow]No subscriptions found (or session not logged in).[/yellow]")
        return
    print(f"[bold]{len(subs)} subscription(s):[/bold]")
    print(f"  {'Vanity':<24} {'Name'}")
    print("  " + "-" * 50)
    for s in subs:
        print(f"  {s['vanity']:<24} {s['name']}")
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


@patreon_app.command("auto")
def patreon_auto(
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
    """Unattended scrape of every registered creator — no LLM, schedulable.

    Ensures the browser daemon is up, refreshes the session cookie, then crawls
    and downloads each creator incrementally (already-downloaded posts skipped).
    Exit codes: 0 ok, 1 nothing to do, 2 session/daemon problem (needs login).
    Schedule it (e.g. Windows Task Scheduler) via scripts/scrape_patreon.ps1.
    """
    from .scrapers.patreon import PatreonScraper, normalize_vanity
    from .scrapers.patreon_daemon import (
        daemon_sync,
        is_daemon_alive,
        start_daemon_process,
        wait_for_daemon,
    )

    # 1. Ensure the logged-in browser daemon is running.
    if not is_daemon_alive():
        if not start_browser:
            print("[red]Browser daemon not running (and --no-start-browser).[/red]")
            raise typer.Exit(2)
        print("[dim]Starting Patreon browser daemon…[/dim]")
        start_daemon_process()
        if not asyncio.run(wait_for_daemon(60.0)):
            print(
                "[red]Daemon did not start. Run once interactively: "
                "kb patreon browser login[/red]"
            )
            raise typer.Exit(2)

    # 2. Refresh + validate the session cookie.
    synced = asyncio.run(daemon_sync())
    if not (synced and synced.get("ok")):
        err = (synced or {}).get("error", "no valid session")
        print(
            f"[red]Session invalid: {err}.[/red]\n"
            "  Sign in once: [bold]kb patreon browser login[/bold]"
        )
        raise typer.Exit(2)
    print(f"[green]Session OK[/green] for {synced.get('full_name') or 'user'}")

    # 3. Decide which creators to scrape.
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

    print(f"[bold]Auto-scrape[/bold] {len(targets)} creator(s): "
          f"{', '.join(h for h, _ in targets)}")

    totals = {"new": 0, "downloaded": 0, "indexed": 0, "failed": 0, "errors": 0}
    for vanity, display in targets:
        print(f"\n[bold]── {vanity} ──[/bold]")
        # Re-sync before each creator so long runs don't outlive the cookie.
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
            log.exception("auto-scrape failed for %s", vanity)
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
