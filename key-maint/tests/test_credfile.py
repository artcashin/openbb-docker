"""Parser must replicate docker compose v2.29 dotenv behavior as observed on the NAS
(2026-08-04 incident): leading whitespace after '=' is trimmed; an inline
' # comment' after a NON-empty value is stripped; a line whose value is only
whitespace followed by '#...' yields the '#...' text AS the value (the hazard
the repo convention now forbids, but the parser must still mirror compose)."""
from app.credfile import load, load_with_warnings, parse_text


class TestParseText:
    def test_plain_pair(self):
        assert parse_text("FMP_API_KEY=abc123\n") == {"FMP_API_KEY": "abc123"}

    def test_empty_value(self):
        assert parse_text("FMP_API_KEY=\n") == {"FMP_API_KEY": ""}

    def test_full_line_comment_and_blank_skipped(self):
        assert parse_text("# a comment\n\nA=1\n") == {"A": "1"}

    def test_inline_comment_after_value_stripped(self):
        assert parse_text("EODHD_API_KEY=tok123  # eodhd.com token\n") == {
            "EODHD_API_KEY": "tok123"
        }

    def test_empty_value_with_inline_comment_becomes_value(self):
        # The compose hazard: comment text leaks in as the value.
        out = parse_text("EIA_API_KEY=                 # eia.gov/opendata\n")
        assert out == {"EIA_API_KEY": "# eia.gov/opendata"}

    def test_leading_whitespace_trimmed_trailing_too(self):
        assert parse_text("A=  v1  \n") == {"A": "v1"}

    def test_malformed_line_skipped(self):
        assert parse_text("NOEQUALSSIGN\nA=1\n") == {"A": "1"}

    def test_last_duplicate_wins(self):
        assert parse_text("A=1\nA=2\n") == {"A": "2"}


class TestLoad:
    def test_missing_file_returns_none(self, tmp_path):
        assert load(str(tmp_path / "nope.env")) is None

    def test_reads_file(self, tmp_path):
        p = tmp_path / "c.env"
        p.write_text("A=1\n")
        assert load(str(p)) == {"A": "1"}


class TestLoadWithWarnings:
    def test_malformed_line_reported_and_rest_parsed(self, tmp_path):
        p = tmp_path / "c.env"
        p.write_text("A=1\nNOEQUALSSIGN\nB=2\n")
        values, warnings = load_with_warnings(str(p))
        assert values == {"A": "1", "B": "2"}
        assert warnings == ["line 2"]
