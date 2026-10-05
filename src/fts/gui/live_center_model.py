from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from fts.domain import LiveMatchStatus, Match, PhaseType, Tournament


@dataclass(frozen=True)
class LiveMatchRef:
    phase_id: UUID
    phase_name: str
    group_id: UUID | None
    group_name: str
    match: Match
    home_name: str
    away_name: str

    @property
    def table_number(self) -> int:
        return self.match.table_number or 0

    @property
    def scheduled_time(self) -> str:
        return self.match.scheduled_time or "--:--"


@dataclass(frozen=True)
class PhaseProgress:
    phase_id: UUID
    phase_name: str
    total_matches: int
    completed_matches: int

    @property
    def progress_percent(self) -> int:
        return round(self.completed_matches * 100 / self.total_matches) if self.total_matches else 0


@dataclass(frozen=True)
class TableStatus:
    table_number: int
    state: str
    match_ref: LiveMatchRef | None


@dataclass(frozen=True)
class FlowWarning:
    code: str
    severity: str
    message: str
    match_ids: tuple[UUID, ...] = ()
    action_key: str = ""
    action_label: str = ""


@dataclass(frozen=True)
class LiveSnapshot:
    total_matches: int
    completed_matches: int
    open_matches: tuple[LiveMatchRef, ...]
    completed: tuple[LiveMatchRef, ...]
    running: tuple[LiveMatchRef, ...]
    preparing: tuple[LiveMatchRef, ...]
    result_pending: tuple[LiveMatchRef, ...]
    phase_progress: tuple[PhaseProgress, ...]
    table_statuses: tuple[TableStatus, ...]
    now_matches: tuple[LiveMatchRef, ...]
    next_matches: tuple[LiveMatchRef, ...]
    later_matches: tuple[LiveMatchRef, ...]
    overdue_matches: tuple[LiveMatchRef, ...]
    warnings: tuple[FlowWarning, ...]

    @property
    def progress_percent(self) -> int:
        return round(self.completed_matches * 100 / self.total_matches) if self.total_matches else 0



def knockout_phase_label(phase) -> str:
    """Return the real current label for a final-round phase.

    Final rounds store matches in one flat list and create later rounds only
    after the previous round is complete.  Do not infer completion from an
    absent ``rounds`` attribute: older live-center code did that and could
    mark a prepared KO phase as finished before the quarterfinals existed.
    """
    if phase.phase_type is not PhaseType.FINAL_ROUND:
        return phase.name
    if phase.final_is_finished:
        return "Turnier abgeschlossen"
    if not phase.matches:
        if len(phase.participant_ids) >= 2:
            return phase.knockout_round_label(1)
        return "K.-o.-Phase vorbereiten"
    open_matches = [match for match in phase.matches if match.result is None]
    if open_matches:
        round_number = max(match.round_number for match in open_matches)
        return phase.knockout_round_label(round_number)
    # Results exist but the phase is not formally finished.  This is an
    # inconsistent/intermediate state and must never be presented as done.
    return "K.-o.-Phase prüfen"

