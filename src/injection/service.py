"""InjectionService: CanonicalIntent -> Tuzzina Public API.

Strict order, each step explicit:
  1. validate the intent (InvalidInjectionIntent)
  2. resolve the integration LIVE from Tuzzina by id
     (stale/missing -> TuzzinaError propagates, never masked)
  3. read the platform identifier FROM Tuzzina's response
  4. select the adapter (UnsupportedPlatform propagates)
  5. capability gates (UnsupportedCapability, never invented payload)
  6. translate via the adapter (pure text/media/settings shaping)
  7. send via TuzzinaClient (the ONLY place HTTP happens)

The service itself performs NO HTTP, runs NO scheduler, manages
NO tokens, stores NOTHING, retries NOTHING (retry policy lives in
TuzzinaClient/Tuzzina).
"""
from __future__ import annotations
from adapters import get_adapter
from injection.capabilities import status
from injection.errors import InvalidInjectionIntent, UnsupportedCapability
from injection.intent import MODES, POST_KINDS, CanonicalIntent


class InjectionService:
    def inject(self, intent: CanonicalIntent, client) -> dict:
        """Inject one intent. Returns a summary dict; raises on any
        refusal. `client` is a TuzzinaClient (duck-typed for tests)."""
        self._validate(intent)

        # 2. Live resolution. Tuzzina is the authority; a dead id
        #    fails here with TuzzinaError, never silently.
        record = client.get_integration(intent.integration_id)

        # 3. Platform comes from Tuzzina's response, never from the
        #    strategy, the intent, or a name lookup.
        identifier = str(record.get("identifier") or "")
        if not identifier:
            raise InvalidInjectionIntent(
                "Tuzzina returned an integration without identifier")

        # 4. Adapter selection. Unknown -> UnsupportedPlatform.
        adapter = get_adapter(identifier)
        caps = adapter.capabilities()

        # 5a. Post-kind gate. Unknown post_types key -> let Tuzzina
        #     decide (its validation is authoritative); a reported
        #     list that lacks the kind -> refuse explicitly.
        reported = caps.get("post_types", None)
        if isinstance(reported, (list, tuple, set)) and \
                intent.post_kind not in reported:
            raise UnsupportedCapability(
                f"{identifier} does not support post_kind="
                f"{intent.post_kind!r}")

        # 5b. Story needs an attachment on platforms that say so
        #     (FB: story_needs_attachment). Refuse instead of sending
        #     a payload Tuzzina would have to reject.
        media_cfg = caps.get("media") if isinstance(
            caps.get("media"), dict) else {}
        if intent.post_kind == "story" and not intent.media and \
                media_cfg.get("story_needs_attachment"):
            raise UnsupportedCapability(
                f"{identifier} story requires at least one media item")

        # 6a. Mentions are inline text on FB+IG: append once, here,
        #     before shaping (shape_text owns final assembly, so no
        #     duplication by construction).
        body = (intent.content or "").strip()
        if intent.mentions:
            handles = ["@" + str(m).lstrip("@").strip()
                       for m in intent.mentions]
            handles = [h for h in handles if len(h) > 1]
            if handles:
                body = f"{body} {' '.join(handles)}".strip()

        # 6b. Links via the adapter's rule. attach on a platform that
        #     reports links=unsupported is OMITTED (documented), never
        #     invented as a payload field.
        body = adapter.apply_link(body, intent.link or "",
                                  intent.links_policy or "hide",
                                  intent.cta_style or "Learn more")
        final_text = adapter.shape_text(body, list(intent.hashtags or []))

        # 6c. Media refs validated, then shaped by the adapter.
        images = []
        for m in intent.media:
            if not isinstance(m, dict) or not m.get("id") or \
                    not m.get("path"):
                raise InvalidInjectionIntent(
                    "each media ref needs id and path")
            images.append({"id": m["id"], "path": m["path"]})
        shaped = adapter.shape_media(images)

        # 6d. Settings: adapter defaults, then intent overrides, then
        #     forced identity fields (platform can never drift).
        settings = dict(adapter.default_settings())
        if intent.settings:
            if not isinstance(intent.settings, dict):
                raise InvalidInjectionIntent("settings must be a mapping")
            settings.update(intent.settings)
        settings["__type__"] = identifier
        settings["post_type"] = intent.post_kind
        # Links: attach goes to settings["url"] ONLY on positive
        # support signal. Anything else (unsupported/unknown) omits
        # the URL field entirely -- never invented.
        if (intent.links_policy or "hide") == "attach" and intent.link \
                and status(caps, "links") == "supported":
            settings["url"] = intent.link

        # 7. Single Tuzzina call. No retry here (client/Tuzzina own it).
        posts = [{
            "integration": {"id": intent.integration_id},
            "value": [{"content": final_text, "image": shaped}],
            "settings": settings,
        }]
        response = client.create_post(posts, intent.publish_at,
                                      post_type=intent.mode)
        return {
            "integration_id": intent.integration_id,
            "identifier": identifier,
            "post_kind": intent.post_kind,
            "mode": intent.mode,
            "publish_at": intent.publish_at,
            "response": response,
        }

    @staticmethod
    def _validate(intent: CanonicalIntent) -> None:
        if not isinstance(intent, CanonicalIntent):
            raise InvalidInjectionIntent("intent must be CanonicalIntent")
        if not isinstance(intent.integration_id, str) or \
                not intent.integration_id.strip():
            raise InvalidInjectionIntent("integration_id is required")
        if not isinstance(intent.content, str) or \
                not intent.content.strip():
            raise InvalidInjectionIntent("content is required")
        if intent.mode not in MODES:
            raise InvalidInjectionIntent(
                f"mode must be one of {MODES}")
        if intent.post_kind not in POST_KINDS:
            raise InvalidInjectionIntent(
                f"post_kind must be one of {POST_KINDS}")
        if not isinstance(intent.publish_at, str) or \
                not intent.publish_at.strip():
            raise InvalidInjectionIntent("publish_at is required")
        if not isinstance(intent.media, list):
            raise InvalidInjectionIntent("media must be a list")
        if not isinstance(intent.hashtags, list):
            raise InvalidInjectionIntent("hashtags must be a list")
        if not isinstance(intent.mentions, list):
            raise InvalidInjectionIntent("mentions must be a list")
