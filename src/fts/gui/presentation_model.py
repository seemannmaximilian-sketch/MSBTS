from __future__ import annotations

from dataclasses import dataclass

from fts.domain import LiveMatchStatus, PhaseType, Tournament
from fts.gui.live_center_model import LiveMatchRef, build_live_snapshot, knockout_phase_label
from fts.scorekeepers import build_scorekeeper_assignments


@dataclass(frozen=True)
class PresentationTable:
    table_number: int
    state: str
    phase_name: str
    group_name: str
    scheduled_time: str
    home_name: str
    away_name: str
    result_text: str
    scorer_name: str = ""


@dataclass(frozen=True)
class PresentationMatch:
    scheduled_time: str
    table_number: int
    phase_name: str
    group_name: str
    home_name: str
    away_name: str
    result_text: str = ""
    scorer_name: str = ""


@dataclass(frozen=True)
class PresentationSnapshot:
    tournament_name: str
    progress_percent: int
    progress_text: str
    current_phase: str
    tables: tuple[PresentationTable, ...]
    upcoming: tuple[PresentationMatch, ...]
    recent_results: tuple[PresentationMatch, ...]
    placements: tuple[str, str, str] = ("", "", "")


def _result_text(ref: LiveMatchRef) -> str:
    result = ref.match.result
    if result is None:
        return ""
    return f"{result.home_score}:{result.away_score}"


def _phase_label(tournament: Tournament, snapshot) -> str:
    phases_by_id = {phase.id: phase for phase in tournament.phases}
    for progress in snapshot.phase_progress:
        if progress.completed_matches < progress.total_matches:
            actual = phases_by_id.get(progress.phase_id)
            if actual is not None and actual.phase_type is PhaseType.FINAL_ROUND:
                return knockout_phase_label(actual)
            return progress.phase_name

    final_phase = next(
        (phase for phase in sorted(tournament.phases, key=lambda item: item.position)
         if phase.phase_type is PhaseType.FINAL_ROUND),
        None,
    )
    if final_phase is not None:
        label = knockout_phase_label(final_phase)
        if label == "Turnier abgeschlossen":
            return "Turnier beendet"
        return label

    if snapshot.phase_progress:
        return "Turnier beendet"
    return "Turniervorbereitung"


def _placements(tournament: Tournament) -> tuple[str, str, str]:
    final_phase = next(
        (phase for phase in tournament.phases if phase.phase_type is PhaseType.FINAL_ROUND),
        None,
    )
    if final_phase is None or not final_phase.matches:
        return "", "", ""
    final_match = next((match for match in final_phase.matches if match.match_kind == "final"), None)
    third_match = next((match for match in final_phase.matches if match.match_kind == "third_place"), None)
    if final_match is None or final_match.result is None:
        return "", "", ""

    first_id = final_match.winner_id()
    second_id = final_match.loser_id()
    third_id = third_match.winner_id() if third_match is not None and third_match.result is not None else None

    def name(person_id) -> str:
        if person_id is None:
            return ""
        person = tournament.person(person_id)
        return f"{person.first_name} {person.last_name}".strip()

    return name(first_id), name(second_id), name(third_id)


def build_presentation_snapshot(tournament: Tournament) -> PresentationSnapshot:
    live = build_live_snapshot(tournament)
    scorer_assignments = build_scorekeeper_assignments(tournament)
    tables: list[PresentationTable] = []
    for table in live.table_statuses:
        ref = table.match_ref
        if ref is None:
            tables.append(PresentationTable(
                table.table_number, "frei", "", "", "--:--", "Feld frei", "", "", ""
            ))
            continue
        state = {
            LiveMatchStatus.RUNNING: "LÄUFT",
            LiveMatchStatus.RESULT_PENDING: "ERGEBNIS FEHLT",
            LiveMatchStatus.PREPARING: "VORBEREITUNG",
            LiveMatchStatus.PLANNED: "GEPLANT",
        }.get(ref.match.live_status, ref.match.live_status.label.upper())
        assignment = scorer_assignments.get(ref.match.id)
        tables.append(PresentationTable(
            table.table_number, state, ref.phase_name, ref.group_name,
            ref.scheduled_time, ref.home_name, ref.away_name, _result_text(ref),
            assignment.scorer_name if assignment is not None else "",
        ))

    active_ids = {table.match_ref.match.id for table in live.table_statuses if table.match_ref is not None}
    upcoming_refs = [ref for ref in live.open_matches if ref.match.id not in active_ids][:8]
    upcoming = tuple(PresentationMatch(
        ref.scheduled_time, ref.table_number, ref.phase_name, ref.group_name,
        ref.home_name, ref.away_name, "",
        scorer_assignments.get(ref.match.id).scorer_name if scorer_assignments.get(ref.match.id) is not None else "",
    ) for ref in upcoming_refs)

    completed_refs = sorted(
        live.completed,
        key=lambda ref: (ref.match.finished_at or "", ref.scheduled_time, str(ref.match.id)),
        reverse=True,
    )[:6]
    recent = tuple(PresentationMatch(
        ref.scheduled_time, ref.table_number, ref.phase_name, ref.group_name,
        ref.home_name, ref.away_name, _result_text(ref),
        scorer_assignments.get(ref.match.id).scorer_name if scorer_assignments.get(ref.match.id) is not None else "",
    ) for ref in completed_refs)

    return PresentationSnapshot(
        tournament.name,
        live.progress_percent,
        f"{live.completed_matches} von {live.total_matches} Spielen abgeschlossen",
        _phase_label(tournament, live),
        tuple(tables), upcoming, recent, _placements(tournament),
    )
