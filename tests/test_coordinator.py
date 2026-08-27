"""Tests for endpoint discovery helpers."""

from custom_components.canon_ccapi.coordinator import (
    find_endpoint_url,
    merge_shooting_settings,
    sticky_updates,
)

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


AGGREGATE = {
    "shootingmodedial": {"value": "a+"},
    "iso": {"value": "", "ability": []},
    "drive": {"value": "single", "ability": ["single", "continuous"]},
    "stillimagequality": {
        "value": {"raw": "none", "jpeg": "small2"},
        "ability": {"raw": ["none", "raw"], "jpeg": ["small2", "large_fine"]},
    },
    "soundrecording_level": {"value": None},
    "unknown_future_setting": {"value": "x"},
}


def test_merge_keeps_known_settings():
    merged = merge_shooting_settings(AGGREGATE)
    assert merged["drive"]["value"] == "single"
    assert merged["iso"] == {"value": "", "ability": []}
    # Read-only settings ride along so the sensors can use them.
    assert merged["shootingmodedial"]["value"] == "a+"


def test_merge_drops_settings_without_entities():
    merged = merge_shooting_settings(AGGREGATE)
    assert "unknown_future_setting" not in merged
    assert "soundrecording_level" not in merged


def test_merge_tolerates_junk():
    assert merge_shooting_settings(None) == {}
    assert merge_shooting_settings({"iso": "not-a-dict"}) == {}


def test_sticky_skips_settings_the_mode_forbids():
    # iso reads "" in A+; re-asserting would only earn a 503.
    assert sticky_updates({"iso": "400"}, AGGREGATE) == {}


def test_sticky_skips_values_already_correct():
    assert sticky_updates({"drive": "single"}, AGGREGATE) == {}


def test_sticky_returns_drifted_value():
    assert sticky_updates({"drive": "continuous"}, AGGREGATE) == {"drive": "continuous"}


def test_sticky_merges_image_quality_sub_values():
    # The endpoint rejects a PUT that omits either sub-value, so raw has to be
    # carried over from what the camera currently holds.
    assert sticky_updates({"stillimagequality": {"jpeg": "large_fine"}}, AGGREGATE) == {
        "stillimagequality": {"raw": "none", "jpeg": "large_fine"}
    }


def test_sticky_skips_unknown_setting():
    assert sticky_updates({"displayoff": "30"}, AGGREGATE) == {}
