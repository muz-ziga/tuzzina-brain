"""Unified Skill loading (Phase: skills core, unified).

A Skill is file-owned expertise, not code: one YAML document per
skill carrying id, name, type, version, enabled flag,
instructions, and a free-form config mapping. The global default
for a stage lives in ``<type>.yaml``; reusable named skills for
the same stage live beside it as ``<type>.<name>.yaml``
(name = ``[a-z0-9-]{1,64}``).

There is exactly ONE resolution path: ``get_skill(type, name)``.
A Campaign or Channel assigns a skill per stage by NAME
(``skills: {research: juzzir, ...}``); an empty assignment means
the global default. Campaigns and Channels never carry skill
CONTENT (no embedded instruction strings): the loader is the
only reader, stages never branch, and editing a skill file
changes the next fresh run (runs are short-lived; the cache is
per process).

Storage is deliberately files, not a database: Brain owns no
DB (see AGENTS.md). History lives in git; the loader reports
id+version+source so every run records exactly which skill
produced it (``source`` is ``assigned`` for a named selection,
``default`` for the global file).

Contract (fail-closed, mirrors strategy_store):
- required keys: id, name, type, version (int >= 1),
  instructions (non-empty string);
- optional: enabled (bool, default true), config (mapping,
  default {});
- the document's ``type`` must equal the requested type;
- unknown top-level keys are rejected (typos fail loud);
- assignment names must match the slug pattern; a missing,
  disabled, or invalid document raises SkillError: stages never
  silently run skill-less, and a malformed assignment fails
  closed instead of falling back.
"""
from __future__ import annotations
import os
import re

try:
    import yaml
except Exception:  # pragma: no cover - runtime always has PyYAML
    yaml = None

SKILL_TYPES = ("research", "analysis", "text", "image", "video")

SKILL_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")

_REQUIRED = ("id", "name", "type", "version", "instructions")
_OPTIONAL = ("enabled", "config")


class SkillError(ValueError):
    """Skill misuse: missing/disabled/invalid skill document or
    malformed assignment. A ValueError so misconfiguration exits
    non-retryable (exit 2), never burning inference budget."""


def skills_dir() -> str:
    """Directory holding skill files. Tests point
    BRAIN_SKILLS_DIR at fixtures; production uses the shipped dir."""
    override = os.environ.get("BRAIN_SKILLS_DIR", "").strip()
    if override:
        return override
    return os.path.join(os.path.dirname(__file__))


def valid_skill_name(value) -> bool:
    """Assignment reference shape: slug, never content, never a path."""
    return isinstance(value, str) and bool(SKILL_NAME_RE.match(value))


def _file_for(skill_type: str, name=None) -> str:
    if not name:
        return os.path.join(skills_dir(), skill_type + ".yaml")
    if not valid_skill_name(name):
        raise SkillError(f"invalid-skill:{skill_type}:bad-assignment")
    # Name is slug-restricted above, so this cannot escape the dir.
    return os.path.join(skills_dir(), "%s.%s.yaml" % (skill_type, name))


_cache: dict = {}


def _load_document(skill_type: str, name=None) -> dict:
    if yaml is None:
        raise SkillError("pyyaml-unavailable")
    ref = name or ""
    path = _file_for(skill_type, name)
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except FileNotFoundError:
        raise SkillError(f"missing-skill:{skill_type}:{ref or 'default'}")
    except SkillError:
        raise
    except Exception:
        raise SkillError(f"unreadable-skill:{skill_type}:{ref or 'default'}")
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


def get_skill(skill_type: str, assignment=None) -> dict:
    """The single Skill resolution path. ``assignment`` is a skill
    NAME (or empty for the global default), never skill content.
    Returns the validated document plus ``source`` (``assigned``
    vs ``default``) so runs trace the actual selected skill."""
    if skill_type not in SKILL_TYPES:
        raise SkillError(f"unknown-skill-type:{skill_type}")
    name = ""
    if assignment is not None and assignment != "":
        if not valid_skill_name(assignment):
            raise SkillError(
                f"invalid-skill:{skill_type}:bad-assignment")
        name = assignment
    key = (skills_dir(), skill_type, name)
    if key not in _cache:
        skill = _load_document(skill_type, name or None)
        if not skill["enabled"]:
            raise SkillError(
                f"disabled-skill:{skill_type}:{name or 'default'}")
        _cache[key] = skill
    return {**_cache[key], "source": "assigned" if name else "default"}


def list_skills() -> list:
    """Every installed skill: the default per type plus all named
    skills. Never raises: missing/disabled/invalid entries report
    as such (the admin surface needs the full picture)."""
    out = []
    try:
        names = sorted(os.listdir(skills_dir()))
    except OSError:
        names = []
    wanted = []
    for skill_type in SKILL_TYPES:
        wanted.append((skill_type, ""))
        for fname in names:
            prefix, ext = skill_type + ".", ".yaml"
            if fname.startswith(prefix) and fname.endswith(ext):
                ref = fname[len(prefix):-len(ext)]
                if ref and valid_skill_name(ref):
                    wanted.append((skill_type, ref))
    for skill_type, ref in wanted:
        try:
            skill = _load_document(skill_type, ref or None)
            out.append({"type": skill_type, "ref": ref,
                        "id": skill["id"], "name": skill["name"],
                        "version": skill["version"],
                        "enabled": skill["enabled"],
                        "status": "ok" if skill["enabled"]
                        else "disabled"})
        except SkillError as e:
            out.append({"type": skill_type, "ref": ref, "id": "",
                        "name": "", "version": 0, "enabled": False,
                        "status": str(e)})
    return out


def clear_cache() -> None:
    """Test seam: drop cached documents (env switching)."""
    _cache.clear()
