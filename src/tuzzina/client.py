"""Tuzzina Public API client. Only uses endpoints that exist:
POST /public/v1/upload (multipart) | POST /public/v1/upload-from-url |
POST /public/v1/posts | GET /public/v1/integrations | GET /public/v1/posts.
Retries on connection errors/timeouts only (never on HTTP responses,
so a retry can never duplicate a created post)."""
from __future__ import annotations
import json
import time
import urllib.parse
import urllib.request


class TuzzinaError(RuntimeError):
    pass


class TuzzinaClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 60,
                 retries: int = 2):
        if not api_key:
            raise TuzzinaError("TUZZINA_API_KEY is not set")
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.timeout = timeout
        self.retries = retries

    def _call(self, method: str, path: str, body=None,
              files: dict | None = None) -> dict:
        url = self.base + path
        headers = {"Authorization": self.key}
        if files is None:
            data = json.dumps(body or {}).encode()
            headers["Content-Type"] = "application/json"
        else:
            boundary = "tzbrain1234567890"
            buf = b""
            for name, (fname, mime, content) in files.items():
                buf += (f"--{boundary}\r\nContent-Disposition: form-data; "
                        f'name="{name}"; filename="{fname}"\r\n'
                        f"Content-Type: {mime}\r\n\r\n").encode() + \
                    content + b"\r\n"
            buf += f"--{boundary}--\r\n".encode()
            data = buf
            headers["Content-Type"] = \
                f"multipart/form-data; boundary={boundary}"
        last = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers,
                                             method=method)
                with urllib.request.urlopen(req,
                                            timeout=self.timeout) as r:
                    raw = r.read().decode()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                try:
                    detail = e.read().decode()[:300]
                except Exception:
                    detail = ""
                raise TuzzinaError(f"HTTP {e.code} {path}: {detail}")
            except Exception as e:  # connection/timeout: safe to retry
                last = e
                time.sleep(1 + attempt)
        raise TuzzinaError(f"connection failed {path}: {last}")

    def upload_bytes(self, data: bytes, filename: str,
                     mime: str) -> dict:
        return self._call("POST", "/public/v1/upload",
                          files={"file": (filename, mime, data)})

    def upload_from_url(self, url: str) -> dict:
        return self._call("POST", "/public/v1/upload-from-url",
                          {"url": url})

    def create_draft(self, posts: list, date: str) -> dict:
        return self._call("POST", "/public/v1/posts", {
            "type": "draft", "shortLink": False, "date": date,
            "tags": [], "posts": posts})

    def integrations(self) -> dict:
        return self._call("GET", "/public/v1/integrations")

    def list_posts(self) -> dict:
        return self._call("GET", "/public/v1/posts")

    def find_integration(self, provider: str,
                         name_contains: str = "") -> dict:
        data = self.integrations()
        items = data if isinstance(data, list) else data.get("data", [])
        for it in items:
            pid = it.get("providerIdentifier") or it.get("identifier", "")
            if pid == provider and name_contains.lower() in \
                    str(it.get("name", "")).lower():
                return it
        raise TuzzinaError(f"no {provider} integration"
                           + (f" matching {name_contains!r}"
                              if name_contains else ""))
