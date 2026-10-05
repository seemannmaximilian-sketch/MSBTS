from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4


class CommandStatus(str, Enum):
    NEW = "new"
    EXECUTED = "executed"
    UNDONE = "undone"


@dataclass
class BaseCommand(ABC):
    """Base class for reversible application commands."""

    description: str = ""
    user: str | None = None
    id: UUID = field(default_factory=uuid4, init=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc), init=False)
    status: CommandStatus = field(default=CommandStatus.NEW, init=False)

    def __post_init__(self) -> None:
        if not self.description:
            self.description = self.__class__.__name__

    @abstractmethod
    def execute(self) -> None:
        """Apply the command."""

    @abstractmethod
    def undo(self) -> None:
        """Reverse the command."""
