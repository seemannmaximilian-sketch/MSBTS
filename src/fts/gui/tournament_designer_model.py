from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fts.domain import PhaseType, Tournament


@dataclass(frozen=True)
class PhaseCardData:
    id: UUID
    title: str
    kind: str
    position: int
    detail: str
    next_phase_id: UUID | None
    auto_advance: bool
    distribution_mode: str


def build_phase_cards(tournament: Tournament) -> list[PhaseCardData]:
    cards: list[PhaseCardData] = []
    for phase in sorted(tournament.phases, key=lambda item: item.position):
        if phase.phase_type is PhaseType.GROUP_STAGE:
            players = sum(len(group.participant_ids) for group in phase.groups)
            detail = f"{len(phase.groups)} Gruppen · {players} Zuordnungen"
            kind = "Gruppenphase"
        else:
            detail = f"{len(phase.participant_ids)} Teilnehmer · {len(phase.matches)} Spiele"
            kind = "K.-o.-Phase"
        cards.append(
            PhaseCardData(
                phase.id,
                phase.name,
                kind,
                phase.position,
                detail,
                phase.next_phase_id,
                phase.auto_advance,
                phase.distribution_mode,
            )
        )
    return cards
