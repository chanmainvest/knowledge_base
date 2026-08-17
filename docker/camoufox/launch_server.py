"""Launch the Camoufox Playwright server, dropping null options.

This mirrors ``camoufox.server.launch_server`` but strips keys whose value is
``None`` before serialising the config for Playwright's Node ``launchServer``.
``camoufox.utils.launch_options`` always emits ``proxy``/``args``/``headless``
(set to ``None`` when unused); newer Playwright Node drivers reject
``proxy: null`` with "proxy: expected object, got null". Dropping null-valued
keys lets the same call work across Playwright versions.

Runs inside the container (binds 0.0.0.0 so the host can connect over the
published port). Usage::

    python launch_server.py [port] [ws_path]
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import orjson
from camoufox.server import LAUNCH_SCRIPT, get_nodejs, to_camel_case_dict
from camoufox.utils import launch_options


def launch_server_clean(**kwargs) -> None:
    config = launch_options(**kwargs)
    # Drop null-valued keys so Playwright's Node launchServer doesn't choke on
    # e.g. {"proxy": null}.
    config = {k: v for k, v in config.items() if v is not None}
    nodejs = get_nodejs()
    data = orjson.dumps(to_camel_case_dict(config))

    process = __import__("subprocess").Popen(  # noqa: S403
        [nodejs, str(LAUNCH_SCRIPT)],
        cwd=str(Path(nodejs).parent / "package"),
        stdin=__import__("subprocess").PIPE,
        text=True,
    )
    if process.stdin:
        process.stdin.write(base64.b64encode(data).decode())
        process.stdin.close()
    process.wait()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("CAMOUFOX_PORT", "9222"))
    ws_path = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("CAMOUFOX_WS_PATH", "hkej")
    launch_server_clean(
        host="0.0.0.0",
        port=port,
        ws_path=ws_path,
        humanize=True,
        disable_coop=True,
        i_know_what_im_doing=True,
    )
