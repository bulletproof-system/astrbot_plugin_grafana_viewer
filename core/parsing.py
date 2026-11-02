from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    name: str
    arguments: tuple[str, ...]

    @property
    def argument_text(self) -> str:
        return " ".join(self.arguments)


def parse_grafana_command(message: str) -> ParsedCommand:
    """Parse the text after AstrBot has woken the command handler."""
    tokens = message.strip().split()
    if tokens and tokens[0].lstrip("/").casefold() == "grafana":
        tokens = tokens[1:]
    if not tokens:
        return ParsedCommand(name="help", arguments=())
    return ParsedCommand(
        name=tokens[0].casefold(),
        arguments=tuple(tokens[1:]),
    )


def group_is_allowed(group_id: str, allowed_groups: frozenset[str]) -> bool:
    """An empty allow-list permits all groups; private chats are not groups."""
    if not group_id or not allowed_groups:
        return True
    return str(group_id) in allowed_groups
