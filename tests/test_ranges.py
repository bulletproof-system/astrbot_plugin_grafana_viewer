import pytest

from core.errors import RangeNotAllowedError
from core.ranges import from_expression, normalize_range, range_label, validate_allowed_ranges


def test_supported_ranges_are_normalized():
    assert normalize_range(" 6H ") == "6h"
    assert from_expression("24h") == "now-24h"
    assert range_label("15m") == "最近 15 分钟"


def test_unsupported_ranges_fail():
    with pytest.raises(RangeNotAllowedError):
        normalize_range("37h")


def test_allowed_ranges_are_deduplicated_and_ordered():
    assert validate_allowed_ranges(["6h", "1h", "6h"]) == ("6h", "1h")
