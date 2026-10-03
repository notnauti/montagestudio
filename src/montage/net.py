"""HTTP for asset sourcing: stdlib urllib, a timeout on every call, a descriptive User-Agent.

Wikimedia asks API clients for a User-Agent that names the tool and a way to reach its
operator (extracted, Wikimedia User-Agent policy). We name the tool; a contact address is
added only when MONTAGE_CONTACT is set, so nothing personal is sent by default.

Downloads stream to disk and stop as soon as the size cap is passed, so a mislabelled 3 GB
video never lands on disk.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TIMEOUT = 20.0  # seconds, per request (connect + each read)
CHUNK = 1 << 16
VERSION = "0.1"


class NetError(RuntimeError):
    """A request failed: unreachable host, HTTP error, bad JSON, or a download over the cap."""


class TooLarge(NetError):
    pass


def user_agent() -> str:
    contact = os.environ.get("MONTAGE_CONTACT", "").strip()
    return (f"MontageStudio/{VERSION} (personal video pipeline asset sourcing"
            + (f"; {contact}" if contact else "") + ") python-urllib")


def safe_url(url: str) -> str:
    """Percent-encode what upstream hands back unencoded (NASA asset URLs contain spaces) and
    upgrade http to https. Already-encoded %XX sequences are left alone."""
    url = urllib.parse.quote(url, safe=":/?=&%~+#,;@!$'()*[]")
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


class Http:
    """The one place sourcing touches the network. Tests pass a fake with the same two methods."""

    def __init__(self, timeout: float = TIMEOUT, opener=None):
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def _request(self, url: str):
        req = urllib.request.Request(safe_url(url), headers={"User-Agent": user_agent(),
                                                             "Accept": "application/json, */*"})
        try:
            return self._open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raise NetError(f"HTTP {exc.code} from {_host(url)}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NetError(f"could not reach {_host(url)}: {getattr(exc, 'reason', exc)}") from exc

    def json(self, url: str) -> dict:
        with self._request(url) as resp:
            try:
                body = resp.read()
            except (TimeoutError, OSError) as exc:
                raise NetError(f"read from {_host(url)} failed: {exc}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise NetError(f"{_host(url)} returned non-JSON ({body[:80]!r})") from exc

    def download(self, url: str, dest: Path, max_bytes: int) -> int:
        """Stream url to dest; raise TooLarge (and delete the partial file) past max_bytes."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._request(url) as resp:
            declared = resp.headers.get("Content-Length") if getattr(resp, "headers", None) else None
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise TooLarge(f"{int(declared) / 1e6:.1f} MB declared, cap is {max_bytes / 1e6:.0f} MB")
            total = 0
            try:
                with open(dest, "wb") as fh:
                    while chunk := resp.read(CHUNK):
                        total += len(chunk)
                        if total > max_bytes:
                            raise TooLarge(f"passed the {max_bytes / 1e6:.0f} MB cap while downloading")
                        fh.write(chunk)
            except TooLarge:
                dest.unlink(missing_ok=True)
                raise
            except (TimeoutError, OSError) as exc:
                dest.unlink(missing_ok=True)
                raise NetError(f"download from {_host(url)} failed: {exc}") from exc
        return total


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url[:40]
