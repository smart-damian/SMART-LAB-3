"""Send text to Telegram. Standard library only, so nothing extra to install.

Security: the bot token is a password. It is read from environment variables
(set as GitHub Secrets in the cloud, exported in your shell locally) and is
NEVER written in code or committed to git.
"""
from __future__ import annotations

import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request

# Telegram rejects messages over 4096 characters; stay safely below.
MAX_CHARS = 3800


class NotifyError(RuntimeError):
    """Raised when Telegram cannot be reached or rejects the message."""


def split_message(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Cut long text into chunks, only ever breaking between lines so a table
    row is never sliced in half."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


def send_telegram(text: str, token: str | None = None, chat_id: str | None = None) -> None:
    """Send `text` as monospace (<pre>) so table columns line up."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise NotifyError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in split_message(text):
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            # html.escape: symbols like ^GSPC or "<" must not be read as markup.
            "text": f"<pre>{html.escape(chunk.rstrip())}</pre>",
            "parse_mode": "HTML",
        }).encode()
        try:
            with urllib.request.urlopen(url, data=payload, timeout=20) as resp:
                body = json.load(resp)
        except urllib.error.HTTPError as exc:
            # Re-raise WITHOUT the URL: it contains the token and would leak into logs.
            raise NotifyError(f"Telegram rejected the message (HTTP {exc.code})") from None
        except (urllib.error.URLError, TimeoutError):
            raise NotifyError("could not reach Telegram (network problem)") from None
        if not body.get("ok"):
            raise NotifyError("Telegram replied ok=false")
