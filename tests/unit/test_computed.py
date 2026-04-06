"""Tests for mdb_engine.routing._computed transforms."""

from __future__ import annotations

from mdb_engine.routing._computed import (
    _transform_first_image,
    apply_computed_fields,
    apply_computed_fields_partial,
    parse_computed_on_write,
)

# ---------------------------------------------------------------------------
# _transform_first_image
# ---------------------------------------------------------------------------

DATA_URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg..."
REAL_URL = "https://example.com/photo.jpg"
RELATIVE_URL = "/uploads/photo.jpg"


class TestTransformFirstImageMarkdown:
    def test_returns_empty_for_empty_body(self):
        assert _transform_first_image("") == ""

    def test_returns_real_url(self):
        body = f"# Hello\n\n![alt]({REAL_URL})\n\ntext"
        assert _transform_first_image(body) == REAL_URL

    def test_skips_data_uri(self):
        body = f"![pasted]({DATA_URI})"
        assert _transform_first_image(body) == ""

    def test_skips_data_uri_returns_next_real_url(self):
        body = f"![pasted]({DATA_URI})\n\n![photo]({REAL_URL})"
        assert _transform_first_image(body) == REAL_URL

    def test_returns_relative_url(self):
        body = f"![photo]({RELATIVE_URL})"
        assert _transform_first_image(body) == RELATIVE_URL

    def test_multiple_data_uris_returns_empty(self):
        body = f"![a]({DATA_URI})\n![b]({DATA_URI})"
        assert _transform_first_image(body) == ""

    def test_multiple_data_uris_then_real(self):
        body = f"![a]({DATA_URI})\n![b]({DATA_URI})\n![c]({REAL_URL})"
        assert _transform_first_image(body) == REAL_URL


class TestTransformFirstImageHTML:
    def test_returns_real_url(self):
        body = f'<p><img src="{REAL_URL}" alt="photo"></p>'
        assert _transform_first_image(body) == REAL_URL

    def test_skips_data_uri(self):
        body = f'<img src="{DATA_URI}">'
        assert _transform_first_image(body) == ""

    def test_skips_data_uri_returns_next_real_url(self):
        body = f'<img src="{DATA_URI}"><img src="{REAL_URL}">'
        assert _transform_first_image(body) == REAL_URL


class TestTransformFirstImageMixed:
    """Markdown images are checked before HTML images."""

    def test_md_data_uri_then_html_real(self):
        body = f'![pasted]({DATA_URI})\n<img src="{REAL_URL}">'
        assert _transform_first_image(body) == REAL_URL

    def test_md_real_beats_html_real(self):
        body = f'![photo]({REAL_URL})\n<img src="https://other.com/img.png">'
        assert _transform_first_image(body) == REAL_URL


# ---------------------------------------------------------------------------
# parse_computed_on_write
# ---------------------------------------------------------------------------


class TestParseComputedOnWrite:
    def test_returns_empty_for_none(self):
        assert parse_computed_on_write(None) == {}

    def test_extracts_x_computed(self):
        schema = {
            "properties": {
                "cover_image": {
                    "type": "string",
                    "x-computed": {"from": "body", "transform": "first_image"},
                },
                "title": {"type": "string"},
            }
        }
        result = parse_computed_on_write(schema)
        assert "cover_image" in result
        assert result["cover_image"]["transform"] == "first_image"
        assert "title" not in result


# ---------------------------------------------------------------------------
# apply_computed_fields / apply_computed_fields_partial
# ---------------------------------------------------------------------------


class TestApplyComputedFields:
    COMPUTED = {
        "cover_image": {"from": "body", "transform": "first_image"},
    }

    def test_sets_cover_image_skipping_data_uri(self):
        body = {"body": f"![pasted]({DATA_URI})\n![photo]({REAL_URL})"}
        apply_computed_fields(body, self.COMPUTED)
        assert body["cover_image"] == REAL_URL

    def test_sets_empty_when_only_data_uri(self):
        body = {"body": f"![pasted]({DATA_URI})"}
        apply_computed_fields(body, self.COMPUTED)
        assert body["cover_image"] == ""

    def test_noop_when_source_missing(self):
        body = {"title": "hello"}
        apply_computed_fields(body, self.COMPUTED)
        assert "cover_image" not in body


class TestApplyComputedFieldsPartial:
    COMPUTED = {
        "cover_image": {"from": "body", "transform": "first_image"},
    }

    def test_recomputes_when_source_present(self):
        body = {"body": f"![photo]({REAL_URL})"}
        apply_computed_fields_partial(body, self.COMPUTED)
        assert body["cover_image"] == REAL_URL

    def test_skips_when_source_absent(self):
        body = {"title": "hello"}
        apply_computed_fields_partial(body, self.COMPUTED)
        assert "cover_image" not in body
