from typing import Protocol

from fts.domain import Tournament


class TournamentRepository(Protocol):
    def load(self) -> Tournament | None:
        ...

    def save(self, tournament: Tournament) -> None:
        ...

    def delete(self) -> None:
        ...
