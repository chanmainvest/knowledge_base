"""Long-lived Patreon browser daemon.

Keeps one logged-in Chromium window open across scrape runs so we never have to
re-login. Its only job is to hold the authenticated browser session and refresh
the ``session_id`` cookie on demand — the actual scraping runs in the CLI
process via Patreon's internal JSON API using that cookie.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from typing import Any

from ..config import DATA_DIR, ROOT, settings
from ..logging_setup import get_logger
from .patreon import (
    PATREON_ROOT,
    PatreonScraper,
    _current_user_url,
)

log = get_logger("patreon.daemon")

DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 17822  # HKEJ daemon uses 17821
BROWSER_PROFILE_DIR = DATA_DIR / "patreon" / ".browser_profile"
DAEMON_INFO_PATH = DATA_DIR / "patreon" / ".browser_daemon.json"
_lock = asyncio.Lock()
_shutdown = asyncio.Event()


def _write_info(pid: int) -> None:
    DAEMON_INFO_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAEMON_INFO_PATH.write_text(
        json.dumps({"host": DAEMON_HOST, "port": DAEMON_PORT, "pid": pid}),
        encoding="utf-8",
    )


def _read_info() -> dict | None:
    if not DAEMON_INFO_PATH.exists():
        return None
    try:
        return json.loads(DAEMON_INFO_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _ping_sync() -> bool:
    import socket

    info = _read_info()
    if not info:
        return False
    host = info.get("host", DAEMON_HOST)
    port = int(info.get("port", DAEMON_PORT))
    try:
        with socket.create_connection((host, port), timeout=3.0) as sock:
            sock.sendall(json.dumps({"cmd": "ping"}).encode("utf-8") + b"\n")
            data = sock.recv(4096)
        if not data:
            return False
        resp = json.loads(data.decode("utf-8"))
        return bool(resp.get("ok"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False


def is_daemon_alive() -> bool:
    info = _read_info()
    if not info or not _pid_alive(int(info.get("pid", 0))):
        return False
    return _ping_sync()


async def _request(payload: dict, *, timeout_sec: float = 7200.0) -> dict | None:
    info = _read_info()
    if not info:
        return None
    host = info.get("host", DAEMON_HOST)
    port = int(info.get("port", DAEMON_PORT))
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0,
        )
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        writer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout_sec)
        if not line:
            return None
        return json.loads(line.decode("utf-8"))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _ping_once() -> bool:
    resp = await _request({"cmd": "ping"}, timeout_sec=3.0)
    return bool(resp and resp.get("ok"))


async def daemon_sync() -> dict | None:
    """Refresh the session_id cookie from the live browser."""
    return await _request({"cmd": "sync"}, timeout_sec=60.0)


async def daemon_login(*, wait_sec: float) -> dict | None:
    return await _request(
        {"cmd": "login", "wait_sec": wait_sec}, timeout_sec=wait_sec + 60,
    )


async def daemon_shutdown() -> bool:
    resp = await _request({"cmd": "shutdown"}, timeout_sec=10.0)
    return bool(resp and resp.get("ok"))


def start_daemon_process() -> None:
    if is_daemon_alive():
        log.info("patreon browser daemon already running")
        return
    env = os.environ.copy()
    env["PATREON_DAEMON"] = "1"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        ["uv", "run", "python", "-m", "kb.scrapers.patreon_daemon"],
        cwd=str(ROOT),
        env=env,
        creationflags=creationflags,
        close_fds=True,
    )


async def wait_for_daemon(timeout_sec: float = 60.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        if await _ping_once():
            return True
        await asyncio.sleep(0.5)
    return False


async def _ensure_page(context, page_holder: dict):
    page = page_holder.get("page")
    try:
        if page is not None and not page.is_closed():
            return page
    except Exception:
        pass
    page = context.pages[0] if context.pages else await context.new_page()
    page_holder["page"] = page
    return page


async def _sync_cookie(scraper: PatreonScraper, context) -> dict:
    """Pull session_id from the browser, save it, and verify it works."""
    await scraper._sync_session_from_browser(context)
    sid = scraper._session_id_cache
    if not sid:
        return {"ok": False, "error": "no session_id cookie yet — log in first"}
    try:
        async with await scraper.http() as client:
            data = await scraper._api_get(client, _current_user_url())
        attrs = (data.get("data") or {}).get("attributes") or {}
        return {"ok": True, "full_name": attrs.get("full_name")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"cookie present but session invalid: {exc}"}


async def _wait_login(scraper: PatreonScraper, context, page, wait_sec: float) -> dict:
    try:
        await page.goto(
            f"{PATREON_ROOT}/home", wait_until="domcontentloaded", timeout=120_000,
        )
    except Exception as exc:
        log.debug("login navigate: %s", exc)
    deadline = asyncio.get_running_loop().time() + wait_sec
    while asyncio.get_running_loop().time() < deadline:
        result = await _sync_cookie(scraper, context)
        if result.get("ok"):
            return result
        await asyncio.sleep(2)
    return {"ok": False, "error": "login not detected in time"}


async def _handle_client(reader, writer, scraper, context, page_holder) -> None:
    try:
        line = await reader.readline()
        if not line:
            return
        req = json.loads(line.decode("utf-8"))
        cmd = req.get("cmd")
        if cmd == "ping":
            resp = {"ok": True}
        elif cmd == "shutdown":
            resp = {"ok": True}
            _shutdown.set()
        elif cmd == "sync":
            async with _lock:
                resp = await _sync_cookie(scraper, context)
        elif cmd == "login":
            async with _lock:
                page = await _ensure_page(context, page_holder)
                resp = await _wait_login(
                    scraper, context, page, float(req.get("wait_sec", 600)),
                )
        else:
            resp = {"ok": False, "error": f"unknown cmd: {cmd}"}
        writer.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
        await writer.drain()
    except Exception as exc:  # noqa: BLE001
        log.exception("daemon client error: %s", exc)
        try:
            writer.write(
                json.dumps({"ok": False, "error": str(exc)}).encode("utf-8") + b"\n"
            )
            await writer.drain()
        except Exception:
            pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def run_daemon() -> None:
    os.environ["PATREON_DAEMON"] = "1"
    _write_info(os.getpid())
    scraper = PatreonScraper()

    from playwright.async_api import async_playwright

    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    log.info("starting Patreon browser daemon (pid %d)", os.getpid())

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(BROWSER_PROFILE_DIR),
            headless=False,
            user_agent=settings().scrape_user_agent,
        )
        page_holder: dict[str, Any] = {
            "page": context.pages[0] if context.pages else await context.new_page(),
        }
        log.info("browser open — log into patreon.com in the window if needed")

        async def _warm_up() -> None:
            page = await _ensure_page(context, page_holder)
            try:
                await page.goto(
                    f"{PATREON_ROOT}/home",
                    wait_until="domcontentloaded",
                    timeout=120_000,
                )
            except Exception as exc:
                log.debug("warm-up navigate: %s", exc)
            result = await _sync_cookie(scraper, context)
            if result.get("ok"):
                log.info("session ready for %s", result.get("full_name") or "user")
            else:
                log.warning("not logged in yet: %s", result.get("error"))

        asyncio.create_task(_warm_up())

        async def _client_handler(reader, writer) -> None:
            await _handle_client(reader, writer, scraper, context, page_holder)

        server = await asyncio.start_server(_client_handler, DAEMON_HOST, DAEMON_PORT)
        log.info("daemon listening on %s:%d", DAEMON_HOST, DAEMON_PORT)
        try:
            async with server:
                serve_task = asyncio.create_task(server.serve_forever())
                stop_task = asyncio.create_task(_shutdown.wait())
                _, pending = await asyncio.wait(
                    {serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                log.info("patreon browser daemon shutting down")
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                DAEMON_INFO_PATH.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> None:
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