def build_live_snapshot(
    tournament: Tournament,
    now: datetime | None = None,
    phase_id: UUID | None = None,
) -> LiveSnapshot:
    now = now or datetime.now()
    people = {person.id: person.full_name for person in tournament.people}
    refs: list[LiveMatchRef] = []
    for phase in sorted(tournament.phases, key=lambda item: item.position):
        if phase_id is not None and phase.id != phase_id:
            continue
        if phase.phase_type is PhaseType.GROUP_STAGE:
            for group in phase.groups:
                for match in group.matches:
                    refs.append(LiveMatchRef(
                        phase.id, phase.name, group.id, group.name, match,
                        people.get(match.home_id, "Unbekannt"), people.get(match.away_id, "Unbekannt"),
                    ))
                if group.playoff_match is not None:
                    match = group.playoff_match
                    refs.append(LiveMatchRef(
                        phase.id, phase.name, group.id, f"{group.name} · Qualifikation", match,
                        people.get(match.home_id, "Unbekannt"), people.get(match.away_id, "Unbekannt"),
                    ))
        else:
            for match in phase.matches:
                ko_label = "Spiel um Platz 3" if match.match_kind == "third_place" else ("Finale" if match.match_kind == "final" else "K.-o.-Phase")
                refs.append(LiveMatchRef(
                    phase.id, phase.name, None, ko_label, match,
                    people.get(match.home_id, "Unbekannt"), people.get(match.away_id, "Unbekannt"),
                ))

    def key(ref: LiveMatchRef) -> tuple[str, int, int, str]:
        time = ref.match.scheduled_time or "99:99"
        table = ref.match.table_number or 999
        return time, table, ref.match.round_number, str(ref.match.id)

    refs.sort(key=key)
    opened = tuple(ref for ref in refs if ref.match.result is None)
    completed = tuple(ref for ref in refs if ref.match.result is not None)
    running = tuple(ref for ref in opened if ref.match.live_status is LiveMatchStatus.RUNNING)
    preparing = tuple(ref for ref in opened if ref.match.live_status is LiveMatchStatus.PREPARING)
    result_pending = tuple(ref for ref in opened if ref.match.live_status is LiveMatchStatus.RESULT_PENDING)

    phase_rows: list[PhaseProgress] = []
    for phase in sorted(tournament.phases, key=lambda item: item.position):
        phase_refs = [ref for ref in refs if ref.phase_id == phase.id]
        if phase_refs:
            phase_rows.append(PhaseProgress(
                phase.id, phase.name, len(phase_refs),
                sum(ref.match.result is not None for ref in phase_refs),
            ))

    highest_table = max([tournament.table_count, *(ref.table_number for ref in refs)])
    table_rows: list[TableStatus] = []
    priority = {
        LiveMatchStatus.RUNNING: 0,
        LiveMatchStatus.RESULT_PENDING: 1,
        LiveMatchStatus.PREPARING: 2,
        LiveMatchStatus.PLANNED: 3,
    }
    for table_number in range(1, highest_table + 1):
        candidates = [ref for ref in opened if ref.table_number == table_number]
        candidates.sort(key=lambda ref: (priority.get(ref.match.live_status, 9), key(ref)))
        active = candidates[0] if candidates else None
        state = 'frei'
        if active is not None:
            state = {
                LiveMatchStatus.RUNNING: 'läuft',
                LiveMatchStatus.RESULT_PENDING: 'Ergebnis fehlt',
                LiveMatchStatus.PREPARING: 'Vorbereitung',
                LiveMatchStatus.PLANNED: 'geplant',
            }.get(active.match.live_status, active.match.live_status.value)
        table_rows.append(TableStatus(table_number, state, active))

    now_matches = tuple(ref for ref in opened if ref.match.live_status in {
        LiveMatchStatus.PREPARING, LiveMatchStatus.RUNNING, LiveMatchStatus.RESULT_PENDING
    })
    planned = [ref for ref in opened if ref.match.live_status is LiveMatchStatus.PLANNED]

    def is_overdue(ref: LiveMatchRef) -> bool:
        value = ref.match.scheduled_time.strip()
        if not value:
            return False
        try:
            scheduled = datetime.strptime(value, "%H:%M").replace(
                year=now.year, month=now.month, day=now.day
            )
        except ValueError:
            return False
        return scheduled < now

    overdue_matches = tuple(ref for ref in planned if is_overdue(ref))
    on_time_planned = [ref for ref in planned if ref not in overdue_matches]
    next_matches = tuple([*overdue_matches, *on_time_planned][:6])
    later_matches = tuple([*overdue_matches, *on_time_planned][6:])

    warnings: list[FlowWarning] = []

    # A player must never be scheduled in two matches at the same time.
    by_slot: dict[str, list[LiveMatchRef]] = {}
    for ref in opened:
        if ref.match.scheduled_time:
            by_slot.setdefault(ref.match.scheduled_time, []).append(ref)
    for slot, slot_refs in sorted(by_slot.items()):
        player_matches: dict[UUID, list[LiveMatchRef]] = {}
        for ref in slot_refs:
            player_matches.setdefault(ref.match.home_id, []).append(ref)
            player_matches.setdefault(ref.match.away_id, []).append(ref)
        for player_id, conflicts in player_matches.items():
            if len(conflicts) > 1:
                name = people.get(player_id, "Unbekannt")
                warnings.append(FlowWarning(
                    "player_overlap", "critical",
                    f"Spielerüberschneidung um {slot}: {name} ist in {len(conflicts)} Spielen eingeplant.",
                    tuple(ref.match.id for ref in conflicts),
                    "reflow_conflict", "Ab Konflikt neu planen",
                ))

    # With 15-minute matches, starts less than 30 minutes apart leave under 15 minutes rest.
    player_schedule: dict[UUID, list[tuple[datetime, LiveMatchRef]]] = {}
    for ref in opened:
        try:
            start = datetime.strptime(ref.match.scheduled_time, "%H:%M")
        except (TypeError, ValueError):
            continue
        player_schedule.setdefault(ref.match.home_id, []).append((start, ref))
        player_schedule.setdefault(ref.match.away_id, []).append((start, ref))
    for player_id, entries in player_schedule.items():
        entries.sort(key=lambda item: item[0])
        for (first_time, first_ref), (second_time, second_ref) in zip(entries, entries[1:]):
            gap = int((second_time - first_time).total_seconds() // 60)
            if 0 < gap < 30:
                name = people.get(player_id, "Unbekannt")
                warnings.append(FlowWarning(
                    "short_break", "warning",
                    f"Zu kurze Pause für {name}: nur {max(0, gap - 15)} Minuten zwischen den Spielen.",
                    (first_ref.match.id, second_ref.match.id),
                    "reflow_conflict", "Pause automatisch herstellen",
                ))

    # Escalate matches that are more than 15 minutes behind schedule.
    for ref in overdue_matches:
        scheduled = datetime.strptime(ref.match.scheduled_time, "%H:%M").replace(
            year=now.year, month=now.month, day=now.day
        )
        delay_minutes = int((now - scheduled).total_seconds() // 60)
        if delay_minutes >= 15:
            warnings.append(FlowWarning(
                "major_delay", "critical",
                f"Größere Verzögerung: {ref.home_name} – {ref.away_name} ist {delay_minutes} Minuten überfällig.",
                (ref.match.id,),
                "reflow_now", "Zeitplan ab jetzt anpassen",
            ))

    free_tables = [row.table_number for row in table_rows if row.state == "frei"]
    if planned and free_tables:
        table_text = ", ".join(str(number) for number in free_tables)
        warnings.append(FlowWarning(
            "idle_tables", "info",
            f"Freie Felder trotz Warteschlange: Feld {table_text}. Das nächste Spiel kann aufgerufen werden.",
            (), "call_next", "Nächstes Spiel aufrufen",
        ))

    severity_order = {"critical": 0, "warning": 1, "info": 2}
    warnings.sort(key=lambda item: (severity_order.get(item.severity, 9), item.code, item.message))

    return LiveSnapshot(
        len(refs), len(completed), opened, completed, running, preparing, result_pending,
        tuple(phase_rows), tuple(table_rows), now_matches, next_matches, later_matches, overdue_matches,
        tuple(warnings),
    )
