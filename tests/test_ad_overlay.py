"""Per-campaign composed-ad overlay.

A campaign owns the layer that goes ON TOP of its generated
backgrounds: its logo asset, its scrim color, where its headline sits.
The dashboard writes `ad` into the campaign document; the engine
translates it into the compositor's own layout shape.
"""
import sys
import unittest

sys.path.insert(0, "src")

from g2.generators import (  # noqa: E402
    AD_LAYOUT,
    AD_TEMPLATES,
    AD_TEXT_ZONES,
    campaign_ad_overlay,
)
from stage_run import _merge_layout  # noqa: E402


class CampaignAdOverlayTest(unittest.TestCase):
    def test_campaign_document_carries_ad_through_the_loader(self):
        # Tuzzina writes `ad` into the campaign YAML; if the loader
        # drops it, stage_run reads None and every campaign silently
        # falls back to the default overlay.
        import tempfile
        import os
        from campaign import load_campaign

        doc = (
            "brand:\n"
            "  name: Acme\n"
            "ad:\n"
            "  scrimColor: '#ff0000'\n"
            "  logo: identity/logos/acme.png\n"
            "  logoBacking: none\n"
        )
        fd, path = tempfile.mkstemp(suffix=".yaml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(doc)
            cfg = load_campaign(path)
        finally:
            os.unlink(path)
        self.assertEqual(cfg["ad"], {
            "scrimColor": "#ff0000",
            "logo": "identity/logos/acme.png",
            "logoBacking": "none",
        })
        # And it is what the engine translates.
        out = campaign_ad_overlay(cfg["ad"])
        self.assertEqual(out["layout"]["scrim"]["color"], "#ff0000")
        self.assertEqual(out["logo"], "identity/logos/acme.png")
        self.assertEqual(out["layout"]["logoBacking"], "none")

    def test_absent_ad_resolves_to_the_default_layout(self):
        # The whole point of the change: a campaign that configures
        # nothing must ship exactly AD_LAYOUT, so every image it made
        # before is byte-identical to the ones it makes now.
        for empty in ({}, None, "not-a-mapping", []):
            out = campaign_ad_overlay(empty)
            self.assertEqual(out["layout"], {})
            self.assertEqual(out["logo"], "")
        merged = _merge_layout(dict(AD_LAYOUT), {})
        self.assertEqual(merged, dict(AD_LAYOUT))

    def test_scrim_color_is_the_only_zone_a_campaign_touches(self):
        out = campaign_ad_overlay({"scrimColor": "#FF0000"})
        self.assertEqual(out["layout"], {"scrim": {"color": "#ff0000"}})
        # One level deep: the sibling zones must survive the override.
        merged = _merge_layout(dict(AD_LAYOUT), out["layout"])
        self.assertEqual(merged["scrim"]["color"], "#ff0000")
        self.assertEqual(merged["scrim"]["opacity"], AD_LAYOUT["scrim"]["opacity"])
        self.assertEqual(merged["text"], dict(AD_LAYOUT["text"]))

    def test_text_zone_position_selects_a_whole_box(self):
        out = campaign_ad_overlay({"textZone": {"position": "top"}})
        self.assertEqual(out["layout"]["text"], dict(AD_TEXT_ZONES["top"]))
        self.assertEqual(out["layout"]["text"]["y"],
                         AD_TEXT_ZONES["top"]["y"])

    def test_template_carries_its_zone_and_backing(self):
        out = campaign_ad_overlay({"template": "bare"})
        self.assertEqual(out["layout"]["logoBacking"],
                         AD_TEMPLATES["bare"]["logoBacking"])
        self.assertEqual(out["layout"]["text"],
                         dict(AD_TEXT_ZONES[AD_TEMPLATES["bare"]["textZone"]]))

    def test_explicit_text_zone_wins_over_the_template(self):
        # A saved manual choice is never overwritten by the preset it
        # was picked from.
        out = campaign_ad_overlay({
            "template": "default",
            "textZone": {"position": "center"},
        })
        self.assertEqual(out["layout"]["text"],
                         dict(AD_TEXT_ZONES["center"]))

    def test_logo_key_is_returned_separately_from_the_layout(self):
        # The logo is an art-direction decision (which asset is pasted),
        # not a geometry zone.
        out = campaign_ad_overlay({"logo": "identity/logos/acme.png"})
        self.assertEqual(out["logo"], "identity/logos/acme.png")
        self.assertNotIn("logo", out["layout"])

    def test_logo_none_is_forwarded_as_an_explicit_opt_out(self):
        # "none" must reach the compositor unchanged: an absent logo
        # means "organization default", while "none" means "paste
        # nothing". Blanking it here silently restores the default logo.
        out = campaign_ad_overlay({"logo": "none"})
        self.assertEqual(out["logo"], "none")

    def test_absent_logo_is_empty_so_the_default_applies(self):
        out = campaign_ad_overlay({"scrimColor": "#123456"})
        self.assertEqual(out["logo"], "")

    def test_explicit_logo_backing_wins_over_the_template(self):
        # The same precedence as the text zone: a saved manual choice
        # survives the preset it was picked from.
        out = campaign_ad_overlay({
            "template": "clean",
            "logoBacking": "none",
        })
        self.assertEqual(out["layout"]["logoBacking"], "none")

    def test_unknown_logo_backing_fails_closed(self):
        with self.assertRaises(ValueError):
            campaign_ad_overlay({"logoBacking": "sparkle"})

    def test_unknown_template_and_zone_fail_closed(self):
        with self.assertRaises(ValueError):
            campaign_ad_overlay({"template": "no-such-template"})
        with self.assertRaises(ValueError):
            campaign_ad_overlay({"textZone": {"position": "diagonal"}})


if __name__ == "__main__":
    unittest.main()