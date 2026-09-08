"""InstagramAdapter. All values proven from pinned Postiz v2.23.0.
Each capability cites its source file:line. Nothing here redefines
the InstagramDto; Tuzzina validates the final payload."""
from __future__ import annotations
from adapters.base import PlatformAdapter, UnsupportedPlatform


class InstagramAdapter(PlatformAdapter):
    identifier = "instagram"

    def capabilities(self) -> dict:
        return {
            # instagram.provider.ts:46-48
            "max_length": 2200,
            # instagram.dto.ts:52-54 (REQUIRED, not optional)
            "post_types": ["post", "story"],
            "post_type_required": True,
            # instagram.provider.ts:50-86 (checkValidity)
            "media": {
                "required": True,
                "min": 1,
                "carousel_min": 2,
                "carousel_max": 10,
                "story_single_picture": True,
                "trial_reel_single_video": True,
            },
            # caption only sent with single media
            # (instagram.provider.ts:661-664)
            "caption_single_media_only": True,
            # no mentionFormat in instagram.provider.ts -> inline text
            "hashtags": "inline",
            "mentions": "inline",
            # no url field in InstagramDto -> links unsupported
            "links": "unsupported",
            # instagram.provider.ts:672-683 (image_url=/video_url=)
            "media_delivery": "url",
        }

    def shape_text(self, text: str, hashtags: list) -> str:
        body = (text or "").strip()
        if hashtags:
            body = f"{body}\n\n{' '.join(hashtags)}".strip()
        return body[:2200]

    def shape_media(self, media: list) -> list:
        # Media required; carousel capped at 10.
        items = list(media or [])
        if not items:
            raise UnsupportedPlatform(
                "instagram requires at least one media item")
        return items[:10]

    def apply_link(self, content: str, url: str, policy: str,
                   cta_style: str) -> str:
        # Instagram posts have no link field; attach is meaningless.
        # 'cta' still appends call-to-action text (no URL).
        if policy == "cta":
            cta = (cta_style or "Learn more").strip()
            return f"{content}\n\n{cta}".strip()
        return content

    def default_settings(self) -> dict:
        return {"__type": "instagram", "post_type": "post"}
