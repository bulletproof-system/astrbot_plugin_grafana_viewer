from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .errors import ConfigurationError
from .ranges import (
    DEFAULT_ALLOWED_RANGES,
    format_ranges,
    normalize_range,
    validate_allowed_ranges,
)

DEFAULT_WIDTH = 1000
DEFAULT_HEIGHT = 600
DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ViewConfig:
    name: str
    title: str
    dashboard_uid: str
    panel_id: int
    slug: str
    org_id: int
    default_range: str
    allowed_ranges: tuple[str, ...]
    variables: tuple[tuple[str, str], ...]
    width: int
    height: int

    def cache_key(self, range_value: str) -> str:
        return json.dumps(
            {
                "dashboard_uid": self.dashboard_uid,
                "height": self.height,
                "name": self.name,
                "org_id": self.org_id,
                "panel_id": self.panel_id,
                "range": range_value,
                "slug": self.slug,
                "variables": dict(self.variables),
                "width": self.width,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class PluginConfig:
    grafana_base_url: str
    grafana_token: str = field(repr=False)
    grafana_org_id: int
    render_timezone: str
    request_timeout: int
    render_width: int
    render_height: int
    cache_seconds: int
    cooldown_seconds: int
    allowed_groups: frozenset[str]
    max_image_bytes: int
    views: tuple[ViewConfig, ...]

    @property
    def has_token(self) -> bool:
        return bool(self.grafana_token)

    def find_view(self, name: str) -> ViewConfig | None:
        wanted = name.strip().casefold()
        for view in self.views:
            if view.name.casefold() == wanted:
                return view
        return None

    def filter_views(self, keyword: str = "") -> list[ViewConfig]:
        needle = keyword.strip().casefold()
        if not needle:
            return list(self.views)
        return [
            view
            for view in self.views
            if needle in view.name.casefold() or needle in view.title.casefold()
        ]


@dataclass(frozen=True, slots=True)
class ConfigLoadResult:
    config: PluginConfig
    warnings: tuple[str, ...] = ()


def _as_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{label} 必须是对象")
    return value


def _string(value: object, label: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigurationError(f"{label} 必须是字符串")
    return value.strip()


def _int_value(
    value: object,
    label: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ConfigurationError(f"{label} 必须是整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{label} 必须是整数") from exc
    if result < minimum or result > maximum:
        raise ConfigurationError(f"{label} 必须在 {minimum} 到 {maximum} 之间")
    return result


def _optional_int(
    value: object,
    label: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if value in (None, "", 0):
        return default
    return _int_value(
        value,
        label,
        default,
        minimum=minimum,
        maximum=maximum,
    )


def _path_component(value: object, label: str, default: str = "") -> str:
    result = _string(value, label, default)
    if not result:
        raise ConfigurationError(f"{label} 不能为空")
    if any(char in result for char in ("/", "\\", "?", "#", "\x00")):
        raise ConfigurationError(f"{label} 不能包含路径或查询字符")
    return result


def _alias(value: object) -> str:
    result = _string(value, "视图 name")
    if not result:
        raise ConfigurationError("视图 name 不能为空")
    if len(result) > 64 or any(char.isspace() for char in result):
        raise ConfigurationError("视图 name 最长 64 字符且不能包含空白")
    return result


def _variables(value: object) -> tuple[tuple[str, str], ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ConfigurationError("视图 variables 必须是有效 JSON 对象") from exc
    if not isinstance(value, Mapping):
        raise ConfigurationError("视图 variables 必须是对象或 JSON 对象字符串")

    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, item in value.items():
        name = str(key).strip()
        if name.startswith("var-"):
            name = name[4:]
        if not name:
            raise ConfigurationError("视图变量名不能为空")
        if name in seen:
            raise ConfigurationError(f"视图变量重复：{name}")
        if item is None:
            raise ConfigurationError(f"视图变量 {name} 不能为空")
        if isinstance(item, bool):
            encoded = "true" if item else "false"
        elif isinstance(item, (str, int, float)):
            encoded = str(item)
        else:
            raise ConfigurationError(f"视图变量 {name} 必须是标量")
        seen.add(name)
        result.append((name, encoded))
    result.sort(key=lambda entry: entry[0])
    return tuple(result)


def _allowed_groups(value: object) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        values: Sequence[object] = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        raise ConfigurationError("allowed_groups 必须是字符串列表")
    return frozenset(str(item).strip() for item in values if str(item).strip())


def _timezone(value: object) -> str:
    result = _string(value, "render_timezone", "Asia/Shanghai")
    if not result:
        return ""
    if len(result) > 64 or any(char.isspace() for char in result):
        raise ConfigurationError("render_timezone 必须是有效时区名称")
    return result


def _base_url(value: object) -> str:
    url = _string(value, "grafana_base_url", "http://grafana:3000")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("grafana_base_url 必须是有效的 HTTP(S) 地址")
    if parsed.username or parsed.password:
        raise ConfigurationError("grafana_base_url 不能包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise ConfigurationError("grafana_base_url 不能包含查询参数或片段")
    return url.rstrip("/")


def _parse_view(
    raw: Mapping[str, object],
    *,
    index: int,
    global_org_id: int,
    global_width: int,
    global_height: int,
) -> ViewConfig:
    label = f"视图 #{index + 1}"
    name = _alias(raw.get("name"))
    title = _string(raw.get("title"), f"{label} title")
    if not title:
        raise ConfigurationError(f"{label} title 不能为空")

    dashboard_uid = _path_component(raw.get("dashboard_uid"), f"{label} dashboard_uid")
    panel_id = _int_value(
        raw.get("panel_id"),
        f"{label} panel_id",
        0,
        minimum=1,
        maximum=2_147_483_647,
    )
    if panel_id == 0:
        raise ConfigurationError(f"{label} panel_id 为必填项")

    slug = _string(raw.get("slug"), f"{label} slug") or dashboard_uid
    slug = _path_component(slug, f"{label} slug")
    org_id = _optional_int(
        raw.get("org_id"),
        f"{label} org_id",
        global_org_id,
        minimum=1,
        maximum=2_147_483_647,
    )

    allowed_source = raw.get("allowed_ranges")
    if allowed_source is None or allowed_source == []:
        allowed_ranges = DEFAULT_ALLOWED_RANGES
    else:
        try:
            allowed_ranges = validate_allowed_ranges(allowed_source)
        except Exception as exc:
            raise ConfigurationError(f"{label} allowed_ranges 无效") from exc

    default_source = raw.get("default_range")
    try:
        default_range = (
            normalize_range(default_source) if default_source not in (None, "") else "1h"
        )
    except Exception as exc:
        raise ConfigurationError(f"{label} default_range 无效") from exc
    if default_range not in allowed_ranges:
        raise ConfigurationError(
            f"{label} default_range 必须包含在 allowed_ranges 中（可选："
            f"{format_ranges(allowed_ranges)}）"
        )

    width = _optional_int(
        raw.get("width"),
        f"{label} width",
        global_width,
        minimum=100,
        maximum=5000,
    )
    height = _optional_int(
        raw.get("height"),
        f"{label} height",
        global_height,
        minimum=100,
        maximum=5000,
    )

    return ViewConfig(
        name=name,
        title=title,
        dashboard_uid=dashboard_uid,
        panel_id=panel_id,
        slug=slug,
        org_id=org_id,
        default_range=default_range,
        allowed_ranges=allowed_ranges,
        variables=_variables(raw.get("variables")),
        width=width,
        height=height,
    )


def load_config(raw_config: object) -> ConfigLoadResult:
    raw = _as_mapping(raw_config, "插件配置")
    base_url = _base_url(raw.get("grafana_base_url"))
    token = _string(raw.get("grafana_token"), "grafana_token")
    org_id = _int_value(
        raw.get("grafana_org_id"),
        "grafana_org_id",
        1,
        minimum=1,
        maximum=2_147_483_647,
    )
    render_timezone = _timezone(raw.get("render_timezone"))
    timeout = _int_value(
        raw.get("request_timeout"),
        "request_timeout",
        10,
        minimum=1,
        maximum=120,
    )
    width = _int_value(
        raw.get("render_width"),
        "render_width",
        DEFAULT_WIDTH,
        minimum=100,
        maximum=5000,
    )
    height = _int_value(
        raw.get("render_height"),
        "render_height",
        DEFAULT_HEIGHT,
        minimum=100,
        maximum=5000,
    )
    cache_seconds = _int_value(
        raw.get("cache_seconds"),
        "cache_seconds",
        20,
        minimum=0,
        maximum=3600,
    )
    cooldown_seconds = _int_value(
        raw.get("cooldown_seconds"),
        "cooldown_seconds",
        15,
        minimum=0,
        maximum=3600,
    )
    max_image_bytes = _int_value(
        raw.get("max_image_bytes"),
        "max_image_bytes",
        DEFAULT_MAX_IMAGE_BYTES,
        minimum=1024,
        maximum=100 * 1024 * 1024,
    )

    views_raw = raw.get("views")
    if views_raw is None:
        views_raw = []
    if not isinstance(views_raw, list):
        raise ConfigurationError("views 必须是列表")

    views: list[ViewConfig] = []
    warnings: list[str] = []
    seen_names: set[str] = set()
    for index, item in enumerate(views_raw):
        try:
            view = _parse_view(
                _as_mapping(item, f"视图 #{index + 1}"),
                index=index,
                global_org_id=org_id,
                global_width=width,
                global_height=height,
            )
            normalized_name = view.name.casefold()
            if normalized_name in seen_names:
                raise ConfigurationError(f"视图 name 重复：{view.name}")
            seen_names.add(normalized_name)
            views.append(view)
        except (ConfigurationError, ValueError) as exc:
            warning = f"已跳过视图 #{index + 1}：{exc}"
            warnings.append(warning)

    return ConfigLoadResult(
        config=PluginConfig(
            grafana_base_url=base_url,
            grafana_token=token,
            grafana_org_id=org_id,
            render_timezone=render_timezone,
            request_timeout=timeout,
            render_width=width,
            render_height=height,
            cache_seconds=cache_seconds,
            cooldown_seconds=cooldown_seconds,
            allowed_groups=_allowed_groups(raw.get("allowed_groups")),
            max_image_bytes=max_image_bytes,
            views=tuple(views),
        ),
        warnings=tuple(warnings),
    )
