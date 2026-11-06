"""An update check must never interrupt or crash the app: a bad URL, no internet, a private repo
(403) or a malformed manifest all have to look exactly like "no update available", and a genuinely
newer version must be detected correctly even when the version strings have different lengths."""

from __future__ import annotations

import json
from unittest.mock import patch

from pdf2word.update_check import UpdateInfo, _parse_version, check_for_update


def _fake_response(payload: dict):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    return _Resp()


def test_parse_version_handles_different_lengths():
    assert _parse_version("0.4.0") < _parse_version("0.4.1")
    assert _parse_version("0.4") < _parse_version("0.4.0.1")
    assert _parse_version("1.0.0") > _parse_version("0.9.9")


def test_newer_version_is_reported():
    with patch("urllib.request.urlopen", return_value=_fake_response(
            {"version": "0.5.0", "notes": "new stuff", "url": "https://example.com"})):
        info = check_for_update(current="0.4.0", url="https://example.com/version.json")
    assert info == UpdateInfo(version="0.5.0", notes="new stuff", url="https://example.com")


def test_same_or_older_version_reports_nothing():
    with patch("urllib.request.urlopen", return_value=_fake_response({"version": "0.4.0"})):
        assert check_for_update(current="0.4.0", url="https://example.com/version.json") is None
    with patch("urllib.request.urlopen", return_value=_fake_response({"version": "0.3.0"})):
        assert check_for_update(current="0.4.0", url="https://example.com/version.json") is None


def test_network_failure_reports_nothing_not_an_exception():
    with patch("urllib.request.urlopen", side_effect=OSError("no internet")):
        assert check_for_update(current="0.4.0", url="https://example.com/version.json") is None


def test_malformed_manifest_reports_nothing():
    class _BadResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"not json at all"

    with patch("urllib.request.urlopen", return_value=_BadResp()):
        assert check_for_update(current="0.4.0", url="https://example.com/version.json") is None
