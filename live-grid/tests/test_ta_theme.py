"""The client sends its live theme; the server honours it.

bdobb has always sent `theme=` on chart requests (dataClient appends it from the
settings store). live-grid ignored it and hardcoded plotly_dark, so a light-mode
card got a dark chart pasted into it.
"""
import pytest

from app.figure import build_figure
from app.ta.figure import GUIDE, THEMES, build_ta_figure, pick_colour, resolve_theme
from app.ta.panes import assign
from app.ta.registry import _LIGHT, REGISTRY, resolve
from tests.ta_helpers import fixture_frame


class TestResolveTheme:
    @pytest.mark.parametrize("given,expected", [
        ("dark", "dark"), ("light", "light"),
        (None, "dark"), ("", "dark"), ("solarized", "dark"), ("DARK", "dark"),
    ])
    def test_unknown_or_absent_falls_back_to_dark(self, given, expected):
        """Permissive on purpose: a chart is presentation, so an unrecognised
        theme should yield a readable chart rather than an error page."""
        assert resolve_theme(given) == expected


class TestPalette:
    def test_every_registry_colour_has_a_light_counterpart(self):
        """_themed raises KeyError on an unmapped colour, which would only show
        up at request time. This turns that into a test failure instead: adding
        an indicator with a new colour fails here, not in someone's chart."""
        used = {c["dark"] for i in REGISTRY.values() for r in i.render.values()
                if isinstance((c := r.get("color")), dict)}
        assert used, "no themed colours found -- the render shape changed"
        assert used <= set(_LIGHT), f"unmapped: {sorted(used - set(_LIGHT))}"

    def test_light_and_dark_are_never_the_same_colour(self):
        """A pair that did not actually change is a copy-paste, not a mapping."""
        assert all(dark != light for dark, light in _LIGHT.items())

    def test_pick_colour_resolves_by_theme_and_passes_strings_through(self):
        assert pick_colour({"dark": "#111111", "light": "#eeeeee"}, "light") == "#eeeeee"
        assert pick_colour({"dark": "#111111", "light": "#eeeeee"}, "dark") == "#111111"
        assert pick_colour("#abcdef", "light") == "#abcdef"   # hand-built entry
        assert pick_colour(None, "light") is None

    def test_a_dark_only_entry_falls_back_rather_than_vanishing(self):
        """A colour dict missing `light` must not render an invisible trace."""
        assert pick_colour({"dark": "#111111"}, "light") == "#111111"


class TestFigureHonoursTheme:
    def _panes(self):
        frame = fixture_frame()
        return frame, assign(None, [resolve("rsi"), resolve("sma")])

    def test_the_template_follows_the_theme(self):
        frame, panes = self._panes()
        assert build_ta_figure("T", frame, panes, theme="light")["layout"]["template"] \
            == THEMES["light"]
        assert build_ta_figure("T", frame, panes, theme="dark")["layout"]["template"] \
            == THEMES["dark"]

    def test_omitting_the_theme_keeps_the_long_standing_dark_default(self):
        frame, panes = self._panes()
        assert build_ta_figure("T", frame, panes)["layout"]["template"] == "plotly_dark"

    def test_trace_colours_differ_between_themes(self):
        """The whole point: a template swap alone would leave dark-tuned lines
        on a white card."""
        frame, panes = self._panes()
        dark = build_ta_figure("T", frame, panes, theme="dark")
        light = build_ta_figure("T", frame, panes, theme="light")

        def colours(fig):
            out = []
            for t in fig["data"]:
                c = (t.get("line") or {}).get("color") or (t.get("marker") or {}).get("color")
                if c:
                    out.append(c)
            return out

        assert colours(dark) and colours(dark) != colours(light)
        assert all(isinstance(c, str) for c in colours(light)), "unresolved dict leaked"

    def test_guide_lines_follow_the_theme(self):
        frame, panes = self._panes()
        light = build_ta_figure("T", frame, panes, theme="light")
        shapes = light["layout"]["shapes"]
        assert shapes, "rsi should draw its 30/70 guides"
        assert all(s["line"]["color"] == GUIDE["light"] for s in shapes)

    def test_the_simple_live_chart_honours_it_too(self):
        """Episode 11's Plotly chart, the other builder."""
        bars = [{"date": "2026-01-01", "open": 1.0, "high": 2.0, "low": 0.5,
                 "close": 1.5, "adj_close": 1.5, "volume": 10.0}]
        assert build_figure("T", bars, theme="light")["layout"]["template"] == "plotly_white"
        assert build_figure("T", bars)["layout"]["template"] == "plotly_dark"
