# Camoufox anti-detect browser as a Playwright WS server, for the HKEJ scraper.
#
# HKEJ sits behind Cloudflare and needs a real (anti-detect) browser. This
# image runs Camoufox (patched Firefox) bound to a virtual display (Xvfb), with
# an optional noVNC web UI so a human can solve interactive Cloudflare
# challenges / log in through http://localhost:7900.
#
# The host connects via Playwright:
#   browser = await playwright.firefox.connect("ws://127.0.0.1:9222/hkej")
#
# Build:  docker compose build camoufox
# Run:    docker compose up -d camoufox   (or: kb hkej docker up)

FROM python:3.12-slim

# Camoufox (patched Firefox) runtime libraries + a virtual framebuffer + VNC.
# Kept as one layer to shrink the image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        x11vnc \
        novnc \
        websockify \
        openbox \
        \
        libgtk-3-0 \
        libasound2 \
        libdbus-glib-1-2 \
        libxt6 \
        libxtst6 \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libpango-1.0-0 \
        libcairo2 \
        libgbm1 \
        libxss1 \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Pin to the versions the host project uses (see pyproject.toml / the venv).
# camoufox's launchServer.js reaches into Playwright's *internal* Node modules
# (driver/package/lib/browserServerImpl.js), which Playwright removed after
# 1.59.x — so pinning is required, not just "latest".
RUN pip install --no-cache-dir 'camoufox==0.4.11' 'playwright==1.59.0'

# Download the Camoufox (patched Firefox) binary into the image.
RUN camoufox fetch

COPY entrypoint.sh /entrypoint.sh
COPY launch_server.py /launch_server.py
RUN chmod +x /entrypoint.sh

# 9222 = Playwright WS endpoint; 7900 = noVNC web UI.
EXPOSE 9222 7900

ENTRYPOINT ["/entrypoint.sh"]
