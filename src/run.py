"""run.py: campaign -> G1 -> G2 -> upload -> G3 -> Tuzzina drafts.
Usage: TUZZINA_API_KEY=... python3 src/run.py campaigns/<name>.yaml [--mock|--openai]
Default generators: mock (no keys needed). --openai needs OPENAI_API_KEY.
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from campaign import load_campaign
from contracts import ContentPackage
from g1.website import WebsiteAdapter
from g2 import generators as G
from g2.pipeline import build_package
from g3.planner import plan
from tuzzina.client import TuzzinaClient


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: run.py campaigns/<name>.yaml [--mock|--openai]")
        return 2
    cfg = load_campaign(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else "--mock"
    oai = os.environ.get("OPENAI_API_KEY", "")
    if mode == "--openai":
        if not oai:
            print("OPENAI_API_KEY is not set; use --mock")
            return 2
        text_gen = G.OpenAITextGenerator(oai)
        image_gen_obj = None  # images via extracted URLs in this slice
    else:
        text_gen = G.MockTextGenerator()
        image_gen_obj = G.MockImageGenerator()

    adapters = {"website": WebsiteAdapter()}
    packages: list[ContentPackage] = []
    for src in cfg["sources"]:
        res = adapters[src["type"]].extract(src["url"], src["n"])
        print(f"G1 {src['type']} {src['url']}: "
              f"{res.meta.get('returned')}/{res.meta.get('requested')} "
              f"(partial={res.meta.get('partial')})")
        for item in res.items:
            packages.append(build_package(
                item, cfg["brand"], cfg["language"], cfg["hashtags"],
                cfg["links_policy"], text_gen, image_gen_obj))
    if not packages:
        print("no content extracted; nothing to do")
        return 1
    planned = plan(packages, cfg["schedule"])
    print(f"G3 planned {len(planned)} posts")

    client = TuzzinaClient(
        os.environ.get("TUZZINA_API_URL", "http://127.0.0.1:4107/api"),
        os.environ.get("TUZZINA_API_KEY", ""))
    integ = client.find_integration(
        "facebook", os.environ.get("TUZZINA_CHANNEL_MATCH", ""))
    print(f"channel: {integ.get('name')} ({integ.get('id')})")

    posts_payload = []
    for p in planned:
        images = []
        for m in p.media:
            if m.get("kind") == "url":
                up = client.upload_from_url(m["url"])
            else:
                gen = m["generator"]
                blob, fname = gen.generate_png(m["prompt"])
                up = client.upload_bytes(blob, fname, "image/png")
            images.append({"id": up["id"], "path": up["path"]})
            print(f"media -> {up['path'][:80]}")
        posts_payload.append({
            "integration": {"id": integ["id"]},
            "value": [{"content": p.content, "image": images}],
            "settings": p.settings,
        })
    date = planned[0].planned_at
    out = client.create_draft(posts_payload, date)
    print("DRAFTS CREATED:")
    print(json.dumps(out, ensure_ascii=False)[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
