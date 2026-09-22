"""Tuzzina Public API client. Only uses endpoints that exist:
POST /public/v1/upload (multipart) | POST /public/v1/upload-from-url |
POST /public/v1/posts | GET /public/v1/integrations | GET /public/v1/posts.
Retries on connection errors/timeouts only (never on HTTP responses,
so a retry can never duplicate a created post)."""
from __future__ import annotations
import json
import socket
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
              files: dict | None = None, url: str | None = None,
              extra_headers: dict | None = None) -> dict:
        if url is None:
            url = self.base + path
        headers = {"Authorization": self.key}
        if extra_headers:
            headers.update(extra_headers)
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

    def create_post(self, posts: list, date: str,
                    post_type: str = "draft") -> dict:
        """POST /public/v1/posts with an explicit Tuzzina type
        (draft|schedule|now). Same endpoint create_draft uses;
        the type selects Tuzzina-side behavior."""
        return self._call("POST", "/public/v1/posts", {
            "type": post_type, "shortLink": False, "date": date,
            "tags": [], "posts": posts})

    def create_draft(self, posts: list, date: str) -> dict:
        return self.create_post(posts, date, post_type="draft")

    def integrations(self) -> list:
        """List Tuzzina integrations connected to the current org.
        Returns a list of {id, name, identifier, picture, disabled,
        profile, customer} dicts. Tuzzina is the source of truth; the
        brain never invents or duplicates channels.
        """
        data = self._call("GET", "/public/v1/integrations")
        return data if isinstance(data, list) else []

    def get_integration(self, integration_id: str) -> dict:
        """Look up one integration by its Tuzzina id. Used after the
        strategy YAML declares the integration_id; we cross-check it
        against the live Tuzzina list so the brain cannot target a
        dead or unknown channel."""
        if not integration_id:
            raise TuzzinaError("integration_id is required")
        for it in self.integrations():
            if str(it.get("id", "")) == str(integration_id):
                return it
        raise TuzzinaError(
            f"integration {integration_id!r} not found in Tuzzina")

    def get_research_state(self, source_id: str) -> dict | None:
        """Load durable monitoring state for one source. Returns the
        stored state dict, or None when no row exists (HTTP 404).
        Any other failure raises TuzzinaError. The id travels in
        the query string: source ids are URLs, and proxies
        normalize encoded slashes out of path segments."""
        if not source_id or not str(source_id).strip():
            raise TuzzinaError("source_id is required")
        path = "/public/v1/research-state?" + urllib.parse.urlencode(
            {"sourceId": str(source_id)})
        try:
            data = self._call("GET", path)
        except TuzzinaError as e:
            if str(e).startswith("HTTP 404 "):
                return None
            raise
        if isinstance(data, dict) and isinstance(
                data.get("state"), dict):
            return data["state"]
        return None

    def save_research_state(self, source_id: str, state: dict) -> dict:
        """Persist monitoring state (upsert). Raises TuzzinaError
        on failure; PUT is idempotent so a retry cannot corrupt.
        The id travels in the body for the same slash-safety
        reason as the read path."""
        if not source_id or not str(source_id).strip():
            raise TuzzinaError("source_id is required")
        if not isinstance(state, dict):
            raise TuzzinaError("state must be a mapping")
        data = self._call("PUT", "/public/v1/research-state",
                          {"sourceId": str(source_id), "state": state})
        return data if isinstance(data, dict) else {}

    def claim_candidate(self, candidate_id: str, run_id: str,
                        slot: str = "",
                        ttl_minutes: int = 120) -> bool:
        """Atomic cross-slot claim. True = this run holds the
        candidate; False = another run holds it (HTTP 409, an
        explicit loss signal, never silent). Same runId
        re-claiming wins idempotently (retry-safe). Any other
        failure raises TuzzinaError (fail-closed: the cycle
        aborts rather than risking a duplicate)."""
        if not candidate_id or not str(candidate_id).strip():
            raise TuzzinaError("candidate_id is required")
        if not run_id or not str(run_id).strip():
            raise TuzzinaError("run_id is required")
        try:
            data = self._call("POST", "/public/v1/candidate-claims",
                              {"candidateId": str(candidate_id),
                               "runId": str(run_id),
                               "slot": str(slot or ""),
                               "ttlMinutes": int(ttl_minutes)})
        except TuzzinaError as e:
            if str(e).startswith("HTTP 409 "):
                return False
            raise
        return bool(isinstance(data, dict) and
                    data.get("claimed") is True)

    def release_claim(self, candidate_id: str,
                      run_id: str) -> int:
        """Release exactly this run's claim row (0/1 freed).
        Best-effort by contract: callers must never fail a run
        on a release error (expiry covers crash paths)."""
        if not candidate_id or not str(candidate_id).strip():
            raise TuzzinaError("candidate_id is required")
        if not run_id or not str(run_id).strip():
            raise TuzzinaError("run_id is required")
        path = "/public/v1/candidate-claims?" + \
            urllib.parse.urlencode(
                {"candidateId": str(candidate_id),
                 "runId": str(run_id)})
        data = self._call("DELETE", path)
        if isinstance(data, dict) and \
                isinstance(data.get("released"), int):
            return data["released"]
        return 0

    def claim_publication(self, slot_id: str, intent_id: str,
                            run_id: str, candidate_id: str = "",
                            integration_id: str = "",
                            mode: str = "draft",
                            publish_at: str = "") -> dict:
        """Atomic publication claim for one slot+intent. Returns
        the row with claimed=True on win. On loss the server
        answers 409 and this raises TuzzinaError("HTTP 409 ...");
        callers then read get_publication() for the recorded
        outcome (idempotent-complete) instead of re-executing.
        Same runId re-claiming wins idempotently. Any other
        failure raises (fail-closed)."""
        for name, value in (("slot_id", slot_id),
                            ("intent_id", intent_id),
                            ("run_id", run_id)):
            if not value or not str(value).strip():
                raise TuzzinaError(f"{name} is required")
        return self._call("POST",
                          "/public/v1/brain-publications/claim",
                          {"slotId": str(slot_id),
                           "intentId": str(intent_id),
                           "runId": str(run_id),
                           "candidateId": str(candidate_id or ""),
                           "integrationId": str(integration_id or ""),
                           "mode": str(mode or "draft"),
                           "publishAt": str(publish_at or "")})

    def get_publication(self, slot_id: str,
                        intent_id: str) -> dict | None:
        """Read the recorded outcome for one slot+intent, or
        None when no row exists (HTTP 404). Terminal rows are
        final: published (with postIds), failed (with error),
        or pending (another execution in flight)."""
        if not slot_id or not str(slot_id).strip():
            raise TuzzinaError("slot_id is required")
        if not intent_id or not str(intent_id).strip():
            raise TuzzinaError("intent_id is required")
        path = "/public/v1/brain-publications?" + \
            urllib.parse.urlencode(
                {"slotId": str(slot_id),
                 "intentId": str(intent_id)})
        try:
            data = self._call("GET", path)
        except TuzzinaError as e:
            if str(e).startswith("HTTP 404 "):
                return None
            raise
        return data if isinstance(data, dict) else None

    def complete_publication(self, slot_id: str, intent_id: str,
                             run_id: str, status: str,
                             post_ids=None,
                             companion_ids=None,
                             error: str = "") -> dict:
        """Record the terminal outcome. First writer wins;
        later arrivals get completed:false with the recorded
        row. Status is published or failed (server-validated)."""
        for name, value in (("slot_id", slot_id),
                            ("intent_id", intent_id),
                            ("run_id", run_id)):
            if not value or not str(value).strip():
                raise TuzzinaError(f"{name} is required")
        if status not in ("published", "failed"):
            raise TuzzinaError(
                "status must be published or failed")
        return self._call("POST",
                          "/public/v1/brain-publications/complete",
                          {"slotId": str(slot_id),
                           "intentId": str(intent_id),
                           "runId": str(run_id),
                           "status": status,
                           "postIds": list(post_ids or []),
                           "companionIds": list(companion_ids or []),
                           "error": str(error or "")})

    def delete_post(self, post_id: str) -> dict:
        """Delete one post (server deletes its whole group:
        main + companion children). Test-hygiene and explicit
        cleanup path only; never called by the publish flow
        itself."""
        if not post_id or not str(post_id).strip():
            raise TuzzinaError("post_id is required")
        return self._call(
            "DELETE", "/public/v1/posts/" + urllib.parse.quote(
                str(post_id), safe=""))

    def get_content_distribution(self, integration_id: str):
        """Load the integration's content distribution config
        ({formats: {name: count}, enabled}) or None when none is
        configured (unconstrained: pre-Phase-8 behavior). Counts
        are planning guidance; Tuzzina validates executability."""
        if not integration_id or not str(integration_id).strip():
            raise TuzzinaError("integration_id is required")
        path = "/public/v1/content-distribution/" + urllib.parse.quote(
            str(integration_id), safe="")
        try:
            data = self._call("GET", path)
        except TuzzinaError as e:
            if str(e).startswith("HTTP 404 "):
                return None
            raise
        if isinstance(data, dict) and isinstance(
                data.get("formats"), dict):
            return data
        return None

    def get_content_usage(self, integration_id: str, start_date: str):
        """Load published-format usage for one integration since a
        UTC day (YYYY-MM-DD). Returns {format: count} (possibly all
        zeros). Raises on transport/validation errors. The server
        counts top-level QUEUE+PUBLISHED posts; Brain only reads."""
        if not integration_id or not str(integration_id).strip():
            raise TuzzinaError("integration_id is required")
        if not start_date or not str(start_date).strip():
            raise TuzzinaError("start_date is required")
        path = "/public/v1/content-usage/" + urllib.parse.quote(
            str(integration_id), safe="") + "?" + urllib.parse.urlencode(
                {"startDate": str(start_date)})
        data = self._call("GET", path)
        if isinstance(data, dict) and isinstance(
                data.get("usage"), dict):
            return {str(k): int(v) for k, v in
                    data["usage"].items()}
        return {}

    def get_integration_settings(self, integration_id: str) -> dict:
        """Fetch one integration's live provider truth from Tuzzina.

        GET /public/v1/integration-settings/:id returns
        {output: {rules, maxLength, settings, tools}} (or a fallback
        shape when the provider is unknown). The response is stored
        as returned: the brain never validates provider schemas and
        never mirrors them into static constants. Read-only; nothing
        here is consumed by adapters/injection yet (STEP 1: fetch only).
        """
        if not integration_id:
            raise TuzzinaError("integration_id is required")
        data = self._call(
            "GET", f"/public/v1/integration-settings/{integration_id}")
        return data if isinstance(data, dict) else {}

    def _mcp_url(self) -> str:
        """MCP endpoint of the same Tuzzina backend, through the same
        base URL as every other call (LIVE-PROVEN 1C).

        The Public API base carries the deployment's prefix (e.g.
        http://127.0.0.1:4107/api) and the frontend proxy strips it
        before the backend (Nest has no global prefix), so MCP lives
        at base + "/mcp". Appending to the stripped root instead
        hits the frontend auth guard (307 -> /auth). Do NOT strip.
        Same host, same API key, no new credentials.
        """
        return self.base.rstrip("/") + "/mcp"

    def generate_image(self, prompt: str,
                       key_hint: str | None = None,
                       headline: str | None = None,
                       icon_key: str | None = None) -> dict:
        """Delegate image generation to Tuzzina's existing image tool.

        Speaks the already-exposed MCP endpoint (POST base + "/mcp",
        API-key auth) through the generic MCP client and calls the
        existing generateImageTool with Brain's prompt decision.
        Tuzzina owns the engine, the credentials, the ai_images
        credits, the storage, and the returned {id, path}
        reference. The brain never sees image bytes and performs
        no upload for delegated images. Fail-closed: any error
        raises TuzzinaError; there is no local fallback (no local
        image engine exists anymore). key_hint is an optional
        caller-chosen R2 object key prefix for idempotent
        replays (same key overwrites instead of duplicating);
        omitted means server-assigned (legacy behavior). headline
        and icon_key are optional composer inputs (composed ad
        path); omitted preserves the raw image path exactly.
        """
        if not prompt or not str(prompt).strip():
            raise TuzzinaError("prompt is required")
        from mcp.client import McpClient, McpError
        client = McpClient(self._mcp_url(), auth_header=self.key,
                           timeout=self.timeout)
        try:
            client.initialize()
        except McpError:
            raise TuzzinaError(
                "Tuzzina image delegation failed during handshake")
        try:
            tool_args: dict = {"prompt": str(prompt)}
            if key_hint:
                tool_args["keyHint"] = str(key_hint)
            if headline:
                tool_args["headline"] = str(headline)[:200]
            if icon_key:
                tool_args["iconKey"] = str(icon_key)[:150]
            result = client.call_tool("generateImageTool", tool_args)
        except McpError as e:
            msg = str(e)
            if msg.startswith("tool-error:"):
                msg = msg[len("tool-error:"):]
            raise TuzzinaError(
                f"Tuzzina image delegation failed: {msg}")
        if isinstance(result, dict) and result.get("isError"):
            detail = ""
            for item in result.get("content") or []:
                if isinstance(item, dict) and \
                        isinstance(item.get("text"), str):
                    detail = item["text"][:300]
                    break
            raise TuzzinaError(
                "Tuzzina image delegation failed: "
                f"{detail or 'tool error'}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and structured.get("id") and \
                structured.get("path"):
            return {"id": str(structured["id"]),
                    "path": str(structured["path"])}
        for item in result.get("content") or []:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            try:
                data = json.loads(item.get("text") or "")
            except Exception:
                continue
            if isinstance(data, dict) and data.get("id") and \
                    data.get("path"):
                return {"id": str(data["id"]),
                        "path": str(data["path"])}
        raise TuzzinaError(
            "Tuzzina image delegation returned no media reference")

    def generate_video(self, video_type: str, output: str, prompt: str,
                       params: dict | None = None,
                       timeout: int = 600) -> dict:
        """Delegate video generation to Tuzzina's existing public
        video pipeline.

        POST /public/v1/generate-video {type, output, customParams}
        with API-key auth. `video_type` is an opaque Tuzzina
        TEMPLATE identifier (selected by Brain config, validated
        by Tuzzina — Brain holds no template catalog); `output`
        is vertical|horizontal; `params` rides into customParams
        untouched alongside the prompt. Tuzzina owns providers,
        credentials, ai_videos credits, storage, and the returned
        {id, path} reference. Brain never sees video bytes and
        performs no upload.

        Single attempt, never retried (a retry could double-spend
        credits). Long default timeout: Tuzzina polls the provider
        inside the request, so generation takes minutes, not
        seconds. Fail-closed: any error raises TuzzinaError; no
        local fallback, no provider substitution.
        """
        if not str(video_type or "").strip():
            raise TuzzinaError("video_type is required")
        if not prompt or not str(prompt).strip():
            raise TuzzinaError("prompt is required")
        custom = dict(params) if isinstance(params, dict) else {}
        custom["prompt"] = str(prompt)
        payload = json.dumps({
            "type": str(video_type), "output": str(output or ""),
            "customParams": custom}).encode()
        url = self.base + "/public/v1/generate-video"
        req = urllib.request.Request(
            url, data=payload,
            headers={"Authorization": self.key,
                     "Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req,
                                       timeout=timeout) as r:
                raw = r.read().decode()
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode()[:300]
            except Exception:
                detail = ""
            raise TuzzinaError(
                f"HTTP {e.code} /public/v1/generate-video: {detail}")
        except (TimeoutError, socket.timeout):
            raise
        except Exception as e:
            raise TuzzinaError(f"video delegation failed: {e}")
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            raise TuzzinaError(
                "Tuzzina video delegation returned malformed response")
        if not isinstance(data, dict) or not data.get("id") or \
                not data.get("path"):
            raise TuzzinaError(
                "Tuzzina video delegation returned no media reference")
        return {"id": str(data["id"]), "path": str(data["path"])}
