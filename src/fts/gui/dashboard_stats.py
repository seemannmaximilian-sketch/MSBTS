from __future__ import annotations

from dataclasses import dataclass

from fts.domain import PhaseType, Tournament
from fts.tournament_health import inspect_tournament


@dataclass(frozen=True)
class DashboardStats:
    players: int
    groups: int
    matches_total: int
    matches_played: int
    matches_open: int
    progress_percent: int


def calculate_dashboard_stats(tournament: Tournament) -> DashboardStats:
    matches = []
    groups = 0
    for phase in tournament.phases:
        if phase.phase_type is PhaseType.GROUP_STAGE:
            groups += len(phase.groups)
            for group in phase.groups:
                matches.extend(group.matches)
        else:
            matches.extend(phase.matches)
    for competition in tournament.competitions:
        matches.extend(competition.matches)
    total = len(matches)
    played = sum(match.result is not None for match in matches)
    progress = round((played / total) * 100) if total else 0
    return DashboardStats(
        players=len(tournament.people),
        groups=groups,
        matches_total=total,
        matches_played=played,
        matches_open=max(total - played, 0),
        progress_percent=progress,
    )


@dataclass(frozen=True)
class TournamentReadiness:
    badge: str
    message: str


def calculate_readiness(tournament: Tournament) -> TournamentReadiness:
    """Return a concise, deterministic next-step recommendation."""
    stats = calculate_dashboard_stats(tournament)
    if stats.players < 2:
        return TournamentReadiness("1 · Teilnehmer", "Erfasse zuerst mindestens zwei Teilnehmer.")
    if stats.groups == 0 and not tournament.competitions:
        return TournamentReadiness("2 · Struktur", "Lege Gruppen oder einen Wettbewerb an und ordne die Teilnehmer zu.")
    if stats.matches_total == 0:
        return TournamentReadiness("3 · Spielplan", "Die Struktur steht. Erzeuge jetzt die Begegnungen und plane Felder sowie Zeiten.")
    if stats.matches_open == 0:
        final_phase = next(
            (phase for phase in sorted(tournament.phases, key=lambda item: item.position)
             if phase.phase_type is PhaseType.FINAL_ROUND),
            None,
        )
        if final_phase is not None and not final_phase.final_is_finished:
            if not final_phase.matches and len(final_phase.participant_ids) >= 2:
                label = final_phase.knockout_round_label(1)
                return TournamentReadiness(
                    f"Live · {label} starten",
                    f"Die vorherigen Spiele sind beendet. Starte jetzt das {label} im Turnierleitstand.",
                )
            return TournamentReadiness(
                "Live · K.-o.-Phase fortsetzen",
                "Die K.-o.-Phase ist noch nicht abgeschlossen. Öffne den Turnierleitstand und setze die nächste Runde fort.",
            )
        health = inspect_tournament(tournament)
        if health.errors:
            return TournamentReadiness(
                "! Prüfung erforderlich",
                f"Alle angelegten Spiele besitzen ein Ergebnis, aber {health.errors} kritische Konsistenzfehler verhindern den Turnierabschluss.",
            )
        return TournamentReadiness("✓ Abgeschlossen", "Alle angelegten Spiele besitzen ein Ergebnis und die Turnierdaten sind konsistent. Das Turnier kann ausgewertet und archiviert werden.")
    if stats.matches_played == 0:
        return TournamentReadiness("4 · Turniertag", "Der Spielplan ist bereit. Öffne den Livebetrieb und starte die ersten Begegnungen.")
    return TournamentReadiness("Live · Fortsetzen", f"{stats.matches_open} Spiele sind noch offen. Setze den Turniertag im Livebetrieb fort.")
