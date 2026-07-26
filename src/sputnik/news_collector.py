from __future__ import annotations

import html
import re
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import certifi
import yaml

from .models import NewsWebhookEvent
from .storage import MarketStore


def _text(node: ET.Element, names: tuple[str, ...]) -> str | None:
    for name in names:
        child = node.find(name)
        if child is not None and child.text:
            return child.text.strip()
    return None


def _published(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _summary(value: str | None) -> str | None:
    if not value:
        return None
    clean = re.sub(r"<[^>]+>", " ", html.unescape(value))
    return re.sub(r"\s+", " ", clean).strip()[:2_000] or None


def collect_news(config: dict[str, Any], store: MarketStore, secret: str) -> dict[str, Any]:
    stored = duplicates = failures = 0
    errors: list[dict[str, str]] = []
    for feed in config.get("feeds", []):
        try:
            url = str(feed["url"])
            host = (urlparse(url).hostname or "").lower()
            if urlparse(url).scheme != "https" or host not in set(feed.get("allowed_hosts", [])):
                raise ValueError("feed URL is not HTTPS or its host is not allowlisted")
            request = urllib.request.Request(url, headers={"User-Agent": "SputnikNews/1.0"})
            tls = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(request, timeout=20, context=tls) as response:
                root = ET.fromstring(response.read(2_000_000))
            items = root.findall(".//item") or root.findall(".//{*}entry")
            for item in items[: int(feed.get("limit", 30))]:
                title = _text(item, ("title", "{*}title"))
                link = _text(item, ("link", "{*}link"))
                if not link:
                    link_node = item.find("{*}link")
                    link = link_node.attrib.get("href") if link_node is not None else None
                if not title or not link or urlparse(link).scheme != "https":
                    continue
                source = _text(item, ("source", "{*}source")) or str(feed["name"])
                result = store.record_news(
                    NewsWebhookEvent(
                        schema_version=1,
                        secret=secret,
                        source=source[:120],
                        title=title[:500],
                        url=link,
                        published_at=_published(
                            _text(item, ("pubDate", "published", "updated", "{*}published", "{*}updated"))
                        ),
                        symbols=list(feed.get("symbols", [])),
                        summary=_summary(_text(item, ("description", "summary", "{*}summary"))),
                        category=str(feed.get("category", "internet_news"))[:80],
                    )
                )
                if result["duplicate"]:
                    duplicates += 1
                else:
                    stored += 1
        except Exception as error:
            failures += 1
            errors.append(
                {
                    "feed": str(feed.get("name", "unknown"))[:120],
                    "error": f"{type(error).__name__}: {error}"[:300],
                }
            )
    return {
        "stored": stored,
        "duplicates": duplicates,
        "feed_failures": failures,
        "errors": errors,
    }


def load_news_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise ValueError("news config must be an object")
    return value
