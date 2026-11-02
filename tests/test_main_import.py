import asyncio
import importlib
import sys
import types
from pathlib import Path


def test_main_imports_and_lifecycle_with_astrbot_api(monkeypatch):
    astrbot = types.ModuleType("astrbot")
    astrbot.logger = types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )
    api = types.ModuleType("astrbot.api")
    api.logger = astrbot.logger

    event_module = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:
        pass

    class Filter:
        def command(self, name):
            def decorator(function):
                return function

            return decorator

    event_module.AstrMessageEvent = AstrMessageEvent
    event_module.filter = Filter()

    message_components = types.ModuleType("astrbot.api.message_components")

    class Plain:
        def __init__(self, text):
            self.text = text

    class Image:
        @staticmethod
        def fromBytes(content):
            return ("image", content)

    message_components.Plain = Plain
    message_components.Image = Image

    star_module = types.ModuleType("astrbot.api.star")

    class Context:
        pass

    class Star:
        def __init__(self, context, config=None):
            self.context = context
            self.config = config

    star_module.Context = Context
    star_module.Star = Star

    for name, module in {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event_module,
        "astrbot.api.message_components": message_components,
        "astrbot.api.star": star_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    parent = str(Path(__file__).resolve().parents[2])
    monkeypatch.syspath_prepend(parent)
    module = importlib.import_module("astrbot_plugin_grafana_viewer.main")
    plugin = module.GrafanaViewerPlugin(
        Context(),
        config={
            "grafana_base_url": "http://grafana:3000",
            "grafana_token": "token",
            "allowed_groups": ["10001"],
            "views": [
                {
                    "name": "cpu",
                    "title": "CPU",
                    "dashboard_uid": "uid",
                    "panel_id": 1,
                }
            ],
        },
    )

    class FakeEvent:
        def __init__(self, message, *, group_id="10001", admin=False):
            self.message_str = message
            self.group_id = group_id
            self.admin = admin

        def get_group_id(self):
            return self.group_id

        def get_session_id(self):
            return self.group_id

        def is_admin(self):
            return self.admin

        def plain_result(self, text):
            return ("text", text)

        def chain_result(self, chain):
            return ("chain", chain)

    class FakeClient:
        async def render(self, view, range_value):
            from astrbot_plugin_grafana_viewer.core.models import RenderedImage

            return RenderedImage(content=b"png", content_type="image/png")

        async def check_health(self):
            return None

        async def aclose(self):
            return None

    class FakeCoordinator:
        def __init__(self):
            self.cache_size = 0
            self.inflight_count = 0

        async def get_or_render(self, *, group_key, cache_key, loader):
            return await loader()

        async def render_direct(self, loader):
            return await loader()

        async def close(self):
            return None

    async def commands():
        await plugin.initialize()
        assert plugin._config is not None
        assert len(plugin._config.views) == 1
        await plugin.terminate()

        plugin._client = FakeClient()
        plugin._coordinator = FakeCoordinator()

        help_result = await plugin._dispatch(FakeEvent("/grafana"))
        assert help_result[0] == "text"
        assert "Grafana 面板助手" in help_result[1]

        list_result = await plugin._dispatch(FakeEvent("/grafana list cpu"))
        assert list_result[0] == "text"
        assert "cpu — CPU" in list_result[1]

        show_result = await plugin._dispatch(FakeEvent("/grafana show cpu 6h"))
        assert show_result[0] == "chain"
        assert show_result[1][0].text == "CPU · 最近 6 小时"

        denied = await plugin._dispatch(FakeEvent("/grafana show cpu", group_id="20002"))
        assert denied == ("text", "本群未启用 Grafana 面板助手。")

        admin_denied = await plugin._dispatch(FakeEvent("/grafana status"))
        assert admin_denied == ("text", "仅管理员可执行该命令。")

        status_result = await plugin._dispatch(FakeEvent("/grafana status", admin=True))
        assert "Grafana 连接：正常" in status_result[1]
        assert "渲染时区：Asia/Shanghai" in status_result[1]

        test_result = await plugin._dispatch(FakeEvent("/grafana test cpu", admin=True))
        assert test_result[0] == "chain"
        assert test_result[1][0].text.startswith("渲染测试")

        await plugin.terminate()

    asyncio.run(commands())
