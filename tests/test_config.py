import pytest

from core.config import load_config
from core.errors import ConfigurationError


def _raw_config():
    return {
        "grafana_base_url": "http://grafana:3000/",
        "grafana_token": "secret-token",
        "views": [
            {
                "name": "cpu",
                "title": "CPU 使用率",
                "dashboard_uid": "prod-overview",
                "panel_id": 4,
                "variables": '{"instance":"prod-1","job":"api"}',
            }
        ],
    }


def test_view_defaults_and_variable_parsing():
    loaded = load_config(_raw_config())
    view = loaded.config.views[0]
    assert loaded.config.grafana_base_url == "http://grafana:3000"
    assert view.slug == "prod-overview"
    assert view.org_id == 1
    assert view.default_range == "1h"
    assert view.allowed_ranges == ("1h", "6h", "24h")
    assert view.width == 1000
    assert view.height == 600
    assert dict(view.variables) == {"instance": "prod-1", "job": "api"}
    assert "secret-token" not in view.cache_key("1h")
    assert "secret-token" not in repr(loaded.config)


def test_view_override_zero_uses_global_values():
    raw = _raw_config()
    raw["views"][0].update({"org_id": 0, "width": 0, "height": 0})
    view = load_config(raw).config.views[0]
    assert (view.org_id, view.width, view.height) == (1, 1000, 600)


def test_invalid_view_is_skipped_with_safe_warning():
    raw = _raw_config()
    raw["views"].append({"name": "bad view", "title": "bad"})
    loaded = load_config(raw)
    assert len(loaded.config.views) == 1
    assert len(loaded.warnings) == 1
    assert "secret-token" not in loaded.warnings[0]


def test_default_range_must_be_allowed():
    raw = _raw_config()
    raw["views"][0]["allowed_ranges"] = ["6h", "24h"]
    loaded = load_config(raw)
    assert loaded.config.views == ()
    assert "default_range" in loaded.warnings[0]


def test_invalid_global_url_fails():
    raw = _raw_config()
    raw["grafana_base_url"] = "file:///etc/passwd"
    with pytest.raises(ConfigurationError):
        load_config(raw)
