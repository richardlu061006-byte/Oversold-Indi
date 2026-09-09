"""Notification channels.

Every notifier takes its settings from the `notifiers:` block in config.yml.
A notifier that fails logs and is skipped — one broken channel must not stop
the others from delivering.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import subprocess
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from . import report
from .screener import Reading

log = logging.getLogger(__name__)


@dataclass
class Payload:
    subject: str
    text: str
    html: str
    markdown: str
    alerts: list[Reading]
    recoveries: list[Reading]

    @property
    def is_empty(self) -> bool:
        return not self.alerts and not self.recoveries


def build_payload(
    alerts: list[Reading], recoveries: list[Reading], kinds: dict[str, str]
) -> Payload:
    line = report.headline(alerts, recoveries)
    return Payload(
        subject=f"[Oversold] {line}",
        text=report.render_text(alerts, recoveries, kinds),
        html=report.render_html(alerts, recoveries, kinds),
        markdown=report.render_markdown(alerts, recoveries),
        alerts=alerts,
        recoveries=recoveries,
    )


class Notifier:
    name = "base"

    def __init__(self, settings: dict[str, Any]):
        self.settings = settings

    def send(self, payload: Payload) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    name = "console"

    def send(self, payload: Payload) -> None:
        print(payload.text)


class FileNotifier(Notifier):
    """Writes an HTML report plus a machine-readable JSON snapshot."""

    name = "file"

    def send(self, payload: Payload) -> None:
        directory = Path(self.settings.get("directory", "reports"))
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self.settings.get("filename_stamp", "latest")
        (directory / f"oversold-{stamp}.html").write_text(payload.html)
        (directory / f"oversold-{stamp}.json").write_text(
            json.dumps(
                {
                    "alerts": [a.to_dict() for a in payload.alerts],
                    "recoveries": [r.to_dict() for r in payload.recoveries],
                },
                indent=2,
            )
        )
        log.info("wrote report to %s", directory)


class EmailNotifier(Notifier):
    """SMTP email. For Gmail use an App Password, not your login password."""

    name = "email"

    def send(self, payload: Payload) -> None:
        host = self.settings.get("host", "smtp.gmail.com")
        port = int(self.settings.get("port", 587))
        username = self.settings.get("username") or ""
        password = self.settings.get("password") or ""
        sender = self.settings.get("from") or username
        recipients = self.settings.get("to") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        if not recipients or not sender:
            raise ValueError("email notifier needs `from` and `to`")

        message = EmailMessage()
        message["Subject"] = payload.subject
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message.set_content(payload.text)
        message.add_alternative(payload.html, subtype="html")

        if self.settings.get("use_ssl"):
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server:
            if not self.settings.get("use_ssl"):
                server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(message)
        log.info("emailed %s", ", ".join(recipients))


class WebhookNotifier(Notifier):
    """Slack, Discord or any JSON endpoint.

    `format: slack | discord | json` picks the body shape.
    """

    name = "webhook"

    def send(self, payload: Payload) -> None:
        import requests

        url = self.settings.get("url")
        if not url:
            raise ValueError("webhook notifier needs a `url`")
        style = self.settings.get("format", "json")

        if style == "slack":
            body: dict[str, Any] = {"text": f"*{payload.subject}*\n{payload.markdown}"}
        elif style == "discord":
            # Discord caps message content at 2000 characters.
            content = f"**{payload.subject}**\n```\n{payload.text[:1800]}\n```"
            body = {"content": content}
        else:
            body = {
                "subject": payload.subject,
                "text": payload.text,
                "alerts": [a.to_dict() for a in payload.alerts],
                "recoveries": [r.to_dict() for r in payload.recoveries],
            }

        response = requests.post(url, json=body, timeout=30)
        response.raise_for_status()
        log.info("posted to webhook (%s)", style)


class NtfyNotifier(Notifier):
    """Push to a phone via ntfy.sh — no account, just a topic name."""

    name = "ntfy"

    def send(self, payload: Payload) -> None:
        import requests

        topic = self.settings.get("topic")
        if not topic:
            raise ValueError("ntfy notifier needs a `topic`")
        server = self.settings.get("server", "https://ntfy.sh").rstrip("/")
        headers = {
            "Title": payload.subject[:200],
            "Tags": "chart_with_downwards_trend",
            "Priority": str(self.settings.get("priority", "default")),
        }
        token = self.settings.get("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = requests.post(
            f"{server}/{topic}",
            data=payload.text.encode("utf-8"),
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        log.info("pushed to ntfy topic %s", topic)


class DesktopNotifier(Notifier):
    """Best-effort local desktop notification (macOS / Linux)."""

    name = "desktop"

    def send(self, payload: Payload) -> None:
        line = report.headline(payload.alerts, payload.recoveries)
        commands = [
            ["notify-send", "Oversold monitor", line],
            [
                "osascript",
                "-e",
                f'display notification {json.dumps(line)} with title "Oversold monitor"',
            ],
        ]
        for command in commands:
            try:
                subprocess.run(command, check=True, capture_output=True, timeout=15)
                return
            except (FileNotFoundError, subprocess.SubprocessError):
                continue
        raise RuntimeError("no desktop notification backend found")


class GithubSummaryNotifier(Notifier):
    """Appends the report to the GitHub Actions run summary."""

    name = "github_summary"

    def send(self, payload: Payload) -> None:
        target = os.environ.get("GITHUB_STEP_SUMMARY")
        if not target:
            log.info("not running in GitHub Actions; skipping summary")
            return
        with open(target, "a") as handle:
            handle.write(f"## {payload.subject}\n\n{payload.markdown}\n")


REGISTRY: dict[str, type[Notifier]] = {
    cls.name: cls
    for cls in (
        ConsoleNotifier,
        FileNotifier,
        EmailNotifier,
        WebhookNotifier,
        NtfyNotifier,
        DesktopNotifier,
        GithubSummaryNotifier,
    )
}


def build_notifiers(specs: list[dict[str, Any]]) -> list[Notifier]:
    notifiers: list[Notifier] = []
    for spec in specs:
        if not spec.get("enabled", True):
            continue
        kind = spec.get("type")
        cls = REGISTRY.get(kind)
        if cls is None:
            log.warning("unknown notifier type %r (have: %s)", kind, ", ".join(REGISTRY))
            continue
        notifiers.append(cls(spec))
    return notifiers or [ConsoleNotifier({})]


def dispatch(notifiers: list[Notifier], payload: Payload) -> list[str]:
    """Send through every notifier; returns the names that failed."""
    failed = []
    for notifier in notifiers:
        try:
            notifier.send(payload)
        except Exception as exc:
            log.error("notifier %s failed: %s", notifier.name, exc)
            failed.append(notifier.name)
    return failed
