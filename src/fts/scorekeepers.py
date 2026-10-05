from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from uuid import UUID

from fts.domain import PhaseType, Tournament


@dataclass(frozen=True)
class ScorekeeperAssignment:
    match_id: UUID
    scorer_name: str
    table_number: int | None
    scheduled_time: str
    phase_name: str
    group_name: str


def normalize_scorer_names(names) -> list[str]:
    """Return clean, unique scorer names while preserving input order."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in names or ():
        name = " ".join(str(raw).strip().split())
        key = _name_key(name)
        if not name or key in seen:
            continue
        result.append(name)
        seen.add(key)
    return result


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", "", value.casefold())


def _name_parts(value: str) -> tuple[str, str]:
    """Return normalized first name and surname/initial for identity checks."""
    parts = re.findall(r"[a-z0-9äöüß]+", str(value or "").casefold())
    if not parts:
        return "", ""
    return parts[0], parts[-1] if len(parts) > 1 else ""


def scorer_matches_player(scorer_name: str, player_name: str) -> bool:
    """Match full names as well as UI abbreviations such as ``Sidney T.``.

    The tournament UI commonly displays/stores abbreviated surnames.  Exact-key
    comparison alone would therefore allow a scorer named ``Sidney T.`` to be
    assigned while participant ``Sidney Tschee`` is playing.
    """
    if not scorer_name or not player_name:
        return False
    if _name_key(scorer_name) == _name_key(player_name):
        return True
    scorer_first, scorer_last = _name_parts(scorer_name)
    player_first, player_last = _name_parts(player_name)
    if not scorer_first or scorer_first != player_first or not scorer_last or not player_last:
        return False
    return scorer_last == player_last or scorer_last.startswith(player_last) or player_last.startswith(scorer_last)


def _iter_matches(tournament: Tournament):
    for phase in sorted(tournament.phases, key=lambda value: value.position):
        if phase.phase_type is PhaseType.GROUP_STAGE:
            for group in phase.groups:
                for match in group.matches:
                    yield phase.name, group.name, match
                if group.playoff_match is not None:
                    yield phase.name, f"{group.name} Qualifikation", group.playoff_match
        else:
            for match in phase.matches:
                yield phase.name, "K.-o.-Runde", match


def build_scorekeeper_assignments(tournament: Tournament) -> dict[UUID, ScorekeeperAssignment]:
    """Distribute named scorekeepers fairly over the scheduled matches.

    The allocator is deterministic, balances the number of duties, rotates tables,
    never double-books one scorekeeper in the same time slot and avoids assigning a
    scorekeeper who is also playing in that time slot (when names match a participant).
    If too few scorekeepers are available for parallel tables, unmatched games remain
    intentionally unassigned instead of double-booking somebody.
    """
    scorers = normalize_scorer_names(getattr(tournament, "scorer_names", ()))
    if not scorers:
        return {}

    name_by_id = {person.id: person.full_name for person in tournament.people}
    duties = {name: 0 for name in scorers}
    last_table: dict[str, int | None] = {name: None for name in scorers}
    last_slot: dict[str, str] = {name: "" for name in scorers}

    rows = list(_iter_matches(tournament))
    rows.sort(key=lambda row: (
        row[2].scheduled_time or "99:99",
        row[2].table_number or 999,
        row[0], row[1], row[2].round_number, str(row[2].id),
    ))

    by_slot: dict[str, list[tuple[str, str, object]]] = defaultdict(list)
    for index, row in enumerate(rows):
        match = row[2]
        # Unscheduled matches are handled as separate slots so they do not block each other.
        slot = match.scheduled_time.strip() if match.scheduled_time else f"__unscheduled_{index:05d}"
        by_slot[slot].append(row)

    assignments: dict[UUID, ScorekeeperAssignment] = {}
    for slot in sorted(by_slot, key=lambda value: (value.startswith("__unscheduled_"), value)):
        slot_rows = sorted(by_slot[slot], key=lambda row: (row[2].table_number or 999, str(row[2].id)))
        busy_player_names: list[str] = []
        for _, _, match in slot_rows:
            busy_player_names.append(name_by_id.get(match.home_id, ""))
            busy_player_names.append(name_by_id.get(match.away_id, ""))
        used_in_slot: set[str] = set()

        for phase_name, group_name, match in slot_rows:
            candidates = [
                name for name in scorers
                if name not in used_in_slot
                and not any(scorer_matches_player(name, player_name) for player_name in busy_player_names)
            ]
            if not candidates:
                continue
            candidates.sort(key=lambda name: (
                duties[name],
                1 if last_table[name] == match.table_number and match.table_number is not None else 0,
                1 if last_slot[name] == slot else 0,
                scorers.index(name),
            ))
            chosen = candidates[0]
            used_in_slot.add(chosen)
            duties[chosen] += 1
            last_table[chosen] = match.table_number
            last_slot[chosen] = slot
            assignments[match.id] = ScorekeeperAssignment(
                match_id=match.id,
                scorer_name=chosen,
                table_number=match.table_number,
                scheduled_time=match.scheduled_time,
                phase_name=phase_name,
                group_name=group_name,
            )
    return assignments
