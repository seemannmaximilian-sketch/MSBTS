from __future__ import annotations

import csv
import html
import base64
import binascii
import re
import json
import shutil
import hashlib
import tempfile
import zipfile
import random

try:
    from openpyxl import load_workbook
except ImportError:  # optional until Excel import is used
    load_workbook = None
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from fts.commands import BaseCommand, CommandManager
from fts.domain import Group, LiveMatchStatus, PhaseType, TournamentPhase, Competition, CompetitionFormat, CompetitionStatus, Match, MatchResult, Person, Standing, Team, TeamMember, BoardGame, TeamMatch, TeamCompetition, PlayingArea, ScheduleItem, Tournament, ValidationError
from fts.repository.protocol import TournamentRepository
from fts.scheduler import assign_schedule
from fts.documents import (export_document_center, export_selected_documents, DocumentExportResult, DocumentSelection, DocumentProfile, PrintSettings, BrandingProfile, built_in_document_profiles, built_in_branding_profiles, html_to_pdf)


@dataclass(frozen=True)
class TournamentCheckIssue:
    severity: str
    title: str
    details: str


@dataclass(frozen=True)
class QualificationResult:
    source_phase_id: UUID
    source_group_id: UUID
    target_phase_id: UUID
    target_group_id: UUID
    qualified_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class BulkQualificationResult:
    source_phase_id: UUID
    target_phase_id: UUID
    assignments: dict[UUID, tuple[UUID, ...]]

    @property
    def qualified_ids(self) -> tuple[UUID, ...]:
        return tuple(person_id for players in self.assignments.values() for person_id in players)




@dataclass(frozen=True)
class PhaseAdvancePreview:
    source_phase_id: UUID
    target_phase_id: UUID
    qualified_ids: tuple[UUID, ...]
    assignments: dict[UUID, tuple[UUID, ...]]


@dataclass(frozen=True)
class PhaseAdvanceResult:
    source_phase_id: UUID
    target_phase_id: UUID
    qualified_ids: tuple[UUID, ...]
    assignments: dict[UUID, tuple[UUID, ...]]

@dataclass(frozen=True)
class PlayerImportPreview:
    players: list[tuple[str, str, str, str, int | None, str]]
    duplicates: list[tuple[str, str, str, str, int | None, str]]
    invalid_rows: list[int]


@dataclass(frozen=True)
class TournamentStructureResult:
    first_phase_id: UUID
    second_phase_id: UUID | None
    final_phase_id: UUID | None




@dataclass(frozen=True)
class ScheduleAdjustmentResult:
    """Summary of one automatic live schedule recalculation."""

    adjusted_match_ids: tuple[UUID, ...]
    start_time: str
    end_time: str
    table_count: int

    @property
    def adjusted_count(self) -> int:
        return len(self.adjusted_match_ids)


@dataclass(frozen=True)
class TournamentSimulationReport:
    """Summary of a complete deterministic tournament test run."""

    simulated_matches: int
    created_schedules: int
    advanced_phases: int
    winner_id: UUID | None
    completed: bool
    log: tuple[str, ...]


@dataclass(frozen=True)
class LiveResultOutcome:
    """Result of one atomic result-entry operation during live play."""

    phase_id: UUID
    group_id: UUID | None
    match_id: UUID
    winner_id: UUID
    home_sets: int
    away_sets: int
    standings: tuple[Standing, ...]
    created_match_ids: tuple[UUID, ...]
    phase_finished: bool


