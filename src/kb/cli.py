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
app.add_typer(db_app, name="db")
app.add_typer(scrape_app, name="scrape")
app.add_typer(ext_app, name="extract")
app.add_typer(lb_app, name="leaderboard")
app.add_typer(hkej_app, name="hkej")

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


@hkej_app.command("scrape-author")
def hkej_scrape_author(
    handle: str = typer.Argument(..., help="Author handle, e.g. 李聲揚"),
    limit: int = typer.Option(0, help="Max new articles to fetch (0 = all)"),
    keep_browser: bool = typer.Option(
        False,
        help="Leave browser open after scrape (close window yourself)",
    ),
    login_wait_minutes: int = typer.Option(
        15, help="Minutes to wait for you to log in at the start",
    ),
) -> None:
    """Scrape articles for one author — log in manually when the browser opens."""
    from .scrapers.hkej import HKEJScraper
    from . import ingest as ingest_mod

    print(
        "\n[bold]HKEJ scrape[/bold] — one browser: prime → login → fetch\n"
        "  1. [bold]search.hkej.com[/bold] — stay on Cloudflare until results load\n"
        "  2. [bold]subscribe.hkej.com[/bold] — Cloudflare, then log in (green 登入)\n"
        "  3. Wait for [bold]歡迎（我的賬戶｜登出）[/bold], then scraping continues\n"
        "  Do not close the browser until scraping finishes.\n"
    )
    sc = HKEJScraper()
    paths = asyncio.run(
        sc.run(
            limit=limit or None,
            author_handle=handle,
            keep_browser_open=keep_browser,
            login_wait_sec=login_wait_minutes * 60,
        )
    )
    s = sc.last_stats
    print(f"\n[bold]Summary for {handle!r}[/bold]")
    print(f"  Search lists:     {s.get('search_total', '?')} articles")
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
