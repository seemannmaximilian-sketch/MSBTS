from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable
from uuid import UUID, uuid4


class ValidationError(ValueError):
    pass


class CompetitionStatus(str, Enum):
    REGISTRATION = "registration"
    RUNNING = "running"
    FINISHED = "finished"


class LiveMatchStatus(str, Enum):
    PLANNED = "planned"
    PREPARING = "preparing"
    RUNNING = "running"
    RESULT_PENDING = "result_pending"
    FINISHED = "finished"

    @property
    def label(self) -> str:
        return {
            LiveMatchStatus.PLANNED: "GEPLANT",
            LiveMatchStatus.PREPARING: "VORBEREITUNG",
            LiveMatchStatus.RUNNING: "LÄUFT",
            LiveMatchStatus.RESULT_PENDING: "ERGEBNIS FEHLT",
            LiveMatchStatus.FINISHED: "BEENDET",
        }[self]


class PhaseType(str, Enum):
    GROUP_STAGE = "group_stage"
    FINAL_ROUND = "final_round"

    @property
    def label(self) -> str:
        return {
            PhaseType.GROUP_STAGE: "Gruppenphase",
            PhaseType.FINAL_ROUND: "Finalrunde",
        }[self]


class CompetitionFormat(str, Enum):
    ROUND_ROBIN = "round_robin"
    SINGLE_ELIMINATION = "single_elimination"
    SWISS = "swiss"

    @property
    def label(self) -> str:
        return {
            CompetitionFormat.ROUND_ROBIN: "Round Robin",
            CompetitionFormat.SINGLE_ELIMINATION: "K.-o.-System",
            CompetitionFormat.SWISS: "Schweizer System",
        }[self]


