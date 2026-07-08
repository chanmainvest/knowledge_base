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
_READY_TIMEOUT = 8.0


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
        ``socks5://127.0.0.1:<port>`` URLs (dead tunnels are skipped)."""
        for i, host in enumerate(self.hosts):
            port = self.base_port + i
            url = f"socks5://127.0.0.1:{port}"
            try:
                proc = subprocess.Popen(
                    ["ssh", "-D", str(port), "-N",
                     "-o", "ExitOnForwardFailure=yes",
                     "-o", "ServerAliveInterval=30",
                     "-o", "ServerAliveCountMax=3",
                     "-o", "ConnectTimeout=10",
                     host],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                log.warning("proxy tunnel FAILED for %s on port %d (skipped)", host, port)
        if not self._urls:
            log.warning("no proxy tunnels came up; requests will go direct")
        return list(self._urls)

    def stop(self) -> None:
        """Terminate every tunnel ssh process."""
        for host, port, proc in self._procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                log.info("proxy tunnel down: %s → 127.0.0.1:%d", host, port)
        self._procs.clear()
        self._urls.clear()
        self._idx = 0

    # -- round-robin --------------------------------------------------------
    def next(self) -> str | None:
        """Return the next proxy URL in round-robin order, or ``None`` if no
        tunnels are live (caller should then connect directly)."""
        if not self._urls:
            return None
        url = self._urls[self._idx % len(self._urls)]
        self._idx += 1
        return url

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


def parse_hosts(spec: str) -> list[str]:
    """Parse a comma-separated host spec into a clean list of hostnames."""
    return [h.strip() for h in spec.split(",") if h.strip()]
