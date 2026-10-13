"""HTTP access to the PLACSP open-data archives.

TLS: PLACSP's chain ends at "AC RAIZ FNMT-RCM SERVIDORES SEGUROS". It is in Mozilla's root store
(certifi) but not in macOS's curl bundle, so we use httpx + certifi with full verification.
Quirks: HEAD requests are reset by the server, and responses are chunked without
Content-Length. The Apache ETag ("<size hex>-<mtime hex>") gives us the size for progress bars.
"""
from __future__ import annotations

import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import certifi
import httpx

BASE = "https://contrataciondelestado.es/sindicacion"
FEEDS = {
    "643": "licitacionesPerfilesContratanteCompleto3",   # buyers publishing directly on PLACSP
    "1044": "PlataformasAgregadasSinMenores",            # aggregated regional platforms
}
USER_AGENT = "licita/0.1 (open-data research; contact via GitHub repo)"


class ArchiveMissing(Exception):
    """The requested period has no archive on the server (404)."""


class ArchiveCorrupt(Exception):
    """Downloaded file failed zip integrity check after all retries."""


@dataclass(frozen=True)
class Archive:
    feed: str
    period: str  # "2023" (yearly) or "202609" (monthly)

    @property
    def url(self) -> str:
        return f"{BASE}/sindicacion_{self.feed}/{FEEDS[self.feed]}_{self.period}.zip"

    @property
    def name(self) -> str:
        return f"{self.feed}_{self.period}"


def client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(
        verify=certifi.where(),
        timeout=httpx.Timeout(timeout, read=300.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def probe_etag(c: httpx.Client, a: Archive) -> tuple[str | None, int | None]:
    """(ETag, size in bytes) of an archive without downloading it. Raises ArchiveMissing on 404.

    Opens a streaming GET and closes it after the headers, since HEAD is reset by the server. Apache's
    ETag is "<size hex>-<mtime hex>", so it changes whenever PLACSP regenerates the archive.
    """
    with c.stream("GET", a.url) as r:
        if r.status_code == 404:
            raise ArchiveMissing(a.url)
        r.raise_for_status()
        etag = r.headers.get("etag", "").strip('"') or None
    head = (etag or "").split("-")[0]
    try:
        return etag, int(head, 16) if head else None
    except ValueError:
        return etag, None


def probe_size(c: httpx.Client, a: Archive) -> int | None:
    """Expected size in bytes from the ETag, or None if absent. Raises ArchiveMissing on 404."""
    return probe_etag(c, a)[1]


def download(c: httpx.Client, a: Archive, dest_dir: Path, retries: int = 3) -> Path:
    """Download `a` to dest_dir/<name>.zip (via .part), verifying zip integrity. Idempotent."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{a.name}.zip"
    if out.exists() and zipfile.is_zipfile(out):
        return out
    part = out.with_suffix(".zip.part")
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with c.stream("GET", a.url) as r:
                if r.status_code == 404:
                    raise ArchiveMissing(a.url)
                r.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            with zipfile.ZipFile(part) as z:
                bad = z.testzip()
            if bad is not None:
                raise ArchiveCorrupt(f"{a.url}: bad member {bad}")
            part.rename(out)
            return out
        except ArchiveMissing:
            raise
        except (httpx.TransportError, httpx.HTTPStatusError, zipfile.BadZipFile, ArchiveCorrupt) as e:
            last = e
            time.sleep(5 * attempt)
    part.unlink(missing_ok=True)
    raise ArchiveCorrupt(f"{a.url}: failed after {retries} attempts: {last}")
