"""Tests for endpoint discovery helpers."""

from custom_components.canon_ccapi.coordinator import find_endpoint_url

MANIFEST = {
    "ver100": [
        {"path": "/ccapi/ver100/shooting/control/shutterbutton", "get": False, "post": True},
    ],
    "ver110": [
        {"path": "/ccapi/ver110/shooting/control/shutterbutton", "get": False, "post": True},
        {"path": "/ccapi/ver110/devicestatus/battery", "get": True},
    ],
}


def test_picks_highest_version():
    url = find_endpoint_url(MANIFEST, "1.2.3.4", 8080, "control/shutterbutton", method="post")
    assert url == "http://1.2.3.4:8080/ccapi/ver110/shooting/control/shutterbutton"


def test_respects_method():
    assert find_endpoint_url(MANIFEST, "1.2.3.4", 8080, "control/shutterbutton") is None


def test_unknown_suffix_returns_none():
    assert find_endpoint_url(MANIFEST, "1.2.3.4", 8080, "does/not/exist") is None
