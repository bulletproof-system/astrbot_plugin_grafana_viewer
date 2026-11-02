from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star

from .core.cache import RenderCoordinator
from .core.config import ConfigLoadResult, PluginConfig, ViewConfig, load_config
from .core.errors import (
    ConfigurationError,
    GrafanaViewerError,
    RangeNotAllowedError,
    ViewNotFoundError,
)
from .core.grafana_client import GrafanaClient
from .core.models import RenderedImage
from .core.parsing import group_is_allowed, parse_grafana_command
from .core.ranges import format_ranges, range_label

HELP_TEXT = """Grafana 面板助手

/grafana list [关键词]       查看可用视图
/grafana show <视图> [范围]  查看面板，例如 /grafana show cpu 6h
/grafana status              检查服务状态（管理员）
/grafana test [视图]         实际测试面板渲染（管理员）

支持的时间范围：15m / 1h / 6h / 24h / 7d / 30d
"""


class GrafanaViewerPlugin(Star):
    """通过 Grafana 渲染接口在聊天中查看预配置的面板图片。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self._raw_config = dict(config or {})
        self._config: PluginConfig | None = None
        self._config_error: ConfigurationError | None = None
        self._config_warnings: tuple[str, ...] = ()
        self._client: GrafanaClient | None = None
        self._coordinator: RenderCoordinator | None = None

    async def initialize(self) -> None:
        try:
            loaded: ConfigLoadResult = load_config(self._raw_config)
        except ConfigurationError as exc:
            self._config_error = exc
            logger.error("Grafana Viewer 配置无效：%s", exc.detail)
            return
        except Exception:
            self._config_error = ConfigurationError("配置解析时发生未预期错误")
            logger.exception("Grafana Viewer 配置解析失败")
            return

        self._config = loaded.config
        self._config_warnings = loaded.warnings
        for warning in loaded.warnings:
            logger.warning("Grafana Viewer %s", warning)

        self._client = GrafanaClient(self._config)
        self._coordinator = RenderCoordinator(
            cache_seconds=self._config.cache_seconds,
            cooldown_seconds=self._config.cooldown_seconds,
        )
        logger.info(
            "Grafana Viewer 已加载：%d 个视图，缓存 %d 秒，冷却 %d 秒",
            len(self._config.views),
            self._config.cache_seconds,
            self._config.cooldown_seconds,
        )

    @filter.command("grafana")
    async def grafana(self, event: AstrMessageEvent):
        """Grafana 面板助手"""
        try:
            result = await self._dispatch(event)
        except GrafanaViewerError as exc:
            logger.warning("Grafana Viewer 请求失败：%s", exc.detail)
            result = event.plain_result(exc.safe_message)
        except Exception:
            logger.exception("Grafana Viewer 未处理异常")
            result = event.plain_result("Grafana 面板请求失败，请稍后重试。")
        yield result

    async def _dispatch(self, event: AstrMessageEvent):
        parsed = parse_grafana_command(event.message_str)
        command = parsed.name

        if command in {"help", "?"}:
            return event.plain_result(HELP_TEXT)

        config, client, coordinator = self._runtime()
        if not group_is_allowed(event.get_group_id(), config.allowed_groups):
            logger.info("Grafana Viewer 拒绝未授权群：%s", event.get_group_id())
            return event.plain_result("本群未启用 Grafana 面板助手。")

        if command == "list":
            return self._handle_list(event, config, parsed.argument_text)
        if command == "show":
            return await self._handle_show(event, config, client, coordinator, parsed.arguments)
        if command == "status":
            if parsed.arguments:
                return event.plain_result("用法：/grafana status")
            return await self._handle_status(event, config, client, coordinator)
        if command == "test":
            return await self._handle_test(event, config, client, coordinator, parsed.arguments)

        return event.plain_result(
            "未知命令。\n\n" + HELP_TEXT,
        )

    def _runtime(self) -> tuple[PluginConfig, GrafanaClient, RenderCoordinator]:
        if self._config_error is not None:
            raise self._config_error
        if self._config is None or self._client is None or self._coordinator is None:
            raise ConfigurationError("插件尚未完成初始化")
        return self._config, self._client, self._coordinator

    def _handle_list(
        self,
        event: AstrMessageEvent,
        config: PluginConfig,
        keyword: str,
    ):
        if not config.views:
            return event.plain_result(
                "暂无可用视图。请管理员在插件配置的 views 中添加视图。",
            )

        matched = config.filter_views(keyword)
        if not matched:
            return event.plain_result(
                f"没有找到包含“{keyword.strip()}”的视图。请使用 /grafana list 查看全部视图。",
            )

        visible = matched[:20]
        lines = ["可用 Grafana 视图：", ""]
        for view in visible:
            lines.append(f"• {view.name} — {view.title}")
            lines.append(f"  默认 {view.default_range}，可选 {format_ranges(view.allowed_ranges)}")
            lines.append("")
        if len(matched) > len(visible):
            lines.append(
                "仅显示前 20 个匹配视图，请使用 /grafana list <关键词> 缩小范围。",
            )
        return event.plain_result("\n".join(lines).rstrip())

    async def _handle_show(
        self,
        event: AstrMessageEvent,
        config: PluginConfig,
        client: GrafanaClient,
        coordinator: RenderCoordinator,
        arguments: tuple[str, ...],
    ):
        if not arguments or len(arguments) > 2:
            return event.plain_result(
                "用法：/grafana show <视图> [时间范围]\n例如：/grafana show cpu 6h",
            )

        view = self._resolve_view(config, arguments[0])
        range_value = arguments[1].strip().lower() if len(arguments) == 2 else view.default_range
        if range_value not in view.allowed_ranges:
            raise RangeNotAllowedError(
                f"view {view.name!r} does not allow range {range_value!r}",
                user_message=(
                    f"该视图不支持此时间范围，可选范围：{format_ranges(view.allowed_ranges)}。"
                ),
            )

        group_key = event.get_group_id() or event.get_session_id()
        image = await coordinator.get_or_render(
            group_key=group_key,
            cache_key=view.cache_key(range_value),
            loader=lambda: client.render(view, range_value),
        )
        caption = f"{view.title} · {range_label(range_value)}"
        return self._image_result(event, caption, image)

    async def _handle_status(
        self,
        event: AstrMessageEvent,
        config: PluginConfig,
        client: GrafanaClient,
        coordinator: RenderCoordinator,
    ):
        if not event.is_admin():
            return event.plain_result("仅管理员可执行该命令。")

        connection = "正常"
        try:
            await client.check_health()
        except GrafanaViewerError as exc:
            connection = f"异常（{exc.safe_message}）"
            logger.warning("Grafana Viewer 健康检查失败：%s", exc.detail)

        lines = [
            "Grafana 面板助手状态",
            "",
            "Grafana 地址：已配置",
            f"Grafana 连接：{connection}",
            f"Grafana 认证：{'已配置' if config.has_token else '未配置'}",
            "图片渲染器：请使用 /grafana test 检查",
            f"已配置视图：{len(config.views)} 个",
            f"缓存条目：{coordinator.cache_size} 个",
            f"进行中的渲染：{coordinator.inflight_count} 个",
            f"默认超时：{config.request_timeout} 秒",
        ]
        if self._config_warnings:
            lines.append(f"配置警告：{len(self._config_warnings)} 条（详见日志）")
        return event.plain_result("\n".join(lines))

    async def _handle_test(
        self,
        event: AstrMessageEvent,
        config: PluginConfig,
        client: GrafanaClient,
        coordinator: RenderCoordinator,
        arguments: tuple[str, ...],
    ):
        if not event.is_admin():
            return event.plain_result("仅管理员可执行该命令。")
        if len(arguments) > 1:
            return event.plain_result("用法：/grafana test [视图]")
        if not config.views:
            raise ConfigurationError("没有可用于测试的视图")
        if arguments:
            view = self._resolve_view(config, arguments[0])
        else:
            view = config.views[0]

        image = await coordinator.render_direct(
            lambda: client.render(view, view.default_range),
        )
        caption = f"渲染测试 · {view.title} · {range_label(view.default_range)}"
        return self._image_result(event, caption, image)

    @staticmethod
    def _resolve_view(config: PluginConfig, name: str) -> ViewConfig:
        view = config.find_view(name)
        if view is None:
            raise ViewNotFoundError(f"unknown view: {name!r}")
        return view

    @staticmethod
    def _image_result(
        event: AstrMessageEvent,
        caption: str,
        image: RenderedImage,
    ):
        return event.chain_result(
            [
                Plain(caption),
                Image.fromBytes(image.content),
            ],
        )

    async def terminate(self) -> None:
        coordinator = self._coordinator
        client = self._client
        self._coordinator = None
        self._client = None
        if coordinator is not None:
            await coordinator.close()
        if client is not None:
            await client.aclose()
