"""Generic Skill loading (Phase: skills core).

A Skill is file-owned expertise, not code: one YAML file per
skill type (``<type>.yaml``) carrying id, name, version,
enabled flag, instructions, and a free-form config mapping.
Stages resolve skills by type; the engine never hardcodes skill
content and never invents new skills at runtime.

Storage is deliberately files, not a database: Brain owns no
DB (see AGENTS.md). Versions advance by editing the file
(history lives in git); the loader reports id+version so every
run can record exactly which skill produced it.

Contract (fail-closed, mirrors strategy_store):
- required keys: id, name, type, version (int >= 1),
  instructions (non-empty string);
- optional: enabled (bool, default true), config (mapping,
  default {});
- the file's ``type`` must equal its filename stem;
- unknown top-level keys are rejected (typos fail loud);
- a missing file, a disabled skill, or an invalid document
  raises SkillError: stages never silently run skill-less.
"""
from __future__ import annotations
import os

try:
    import yaml
except Exception:  # pragma: no cover - runtime always has PyYAML
    yaml = None

SKILL_TYPES = ("research", "analysis", "text", "image", "video")

_REQUIRED = ("id", "name", "type", "version", "instructions")
_OPTIONAL = ("enabled", "config")


class SkillError(ValueError):
    """Skill misuse: missing/disabled/invalid skill document.
    A ValueError so misconfiguration exits non-retryable (exit
    2), never burning inference budget on retries."""


def skills_dir() -> str:
    """Directory holding ``<type>.yaml`` skill files. Tests point
    BRAIN_SKILLS_DIR at fixtures; production uses the shipped dir."""
    override = os.environ.get("BRAIN_SKILLS_DIR", "").strip()
    if override:
        return override
    return os.path.join(os.path.dirname(__file__))


_cache: dict = {}


def _load_document(skill_type: str) -> dict:
    if yaml is None:
        raise SkillError("pyyaml-unavailable")
    path = os.path.join(skills_dir(), skill_type + ".yaml")
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except FileNotFoundError:
        raise SkillError(f"missing-skill:{skill_type}")
    except Exception:
        raise SkillError(f"unreadable-skill:{skill_type}")
    if not isinstance(doc, dict):
        raise SkillError(f"invalid-skill:{skill_type}")
    for key in _REQUIRED:
        if key not in doc:
            raise SkillError(f"invalid-skill:{skill_type}:missing:{key}")
    for key in doc:
        if key not in _REQUIRED and key not in _OPTIONAL:
            raise SkillError(f"invalid-skill:{skill_type}:unknown:{key}")
    if not isinstance(doc["id"], str) or not doc["id"].strip():
        raise SkillError(f"invalid-skill:{skill_type}:id")
    if not isinstance(doc["name"], str) or not doc["name"].strip():
        raise SkillError(f"invalid-skill:{skill_type}:name")
    if doc["type"] != skill_type:
        raise SkillError(f"invalid-skill:{skill_type}:type-mismatch")
    if not isinstance(doc["version"], int) or doc["version"] < 1:
        raise SkillError(f"invalid-skill:{skill_type}:version")
    if not isinstance(doc["instructions"], str) or \
            not doc["instructions"].strip():
        raise SkillError(f"invalid-skill:{skill_type}:instructions")
    enabled = doc.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SkillError(f"invalid-skill:{skill_type}:enabled")
    config = doc.get("config", {})
    if not isinstance(config, dict):
        raise SkillError(f"invalid-skill:{skill_type}:config")
    return {"id": doc["id"].strip(), "name": doc["name"].strip(),
            "type": skill_type, "version": doc["version"],
            "enabled": enabled,
            "instructions": doc["instructions"].strip(),
            "config": dict(config)}


def get_skill(skill_type: str, overrides: dict | None = None) -> dict:
    """Load and validate one skill. Results are cached per
    process (same rationale as strategy files: runs are
    short-lived; tests clear the cache through the env).

    Campaign override: when the current campaign carries its own
    instructions for this type, they win over the global file.
    The returned shape is identical (plus ``source``), so stages
    need no branching. Trace identity comes from the slot's
    global id/version; ``source`` says which content ran.
    """
    if skill_type not in SKILL_TYPES:
        raise SkillError(f"unknown-skill-type:{skill_type}")
    if isinstance(overrides, dict):
        content = overrides.get(skill_type)
        if isinstance(content, str) and content.strip():
            if len(content) > 8000:
                raise SkillError(
                    f"invalid-skill:{skill_type}:campaign-too-long")
            base = _load_document(skill_type)
            if not base["enabled"]:
                raise SkillError(f"disabled-skill:{skill_type}")
            return {"id": base["id"], "name": base["name"],
                    "type": skill_type, "version": base["version"],
                    "enabled": True,
                    "instructions": content.strip(),
                    "config": dict(base["config"]),
                    "source": "campaign"}
    key = (skills_dir(), skill_type)
    if key not in _cache:
        _cache[key] = _load_document(skill_type)
    skill = _cache[key]
    if not skill["enabled"]:
        raise SkillError(f"disabled-skill:{skill_type}")
    return {**skill, "source": "file"}


def list_skills() -> list:
    """All four skill slots with their load status. Never raises:
    missing/disabled/invalid entries report as such (the admin
    surface needs the full picture, not just the healthy row)."""
    out = []
    for skill_type in SKILL_TYPES:
        try:
            skill = _load_document(skill_type)
            out.append({"type": skill_type, "id": skill["id"],
                        "name": skill["name"],
                        "version": skill["version"],
                        "enabled": skill["enabled"],
                        "status": "ok" if skill["enabled"]
                        else "disabled"})
        except SkillError as e:
            out.append({"type": skill_type, "id": "", "name": "",
                        "version": 0, "enabled": False,
                        "status": str(e)})
    return out


def clear_cache() -> None:
    """Test seam: drop cached documents (env switching)."""
    _cache.clear()
