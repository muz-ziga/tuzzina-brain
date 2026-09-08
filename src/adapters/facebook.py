"""FacebookAdapter. All values proven from pinned Postiz v2.23.0.
Each capability cites its source file:line. Nothing here redefines
the FacebookDto; Tuzzina validates the final payload."""
from __future__ import annotations
from adapters.base import PlatformAdapter


class FacebookAdapter(PlatformAdapter):
    identifier = "facebook"

    def capabilities(self) -> dict:
        return {
            # facebook.provider.ts:42-44
            "max_length": 63206,
            # facebook.dto.ts:101-103
            "post_types": ["post", "story"],
            # facebook.provider.ts:27 (+513-800 impl)
            "media": {
                "text_only_allowed": True,
                "photos": True,
                "video_mp4_to_reel": True,
                "story_needs_attachment": True,
            },
            # no mentionFormat in facebook.provider.ts -> inline text
            "hashtags": "inline",
            "mentions": "inline",
            # facebook.provider.ts:851-852 (settings.url -> link)
            "links": "attach_allowed",
            # facebook.provider.ts:804-825 (URL-based, Meta downloads)
            "media_delivery": "url",
        }

    def shape_text(self, text: str, hashtags: list) -> str:
        body = (text or "").strip()
        if hashtags:
            body = f"{body}\n\n{' '.join(hashtags)}".strip()
        return body[:63206]

    def shape_media(self, media: list) -> list:
        # Facebook accepts text-only, photos, or one video.
        return list(media or [])

    def apply_link(self, content: str, url: str, policy: str,
                   cta_style: str) -> str:
        if policy == "attach" and url:
            return f"{content}\n\n{url}".strip()
        if policy == "cta":
            cta = (cta_style or "Learn more").strip()
            return f"{content}\n\n{cta}".strip()
        return content

    def default_settings(self) -> dict:
        return {"__type": "facebook", "post_type": "post"}
