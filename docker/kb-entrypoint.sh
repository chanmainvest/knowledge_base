#!/bin/sh
# Entrypoint for the kb container.
#
# Problem: the host ~/.ssh is bind-mounted into the container for the YouTube
# SOCKS5 proxy pool. On Windows hosts, bind-mounts inherit the source ACLs, so
# the keys/config arrive world-writable (mode 0777). SSH refuses to read a
# config or private key that is group/world-writable
# ("Bad owner or permissions on /root/.ssh/config"), which silently breaks
# every `ssh -D` tunnel — yt-dlp then falls back to a direct connection and
# YouTube rate-limits it to death (HTTP 429 on nearly every subtitle download).
#
# Fix: the mount is staged at /ssh-staging (read-only); copy it into a private
# /root/.ssh with strict perms (dirs 0700, files 0600) on every container start.
# This never touches the host files.

set -e

if [ -d /ssh-staging ]; then
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh
    # Copy each entry, dropping the world/group bits.
    for f in /ssh-staging/*; do
        [ -e "$f" ] || continue
        name=$(basename "$f")
        cp "$f" "/root/.ssh/$name"
        chmod 600 "/root/.ssh/$name"
    done
    chmod 644 /root/.ssh/*.pub 2>/dev/null || true
    chmod 644 /root/.ssh/known_hosts 2>/dev/null || true
fi

# Hand off to the kb CLI (the image's default entrypoint is `uv run kb`).
exec uv run kb "$@"
