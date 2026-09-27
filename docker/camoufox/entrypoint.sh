#!/bin/sh
# Entrypoint for the Camoufox browser container.
#
# Starts a virtual display (Xvfb) — required for humanize/cursor movement and
# for rendering — then optionally noVNC (so a human can solve interactive
# Cloudflare challenges / log in at http://localhost:7900), then the Camoufox
# Playwright server bound to 0.0.0.0:9222 with a fixed WS path (/hkej).
#
# The host connects with:  playwright.firefox.connect("ws://127.0.0.1:9222/hkej")
#
# Env vars:
#   CAMOUFOX_PORT     (default 9222)  Playwright WS port inside the container
#   CAMOUFOX_WS_PATH  (default hkej)  WS path segment → ws://host:port/<path>
#   CAMOUFOX_NOVNC    (default 1)     1 = expose noVNC web UI on :7900; 0 = headless-auto only
set -e

export DISPLAY=:99
# Firefox's content sandbox needs Linux user namespaces (clone CLONE_NEWUSER).
# Docker Desktop (Windows/Mac) denies that by default → EPERM, then
# "cannot open display: :99", then the process exits and `restart: unless-stopped`
# loops the container. Disabling the content sandbox is the portable fix;
# `security_opt: seccomp:unconfined` on the compose/docker-run side also works.
export MOZ_DISABLE_CONTENT_SANDBOX=1

# Virtual framebuffer — always on (humanize + page rendering need a display).
# /tmp is writable-layer (or tmpfs) and persists across `restart: unless-stopped`
# restarts: an unclean exit (docker kill, crash) leaves /tmp/.X99-lock and the
# /tmp/.X11-unix/X99 socket behind, after which Xvfb refuses to start forever
# ("Server is already active for display 99") while the stale socket passes the
# wait loop below — a permanent crash loop. This container is the sole owner of
# display :99 in its own mount namespace, so removing the leftovers is safe.
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 /tmp/.X11-unix/X99-lock
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
# Wait until the X socket exists; a fixed 1s sleep races on slow hosts.
i=0
while [ ! -e /tmp/.X11-unix/X99 ]; do
    i=$((i + 1))
    if [ "$i" -gt 50 ]; then
        echo "Xvfb failed to start on :99; last log:" >&2
        cat /tmp/xvfb.log >&2 || true
        exit 1
    fi
    sleep 0.1
done

if [ "${CAMOUFOX_NOVNC:-1}" = "1" ]; then
    # Window manager (gives Cloudflare challenge widgets a sane root window).
    openbox >/tmp/openbox.log 2>&1 &
    # VNC server on the framebuffer, listening on :5900.
    x11vnc -display :99 -forever -nopw -quiet -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
    sleep 1
    # noVNC web UI on :7900, proxied to the VNC server.
    websockify --web=/usr/share/novnc 0.0.0.0:7900 localhost:5900 >/tmp/novnc.log 2>&1 &
    echo "noVNC web UI: http://localhost:7900  (open this to solve Cloudflare / log in)"
else
    echo "noVNC disabled (CAMOUFOX_NOVNC=0) — headless-auto mode"
fi

echo "starting Camoufox Playwright server on 0.0.0.0:${CAMOUFOX_PORT:-9222}/${CAMOUFOX_WS_PATH:-hkej} …"

exec python /launch_server.py "${CAMOUFOX_PORT:-9222}" "${CAMOUFOX_WS_PATH:-hkej}"
