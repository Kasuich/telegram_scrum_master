"""Telemost URL validation helpers."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TELEMOST_HOST_RE = re.compile(
    r"(^|\.)telemost(?:\.360)?\.yandex\.(ru|com|com\.tr)$",
    re.IGNORECASE,
)
TELEMOST_PATH_RE = re.compile(r"^/(j|live)/[A-Za-z0-9_-]+/?$")


class TelemostUrlError(ValueError):
    """Raised when a URL is not a supported Telemost meeting URL."""


def normalize_telemost_url(raw_url: str) -> str:
    """Validate and normalize a Telemost meeting URL.

    The MVP supports meeting and live links from Telemost public hosts. Query
    params are preserved except empty tracking noise, and fragments are removed.
    """

    url = raw_url.strip()
    if not url:
        raise TelemostUrlError("Telemost URL is empty")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise TelemostUrlError("Telemost URL must use https")
    if not parsed.netloc or not TELEMOST_HOST_RE.search(parsed.hostname or ""):
        raise TelemostUrlError("URL host is not a supported Telemost host")
    if not TELEMOST_PATH_RE.match(parsed.path):
        raise TelemostUrlError("URL path must look like /j/<meeting-id> or /live/<id>")

    query_items = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)]
    query = urlencode(query_items)
    return urlunparse(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", query, ""))


__all__ = ["TelemostUrlError", "normalize_telemost_url"]
