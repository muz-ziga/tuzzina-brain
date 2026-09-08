"""InstagramAdapter: pure translator from CanonicalIntent to the
Tuzzina request shape. No provider truth lives here: lengths, post
types, and media validity are owned and validated by Tuzzina."""
from __future__ import annotations
from adapters.base import PlatformAdapter


class InstagramAdapter(PlatformAdapter):
    identifier = "instagram"

    def shape_text(self, text: str, hashtags: list) -> str:
        body = (text or "").strip()
        if hashtags:
            body = f"{body}\n\n{' '.join(hashtags)}".strip()
        return body

    def shape_media(self, media: list) -> list:
        # Media refs pass through; Tuzzina validates count/rules.
        return list(media or [])

    def apply_link(self, content: str, url: str, policy: str,
                   cta_style: str) -> str:
        # Instagram posts have no link field; attach is meaningless.
        # 'cta' still appends call-to-action text (no URL).
        if policy == "cta":
            cta = (cta_style or "Learn more").strip()
            return f"{content}\n\n{cta}".strip()
        return content

    def link_setting(self, policy: str, link: str) -> dict:
        # Instagram has no link field; attach is meaningless here.
        return {}

    def default_settings(self) -> dict:
        return {"__type": "instagram", "post_type": "post"}
