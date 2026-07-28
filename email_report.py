#!/usr/bin/env python3
"""Email the rendered report.html to the configured recipients.

The actual send is gated behind config.email.enabled AND the presence of SMTP
env vars, so it is a safe no-op until you deliberately wire credentials.

Env (set as GitHub Actions secrets later):
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS
Recipients & subject/from live in config.yaml -> email:.
"""
from __future__ import annotations

import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yaml

import paths

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    with open(os.path.join(ROOT, "config.yaml")) as fh:
        cfg = yaml.safe_load(fh)
    ec = cfg.get("email", {})

    recipients = ec.get("recipients") or []
    if not ec.get("enabled"):
        print("email.enabled=false — skipping send (report.html still generated).")
        return
    if not recipients:
        print("email.enabled=true but no recipients in config.yaml — skipping.")
        return

    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    port = int(os.environ.get("SMTP_PORT", "587"))
    if not (host and user and pwd):
        print("SMTP_HOST/SMTP_USER/SMTP_PASS not set — skipping send.", file=sys.stderr)
        return

    with open(paths.data_path("report.html")) as fh:
        html = fh.read()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = ec.get("subject", "Constructor Insight report")
    msg["From"] = ec.get("from_addr", user)
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText("Your client does not support HTML email. See attached.", "plain"))
    msg.attach(MIMEText(html, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ctx)
        s.login(user, pwd)
        s.sendmail(msg["From"], recipients, msg.as_string())
    print(f"Sent report to {len(recipients)} recipient(s).")


if __name__ == "__main__":
    main()
