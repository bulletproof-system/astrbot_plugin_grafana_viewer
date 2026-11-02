from __future__ import annotations

from urllib.parse import quote

import httpx

from .config import PluginConfig, ViewConfig
from .errors import (
    GrafanaAuthError,
    GrafanaConnectionError,
    ImageTooLargeError,
    InvalidImageResponseError,
    MissingTokenError,
    PanelNotFoundError,
    RendererUnavailableError,
    RenderFailedError,
    RenderTimeoutError,
)
from .models import RenderedImage
from .ranges import from_expression, normalize_range

ACCEPTED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg"})


class GrafanaClient:
    """Small authenticated client for Grafana image rendering."""

    def __init__(
        self,
        config: PluginConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout),
            follow_redirects=False,
            transport=transport,
        )

    @property
    def base_url(self) -> str:
        return self._config.grafana_base_url

    @staticmethod
    def build_render_url(config: PluginConfig, view: ViewConfig) -> str:
        dashboard_uid = quote(view.dashboard_uid, safe="")
        slug = quote(view.slug, safe="")
        return f"{config.grafana_base_url}/render/d-solo/{dashboard_uid}/{slug}"

    @staticmethod
    def build_query_params(view: ViewConfig, range_value: str) -> list[tuple[str, str]]:
        normalized = normalize_range(range_value)
        params: list[tuple[str, str]] = [
            ("orgId", str(view.org_id)),
            ("panelId", str(view.panel_id)),
            ("from", from_expression(normalized)),
            ("to", "now"),
            ("width", str(view.width)),
            ("height", str(view.height)),
            ("kiosk", ""),
        ]
        params.extend((f"var-{key}", value) for key, value in view.variables)
        return params

    @staticmethod
    def _status_error(status_code: int) -> Exception:
        if status_code in {401, 403}:
            return GrafanaAuthError(f"Grafana returned HTTP {status_code}")
        if status_code == 404:
            return PanelNotFoundError("Grafana returned HTTP 404")
        if status_code == 504:
            return RenderTimeoutError("Grafana returned HTTP 504")
        if status_code in {500, 502, 503}:
            return RendererUnavailableError(f"Grafana returned HTTP {status_code}")
        return RenderFailedError(f"Grafana returned HTTP {status_code}")

    def _auth_headers(self, *, accept: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._config.grafana_token}"}
        if accept:
            headers["Accept"] = accept
        return headers

    async def render(self, view: ViewConfig, range_value: str) -> RenderedImage:
        if not self._config.has_token:
            raise MissingTokenError("grafana_token is not configured")

        url = self.build_render_url(self._config, view)
        params = self.build_query_params(view, range_value)
        headers = self._auth_headers(accept="image/png,image/jpeg")

        try:
            async with self._client.stream(
                "GET",
                url,
                params=params,
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    raise self._status_error(response.status_code)

                content_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                if content_type not in ACCEPTED_CONTENT_TYPES:
                    raise InvalidImageResponseError(
                        f"unexpected content type: {content_type or '<missing>'}"
                    )

                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        declared_size = 0
                    if declared_size > self._config.max_image_bytes:
                        raise ImageTooLargeError("Content-Length exceeds max_image_bytes")

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._config.max_image_bytes:
                        raise ImageTooLargeError("response body exceeds max_image_bytes")
                    chunks.append(chunk)
        except (
            GrafanaAuthError,
            PanelNotFoundError,
            RendererUnavailableError,
            RenderTimeoutError,
            InvalidImageResponseError,
            ImageTooLargeError,
            RenderFailedError,
        ):
            raise
        except httpx.TimeoutException as exc:
            raise RenderTimeoutError("Grafana request timed out") from exc
        except httpx.RequestError as exc:
            raise GrafanaConnectionError("Grafana request failed") from exc
        except httpx.HTTPError as exc:
            raise RenderFailedError("Grafana HTTP client error") from exc

        content = b"".join(chunks)
        if not content:
            raise InvalidImageResponseError("empty image response")
        return RenderedImage(content=content, content_type=content_type)

    async def check_health(self) -> None:
        """Check Grafana reachability without touching the image renderer."""
        url = f"{self._config.grafana_base_url}/api/health"
        headers = (
            self._auth_headers(accept="application/json")
            if self._config.has_token
            else {"Accept": "application/json"}
        )
        try:
            response = await self._client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise RenderTimeoutError("Grafana health check timed out") from exc
        except httpx.RequestError as exc:
            raise GrafanaConnectionError("Grafana health check failed") from exc
        except httpx.HTTPError as exc:
            raise GrafanaConnectionError("Grafana health check client error") from exc

        if response.status_code in {401, 403}:
            raise GrafanaAuthError(f"Grafana health check returned HTTP {response.status_code}")
        if response.status_code != 200:
            raise GrafanaConnectionError(
                f"Grafana health check returned HTTP {response.status_code}"
            )

    async def aclose(self) -> None:
        await self._client.aclose()
