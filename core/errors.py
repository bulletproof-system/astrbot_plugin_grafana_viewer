from __future__ import annotations


class GrafanaViewerError(Exception):
    """Base class for expected plugin failures with a safe user message."""

    user_message = "Grafana 面板请求失败，请稍后重试。"

    def __init__(self, detail: str = "", *, user_message: str | None = None):
        self.detail = detail or self.user_message
        self._user_message = user_message or self.user_message
        super().__init__(self.detail)

    @property
    def safe_message(self) -> str:
        return self._user_message


class ConfigurationError(GrafanaViewerError):
    user_message = "插件配置有误，请联系管理员检查配置。"


class MissingTokenError(GrafanaViewerError):
    user_message = "Grafana 认证未配置，请联系管理员。"


class ViewNotFoundError(GrafanaViewerError):
    user_message = "未找到该视图，请使用 /grafana list 查看可用视图。"


class RangeNotAllowedError(GrafanaViewerError):
    user_message = "该视图不支持此时间范围。"


class RateLimitedError(GrafanaViewerError):
    user_message = "操作过于频繁，请稍后再试。"


class GrafanaConnectionError(GrafanaViewerError):
    user_message = "Grafana 连接失败，请稍后重试。"


class GrafanaAuthError(GrafanaViewerError):
    user_message = "Grafana 认证失败，请联系管理员检查配置。"


class PanelNotFoundError(GrafanaViewerError):
    user_message = "面板不存在或配置已失效，请联系管理员。"


class RendererUnavailableError(GrafanaViewerError):
    user_message = "Grafana 图片渲染器不可用，请联系管理员。"


class RenderTimeoutError(GrafanaViewerError):
    user_message = "Grafana 渲染超时，请稍后重试。"


class InvalidImageResponseError(GrafanaViewerError):
    user_message = "Grafana 返回的渲染结果不是有效图片，请联系管理员。"


class ImageTooLargeError(GrafanaViewerError):
    user_message = "渲染结果过大，已拒绝发送。"


class RenderFailedError(GrafanaViewerError):
    user_message = "Grafana 渲染失败，请稍后重试。"
