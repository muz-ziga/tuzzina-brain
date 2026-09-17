"""Shared external-fetch security boundary (Stage B foundation).

Single choke point for every Brain HTTP fetch of untrusted
content (RSS, websites, and any future discovery adapter).
Defense model, extracted verbatim from the proven g1/rss.py
implementation so behavior is identical:

- http/https only, hostname required, no userinfo (@);
- every resolved address (v4+v6, all getaddrinfo results) must
  be globally routable: literals checked without DNS, names
  resolved and every result checked (DNS-rebinding style
  mixed answers refuse);
- redirects capped (5) AND every hop re-validated before
  following (a redirect into 127.0.0.1/metadata/private
  refuses instead of connecting);
- size cap enforced while reading (oversized), timeouts mapped
  to "timeout", HTTP errors to "http-NNN".

Deliberate scope: this guards FETCHING. Configured MCP/server
endpoints are operator-trusted destinations and are validated
separately (scheme only); never route those through refusal
logic meant for discovered content.

TOCTOU residual (documented, not fixable without raw
sockets): DNS is re-resolved per hop, but connect re-resolves
again; a hostile resolver flipping between check and connect
is out of scope for stdlib urllib. Treat as accepted residual.

Errors are FetchError whose str() is a short machine reason
(same vocabulary as the RSS collector: unsafe-url,
dns-failed, non-public-target, too-many-redirects, timeout,
fetch-failed, http-NNN, unsupported-content-type,
oversized). Safe to store, log, and surface.
"""
from __future__ import annotations
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request

MAX_REDIRECTS = 5


class FetchError(Exception):
    """Machine reason only. Never carries bodies, URLs with
    credentials (rejected pre-connect), or response content."""


def _deny(reason: str) -> FetchError:
    return FetchError(reason)


def check_url(url: str) -> urllib.parse.ParseResult:
    """Scheme/host shape gate. No network. Raises FetchError."""
    try:
        p = urllib.parse.urlparse((url or "").strip())
    except Exception:
        raise _deny("unsafe-url")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise _deny("unsafe-url")
    if "@" in (p.netloc or ""):
        raise _deny("unsafe-url")
    return p


def resolve_host(host: str) -> None:
    """Refuse non-public targets. Literals are checked without
    DNS; names resolve (patched in tests) and EVERY address must
    be globally routable. Raises FetchError on any refusal."""
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            raise _deny("dns-failed")
        ips = []
        for info in infos:
            try:
                ips.append(ipaddress.ip_address(info[4][0]))
            except Exception:
                continue
    if not ips:
        raise _deny("dns-failed")
    for ip in ips:
        if not ip.is_global:
            raise _deny("non-public-target")


class _HopCheckedRedirect(urllib.request.HTTPRedirectHandler):
    """Redirect follower that re-validates every hop BEFORE
    connecting: scheme/host shape plus full DNS global check.
    Count-capped like the legacy handler. Raises FetchError
    (never returns a private-bound request)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        n = getattr(req, "_brain_redirects", 0) + 1
        if n > MAX_REDIRECTS:
            raise FetchError("too-many-redirects")
        try:
            p = check_url(newurl)
        except FetchError:
            raise
        except Exception:
            raise FetchError("unsafe-url")
        try:
            resolve_host(p.hostname or "")
        except FetchError:
            raise
        except Exception:
            raise FetchError("dns-failed")
        new_req = super().redirect_request(req, fp, code, msg,
                                           headers, newurl)
        if new_req is None:
            return None
        new_req._brain_redirects = n  # noqa: SLF001 (stdlib req attr)
        return new_req


def _opener():
    """Opener with hop-checked redirects. Module-level
    indirection so tests can substitute a deterministic
    double, mirroring the g1/rss.py seam."""
    return urllib.request.build_opener(_HopCheckedRedirect)


def safe_get(url: str, *, timeout: int = 25,
             max_bytes: int = 1_000_000,
             allowed_content_types: tuple | None = None,
             user_agent: str = "tuzzina-brain/0.1") -> tuple:
    """GET one URL through the full boundary. Returns
    (body_bytes, final_url, content_type). allowed types are
    substring matched case-insensitively; None skips the check.
    Every failure raises FetchError with a machine reason."""
    if timeout is None or timeout <= 0:
        timeout = 25
    if max_bytes is None or max_bytes <= 0:
        max_bytes = 1_000_000
    p = check_url(url)
    resolve_host(p.hostname or "")
    opener = _opener()
    req = urllib.request.Request(url, headers={"User-Agent":
                                               user_agent})
    try:
        with opener.open(req, timeout=timeout) as r:
            final = ""
            try:
                final = r.geturl()
            except Exception:
                final = url
            ctype = ""
            try:
                ctype = r.headers.get("Content-Type", "") or ""
            except Exception:
                ctype = ""
            if allowed_content_types:
                low = ctype.lower()
                if ctype and not any(k in low
                                     for k in allowed_content_types):
                    raise _deny("unsupported-content-type")
            try:
                body = r.read(max_bytes + 1)
            except (TimeoutError, socket.timeout):
                raise _deny("timeout")
            ctype_out = ctype
    except FetchError:
        raise
    except urllib.error.HTTPError as e:
        raise _deny(f"http-{e.code}")
    except (urllib.error.URLError, TimeoutError, socket.timeout,
            ConnectionError, OSError) as e:
        name = type(e).__name__
        raise _deny("timeout" if "imeout" in name else "fetch-failed")
    except Exception:
        raise _deny("fetch-failed")
    if len(body) > max_bytes:
        raise _deny("oversized")
    return body, final, ctype_out
