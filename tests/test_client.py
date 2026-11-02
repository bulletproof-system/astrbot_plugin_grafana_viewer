import asyncio

import httpx
import pytest
from httpx import MockTransport, Request, Response

from core.config import load_config
from core.errors import (
    GrafanaAuthError,
    ImageTooLargeError,
    InvalidImageResponseError,
    PanelNotFoundError,
    RendererUnavailableError,
    RenderTimeoutError,
)
from core.grafana_client import GrafanaClient


def _client(handler, *, max_image_bytes=1024 * 1024):
    raw = {
        "grafana_base_url": "http://grafana:3000",
        "grafana_token": "secret-token",
        "render_timezone": "Asia/Shanghai",
        "max_image_bytes": max_image_bytes,
        "views": [
            {
                "name": "cpu",
                "title": "CPU",
                "dashboard_uid": "prod overview",
                "slug": "overview page",
                "panel_id": 4,
                "variables": {"instance": "prod 1", "job": "api&v1"},
            }
        ],
    }
    config = load_config(raw).config
    return config, config.views[0], GrafanaClient(config, transport=MockTransport(handler))


def test_render_url_and_query_encoding():
    seen = {}

    def handler(request: Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["accept"] = request.headers["accept"]
        return Response(200, headers={"content-type": "image/png"}, content=b"png")

    config, view, client = _client(handler)
    image = asyncio.run(client.render(view, "6h"))
    asyncio.run(client.aclose())
    assert image.content == b"png"
    assert seen["auth"] == "Bearer secret-token"
    assert seen["accept"] == "image/png,image/jpeg"
    assert "/render/d-solo/prod%20overview/overview%20page" in seen["url"]
    assert "panelId=4" in seen["url"]
    assert "from=now-6h" in seen["url"]
    assert "tz=Asia%2FShanghai" in seen["url"]
    assert "var-instance=prod+1" in seen["url"]
    assert "var-job=api%26v1" in seen["url"]


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, GrafanaAuthError),
        (403, GrafanaAuthError),
        (404, PanelNotFoundError),
        (500, RendererUnavailableError),
        (503, RendererUnavailableError),
        (504, RenderTimeoutError),
    ],
)
def test_http_errors_are_classified(status, error):
    def handler(request: Request):
        return Response(status)

    _, view, client = _client(handler)
    with pytest.raises(error):
        asyncio.run(client.render(view, "1h"))
    asyncio.run(client.aclose())


def test_non_image_response_is_rejected():
    def handler(request: Request):
        return Response(200, headers={"content-type": "text/html"}, content=b"<html>")

    _, view, client = _client(handler)
    with pytest.raises(InvalidImageResponseError):
        asyncio.run(client.render(view, "1h"))
    asyncio.run(client.aclose())


def test_image_size_limit_is_enforced():
    def handler(request: Request):
        return Response(200, headers={"content-type": "image/png"}, content=b"x" * 2048)

    _, view, client = _client(handler, max_image_bytes=1024)
    with pytest.raises(ImageTooLargeError):
        asyncio.run(client.render(view, "1h"))
    asyncio.run(client.aclose())


def test_request_timeout_is_classified():
    def handler(request: Request):
        raise httpx.ReadTimeout("timed out", request=request)

    _, view, client = _client(handler)
    with pytest.raises(RenderTimeoutError):
        asyncio.run(client.render(view, "1h"))
    asyncio.run(client.aclose())
