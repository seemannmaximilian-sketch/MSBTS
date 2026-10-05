from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .base import BaseCommand, CommandStatus
from .exceptions import NothingToRedoError, NothingToUndoError


CommandListener = Callable[[BaseCommand], None]


@dataclass(frozen=True)
class CommandHistoryEntry:
    description: str
    status: CommandStatus
    command: BaseCommand


class CommandManager:
    """Executes reversible commands and manages undo/redo history."""

    def __init__(self) -> None:
        self._undo_stack: list[BaseCommand] = []
        self._redo_stack: list[BaseCommand] = []
        self._history: list[CommandHistoryEntry] = []
        self._listeners: dict[str, list[CommandListener]] = {
            "executed": [],
            "undone": [],
            "redone": [],
            "cleared": [],
        }

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def undo_description(self) -> str | None:
        return self._undo_stack[-1].description if self._undo_stack else None

    @property
    def redo_description(self) -> str | None:
        return self._redo_stack[-1].description if self._redo_stack else None

    def subscribe(self, event: str, listener: CommandListener) -> None:
        if event not in self._listeners:
            raise ValueError(f"Unbekanntes Command-Ereignis: {event}")
        self._listeners[event].append(listener)

    def execute(self, command: BaseCommand) -> BaseCommand:
        command.execute()
        command.status = CommandStatus.EXECUTED
        self._undo_stack.append(command)
        self._redo_stack.clear()
        self._record(command)
        self._emit("executed", command)
        return command

    def undo(self) -> BaseCommand:
        if not self._undo_stack:
            raise NothingToUndoError("Es gibt keine Aktion zum Rückgängigmachen.")
        command = self._undo_stack.pop()
        command.undo()
        command.status = CommandStatus.UNDONE
        self._redo_stack.append(command)
        self._record(command)
        self._emit("undone", command)
        return command

    def redo(self) -> BaseCommand:
        if not self._redo_stack:
            raise NothingToRedoError("Es gibt keine Aktion zum Wiederholen.")
        command = self._redo_stack.pop()
        command.execute()
        command.status = CommandStatus.EXECUTED
        self._undo_stack.append(command)
        self._record(command)
        self._emit("redone", command)
        return command

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._history.clear()
        self._emit("cleared", _ClearCommand())

    def history(self) -> tuple[CommandHistoryEntry, ...]:
        return tuple(self._history)

    def _record(self, command: BaseCommand) -> None:
        self._history.append(CommandHistoryEntry(command.description, command.status, command))

    def _emit(self, event: str, command: BaseCommand) -> None:
        for listener in tuple(self._listeners[event]):
            listener(command)


class _ClearCommand(BaseCommand):
    def __init__(self) -> None:
        super().__init__(description="Command-Verlauf geleert")

    def execute(self) -> None:
        return None

    def undo(self) -> None:
        return None
