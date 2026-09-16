"""OllamaClient -- mocked HTTP, no real network, no real Ollama server
required to run these. Never-fatal contract mirrors
bbb_scraper.webcheck.check_website: any failure returns None, never
raises."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from curl_cffi import requests as curl_requests

from bbb_scraper.config import Settings
from bbb_scraper.sentiment.client import OllamaClient, is_available


def _cfg(**overrides) -> Settings:
    base = {
        "ollama_base_url": "http://localhost:11434",
        "ollama_model": "llama3.2:latest",
        "ollama_timeout_seconds": 5.0,
        "ollama_max_retries": 2,
    }
    base.update(overrides)
    return Settings(**base)


def _ollama_response(inner_json: dict) -> MagicMock:
    """Ollama's own /api/generate envelope -- the actual model output is a
    JSON *string* inside the "response" field, not the top-level body."""
    import json as _json
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"response": _json.dumps(inner_json)}
    resp.text = _json.dumps({"response": _json.dumps(inner_json)})
    return resp


@patch("bbb_scraper.sentiment.client.curl_requests.Session")
def test_analyze_review_returns_the_real_shape_on_a_good_response(mock_session_cls):
    mock_session_cls.return_value.post.return_value = _ollama_response({
        "sentiment": "negative", "severity": 5, "theme": "quality of work",
        "actionable_for_pitch": True, "summary": "Poor quality of work.",
    })

    client = OllamaClient(_cfg())
    result = client.analyze_review("terrible inspection, missed everything", rating=1.0)

    assert result == {
        "sentiment": "negative", "severity": 5, "theme": "quality of work",
        "actionable_for_pitch": True, "summary": "Poor quality of work.",
    }


@patch("bbb_scraper.sentiment.client.curl_requests.Session")
def test_analyze_review_empty_text_returns_none_without_a_request(mock_session_cls):
    client = OllamaClient(_cfg())
    assert client.analyze_review("") is None
    assert client.analyze_review("   ") is None
    mock_session_cls.return_value.post.assert_not_called()


@patch("bbb_scraper.sentiment.client.curl_requests.Session")
def test_analyze_review_connection_failure_returns_none_not_raises(mock_session_cls):
    mock_session_cls.return_value.post.side_effect = curl_requests.exceptions.RequestException(
        "Ollama not running"
    )
    client = OllamaClient(_cfg())
    assert client.analyze_review("some review text") is None


@patch("bbb_scraper.sentiment.client.curl_requests.Session")
def test_analyze_review_malformed_json_in_response_field_returns_none(mock_session_cls):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"response": "this is not json"}
    resp.text = "this is not json"
    mock_session_cls.return_value.post.return_value = resp

    client = OllamaClient(_cfg())
    assert client.analyze_review("some review text") is None


@patch("bbb_scraper.sentiment.client.curl_requests.Session")
def test_analyze_review_invalid_sentiment_value_is_dropped_entirely(mock_session_cls):
    """sentiment is load-bearing for every downstream signal -- if the
    model returns something outside the 4 real values, the whole result
    is discarded rather than passed through with a garbage sentiment."""
    mock_session_cls.return_value.post.return_value = _ollama_response({
        "sentiment": "somewhat annoyed", "severity": 3, "theme": "other",
        "actionable_for_pitch": False, "summary": "meh",
    })

    client = OllamaClient(_cfg())
    assert client.analyze_review("some review text") is None


@patch("bbb_scraper.sentiment.client.curl_requests.Session")
def test_analyze_review_severity_out_of_range_is_clamped(mock_session_cls):
    mock_session_cls.return_value.post.return_value = _ollama_response({
        "sentiment": "negative", "severity": 11, "theme": "other",
        "actionable_for_pitch": True, "summary": "bad",
    })

    client = OllamaClient(_cfg())
    result = client.analyze_review("some review text")

    assert result["severity"] == 5


@patch("bbb_scraper.sentiment.client.curl_requests.Session")
def test_analyze_review_missing_optional_fields_still_returns_a_result(mock_session_cls):
    """Only sentiment is truly required -- a model that leaves out theme/
    summary/severity shouldn't lose the sentiment classification itself."""
    mock_session_cls.return_value.post.return_value = _ollama_response({"sentiment": "positive"})

    client = OllamaClient(_cfg())
    result = client.analyze_review("great work, very happy")

    assert result == {
        "sentiment": "positive", "severity": None, "theme": None,
        "actionable_for_pitch": None, "summary": None,
    }


@patch("bbb_scraper.sentiment.client.curl_requests.get")
def test_is_available_true_on_a_real_200(mock_get):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    mock_get.return_value = resp

    assert is_available(_cfg()) is True


@patch("bbb_scraper.sentiment.client.curl_requests.get")
def test_is_available_false_when_unreachable(mock_get):
    mock_get.side_effect = curl_requests.exceptions.RequestException("connection refused")

    assert is_available(_cfg()) is False
