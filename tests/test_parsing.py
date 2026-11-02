from core.parsing import group_is_allowed, parse_grafana_command


def test_parse_grafana_commands():
    assert parse_grafana_command("/grafana").name == "help"
    parsed = parse_grafana_command("grafana show CPU 6h")
    assert parsed.name == "show"
    assert parsed.arguments == ("CPU", "6h")
    assert parse_grafana_command("grafana list api db").argument_text == "api db"


def test_group_access_control():
    allowed = frozenset({"10001"})
    assert group_is_allowed("10001", allowed)
    assert not group_is_allowed("20002", allowed)
    assert group_is_allowed("20002", frozenset())
    assert group_is_allowed("", allowed)
