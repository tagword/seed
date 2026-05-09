"""Command routing engine."""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from seed.core.models import Command, CommandRoutingResult

logger = logging.getLogger(__name__)


def tokenize_input(input_string: str) -> Tuple[str, List[str]]:
    tokens = input_string.strip().split()
    if not tokens:
        return "", []
    command_name = tokens[0]
    arguments = tokens[1:] if len(tokens) > 1 else []
    return command_name, arguments


def edit_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def fuzzy_match(input_cmd: str, commands: List[Command], threshold: int = 3) -> List[str]:
    suggestions: List[Tuple[int, str]] = []
    input_lower = input_cmd.lower()
    for cmd in commands:
        if cmd.name.lower() == input_lower or any(
            alias.lower() == input_lower for alias in cmd.aliases
        ):
            return [cmd.name]
        distance = edit_distance(input_lower, cmd.name.lower())
        if distance <= threshold:
            suggestions.append((distance, cmd.name))
    suggestions.sort(key=lambda x: x[0])
    return [name for _, name in suggestions[:3]] if suggestions else []


def find_command(input_cmd: str, commands: List[Command], fuzzy_threshold: int = 3) -> CommandRoutingResult:
    input_lower = input_cmd.lower().strip()
    for cmd in commands:
        if cmd.name.lower() == input_lower:
            return CommandRoutingResult(matched=True, command=cmd, command_args=[])
        for alias in cmd.aliases:
            if alias.lower() == input_lower:
                return CommandRoutingResult(
                    matched=True,
                    command=cmd,
                    command_args=[],
                    suggestions=[cmd.name],
                )
    prefix_matches = [
        cmd
        for cmd in commands
        if cmd.name.lower().startswith(input_lower)
        or any(alias.lower().startswith(input_lower) for alias in cmd.aliases)
    ]
    if len(prefix_matches) == 1:
        return CommandRoutingResult(matched=True, command=prefix_matches[0], command_args=[])

    suggestions = fuzzy_match(input_cmd, commands, fuzzy_threshold)

    if prefix_matches:
        names = [cmd.name for cmd in prefix_matches[:3]]
        return CommandRoutingResult(
            matched=False,
            command=None,
            command_args=[],
            suggestions=names,
            error=f"Ambiguous prefix match. Did you mean one of: {', '.join(names)}?",
        )
    if suggestions:
        return CommandRoutingResult(
            matched=False,
            command=None,
            command_args=[],
            suggestions=suggestions,
            error=f"Command not found. Did you mean: {', '.join(suggestions)}?",
        )
    return CommandRoutingResult(
        matched=False,
        command=None,
        command_args=[],
        suggestions=[],
        error=f"Command '{input_cmd}' not found. Use 'commands' to see available commands.",
    )


class CommandRouter:
    """Register commands and route raw input strings."""

    def __init__(self) -> None:
        self.commands: List[Command] = []

    def add_command(self, command: Command) -> None:
        self.commands.append(command)

    def add_commands(self, commands: List[Command]) -> None:
        self.commands.extend(commands)

    def remove_command(self, name: str) -> bool:
        for i, cmd in enumerate(self.commands):
            if cmd.name.lower() == name.lower() or any(
                alias.lower() == name.lower() for alias in cmd.aliases
            ):
                self.commands.pop(i)
                return True
        return False

    def get_command(self, name: str) -> Optional[Command]:
        for cmd in self.commands:
            if cmd.name.lower() == name.lower() or any(
                alias.lower() == name.lower() for alias in cmd.aliases
            ):
                return cmd
        return None

    def list_commands(self) -> List[Command]:
        return self.commands.copy()

    def count_commands(self) -> int:
        return len(self.commands)

    def route(self, input_string: str, fuzzy_threshold: int = 3) -> CommandRoutingResult:
        command_name, arguments = tokenize_input(input_string)
        if not command_name:
            return CommandRoutingResult(
                matched=False,
                command=None,
                command_args=[],
                error="Empty input",
            )
        result = find_command(command_name, self.commands, fuzzy_threshold)
        result.command_args = arguments
        return result
