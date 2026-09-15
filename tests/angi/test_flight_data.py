from bbb_scraper.angi.flight_data import reassemble


def test_reassembles_single_push_chunk():
    html = '<script>self.__next_f.push([1,"1:[\\"hello\\",\\"world\\"]"])</script>'
    assert reassemble(html) == '1:["hello","world"]'


def test_concatenates_multiple_chunks_in_order():
    html = (
        '<script>self.__next_f.push([1,"a"])</script>'
        '<script>self.__next_f.push([1,"b"])</script>'
        '<script>self.__next_f.push([1,"c"])</script>'
    )
    assert reassemble(html) == "abc"


def test_ignores_unrelated_script_tags():
    html = (
        '<script>console.log("not a flight chunk")</script>'
        '<script>self.__next_f.push([1,"real"])</script>'
    )
    assert reassemble(html) == "real"


def test_no_push_chunks_returns_empty_string():
    assert reassemble("<html><body>nothing here</body></html>") == ""


def test_malformed_chunk_is_skipped_not_fatal():
    html = (
        '<script>self.__next_f.push([1,"good"])</script>'
        # A push whose argument isn't valid JSON (shouldn't happen from a
        # real page, but a truncated/malformed response is possible) --
        # must not raise, just contribute nothing.
        '<script>self.__next_f.push([1,"unterminated])</script>'
    )
    assert reassemble(html) == "good"


def test_unescapes_json_string_content():
    html = '<script>self.__next_f.push([1,"line one\\nline \\"two\\""])</script>'
    assert reassemble(html) == 'line one\nline "two"'