def _required(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError(f"{field_name} darf nicht leer sein.")
    return cleaned


@dataclass
class Person:
    first_name: str
    last_name: str
    club: str = ""
    category: str = "Offen"
    start_number: int | None = None
    license_number: str = ""
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self.rename(self.first_name, self.last_name, self.club, self.category, self.start_number, self.license_number)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def rename(
        self, first_name: str, last_name: str, club: str | None = None,
        category: str | None = None, start_number: int | None = None,
        license_number: str | None = None,
    ) -> None:
        self.first_name = _required(first_name, "Vorname")
        self.last_name = _required(last_name, "Nachname")
        if club is not None:
            self.club = club.strip()
        if category is not None:
            self.category = category.strip() or "Offen"
        if start_number is not None and start_number <= 0:
            raise ValidationError("Die Startnummer muss größer als null sein.")
        self.start_number = start_number
        if license_number is not None:
            self.license_number = license_number.strip()


@dataclass(frozen=True)
class MatchResult:
    home_score: int
    away_score: int
    set_scores: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if self.home_score < 0 or self.away_score < 0:
            raise ValidationError("Ergebnisse dürfen nicht negativ sein.")
        if self.set_scores:
            home_sets = 0
            away_sets = 0
            if len(self.set_scores) > 3:
                raise ValidationError("Best of 3 kann höchstens drei Sätze enthalten.")
            for home_points, away_points in self.set_scores:
                if home_points < 0 or away_points < 0:
                    raise ValidationError("Satzpunkte dürfen nicht negativ sein.")
                if home_points == away_points:
                    raise ValidationError("Ein Satz darf nicht unentschieden enden.")
                winner = max(home_points, away_points)
                loser = min(home_points, away_points)
                if winner > 30:
                    raise ValidationError("Ein Badminton-Satz endet spätestens bei 30 Punkten.")
                if winner < 21:
                    raise ValidationError("Ein Badminton-Satz endet ab 21 Punkten.")
                if winner < 30 and winner - loser < 2:
                    raise ValidationError("Ab 20:20 sind zwei Punkte Vorsprung nötig; bei 29:29 entscheidet der 30. Punkt.")
                if winner == 30 and loser > 29:
                    raise ValidationError("Bei 29:29 entscheidet der 30. Punkt.")
                if home_points > away_points:
                    home_sets += 1
                else:
                    away_sets += 1
            if (home_sets, away_sets) != (self.home_score, self.away_score):
                raise ValidationError("Satzstände und Spielergebnis stimmen nicht überein.")
            if max(home_sets, away_sets) != 2:
                raise ValidationError("Bei Best of 3 muss ein Spieler zwei Sätze gewinnen.")

    @classmethod
    def from_set_scores(cls, set_scores: list[tuple[int, int]] | tuple[tuple[int, int], ...]) -> "MatchResult":
        scores = tuple((int(home), int(away)) for home, away in set_scores)
        home_sets = sum(home > away for home, away in scores)
        away_sets = sum(away > home for home, away in scores)
        return cls(home_sets, away_sets, scores)


@dataclass
class Match:
    round_number: int
    home_id: UUID
    away_id: UUID
    id: UUID = field(default_factory=uuid4)
    result: MatchResult | None = None
    table_number: int | None = None
    scheduled_time: str = ""
    live_status: LiveMatchStatus = LiveMatchStatus.PLANNED
    started_at: str = ""
    finished_at: str = ""
    match_kind: str = "standard"

    def set_result(self, home_score: int, away_score: int) -> None:
        self.result = MatchResult(home_score, away_score)
        self.live_status = LiveMatchStatus.FINISHED

    def set_result_from_sets(self, set_scores: list[tuple[int, int]] | tuple[tuple[int, int], ...]) -> None:
        self.result = MatchResult.from_set_scores(set_scores)
        self.live_status = LiveMatchStatus.FINISHED

    def loser_id(self) -> UUID:
        if self.result is None:
            raise ValidationError("Für dieses Spiel liegt noch kein Ergebnis vor.")
        if self.result.home_score == self.result.away_score:
            raise ValidationError("Im K.-o.-System ist kein Unentschieden möglich.")
        return self.away_id if self.result.home_score > self.result.away_score else self.home_id

    def winner_id(self) -> UUID:
        if self.result is None:
            raise ValidationError("Für dieses Spiel liegt noch kein Ergebnis vor.")
        if self.result.home_score == self.result.away_score:
            raise ValidationError("Im K.-o.-System ist kein Unentschieden möglich.")
        return self.home_id if self.result.home_score > self.result.away_score else self.away_id


@dataclass(frozen=True)
class Standing:
    person_id: UUID
    played: int
    wins: int
    draws: int
    losses: int
    scored: int
    conceded: int
    points: int
    balls_scored: int = 0
    balls_conceded: int = 0
    direct_points: int = 0

    @property
    def difference(self) -> int:
        """Set difference (kept for backwards compatibility)."""
        return self.scored - self.conceded

    @property
    def set_ratio(self) -> float:
        return self.scored / self.conceded if self.conceded else float(self.scored or 0)

    @property
    def ball_difference(self) -> int:
        return self.balls_scored - self.balls_conceded

    @property
    def ball_ratio(self) -> float:
        return self.balls_scored / self.balls_conceded if self.balls_conceded else float(self.balls_scored or 0)


@dataclass
class Competition:
    name: str
    id: UUID = field(default_factory=uuid4)
    format: CompetitionFormat = CompetitionFormat.ROUND_ROBIN
    status: CompetitionStatus = CompetitionStatus.REGISTRATION
    registered_ids: list[UUID] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    waiting_ids: list[UUID] = field(default_factory=list)
    swiss_rounds: int = 5
    bye_ids: list[UUID] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = _required(self.name, "Wettbewerbsname")

    def register(self, person_id: UUID) -> None:
        if self.status is not CompetitionStatus.REGISTRATION:
            raise ValidationError("Die Anmeldung ist bereits geschlossen.")
        if person_id not in self.registered_ids:
            self.registered_ids.append(person_id)

    def unregister(self, person_id: UUID) -> None:
        if self.status is not CompetitionStatus.REGISTRATION:
            raise ValidationError("Die Anmeldung ist bereits geschlossen.")
        if person_id in self.registered_ids:
            self.registered_ids.remove(person_id)

    def start(self) -> None:
        if self.status is not CompetitionStatus.REGISTRATION:
            raise ValidationError("Der Wettbewerb wurde bereits gestartet.")
        if len(self.registered_ids) < 2:
            raise ValidationError("Mindestens zwei Teilnehmer werden benötigt.")
        if self.format is CompetitionFormat.ROUND_ROBIN:
            self.matches = generate_round_robin_matches(self.registered_ids)
        elif self.format is CompetitionFormat.SINGLE_ELIMINATION:
            self.matches = []
            self._create_knockout_round(list(self.registered_ids), 1)
        else:
            if self.swiss_rounds < 1:
                raise ValidationError("Für das Schweizer System wird mindestens eine Runde benötigt.")
            self.matches = []
            self.bye_ids = []
            self._create_swiss_round(1)
        self.status = CompetitionStatus.RUNNING

    def set_match_result(self, match: Match, home_score: int, away_score: int) -> None:
        if self.status is not CompetitionStatus.RUNNING:
            raise ValidationError("Ergebnisse können nur in einem laufenden Wettbewerb erfasst werden.")
        if self.format is CompetitionFormat.SINGLE_ELIMINATION:
            current_round = max(item.round_number for item in self.matches)
            if match.round_number != current_round:
                raise ValidationError("Abgeschlossene K.-o.-Runden können nicht mehr geändert werden.")
            if home_score == away_score:
                raise ValidationError("Im K.-o.-System ist kein Unentschieden möglich.")
        elif self.format is CompetitionFormat.SWISS:
            current_round = max(item.round_number for item in self.matches)
            if match.round_number != current_round:
                raise ValidationError("Abgeschlossene Runden können nicht mehr geändert werden.")
        match.set_result(home_score, away_score)
        if self.format is CompetitionFormat.SINGLE_ELIMINATION:
            self._advance_knockout_if_round_complete()
        elif self.format is CompetitionFormat.SWISS:
            self._advance_swiss_if_round_complete()

    def _create_knockout_round(self, participant_ids: list[UUID], round_number: int) -> None:
        self.waiting_ids = []
        index = 0
        while index + 1 < len(participant_ids):
            self.matches.append(Match(round_number, participant_ids[index], participant_ids[index + 1]))
            index += 2
        if index < len(participant_ids):
            self.waiting_ids.append(participant_ids[index])

    def _advance_knockout_if_round_complete(self) -> None:
        current_round = max(match.round_number for match in self.matches)
        round_matches = [match for match in self.matches if match.round_number == current_round]
        if any(match.result is None for match in round_matches):
            return
        participants = [match.winner_id() for match in round_matches] + list(self.waiting_ids)
        if len(participants) == 1:
            self.waiting_ids = participants
            self.status = CompetitionStatus.FINISHED
            return
        self._create_knockout_round(participants, current_round + 1)


    def _create_swiss_round(self, round_number: int) -> None:
        standings = self.standings([])
        points = {row.person_id: row.points for row in standings}
        order = sorted(self.registered_ids, key=lambda pid: (-points.get(pid, 0), str(pid)))
        if len(order) % 2:
            candidates = [pid for pid in reversed(order) if pid not in self.bye_ids]
            bye = candidates[0] if candidates else order[-1]
            order.remove(bye)
            self.bye_ids.append(bye)
        previous = {frozenset((m.home_id, m.away_id)) for m in self.matches}

        def pair(players: list[UUID]) -> list[tuple[UUID, UUID]] | None:
            if not players:
                return []
            first = players[0]
            for index in range(1, len(players)):
                second = players[index]
                if frozenset((first, second)) in previous:
                    continue
                rest = players[1:index] + players[index + 1:]
                paired = pair(rest)
                if paired is not None:
                    return [(first, second), *paired]
            return None

        pairs = pair(order)
        if pairs is None:
            pairs = list(zip(order[::2], order[1::2]))
        self.matches.extend(Match(round_number, home, away) for home, away in pairs)

    def _advance_swiss_if_round_complete(self) -> None:
        current_round = max(match.round_number for match in self.matches)
        round_matches = [match for match in self.matches if match.round_number == current_round]
        if any(match.result is None for match in round_matches):
            return
        if current_round >= self.swiss_rounds:
            self.status = CompetitionStatus.FINISHED
            return
        self._create_swiss_round(current_round + 1)

    @property
    def winner_id(self) -> UUID | None:
        if self.format is not CompetitionFormat.SINGLE_ELIMINATION or self.status is not CompetitionStatus.FINISHED:
            return None
        return self.waiting_ids[0] if self.waiting_ids else None

    def finish(self) -> None:
        if self.format is CompetitionFormat.SINGLE_ELIMINATION:
            if self.status is not CompetitionStatus.FINISHED:
                raise ValidationError("Der K.-o.-Wettbewerb endet automatisch nach dem Finale.")
            return
        if self.status is not CompetitionStatus.RUNNING:
            raise ValidationError("Nur ein laufender Wettbewerb kann beendet werden.")
        if any(match.result is None for match in self.matches):
            raise ValidationError("Vor dem Abschluss müssen alle Ergebnisse erfasst sein.")
        self.status = CompetitionStatus.FINISHED

    def standings(self, people: Iterable[Person]) -> list[Standing]:
        if self.format is CompetitionFormat.SINGLE_ELIMINATION:
            return []
        names = {person.id: person.full_name.casefold() for person in people}
        rows: dict[UUID, dict[str, int]] = {
            pid: {"played": 0, "wins": 0, "draws": 0, "losses": 0, "scored": 0, "conceded": 0, "points": 0}
            for pid in self.registered_ids
        }
        for match in self.matches:
            if match.result is None:
                continue
            home, away = rows[match.home_id], rows[match.away_id]
            hs, as_ = match.result.home_score, match.result.away_score
            home["played"] += 1; away["played"] += 1
            home["scored"] += hs; home["conceded"] += as_
            away["scored"] += as_; away["conceded"] += hs
            if hs > as_:
                home["wins"] += 1; away["losses"] += 1; home["points"] += 2
            elif hs < as_:
                away["wins"] += 1; home["losses"] += 1; away["points"] += 2
            else:
                home["draws"] += 1; away["draws"] += 1; home["points"] += 1; away["points"] += 1
        if self.format is CompetitionFormat.SWISS:
            for person_id in self.bye_ids:
                if person_id in rows:
                    rows[person_id]["points"] += 2
        standings = [Standing(person_id=pid, **values) for pid, values in rows.items()]
        standings.sort(key=lambda row: (-row.points, -row.difference, -row.scored, names.get(row.person_id, "")))
        return standings



@dataclass
class TeamMember:
    person_id: UUID
    board_number: int
    substitute: bool = False
    active: bool = True

    def __post_init__(self) -> None:
        if self.board_number < 1:
            raise ValidationError("Die Brettnummer muss größer als null sein.")


@dataclass
class Team:
    name: str
    club: str = ""
    abbreviation: str = ""
    captain: str = ""
    notes: str = ""
    members: list[TeamMember] = field(default_factory=list)
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self.name = _required(self.name, "Mannschaftsname")
        self.club = self.club.strip()
        self.abbreviation = self.abbreviation.strip()
        self.captain = self.captain.strip()
        self.notes = self.notes.strip()

    def add_member(self, person_id: UUID, board_number: int, substitute: bool = False, active: bool = True) -> None:
        if any(member.person_id == person_id for member in self.members):
            raise ValidationError("Der Spieler gehört bereits zu dieser Mannschaft.")
        if any(member.board_number == board_number for member in self.members):
            raise ValidationError("Diese Brettnummer ist bereits vergeben.")
        self.members.append(TeamMember(person_id, board_number, substitute, active))
        self.members.sort(key=lambda member: member.board_number)

    def remove_member(self, person_id: UUID) -> None:
        self.members = [member for member in self.members if member.person_id != person_id]

    def lineup(self, boards: int) -> list[UUID]:
        active = [member for member in self.members if member.active]
        active.sort(key=lambda member: (member.substitute, member.board_number))
        if len(active) < boards:
            raise ValidationError(f"Die Mannschaft {self.name} benötigt mindestens {boards} aktive Spieler.")
        return [member.person_id for member in active[:boards]]


@dataclass
class BoardGame:
    board_number: int
    home_person_id: UUID
    away_person_id: UUID
    result: MatchResult | None = None
    id: UUID = field(default_factory=uuid4)

    def set_result(self, home_score: int, away_score: int) -> None:
        self.result = MatchResult(home_score, away_score)


@dataclass
class TeamMatch:
    round_number: int
    home_team_id: UUID
    away_team_id: UUID
    boards: list[BoardGame] = field(default_factory=list)
    id: UUID = field(default_factory=uuid4)

    @property
    def result(self) -> tuple[int, int] | None:
        if not self.boards or any(board.result is None for board in self.boards):
            return None
        return (sum(board.result.home_score for board in self.boards if board.result),
                sum(board.result.away_score for board in self.boards if board.result))


@dataclass(frozen=True)
class TeamStanding:
    team_id: UUID
    played: int
    wins: int
    draws: int
    losses: int
    board_points_for: int
    board_points_against: int
    team_points: int

    @property
    def board_difference(self) -> int:
        return self.board_points_for - self.board_points_against


@dataclass
class TeamCompetition:
    name: str
    boards: int = 4
    status: CompetitionStatus = CompetitionStatus.REGISTRATION
    registered_team_ids: list[UUID] = field(default_factory=list)
    matches: list[TeamMatch] = field(default_factory=list)
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self.name = _required(self.name, "Mannschaftswettbewerbsname")
        if self.boards < 1:
            raise ValidationError("Ein Mannschaftskampf benötigt mindestens ein Brett.")

    def register(self, team_id: UUID) -> None:
        if self.status is not CompetitionStatus.REGISTRATION:
            raise ValidationError("Die Anmeldung ist bereits geschlossen.")
        if team_id not in self.registered_team_ids:
            self.registered_team_ids.append(team_id)

    def start(self, teams: Iterable[Team]) -> None:
        if self.status is not CompetitionStatus.REGISTRATION:
            raise ValidationError("Der Wettbewerb wurde bereits gestartet.")
        if len(self.registered_team_ids) < 2:
            raise ValidationError("Mindestens zwei Mannschaften werden benötigt.")
        team_map = {team.id: team for team in teams}
        ids: list[UUID | None] = list(self.registered_team_ids)
        if len(ids) % 2:
            ids.append(None)
        self.matches = []
        size = len(ids)
        for round_index in range(size - 1):
            for index in range(size // 2):
                home_id, away_id = ids[index], ids[size - 1 - index]
                if home_id is None or away_id is None:
                    continue
                if round_index % 2 and index == 0:
                    home_id, away_id = away_id, home_id
                home = team_map[home_id].lineup(self.boards)
                away = team_map[away_id].lineup(self.boards)
                board_games = [BoardGame(i + 1, home[i], away[i]) for i in range(self.boards)]
                self.matches.append(TeamMatch(round_index + 1, home_id, away_id, board_games))
            ids = [ids[0], ids[-1], *ids[1:-1]]
        self.status = CompetitionStatus.RUNNING

    def set_board_result(self, team_match_id: UUID, board_game_id: UUID, home_score: int, away_score: int) -> None:
        if self.status is not CompetitionStatus.RUNNING:
            raise ValidationError("Ergebnisse können nur in einem laufenden Wettbewerb erfasst werden.")
        match = next((item for item in self.matches if item.id == team_match_id), None)
        if match is None:
            raise ValidationError("Mannschaftskampf wurde nicht gefunden.")
        board = next((item for item in match.boards if item.id == board_game_id), None)
        if board is None:
            raise ValidationError("Brett wurde nicht gefunden.")
        board.set_result(home_score, away_score)

    def finish(self) -> None:
        if self.status is not CompetitionStatus.RUNNING:
            raise ValidationError("Nur ein laufender Wettbewerb kann beendet werden.")
        if any(match.result is None for match in self.matches):
            raise ValidationError("Vor dem Abschluss müssen alle Brett-Ergebnisse erfasst sein.")
        self.status = CompetitionStatus.FINISHED

    def standings(self, teams: Iterable[Team]) -> list[TeamStanding]:
        names = {team.id: team.name.casefold() for team in teams}
        rows = {team_id: {"played": 0, "wins": 0, "draws": 0, "losses": 0, "board_points_for": 0, "board_points_against": 0, "team_points": 0} for team_id in self.registered_team_ids}
        for match in self.matches:
            result = match.result
            if result is None:
                continue
            home_score, away_score = result
            home, away = rows[match.home_team_id], rows[match.away_team_id]
            home["played"] += 1; away["played"] += 1
            home["board_points_for"] += home_score; home["board_points_against"] += away_score
            away["board_points_for"] += away_score; away["board_points_against"] += home_score
            if home_score > away_score:
                home["wins"] += 1; away["losses"] += 1; home["team_points"] += 2
            elif away_score > home_score:
                away["wins"] += 1; home["losses"] += 1; away["team_points"] += 2
            else:
                home["draws"] += 1; away["draws"] += 1; home["team_points"] += 1; away["team_points"] += 1
        result = [TeamStanding(team_id=team_id, **values) for team_id, values in rows.items()]
        result.sort(key=lambda row: (-row.team_points, -row.board_difference, -row.board_points_for, names.get(row.team_id, "")))
        return result



@dataclass
class PlayingArea:
    name: str
    hall: str = ""
    room: str = ""
    table_number: str = ""
    notes: str = ""
    active: bool = True
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self.name = _required(self.name, "Spielort")
        self.hall = self.hall.strip()
        self.room = self.room.strip()
        self.table_number = self.table_number.strip()
        self.notes = self.notes.strip()

    @property
    def display_name(self) -> str:
        details = [value for value in (self.hall, self.room, self.table_number) if value]
        return self.name if not details else f"{self.name} ({' · '.join(details)})"


@dataclass
class ScheduleItem:
    title: str
    start_time: str
    end_time: str = ""
    location: str = ""
    status: str = "geplant"
    notes: str = ""
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self.title = _required(self.title, "Programmpunkt")
        self.start_time = _required(self.start_time, "Startzeit")
        self.end_time = self.end_time.strip()
        self.location = self.location.strip()
        self.status = self.status.strip() or "geplant"
        self.notes = self.notes.strip()


@dataclass
class Group:
    name: str
    participant_ids: list[UUID] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    qualification_count: int = 2
    qualification_playoff: bool = False
    profile: str = "Offen"
    target_capacity: int = 0
    playoff_match: Match | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self.name = _required(self.name, "Gruppenname")
        self.profile = (self.profile or "Offen").strip() or "Offen"
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValidationError("Ein Spieler darf innerhalb einer Gruppe nur einmal vorkommen.")
        if self.qualification_count < 0:
            raise ValidationError("Die Anzahl der direkten Qualifikationsplätze darf nicht negativ sein.")
        if self.target_capacity < 0:
            raise ValidationError("Die Zielgröße einer Gruppe darf nicht negativ sein.")

    def set_qualification_count(self, count: int) -> None:
        if count < 0:
            raise ValidationError("Die Anzahl der direkten Qualifikationsplätze darf nicht negativ sein.")
        self.qualification_count = count

    def set_qualification_playoff(self, enabled: bool) -> None:
        self.qualification_playoff = bool(enabled)
        if not self.qualification_playoff:
            self.playoff_match = None

    @property
    def qualification_playoff_applicable(self) -> bool:
        """Whether the configured 3rd-vs-4th playoff can actually be played.

        A three-player group follows the dynamic default automatically: places 1
        and 2 qualify directly and place 3 is eliminated. The stored playoff
        preference is kept so that a later roster expansion to four or more
        players re-enables the playoff without losing the tournament setup.
        """
        return bool(self.qualification_playoff and len(self.participant_ids) >= 4)

    def ensure_playoff_match(self) -> Match | None:
        if not self.qualification_playoff_applicable:
            self.playoff_match = None
            return None
        if not self.is_finished:
            raise ValidationError("Das Qualifikationsspiel kann erst nach Abschluss der Gruppe erzeugt werden.")
        standings = self.standings([])
        home_id, away_id = standings[2].person_id, standings[3].person_id
        if self.playoff_match is None or {self.playoff_match.home_id, self.playoff_match.away_id} != {home_id, away_id}:
            self.playoff_match = Match(round_number=max((m.round_number for m in self.matches), default=0) + 1, home_id=home_id, away_id=away_id)
        return self.playoff_match

    @property
    def qualification_ready(self) -> bool:
        if not self.is_finished:
            return False
        return not self.qualification_playoff_applicable or (self.playoff_match is not None and self.playoff_match.result is not None)

    def record_playoff_result(self, home_score: int, away_score: int) -> None:
        match = self.ensure_playoff_match()
        if match is None:
            raise ValidationError("Für diese Gruppe ist kein Qualifikationsspiel aktiviert.")
        if home_score == away_score:
            raise ValidationError("Im Qualifikationsspiel ist kein Unentschieden möglich.")
        match.set_result(home_score, away_score)

    def qualified_ids(self, people: Iterable[Person]) -> list[UUID]:
        standings = self.standings(people)
        direct = [row.person_id for row in standings[:min(self.qualification_count, len(standings))]]
        if self.qualification_playoff_applicable:
            if self.playoff_match is None or self.playoff_match.result is None:
                raise ValidationError(f"Das Qualifikationsspiel in {self.name} ist noch nicht abgeschlossen.")
            winner = self.playoff_match.winner_id()
            if winner not in direct:
                direct.append(winner)
        return direct

    def add_participant(self, person_id: UUID) -> None:
        if self.matches:
            raise ValidationError("Nach der Spielplanerzeugung können keine Spieler mehr hinzugefügt werden.")
        if person_id in self.participant_ids:
            raise ValidationError("Der Spieler ist bereits in dieser Gruppe.")
        self.participant_ids.append(person_id)

    def remove_participant(self, person_id: UUID) -> None:
        if self.matches:
            raise ValidationError("Nach der Spielplanerzeugung können keine Spieler mehr entfernt werden.")
        if person_id not in self.participant_ids:
            raise ValidationError("Der Spieler ist nicht in dieser Gruppe.")
        self.participant_ids.remove(person_id)

    def generate_schedule(self, replace: bool = False) -> list[Match]:
        if len(self.participant_ids) < 2:
            raise ValidationError("Für einen Gruppenspielplan werden mindestens zwei Spieler benötigt.")
        if self.matches and not replace:
            raise ValidationError("Für diese Gruppe wurde bereits ein Spielplan erzeugt.")
        self.matches = generate_round_robin_matches(self.participant_ids)
        return self.matches

    def match(self, match_id: UUID) -> Match:
        for match in self.matches:
            if match.id == match_id:
                return match
        raise ValidationError("Gruppenspiel wurde nicht gefunden.")

    def record_result(self, match_id: UUID, home_score: int | None, away_score: int | None) -> None:
        match = self.match(match_id)
        if home_score is None and away_score is None:
            match.result = None
            return
        if home_score is None or away_score is None:
            raise ValidationError("Bitte beide Ergebnisfelder ausfüllen oder beide leer lassen.")
        match.set_result(home_score, away_score)

    @property
    def played_matches(self) -> int:
        return sum(match.result is not None for match in self.matches)

    @property
    def open_matches(self) -> list[Match]:
        return [match for match in self.matches if match.result is None]

    @property
    def is_finished(self) -> bool:
        return bool(self.matches) and not self.open_matches

    def standings(self, people: Iterable[Person]) -> list[Standing]:
        names = {person.id: person.full_name.casefold() for person in people}
        rows: dict[UUID, dict[str, int]] = {
            pid: {"played": 0, "wins": 0, "draws": 0, "losses": 0, "scored": 0, "conceded": 0, "points": 0,
                  "balls_scored": 0, "balls_conceded": 0, "direct_points": 0}
            for pid in self.participant_ids
        }
        completed: list[Match] = []
        for match in self.matches:
            if match.result is None:
                continue
            completed.append(match)
            home, away = rows[match.home_id], rows[match.away_id]
            hs, as_ = match.result.home_score, match.result.away_score
            home["played"] += 1; away["played"] += 1
            home["scored"] += hs; home["conceded"] += as_
            away["scored"] += as_; away["conceded"] += hs
            for home_points, away_points in match.result.set_scores:
                home["balls_scored"] += home_points; home["balls_conceded"] += away_points
                away["balls_scored"] += away_points; away["balls_conceded"] += home_points
            if hs > as_:
                home["wins"] += 1; away["losses"] += 1; home["points"] += 2
            elif hs < as_:
                away["wins"] += 1; home["losses"] += 1; away["points"] += 2
            else:
                home["draws"] += 1; away["draws"] += 1
                home["points"] += 1; away["points"] += 1

        # Direct comparison is applied only inside groups tied on match points.
        tied_by_points: dict[int, set[UUID]] = {}
        for pid, values in rows.items():
            tied_by_points.setdefault(values["points"], set()).add(pid)
        for tied_ids in tied_by_points.values():
            if len(tied_ids) < 2:
                continue
            for match in completed:
                if match.home_id not in tied_ids or match.away_id not in tied_ids:
                    continue
                hs, as_ = match.result.home_score, match.result.away_score
                if hs > as_:
                    rows[match.home_id]["direct_points"] += 2
                elif hs < as_:
                    rows[match.away_id]["direct_points"] += 2
                else:
                    rows[match.home_id]["direct_points"] += 1
                    rows[match.away_id]["direct_points"] += 1

        standings = [Standing(person_id=pid, **values) for pid, values in rows.items()]
        standings.sort(key=lambda row: (
            -row.points,
            -row.direct_points,
            -row.difference,
            -row.set_ratio,
            -row.ball_difference,
            -row.ball_ratio,
            names.get(row.person_id, ""),
        ))
        return standings


@dataclass
class TournamentPhase:
    name: str
    phase_type: PhaseType = PhaseType.GROUP_STAGE
    position: int = 1
    groups: list[Group] = field(default_factory=list)
    participant_ids: list[UUID] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    waiting_ids: list[UUID] = field(default_factory=list)
    next_phase_id: UUID | None = None
    auto_advance: bool = False
    distribution_mode: str = "snake"
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self.name = _required(self.name, "Phasenname")
        if self.position < 1:
            raise ValidationError("Die Phasenposition muss mindestens 1 sein.")
        if self.phase_type is PhaseType.FINAL_ROUND and self.groups:
            raise ValidationError("Eine Finalrunde enthält keine Gruppen.")
        if self.phase_type is PhaseType.GROUP_STAGE and (self.participant_ids or self.matches or self.waiting_ids):
            raise ValidationError("K.-o.-Daten sind nur in einer Finalrunde zulässig.")
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValidationError("Ein Spieler darf in einer Finalrunde nur einmal vorkommen.")
        if self.distribution_mode not in {"snake", "balanced"}:
            raise ValidationError("Unbekannter Verteilungsmodus für den Phasenübergang.")

    def add_group(self, name: str) -> Group:
        cleaned = _required(name, "Gruppenname")
        if any(group.name.casefold() == cleaned.casefold() for group in self.groups):
            raise ValidationError("Dieser Gruppenname ist in der Phase bereits vergeben.")
        if self.phase_type is not PhaseType.GROUP_STAGE:
            raise ValidationError("Gruppen können nur in einer Gruppenphase angelegt werden.")
        group = Group(cleaned)
        self.groups.append(group)
        return group

    def group(self, group_id: UUID) -> Group:
        for group in self.groups:
            if group.id == group_id:
                return group
        raise ValidationError("Gruppe wurde nicht gefunden.")

    def assign_participant(self, group_id: UUID, person_id: UUID) -> None:
        for group in self.groups:
            if group.id != group_id and person_id in group.participant_ids:
                raise ValidationError("Ein Spieler darf pro Phase nur einer Gruppe angehören.")
        self.group(group_id).add_participant(person_id)

    def add_final_participant(self, person_id: UUID) -> None:
        if self.phase_type is not PhaseType.FINAL_ROUND:
            raise ValidationError("Teilnehmer können hier nur einer Finalrunde hinzugefügt werden.")
        if self.matches:
            raise ValidationError("Nach der Auslosung können keine Teilnehmer mehr hinzugefügt werden.")
        if person_id not in self.participant_ids:
            self.participant_ids.append(person_id)

    def remove_final_participant(self, person_id: UUID) -> None:
        if self.phase_type is not PhaseType.FINAL_ROUND:
            raise ValidationError("Teilnehmer können hier nur aus einer Finalrunde entfernt werden.")
        if self.matches:
            raise ValidationError("Nach der Auslosung können keine Teilnehmer mehr entfernt werden.")
        if person_id not in self.participant_ids:
            raise ValidationError("Der Spieler gehört nicht zu dieser Finalrunde.")
        self.participant_ids.remove(person_id)

    def generate_final_schedule(self) -> list[Match]:
        if self.phase_type is not PhaseType.FINAL_ROUND:
            raise ValidationError("Ein K.-o.-Spielplan kann nur für eine Finalrunde erzeugt werden.")
        if self.matches:
            raise ValidationError("Für diese Finalrunde wurde bereits ein Spielplan erzeugt.")
        if len(self.participant_ids) < 2:
            raise ValidationError("Für eine Finalrunde werden mindestens zwei Spieler benötigt.")
        self._create_initial_final_round(list(self.participant_ids))
        return self.matches

    @staticmethod
    def knockout_round_count(participant_count: int) -> int:
        """Return how many KO stages are required for ``participant_count`` players."""
        if participant_count < 2:
            return 0
        stages = 0
        size = 1
        while size < participant_count:
            size *= 2
            stages += 1
        return stages

    def knockout_round_label(self, round_number: int) -> str:
        """Human readable name for a dynamically sized KO round."""
        remaining = self.knockout_round_count(len(self.participant_ids)) - round_number
        labels = {0: "Finale", 1: "Halbfinale", 2: "Viertelfinale", 3: "Achtelfinale", 4: "Sechzehntelfinale"}
        return labels.get(remaining, f"K.-o.-Runde {round_number}")

    def _create_initial_final_round(self, participant_ids: list[UUID]) -> None:
        """Create a balanced first KO round with the correct number of byes.

        For non-powers of two, only the number of matches required to reduce the
        field to the next lower power of two is played. The other participants
        receive a bye and are stored in ``waiting_ids``.
        """
        count = len(participant_ids)
        if count < 2:
            return
        # Exact powers of two start with a full round. Otherwise play only as
        # many matches as necessary to reach the next lower power of two.
        lower_power = 1 << (count.bit_length() - 1)
        if lower_power == count:
            match_count = count // 2
            bye_count = 0
        else:
            match_count = count - lower_power
            bye_count = count - (2 * match_count)
        self.waiting_ids = list(participant_ids[:bye_count])
        playing = participant_ids[bye_count:]
        for index in range(match_count):
            self.matches.append(Match(1, playing[index * 2], playing[index * 2 + 1]))

    def _create_final_round(self, participant_ids: list[UUID], round_number: int) -> None:
        self.waiting_ids = []
        index = 0
        while index + 1 < len(participant_ids):
            self.matches.append(Match(round_number, participant_ids[index], participant_ids[index + 1]))
            index += 2
        if index < len(participant_ids):
            self.waiting_ids.append(participant_ids[index])

    def final_match(self, match_id: UUID) -> Match:
        for match in self.matches:
            if match.id == match_id:
                return match
        raise ValidationError("Finalrundenspiel wurde nicht gefunden.")

    def record_final_result(self, match_id: UUID, home_score: int, away_score: int) -> None:
        if home_score == away_score:
            raise ValidationError("In der Finalrunde ist kein Unentschieden möglich.")
        self._record_final_match_result(match_id, MatchResult(home_score, away_score))

    def record_final_set_scores(self, match_id: UUID, set_scores: list[tuple[int, int]]) -> None:
        self._record_final_match_result(match_id, MatchResult.from_set_scores(set_scores))

    def _record_final_match_result(self, match_id: UUID, result: MatchResult) -> None:
        if self.phase_type is not PhaseType.FINAL_ROUND:
            raise ValidationError("Ergebnisse können hier nur für eine Finalrunde erfasst werden.")
        match = self.final_match(match_id)
        current_round = max(item.round_number for item in self.matches)
        if match.round_number != current_round:
            raise ValidationError("Abgeschlossene K.-o.-Runden können nicht mehr geändert werden.")
        match.result = result
        round_matches = [item for item in self.matches if item.round_number == current_round]
        if any(item.result is None for item in round_matches):
            return
        # Finale und Spiel um Platz 3 bilden gemeinsam die letzte Runde.
        # Sobald beide Ergebnisse vorliegen, ist nur der Sieger des Finales
        # der Turniersieger; der Gewinner des Platzierungsspiels wird Dritter.
        if any(item.match_kind == "third_place" for item in round_matches):
            final_match = next(item for item in round_matches if item.match_kind == "final")
            self.waiting_ids = [final_match.winner_id()]
            return

        advancing = [item.winner_id() for item in round_matches] + list(self.waiting_ids)
        if len(advancing) == 1:
            self.waiting_ids = advancing
            return

        # Sobald zwei Halbfinalspiele beendet sind, entstehen gleichzeitig
        # das Finale und das Spiel um Platz 3.
        if len(round_matches) == 2 and not self.waiting_ids:
            losers = [item.loser_id() for item in round_matches]
            next_round = current_round + 1
            self.waiting_ids = []
            self.matches.append(Match(next_round, advancing[0], advancing[1], match_kind="final"))
            self.matches.append(Match(next_round, losers[0], losers[1], match_kind="third_place"))
            return

        self._create_final_round(advancing, current_round + 1)

    @property
    def final_is_finished(self) -> bool:
        return self.phase_type is PhaseType.FINAL_ROUND and bool(self.matches) and len(self.waiting_ids) == 1 and all(match.result is not None for match in self.matches)

    @property
    def final_winner_id(self) -> UUID | None:
        return self.waiting_ids[0] if self.final_is_finished else None

    @property
    def third_place_winner_id(self) -> UUID | None:
        match = next((item for item in self.matches if item.match_kind == "third_place"), None)
        return match.winner_id() if match is not None and match.result is not None else None


@dataclass
class Tournament:
    name: str
    organizer: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    table_count: int = 3
    match_duration_minutes: int = 15
    schedule_start_time: str = "17:10"
    scorer_count: int = 6
    scorer_names: list[str] = field(default_factory=list)
    best_of: int = 3
    qualification_enabled: bool = True
    intermediate_enabled: bool = True
    people: list[Person] = field(default_factory=list)
    competitions: list[Competition] = field(default_factory=list)
    teams: list[Team] = field(default_factory=list)
    team_competitions: list[TeamCompetition] = field(default_factory=list)
    playing_areas: list[PlayingArea] = field(default_factory=list)
    schedule_items: list[ScheduleItem] = field(default_factory=list)
    phases: list[TournamentPhase] = field(default_factory=list)
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self.rename(self.name)
        self.set_schedule_start_time(self.schedule_start_time)

    def set_schedule_start_time(self, value: str) -> None:
        from datetime import datetime
        cleaned = str(value or "").strip()
        try:
            datetime.strptime(cleaned, "%H:%M")
        except ValueError as error:
            raise ValidationError("Die Startzeit muss im Format HH:MM angegeben werden.") from error
        self.schedule_start_time = cleaned

    def rename(self, name: str) -> None:
        self.name = _required(name, "Turniername")

    def update_details(
        self, organizer: str = "", location: str = "", start_date: str = "", end_date: str = "",
        table_count: int = 3, match_duration_minutes: int = 15, scorer_count: int = 6, best_of: int = 3, scorer_names: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        if not 1 <= table_count <= 16:
            raise ValidationError("Die Anzahl der Felder muss zwischen 1 und 16 liegen.")
        if not 5 <= match_duration_minutes <= 180:
            raise ValidationError("Die Spieldauer muss zwischen 5 und 180 Minuten liegen.")
        if not 0 <= scorer_count <= 99:
            raise ValidationError("Die Anzahl der Schiedsrichter darf nicht negativ sein.")
        if best_of not in (3, 5, 7):
            raise ValidationError("Best of muss 3, 5 oder 7 sein.")
        self.organizer = organizer.strip()
        self.location = location.strip()
        self.start_date = start_date.strip()
        self.end_date = end_date.strip()
        self.table_count = table_count
        self.match_duration_minutes = match_duration_minutes
        self.scorer_count = scorer_count
        if scorer_names is not None:
            from fts.scorekeepers import normalize_scorer_names
            self.scorer_names = normalize_scorer_names(scorer_names)
            self.scorer_count = max(self.scorer_count, len(self.scorer_names))
        self.best_of = best_of

    def add_person(
        self, first_name: str, last_name: str, club: str = "", category: str = "Offen",
        start_number: int | None = None, license_number: str = "",
    ) -> Person:
        if start_number is None:
            used = {person.start_number for person in self.people if person.start_number is not None}
            start_number = next(number for number in range(1, len(self.people) + 2) if number not in used)
        if any(person.start_number == start_number for person in self.people):
            raise ValidationError("Diese Startnummer ist bereits vergeben.")
        person = Person(first_name, last_name, club, category, start_number, license_number)
        self.people.append(person)
        return person

    def remove_person(self, person_id: UUID) -> None:
        if any(person_id in c.registered_ids for c in self.competitions):
            raise ValidationError("Ein angemeldeter Spieler kann nicht gelöscht werden.")
        self.people = [person for person in self.people if person.id != person_id]

    def person(self, person_id: UUID) -> Person:
        for person in self.people:
            if person.id == person_id:
                return person
        raise ValidationError("Spieler wurde nicht gefunden.")

    def add_team(self, name: str, club: str = "", abbreviation: str = "", captain: str = "", notes: str = "") -> Team:
        team = Team(name, club, abbreviation, captain, notes)
        self.teams.append(team)
        return team

    def team(self, team_id: UUID) -> Team:
        for team in self.teams:
            if team.id == team_id:
                return team
        raise ValidationError("Mannschaft wurde nicht gefunden.")

    def add_team_competition(self, name: str, boards: int = 4) -> TeamCompetition:
        competition = TeamCompetition(name=name, boards=boards)
        self.team_competitions.append(competition)
        return competition

    def add_playing_area(self, name: str, hall: str = "", room: str = "", table_number: str = "", notes: str = "", active: bool = True) -> PlayingArea:
        area = PlayingArea(name, hall, room, table_number, notes, active)
        self.playing_areas.append(area)
        return area

    def remove_playing_area(self, area_id: UUID) -> None:
        before = len(self.playing_areas)
        self.playing_areas = [area for area in self.playing_areas if area.id != area_id]
        if len(self.playing_areas) == before:
            raise ValidationError("Spielort wurde nicht gefunden.")

    def add_schedule_item(self, title: str, start_time: str, end_time: str = "", location: str = "", status: str = "geplant", notes: str = "") -> ScheduleItem:
        item = ScheduleItem(title, start_time, end_time, location, status, notes)
        self.schedule_items.append(item)
        self.schedule_items.sort(key=lambda value: value.start_time)
        return item

    def remove_schedule_item(self, item_id: UUID) -> None:
        before = len(self.schedule_items)
        self.schedule_items = [item for item in self.schedule_items if item.id != item_id]
        if len(self.schedule_items) == before:
            raise ValidationError("Programmpunkt wurde nicht gefunden.")

    def add_phase(self, name: str, phase_type: PhaseType = PhaseType.GROUP_STAGE) -> TournamentPhase:
        if any(phase.name.casefold() == name.strip().casefold() for phase in self.phases):
            raise ValidationError("Dieser Phasenname ist bereits vergeben.")
        phase = TournamentPhase(name=name, phase_type=phase_type, position=len(self.phases) + 1)
        self.phases.append(phase)
        return phase

    def phase(self, phase_id: UUID) -> TournamentPhase:
        for phase in self.phases:
            if phase.id == phase_id:
                return phase
        raise ValidationError("Turnierphase wurde nicht gefunden.")

    def add_competition(self, name: str, format: CompetitionFormat = CompetitionFormat.ROUND_ROBIN) -> Competition:
        competition = Competition(name=name, format=format)
        self.competitions.append(competition)
        return competition


def generate_round_robin_matches(player_ids: list[UUID]) -> list[Match]:
    players: list[UUID | None] = list(player_ids)
    if len(players) % 2:
        players.append(None)
    rounds: list[Match] = []
    size = len(players)
    for round_index in range(size - 1):
        for index in range(size // 2):
            home, away = players[index], players[size - 1 - index]
            if home is None or away is None:
                continue
            if round_index % 2 and index == 0:
                home, away = away, home
            rounds.append(Match(round_number=round_index + 1, home_id=home, away_id=away))
        players = [players[0], players[-1], *players[1:-1]]
    return rounds
