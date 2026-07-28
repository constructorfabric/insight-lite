#!/usr/bin/env python3
"""Make a failed or missing refresh audible.

In July 2026 the nightly refresh died ten nights in a row and nobody was told. The
cron entry had MAILTO="" and redirected both streams into a log file, so the only
trace was a traceback in /var/log/insight-report.log that no one had reason to
open; meanwhile the portal kept serving the last good day, looking perfectly healthy.
The fix for THAT is not a better collector — it is a channel.

Two ways in, both meant to be called from cron:

  python alert.py check              # stale data -> notify + exit 1
  python alert.py notify "message"   # send one message (use after a failed command)

The channel is ALERT_WEBHOOK_URL — any endpoint accepting {"text": "…"} (Slack and
Mattermost incoming webhooks both do). It is read from the environment and never
stored here. With nothing configured this still writes to stderr and still exits
non-zero, so cron's own output and the exit status remain truthful; it just cannot
reach anyone. Set the variable in .env to actually get notified.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request

WEBHOOK = os.environ.get("ALERT_WEBHOOK_URL", "")
TIMEOUT = float(os.environ.get("ALERT_TIMEOUT_S", "10"))
# Which deployment is shouting. Inside a container gethostname() is the container
# id, which tells a half-awake reader nothing — set ALERT_LABEL (e.g. "prod") so the
# message names the environment instead of a hex string.
LABEL = os.environ.get("ALERT_LABEL") or socket.gethostname()


def send(text: str) -> bool:
    """Post one message. Returns True only if it actually reached the channel."""
    label = f"[insight:{LABEL}] {text}"
    print(label, file=sys.stderr)
    if not WEBHOOK:
        print("ALERT_WEBHOOK_URL not set — nothing was notified.", file=sys.stderr)
        return False
    req = urllib.request.Request(
        WEBHOOK,
        data=json.dumps({"text": label}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if 200 <= resp.status < 300:
                return True
            print(f"webhook returned HTTP {resp.status}", file=sys.stderr)
    except (urllib.error.URLError, OSError) as exc:
        print(f"webhook post failed: {exc}", file=sys.stderr)
    return False


def check() -> int:
    """Notify when the newest stored run is older than the freshness limit."""
    import server                      # reuse ONE definition of "stale"
    payload, ok = server.data_freshness()
    if ok:
        print(f"data fresh: run {payload.get('last_run')} "
              f"({payload.get('age_hours')}h old)", file=sys.stderr)
        return 0
    send(f"report data is STALE — {payload.get('reason')}. "
         f"Last run: {payload.get('last_run') or 'none'}. "
         f"Check the refresh log and /health/data.")
    return 1


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "check":
        return check()
    if cmd == "notify":
        message = " ".join(argv[2:]) or "refresh failed (no detail given)"
        return 0 if send(message) else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
