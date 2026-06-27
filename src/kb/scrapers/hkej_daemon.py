"""Long-lived HKEJ browser daemon — one Camoufox window across scrape runs."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..config import ROOT
from ..logging_setup import get_logger
from .hkej import BROWSER_PROFILE_DIR, HKEJScraper

log = get_logger("hkej.daemon")

DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 17821
DAEMON_INFO_PATH = BROWSER_PROFILE_DIR.parent / ".browser_daemon.json"
_scrape_lock = asyncio.Lock()
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


async def _ping_once() -> bool:
    resp = await _request({"cmd": "ping"}, timeout_sec=3.0)
    return bool(resp and resp.get("ok"))


async def _request(payload: dict, *, timeout_sec: float = 7200.0) -> dict | None:
    info = _read_info()
    if not info:
        return None
    host = info.get("host", DAEMON_HOST)
    port = int(info.get("port", DAEMON_PORT))
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=5.0,
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


async def daemon_scrape_author(
    handle: str,
    *,
    limit: int | None,
    login_wait_sec: float,
) -> dict | None:
    return await _request(
        {
            "cmd": "scrape_author",
            "handle": handle,
            "limit": limit,
            "login_wait_sec": login_wait_sec,
        },
        timeout_sec=max(login_wait_sec + 3600, 7200),
    )


async def daemon_shutdown() -> bool:
    resp = await _request({"cmd": "shutdown"}, timeout_sec=10.0)
    return bool(resp and resp.get("ok"))


async def daemon_prime_login(*, wait_sec: float) -> dict | None:
    return await _request(
        {"cmd": "prime_login", "wait_sec": wait_sec},
        timeout_sec=wait_sec + 60,
    )


def start_daemon_process(*, login_wait_minutes: int = 15) -> None:
    if is_daemon_alive():
        log.info("browser daemon already running")
        return
    env = os.environ.copy()
    env["HKEJ_DAEMON"] = "1"
    env["HKEJ_DAEMON_LOGIN_WAIT_SEC"] = str(login_wait_minutes * 60)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        ["uv", "run", "python", "-m", "kb.scrapers.hkej_daemon"],
        cwd=str(ROOT),
        env=env,
        creationflags=creationflags,
        close_fds=True,
    )


async def wait_for_daemon(timeout_sec: float = 45.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        if await _ping_once():
            return True
        await asyncio.sleep(0.5)
    return False


async def _ensure_page(context, page_holder: dict):
    """Return an open tab, opening a new one if the browser was closed."""
    page = page_holder.get("page")
    try:
        if page is not None and not page.is_closed():
            return page
    except Exception:
        pass
    page = context.pages[0] if context.pages else await context.new_page()
    page_holder["page"] = page
    return page


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    scraper: HKEJScraper,
    context,
    page_holder: dict,
) -> None:
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
        elif cmd == "prime_login":
            async with _scrape_lock:
                page = await _ensure_page(context, page_holder)
                ok = await scraper._wait_for_manual_login(
                    page,
                    wait_sec=float(req.get("wait_sec", 900)),
                )
            resp = {"ok": ok}
        elif cmd == "scrape_author":
            async with _scrape_lock:
                page = await _ensure_page(context, page_holder)
                paths, stats = await scraper.run_on_page(
                    page,
                    limit=req.get("limit"),
                    author_handle=req.get("handle"),
                    login_wait_sec=float(req.get("login_wait_sec", 900)),
                    skip_prime_if_warm=True,
                )
            if stats.get("aborted"):
                resp = {
                    "ok": False,
                    "error": "session not ready — complete Cloudflare/login in browser",
                    "stats": stats,
                }
            else:
                resp = {
                    "ok": True,
                    "paths": [str(p) for p in paths],
                    "stats": stats,
                }
        else:
            resp = {"ok": False, "error": f"unknown cmd: {cmd}"}
        writer.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
        await writer.drain()
    except SystemExit:
        raise
    except Exception as exc:
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
    os.environ["HKEJ_DAEMON"] = "1"
    login_wait = float(os.environ.get("HKEJ_DAEMON_LOGIN_WAIT_SEC", "900"))
    _write_info(os.getpid())
    scraper = HKEJScraper()
    scraper._daemon_mode = True

    from camoufox.async_api import AsyncCamoufox

    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    log.info("starting HKEJ browser daemon (pid %d)", os.getpid())

    async with AsyncCamoufox(
        headless=False,
        humanize=True,
        persistent_context=True,
        user_data_dir=str(BROWSER_PROFILE_DIR),
        disable_coop=True,
        i_know_what_im_doing=True,
    ) as context:
        page_holder: dict[str, Any] = {
            "page": context.pages[0] if context.pages else await context.new_page(),
        }
        log.info(
            "browser open — complete Cloudflare + login in the window; "
            "scrapes start once the session is ready"
        )

        async def _client_handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        ) -> None:
            await _handle_client(reader, writer, scraper, context, page_holder)

        server = await asyncio.start_server(_client_handler, DAEMON_HOST, DAEMON_PORT)
        log.info("daemon listening on %s:%d", DAEMON_HOST, DAEMON_PORT)

        async def _warm_up() -> None:
            page = await _ensure_page(context, page_holder)
            if not await scraper._prepare_session(
                page, "李聲揚", login_wait, skip_if_warm=False,
            ):
                log.warning("session not ready — complete Cloudflare/login in browser")

        asyncio.create_task(_warm_up())

        try:
            async with server:
                serve_task = asyncio.create_task(server.serve_forever())
                stop_task = asyncio.create_task(_shutdown.wait())
                done, pending = await asyncio.wait(
                    {serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                log.info("browser daemon shutting down")
        finally:
            try:
                DAEMON_INFO_PATH.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> None:
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
