"""FacebookAdapter: pure translator from CanonicalIntent to the
Tuzzina request shape. No provider truth lives here: lengths, post
types, and media validity are owned and validated by Tuzzina."""
from __future__ import annotations
from adapters.base import PlatformAdapter


class FacebookAdapter(PlatformAdapter):
    identifier = "facebook"

    def shape_text(self, text: str, hashtags: list) -> str:
        body = (text or "").strip()
        if hashtags:
            body = f"{body}\n\n{' '.join(hashtags)}".strip()
        return body

    def shape_media(self, media: list) -> list:
        # Facebook accepts text-only, photos, or video; Tuzzina
        # validates the final refs.
        return list(media or [])

    def apply_link(self, content: str, url: str, policy: str,
                   cta_style: str) -> str:
        if policy == "attach" and url:
            return f"{content}\n\n{url}".strip()
        if policy == "cta":
            cta = (cta_style or "Learn more").strip()
            return f"{content}\n\n{cta}".strip()
        return content

    def link_setting(self, policy: str, link: str) -> dict:
        # Facebook link attach travels as settings.url.
        if (policy or "hide") == "attach" and link:
            return {"url": link}
        return {}

    def default_settings(self) -> dict:
        return {"__type": "facebook", "post_type": "post"}
