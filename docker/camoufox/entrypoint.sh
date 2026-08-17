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

# Virtual framebuffer — always on (humanize + page rendering need a display).
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
sleep 1

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
