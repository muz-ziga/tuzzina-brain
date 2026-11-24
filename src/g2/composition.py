"""Campaign-declared image composition profiles.

A profile is data owned by the active Image Skill: it lives under
`config.composition` in the skill document. This module only reads
and validates the declaration; it holds no campaign names, domains,
fonts, colours, or geometry.

Absent profile -> None -> every existing media path runs exactly as
before. Present profile -> the pipeline emits a cover-card media item
that carries the profile to Tuzzina's standalone composer.
"""
from __future__ import annotations

import hashlib

_MODES = ("cover-blur-card", "brand-card", "auto")


def composition_from_policy(policy: dict | None) -> dict | None:
    """Return the active image Skill's composition profile, or None.

    No profile means the campaign never enters the new path. A
    malformed profile fails loud here, before any image is attempted.
    """
    if not isinstance(policy, dict):
        return None
    skills = policy.get("skills")
    if not isinstance(skills, dict):
        return None
    name = skills.get("image")
    if not isinstance(name, str) or not name.strip():
        return None
    # Role gate: a campaign that explicitly excludes the image role can
    # never enter the composition path, even if it still names an image
    # Skill. Absent/empty roles keep the pre-roles default (all on).
    roles = policy.get("roles")
    if isinstance(roles, list) and roles and "image" not in roles:
        return None
    from skills.loader import get_skill

    doc = get_skill("image", name.strip())
    config = doc.get("config") if isinstance(doc, dict) else None
    if not isinstance(config, dict):
        return None
    comp = config.get("composition")
    if comp is None:
        return None
    if not isinstance(comp, dict):
        raise ValueError("image skill composition must be a mapping")
    mode = str(comp.get("mode") or "").strip()
    if mode not in _MODES:
        raise ValueError(
            "image skill composition.mode is not supported: %s" % mode
        )
    hosts = comp.get("allowedHosts")
    if not isinstance(hosts, list) or not [
        h for h in hosts if isinstance(h, str) and h.strip()
    ]:
        raise ValueError(
            "image skill composition.allowedHosts is required"
        )
    pool = comp.get("source_pool")
    if pool is not None and (
        not isinstance(pool, list)
        or not [u for u in pool if isinstance(u, str) and u.strip()]
    ):
        raise ValueError(
            "image skill composition.source_pool must be a non-empty list "
            "when present"
        )
    catalogue = comp.get("catalogue")
    if catalogue is not None and not isinstance(catalogue, list):
        raise ValueError("image skill composition.catalogue must be a list")
    brand = comp.get("brand")
    if brand is not None and not isinstance(brand, dict):
        raise ValueError("image skill composition.brand must be a mapping")
    if isinstance(brand, dict):
        # Brand-card display contract (generic, campaign data only):
        # pattern selects full-bleed geometry, wordmark/category/footer
        # are free text blocks, variants pick them by headline keywords,
        # brandMentions derive the excerpt from the headline. Types are
        # validated here; values belong to the Skill.
        if brand.get("pattern") is not None and not isinstance(
            brand.get("pattern"), str
        ):
            raise ValueError("image skill composition.brand.pattern must be a string")
        for key in ("wordmark", "category", "footer", "excerpt"):
            if brand.get(key) is not None and not isinstance(
                brand.get(key), dict
            ):
                raise ValueError(
                    "image skill composition.brand.%s must be a mapping" % key
                )
        if brand.get("variants") is not None and not isinstance(
            brand.get("variants"), list
        ):
            raise ValueError("image skill composition.brand.variants must be a list")
        if brand.get("brandMentions") is not None and not isinstance(
            brand.get("brandMentions"), list
        ):
            raise ValueError(
                "image skill composition.brand.brandMentions must be a list"
            )
    return dict(comp)
