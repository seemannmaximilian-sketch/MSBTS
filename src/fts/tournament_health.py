from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from fts.domain import PhaseType, Tournament


class HealthSeverity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class HealthIssue:
    severity: HealthSeverity
    title: str
    message: str
    area: str


@dataclass(frozen=True)
class TournamentHealth:
    issues: tuple[HealthIssue, ...]

    @property
    def errors(self) -> int:
        return sum(issue.severity is HealthSeverity.ERROR for issue in self.issues)

    @property
    def warnings(self) -> int:
        return sum(issue.severity is HealthSeverity.WARNING for issue in self.issues)

    @property
    def is_ready(self) -> bool:
        return self.errors == 0

    @property
    def summary(self) -> str:
        if self.errors:
            return f"{self.errors} kritische Punkte müssen geprüft werden"
        if self.warnings:
            return f"Turnier stabil · {self.warnings} Hinweise vorhanden"
        return "Turnierdaten vollständig und konsistent"


def inspect_tournament(tournament: Tournament) -> TournamentHealth:
    """Perform fast, deterministic consistency checks without mutating data."""
    issues: list[HealthIssue] = []
    people_ids = {person.id for person in tournament.people}

    if len(people_ids) != len(tournament.people):
        issues.append(HealthIssue(HealthSeverity.ERROR, "Doppelte Spieler-ID", "Mindestens zwei Teilnehmer verwenden dieselbe interne ID.", "Teilnehmer"))

    start_numbers = [person.start_number for person in tournament.people if person.start_number is not None]
    if len(start_numbers) != len(set(start_numbers)):
        issues.append(HealthIssue(HealthSeverity.ERROR, "Doppelte Startnummer", "Startnummern müssen eindeutig sein.", "Teilnehmer"))

    assigned_by_phase: dict[UUID, set[UUID]] = {}
    all_matches = []
    for phase in tournament.phases:
        seen: set[UUID] = set()
        if phase.phase_type is PhaseType.GROUP_STAGE:
            for group in phase.groups:
                unknown = set(group.participant_ids) - people_ids
                if unknown:
                    issues.append(HealthIssue(HealthSeverity.ERROR, "Unbekannter Teilnehmer", f"{group.name} enthält {len(unknown)} nicht mehr vorhandene Teilnehmer.", "Gruppen"))
                duplicate = seen.intersection(group.participant_ids)
                if duplicate:
                    issues.append(HealthIssue(HealthSeverity.ERROR, "Mehrfachzuordnung", f"In {phase.name} sind {len(duplicate)} Teilnehmer mehreren Gruppen zugeordnet.", "Gruppen"))
                seen.update(group.participant_ids)
                if len(group.participant_ids) == 1:
                    issues.append(HealthIssue(HealthSeverity.WARNING, "Gruppe nicht spielbar", f"{group.name} enthält nur einen Teilnehmer.", "Gruppen"))
                all_matches.extend(group.matches)
                if group.playoff_match is not None:
                    all_matches.append(group.playoff_match)
        else:
            unknown = set(phase.participant_ids) - people_ids
            if unknown:
                issues.append(HealthIssue(HealthSeverity.ERROR, "Ungültige Finalrunde", f"{phase.name} enthält {len(unknown)} unbekannte Teilnehmer.", "Finalrunde"))
            all_matches.extend(phase.matches)
        assigned_by_phase[phase.id] = seen

    for competition in tournament.competitions:
        unknown = set(competition.registered_ids) - people_ids
        if unknown:
            issues.append(HealthIssue(HealthSeverity.ERROR, "Ungültige Anmeldung", f"{competition.name} enthält {len(unknown)} unbekannte Teilnehmer.", "Wettbewerbe"))
        all_matches.extend(competition.matches)

    match_ids = [match.id for match in all_matches]
    if len(match_ids) != len(set(match_ids)):
        issues.append(HealthIssue(HealthSeverity.ERROR, "Doppelte Spiel-ID", "Mindestens zwei Begegnungen verwenden dieselbe interne ID.", "Spielplan"))

    for match in all_matches:
        if match.home_id == match.away_id:
            issues.append(HealthIssue(HealthSeverity.ERROR, "Ungültige Begegnung", "Ein Teilnehmer wurde gegen sich selbst angesetzt.", "Spielplan"))
        if match.home_id not in people_ids or match.away_id not in people_ids:
            issues.append(HealthIssue(HealthSeverity.ERROR, "Fehlender Spieler", "Eine Begegnung verweist auf einen gelöschten Teilnehmer.", "Spielplan"))
        if match.table_number is not None and not 1 <= match.table_number <= tournament.table_count:
            issues.append(HealthIssue(HealthSeverity.WARNING, "Feld außerhalb der Einstellung", f"Eine Begegnung ist Feld {match.table_number} zugeordnet, eingestellt sind {tournament.table_count} Felder.", "Spielplan"))

    playable_groups = sum(
        len(group.participant_ids) >= 2
        for phase in tournament.phases if phase.phase_type is PhaseType.GROUP_STAGE
        for group in phase.groups
    )
    if tournament.people and not tournament.phases and not tournament.competitions:
        issues.append(HealthIssue(HealthSeverity.INFO, "Struktur fehlt", "Teilnehmer sind vorhanden, aber noch keine Turnierstruktur.", "Turnieraufbau"))
    elif tournament.phases and playable_groups == 0 and not tournament.competitions:
        issues.append(HealthIssue(HealthSeverity.WARNING, "Keine spielbereite Gruppe", "Ordne mindestens zwei Teilnehmer einer Gruppe zu.", "Gruppen"))

    if not issues:
        issues.append(HealthIssue(HealthSeverity.OK, "Alles in Ordnung", "Die automatische Prüfung hat keine Widersprüche gefunden.", "System"))
    return TournamentHealth(tuple(issues))
