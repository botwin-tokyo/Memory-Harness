from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from memory_ingest.config import Settings


class FetchRefused(ValueError):
    'The guardrails said no. The message says why.'


@dataclass
class Source:
    data: bytes
    content_type: str
    title_hint: str
    source_ref: str


def _resolved_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchRefused(f"cannot resolve host: {host}") from exc
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def check_url_allowed(url: str, settings: Settings) -> None:
    'SSRF guard: scheme, harness host, and every resolved IP must be public.'
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchRefused(f"scheme not allowed: {parsed.scheme}")
    host = parsed.hostname or ""
    harness_host = urlparse(settings.harness_base).hostname
    if host == harness_host:
        raise FetchRefused("refusing to fetch the harness itself")
    for ip in _resolved_ips(host):
        if not ip.is_global:
            if settings.allow_private_fetch and (ip.is_loopback or ip.is_private):
                continue
            raise FetchRefused(f"refusing non-public address {ip} for {host}")


def _classify(content_type_header: str, ref: str, data: bytes) -> str:
    ct = content_type_header.split(";")[0].strip().lower()
    if ct == "application/pdf":
        return "pdf"
    if ct in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        return "word"
    if ct.startswith("image/"):
        return "image"
    if ct in ("text/html", "application/xhtml+xml"):
        return "html"
    if ct.startswith("text/") or ct in ("application/markdown",):
        return "text"
    suffix = Path(urlparse(ref).path if "://" in ref else ref).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "word"
    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"):
        return "image"
    if suffix in (".html", ".htm"):
        return "html"
    if suffix in (".txt", ".md", ".markdown", ".rst", ".py", ".c", ".json", ".yaml", ".yml"):
        return "text"
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith((b"\x89PNG", b"\xff\xd8\xff")):
        return "image"
    if b"<html" in data[:2048].lower() or b"<!doctype html" in data[:2048].lower():
        return "html"
    return "text"


def fetch_url(url: str, settings: Settings) -> Source:
    check_url_allowed(url, settings)
    with httpx.Client(
        follow_redirects=True, timeout=settings.fetch_timeout_s,
        http2=True,
        headers={"User-Agent": "memory-ingest/0.1"},
    ) as client:
        with client.stream("GET", url) as r:
            if r.status_code >= 400:
                raise FetchRefused(f"source answered {r.status_code} for {url}")
            check_url_allowed(str(r.url), settings)
            declared = int(r.headers.get("content-length") or 0)
            if declared > settings.fetch_max_bytes:
                raise FetchRefused(f"content-length {declared} over cap")
            chunks: list[bytes] = []
            got = 0
            for chunk in r.iter_bytes():
                got += len(chunk)
                if got > settings.fetch_max_bytes:
                    raise FetchRefused(f"body exceeded cap {settings.fetch_max_bytes}")
                chunks.append(chunk)
            data = b"".join(chunks)
            ctype = _classify(r.headers.get("content-type", ""), str(r.url), data)
    title = Path(urlparse(url).path).name or urlparse(url).hostname or url
    return Source(data=data, content_type=ctype, title_hint=title, source_ref=url)


def read_file(file_path: str, settings: Settings) -> Source:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FetchRefused(f"not a file: {path}")
    if path.stat().st_size > settings.fetch_max_bytes:
        raise FetchRefused(f"file over cap {settings.fetch_max_bytes}")
    data = path.read_bytes()
    return Source(
        data=data,
        content_type=_classify("", str(path), data),
        title_hint=path.stem.replace("_", " ").replace("-", " "),
        source_ref=str(path),
    )


def acquire(source: str, settings: Settings) -> Source:
    '`url | file_path` in, guarded bytes out.'
    if source.startswith(("http://", "https://")):
        return fetch_url(source, settings)
    return read_file(source, settings)
