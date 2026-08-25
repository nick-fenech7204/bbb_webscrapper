from bbb_scraper.parsing.json_extract import (
    extract_json_scripts,
    extract_window_assignment,
)


def test_extract_json_scripts_finds_application_json_blocks():
    html = """
    <html><body>
      <script type="application/json">{"results": [{"a": 1}]}</script>
      <script type="application/ld+json">{"@type": "Organization"}</script>
    </body></html>
    """
    blobs = extract_json_scripts(html, content_type="application/json")
    assert len(blobs) == 1
    assert blobs[0]["results"][0]["a"] == 1


def test_extract_json_scripts_skips_invalid_json_without_raising():
    html = """
    <script type="application/json">{not valid json}</script>
    <script type="application/json">{"ok": true}</script>
    """
    blobs = extract_json_scripts(html, content_type="application/json")
    assert blobs == [{"ok": True}]


def test_extract_window_assignment_handles_nested_braces_and_escaped_quotes():
    html = (
        '<script>window.__STATE__ = {"a": {"b": 1}, '
        '"text": "has {braces} and \\"quotes\\" inside"};</script>'
    )
    data = extract_window_assignment(html, "__STATE__")
    assert data["a"]["b"] == 1
    assert data["text"] == 'has {braces} and "quotes" inside'


def test_extract_window_assignment_returns_none_when_absent():
    assert extract_window_assignment("<html></html>", "__MISSING__") is None