class TournamentEngine:
    """Central application facade coordinating tournament use cases and persistence.

    GUI code should depend on this class instead of manipulating domain objects
    or repositories directly.
    """

    def __init__(self, repository: TournamentRepository) -> None:
        self.repository = repository
        self.tournament = repository.load()
        self.commands = CommandManager()

    @property
    def can_undo(self) -> bool:
        return self.commands.can_undo

    @property
    def can_redo(self) -> bool:
        return self.commands.can_redo

    def execute(self, command: BaseCommand) -> BaseCommand:
        executed = self.commands.execute(command)
        self.save()
        return executed

    def undo(self) -> BaseCommand:
        command = self.commands.undo()
        self.save()
        return command

    def redo(self) -> BaseCommand:
        command = self.commands.redo()
        self.save()
        return command

    def require_tournament(self) -> Tournament:
        if self.tournament is None:
            raise ValidationError("Es ist kein Turnier geladen.")
        return self.tournament

    def create_tournament(self, name: str, replace: bool = False) -> Tournament:
        tournament = Tournament(name)
        if replace:
            self.repository.delete()
        self.repository.save(tournament)
        self.tournament = tournament
        return tournament

    def create_demo_tournament(self, replace: bool = False) -> Tournament:
        """Create a complete Badminton demo including qualification and intermediate round."""
        tournament = self.create_tournament("MSBTS Badminton-Demoturnier", replace=replace)
        self.update_tournament_details(
            organizer="M. Seemann Badminton Tournament System",
            location="Demo-Sporthalle",
            start_date="Testbetrieb",
            table_count=3,
            match_duration_minutes=15,
            scorer_count=4,
            best_of=3,
        )

        players = [
            ("Anna", "Becker"), ("Ben", "Fischer"), ("Clara", "Hoffmann"),
            ("David", "Klein"), ("Emma", "Lorenz"), ("Felix", "Meyer"),
            ("Greta", "Neumann"), ("Hannes", "Schulz"), ("Ida", "Vogel"),
            ("Jonas", "Wagner"), ("Klara", "Braun"), ("Leon", "Hartmann"),
            ("Mia", "Koch"), ("Noah", "Richter"), ("Olivia", "Wolf"),
            ("Paul", "Zimmermann"), ("Romy", "Schmitt"), ("Samuel", "Krueger"),
            ("Tara", "Lehmann"), ("Vincent", "Werner"), ("Zoe", "Krause"),
        ]
        for number, (first_name, last_name) in enumerate(players, start=1):
            self.add_player(first_name, last_name, "Demo Club", "Offen", start_number=number)

        structure = self.configure_tournament_structure(
            first_group_count=5,
            first_qualifiers_per_group=2,
            use_second_group_stage=True,
            second_group_count=4,
            second_qualifiers_per_group=2,
            use_final_round=True,
            replace_existing=True,
        )
        first_phase = tournament.phase(structure.first_phase_id)
        groups = first_phase.groups

        # One group of five and four groups of four creates a 16-player
        # intermediate round: top 2 direct, places 3/4 via qualification,
        # plus four direct qualifiers from the five-player group.
        for person in tournament.people[:5]:
            groups[0].add_participant(person.id)
        for offset, person in enumerate(tournament.people[5:]):
            groups[1 + (offset // 4)].add_participant(person.id)

        groups[0].qualification_count = 4
        for group in groups[1:]:
            group.qualification_count = 2

        tournament.qualification_enabled = True
        tournament.intermediate_enabled = True
        self.sync_intermediate_qualification_playoffs(first_phase.id)

        self.generate_phase_schedule(
            first_phase.id,
            tables=3,
            start_time="17:00",
            duration_minutes=15,
            strategy="fair",
            replace=True,
        )
        self.save()
        return tournament

    def create_freudenholm_demo(self, replace: bool = False) -> Tournament:
        """Create the Freudenholm 2026 structure with all 21 known participants."""
        tournament = self.create_tournament("Freudenholm 2026 – Testturnier", replace=replace)
        self.update_tournament_details(
            organizer="Sidney Tschee und Max Seemann",
            location="Turnhalle",
            start_date="21.07.2026",
            end_date="23.07.2026",
        )

        players = [
            ("Petra", "Lühr", "", "Frauen"),
            ("Swantje", "Lehswing", "", "Frauen"),
            ("Nicole", "Langermann", "", "Frauen"),
            ("Inga", "Rüthemann", "", "Frauen"),
            ("Bella", "Betz", "", "Frauen"),
            ("Karl", "Kinzel", "", "Männer"),
            ("Helmut", "Wirsing", "", "Männer"),
            ("René", "Pott", "", "Männer"),
            ("Jan", "Hennig", "", "Männer"),
            ("Malte", "Wirth", "", "Männer"),
            ("Marc", "Halsband", "", "Männer"),
            ("Daniel", "Kirschbaum", "", "Männer"),
            ("Alex", "Kaufmann", "", "Männer"),
            ("Thies", "Wittke", "", "Männer"),
            ("Tobias", "Hofmann", "", "Männer"),
            ("Marko", "Puppe", "", "Männer"),
            ("Mike", "Riedl", "", "Männer"),
            ("Waldemar", "Rau", "", "Männer"),
            ("Sidney", "Tschee", "", "Männer"),
            ("Lukasz", "Potrykus", "", "Männer"),
            ("Thorben", "Krüger", "", "Männer"),
        ]
        for number, (first_name, last_name, club, category) in enumerate(players, start=1):
            self.add_player(first_name, last_name, club, category, start_number=number)

        structure = self.configure_tournament_structure(
            first_group_count=5,
            first_qualifiers_per_group=2,
            use_second_group_stage=True,
            second_group_count=4,
            second_qualifiers_per_group=2,
            use_final_round=True,
            replace_existing=True,
        )

        first_phase = tournament.phase(structure.first_phase_id)
        groups = first_phase.groups

        # Gruppe A: Frauen, Gruppen B-E: je vier Männer.
        for person in tournament.people[:5]:
            groups[0].add_participant(person.id)
        for offset, person in enumerate(tournament.people[5:]):
            groups[1 + (offset // 4)].add_participant(person.id)

        groups[0].qualification_count = 4
        for group in groups[1:]:
            group.qualification_count = 2

        self.generate_phase_schedule(
            first_phase.id,
            tables=3,
            start_time="17:10",
            duration_minutes=15,
            strategy="fair",
            replace=True,
        )
        self.save()
        return tournament

    def save(self) -> None:
        self.repository.save(self.require_tournament())

    def rename_tournament(self, name: str) -> None:
        self.require_tournament().rename(name)
        self.save()

    def update_tournament_details(
        self, organizer: str = "", location: str = "", start_date: str = "", end_date: str = "",
        table_count: int = 3, match_duration_minutes: int = 15, scorer_count: int = 6, best_of: int = 3, scorer_names: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.require_tournament().update_details(organizer, location, start_date, end_date, table_count, match_duration_minutes, scorer_count, best_of, scorer_names)
        self.save()

    def phase_option_availability(self) -> tuple[bool, bool]:
        tournament = self.require_tournament()
        group_phases = sorted(
            (phase for phase in tournament.phases if phase.phase_type is PhaseType.GROUP_STAGE),
            key=lambda phase: phase.position,
        )
        first = group_phases[0] if group_phases else None
        groups = first.groups if first is not None else []
        qualification_available = any(len(group.participant_ids) >= 2 for group in groups)
        assigned = {person_id for group in groups for person_id in group.participant_ids}
        # Legacy optional-phase switch remains compatible with existing projects.
        # Smaller/custom intermediate rounds are enabled explicitly by the variable designer.
        intermediate_available = len(assigned) >= 16
        return qualification_available, intermediate_available

    def set_optional_phases(self, *, qualification_enabled: bool, intermediate_enabled: bool) -> None:
        tournament = self.require_tournament()
        qualification_available, intermediate_available = self.phase_option_availability()
        if qualification_enabled and not qualification_available:
            raise ValidationError("Qualifikation ist erst verfügbar, wenn mindestens eine Gruppe vier Spieler enthält.")
        if intermediate_enabled and not intermediate_available:
            raise ValidationError("Die Zwischenrunde ist über diesen Schalter erst ab 16 zugeordneten Spielern verfügbar. Kleinere Formate können im variablen Turnier-Designer angelegt werden.")
        tournament.qualification_enabled = bool(qualification_enabled)
        tournament.intermediate_enabled = bool(intermediate_enabled)
        # Keep the five-group Freudenholm preset compatible, but do not force
        # arbitrary tournaments into that fixed structure.
        group_phases = sorted((p for p in tournament.phases if p.phase_type is PhaseType.GROUP_STAGE), key=lambda p: p.position)
        if group_phases and len(group_phases[0].groups) == 5:
            self.ensure_freudenholm_flow()
        self.save()

    def effective_optional_phases(self) -> tuple[bool, bool]:
        tournament = self.require_tournament()
        qualification_available, intermediate_available = self.phase_option_availability()
        group_phases = sorted(
            (phase for phase in tournament.phases if phase.phase_type is PhaseType.GROUP_STAGE),
            key=lambda phase: phase.position,
        )
        source = group_phases[0] if group_phases else None
        automatic_intermediate_qualification = bool(
            tournament.intermediate_enabled
            and source is not None
            and any(len(group.participant_ids) >= 4 for group in source.groups)
        )
        return (
            (tournament.qualification_enabled or automatic_intermediate_qualification) and qualification_available,
            tournament.intermediate_enabled and intermediate_available,
        )

    def sync_intermediate_qualification_playoffs(self, source_phase_id: UUID) -> tuple[str, ...]:
        """Apply the universal 3rd-vs-4th rule before an intermediate round.

        Whenever a real intermediate group stage follows the source phase, every
        source group with at least four assigned players must play places 3 vs 4.
        Smaller groups cannot play that match and therefore have the playoff
        disabled.  This rule is independent of group name, profile or legacy
        preset and repairs older tournament files automatically.
        """
        tournament = self.require_tournament()
        source = tournament.phase(source_phase_id)
        if source.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Qualifikationsspiele können nur aus einer Gruppenphase erzeugt werden.")

        phases = sorted(tournament.phases, key=lambda phase: phase.position)
        target = tournament.phase(source.next_phase_id) if source.next_phase_id else next(
            (phase for phase in phases if phase.position > source.position and phase.phase_type is PhaseType.GROUP_STAGE),
            None,
        )
        intermediate_active = bool(
            tournament.intermediate_enabled
            and target is not None
            and target.phase_type is PhaseType.GROUP_STAGE
        )
        if not intermediate_active:
            return ()

        changed = False
        eligible_names: list[str] = []
        for group in source.groups:
            should_play = len(group.participant_ids) >= 4
            if should_play:
                eligible_names.append(group.name)
            if group.qualification_playoff != should_play:
                group.set_qualification_playoff(should_play)
                changed = True

        if eligible_names and not tournament.qualification_enabled:
            tournament.qualification_enabled = True
            changed = True
        if changed:
            self.save()
        return tuple(eligible_names)

    def add_player(self, first_name: str, last_name: str, club: str = "", category: str = "Offen", start_number: int | None = None, license_number: str = "") -> Person:
        person = self.require_tournament().add_person(first_name, last_name, club, category, start_number, license_number)
        self.save()
        return person

    def add_players_bulk(
        self,
        entries: list[tuple[str, str, str, str]],
        *,
        skip_duplicates: bool = True,
    ) -> tuple[Person, ...]:
        """Add several players atomically and save only once.

        Each entry contains first name, last name, club and category. Existing or
        repeated full names can either be skipped or rejected.
        """
        tournament = self.require_tournament()
        normalized_existing = {person.full_name.casefold() for person in tournament.people}
        normalized_batch: set[str] = set()
        prepared: list[tuple[str, str, str, str]] = []
        for index, entry in enumerate(entries, start=1):
            if len(entry) != 4:
                raise ValidationError(f"Eintrag {index} besitzt nicht genau vier Felder.")
            first_name, last_name, club, category = (str(value).strip() for value in entry)
            if not first_name or not last_name:
                raise ValidationError(f"Eintrag {index}: Vor- und Nachname sind erforderlich.")
            category = category or "Offen"
            key = f"{first_name} {last_name}".casefold()
            duplicate = key in normalized_existing or key in normalized_batch
            if duplicate:
                if skip_duplicates:
                    continue
                raise ValidationError(f"Spieler doppelt vorhanden: {first_name} {last_name}")
            normalized_batch.add(key)
            prepared.append((first_name, last_name, club, category))

        added = tuple(tournament.add_person(first, last, club, category) for first, last, club, category in prepared)
        if added:
            self.save()
        return added

    def edit_player(self, person_id: UUID, first_name: str, last_name: str, club: str = "", category: str = "Offen", start_number: int | None = None, license_number: str = "") -> None:
        tournament = self.require_tournament()
        if start_number is not None and any(p.id != person_id and p.start_number == start_number for p in tournament.people):
            raise ValidationError("Diese Startnummer ist bereits vergeben.")
        tournament.person(person_id).rename(first_name, last_name, club, category, start_number, license_number)
        self.save()

    def remove_player(self, person_id: UUID) -> None:
        self.require_tournament().remove_person(person_id)
        self.save()

    def search_players(self, query: str = "") -> list[Person]:
        term = query.strip().casefold()
        players = self.require_tournament().people
        if term:
            players = [person for person in players if term in f"{person.full_name} {person.club} {person.category} {person.start_number or ''} {person.license_number}".casefold()]
        return sorted(players, key=lambda person: (person.last_name.casefold(), person.first_name.casefold()))

    def search_competitions(self, query: str = "") -> list[Competition]:
        term = query.strip().casefold()
        competitions = self.require_tournament().competitions
        if term:
            competitions = [competition for competition in competitions if term in competition.name.casefold()]
        return sorted(competitions, key=lambda competition: competition.name.casefold())

    def create_team(self, name: str, club: str = "", abbreviation: str = "", captain: str = "", notes: str = "") -> Team:
        team = self.require_tournament().add_team(name, club, abbreviation, captain, notes)
        self.save()
        return team

    def add_team_member(self, team_id: UUID, person_id: UUID, board_number: int, substitute: bool = False, active: bool = True) -> None:
        tournament = self.require_tournament()
        tournament.person(person_id)
        tournament.team(team_id).add_member(person_id, board_number, substitute, active)
        self.save()

    def create_team_competition(self, name: str, boards: int = 4) -> TeamCompetition:
        competition = self.require_tournament().add_team_competition(name, boards)
        self.save()
        return competition

    def add_playing_area(self, name: str, hall: str = "", room: str = "", table_number: str = "", notes: str = "", active: bool = True) -> PlayingArea:
        area = self.require_tournament().add_playing_area(name, hall, room, table_number, notes, active)
        self.save()
        return area

    def remove_playing_area(self, area_id: UUID) -> None:
        self.require_tournament().remove_playing_area(area_id)
        self.save()

    def add_schedule_item(self, title: str, start_time: str, end_time: str = "", location: str = "", status: str = "geplant", notes: str = "") -> ScheduleItem:
        item = self.require_tournament().add_schedule_item(title, start_time, end_time, location, status, notes)
        self.save()
        return item

    def remove_schedule_item(self, item_id: UUID) -> None:
        self.require_tournament().remove_schedule_item(item_id)
        self.save()

    def team_competition(self, competition_id: UUID) -> TeamCompetition:
        for competition in self.require_tournament().team_competitions:
            if competition.id == competition_id:
                return competition
        raise ValidationError("Mannschaftswettbewerb wurde nicht gefunden.")

    def register_team(self, competition_id: UUID, team_id: UUID) -> None:
        tournament = self.require_tournament()
        tournament.team(team_id)
        self.team_competition(competition_id).register(team_id)
        self.save()

    def start_team_competition(self, competition_id: UUID) -> None:
        tournament = self.require_tournament()
        self.team_competition(competition_id).start(tournament.teams)
        self.save()

    def record_board_result(self, competition_id: UUID, team_match_id: UUID, board_game_id: UUID, home_score: int, away_score: int) -> None:
        self.team_competition(competition_id).set_board_result(team_match_id, board_game_id, home_score, away_score)
        self.save()

    def finish_team_competition(self, competition_id: UUID) -> None:
        self.team_competition(competition_id).finish()
        self.save()

    def create_phase(self, name: str, phase_type: PhaseType = PhaseType.GROUP_STAGE) -> TournamentPhase:
        phase = self.require_tournament().add_phase(name, phase_type)
        self.save()
        return phase

    def configure_tournament_structure(
        self,
        first_group_count: int,
        first_qualifiers_per_group: int,
        use_second_group_stage: bool = True,
        second_group_count: int = 4,
        second_qualifiers_per_group: int = 2,
        use_final_round: bool = True,
        replace_existing: bool = False,
    ) -> TournamentStructureResult:
        """Create a complete groups -> optional intermediate groups -> optional KO structure.

        Qualification counts are defaults and can still be changed independently
        for every group afterwards.
        """
        tournament = self.require_tournament()
        if not 1 <= first_group_count <= 26:
            raise ValidationError("Die erste Gruppenphase muss zwischen 1 und 26 Gruppen enthalten.")
        if first_qualifiers_per_group < 0:
            raise ValidationError("Die Anzahl der Qualifizierten darf nicht negativ sein.")
        if use_second_group_stage and not 1 <= second_group_count <= 26:
            raise ValidationError("Die Zwischenrunde muss zwischen 1 und 26 Gruppen enthalten.")
        if second_qualifiers_per_group < 0:
            raise ValidationError("Die Anzahl der Qualifizierten darf nicht negativ sein.")
        if tournament.phases:
            has_started = any(
                group.matches
                for phase in tournament.phases
                for group in phase.groups
            ) or any(phase.matches for phase in tournament.phases)
            if has_started:
                raise ValidationError("Die Turnierstruktur kann nach Beginn des Spielbetriebs nicht ersetzt werden.")
            if not replace_existing:
                raise ValidationError("Es existiert bereits eine Turnierstruktur.")
            tournament.phases.clear()

        first = tournament.add_phase("1. Gruppenphase", PhaseType.GROUP_STAGE)
        for index in range(first_group_count):
            group = first.add_group(f"Gruppe {chr(65 + index)}")
            group.set_qualification_count(first_qualifiers_per_group)

        second = None
        if use_second_group_stage:
            second = tournament.add_phase("Zwischenrunde", PhaseType.GROUP_STAGE)
            for index in range(second_group_count):
                group = second.add_group(f"Zwischengruppe {chr(65 + index)}")
                group.set_qualification_count(second_qualifiers_per_group)

        final = tournament.add_phase("K.-o.-Phase", PhaseType.FINAL_ROUND) if use_final_round else None
        if second is not None:
            first.next_phase_id = second.id
            first.distribution_mode = "snake"
            if final is not None:
                second.next_phase_id = final.id
                second.distribution_mode = "snake"
        elif final is not None:
            first.next_phase_id = final.id
            first.distribution_mode = "snake"
        self.save()
        return TournamentStructureResult(first.id, second.id if second else None, final.id if final else None)

    def projected_qualifier_count(self, source_phase_id: UUID) -> int:
        """Return how many players the current group qualification rules produce."""
        phase = self.require_tournament().phase(source_phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Qualifikationsregeln können nur für Gruppenphasen berechnet werden.")
        total = 0
        for group in phase.groups:
            size = len(group.participant_ids)
            total += min(group.qualification_count, size)
            if group.qualification_playoff and size >= 4:
                total += 1
        return total

    @staticmethod
    def balanced_group_sizes(total: int, preferred_max: int = 4) -> tuple[int, ...]:
        """Suggest balanced target group sizes without producing one-player groups."""
        if total < 2:
            return ()
        preferred_max = max(2, preferred_max)
        group_count = max(1, (total + preferred_max - 1) // preferred_max)
        while group_count > 1 and total // group_count < 2:
            group_count -= 1
        base, extra = divmod(total, group_count)
        return tuple(base + (1 if index < extra else 0) for index in range(group_count))

    def suggested_intermediate_group_sizes(self, source_phase_id: UUID) -> tuple[int, ...]:
        return self.balanced_group_sizes(self.projected_qualifier_count(source_phase_id))

    def configure_variable_intermediate_round(
        self, source_phase_id: UUID, group_sizes: tuple[int, ...], *, qualifiers_per_group: int = 2
    ) -> UUID:
        """Create or resize the next group phase for any tournament size.

        The sum of target capacities must match the number of players produced by
        the source qualification rules. Existing played target schedules are never
        overwritten.
        """
        tournament = self.require_tournament()
        source = tournament.phase(source_phase_id)
        if source.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Die Quellphase muss eine Gruppenphase sein.")
        if not group_sizes or any(size < 2 for size in group_sizes):
            raise ValidationError("Jede Zwischenrundengruppe benötigt mindestens zwei Spieler.")
        expected = self.projected_qualifier_count(source_phase_id)
        if sum(group_sizes) != expected:
            raise ValidationError(
                f"Die Zielgrößen ergeben {sum(group_sizes)} Plätze, die Qualifikationsregeln aber {expected}."
            )
        phases = sorted(tournament.phases, key=lambda item: item.position)
        target = tournament.phase(source.next_phase_id) if source.next_phase_id else None
        if target is None or target.phase_type is not PhaseType.GROUP_STAGE:
            later = next((p for p in phases if p.position > source.position and p.phase_type is PhaseType.GROUP_STAGE), None)
            target = later or tournament.add_phase("Zwischenrunde", PhaseType.GROUP_STAGE)
        if any(group.matches for group in target.groups):
            current = tuple(group.target_capacity or len(group.participant_ids) for group in target.groups)
            if current != tuple(group_sizes):
                raise ValidationError("Die Zwischenrunde besitzt bereits Spielpläne und kann nicht in ihrer Größe geändert werden.")
        else:
            while len(target.groups) < len(group_sizes):
                target.add_group(f"Gruppe {chr(70 + len(target.groups))}")
            if len(target.groups) > len(group_sizes):
                occupied = [g.name for g in target.groups[len(group_sizes):] if g.participant_ids]
                if occupied:
                    raise ValidationError("Zusätzliche belegte Zwischenrundengruppen können nicht automatisch entfernt werden: " + ", ".join(occupied))
                target.groups = target.groups[:len(group_sizes)]
            for index, (group, size) in enumerate(zip(target.groups, group_sizes)):
                group.name = f"Gruppe {chr(70 + index)}"
                group.target_capacity = size
                group.set_qualification_count(min(qualifiers_per_group, size))
                group.set_qualification_playoff(False)
        source.next_phase_id = target.id
        source.distribution_mode = source.distribution_mode or "snake"

        final = next((p for p in tournament.phases if p.position > target.position and p.phase_type is PhaseType.FINAL_ROUND), None)
        if final is None:
            final = next((p for p in tournament.phases if p.phase_type is PhaseType.FINAL_ROUND), None)
        if final is None:
            final = tournament.add_phase("K.-o.-Phase", PhaseType.FINAL_ROUND)
        target.next_phase_id = final.id
        target.distribution_mode = "snake"

        # Normalize positions while preserving any additional later phases.
        source.position = 1
        target.position = 2
        final.position = 3
        protected = {source.id, target.id, final.id}
        position = 4
        for extra in sorted((p for p in tournament.phases if p.id not in protected), key=lambda p: p.position):
            extra.position = position
            position += 1
        tournament.phases.sort(key=lambda p: p.position)
        tournament.intermediate_enabled = True
        self.save()
        return target.id

    @staticmethod
    def _is_women_category(category: str) -> bool:
        value = (category or "").strip().casefold()
        return value in {"damen", "frau", "frauen", "weiblich", "women", "female", "w"}

    @staticmethod
    def _is_amateur_category(category: str) -> bool:
        value = (category or "").strip().casefold()
        return value in {"amateur", "amateure", "amateurin", "amateurinnen"}

    def qualified_women_ids(self, ordered: list[UUID] | tuple[UUID, ...], source_phase_id: UUID | None = None) -> list[UUID]:
        """Identify qualified women robustly, including legacy Freudenholm files.

        Newer projects use the participant category (Damen/Frau/etc.). Older
        Freudenholm projects did not always persist that category; their canonical
        21-player layout is one 5-player women's group followed by four 4-player
        groups. For that legacy shape we treat the first group's qualifiers as
        women, so an upgrade cannot silently split the women's intermediate group.
        """
        tournament = self.require_tournament()
        ordered_list = list(ordered)
        women = [pid for pid in ordered_list if self._is_women_category(tournament.person(pid).category)]
        if women or source_phase_id is None:
            return women
        source = tournament.phase(source_phase_id)
        if len(source.groups) == 5 and [len(g.participant_ids) for g in source.groups] == [5, 4, 4, 4, 4]:
            first_group_ids = set(source.groups[0].participant_ids)
            return [pid for pid in ordered_list if pid in first_group_ids]
        return women

    def _qualified_women_group_plan(self, ordered: list[UUID], target, source_phase_id: UUID | None = None):
        """Return a women-only intermediate-group plan when at least 3 women qualify."""
        women = self.qualified_women_ids(ordered, source_phase_id)
        if len(women) < 3:
            return None
        candidates = [group for group in target.groups if group.target_capacity == len(women)]
        if not candidates:
            return None
        group = candidates[0]
        women_set = set(women)
        others = [pid for pid in ordered if pid not in women_set]
        return group, women, others

    def qualified_amateur_ids(self, ordered: list[UUID] | tuple[UUID, ...]) -> list[UUID]:
        """Return qualified participants explicitly assigned to the Amateur category."""
        tournament = self.require_tournament()
        return [pid for pid in ordered if self._is_amateur_category(tournament.person(pid).category)]

    def _qualified_amateur_group_plan(self, ordered: list[UUID], target, excluded_group_ids: set[UUID] | None = None):
        """Return an amateur-only intermediate-group plan when at least 3 amateurs qualify."""
        amateurs = self.qualified_amateur_ids(ordered)
        if len(amateurs) < 3:
            return None
        excluded = excluded_group_ids or set()
        candidates = [
            group for group in target.groups
            if group.id not in excluded and group.target_capacity == len(amateurs)
        ]
        if not candidates:
            return None
        group = candidates[0]
        amateur_set = set(amateurs)
        others = [pid for pid in ordered if pid not in amateur_set]
        return group, amateurs, others

    def uses_gender_split_13_flow(self, phase_id: UUID | None = None) -> bool:
        """Return True for the A/B women + C/D/E men tournament mode.

        A/B qualify their top two directly. C/D/E qualify their top two plus
        the winner of places 3 vs 4. This yields 4 women + 9 men = 13 players.
        """
        tournament = self.require_tournament()
        phases = sorted(tournament.phases, key=lambda item: item.position)
        phase = tournament.phase(phase_id) if phase_id is not None else next(
            (item for item in phases if item.phase_type is PhaseType.GROUP_STAGE), None
        )
        if phase is None or len(phase.groups) != 5:
            return False
        groups = phase.groups
        return (
            all(group.qualification_count == 2 and not group.qualification_playoff for group in groups[:2])
            and all(group.qualification_count == 2 and group.qualification_playoff for group in groups[2:5])
        )

    def intermediate_group_capacities(self, source_phase_id: UUID | None = None) -> tuple[int, ...]:
        """Expected intermediate group sizes for the configured tournament mode."""
        tournament = self.require_tournament()
        source = tournament.phase(source_phase_id) if source_phase_id is not None else next(
            (p for p in sorted(tournament.phases, key=lambda item: item.position) if p.phase_type is PhaseType.GROUP_STAGE), None
        )
        if source is not None and source.next_phase_id is not None:
            target = tournament.phase(source.next_phase_id)
            if target.phase_type is PhaseType.GROUP_STAGE and target.groups:
                explicit = tuple(group.target_capacity for group in target.groups)
                if all(size > 0 for size in explicit):
                    return explicit
        if source is not None and self.uses_gender_split_13_flow(source.id):
            return (4, 3, 3, 3)
        # Backwards compatibility: older MSTTS projects implicitly used a
        # four-by-four intermediate round and stored no capacities.
        return (4, 4, 4, 4)

    def ensure_freudenholm_flow(self) -> TournamentStructureResult:
        """Repair the Freudenholm flow without deleting players or results.

        The existing first group stage is preserved. Missing intermediate and
        final phases are created, qualification rules are repaired, and the
        phase transitions are connected. This is safe after group matches have
        already been generated.
        """
        tournament = self.require_tournament()
        phases = sorted(tournament.phases, key=lambda item: item.position)

        first = next((p for p in phases if p.phase_type is PhaseType.GROUP_STAGE), None)
        if first is None:
            first = tournament.add_phase("1. Gruppenphase", PhaseType.GROUP_STAGE)
        qualification_active = tournament.qualification_enabled
        intermediate_active = tournament.intermediate_enabled

        # The historic Freudenholm preset has exactly five groups. Arbitrary
        # tournaments are now handled by the variable designer instead of being
        # forced into A-E.
        if len(first.groups) != 5:
            if intermediate_active:
                sizes = self.suggested_intermediate_group_sizes(first.id)
                if not sizes:
                    raise ValidationError("Für die Zwischenrunde sind noch keine Qualifikationsplätze eingestellt.")
                target_id = self.configure_variable_intermediate_round(first.id, sizes)
                tournament = self.require_tournament()
                target = tournament.phase(target_id)
                final = tournament.phase(target.next_phase_id) if target.next_phase_id else None
                return TournamentStructureResult(first.id, target.id, final.id if final else None)
            final = next((p for p in phases if p.phase_type is PhaseType.FINAL_ROUND), None)
            if final is None:
                final = tournament.add_phase("K.-o.-Phase", PhaseType.FINAL_ROUND)
            first.position = 1
            final.position = 2
            first.next_phase_id = final.id
            self.save()
            return TournamentStructureResult(first.id, None, final.id)

        while len(first.groups) < 5:
            first.add_group(f"Gruppe {chr(65 + len(first.groups))}")

        gender_split = self.uses_gender_split_13_flow(first.id)
        if gender_split:
            # A/B are women's groups: top two directly. C/D/E are men's
            # groups: top two directly plus winner of places 3 vs 4.
            for group in first.groups[:2]:
                group.set_qualification_count(2)
                group.set_qualification_playoff(False)
            for group in first.groups[2:5]:
                group.set_qualification_count(2)
                group.set_qualification_playoff(qualification_active)
        else:
            first.groups[0].set_qualification_count(4)
            first.groups[0].set_qualification_playoff(False)
            for group in first.groups[1:]:
                group.set_qualification_count(2)
                group.set_qualification_playoff(qualification_active)

        later_group_phases = [
            p for p in phases
            if p.phase_type is PhaseType.GROUP_STAGE and p.id != first.id
        ]
        second = later_group_phases[0] if later_group_phases else None
        existing_final = next((p for p in phases if p.phase_type is PhaseType.FINAL_ROUND), None)
        if intermediate_active and second is None:
            second = tournament.add_phase("Zwischenrunde", PhaseType.GROUP_STAGE)
        if intermediate_active and second is not None:
            if any(group.matches for group in second.groups) and len(second.groups) != 4:
                raise ValidationError(
                    "Die vorhandene Zwischenrunde besitzt bereits Spielpläne und kann nicht automatisch auf vier Gruppen geändert werden."
                )
            if not any(group.matches for group in second.groups):
                while len(second.groups) < 4:
                    second.add_group(f"Gruppe {chr(70 + len(second.groups))}")
                if len(second.groups) > 4:
                    occupied = [g.name for g in second.groups[4:] if g.participant_ids]
                    if occupied:
                        raise ValidationError(
                            "Die Zwischenrunde enthält mehr als vier belegte Gruppen: " + ", ".join(occupied)
                        )
                    second.groups = second.groups[:4]
            for index, group in enumerate(second.groups):
                if gender_split:
                    group.name = "Frauengruppe F" if index == 0 else f"Männergruppe {chr(70 + index)}"
                else:
                    group.name = f"Gruppe {chr(70 + index)}"
                group.set_qualification_count(2)
                group.set_qualification_playoff(False)

        final = existing_final
        if final is None:
            final = tournament.add_phase("K.-o.-Phase", PhaseType.FINAL_ROUND)

        first.position = 1
        if intermediate_active and second is not None:
            second.position = 2
            final.position = 3
            first.next_phase_id = second.id
            second.next_phase_id = final.id
            second.distribution_mode = "snake"
            protected = {first.id, second.id, final.id}
            next_position = 4
        else:
            final.position = 2
            first.next_phase_id = final.id
            protected = {first.id, final.id}
            next_position = 3
        first.distribution_mode = "snake"
        remaining = [p for p in tournament.phases if p.id not in protected]
        for position, extra in enumerate(sorted(remaining, key=lambda p: p.position), start=next_position):
            extra.position = position
        tournament.phases.sort(key=lambda p: p.position)
        self.save()

        if intermediate_active and second is not None:
            complete_roster = len(second.groups) == 4 and all(len(group.participant_ids) == 4 for group in second.groups)
            existing_matches = [match for group in second.groups for match in group.matches]
            schedule_incomplete = any(not match.scheduled_time or not match.table_number for match in existing_matches)
            if complete_roster and existing_matches and schedule_incomplete:
                self.generate_phase_schedule(
                    second.id, tables=tournament.table_count, start_time=tournament.schedule_start_time,
                    duration_minutes=tournament.match_duration_minutes, replace=False, strategy="smart",
                )

        return TournamentStructureResult(first.id, second.id if intermediate_active and second else None, final.id)

    def create_group(self, phase_id: UUID, name: str) -> Group:
        group = self.require_tournament().phase(phase_id).add_group(name)
        self.save()
        return group

    def rename_group(self, phase_id: UUID, group_id: UUID, name: str) -> Group:
        phase = self.require_tournament().phase(phase_id)
        group = phase.group(group_id)
        cleaned = name.strip()
        if not cleaned:
            raise ValidationError("Der Gruppenname darf nicht leer sein.")
        if any(item.id != group_id and item.name.casefold() == cleaned.casefold() for item in phase.groups):
            raise ValidationError("Dieser Gruppenname ist in der Phase bereits vergeben.")
        group.name = cleaned
        self.save()
        return group

    def delete_group(self, phase_id: UUID, group_id: UUID) -> None:
        phase = self.require_tournament().phase(phase_id)
        group = phase.group(group_id)
        if group.matches:
            raise ValidationError("Eine Gruppe mit Spielplan kann erst nach dem Zurücksetzen gelöscht werden.")
        phase.groups = [item for item in phase.groups if item.id != group_id]
        self.save()

    def replace_groups(self, phase_id: UUID, count: int) -> list[Group]:
        if count < 1 or count > 26:
            raise ValidationError("Die Gruppenanzahl muss zwischen 1 und 26 liegen.")
        phase = self.require_tournament().phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Gruppen können nur in einer Gruppenphase angelegt werden.")
        if any(group.matches for group in phase.groups):
            raise ValidationError("Bitte zuerst alle vorhandenen Spielpläne dieser Phase zurücksetzen.")
        phase.groups.clear()
        groups = [phase.add_group(f"Gruppe {chr(65 + index)}") for index in range(count)]
        self.save()
        return groups

    def resize_groups(self, phase_id: UUID, count: int) -> list[Group]:
        """Set the number of groups while preserving existing groups and assignments.

        Increasing the count appends new groups. Decreasing the count only removes
        empty groups at the end, so assigned players are never lost silently.
        Existing match schedules must be reset before the number can be changed.
        """
        if count < 1 or count > 26:
            raise ValidationError("Die Gruppenanzahl muss zwischen 1 und 26 liegen.")
        phase = self.require_tournament().phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Die Gruppenanzahl kann nur in einer Gruppenphase geändert werden.")
        if any(group.matches for group in phase.groups):
            raise ValidationError("Bitte zuerst alle vorhandenen Spielpläne dieser Phase zurücksetzen.")

        current = len(phase.groups)
        if count > current:
            for index in range(current, count):
                phase.add_group(f"Gruppe {chr(65 + index)}")
        elif count < current:
            removable = phase.groups[count:]
            occupied = [group.name for group in removable if group.participant_ids]
            if occupied:
                raise ValidationError(
                    "Diese Gruppen enthalten noch Spieler und können nicht entfernt werden: "
                    + ", ".join(occupied)
                    + ". Bitte die Spieler vorher verschieben oder entfernen."
                )
            phase.groups = phase.groups[:count]
        self.save()
        return list(phase.groups)

    def ensure_group_stage(self, name: str = "1. Gruppenphase") -> TournamentPhase:
        tournament = self.require_tournament()
        for phase in sorted(tournament.phases, key=lambda item: item.position):
            if phase.phase_type is PhaseType.GROUP_STAGE:
                return phase
        phase = tournament.add_phase(name, PhaseType.GROUP_STAGE)
        self.save()
        return phase

    def create_groups(self, phase_id: UUID, count: int) -> list[Group]:
        if count < 1 or count > 26:
            raise ValidationError("Die Gruppenanzahl muss zwischen 1 und 26 liegen.")
        phase = self.require_tournament().phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Gruppen können nur in einer Gruppenphase angelegt werden.")
        if any(group.matches for group in phase.groups):
            raise ValidationError("Nach der Spielplanerzeugung können keine Gruppen ergänzt werden.")
        created: list[Group] = []
        for index in range(count):
            letter = chr(65 + index)
            canonical = f"Gruppe {letter}"
            aliases = {canonical.casefold(), letter.casefold()}
            existing = next((group for group in phase.groups if group.name.strip().casefold() in aliases), None)
            if existing is None:
                existing = phase.add_group(canonical)
            else:
                existing.name = canonical
            created.append(existing)
        self.save()
        return created

    def auto_distribute_players(
        self,
        phase_id: UUID,
        freudenholm: bool = False,
        strategy: str = "balanced",
    ) -> dict[UUID, list[UUID]]:
        """Distribute players evenly, optionally separating clubs/categories and seeded players."""
        tournament = self.require_tournament()
        phase = tournament.phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Die automatische Verteilung ist nur für Gruppenphasen verfügbar.")
        if not phase.groups:
            raise ValidationError("Bitte zuerst Gruppen anlegen.")
        if any(group.matches for group in phase.groups):
            raise ValidationError("Nach der Spielplanerzeugung kann die Gruppeneinteilung nicht geändert werden.")
        for group in phase.groups:
            group.participant_ids.clear()
        people = sorted(tournament.people, key=lambda person: (person.last_name.casefold(), person.first_name.casefold()))
        if freudenholm:
            if len(phase.groups) < 5:
                raise ValidationError("Der Freudenholm-Modus benötigt mindestens fünf Gruppen.")
            women = [person for person in people if person.category.casefold() in {"damen", "frau", "frauen"}]
            men = [person for person in people if person not in women]
            phase.groups[0].participant_ids.extend(person.id for person in women)
            targets = phase.groups[1:5]
            for index, person in enumerate(men):
                targets[index % len(targets)].participant_ids.append(person.id)
        elif strategy == "balanced":
            for index, person in enumerate(people):
                phase.groups[index % len(phase.groups)].participant_ids.append(person.id)
        else:
            # Seeded players (start number = seed) are handled first. Remaining players are
            # ordered by club/category scarcity so collisions can be avoided greedily.
            seeded = sorted(
                [person for person in people if person.start_number is not None],
                key=lambda person: (person.start_number or 999999, person.last_name.casefold()),
            )
            unseeded = [person for person in people if person.start_number is None]
            ordered = seeded + sorted(
                unseeded,
                key=lambda person: (
                    person.club.casefold() if person.club else "~",
                    person.category.casefold(),
                    person.last_name.casefold(),
                ),
            )
            target_size = (len(ordered) + len(phase.groups) - 1) // len(phase.groups)
            assignments: dict[UUID, object] = {person.id: person for person in ordered}
            for person in ordered:
                best_group = None
                best_score = None
                for group_index, group in enumerate(phase.groups):
                    members = [assignments[pid] for pid in group.participant_ids]
                    if len(group.participant_ids) >= target_size:
                        size_penalty = 100
                    else:
                        size_penalty = len(group.participant_ids) * 10
                    club_collisions = sum(
                        1 for member in members
                        if person.club and member.club and member.club.casefold() == person.club.casefold()
                    )
                    category_collisions = sum(
                        1 for member in members
                        if person.category and member.category.casefold() == person.category.casefold()
                    )
                    # Strongly spread seeded players across groups before allowing a second seed.
                    seed_collisions = sum(1 for member in members if member.start_number is not None)
                    score = (
                        size_penalty
                        + (club_collisions * 30 if strategy in {"club", "fair"} else 0)
                        + (category_collisions * 8 if strategy in {"category", "fair"} else 0)
                        + (seed_collisions * 40 if person.start_number is not None and strategy in {"seeded", "fair"} else 0),
                        len(group.participant_ids),
                        group_index,
                    )
                    if best_score is None or score < best_score:
                        best_score = score
                        best_group = group
                if best_group is not None:
                    best_group.participant_ids.append(person.id)
        self.save()
        return {group.id: list(group.participant_ids) for group in phase.groups}

    def apply_group_assignments(self, phase_id: UUID, assignments: dict[UUID, list[UUID]]) -> None:
        """Atomically replace all participant assignments of one group phase."""
        tournament = self.require_tournament()
        phase = tournament.phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Gruppeneinteilungen können nur für Gruppenphasen gespeichert werden.")
        if any(group.matches for group in phase.groups):
            raise ValidationError("Nach der Spielplanerzeugung kann die Gruppeneinteilung nicht geändert werden.")

        valid_groups = {group.id for group in phase.groups}
        if set(assignments) != valid_groups:
            raise ValidationError("Die Gruppeneinteilung passt nicht zur aktuellen Gruppenstruktur.")

        all_ids = [person_id for ids in assignments.values() for person_id in ids]
        if len(all_ids) != len(set(all_ids)):
            raise ValidationError("Ein Teilnehmer darf nur einer Gruppe zugeordnet sein.")
        for person_id in all_ids:
            tournament.person(person_id)

        for group in phase.groups:
            group.participant_ids.clear()
        for group in phase.groups:
            group.participant_ids.extend(assignments[group.id])
        self.save()

    def set_group_qualification_count(self, phase_id: UUID, group_id: UUID, count: int) -> None:
        group = self.require_tournament().phase(phase_id).group(group_id)
        group.set_qualification_count(count)
        self.save()

    def set_group_qualification_playoff(self, phase_id: UUID, group_id: UUID, enabled: bool) -> None:
        group = self.require_tournament().phase(phase_id).group(group_id)
        group.set_qualification_playoff(enabled)
        self.save()

    def set_group_profile(self, phase_id: UUID, group_id: UUID, profile: str) -> None:
        group = self.require_tournament().phase(phase_id).group(group_id)
        group.profile = (profile or "Offen").strip() or "Offen"
        self.save()

    def ensure_group_playoff(self, phase_id: UUID, group_id: UUID) -> Match | None:
        group = self.require_tournament().phase(phase_id).group(group_id)
        match = group.ensure_playoff_match()
        self.save()
        return match

    def record_group_playoff_result(self, phase_id: UUID, group_id: UUID, home_score: int, away_score: int) -> None:
        group = self.require_tournament().phase(phase_id).group(group_id)
        group.record_playoff_result(home_score, away_score)
        self.save()
        # Startfix 153: qualification playoffs are the final gate of the
        # Freudenholm first phase. Previously this path saved the result but did
        # not trigger the automatic phase transfer.
        self.auto_advance_completed_phases()
        self._continue_freudenholm_phase_if_ready(phase_id)

    def prepare_qualification_playoffs(self, phase_id: UUID) -> tuple[Match, ...]:
        """Create every enabled 3rd-vs-4th playoff once the groups are finished.

        The method is intentionally separate from the phase advance. The UI can
        therefore move the tournament day into the qualification section first,
        let the operator enter those results, and only then advance to the next
        group stage.
        """
        phase = self.require_tournament().phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Qualifikationsspiele gibt es nur in einer Gruppenphase.")
        self.sync_intermediate_qualification_playoffs(phase_id)
        phase = self.require_tournament().phase(phase_id)
        created: list[Match] = []
        eligible_groups = [
            group for group in phase.groups
            if group.qualification_playoff_applicable
        ]
        unfinished = [group.name for group in eligible_groups if not group.is_finished]
        if unfinished:
            raise ValidationError(
                "Zuerst müssen alle Gruppenspiele dieser Gruppen abgeschlossen werden: "
                + ", ".join(unfinished)
            )
        playoff_matches: list[Match] = []
        for group in eligible_groups:
            before = group.playoff_match
            match = group.ensure_playoff_match()
            if match is not None:
                playoff_matches.append(match)
                if before is None:
                    created.append(match)

        # Qualifikationsspiele sind echte Spiele des Turniertags. Ältere
        # Datenbanken enthalten dafür häufig noch keine Zeit- oder Feldwerte.
        # Sie werden direkt im Anschluss an den letzten Gruppenslot auf die
        # zentral konfigurierte Felderzahl verteilt, ohne bestehende Werte zu überschreiben.
        from datetime import datetime, timedelta
        latest = None
        for group in phase.groups:
            for existing in group.matches:
                if not existing.scheduled_time:
                    continue
                try:
                    value = datetime.strptime(existing.scheduled_time, "%H:%M")
                except ValueError:
                    continue
                latest = value if latest is None or value > latest else latest
        tournament = self.require_tournament()
        tables = tournament.table_count
        duration = tournament.match_duration_minutes
        start = (latest + timedelta(minutes=duration)) if latest is not None else datetime.strptime(tournament.schedule_start_time, "%H:%M")
        for index, match in enumerate(playoff_matches):
            if match.table_number is None:
                match.table_number = (index % tables) + 1
            if not match.scheduled_time:
                slot = start + timedelta(minutes=duration * (index // tables))
                match.scheduled_time = slot.strftime("%H:%M")

        self.save()
        return tuple(created)

    def assign_player_to_group(self, phase_id: UUID, group_id: UUID, person_id: UUID) -> None:
        tournament = self.require_tournament()
        tournament.person(person_id)
        tournament.phase(phase_id).assign_participant(group_id, person_id)
        self.save()

    def remove_player_from_group(self, phase_id: UUID, group_id: UUID, person_id: UUID) -> None:
        self.require_tournament().phase(phase_id).group(group_id).remove_participant(person_id)
        self.save()

    def move_player_between_groups(
        self,
        phase_id: UUID,
        source_group_id: UUID,
        target_group_id: UUID,
        person_id: UUID,
        *,
        reset_schedules: bool = False,
    ) -> None:
        tournament = self.require_tournament()
        tournament.person(person_id)
        phase = tournament.phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError(
                "Spieler können nur innerhalb einer Gruppenphase verschoben werden."
            )
        if source_group_id == target_group_id:
            raise ValidationError("Quell- und Zielgruppe sind identisch.")

        source_group = phase.group(source_group_id)
        target_group = phase.group(target_group_id)
        if person_id not in source_group.participant_ids:
            raise ValidationError("Der Spieler ist nicht in der Quellgruppe.")
        if person_id in target_group.participant_ids:
            raise ValidationError("Der Spieler befindet sich bereits in der Zielgruppe.")

        groups_with_schedule = [
            group for group in (source_group, target_group) if group.matches
        ]
        if groups_with_schedule and not reset_schedules:
            names = ", ".join(group.name for group in groups_with_schedule)
            raise ValidationError(
                f"Für folgende Gruppe(n) existiert bereits ein Spielplan: {names}."
            )
        for group in groups_with_schedule:
            group.matches.clear()

        source_group.remove_participant(person_id)
        phase.assign_participant(target_group_id, person_id)
        self.save()

    def assign_player_to_final(self, phase_id: UUID, person_id: UUID) -> None:
        tournament = self.require_tournament()
        tournament.person(person_id)
        tournament.phase(phase_id).add_final_participant(person_id)
        self.save()

    def remove_player_from_final(self, phase_id: UUID, person_id: UUID) -> None:
        self.require_tournament().phase(phase_id).remove_final_participant(person_id)
        self.save()

    def generate_final_schedule(self, phase_id: UUID) -> list[Match]:
        tournament = self.require_tournament()
        matches = tournament.phase(phase_id).generate_final_schedule()
        # STARTFIX 152: K.-o.-Spiele erhalten sofort echte Zeiten und Felder.
        # Damit verwenden Leitstand und Präsentation dieselbe gespeicherte Planung.
        self.sync_final_schedule_to_defaults(phase_id, save=False)
        self.save()
        return matches

    def sync_final_schedule_to_defaults(self, phase_id: UUID, *, save: bool = True) -> bool:
        """Plan the currently open KO round from the configured start time.

        The planning controls are shared by group and final rounds.  Older builds
        persisted the selected time for a final round but never copied it into
        the KO matches, leaving the presentation at ``--:--``.
        """
        from datetime import datetime, timedelta

        tournament = self.require_tournament()
        phase = tournament.phase(phase_id)
        if phase.phase_type is not PhaseType.FINAL_ROUND:
            return False
        open_matches = [match for match in phase.matches if match.result is None]
        if not open_matches:
            return False
        current_round = max(match.round_number for match in open_matches)
        current = [match for match in open_matches if match.round_number == current_round]
        current.sort(key=lambda match: (match.table_number or 999, match.scheduled_time or "99:99", str(match.id)))
        try:
            start = datetime.strptime(tournament.schedule_start_time, "%H:%M")
        except ValueError:
            return False
        tables = max(1, tournament.table_count)
        duration = max(1, tournament.match_duration_minutes)
        changed = False
        for index, match in enumerate(current):
            table_number = (index % tables) + 1
            scheduled_time = (start + timedelta(minutes=duration * (index // tables))).strftime("%H:%M")
            if match.table_number != table_number or match.scheduled_time != scheduled_time:
                match.table_number = table_number
                match.scheduled_time = scheduled_time
                changed = True
        if changed and save:
            self.save()
        return changed

    def record_final_result(self, phase_id: UUID, match_id: UUID, home_score: int, away_score: int) -> None:
        from datetime import datetime, timedelta

        phase = self.require_tournament().phase(phase_id)
        existing_ids = {match.id for match in phase.matches}
        phase.record_final_result(match_id, home_score, away_score)

        # Neu entstandene Halbfinal-, Final- und Platzierungsspiele sofort
        # für den Turniertag einplanen. Finale und Spiel um Platz 3 laufen
        # parallel auf Feld 1 und 2.
        created = [match for match in phase.matches if match.id not in existing_ids]
        if created:
            latest = None
            for match in phase.matches:
                if match.id in {item.id for item in created} or not match.scheduled_time:
                    continue
                try:
                    value = datetime.strptime(match.scheduled_time, "%H:%M")
                except ValueError:
                    continue
                latest = value if latest is None or value > latest else latest
            tournament = self.require_tournament()
            duration = tournament.match_duration_minutes
            start = (latest + timedelta(minutes=duration)) if latest is not None else datetime.strptime(tournament.schedule_start_time, "%H:%M")
            for index, match in enumerate(created):
                match.table_number = index + 1
                match.scheduled_time = start.strftime("%H:%M")
        self.save()


    def _phase_match(self, phase_id: UUID, group_id: UUID | None, match_id: UUID) -> Match:
        phase = self.require_tournament().phase(phase_id)
        if group_id is None:
            return phase.final_match(match_id)
        group = phase.group(group_id)
        if group.playoff_match is not None and group.playoff_match.id == match_id:
            return group.playoff_match
        return group.match(match_id)

    def _log_live_event(
        self, event_type: str, summary: str, *, details: str = "", phase_id: UUID | None = None,
        group_id: UUID | None = None, match_id: UUID | None = None, table_number: int | None = None,
        signature: str | None = None,
    ) -> None:
        writer = getattr(self.repository, "append_live_operation_event", None)
        if writer is not None:
            writer(self.require_tournament().id, event_type, summary, details=details, phase_id=phase_id,
                   group_id=group_id, match_id=match_id, table_number=table_number, signature=signature)

    def live_operation_log(self, limit: int = 200) -> tuple[dict, ...]:
        reader = getattr(self.repository, "live_operation_audit", None)
        return tuple(reader(limit) if reader is not None else ())

    def record_flow_warnings(self, warnings) -> int:
        recorded = 0
        for warning in warnings:
            signature = "warning:" + warning.code + ":" + ",".join(sorted(str(i) for i in warning.match_ids))
            before = len(self.live_operation_log(1))
            writer = getattr(self.repository, "append_live_operation_event", None)
            if writer is not None and writer(self.require_tournament().id, "warning", "Ablaufwarnung",
                details=warning.message, signature=signature):
                recorded += 1
        return recorded

    def set_live_match_status(
        self, phase_id: UUID, group_id: UUID | None, match_id: UUID, status: LiveMatchStatus
    ) -> Match:
        match = self._phase_match(phase_id, group_id, match_id)
        if match.result is not None and status is not LiveMatchStatus.FINISHED:
            raise ValidationError("Ein abgeschlossenes Spiel kann nicht erneut gestartet werden.")
        now = datetime.now().isoformat(timespec="seconds")
        if status is LiveMatchStatus.RUNNING:
            if not match.started_at:
                match.started_at = now
            match.finished_at = ""
        elif status in {LiveMatchStatus.RESULT_PENDING, LiveMatchStatus.FINISHED}:
            if not match.started_at:
                match.started_at = now
            match.finished_at = now
        elif status is LiveMatchStatus.PLANNED:
            match.started_at = ""
            match.finished_at = ""
        match.live_status = status
        self.save()
        return match

    def assign_live_match_to_table(
        self, phase_id: UUID, group_id: UUID | None, match_id: UUID, table_number: int,
        *, start_preparing: bool = True,
    ) -> Match:
        tournament = self.require_tournament()
        if not 1 <= table_number <= tournament.table_count:
            raise ValidationError(
                f"Die Feldnummer muss zwischen 1 und {tournament.table_count} liegen."
            )
        match = self._phase_match(phase_id, group_id, match_id)
        if match.result is not None:
            raise ValidationError("Ein abgeschlossenes Spiel kann keinem Feld neu zugewiesen werden.")
        for phase in tournament.phases:
            phase_matches = [m for group in phase.groups for m in ([*group.matches] + ([group.playoff_match] if group.playoff_match is not None else []))] if phase.phase_type is PhaseType.GROUP_STAGE else list(phase.matches)
            for other in phase_matches:
                if other.id == match.id or other.result is not None:
                    continue
                if other.table_number == table_number and other.live_status in {
                    LiveMatchStatus.PREPARING, LiveMatchStatus.RUNNING, LiveMatchStatus.RESULT_PENDING
                }:
                    raise ValidationError(f"Feld {table_number} ist bereits durch ein aktives Spiel belegt.")
        match.table_number = table_number
        if start_preparing:
            match.live_status = LiveMatchStatus.PREPARING
        self.save()
        self._log_live_event("match_called", f"Spiel auf Feld {table_number} aufgerufen", phase_id=phase_id, group_id=group_id, match_id=match_id, table_number=table_number)
        return match

    def move_live_match_to_table(
        self, phase_id: UUID, group_id: UUID | None, match_id: UUID, table_number: int
    ) -> Match:
        match = self._phase_match(phase_id, group_id, match_id)
        return self.assign_live_match_to_table(
            phase_id, group_id, match_id, table_number,
            start_preparing=match.live_status is not LiveMatchStatus.PLANNED,
        )

    def available_live_tables(self, minimum_tables: int | None = None) -> tuple[int, ...]:
        """Return currently unoccupied tables from the central tournament settings."""
        tournament = self.require_tournament()
        configured = tournament.table_count if minimum_tables is None else minimum_tables
        if configured < 1:
            raise ValidationError("Es muss mindestens einen Feld geben.")
        occupied: set[int] = set()
        highest = configured
        for phase in tournament.phases:
            phase_matches = [m for group in phase.groups for m in ([*group.matches] + ([group.playoff_match] if group.playoff_match is not None else []))] if phase.phase_type is PhaseType.GROUP_STAGE else list(phase.matches)
            for match in phase_matches:
                if match.table_number:
                    highest = max(highest, match.table_number)
                if match.result is None and match.table_number and match.live_status in {
                    LiveMatchStatus.PREPARING, LiveMatchStatus.RUNNING, LiveMatchStatus.RESULT_PENDING
                }:
                    occupied.add(match.table_number)
        return tuple(table for table in range(1, highest + 1) if table not in occupied)

    def suggest_live_table(self, minimum_tables: int | None = None) -> int | None:
        """Suggest the first free table, preferring the least scheduled table."""
        available = self.available_live_tables(minimum_tables)
        if not available:
            return None
        tournament = self.require_tournament()
        load = {table: 0 for table in available}
        for phase in tournament.phases:
            phase_matches = [m for group in phase.groups for m in ([*group.matches] + ([group.playoff_match] if group.playoff_match is not None else []))] if phase.phase_type is PhaseType.GROUP_STAGE else list(phase.matches)
            for match in phase_matches:
                if match.result is None and match.table_number in load:
                    load[match.table_number] += 1
        return min(available, key=lambda table: (load[table], table))

    def reschedule_live_match(
        self, phase_id: UUID, group_id: UUID | None, match_id: UUID, scheduled_time: str
    ) -> Match:
        """Change the planned start time using a strict 24-hour HH:MM value."""
        value = scheduled_time.strip()
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError as error:
            raise ValidationError("Die Startzeit muss im Format HH:MM angegeben werden.") from error
        match = self._phase_match(phase_id, group_id, match_id)
        if match.result is not None:
            raise ValidationError("Die Startzeit eines abgeschlossenen Spiels kann nicht geändert werden.")
        old_time = match.scheduled_time
        match.scheduled_time = value
        self.save()
        self._log_live_event("time_changed", f"Startzeit auf {value} geändert", details=f"Vorher: {old_time or '–'}", phase_id=phase_id, group_id=group_id, match_id=match_id, table_number=match.table_number)
        return match


    @staticmethod
    def _live_time_at_or_after(value: str, cutoff: datetime) -> bool:
        try:
            return datetime.strptime(value, "%H:%M") >= cutoff
        except (TypeError, ValueError):
            return True

    def recalculate_live_schedule(
        self, start_time: str, *, duration_minutes: int = 15, tables: int = 3,
        minimum_break_minutes: int = 15, only_from_time: str | None = None,
    ) -> ScheduleAdjustmentResult:
        """Reflow all planned live matches from ``start_time`` without player conflicts.

        Active and completed matches are left untouched. Planned matches keep their
        existing chronological order, while table assignments and start times are
        recalculated. A player can never be assigned twice in the same slot and the
        configured minimum break is observed between that player's matches.
        """
        value = start_time.strip()
        try:
            start = datetime.strptime(value, "%H:%M")
        except ValueError as error:
            raise ValidationError("Die Startzeit muss im Format HH:MM angegeben werden.") from error
        if duration_minutes < 1:
            raise ValidationError("Die Spieldauer muss mindestens eine Minute betragen.")
        if tables < 1:
            raise ValidationError("Es muss mindestens einen Feld geben.")
        if minimum_break_minutes < 0:
            raise ValidationError("Die Mindestpause darf nicht negativ sein.")
        cutoff = None
        if only_from_time is not None:
            cutoff_value = only_from_time.strip()
            try:
                cutoff = datetime.strptime(cutoff_value, "%H:%M")
            except ValueError as error:
                raise ValidationError("Der Konfliktzeitpunkt muss im Format HH:MM angegeben werden.") from error

        tournament = self.require_tournament()
        planned: list[Match] = []
        for phase in sorted(tournament.phases, key=lambda item: item.position):
            phase_matches = (
                [match for group in phase.groups for match in group.matches]
                if phase.phase_type is PhaseType.GROUP_STAGE else list(phase.matches)
            )
            planned.extend(
                match for match in phase_matches
                if match.result is None
                and match.live_status is LiveMatchStatus.PLANNED
                and (cutoff is None or self._live_time_at_or_after(match.scheduled_time, cutoff))
            )
        planned.sort(key=lambda match: (match.scheduled_time or "99:99", match.round_number, str(match.id)))

        player_ready: dict[UUID, datetime] = {}
        adjusted: list[UUID] = []
        pending = list(planned)
        slot = start
        while pending:
            used_players: set[UUID] = set()
            assigned_this_slot: list[Match] = []
            for match in list(pending):
                if len(assigned_this_slot) >= tables:
                    break
                if match.home_id in used_players or match.away_id in used_players:
                    continue
                if player_ready.get(match.home_id, start) > slot or player_ready.get(match.away_id, start) > slot:
                    continue
                assigned_this_slot.append(match)
                used_players.update((match.home_id, match.away_id))
                pending.remove(match)

            if not assigned_this_slot:
                next_ready = min(
                    (ready for match in pending for ready in (player_ready.get(match.home_id), player_ready.get(match.away_id)) if ready and ready > slot),
                    default=slot + timedelta(minutes=duration_minutes),
                )
                slot = max(slot + timedelta(minutes=duration_minutes), next_ready)
                continue

            ready_at = slot + timedelta(minutes=duration_minutes + minimum_break_minutes)
            for table_number, match in enumerate(assigned_this_slot, start=1):
                match.scheduled_time = slot.strftime("%H:%M")
                match.table_number = table_number
                player_ready[match.home_id] = ready_at
                player_ready[match.away_id] = ready_at
                adjusted.append(match.id)
            slot += timedelta(minutes=duration_minutes)

        self.save()
        self._log_live_event("schedule_reflow", f"Zeitplan automatisch angepasst: {len(adjusted)} Spiele", details=f"Start {value}, Spieldauer {duration_minutes} Min., Mindestpause {minimum_break_minutes} Min.")
        end_time = start.strftime("%H:%M") if not adjusted else max(
            datetime.strptime(match.scheduled_time, "%H:%M") for match in planned
        ).strftime("%H:%M")
        return ScheduleAdjustmentResult(tuple(adjusted), value, end_time, tables)

    def pause_live_match(self, phase_id: UUID, group_id: UUID | None, match_id: UUID) -> Match:
        match = self._phase_match(phase_id, group_id, match_id)
        if match.result is not None:
            raise ValidationError("Ein abgeschlossenes Spiel kann nicht pausiert werden.")
        match.live_status = LiveMatchStatus.PLANNED
        match.table_number = None
        match.started_at = ""
        match.finished_at = ""
        self.save()
        self._log_live_event("match_paused", "Spiel pausiert und vom Feld genommen", phase_id=phase_id, group_id=group_id, match_id=match_id)
        return match

    def prepare_live_match(self, phase_id: UUID, group_id: UUID | None, match_id: UUID) -> Match:
        return self.set_live_match_status(phase_id, group_id, match_id, LiveMatchStatus.PREPARING)

    def start_live_match(self, phase_id: UUID, group_id: UUID | None, match_id: UUID) -> Match:
        return self.set_live_match_status(phase_id, group_id, match_id, LiveMatchStatus.RUNNING)

    def mark_live_result_pending(self, phase_id: UUID, group_id: UUID | None, match_id: UUID) -> Match:
        return self.set_live_match_status(phase_id, group_id, match_id, LiveMatchStatus.RESULT_PENDING)

    def record_final_set_scores(self, phase_id: UUID, match_id: UUID, set_scores: list[tuple[int, int]]) -> None:
        self.require_tournament().phase(phase_id).record_final_set_scores(match_id, set_scores)
        self.save()

    def record_live_set_scores(
        self, phase_id: UUID, group_id: UUID | None, match_id: UUID,
        set_scores: list[tuple[int, int]], *, allow_correction: bool = False,
        correction_reason: str = "", changed_by: str = "Turnierleitung",
    ) -> LiveResultOutcome:
        """Save one live result atomically and return all UI-relevant effects.

        This is the single entry point for the tournament-day UI. It supports
        group and final-round matches, marks the match as finished, refreshes
        standings and reports K.-o. matches generated by the completed round.
        """
        tournament = self.require_tournament()
        phase = tournament.phase(phase_id)
        before_ids = {match.id for match in phase.matches}
        current_match = self._phase_match(phase_id, group_id, match_id)
        old_result = None if current_match.result is None else {
            "home_score": current_match.result.home_score,
            "away_score": current_match.result.away_score,
            "set_scores": [list(score) for score in current_match.result.set_scores],
        }
        is_correction = old_result is not None
        if is_correction and not allow_correction:
            raise ValidationError(
                "Für dieses Spiel ist bereits ein Ergebnis gespeichert. "
                "Nutze die bestätigte Ergebniskorrektur, um es zu ändern."
            )
        if is_correction:
            if not correction_reason.strip():
                raise ValidationError("Für eine Ergebniskorrektur ist eine Begründung erforderlich.")
            self._ensure_live_correction_safe(phase, group_id, current_match)

        if group_id is None:
            if phase.phase_type is not PhaseType.FINAL_ROUND:
                raise ValidationError("Ohne Gruppe können Ergebnisse nur in einer K.-o.-Phase gespeichert werden.")
            phase.record_final_set_scores(match_id, set_scores)
            match = phase.final_match(match_id)
            standings: tuple[Standing, ...] = ()
            phase_finished = phase.final_is_finished
        else:
            if phase.phase_type is not PhaseType.GROUP_STAGE:
                raise ValidationError("Gruppenergebnisse können nur in einer Gruppenphase gespeichert werden.")
            group = phase.group(group_id)
            if group.playoff_match is not None and group.playoff_match.id == match_id:
                match = group.playoff_match
                match.set_result_from_sets(set_scores)
            else:
                match = group.match(match_id)
                match.set_result_from_sets(set_scores)
            standings = tuple(group.standings(tournament.people))
            phase_finished = all(item.qualification_ready for item in phase.groups)

        # Match.set_result_from_sets sets FINISHED; timestamps are completed here
        # so direct result entry and the explicit live-status workflow behave alike.
        now = datetime.now().isoformat(timespec="seconds")
        if not match.started_at:
            match.started_at = now
        match.finished_at = now
        match.live_status = LiveMatchStatus.FINISHED

        self.save()
        new_result = {
            "home_score": match.result.home_score,
            "away_score": match.result.away_score,
            "set_scores": [list(score) for score in match.result.set_scores],
        }
        append_audit = getattr(self.repository, "append_result_audit", None)
        if append_audit is not None:
            append_audit(
                tournament_id=tournament.id, phase_id=phase_id, group_id=group_id, match_id=match_id,
                changed_at=now, changed_by=changed_by, reason=correction_reason if is_correction else "Ersterfassung",
                old_result=old_result, new_result=new_result,
            )
        self.auto_advance_completed_phases()

        # Der feste Freudenholm-Ablauf setzt sich nach dem letzten
        # Qualifikationsergebnis beziehungsweise nach der Zwischenrunde selbst
        # fort. Dadurch endet der Leitstand nicht erneut in einer Sackgasse.
        if (
            phase.phase_type is PhaseType.GROUP_STAGE
            and len(phase.groups) in {4, 5}
            and phase.next_phase_id is not None
            and all(group.qualification_ready for group in phase.groups)
        ):
            target = tournament.phase(phase.next_phase_id)
            target_filled = (
                bool(target.participant_ids)
                if target.phase_type is PhaseType.FINAL_ROUND
                else any(group.participant_ids for group in target.groups)
            )
            if not target_filled:
                self.advance_phase(phase.id)
                if target.phase_type is PhaseType.GROUP_STAGE:
                    self.generate_phase_schedule(
                        target.id, tables=tournament.table_count, start_time=tournament.schedule_start_time,
                        duration_minutes=tournament.match_duration_minutes, strategy="fair", replace=False,
                    )
                elif not target.matches:
                    self.generate_final_schedule(target.id)

        created_ids = tuple(item.id for item in phase.matches if item.id not in before_ids)
        self._log_live_event(
            "result_corrected" if old_result is not None else "result_recorded",
            "Ergebnis korrigiert" if old_result is not None else "Ergebnis gespeichert",
            details=(correction_reason if old_result is not None else f"{match.result.home_score}:{match.result.away_score}"),
            phase_id=phase_id, group_id=group_id, match_id=match_id, table_number=current_match.table_number,
        )
        return LiveResultOutcome(
            phase_id=phase_id, group_id=group_id, match_id=match_id,
            winner_id=match.winner_id(), home_sets=match.result.home_score,
            away_sets=match.result.away_score, standings=standings,
            created_match_ids=created_ids, phase_finished=phase_finished,
        )

    def _ensure_live_correction_safe(
        self, phase: TournamentPhase, group_id: UUID | None, match: Match
    ) -> None:
        tournament = self.require_tournament()
        if group_id is not None and phase.next_phase_id is not None:
            target = tournament.phase(phase.next_phase_id)
            target_has_players = bool(target.participant_ids) or any(group.participant_ids for group in target.groups)
            target_has_matches = bool(target.matches) or any(group.matches for group in target.groups)
            if target_has_players or target_has_matches:
                raise ValidationError(
                    "Die Folgephase wurde bereits befüllt. Setze sie vor dieser Korrektur bewusst zurück."
                )
        if group_id is None and match.result is not None:
            old_winner = match.winner_id()
            dependent = [
                candidate for candidate in phase.matches
                if candidate.round_number > match.round_number
                and old_winner in {candidate.home_id, candidate.away_id}
            ]
            if dependent:
                raise ValidationError(
                    "Aus diesem Ergebnis wurden bereits Folgepaarungen erzeugt. "
                    "Setze die abhängigen K.-o.-Runden vor der Korrektur zurück."
                )

    def live_result_audit(self, match_id: UUID | None = None) -> tuple[dict, ...]:
        reader = getattr(self.repository, "result_audit", None)
        return tuple(reader(match_id)) if reader is not None else ()

    def generate_group_schedule(self, phase_id: UUID, group_id: UUID, replace: bool = False) -> list[Match]:
        matches = self.require_tournament().phase(phase_id).group(group_id).generate_schedule(replace)
        self.save()
        return matches

    def clear_group_schedule(self, phase_id: UUID, group_id: UUID) -> None:
        """Remove a group's schedule so its roster can be edited again.

        Existing results, table assignments and times are discarded intentionally.
        """
        phase = self.require_tournament().phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Spielpläne können nur in einer Gruppenphase zurückgesetzt werden.")
        group = phase.group(group_id)
        if not group.matches:
            raise ValidationError("Für diese Gruppe existiert noch kein Spielplan.")
        group.matches.clear()
        self.save()

    def reset_group_schedule(self, phase_id: UUID, group_id: UUID) -> None:
        """Compatibility alias for clearing a group schedule before roster edits.

        The GUI historically used ``reset_group_schedule`` while the engine
        exposes ``clear_group_schedule``. Keep one public operation name for
        both call sites so editing an existing group cannot crash at runtime.
        """
        self.clear_group_schedule(phase_id, group_id)

    def reset_intermediate_round(self, phase_id: UUID) -> None:
        """Reset an intermediate group stage so it can be assigned again.

        The source group stage and qualification play-offs are preserved. All
        schedules, results and assignments in the selected intermediate phase
        are removed. Later phases are cleared because they depend on this draw.
        """
        tournament = self.require_tournament()
        phase = tournament.phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Nur eine Gruppenphase kann neu eingeteilt werden.")

        for group in phase.groups:
            group.matches.clear()
            group.participant_ids.clear()
        phase.groups.clear()
        for index in range(4):
            phase.add_group(f"Gruppe {chr(70 + index)}")

        for later in tournament.phases:
            if later.position <= phase.position:
                continue
            if later.phase_type is PhaseType.GROUP_STAGE:
                for group in later.groups:
                    group.matches.clear()
                    group.participant_ids.clear()
            else:
                later.matches.clear()
                later.participant_ids.clear()
                later.waiting_ids.clear()
        self.save()

    def reset_phase_groups(self, phase_id: UUID) -> None:
        """Clear group assignments and schedules while keeping all players.

        Later phases are cleared as well because their participants and matches
        depend on the selected group stage. Group names themselves are kept so
        the user can immediately start a fresh assignment.
        """
        tournament = self.require_tournament()
        phase = tournament.phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Nur eine Gruppenphase kann zurückgesetzt werden.")
        if not phase.groups:
            raise ValidationError("In dieser Phase existieren noch keine Gruppen.")

        for group in phase.groups:
            group.matches.clear()
            group.participant_ids.clear()

        for later in tournament.phases:
            if later.position <= phase.position:
                continue
            if later.phase_type is PhaseType.GROUP_STAGE:
                for group in later.groups:
                    group.matches.clear()
                    group.participant_ids.clear()
            else:
                later.matches.clear()
                later.participant_ids.clear()
                later.waiting_ids.clear()
        self.save()

    def clear_phase_schedules(self, phase_id: UUID) -> int:
        """Remove every existing group schedule in a group phase."""
        phase = self.require_tournament().phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Spielpläne können nur in einer Gruppenphase zurückgesetzt werden.")
        cleared = 0
        for group in phase.groups:
            if group.matches:
                group.matches.clear()
                cleared += 1
        if not cleared:
            raise ValidationError("In dieser Phase existiert noch kein Spielplan.")
        self.save()
        return cleared

    def generate_phase_schedule(
        self, phase_id: UUID, tables: int | None = None, start_time: str | None = None,
        duration_minutes: int | None = None, replace: bool = False, strategy: str = "smart",
    ) -> list[tuple[UUID, Match]]:
        tournament = self.require_tournament()
        tables = tournament.table_count if tables is None else tables
        start_time = tournament.schedule_start_time if start_time is None else start_time
        duration_minutes = tournament.match_duration_minutes if duration_minutes is None else duration_minutes
        if tables < 1:
            raise ValidationError("Es wird mindestens ein Feld benötigt.")
        if duration_minutes < 1:
            raise ValidationError("Die Spieldauer muss mindestens eine Minute betragen.")
        try:
            start = datetime.strptime(start_time.strip(), "%H:%M")
        except ValueError as exc:
            raise ValidationError("Die Startzeit muss im Format HH:MM angegeben werden.") from exc
        phase = self.require_tournament().phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Ein gemeinsamer Spielplan kann nur für eine Gruppenphase erstellt werden.")
        if not phase.groups:
            raise ValidationError("Bitte zuerst Gruppen anlegen.")

        # Gruppengrößen sind vollständig dynamisch. Für den gemeinsamen
        # Spielplan werden nur Gruppen mit mindestens zwei Spielern
        # berücksichtigt; leere oder noch nicht spielbereite Gruppen werden
        # übersprungen und blockieren die übrigen Gruppen nicht.
        playable_groups = [group for group in phase.groups if len(group.participant_ids) >= 2]
        if not playable_groups:
            raise ValidationError("Mindestens eine Gruppe mit zwei Spielern wird benötigt.")

        for group in playable_groups:
            if not group.matches or replace:
                group.generate_schedule(replace=bool(group.matches))

        pending = [
            (group.id, match)
            for group in playable_groups
            for match in group.matches
        ]
        planned = assign_schedule(
            pending,
            tables=tables,
            start=start,
            duration_minutes=duration_minutes,
            strategy=strategy,
        )
        self.save()
        return planned

    def sync_phase_schedule_to_defaults(self, phase_id: UUID, strategy: str = "smart") -> bool:
        """Re-time an untouched group-stage schedule to the tournament planning defaults.

        This is intentionally conservative: once any result exists or a match has
        left PLANNED state, the live schedule is not changed automatically.
        """
        tournament = self.require_tournament()
        phase = tournament.phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            return False
        matches = [match for group in phase.groups for match in group.matches]
        if not matches:
            return False
        if any(match.result is not None or match.live_status is not LiveMatchStatus.PLANNED for match in matches):
            return False

        scheduled = [match for match in matches if match.scheduled_time and match.table_number is not None]
        needs_sync = len(scheduled) != len(matches)
        if scheduled and not needs_sync:
            times = sorted({match.scheduled_time for match in scheduled if match.scheduled_time})
            if not times or times[0] != tournament.schedule_start_time:
                needs_sync = True
            if any((match.table_number or 0) > tournament.table_count for match in scheduled):
                needs_sync = True
            if len(times) >= 2 and not needs_sync:
                parsed = [datetime.strptime(value, "%H:%M") for value in times]
                expected = tournament.match_duration_minutes
                gaps = [int((parsed[i] - parsed[i-1]).total_seconds() // 60) for i in range(1, len(parsed))]
                if any(gap != expected for gap in gaps):
                    needs_sync = True

        if not needs_sync:
            return False

        self.generate_phase_schedule(
            phase_id,
            tables=tournament.table_count,
            start_time=tournament.schedule_start_time,
            duration_minutes=tournament.match_duration_minutes,
            replace=False,
            strategy=strategy,
        )
        return True

    def phase_schedule_quality(self, phase_id: UUID) -> dict[str, object]:
        """Summarize schedule quality for a group phase without changing tournament data."""
        phase = self.require_tournament().phase(phase_id)
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Die Spielplanprüfung ist nur für Gruppenphasen verfügbar.")

        matches = [match for group in phase.groups for match in group.matches]
        scheduled = [
            match for match in matches
            if match.scheduled_time and match.table_number is not None
        ]
        if not scheduled:
            return {
                "total_matches": len(matches),
                "scheduled_matches": 0,
                "slots": 0,
                "max_parallel": 0,
                "average_table_utilization": 0.0,
                "simultaneous_conflicts": 0,
                "back_to_back_appearances": 0,
                "table_balance_spread": 0,
                "rating": "Nicht geplant",
            }

        by_time: dict[str, list[Match]] = {}
        table_usage: dict[int, int] = {}
        for match in scheduled:
            by_time.setdefault(match.scheduled_time or "", []).append(match)
            table = int(match.table_number or 0)
            if table:
                table_usage[table] = table_usage.get(table, 0) + 1

        simultaneous_conflicts = 0
        slot_players: list[set[UUID]] = []
        for time in sorted(by_time):
            seen: set[UUID] = set()
            for match in by_time[time]:
                players = {match.home_id, match.away_id}
                simultaneous_conflicts += len(players & seen)
                seen.update(players)
            slot_players.append(seen)

        back_to_back = sum(
            len(slot_players[index - 1] & slot_players[index])
            for index in range(1, len(slot_players))
        )

        configured_tables = max(
            1,
            int(getattr(self.require_tournament(), "table_count", 1) or 1),
            max(table_usage, default=1),
        )
        slots = len(by_time)
        utilization = (
            len(scheduled) / (slots * configured_tables) * 100.0
            if slots else 0.0
        )
        spread = (
            max(table_usage.get(table, 0) for table in range(1, configured_tables + 1))
            - min(table_usage.get(table, 0) for table in range(1, configured_tables + 1))
            if configured_tables else 0
        )
        max_parallel = max((len(items) for items in by_time.values()), default=0)

        if simultaneous_conflicts:
            rating = "Prüfen"
        elif back_to_back == 0 and utilization >= 70:
            rating = "Sehr gut"
        elif back_to_back <= max(1, len(scheduled) // 10) and utilization >= 55:
            rating = "Gut"
        else:
            rating = "Ordentlich"

        return {
            "total_matches": len(matches),
            "scheduled_matches": len(scheduled),
            "slots": slots,
            "max_parallel": max_parallel,
            "average_table_utilization": round(utilization, 1),
            "simultaneous_conflicts": simultaneous_conflicts,
            "back_to_back_appearances": back_to_back,
            "table_balance_spread": spread,
            "rating": rating,
        }

    def _continue_freudenholm_phase_if_ready(self, phase_id: UUID) -> PhaseAdvanceResult | None:
        """Continue the established 5-group -> 4-group -> KO Freudenholm flow.

        This deliberately targets the canonical tournament shape only. Generic
        tournaments keep their existing manual/``auto_advance`` behavior. The
        helper never replaces an already populated target phase or an existing
        schedule.
        """
        tournament = self.require_tournament()
        phase = tournament.phase(phase_id)
        if (
            phase.phase_type is not PhaseType.GROUP_STAGE
            or len(phase.groups) not in {4, 5}
            or phase.next_phase_id is None
            or not all(group.qualification_ready for group in phase.groups)
        ):
            return None

        target = tournament.phase(phase.next_phase_id)
        canonical_transition = (
            len(phase.groups) == 5
            and target.phase_type is PhaseType.GROUP_STAGE
            and len(target.groups) == 4
        ) or (
            len(phase.groups) == 4
            and target.phase_type is PhaseType.FINAL_ROUND
        )
        if not canonical_transition:
            return None

        target_filled = (
            bool(target.participant_ids)
            if target.phase_type is PhaseType.FINAL_ROUND
            else any(group.participant_ids for group in target.groups)
        )
        result = None
        if not target_filled:
            result = self.advance_phase(phase.id)
            tournament = self.require_tournament()
            target = tournament.phase(phase.next_phase_id)

        if target.phase_type is PhaseType.GROUP_STAGE:
            playable = [group for group in target.groups if len(group.participant_ids) >= 2]
            if playable and not any(group.matches for group in target.groups):
                self.generate_phase_schedule(
                    target.id,
                    tables=tournament.table_count,
                    start_time=tournament.schedule_start_time,
                    duration_minutes=tournament.match_duration_minutes,
                    strategy="fair",
                    replace=False,
                )
        elif target.participant_ids and not target.matches:
            self.generate_final_schedule(target.id)
        return result

    def record_group_result(
        self, phase_id: UUID, group_id: UUID, match_id: UUID,
        home_score: int | None, away_score: int | None,
    ) -> None:
        self.require_tournament().phase(phase_id).group(group_id).record_result(
            match_id, home_score, away_score
        )
        self.save()
        self.auto_advance_completed_phases()
        self._continue_freudenholm_phase_if_ready(phase_id)

    def record_group_set_scores(
        self, phase_id: UUID, group_id: UUID, match_id: UUID,
        set_scores: list[tuple[int, int]],
    ) -> None:
        match = self.require_tournament().phase(phase_id).group(group_id).match(match_id)
        match.set_result_from_sets(set_scores)
        self.save()
        self.auto_advance_completed_phases()
        self._continue_freudenholm_phase_if_ready(phase_id)

    def group_standings(self, phase_id: UUID, group_id: UUID) -> list[Standing]:
        tournament = self.require_tournament()
        return tournament.phase(phase_id).group(group_id).standings(tournament.people)

    def configure_phase_transition(
        self, source_phase_id: UUID, target_phase_id: UUID,
        *, auto_advance: bool = False, distribution_mode: str = "snake",
    ) -> None:
        tournament = self.require_tournament()
        source = tournament.phase(source_phase_id)
        target = tournament.phase(target_phase_id)
        if target.position <= source.position:
            raise ValidationError("Das Ziel muss eine spätere Turnierphase sein.")
        if distribution_mode not in {"snake", "balanced"}:
            raise ValidationError("Als Verteilung sind nur 'snake' und 'balanced' zulässig.")
        source.next_phase_id = target.id
        source.auto_advance = auto_advance
        source.distribution_mode = distribution_mode
        self.save()

    def clear_phase_transition(self, source_phase_id: UUID) -> None:
        phase = self.require_tournament().phase(source_phase_id)
        phase.next_phase_id = None
        phase.auto_advance = False
        self.save()

    def phase_advance_preview(self, source_phase_id: UUID) -> PhaseAdvancePreview:
        tournament = self.require_tournament()
        source = tournament.phase(source_phase_id)
        if source.next_phase_id is None:
            raise ValidationError("Für diese Phase ist keine Folgephase eingestellt.")
        target = tournament.phase(source.next_phase_id)
        if source.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Automatische Übergänge werden derzeit aus Gruppenphasen unterstützt.")
        if not source.groups:
            raise ValidationError("Die Quellphase enthält keine Gruppen.")
        unfinished = [
            group.name for group in source.groups
            if (group.qualification_count > 0 or group.qualification_playoff_applicable)
            and not group.qualification_ready
        ]
        if unfinished:
            raise ValidationError("Noch nicht abgeschlossene Gruppen oder Qualifikationsspiele: " + ", ".join(unfinished))

        ranked: list[list[UUID]] = []
        max_rank = 0
        for group in source.groups:
            # qualified_ids() enthält sowohl die direkt qualifizierten Spieler
            # als auch den Sieger des optionalen Spiels Platz 3 gegen Platz 4.
            ids = group.qualified_ids(tournament.people)
            ranked.append(ids)
            max_rank = max(max_rank, len(ids))
        ordered: list[UUID] = []
        for rank in range(max_rank):
            current = [ids[rank] for ids in ranked if rank < len(ids)]
            ordered.extend(current)
        if not ordered:
            raise ValidationError("In der Quellphase sind keine Qualifikationsplätze eingestellt.")
        if len(set(ordered)) != len(ordered):
            raise ValidationError("Ein Spieler ist mehrfach als Qualifikant enthalten.")

        # Startfix 42: explicitly configured target capacities make the qualification
        # rules authoritative. Legacy projects without capacities keep their previous
        # auto-fill behavior for backwards compatibility.
        explicit_capacities = (
            target.phase_type is PhaseType.GROUP_STAGE
            and bool(target.groups)
            and all(group.target_capacity > 0 for group in target.groups)
        )
        if target.phase_type is PhaseType.GROUP_STAGE and tournament.intermediate_enabled:
            target_count = sum(self.intermediate_group_capacities(source.id))
            if explicit_capacities and len(ordered) != target_count:
                raise ValidationError(
                    f"Die Qualifikationsregeln liefern {len(ordered)} Spieler, die Zwischenrunde erwartet {target_count}. "
                    "Bitte Regeln oder Zielgrößen im Turnier-Designer anpassen."
                )
            if not explicit_capacities:
                assigned = {person_id for group in source.groups for person_id in group.participant_ids}
                if len(assigned) >= target_count and len(ordered) < target_count:
                    ranked_all: list[list[UUID]] = []
                    max_all_rank = 0
                    for group in source.groups:
                        ids = [standing.person_id for standing in group.standings(tournament.people)]
                        ranked_all.append(ids)
                        max_all_rank = max(max_all_rank, len(ids))
                    for rank in range(max_all_rank):
                        for ids in ranked_all:
                            if rank < len(ids) and ids[rank] not in ordered:
                                ordered.append(ids[rank])
                                if len(ordered) == target_count:
                                    break
                        if len(ordered) == target_count:
                            break

        assignments: dict[UUID, tuple[UUID, ...]] = {}
        if target.phase_type is PhaseType.FINAL_ROUND:
            assignments[target.id] = tuple(ordered)
        else:
            if not target.groups:
                raise ValidationError("Die Zielphase enthält keine Gruppen.")
            if any(group.matches for group in target.groups):
                raise ValidationError("Die Zielphase besitzt bereits erzeugte Gruppenspielpläne.")
            buckets: dict[UUID, list[UUID]] = {group.id: [] for group in target.groups}

            # Startfix 47: use dynamic group profiles for every newly configured
            # tournament. ``Offen`` is a wildcard; all other target profiles must
            # match the participant category. Capacity limits are authoritative.
            profile_targets = any((group.profile or "Offen").strip().casefold() != "offen" for group in target.groups)
            explicit_target_caps = all(group.target_capacity > 0 for group in target.groups)
            # Startfix 118: the women-only intermediate-round rule has priority
            # over generic target profiles. Otherwise an older target phase with
            # category/profile metadata can bypass the women-group reservation and
            # the "Automatisch fair verteilen" button mixes women and men again.
            women_plan = self._qualified_women_group_plan(ordered, target, source.id)
            reserved_group_ids: set[UUID] = set()
            remaining_people = list(ordered)
            if women_plan is not None:
                women_group, women, remaining_people = women_plan
                buckets[women_group.id].extend(women)
                reserved_group_ids.add(women_group.id)

            # Startfix 125: Amateur is a protected category just like Damen.
            # If at least three amateurs qualify and a target group with the exact
            # capacity exists, keep them together before distributing everybody else.
            amateur_plan = self._qualified_amateur_group_plan(remaining_people, target, reserved_group_ids)
            if amateur_plan is not None:
                amateur_group, amateurs, remaining_people = amateur_plan
                buckets[amateur_group.id].extend(amateurs)
                reserved_group_ids.add(amateur_group.id)

            if reserved_group_ids:
                remaining_groups = [group for group in target.groups if group.id not in reserved_group_ids]
                if remaining_people and not remaining_groups:
                    raise ValidationError("Die reservierten Zwischenrundengruppen lassen keinen Platz für die übrigen Qualifizierten.")
                snake_indices = list(range(len(remaining_groups))) + list(range(len(remaining_groups) - 1, -1, -1))
                cursor = 0
                for person_id in remaining_people:
                    placed = False
                    for _ in range(max(1, len(snake_indices) * 2)):
                        group = remaining_groups[snake_indices[cursor % len(snake_indices)]]
                        cursor += 1
                        if not explicit_target_caps or len(buckets[group.id]) < group.target_capacity:
                            buckets[group.id].append(person_id)
                            placed = True
                            break
                    if not placed:
                        raise ValidationError("Die übrigen Zwischenrundengruppen besitzen nicht genügend freie Plätze.")
            elif profile_targets:
                def compatible(group, person_id: UUID) -> bool:
                    wanted = (group.profile or "Offen").strip().casefold()
                    actual = (tournament.person(person_id).category or "Offen").strip().casefold()
                    return wanted == "offen" or wanted == actual

                for person_id in ordered:
                    candidates = [
                        group for group in target.groups
                        if compatible(group, person_id)
                        and (not explicit_target_caps or len(buckets[group.id]) < group.target_capacity)
                    ]
                    if not candidates:
                        person = tournament.person(person_id)
                        raise ValidationError(
                            f"Für {person.full_name} ({person.category}) gibt es in der Zielphase keinen passenden freien Gruppenplatz."
                        )
                    # Prefer an exact profile over an open wildcard, then the least
                    # filled group. This keeps category-specific target groups clean.
                    candidates.sort(key=lambda group: (
                        0 if (group.profile or "Offen").strip().casefold() != "offen" else 1,
                        len(buckets[group.id]),
                        group.name.casefold(),
                    ))
                    buckets[candidates[0].id].append(person_id)
            elif self.uses_gender_split_13_flow(source.id):
                if len(target.groups) != 4:
                    raise ValidationError("Der Frauen-/Männer-Modus benötigt genau vier Zwischenrundengruppen.")
                # Gender split follows the configured source groups, not a free-text
                # category field: A/B are the women's groups, C/D/E the men's groups.
                women_ids = {pid for group in source.groups[:2] for pid in group.participant_ids}
                women = [pid for pid in ordered if pid in women_ids]
                men = [pid for pid in ordered if pid not in women_ids]
                if len(women) != 4 or len(men) != 9:
                    raise ValidationError(
                        f"Für die Zwischenrunde werden 4 Frauen und 9 Männer benötigt; aktuell sind es {len(women)} Frauen und {len(men)} Männer."
                    )
                buckets[target.groups[0].id].extend(women)
                # The three men's groups each receive exactly three players.
                for offset, person_id in enumerate(men):
                    buckets[target.groups[1 + (offset % 3)].id].append(person_id)
            elif source.distribution_mode == "balanced":
                for person_id in ordered:
                    candidates = [
                        group for group in target.groups
                        if not explicit_target_caps or len(buckets[group.id]) < group.target_capacity
                    ]
                    if not candidates:
                        raise ValidationError("Die Zielgruppen besitzen nicht genügend freie Plätze für alle Qualifizierten.")
                    group = min(candidates, key=lambda g: (len(g.participant_ids) + len(buckets[g.id]), g.name.casefold()))
                    buckets[group.id].append(person_id)
            else:
                group_count = len(target.groups)
                snake_indices = list(range(group_count)) + list(range(group_count - 1, -1, -1))
                cursor = 0
                for person_id in ordered:
                    selected = None
                    attempts = 0
                    while attempts < len(snake_indices):
                        group = target.groups[snake_indices[cursor % len(snake_indices)]]
                        cursor += 1
                        attempts += 1
                        if not explicit_target_caps or len(buckets[group.id]) < group.target_capacity:
                            selected = group
                            break
                    if selected is None:
                        raise ValidationError("Die Zielgruppen besitzen nicht genügend freie Plätze für alle Qualifizierten.")
                    buckets[selected.id].append(person_id)
            assignments = {gid: tuple(ids) for gid, ids in buckets.items()}
        return PhaseAdvancePreview(source.id, target.id, tuple(ordered), assignments)

    def advance_phase_to_final(self, source_phase_id: UUID, *, limit: int | None = None, replace: bool = False) -> PhaseAdvanceResult:
        """Advance a group phase directly to a final round using the best N qualifiers.

        This is used when the optional intermediate round is disabled. The
        normal generic phase transition keeps its previous behavior.
        """
        tournament = self.require_tournament()
        preview = self.phase_advance_preview(source_phase_id)
        source = tournament.phase(source_phase_id)
        target = tournament.phase(preview.target_phase_id)
        if target.phase_type is not PhaseType.FINAL_ROUND:
            raise ValidationError("Das direkte Überspringen ist nur vor einer K.-o.-Phase möglich.")
        candidates = list(preview.qualified_ids)
        if limit is not None and len(candidates) < limit:
            ranked_all: list[list[UUID]] = []
            max_rank = 0
            for group in source.groups:
                ids = [standing.person_id for standing in group.standings(tournament.people)]
                ranked_all.append(ids)
                max_rank = max(max_rank, len(ids))
            for rank in range(max_rank):
                for ids in ranked_all:
                    if rank < len(ids) and ids[rank] not in candidates:
                        candidates.append(ids[rank])
                        if len(candidates) == limit:
                            break
                if len(candidates) == limit:
                    break
        if limit is not None and len(candidates) < limit:
            raise ValidationError(
                f"Für die direkte K.-o.-Phase werden mindestens {limit} Spieler benötigt; aktuell sind es {len(candidates)}."
            )
        selected = tuple(candidates if limit is None else candidates[:limit])
        if len(selected) < 2:
            raise ValidationError("Für eine K.-o.-Phase werden mindestens zwei qualifizierte Spieler benötigt.")
        if target.matches:
            raise ValidationError("Die Finalrunde wurde bereits ausgelost.")
        if target.participant_ids and not replace:
            raise ValidationError("Die Finalrunde enthält bereits Spieler. Bitte Ersetzen bestätigen.")
        if replace:
            target.participant_ids.clear()
        for person_id in selected:
            target.add_final_participant(person_id)
        self.save()
        return PhaseAdvanceResult(source.id, target.id, selected, {target.id: selected})

    def advance_phase(self, source_phase_id: UUID, *, replace: bool = False) -> PhaseAdvanceResult:
        tournament = self.require_tournament()
        preview = self.phase_advance_preview(source_phase_id)
        source = tournament.phase(source_phase_id)
        target = tournament.phase(preview.target_phase_id)
        if target.phase_type is PhaseType.FINAL_ROUND:
            if target.matches:
                raise ValidationError("Die Finalrunde wurde bereits ausgelost.")
            if target.participant_ids and not replace:
                raise ValidationError("Die Finalrunde enthält bereits Spieler. Bitte Ersetzen bestätigen.")
            if replace:
                target.participant_ids.clear()

            # STARTFIX 154: Bei einer klassischen Zwischenrunde mit vier Gruppen
            # und je zwei Qualifikanten werden die Viertelfinals gesetzt:
            # Gruppensieger spielen ausschließlich gegen Gruppenzweite. Außerdem
            # vermeiden wir ein sofortiges Rematch aus derselben Zwischenrundengruppe.
            # Paarungsschema: A1-B2, B1-A2, C1-D2, D1-C2.
            seeded_ids = list(preview.qualified_ids)
            if len(source.groups) == 4 and len(seeded_ids) == 8:
                qualifiers_by_group: list[list[UUID]] = []
                valid_seed = True
                for group in source.groups:
                    try:
                        qualified = group.qualified_ids(tournament.people)
                    except ValidationError:
                        valid_seed = False
                        break
                    if len(qualified) != 2:
                        valid_seed = False
                        break
                    qualifiers_by_group.append(qualified)
                if valid_seed and len({pid for q in qualifiers_by_group for pid in q}) == 8:
                    a, b, c, d = qualifiers_by_group
                    seeded_ids = [a[0], b[1], b[0], a[1], c[0], d[1], d[0], c[1]]

            for person_id in seeded_ids:
                target.add_final_participant(person_id)
        else:
            if any(group.participant_ids for group in target.groups) and not replace:
                raise ValidationError("Die Zielgruppen enthalten bereits Spieler. Bitte Ersetzen bestätigen.")
            if replace:
                for group in target.groups:
                    if group.matches:
                        raise ValidationError("Eine Zielgruppe besitzt bereits einen Spielplan.")
                    group.participant_ids.clear()
            for group_id, person_ids in preview.assignments.items():
                group = target.group(group_id)
                for person_id in person_ids:
                    if person_id not in group.participant_ids:
                        target.assign_participant(group.id, person_id)
        self.save()
        return PhaseAdvanceResult(source.id, target.id, preview.qualified_ids, preview.assignments)

    def auto_advance_completed_phases(self) -> list[PhaseAdvanceResult]:
        tournament = self.require_tournament()
        results: list[PhaseAdvanceResult] = []
        for phase in sorted(tournament.phases, key=lambda value: value.position):
            if not phase.auto_advance or phase.next_phase_id is None:
                continue
            target = tournament.phase(phase.next_phase_id)
            already_filled = bool(target.participant_ids) if target.phase_type is PhaseType.FINAL_ROUND else any(g.participant_ids for g in target.groups)
            if already_filled:
                continue
            try:
                results.append(self.advance_phase(phase.id))
            except ValidationError as exc:
                if not str(exc).startswith("Noch nicht abgeschlossene Gruppen"):
                    raise
        return results

    def qualify_top_players(
        self, source_phase_id: UUID, source_group_id: UUID,
        target_phase_id: UUID, target_group_id: UUID, count: int,
    ) -> QualificationResult:
        tournament = self.require_tournament()
        if count < 1:
            raise ValidationError("Es muss mindestens ein Qualifikationsplatz ausgewählt werden.")
        source_phase = tournament.phase(source_phase_id)
        target_phase = tournament.phase(target_phase_id)
        if source_phase.phase_type is not PhaseType.GROUP_STAGE or target_phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Qualifikationen zwischen Gruppen sind nur für Gruppenphasen möglich.")
        if target_phase.position <= source_phase.position:
            raise ValidationError("Das Ziel muss eine spätere Turnierphase sein.")
        source_group = source_phase.group(source_group_id)
        target_group = target_phase.group(target_group_id)
        if not source_group.is_finished:
            raise ValidationError("Die Quellgruppe muss vollständig abgeschlossen sein.")
        if target_group.matches:
            raise ValidationError("Der Zielgruppe wurde bereits ein Spielplan zugeordnet.")
        standings = source_group.standings(tournament.people)
        if count > len(standings):
            raise ValidationError("Die Quellgruppe enthält nicht genügend Spieler.")
        qualified_list = [row.person_id for row in standings[:count]]
        if source_group.qualification_playoff:
            if source_group.playoff_match is None or source_group.playoff_match.result is None:
                raise ValidationError("Das Qualifikationsspiel dieser Gruppe ist noch nicht abgeschlossen.")
            winner = source_group.playoff_match.winner_id()
            if winner not in qualified_list:
                qualified_list.append(winner)
        qualified = tuple(qualified_list)
        assigned_elsewhere = {
            person_id
            for group in target_phase.groups
            if group.id != target_group.id
            for person_id in group.participant_ids
        }
        conflicts = [person_id for person_id in qualified if person_id in assigned_elsewhere]
        if conflicts:
            raise ValidationError("Mindestens ein qualifizierter Spieler ist in der Zielphase bereits einer anderen Gruppe zugeordnet.")
        for person_id in qualified:
            if person_id not in target_group.participant_ids:
                target_group.add_participant(person_id)
        self.save()
        return QualificationResult(source_phase_id, source_group_id, target_phase_id, target_group_id, qualified)


    def qualify_all_groups(
        self, source_phase_id: UUID, target_phase_id: UUID, replace: bool = False,
    ) -> BulkQualificationResult:
        """Transfer all direct qualifiers to a later group phase.

        Qualifiers are collected rank by rank and distributed evenly across the
        target groups. This keeps the target groups balanced while avoiding any
        fixed assumptions about source or target group sizes.
        """
        tournament = self.require_tournament()
        source_phase = tournament.phase(source_phase_id)
        target_phase = tournament.phase(target_phase_id)
        if source_phase.phase_type is not PhaseType.GROUP_STAGE or target_phase.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Die Sammelqualifikation ist nur zwischen Gruppenphasen möglich.")
        if target_phase.position <= source_phase.position:
            raise ValidationError("Das Ziel muss eine spätere Turnierphase sein.")
        if not source_phase.groups:
            raise ValidationError("Die Quellphase enthält keine Gruppen.")
        if not target_phase.groups:
            raise ValidationError("Die Zielphase enthält keine Gruppen.")
        unfinished = [group.name for group in source_phase.groups if (group.qualification_count > 0 or group.qualification_playoff_applicable) and not group.qualification_ready]
        if unfinished:
            raise ValidationError("Noch nicht abgeschlossene Gruppen: " + ", ".join(unfinished))
        if any(group.matches for group in target_phase.groups):
            raise ValidationError("Mindestens eine Zielgruppe besitzt bereits einen Spielplan.")
        if any(group.participant_ids for group in target_phase.groups) and not replace:
            raise ValidationError("Die Zielgruppen enthalten bereits Spieler. Bitte die Übertragung mit Ersetzen bestätigen.")
        if replace:
            for group in target_phase.groups:
                group.participant_ids.clear()

        ranked_by_group: list[list[UUID]] = []
        maximum_places = 0
        for group in source_phase.groups:
            qualified = group.qualified_ids(tournament.people)
            ranked_by_group.append(qualified)
            maximum_places = max(maximum_places, len(qualified))

        ordered: list[UUID] = []
        for rank in range(maximum_places):
            for qualified in ranked_by_group:
                if rank < len(qualified):
                    ordered.append(qualified[rank])
        if not ordered:
            raise ValidationError("In der Quellphase sind keine direkten Qualifikationsplätze eingestellt.")
        if len(set(ordered)) != len(ordered):
            raise ValidationError("Ein Spieler ist in mehreren Quellgruppen als Qualifikant enthalten.")

        assignments: dict[UUID, list[UUID]] = {group.id: list(group.participant_ids) for group in target_phase.groups}
        start_index = sum(len(players) for players in assignments.values())
        for offset, person_id in enumerate(ordered):
            target_group = target_phase.groups[(start_index + offset) % len(target_phase.groups)]
            if person_id not in target_group.participant_ids:
                target_group.add_participant(person_id)
                assignments[target_group.id].append(person_id)
        self.save()
        return BulkQualificationResult(
            source_phase_id, target_phase_id,
            {group_id: tuple(players) for group_id, players in assignments.items()},
        )


    def qualify_top_players_to_final(
        self, source_phase_id: UUID, source_group_id: UUID, target_phase_id: UUID, count: int,
    ) -> tuple[UUID, ...]:
        tournament = self.require_tournament()
        if count < 1:
            raise ValidationError("Es muss mindestens ein Qualifikationsplatz ausgewählt werden.")
        source_phase = tournament.phase(source_phase_id)
        target_phase = tournament.phase(target_phase_id)
        if source_phase.phase_type is not PhaseType.GROUP_STAGE or target_phase.phase_type is not PhaseType.FINAL_ROUND:
            raise ValidationError("Diese Qualifikation benötigt eine Gruppe als Quelle und eine Finalrunde als Ziel.")
        if target_phase.position <= source_phase.position:
            raise ValidationError("Das Ziel muss eine spätere Turnierphase sein.")
        source_group = source_phase.group(source_group_id)
        if not source_group.is_finished:
            raise ValidationError("Die Quellgruppe muss vollständig abgeschlossen sein.")
        if target_phase.matches:
            raise ValidationError("Die Finalrunde wurde bereits ausgelost.")
        standings = source_group.standings(tournament.people)
        if count > len(standings):
            raise ValidationError("Die Gruppe enthält nicht genügend Spieler.")
        qualified_ids = tuple(row.person_id for row in standings[:count])
        for person_id in qualified_ids:
            target_phase.add_final_participant(person_id)
        self.save()
        return qualified_ids

    def simulate_tournament(self, seed: int = 2026) -> TournamentSimulationReport:
        """Play the configured tournament through to the final winner.

        Existing results are preserved. Only missing schedules, open matches and
        required phase transitions are completed. The seeded generator makes
        every test run reproducible. This is intended for pre-event validation,
        not for production results.
        """
        tournament = self.require_tournament()
        rng = random.Random(seed)
        simulated_matches = 0
        created_schedules = 0
        advanced_phases = 0
        log: list[str] = []

        # Older Freudenholm projects often contain only the first group stage.
        # Repairing the flow is non-destructive and gives the simulation a
        # complete path through intermediate and final rounds.
        self.ensure_freudenholm_flow()
        tournament = self.require_tournament()

        def score() -> tuple[int, int]:
            home_wins = bool(rng.getrandbits(1))
            loser_sets = rng.choice((0, 1))
            return (2, loser_sets) if home_wins else (loser_sets, 2)

        phases = sorted(tournament.phases, key=lambda item: item.position)
        for phase in phases:
            if phase.phase_type is PhaseType.GROUP_STAGE:
                for group in phase.groups:
                    if len(group.participant_ids) < 2:
                        raise ValidationError(f"{group.name} enthält weniger als zwei Spieler.")
                    if not group.matches:
                        group.generate_schedule()
                        created_schedules += 1
                        log.append(f"Spielplan für {phase.name} / {group.name} erzeugt.")
                    for match in group.matches:
                        if match.result is None:
                            match.set_result(*score())
                            simulated_matches += 1
                    if group.qualification_playoff:
                        playoff = group.ensure_playoff_match()
                        if playoff is not None and playoff.result is None:
                            playoff.set_result(*score())
                            simulated_matches += 1
                            log.append(f"Qualifikationsspiel in {group.name} simuliert.")

                if phase.next_phase_id is not None:
                    target = tournament.phase(phase.next_phase_id)
                    target_filled = (
                        bool(target.participant_ids)
                        if target.phase_type is PhaseType.FINAL_ROUND
                        else any(group.participant_ids for group in target.groups)
                    )
                    if not target_filled:
                        result = self.advance_phase(phase.id)
                        advanced_phases += 1
                        log.append(
                            f"{len(result.qualified_ids)} Qualifizierte von {phase.name} nach {target.name} übertragen."
                        )

            else:
                if not phase.matches:
                    phase.generate_final_schedule()
                    created_schedules += 1
                    log.append("K.-o.-Spielplan erzeugt.")
                while not phase.final_is_finished:
                    current_round = max(match.round_number for match in phase.matches)
                    open_matches = [
                        match for match in phase.matches
                        if match.round_number == current_round and match.result is None
                    ]
                    if not open_matches:
                        raise ValidationError("Die K.-o.-Simulation konnte nicht fortgesetzt werden.")
                    for match in open_matches:
                        phase.record_final_result(match.id, *score())
                        simulated_matches += 1
                    log.append(f"K.-o.-Runde {current_round} simuliert.")

        final_phase = next(
            (phase for phase in phases if phase.phase_type is PhaseType.FINAL_ROUND),
            None,
        )
        winner_id = final_phase.final_winner_id if final_phase else None
        completed = bool(final_phase and final_phase.final_is_finished and winner_id)
        self.save()
        return TournamentSimulationReport(
            simulated_matches=simulated_matches,
            created_schedules=created_schedules,
            advanced_phases=advanced_phases,
            winner_id=winner_id,
            completed=completed,
            log=tuple(log),
        )


    def create_competition(self, name: str, format: CompetitionFormat = CompetitionFormat.ROUND_ROBIN, swiss_rounds: int = 5) -> Competition:
        competition = self.require_tournament().add_competition(name, format)
        competition.swiss_rounds = swiss_rounds
        self.save()
        return competition

    def competition(self, competition_id: UUID) -> Competition:
        for competition in self.require_tournament().competitions:
            if competition.id == competition_id:
                return competition
        raise ValidationError("Wettbewerb wurde nicht gefunden.")

    def register_player(self, competition_id: UUID, person_id: UUID) -> None:
        self.competition(competition_id).register(person_id)
        self.save()

    def unregister_player(self, competition_id: UUID, person_id: UUID) -> None:
        self.competition(competition_id).unregister(person_id)
        self.save()

    def start_competition(self, competition_id: UUID) -> None:
        self.competition(competition_id).start()
        self.save()

    def record_result(self, competition_id: UUID, match_id: UUID, home_score: int | None, away_score: int | None) -> None:
        competition = self.competition(competition_id)
        match = self._match(competition, match_id)
        if home_score is None and away_score is None:
            match.result = None
        elif home_score is None or away_score is None:
            raise ValidationError("Bitte beide Ergebnisfelder ausfüllen oder beide leer lassen.")
        else:
            competition.set_match_result(match, home_score, away_score)
        self.save()

    def finish_competition(self, competition_id: UUID) -> None:
        self.competition(competition_id).finish()
        self.save()

    @staticmethod
    def _match(competition: Competition, match_id: UUID) -> Match:
        for match in competition.matches:
            if match.id == match_id:
                return match
        raise ValidationError("Spiel wurde nicht gefunden.")

    @staticmethod
    def _normalize_import_cell(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    def _preview_player_rows(self, rows: list[list[object]]) -> PlayerImportPreview:
        tournament = self.require_tournament()
        existing = {(p.first_name.casefold(), p.last_name.casefold()) for p in tournament.people}
        players: list[tuple[str, str, str, str, int | None, str]] = []
        duplicates: list[tuple[str, str, str, str, int | None, str]] = []
        invalid_rows: list[int] = []
        seen = set(existing)
        for row_number, raw_row in enumerate(rows, start=1):
            row = [self._normalize_import_cell(value) for value in raw_row]
            if not any(row):
                continue
            first_cell = row[0].casefold() if row else ""
            if row_number == 1 and first_cell in {"vorname", "first_name", "firstname"}:
                continue
            if len(row) < 2 or not row[0] or not row[1]:
                invalid_rows.append(row_number)
                continue
            start_number = None
            if len(row) > 4 and row[4]:
                try:
                    start_number = int(row[4])
                    if start_number < 1:
                        raise ValueError
                except ValueError:
                    invalid_rows.append(row_number)
                    continue
            player = (
                row[0], row[1], row[2] if len(row) > 2 else "",
                (row[3] if len(row) > 3 else "") or "Offen", start_number,
                row[5] if len(row) > 5 else "",
            )
            key = (player[0].casefold(), player[1].casefold())
            if key in seen:
                duplicates.append(player)
                continue
            seen.add(key)
            players.append(player)
        return PlayerImportPreview(players, duplicates, invalid_rows)

    def preview_player_import(self, path: Path) -> PlayerImportPreview:
        suffix = path.suffix.casefold()
        if suffix == ".xlsx":
            if load_workbook is None:
                raise ValidationError("Für den Excel-Import fehlt das Paket openpyxl.")
            try:
                workbook = load_workbook(path, read_only=True, data_only=True)
                sheet = workbook.active
                rows = [list(row) for row in sheet.iter_rows(values_only=True)]
                workbook.close()
            except (OSError, ValueError, KeyError) as error:
                raise ValidationError("Die Excel-Datei konnte nicht gelesen werden.") from error
            return self._preview_player_rows(rows)

        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                sample = handle.read(4096)
                handle.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
                except csv.Error:
                    dialect = csv.excel
                    dialect.delimiter = ";"
                rows = [list(row) for row in csv.reader(handle, dialect)]
        except (OSError, UnicodeError, csv.Error) as error:
            raise ValidationError("Die CSV-Datei konnte nicht gelesen werden.") from error
        return self._preview_player_rows(rows)

    def import_players(self, path: Path) -> PlayerImportPreview:
        preview = self.preview_player_import(path)
        tournament = self.require_tournament()
        for first_name, last_name, club, category, start_number, license_number in preview.players:
            tournament.add_person(first_name, last_name, club, category, start_number, license_number)
        if preview.players:
            self.save()
        return preview

    def import_players_csv(self, path: Path) -> PlayerImportPreview:
        return self.import_players(path)

    def _database_path(self) -> Path:
        path = getattr(self.repository, "database_path", None)
        if path is None:
            raise ValidationError("Backups werden von diesem Speicher nicht unterstützt.")
        return Path(path)

    def _backup_checksum(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def create_backup(self, destination: Path | None = None, *, reason: str = "Manuelle Sicherung") -> Path:
        self.save()
        source = self._database_path()

        # macOS protects the filesystem root. A plain filename returned by some
        # native save dialogs can occasionally arrive as ``/fts-backup.db``.
        # Never attempt to write a backup there; keep backups inside the MSTTS
        # data directory instead.
        backup_dir = source.parent / "backups"
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            backup_dir = Path.home() / ".fts" / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)

        if destination is None:
            destination = backup_dir / f"fts-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
        else:
            destination = Path(destination).expanduser()
            if destination.parent == Path("/"):
                destination = backup_dir / destination.name
            else:
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    destination = backup_dir / destination.name

        try:
            shutil.copy2(source, destination)
        except OSError as error:
            # If the chosen destination is read-only or inaccessible, fall back
            # to the safe MSTTS backup directory instead of aborting the export.
            fallback = backup_dir / destination.name
            if fallback == destination:
                raise
            shutil.copy2(source, fallback)
            destination = fallback
        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "reason": reason,
            "database": destination.name,
            "sha256": self._backup_checksum(destination),
        }
        destination.with_suffix(destination.suffix + ".json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return destination

    def list_backups(self) -> list[Path]:
        source = self._database_path()
        backup_dir = source.parent / "backups"
        if not backup_dir.exists():
            return []
        return sorted(backup_dir.glob("fts-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)

    def backup_info(self, source: Path) -> dict:
        manifest_path = source.with_suffix(source.suffix + ".json")
        info = {
            "path": source,
            "created_at": datetime.fromtimestamp(source.stat().st_mtime).isoformat(timespec="seconds"),
            "reason": "Sicherung",
            "sha256": "",
            "valid": False,
        }
        if manifest_path.exists():
            try:
                info.update(json.loads(manifest_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                pass
        try:
            info["valid"] = self.verify_backup(source)
        except ValidationError:
            info["valid"] = False
        return info

    def create_automatic_backup(self, retention: int = 10, *, reason: str = "Automatische Sicherung") -> Path:
        if retention < 1:
            raise ValidationError("Es muss mindestens eine Sicherung aufbewahrt werden.")
        backup = self.create_backup(reason=reason)
        for old_backup in self.list_backups()[retention:]:
            old_backup.unlink(missing_ok=True)
            old_backup.with_suffix(old_backup.suffix + ".json").unlink(missing_ok=True)
        return backup

    def verify_backup(self, source: Path) -> bool:
        if not source.is_file():
            raise ValidationError("Die ausgewählte Sicherungsdatei wurde nicht gefunden.")
        manifest_path = source.with_suffix(source.suffix + ".json")
        if manifest_path.exists():
            try:
                expected = json.loads(manifest_path.read_text(encoding="utf-8")).get("sha256", "")
            except (OSError, ValueError, TypeError) as error:
                raise ValidationError("Die Prüfsummendatei der Sicherung ist beschädigt.") from error
            if expected and self._backup_checksum(source) != expected:
                raise ValidationError("Die Prüfsumme der Sicherung stimmt nicht überein.")
        import sqlite3
        try:
            with sqlite3.connect(source) as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise ValidationError("Die Sicherungsdatei ist beschädigt.")
                row = connection.execute("SELECT COUNT(*) FROM tournaments").fetchone()
                if not row or row[0] < 1:
                    raise ValidationError("Die Sicherung enthält kein Turnier.")
        except sqlite3.DatabaseError as error:
            raise ValidationError("Die Sicherungsdatei ist keine gültige FTS-Datenbank.") from error
        return True

    def restore_backup(self, source: Path) -> Tournament:
        self.verify_backup(source)
        destination = self._database_path()
        safety_copy = destination.with_suffix(destination.suffix + ".before-restore")
        if destination.exists():
            shutil.copy2(destination, safety_copy)
        shutil.copy2(source, destination)
        restored = self.repository.load()
        if restored is None:
            if safety_copy.exists():
                shutil.copy2(safety_copy, destination)
            raise ValidationError("Die Sicherung enthält kein Turnier.")
        self.tournament = restored
        return restored

    def export_backup_package(self, destination: Path) -> Path:
        backup = self.create_backup(reason="Exportpaket")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(backup, arcname="tournament.db")
            manifest = backup.with_suffix(backup.suffix + ".json")
            if manifest.exists():
                archive.write(manifest, arcname="manifest.json")
        return destination


    def repair_safe_tournament_references(self) -> dict[str, int]:
        """Repair stale participant references without discarding recorded results.

        Open matches that reference deleted participants can be removed safely. If a
        deleted participant is referenced by an already recorded result, a minimal
        archive participant with the original UUID is restored first. This keeps the
        result, standings and audit trail intact while removing the inconsistency.
        A backup is created before every mutation.
        """
        tournament = self.require_tournament()
        people_ids = {person.id for person in tournament.people}
        changed = {
            "groups": 0, "phases": 0, "registrations": 0, "matches": 0,
            "blocked_results": 0, "restored_people": 0,
        }

        all_matches = []
        for phase in tournament.phases:
            if phase.phase_type is PhaseType.GROUP_STAGE:
                for group in phase.groups:
                    all_matches.extend(group.matches)
                    if group.playoff_match is not None:
                        all_matches.append(group.playoff_match)
            else:
                all_matches.extend(phase.matches)
        for competition in tournament.competitions:
            all_matches.extend(competition.matches)

        # Preserve every completed result: restore the missing UUID as an archive
        # participant instead of deleting a scored match.
        restore_ids = {
            participant_id
            for match in all_matches if match.result is not None
            for participant_id in (match.home_id, match.away_id)
            if participant_id not in people_ids
        }

        stale_group_refs = 0
        stale_phase_refs = 0
        stale_registration_refs = 0
        removable_open_matches = 0
        for phase in tournament.phases:
            if phase.phase_type is PhaseType.GROUP_STAGE:
                for group in phase.groups:
                    stale_group_refs += sum(pid not in people_ids and pid not in restore_ids for pid in group.participant_ids)
                    removable_open_matches += sum(
                        (m.home_id not in people_ids or m.away_id not in people_ids) and m.result is None
                        for m in group.matches
                    )
                    if group.playoff_match is not None:
                        m = group.playoff_match
                        removable_open_matches += int(
                            (m.home_id not in people_ids or m.away_id not in people_ids) and m.result is None
                        )
            else:
                stale_phase_refs += sum(pid not in people_ids and pid not in restore_ids for pid in phase.participant_ids)
                removable_open_matches += sum(
                    (m.home_id not in people_ids or m.away_id not in people_ids) and m.result is None
                    for m in phase.matches
                )
        for competition in tournament.competitions:
            stale_registration_refs += sum(pid not in people_ids and pid not in restore_ids for pid in competition.registered_ids)
            removable_open_matches += sum(
                (m.home_id not in people_ids or m.away_id not in people_ids) and m.result is None
                for m in competition.matches
            )

        mutation_count = len(restore_ids) + stale_group_refs + stale_phase_refs + stale_registration_refs + removable_open_matches
        if not mutation_count:
            return changed

        self.create_backup(reason="Automatische Turnierreparatur")

        used_start_numbers = {person.start_number for person in tournament.people if person.start_number is not None}
        next_number = max(used_start_numbers, default=0) + 1
        for participant_id in sorted(restore_ids, key=str):
            while next_number in used_start_numbers:
                next_number += 1
            participant = Person(
                "Archiv",
                f"Teilnehmer {str(participant_id)[:8]}",
                club="Automatisch wiederhergestellt",
                category="Archiv",
                start_number=next_number,
                id=participant_id,
            )
            tournament.people.append(participant)
            used_start_numbers.add(next_number)
            next_number += 1
            changed["restored_people"] += 1

        people_ids = {person.id for person in tournament.people}

        def clean_matches(matches):
            kept = []
            for match in matches:
                invalid = match.home_id not in people_ids or match.away_id not in people_ids
                if invalid and match.result is None:
                    changed["matches"] += 1
                    continue
                if invalid:
                    changed["blocked_results"] += 1
                kept.append(match)
            return kept

        for phase in tournament.phases:
            if phase.phase_type is PhaseType.GROUP_STAGE:
                for group in phase.groups:
                    before = len(group.participant_ids)
                    group.participant_ids[:] = [pid for pid in group.participant_ids if pid in people_ids]
                    changed["groups"] += before - len(group.participant_ids)
                    group.matches[:] = clean_matches(group.matches)
                    if group.playoff_match is not None:
                        m = group.playoff_match
                        invalid = m.home_id not in people_ids or m.away_id not in people_ids
                        if invalid and m.result is None:
                            group.playoff_match = None
                            changed["matches"] += 1
                        elif invalid:
                            changed["blocked_results"] += 1
            else:
                before = len(phase.participant_ids)
                phase.participant_ids[:] = [pid for pid in phase.participant_ids if pid in people_ids]
                changed["phases"] += before - len(phase.participant_ids)
                phase.matches[:] = clean_matches(phase.matches)
        for competition in tournament.competitions:
            before = len(competition.registered_ids)
            competition.registered_ids[:] = [pid for pid in competition.registered_ids if pid in people_ids]
            changed["registrations"] += before - len(competition.registered_ids)
            competition.matches[:] = clean_matches(competition.matches)

        self.save()
        return changed

    def validate_tournament(self) -> list[TournamentCheckIssue]:
        tournament = self.require_tournament()
        issues: list[TournamentCheckIssue] = []

        if not tournament.people:
            issues.append(TournamentCheckIssue("Fehler", "Keine Teilnehmer", "Dem Turnier wurden noch keine Spieler hinzugefügt."))

        start_numbers: dict[int, list[str]] = {}
        for person in tournament.people:
            if person.start_number is not None:
                start_numbers.setdefault(person.start_number, []).append(person.full_name)
        for number, names in sorted(start_numbers.items()):
            if len(names) > 1:
                issues.append(TournamentCheckIssue("Fehler", f"Startnummer {number} mehrfach vergeben", ", ".join(names)))

        for competition in tournament.competitions:
            if len(competition.registered_ids) < 2:
                issues.append(TournamentCheckIssue("Fehler", f"Wettbewerb: {competition.name}", "Weniger als zwei Teilnehmer sind angemeldet."))
            open_matches = sum(match.result is None for match in competition.matches)
            if competition.status is CompetitionStatus.RUNNING and open_matches:
                issues.append(TournamentCheckIssue("Warnung", f"Offene Ergebnisse: {competition.name}", f"{open_matches} Begegnung(en) sind noch ohne Ergebnis."))
            if competition.status is CompetitionStatus.FINISHED and open_matches:
                issues.append(TournamentCheckIssue("Fehler", f"Unvollständiger Abschluss: {competition.name}", f"{open_matches} Begegnung(en) sind trotz Abschluss offen."))

        team_by_id = {team.id: team for team in tournament.teams}
        for competition in tournament.team_competitions:
            if len(competition.registered_team_ids) < 2:
                issues.append(TournamentCheckIssue("Fehler", f"Mannschaftswettbewerb: {competition.name}", "Weniger als zwei Mannschaften sind angemeldet."))
            for team_id in competition.registered_team_ids:
                team = team_by_id.get(team_id)
                if team is None:
                    issues.append(TournamentCheckIssue("Fehler", f"Mannschaftswettbewerb: {competition.name}", "Eine angemeldete Mannschaft wurde nicht gefunden."))
                    continue
                active = sum(member.active for member in team.members)
                if active < competition.boards:
                    issues.append(TournamentCheckIssue("Fehler", f"Unvollständige Aufstellung: {team.name}", f"Benötigt werden {competition.boards} aktive Spieler, vorhanden sind {active}."))
            open_team_matches = sum(match.result is None for match in competition.matches)
            if competition.status is CompetitionStatus.RUNNING and open_team_matches:
                issues.append(TournamentCheckIssue("Warnung", f"Offene Mannschaftskämpfe: {competition.name}", f"{open_team_matches} Mannschaftskampf/-kämpfe sind noch nicht vollständig."))

        timed_items = []
        for item in tournament.schedule_items:
            if not item.end_time:
                continue
            try:
                start = datetime.strptime(item.start_time, "%H:%M")
                end = datetime.strptime(item.end_time, "%H:%M")
            except ValueError:
                issues.append(TournamentCheckIssue("Warnung", f"Ungültige Zeitangabe: {item.title}", "Erwartet wird das Format HH:MM."))
                continue
            if end <= start:
                issues.append(TournamentCheckIssue("Fehler", f"Ungültiger Zeitraum: {item.title}", "Die Endzeit muss nach der Startzeit liegen."))
            timed_items.append((start, end, item.title))
        timed_items.sort(key=lambda row: row[0])
        for previous, current in zip(timed_items, timed_items[1:]):
            if current[0] < previous[1]:
                issues.append(TournamentCheckIssue("Warnung", "Überschneidung im Tagesplan", f"{previous[2]} überschneidet sich mit {current[2]}."))

        if tournament.competitions and not tournament.playing_areas:
            issues.append(TournamentCheckIssue("Hinweis", "Keine Spielorte", "Es wurden noch keine Spielorte angelegt."))
        return issues


    def export_document_package(
        self,
        output_dir: Path,
        *,
        create_pdf: bool = True,
        create_archive: bool = True,
    ) -> DocumentExportResult:
        """Create the Phase 3 document center package for the loaded tournament."""
        tournament = self.require_tournament()
        return export_document_center(
            tournament,
            output_dir,
            create_pdf=create_pdf,
            create_archive=create_archive,
        )

    def export_selected_document_package(
        self,
        output_dir: Path,
        selection: DocumentSelection,
        *,
        create_pdf: bool = False,
        create_word: bool = True,
        create_excel: bool = True,
        create_archive: bool = False,
        print_settings: PrintSettings = PrintSettings(),
    ) -> DocumentExportResult:
        """Export only the documents/groups/matches selected in Phase 3.3."""
        return export_selected_documents(
            self.require_tournament(), output_dir, selection,
            create_pdf=create_pdf, create_word=create_word, create_excel=create_excel,
            create_archive=create_archive, print_settings=print_settings,
        )

    def _document_profiles_path(self) -> Path:
        return self._database_path().with_name("document_profiles.json")

    def document_profiles(self) -> dict[str, DocumentProfile]:
        profiles = built_in_document_profiles()
        path = self._document_profiles_path()
        if not path.exists():
            return profiles
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for item in payload.get("profiles", []):
                selection = DocumentSelection(**item["selection"])
                settings = PrintSettings(**item["print_settings"])
                settings.validate()
                name = str(item["name"]).strip()
                if name and name not in profiles:
                    profiles[name] = DocumentProfile(name, selection, settings, False)
        except (OSError, ValueError, TypeError, KeyError, ValidationError):
            pass
        return profiles

    def save_document_profile(self, profile: DocumentProfile) -> None:
        name = profile.name.strip()
        if not name:
            raise ValidationError("Bitte einen Profilnamen eingeben.")
        if name in built_in_document_profiles():
            raise ValidationError("Ein Standardprofil kann nicht überschrieben werden.")
        profile.print_settings.validate()
        custom = {key: value for key, value in self.document_profiles().items() if not value.built_in}
        custom[name] = DocumentProfile(name, profile.selection, profile.print_settings, False)
        payload = {"profiles": [
            {"name": value.name, "selection": value.selection.__dict__, "print_settings": value.print_settings.__dict__}
            for value in sorted(custom.values(), key=lambda value: value.name.casefold())
        ]}
        path = self._document_profiles_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete_document_profile(self, name: str) -> None:
        if name in built_in_document_profiles():
            raise ValidationError("Standardprofile können nicht gelöscht werden.")
        custom = {key: value for key, value in self.document_profiles().items() if not value.built_in and key != name}
        payload = {"profiles": [
            {"name": value.name, "selection": value.selection.__dict__, "print_settings": value.print_settings.__dict__}
            for value in sorted(custom.values(), key=lambda value: value.name.casefold())
        ]}
        self._document_profiles_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _branding_profiles_path(self) -> Path:
        return self._database_path().with_name("branding_profiles.json")

    def branding_profiles(self) -> dict[str, BrandingProfile]:
        profiles = built_in_branding_profiles()
        path = self._branding_profiles_path()
        if not path.exists():
            return profiles
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for item in payload.get("profiles", []):
                profile = BrandingProfile(
                    name=str(item["name"]).strip(),
                    organizer_name=str(item.get("organizer_name", "")),
                    primary_color=str(item.get("primary_color", "#68472f")),
                    accent_color=str(item.get("accent_color", "#b99a80")),
                    custom_logo_path=str(item.get("custom_logo_path", "")),
                )
                profile.validate()
                if profile.name and profile.name not in profiles:
                    profiles[profile.name] = profile
        except (OSError, ValueError, TypeError, KeyError, ValidationError):
            pass
        return profiles

    def save_branding_profile(self, profile: BrandingProfile) -> None:
        name = profile.name.strip()
        if not name:
            raise ValidationError("Bitte einen Designprofilnamen eingeben.")
        if name in built_in_branding_profiles():
            raise ValidationError("Ein Standard-Designprofil kann nicht überschrieben werden.")
        profile.validate()
        custom = {key: value for key, value in self.branding_profiles().items() if not value.built_in}
        custom[name] = BrandingProfile(name, profile.organizer_name, profile.primary_color, profile.accent_color, profile.custom_logo_path)
        payload = {"profiles": [value.__dict__ for value in sorted(custom.values(), key=lambda value: value.name.casefold())]}
        path = self._branding_profiles_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete_branding_profile(self, name: str) -> None:
        if name in built_in_branding_profiles():
            raise ValidationError("Standard-Designprofile können nicht gelöscht werden.")
        custom = {key: value for key, value in self.branding_profiles().items() if not value.built_in and key != name}
        payload = {"profiles": [value.__dict__ for value in sorted(custom.values(), key=lambda value: value.name.casefold())]}
        path = self._branding_profiles_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def export_branding_profile(self, name: str, path: Path) -> Path:
        profile = self.branding_profiles().get(name)
        if profile is None:
            raise ValidationError("Das ausgewählte Designprofil wurde nicht gefunden.")
        profile.validate()
        profile_data = dict(profile.__dict__)
        logo_path = Path(profile.custom_logo_path) if profile.custom_logo_path else None
        if logo_path and logo_path.is_file():
            profile_data["logo_filename"] = logo_path.name
            profile_data["logo_data_base64"] = base64.b64encode(logo_path.read_bytes()).decode("ascii")
        payload = {"format": "mstts-branding-profile-v1", "profile": profile_data}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def import_branding_profile(self, path: Path) -> BrandingProfile:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("format") != "mstts-branding-profile-v1":
                raise ValidationError("Die Datei ist kein gültiges MSTTS-Designprofil.")
            item = payload["profile"]
            name = str(item["name"]).strip()
            if name in built_in_branding_profiles():
                name = f"{name} (importiert)"
            logo_path = str(item.get("custom_logo_path", ""))
            encoded_logo = str(item.get("logo_data_base64", ""))
            if encoded_logo:
                suffix = Path(str(item.get("logo_filename", "logo.png"))).suffix.lower()
                if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".svg"}:
                    suffix = ".png"
                logo_dir = self._database_path().with_name("branding_logos")
                logo_dir.mkdir(parents=True, exist_ok=True)
                target = logo_dir / f"{re.sub(r'[^A-Za-z0-9_-]+', '-', name).strip('-') or 'design'}{suffix}"
                try:
                    target.write_bytes(base64.b64decode(encoded_logo, validate=True))
                except (ValueError, binascii.Error) as error:
                    raise ValidationError("Das eingebettete Logo des Designprofils ist beschädigt.") from error
                logo_path = str(target)
            profile = BrandingProfile(
                name=name, organizer_name=str(item.get("organizer_name", "")),
                primary_color=str(item.get("primary_color", "#68472f")),
                accent_color=str(item.get("accent_color", "#b99a80")),
                custom_logo_path=logo_path,
            )
            profile.validate()
            self.save_branding_profile(profile)
            return profile
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise ValidationError("Das Designprofil konnte nicht importiert werden.") from error

    def export_validation_html(self, path: Path) -> None:
        tournament = self.require_tournament()
        issues = self.validate_tournament()
        counts = {severity: sum(issue.severity == severity for issue in issues) for severity in ("Fehler", "Warnung", "Hinweis")}
        rows = "".join(
            f"<tr><td>{html.escape(issue.severity)}</td><td>{html.escape(issue.title)}</td><td>{html.escape(issue.details)}</td></tr>"
            for issue in issues
        ) or '<tr><td colspan="3">Keine Probleme gefunden. Das Turnier ist bereit.</td></tr>'
        body = (
            f"<h1>{html.escape(tournament.name)}</h1><h2>Turnierprüfung</h2>"
            f"<p><strong>{counts['Fehler']}</strong> Fehler · <strong>{counts['Warnung']}</strong> Warnungen · <strong>{counts['Hinweis']}</strong> Hinweise</p>"
            "<table><thead><tr><th>Stufe</th><th>Prüfung</th><th>Details</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
        self._write_html(path, f"{tournament.name} – Turnierprüfung", body)

    @staticmethod
    def _write_html(path: Path, title: str, body: str) -> None:
        document = f"""<!doctype html>
<html lang=\"de\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 24mm 18mm; color: #111; }}
h1 {{ margin: 0 0 4mm; }}
h2 {{ margin-top: 10mm; border-bottom: 1px solid #888; padding-bottom: 2mm; }}
table {{ border-collapse: collapse; width: 100%; margin: 4mm 0 8mm; }}
th, td {{ border: 1px solid #888; padding: 2.2mm; text-align: left; }}
th {{ background: #eee; }}
.center {{ text-align: center; }}
.muted {{ color: #555; }}
.score {{ width: 18mm; height: 8mm; }}
@media print {{ body {{ margin: 12mm; }} .no-print {{ display: none; }} }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
        if path.suffix.casefold() == ".pdf":
            html_to_pdf(document, path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(document, encoding="utf-8")

    def export_players_html(self, path: Path) -> None:
        tournament = self.require_tournament()
        rows = "".join(
            f"<tr><td>{person.start_number or index}</td><td>{html.escape(person.last_name)}</td><td>{html.escape(person.first_name)}</td><td>{html.escape(person.club)}</td><td>{html.escape(person.category)}</td><td>{html.escape(person.license_number)}</td></tr>"
            for index, person in enumerate(
                sorted(tournament.people, key=lambda p: (p.last_name.casefold(), p.first_name.casefold())),
                start=1,
            )
        )
        metadata = " · ".join(value for value in [tournament.organizer, tournament.location, tournament.start_date] if value)
        body = (
            f"<h1>{html.escape(tournament.name)}</h1>"
            + (f"<p class=\"muted\">{html.escape(metadata)}</p>" if metadata else "")
            + f"<p class=\"muted\">Teilnehmerliste · {len(tournament.people)} Spieler</p>"
            "<table><thead><tr><th>Startnr.</th><th>Nachname</th><th>Vorname</th><th>Verein</th><th>Kategorie</th><th>Lizenz</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
        self._write_html(path, f"{tournament.name} – Teilnehmerliste", body)

    def export_competition_html(self, competition_id: UUID, path: Path) -> None:
        tournament = self.require_tournament()
        competition = self.competition(competition_id)
        names = {person.id: person.full_name for person in tournament.people}
        match_rows = "".join(
            "<tr>"
            f"<td>{match.round_number}</td>"
            f"<td>{html.escape(names.get(match.home_id, ''))}</td>"
            f"<td>{html.escape(names.get(match.away_id, ''))}</td>"
            f"<td class=\"center\">{'' if match.result is None else match.result.home_score}</td>"
            f"<td class=\"center\">{'' if match.result is None else match.result.away_score}</td>"
            "</tr>"
            for match in competition.matches
        )
        metadata = " · ".join(value for value in [tournament.organizer, tournament.location, tournament.start_date] if value)
        body = (
            f"<h1>{html.escape(tournament.name)}</h1>"
            + (f"<p class=\"muted\">{html.escape(metadata)}</p>" if metadata else "")
            + f"<h2>{html.escape(competition.name)}</h2>"
            f"<p class=\"muted\">System: {html.escape(competition.format.label)}</p>"
            "<h2>Paarungen und Ergebnisse</h2>"
            "<table><thead><tr><th>Runde</th><th>Heim</th><th>Auswärts</th><th>Heim</th><th>Auswärts</th></tr></thead>"
            f"<tbody>{match_rows}</tbody></table>"
        )
        if competition.format is CompetitionFormat.ROUND_ROBIN:
            standing_rows = "".join(
                "<tr>"
                f"<td>{position}</td><td>{html.escape(names.get(row.person_id, ''))}</td>"
                f"<td>{row.played}</td><td>{row.wins}</td><td>{row.draws}</td><td>{row.losses}</td>"
                f"<td>{row.scored}</td><td>{row.conceded}</td><td>{row.difference}</td><td>{row.points}</td>"
                "</tr>"
                for position, row in enumerate(competition.standings(tournament.people), start=1)
            )
            body += (
                "<h2>Tabelle</h2><table><thead><tr><th>Platz</th><th>Spieler</th><th>Sp</th><th>S</th><th>U</th><th>N</th>"
                "<th>Tore</th><th>Gegentore</th><th>Diff</th><th>Pkt</th></tr></thead>"
                f"<tbody>{standing_rows}</tbody></table>"
            )
        elif competition.winner_id is not None:
            body += f"<h2>Sieger</h2><p><strong>{html.escape(names.get(competition.winner_id, ''))}</strong></p>"
        self._write_html(path, f"{tournament.name} – {competition.name}", body)


    def competition_podium(self, competition_id: UUID) -> list[tuple[int, Person]]:
        tournament = self.require_tournament()
        competition = self.competition(competition_id)
        people = {person.id: person for person in tournament.people}
        if competition.status is not CompetitionStatus.FINISHED:
            raise ValidationError("Der Wettbewerb muss vor der Siegerehrung beendet sein.")
        ids: list[UUID] = []
        if competition.format is CompetitionFormat.SINGLE_ELIMINATION:
            if not competition.matches or competition.winner_id is None:
                return []
            final_round = max(match.round_number for match in competition.matches)
            final_match = next(match for match in competition.matches if match.round_number == final_round)
            ids.append(competition.winner_id)
            ids.append(final_match.away_id if final_match.home_id == competition.winner_id else final_match.home_id)
        else:
            ids = [row.person_id for row in competition.standings(tournament.people)[:3]]
        return [(position, people[person_id]) for position, person_id in enumerate(ids, start=1) if person_id in people]

    def medal_table(self) -> list[dict[str, int | str]]:
        tournament = self.require_tournament()
        medals: dict[str, dict[str, int | str]] = {}
        for competition in tournament.competitions:
            if competition.status is not CompetitionStatus.FINISHED:
                continue
            for position, person in self.competition_podium(competition.id):
                club = person.club or "Ohne Verein"
                row = medals.setdefault(club, {"club": club, "gold": 0, "silver": 0, "bronze": 0})
                key = {1: "gold", 2: "silver", 3: "bronze"}.get(position)
                if key:
                    row[key] = int(row[key]) + 1
        rows = list(medals.values())
        rows.sort(key=lambda row: (-int(row["gold"]), -int(row["silver"]), -int(row["bronze"]), str(row["club"]).casefold()))
        return rows

    def export_medal_table_html(self, path: Path) -> None:
        tournament = self.require_tournament()
        rows = "".join(
            f"<tr><td>{position}</td><td>{html.escape(str(row['club']))}</td><td>{row['gold']}</td><td>{row['silver']}</td><td>{row['bronze']}</td><td>{int(row['gold']) + int(row['silver']) + int(row['bronze'])}</td></tr>"
            for position, row in enumerate(self.medal_table(), start=1)
        ) or '<tr><td colspan="6" class="muted">Noch keine abgeschlossenen Wettbewerbe mit Platzierungen.</td></tr>'
        body = (
            f"<h1>{html.escape(tournament.name)}</h1><h2>Medaillenspiegel</h2>"
            "<table><thead><tr><th>Platz</th><th>Verein</th><th>Gold</th><th>Silber</th><th>Bronze</th><th>Gesamt</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
        self._write_html(path, f"{tournament.name} – Medaillenspiegel", body)

    def export_certificates_html(self, competition_id: UUID, path: Path) -> None:
        tournament = self.require_tournament()
        competition = self.competition(competition_id)
        podium = self.competition_podium(competition_id)
        if not podium:
            raise ValidationError("Für diesen Wettbewerb sind keine Platzierungen verfügbar.")
        certificates = []
        for position, person in podium:
            certificates.append(
                '<section class="certificate">'
                '<div class="certificate-title">Urkunde</div>'
                f'<p class="muted">{html.escape(tournament.name)}</p>'
                f'<h1>{html.escape(person.full_name)}</h1>'
                f'<p>erreichte im Wettbewerb <strong>{html.escape(competition.name)}</strong></p>'
                f'<div class="placing">{position}. Platz</div>'
                + (f'<p>{html.escape(person.club)}</p>' if person.club else '')
                + f'<p class="signature">{html.escape(tournament.location)} {html.escape(tournament.end_date or tournament.start_date)}</p>'
                '</section>'
            )
        body = ''.join(certificates)
        self._write_html(path, f"{tournament.name} – Urkunden {competition.name}", body)

    def export_schedule_html(self, path: Path) -> None:
        tournament = self.require_tournament()
        rows = "".join(
            f"<tr><td>{html.escape(item.start_time)}</td><td>{html.escape(item.end_time)}</td><td>{html.escape(item.title)}</td><td>{html.escape(item.location)}</td><td>{html.escape(item.status)}</td></tr>"
            for item in sorted(tournament.schedule_items, key=lambda value: value.start_time)
        )
        body = f"<h1>{html.escape(tournament.name)}</h1><h2>Tagesplan</h2><table><thead><tr><th>Beginn</th><th>Ende</th><th>Programmpunkt</th><th>Ort</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>"
        self._write_html(path, f"{tournament.name} – Tagesplan", body)

    def export_live_html(self, path: Path, refresh_seconds: int = 15) -> None:
        """Create a self-refreshing spectator view for the current tournament state."""
        if refresh_seconds < 5 or refresh_seconds > 3600:
            raise ValidationError("Das Aktualisierungsintervall muss zwischen 5 und 3600 Sekunden liegen.")
        tournament = self.require_tournament()
        names = {person.id: person.full_name for person in tournament.people}
        sections: list[str] = []
        for competition in tournament.competitions:
            current_round = max((match.round_number for match in competition.matches), default=0)
            current_matches = [match for match in competition.matches if match.round_number == current_round]
            match_rows = "".join(
                "<tr>"
                f"<td>{match.round_number}</td>"
                f"<td>{html.escape(names.get(match.home_id, 'Unbekannt'))}</td>"
                f"<td>{html.escape(names.get(match.away_id, 'Unbekannt'))}</td>"
                f"<td class=\"center\">{'offen' if match.result is None else f'{match.result.home_score}:{match.result.away_score}'}</td>"
                "</tr>"
                for match in current_matches
            ) or '<tr><td colspan="4" class="muted">Noch keine Paarungen vorhanden.</td></tr>'
            status_label = {
                CompetitionStatus.REGISTRATION: "Anmeldung",
                CompetitionStatus.RUNNING: "Läuft",
                CompetitionStatus.FINISHED: "Beendet",
            }[competition.status]
            section = (
                f"<section><h2>{html.escape(competition.name)}</h2>"
                f"<p class=\"muted\">{html.escape(competition.format.label)} · {status_label}"
                + (f" · Runde {current_round}" if current_round else "")
                + "</p><table><thead><tr><th>Runde</th><th>Heim</th><th>Auswärts</th><th>Ergebnis</th></tr></thead>"
                f"<tbody>{match_rows}</tbody></table>"
            )
            if competition.matches:
                standing_rows = "".join(
                    f"<tr><td>{position}</td><td>{html.escape(names.get(row.person_id, 'Unbekannt'))}</td><td>{row.played}</td><td>{row.points}</td><td>{row.difference}</td></tr>"
                    for position, row in enumerate(competition.standings(tournament.people), start=1)
                )
                section += (
                    "<h3>Live-Tabelle</h3><table><thead><tr><th>Platz</th><th>Spieler</th><th>Sp</th><th>Pkt</th><th>Diff</th></tr></thead>"
                    f"<tbody>{standing_rows}</tbody></table>"
                )
            section += "</section>"
            sections.append(section)

        schedule_rows = "".join(
            f"<tr><td>{html.escape(item.start_time)}</td><td>{html.escape(item.title)}</td><td>{html.escape(item.location)}</td><td>{html.escape(item.status)}</td></tr>"
            for item in sorted(tournament.schedule_items, key=lambda value: value.start_time)
        )
        schedule_section = ""
        if schedule_rows:
            schedule_section = (
                "<section><h2>Tagesplan</h2><table><thead><tr><th>Zeit</th><th>Programmpunkt</th><th>Ort</th><th>Status</th></tr></thead>"
                f"<tbody>{schedule_rows}</tbody></table></section>"
            )
        body = (
            f"<header><h1>{html.escape(tournament.name)}</h1>"
            f"<p class=\"muted\">Live-Ansicht · automatische Aktualisierung alle {refresh_seconds} Sekunden · Stand {datetime.now():%d.%m.%Y %H:%M:%S}</p></header>"
            + schedule_section
            + ("".join(sections) if sections else '<p class="muted">Noch keine Wettbewerbe vorhanden.</p>')
        )
        document = (
            '<!doctype html><html lang="de"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta http-equiv="refresh" content="{refresh_seconds}">'
            f'<title>{html.escape(tournament.name)} – Live</title>'
            '<style>body{font-family:Arial,sans-serif;margin:2rem;background:#f4f5f7;color:#151515}'
            'header,section{background:white;border-radius:12px;padding:1.2rem 1.5rem;margin:0 0 1rem;box-shadow:0 2px 8px rgba(0,0,0,.08)}'
            'h1{font-size:2.2rem;margin:.1rem 0}h2{font-size:1.6rem;margin:.2rem 0}h3{margin-top:1.5rem}'
            'table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #ddd;padding:.65rem;text-align:left}'
            'th{background:#f0f1f3}.center{text-align:center}.muted{color:#60656f}'
            '@media(min-width:1000px){body{max-width:1200px;margin:2rem auto}}</style></head><body>'
            + body + '</body></html>'
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document, encoding="utf-8")

    def export_tournament_archive(self, path: Path) -> None:
        """Export a portable ZIP archive with data, reports and a manifest."""
        tournament = self.require_tournament()
        path.parent.mkdir(parents=True, exist_ok=True)

        def safe_name(value: str) -> str:
            cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
            return cleaned or "wettbewerb"

        with tempfile.TemporaryDirectory(prefix="fts-archive-") as temp_name:
            root = Path(temp_name)
            data_dir = root / "daten"
            report_dir = root / "berichte"
            competition_dir = report_dir / "wettbewerbe"
            data_dir.mkdir(parents=True)
            competition_dir.mkdir(parents=True)

            self.export_tournament_json(data_dir / "turnier.fts.json")
            self.export_players_csv(data_dir / "teilnehmer.csv")
            self.export_players_html(report_dir / "teilnehmerliste.html")
            self.export_validation_html(report_dir / "turnierpruefung.html")
            self.export_live_html(report_dir / "live-ansicht.html")
            self.export_medal_table_html(report_dir / "medaillenspiegel.html")
            if tournament.schedule_items:
                self.export_schedule_html(report_dir / "tagesplan.html")

            used_names: set[str] = set()
            competition_files: list[str] = []
            for index, competition in enumerate(tournament.competitions, start=1):
                base = safe_name(competition.name)
                candidate = base
                suffix = 2
                while candidate.casefold() in used_names:
                    candidate = f"{base}-{suffix}"
                    suffix += 1
                used_names.add(candidate.casefold())
                csv_path = competition_dir / f"{index:02d}-{candidate}.csv"
                html_path = competition_dir / f"{index:02d}-{candidate}.html"
                self.export_competition_csv(competition.id, csv_path)
                self.export_competition_html(competition.id, html_path)
                competition_files.extend([str(csv_path.relative_to(root)), str(html_path.relative_to(root))])
                if competition.status is CompetitionStatus.FINISHED:
                    try:
                        certificate_path = competition_dir / f"{index:02d}-{candidate}-urkunden.html"
                        self.export_certificates_html(competition.id, certificate_path)
                        competition_files.append(str(certificate_path.relative_to(root)))
                    except ValidationError:
                        pass

            issues = self.validate_tournament()
            manifest = {
                "schema": "fts-tournament-archive",
                "version": 2,
                "application_version": "1.10.0",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "tournament": {
                    "id": str(tournament.id),
                    "name": tournament.name,
                    "participants": len(tournament.people),
                    "competitions": len(tournament.competitions),
                    "team_competitions": len(tournament.team_competitions),
                    "playing_areas": len(tournament.playing_areas),
                    "schedule_items": len(tournament.schedule_items),
                },
                "validation": {
                    "errors": sum(issue.severity == "Fehler" for issue in issues),
                    "warnings": sum(issue.severity == "Warnung" for issue in issues),
                    "notes": sum(issue.severity == "Hinweis" for issue in issues),
                },
                "competition_files": competition_files,
            }
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            (root / "README.txt").write_text(
                "FTS-Turnierarchiv\n\n"
                "daten/turnier.fts.json kann wieder in FTS geöffnet werden.\n"
                "berichte/ enthält druckbare HTML-Berichte.\n"
                "manifest.json beschreibt Inhalt und Erstellungszeitpunkt.\n",
                encoding="utf-8",
            )

            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in sorted(root.rglob("*")):
                    if item.is_file():
                        archive.write(item, item.relative_to(root).as_posix())

    def export_tournament_json(self, path: Path) -> None:
        tournament = self.require_tournament()
        payload = {
            "schema": "fts-tournament",
            "version": 2,
            "tournament": {
                "id": str(tournament.id),
                "name": tournament.name,
                "organizer": tournament.organizer,
                "location": tournament.location,
                "start_date": tournament.start_date,
                "end_date": tournament.end_date,
                "table_count": tournament.table_count,
                "match_duration_minutes": tournament.match_duration_minutes,
                "schedule_start_time": tournament.schedule_start_time,
                "people": [
                    {
                        "id": str(person.id),
                        "first_name": person.first_name,
                        "last_name": person.last_name,
                        "club": person.club,
                        "category": person.category,
                        "start_number": person.start_number,
                        "license_number": person.license_number,
                    }
                    for person in tournament.people
                ],
                "teams": [{
                    "id": str(team.id), "name": team.name, "club": team.club, "abbreviation": team.abbreviation,
                    "captain": team.captain, "notes": team.notes,
                    "members": [{"person_id": str(member.person_id), "board_number": member.board_number, "substitute": member.substitute, "active": member.active} for member in team.members],
                } for team in tournament.teams],
                "playing_areas": [{"id": str(area.id), "name": area.name, "hall": area.hall, "room": area.room, "table_number": area.table_number, "notes": area.notes, "active": area.active} for area in tournament.playing_areas],
                "schedule_items": [{"id": str(item.id), "title": item.title, "start_time": item.start_time, "end_time": item.end_time, "location": item.location, "status": item.status, "notes": item.notes} for item in tournament.schedule_items],
                "team_competitions": [{
                    "id": str(comp.id), "name": comp.name, "boards": comp.boards, "status": comp.status.value,
                    "registered_team_ids": [str(value) for value in comp.registered_team_ids],
                    "matches": [{"id": str(match.id), "round_number": match.round_number, "home_team_id": str(match.home_team_id), "away_team_id": str(match.away_team_id),
                        "boards": [{"id": str(board.id), "board_number": board.board_number, "home_person_id": str(board.home_person_id), "away_person_id": str(board.away_person_id), "result": None if board.result is None else {"home_score": board.result.home_score, "away_score": board.result.away_score}} for board in match.boards]} for match in comp.matches],
                } for comp in tournament.team_competitions],
                "phases": [
                    {
                        "id": str(phase.id),
                        "name": phase.name,
                        "phase_type": phase.phase_type.value,
                        "position": phase.position,
                        "participant_ids": [str(value) for value in phase.participant_ids],
                        "waiting_ids": [str(value) for value in phase.waiting_ids],
                        "matches": [
                            {
                                "id": str(match.id),
                                "round_number": match.round_number,
                                "home_id": str(match.home_id),
                                "away_id": str(match.away_id),
                                "table_number": match.table_number,
                                "scheduled_time": match.scheduled_time,
                                "result": None if match.result is None else {
                                    "home_score": match.result.home_score,
                                    "away_score": match.result.away_score,
                                    "set_scores": [list(score) for score in match.result.set_scores],
                                },
                            }
                            for match in phase.matches
                        ],
                        "groups": [
                            {
                                "id": str(group.id),
                                "name": group.name,
                                "qualification_count": group.qualification_count,
                                "qualification_playoff": group.qualification_playoff,
                                "profile": group.profile,
                                "playoff_match": None if group.playoff_match is None else {
                                    "id": str(group.playoff_match.id), "round_number": group.playoff_match.round_number,
                                    "home_id": str(group.playoff_match.home_id), "away_id": str(group.playoff_match.away_id),
                                    "table_number": group.playoff_match.table_number, "scheduled_time": group.playoff_match.scheduled_time,
                                    "result": None if group.playoff_match.result is None else {
                                        "home_score": group.playoff_match.result.home_score, "away_score": group.playoff_match.result.away_score,
                                        "set_scores": [list(score) for score in group.playoff_match.result.set_scores],
                                    },
                                },
                                "participant_ids": [str(value) for value in group.participant_ids],
                                "matches": [
                                    {
                                        "id": str(match.id),
                                        "round_number": match.round_number,
                                        "home_id": str(match.home_id),
                                        "away_id": str(match.away_id),
                                        "table_number": match.table_number,
                                        "scheduled_time": match.scheduled_time,
                                        "result": None if match.result is None else {
                                            "home_score": match.result.home_score,
                                            "away_score": match.result.away_score,
                                            "set_scores": [list(score) for score in match.result.set_scores],
                                        },
                                    }
                                    for match in group.matches
                                ],
                            }
                            for group in phase.groups
                        ],
                    }
                    for phase in tournament.phases
                ],
                "competitions": [
                    {
                        "id": str(competition.id),
                        "name": competition.name,
                        "format": competition.format.value,
                        "status": competition.status.value,
                        "registered_ids": [str(value) for value in competition.registered_ids],
                        "waiting_ids": [str(value) for value in competition.waiting_ids],
                        "swiss_rounds": competition.swiss_rounds,
                        "bye_ids": [str(value) for value in competition.bye_ids],
                        "matches": [
                            {
                                "id": str(match.id),
                                "round_number": match.round_number,
                                "home_id": str(match.home_id),
                                "away_id": str(match.away_id),
                                "result": None
                                if match.result is None
                                else {
                                    "home_score": match.result.home_score,
                                    "away_score": match.result.away_score,
                                    "set_scores": [list(score) for score in match.result.set_scores],
                                },
                            }
                            for match in competition.matches
                        ],
                    }
                    for competition in tournament.competitions
                ],
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def import_tournament_json(self, path: Path) -> Tournament:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValidationError(f"Die Turnierdatei konnte nicht gelesen werden: {error}") from error
        version = payload.get("version")
        if payload.get("schema") != "fts-tournament" or version not in (1, 2):
            raise ValidationError("Die Datei ist keine unterstützte FTS-Turnierdatei.")
        try:
            data = payload["tournament"]
            people = [
                Person(
                    id=UUID(item["id"]),
                    first_name=item["first_name"],
                    last_name=item["last_name"],
                    club=item.get("club", ""),
                    category=item.get("category", "Offen"),
                    start_number=item.get("start_number"),
                    license_number=item.get("license_number", ""),
                )
                for item in data["people"]
            ]
            person_ids = {person.id for person in people}
            competitions = []
            for item in data["competitions"]:
                registered_ids = [UUID(value) for value in item["registered_ids"]]
                waiting_ids = [UUID(value) for value in item.get("waiting_ids", [])]
                bye_ids = [UUID(value) for value in item.get("bye_ids", [])]
                if not set(registered_ids + waiting_ids + bye_ids).issubset(person_ids):
                    raise ValidationError("Die Turnierdatei verweist auf unbekannte Spieler.")
                matches = []
                for match_data in item["matches"]:
                    home_id = UUID(match_data["home_id"])
                    away_id = UUID(match_data["away_id"])
                    if home_id not in person_ids or away_id not in person_ids:
                        raise ValidationError("Die Turnierdatei enthält ein Spiel mit unbekanntem Spieler.")
                    result_data = match_data.get("result")
                    result = None if result_data is None else MatchResult(
                        int(result_data["home_score"]), int(result_data["away_score"]),
                        tuple(tuple(int(value) for value in score) for score in result_data.get("set_scores", [])),
                    )
                    matches.append(Match(
                        id=UUID(match_data["id"]),
                        round_number=int(match_data["round_number"]),
                        home_id=home_id,
                        away_id=away_id,
                        result=result,
                    ))
                competitions.append(Competition(
                    id=UUID(item["id"]),
                    name=item["name"],
                    format=CompetitionFormat(item["format"]),
                    status=CompetitionStatus(item["status"]),
                    registered_ids=registered_ids,
                    waiting_ids=waiting_ids,
                    swiss_rounds=int(item.get("swiss_rounds", 5)),
                    bye_ids=bye_ids,
                    matches=matches,
                ))
            teams = [Team(id=UUID(item["id"]), name=item["name"], club=item.get("club", ""), abbreviation=item.get("abbreviation", ""), captain=item.get("captain", ""), notes=item.get("notes", ""), members=[TeamMember(UUID(member["person_id"]), int(member["board_number"]), bool(member.get("substitute", False)), bool(member.get("active", True))) for member in item.get("members", [])]) for item in data.get("teams", [])]
            team_ids = {team.id for team in teams}
            team_competitions = []
            for item in data.get("team_competitions", []):
                registered_team_ids = [UUID(value) for value in item.get("registered_team_ids", [])]
                if not set(registered_team_ids).issubset(team_ids):
                    raise ValidationError("Die Turnierdatei verweist auf unbekannte Mannschaften.")
                team_matches = []
                for match in item.get("matches", []):
                    boards = []
                    for board in match.get("boards", []):
                        result_data = board.get("result")
                        boards.append(BoardGame(id=UUID(board["id"]), board_number=int(board["board_number"]), home_person_id=UUID(board["home_person_id"]), away_person_id=UUID(board["away_person_id"]), result=None if result_data is None else MatchResult(
                            int(result_data["home_score"]), int(result_data["away_score"]),
                            tuple(tuple(int(value) for value in score) for score in result_data.get("set_scores", [])),
                        )))
                    team_matches.append(TeamMatch(id=UUID(match["id"]), round_number=int(match["round_number"]), home_team_id=UUID(match["home_team_id"]), away_team_id=UUID(match["away_team_id"]), boards=boards))
                team_competitions.append(TeamCompetition(id=UUID(item["id"]), name=item["name"], boards=int(item.get("boards", 4)), status=CompetitionStatus(item["status"]), registered_team_ids=registered_team_ids, matches=team_matches))
            playing_areas = [PlayingArea(id=UUID(item["id"]), name=item["name"], hall=item.get("hall", ""), room=item.get("room", ""), table_number=item.get("table_number", ""), notes=item.get("notes", ""), active=bool(item.get("active", True))) for item in data.get("playing_areas", [])]
            schedule_items = [ScheduleItem(id=UUID(item["id"]), title=item["title"], start_time=item["start_time"], end_time=item.get("end_time", ""), location=item.get("location", ""), status=item.get("status", "geplant"), notes=item.get("notes", "")) for item in data.get("schedule_items", [])]
            phases = []
            for item in data.get("phases", []):
                phase_type = PhaseType(item.get("phase_type", PhaseType.GROUP_STAGE.value))
                phase_participant_ids = [UUID(value) for value in item.get("participant_ids", [])]
                phase_waiting_ids = [UUID(value) for value in item.get("waiting_ids", [])]
                if not set(phase_participant_ids + phase_waiting_ids).issubset(person_ids):
                    raise ValidationError("Die Turnierdatei verweist in einer Phase auf unbekannte Spieler.")

                def load_match(match_data: dict) -> Match:
                    home_id = UUID(match_data["home_id"]); away_id = UUID(match_data["away_id"])
                    if home_id not in person_ids or away_id not in person_ids:
                        raise ValidationError("Die Turnierdatei enthält ein Phasenspiel mit unbekanntem Spieler.")
                    result_data = match_data.get("result")
                    return Match(
                        id=UUID(match_data["id"]),
                        round_number=int(match_data["round_number"]),
                        home_id=home_id,
                        away_id=away_id,
                        result=None if result_data is None else MatchResult(
                            int(result_data["home_score"]), int(result_data["away_score"]),
                            tuple(tuple(int(value) for value in score) for score in result_data.get("set_scores", [])),
                        ),
                        table_number=int(match_data.get("table_number", 0)),
                        scheduled_time=match_data.get("scheduled_time", ""),
                    )

                groups = []
                for group_data in item.get("groups", []):
                    group_participant_ids = [UUID(value) for value in group_data.get("participant_ids", [])]
                    if not set(group_participant_ids).issubset(person_ids):
                        raise ValidationError("Die Turnierdatei verweist in einer Gruppe auf unbekannte Spieler.")
                    groups.append(Group(
                        id=UUID(group_data["id"]),
                        name=group_data["name"],
                        qualification_count=int(group_data.get("qualification_count", 2)),
                        qualification_playoff=bool(group_data.get("qualification_playoff", False)),
                        profile=str(group_data.get("profile", "Offen") or "Offen"),
                        playoff_match=None if group_data.get("playoff_match") is None else load_match(group_data["playoff_match"]),
                        participant_ids=group_participant_ids,
                        matches=[load_match(match_data) for match_data in group_data.get("matches", [])],
                    ))
                phases.append(TournamentPhase(
                    id=UUID(item["id"]),
                    name=item["name"],
                    phase_type=phase_type,
                    position=int(item.get("position", len(phases) + 1)),
                    groups=groups,
                    participant_ids=phase_participant_ids,
                    matches=[load_match(match_data) for match_data in item.get("matches", [])],
                    waiting_ids=phase_waiting_ids,
                ))

            tournament = Tournament(
                id=UUID(data["id"]),
                name=data["name"],
                organizer=data.get("organizer", ""),
                location=data.get("location", ""),
                start_date=data.get("start_date", ""),
                end_date=data.get("end_date", ""),
                table_count=int(data.get("table_count", 3)),
                match_duration_minutes=int(data.get("match_duration_minutes", 15)),
                schedule_start_time=data.get("schedule_start_time", "17:10"),
                people=people,
                phases=phases,
                competitions=competitions,
                teams=teams,
                team_competitions=team_competitions,
                playing_areas=playing_areas,
                schedule_items=schedule_items,
            )
        except ValidationError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise ValidationError("Die Turnierdatei ist unvollständig oder beschädigt.") from error
        self.repository.save(tournament)
        self.tournament = tournament
        return tournament

    def tournament_summary(self) -> dict[str, int]:
        tournament = self.require_tournament()
        return {
            "players": len(tournament.people),
            "competitions": len(tournament.competitions),
            "running": sum(c.status is CompetitionStatus.RUNNING for c in tournament.competitions),
            "finished": sum(c.status is CompetitionStatus.FINISHED for c in tournament.competitions),
            "matches": sum(len(c.matches) for c in tournament.competitions) + sum(len(c.matches) for c in tournament.team_competitions),
            "playing_areas": len(tournament.playing_areas),
            "schedule_items": len(tournament.schedule_items),
        }

    def export_players_csv(self, path: Path) -> None:
        tournament = self.require_tournament()
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["Vorname", "Nachname", "Verein", "Kategorie", "Startnummer", "Lizenznummer"])
            for person in sorted(tournament.people, key=lambda p: p.full_name.casefold()):
                writer.writerow([person.first_name, person.last_name, person.club, person.category, person.start_number or "", person.license_number])

    def export_competition_csv(self, competition_id: UUID, path: Path) -> None:
        tournament = self.require_tournament()
        competition = self.competition(competition_id)
        names = {person.id: person.full_name for person in tournament.people}
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow([competition.name]); writer.writerow([])
            writer.writerow(["Runde", "Heim", "Auswärts", "Heim-Ergebnis", "Auswärts-Ergebnis"])
            for match in competition.matches:
                writer.writerow([match.round_number, names.get(match.home_id, ""), names.get(match.away_id, ""), "" if match.result is None else match.result.home_score, "" if match.result is None else match.result.away_score])
            writer.writerow([])
            writer.writerow(["Platz", "Spieler", "Sp", "S", "U", "N", "Tore", "Gegentore", "Diff", "Pkt"])
            for position, standing in enumerate(competition.standings(tournament.people), start=1):
                writer.writerow([position, names.get(standing.person_id, ""), standing.played, standing.wins, standing.draws, standing.losses, standing.scored, standing.conceded, standing.difference, standing.points])
