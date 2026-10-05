from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from uuid import UUID

from fts.domain import Match, ValidationError


@dataclass(frozen=True)
class ScheduledMatch:
    group_id: UUID
    match: Match
    slot: int
    table: int


def _players(match: Match) -> frozenset[UUID]:
    return frozenset((match.home_id, match.away_id))


def _ordered_matches(matches: Iterable[tuple[UUID, Match]]) -> list[tuple[UUID, Match]]:
    indexed = list(enumerate(matches))
    indexed.sort(key=lambda item: (item[1][1].round_number, item[0]))
    return [match for _index, match in indexed]


def build_fast_schedule(matches: Iterable[tuple[UUID, Match]], tables: int) -> list[ScheduledMatch]:
    """Create a compact schedule without ever double-booking a player.

    "Fast" intentionally skips the more expensive rest optimisation used by
    ``build_fair_schedule``.  It must nevertheless respect the hard invariant
    that one participant can only play one match per time slot.
    """
    if tables < 1:
        raise ValidationError("Es wird mindestens ein Feld benötigt.")

    remaining = list(matches)
    plan: list[ScheduledMatch] = []
    slot = 0
    while remaining:
        chosen: list[tuple[int, UUID, Match]] = []
        slot_players: set[UUID] = set()
        for index, (group_id, match) in enumerate(remaining):
            players = set(_players(match))
            if players & slot_players:
                continue
            chosen.append((index, group_id, match))
            slot_players.update(players)
            if len(chosen) >= tables:
                break

        if not chosen:
            index, (group_id, match) = 0, remaining[0]
            chosen = [(index, group_id, match)]

        for table, (_index, group_id, match) in enumerate(chosen, start=1):
            plan.append(ScheduledMatch(group_id=group_id, match=match, slot=slot, table=table))

        for index, _group_id, _match in sorted(chosen, reverse=True):
            remaining.pop(index)
        slot += 1

    return plan


def build_fair_schedule(matches: Iterable[tuple[UUID, Match]], tables: int) -> list[ScheduledMatch]:
    """Pack as many conflict-free matches as possible while protecting player rest.

    Goals, in order:
    1. never place one player on two tables in the same slot,
    2. avoid players appearing in consecutive slots whenever alternatives exist,
    3. use the available tables instead of deliberately leaving them idle,
    4. preserve a stable, round-oriented order as a tie breaker.
    """
    if tables < 1:
        raise ValidationError("Es wird mindestens ein Feld benötigt.")

    remaining = _ordered_matches(matches)
    plan: list[ScheduledMatch] = []
    previous_players: set[UUID] = set()
    slot = 0
    table_use = [0 for _ in range(tables)]

    while remaining:
        chosen: list[tuple[int, UUID, Match]] = []
        slot_players: set[UUID] = set()

        # First pass: fill tables with matches whose players also rested in the previous slot.
        for index, (group_id, match) in enumerate(remaining):
            players = set(_players(match))
            if players & slot_players:
                continue
            if players & previous_players:
                continue
            chosen.append((index, group_id, match))
            slot_players.update(players)
            if len(chosen) >= tables:
                break

        # Second pass: if tables are still free, use compatible matches with the smallest
        # possible back-to-back penalty. This avoids artificial idle time.
        if len(chosen) < tables:
            selected_indices = {index for index, _group_id, _match in chosen}
            candidates = []
            for index, (group_id, match) in enumerate(remaining):
                if index in selected_indices:
                    continue
                players = set(_players(match))
                if players & slot_players:
                    continue
                rest_penalty = len(players & previous_players)
                candidates.append((rest_penalty, match.round_number, index, group_id, match))
            candidates.sort(key=lambda item: (item[0], item[1], item[2]))
            for _penalty, _round_number, index, group_id, match in candidates:
                players = set(_players(match))
                if players & slot_players:
                    continue
                chosen.append((index, group_id, match))
                slot_players.update(players)
                if len(chosen) >= tables:
                    break

        if not chosen:
            # Defensive fallback; should only be reachable for malformed input.
            index, (group_id, match) = 0, remaining[0]
            chosen = [(index, group_id, match)]
            slot_players = set(_players(match))

        # Prefer the least-used physical tables to keep table utilization balanced.
        available_tables = sorted(range(1, tables + 1), key=lambda table: (table_use[table - 1], table))
        for table, (_index, group_id, match) in zip(available_tables, chosen):
            plan.append(ScheduledMatch(group_id, match, slot, table))
            table_use[table - 1] += 1

        for index, _group_id, _match in sorted(chosen, reverse=True):
            remaining.pop(index)

        previous_players = slot_players
        slot += 1

    return plan


def build_smart_schedule(matches: Iterable[tuple[UUID, Match]], tables: int) -> list[ScheduledMatch]:
    """Professional default: fair rest-aware scheduling with active table utilization."""
    return build_fair_schedule(matches, tables)


def assign_schedule(
    matches: Iterable[tuple[UUID, Match]],
    *,
    tables: int,
    start: datetime,
    duration_minutes: int,
    strategy: str = "fair",
) -> list[tuple[UUID, Match]]:
    if strategy == "fast":
        plan = build_fast_schedule(matches, tables)
    elif strategy == "fair":
        plan = build_fair_schedule(matches, tables)
    elif strategy == "smart":
        plan = build_smart_schedule(matches, tables)
    else:
        raise ValidationError("Unbekannte Planungsstrategie.")

    for item in plan:
        item.match.table_number = item.table
        item.match.scheduled_time = (
            start + timedelta(minutes=item.slot * duration_minutes)
        ).strftime("%H:%M")
    return [(item.group_id, item.match) for item in plan]
