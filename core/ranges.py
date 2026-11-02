from __future__ import annotations

from .errors import RangeNotAllowedError

SUPPORTED_RANGES: tuple[str, ...] = ("15m", "1h", "6h", "24h", "7d", "30d")
DEFAULT_ALLOWED_RANGES: tuple[str, ...] = ("1h", "6h", "24h")

_RANGE_LABELS = {
    "15m": "最近 15 分钟",
    "1h": "最近 1 小时",
    "6h": "最近 6 小时",
    "24h": "最近 24 小时",
    "7d": "最近 7 天",
    "30d": "最近 30 天",
}


def normalize_range(value: object) -> str:
    """Normalize and validate one of the supported Grafana relative ranges."""
    candidate = str(value).strip().lower()
    if candidate not in SUPPORTED_RANGES:
        raise RangeNotAllowedError(
            f"unsupported range: {candidate!r}",
            user_message=("不支持该时间范围，可选范围：" + " / ".join(SUPPORTED_RANGES) + "。"),
        )
    return candidate


def validate_allowed_ranges(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("allowed_ranges 必须是非空列表")

    result: list[str] = []
    for value in values:
        normalized = normalize_range(value)
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def from_expression(range_value: str) -> str:
    return f"now-{normalize_range(range_value)}"


def range_label(range_value: str) -> str:
    normalized = normalize_range(range_value)
    return _RANGE_LABELS[normalized]


def format_ranges(values: tuple[str, ...]) -> str:
    return " / ".join(values)
