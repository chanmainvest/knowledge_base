"""SOCKS5 proxy pool over SSH dynamic tunnels (``ssh -D``).

Manages a pool of background ``ssh -D <local_port> -N <host>`` tunnels, one
per SSH host alias, so yt-dlp requests can be distributed round-robin across
multiple egress IPs. This avoids YouTube's per-IP rate limiting (HTTP 429)
without paying for a rotating-proxy service: each tunnel is just an SSH
connection to one of your own hosts, exposing a local SOCKS5 endpoint.

Used as a context manager so the tunnels are always torn down when the scrape
ends (or crashes)::

    with ProxyPool(["oc1.hevangel.com", "serv00"]) as pool:
        scraper.proxy_pool = pool
        scraper.run(...)

Inside the scraper, ``pool.next()`` cycles through ``socks5://127.0.0.1:<port>``
URLs so each yt-dlp invocation hits a different IP.

The SSH hosts are expected to be resolvable aliases in ``~/.ssh/config``
(e.g. ``Host oc1.hevangel.com``). Key auth is assumed (no password prompt).
"""
from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

from ..logging_setup import get_logger

log = get_logger("proxy")

# Local ports are assigned consecutively from this base. 1081+ avoids the
# common 1080 default so a manually-opened tunnel there isn't clobbered.
_BASE_PORT = 1081
# Seconds to wait for each tunnel's SOCKS port to start accepting connections
# after spawning ssh, before declaring the tunnel dead and moving on.
_READY_TIMEOUT = 12.0


class ProxyPool:
    """A round-robin pool of SSH dynamic-forward (SOCKS5) tunnels."""

    def __init__(self, hosts: list[str], base_port: int = _BASE_PORT) -> None:
        self.hosts = list(hosts)
        self.base_port = base_port
        self._procs: list[tuple[str, int, subprocess.Popen]] = []  # (host, port, popen)
        self._urls: list[str] = []
        self._idx = 0

    # -- context manager ----------------------------------------------------
    def __enter__(self) -> "ProxyPool":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> list[str]:
        """Spawn one ``ssh -D`` tunnel per host. Returns the list of live
        ``socks5://127.0.0.1:<port>`` URLs (dead tunnels are skipped).

        Each tunnel binds a *free* local port rather than ``base_port+i`` so
        orphaned ssh processes from a previous run (which still hold their
        port) can't cause a silent ``ExitOnForwardFailure`` collision — the
        original cause of yt-dlp's ``4 bytes missing`` here."""
        for i, host in enumerate(self.hosts):
            port = self._next_free_port(self.base_port + i)
            url = f"socks5://127.0.0.1:{port}"
            try:
                proc = subprocess.Popen(
                    ["ssh", "-D", str(port), "-N",
                     "-o", "ExitOnForwardFailure=yes",
                     # Detect a dead tunnel fast so we stop handing clients a
                     # half-open socket (the cause of yt-dlp's "4 bytes missing"
                     # SOCKS5 EOFError). 15s × 2 = ~30s to teardown.
                     "-o", "ServerAliveInterval=15",
                     "-o", "ServerAliveCountMax=2",
                     "-o", "TCPKeepAlive=yes",
                     "-o", "ConnectTimeout=10",
                     host],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    # Put ssh in its own process group so Ctrl-C / SIGINT on the
                    # scraper doesn't orphan it: stop() reaps via proc.terminate,
                    # and creationflags keeps it detachable on Windows.
                    **self._popen_kwargs(),
                )
            except FileNotFoundError:
                log.error("ssh not found on PATH; cannot open proxy tunnels")
                break
            if self._wait_ready(port):
                self._procs.append((host, port, proc))
                self._urls.append(url)
                log.info("proxy tunnel up: %s → %s (pid %d)", host, url, proc.pid)
            else:
                # ssh failed to establish the forward — kill it and skip.
                self._kill_proc(proc)
                log.warning("proxy tunnel FAILED for %s on port %d (skipped)", host, port)
        if not self._urls:
            log.warning("no proxy tunnels came up; requests will go direct")
        return list(self._urls)

    def stop(self) -> None:
        """Terminate every tunnel ssh process. Uses a forceful kill on Windows
        where ``terminate()`` (SIGTERM) on ssh.exe is unreliable — without this,
        tunnels orphan and squat their port, breaking the next run."""
        for host, port, proc in self._procs:
            if proc.poll() is None:
                self._kill_proc(proc)
                log.info("proxy tunnel down: %s → 127.0.0.1:%d", host, port)
        self._procs.clear()
        self._urls.clear()
        self._idx = 0

    # -- round-robin --------------------------------------------------------
    def next(self) -> str | None:
        """Return the next live proxy URL in round-robin order.

        Tunnels whose ssh process has exited are pruned on the fly so a dead
        connection is never handed to a client (that's what produces yt-dlp's
        ``4 bytes missing`` SOCKS5 EOFError). Returns ``None`` once no tunnel
        is alive, signalling the caller to connect directly."""
        self._reap()
        if not self._urls:
            return None
        url = self._urls[self._idx % len(self._urls)]
        self._idx += 1
        return url

    def _reap(self) -> None:
        """Drop any tunnel whose ssh process has terminated. Reindexes the
        round-robin index so it stays in range after pruning."""
        if not self._procs:
            return
        alive: list[tuple[str, int, subprocess.Popen]] = []
        dead: list[tuple[str, int, subprocess.Popen]] = []
        for entry in self._procs:
            (dead if entry[2].poll() is not None else alive).append(entry)
        if not dead:
            return
        for host, port, proc in dead:
            log.warning("proxy tunnel reaped (ssh exited): %s → 127.0.0.1:%d",
                        host, port)
        self._procs = alive
        self._urls = [f"socks5://127.0.0.1:{port}" for _, port, _ in alive]
        if self._urls:
            self._idx %= len(self._urls)

    @property
    def urls(self) -> list[str]:
        return list(self._urls)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _wait_ready(port: int, timeout: float = _READY_TIMEOUT) -> bool:
        """Poll until the local SOCKS port accepts a connection (ssh has
        bound the dynamic forward) or ``timeout`` elapses."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                    return True
            except OSError:
                time.sleep(0.3)
        return False

    @staticmethod
    def _next_free_port(preferred: int) -> int:
        """Return the first free localhost TCP port at or above *preferred*.

        Binding a socket then closing it is only a hint (TOCTOU), but it's
        good enough here: ``ExitOnForwardFailure=yes`` remains the backstop
        that kills ssh if a race occurs. The point is to avoid *guaranteed*
        collisions with orphans squatting on the fixed ``base_port+i`` slots."""
        import sys
        for port in range(preferred, preferred + 64):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
        # Fallback: let the OS pick.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @staticmethod
    def _popen_kwargs() -> dict:
        """Platform-specific kwargs to keep the tunnel cleanly attached to
        the parent so ``stop()`` can always reap it."""
        import sys
        if sys.platform == "win32":
            # CREATE_NEW_PROCESS_GROUP: child ignores Ctrl-C, so it doesn't
            # detach mid-shutdown. We terminate it explicitly in stop().
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    @staticmethod
    def _kill_proc(proc: subprocess.Popen) -> None:
        """Forcefully terminate an ssh tunnel on every platform. On Windows
        ``Popen.terminate`` posts WM_CLOSE which ssh.exe routinely ignores,
        so we use taskkill /F as the reliable path there."""
        import sys
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def parse_hosts(spec: str) -> list[str]:
    """Parse a comma-separated host spec into a clean list of hostnames."""
    return [h.strip() for h in spec.split(",") if h.strip()]
