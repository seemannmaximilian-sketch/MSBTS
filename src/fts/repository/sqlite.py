from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import UUID

from fts.domain import (
    Competition,
    Group, PhaseType, TournamentPhase,
    CompetitionFormat,
    CompetitionStatus,
    LiveMatchStatus,
    Match,
    MatchResult,
    Person,
    Team, TeamMember, BoardGame, TeamMatch, TeamCompetition, PlayingArea, ScheduleItem,
    Tournament,
)


class SQLiteTournamentRepository:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tournaments (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    organizer TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    start_date TEXT NOT NULL DEFAULT '',
                    end_date TEXT NOT NULL DEFAULT '',
                    table_count INTEGER NOT NULL DEFAULT 3,
                    match_duration_minutes INTEGER NOT NULL DEFAULT 15,
                    schedule_start_time TEXT NOT NULL DEFAULT '17:10',
                    scorer_count INTEGER NOT NULL DEFAULT 6,
                    scorer_names_json TEXT NOT NULL DEFAULT '[]',
                    best_of INTEGER NOT NULL DEFAULT 3,
                    qualification_enabled INTEGER NOT NULL DEFAULT 1,
                    intermediate_enabled INTEGER NOT NULL DEFAULT 1,
                    teams_json TEXT NOT NULL DEFAULT '[]',
                    team_competitions_json TEXT NOT NULL DEFAULT '[]',
                    playing_areas_json TEXT NOT NULL DEFAULT '[]',
                    schedule_items_json TEXT NOT NULL DEFAULT '[]',
                    phases_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS persons (
                    id TEXT PRIMARY KEY,
                    tournament_id TEXT NOT NULL,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    club TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'Offen',
                    start_number INTEGER,
                    license_number TEXT NOT NULL DEFAULT '',
                    position INTEGER NOT NULL,
                    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS competitions (
                    id TEXT PRIMARY KEY,
                    tournament_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    format TEXT NOT NULL DEFAULT 'round_robin',
                    waiting_ids TEXT NOT NULL DEFAULT '[]',
                    swiss_rounds INTEGER NOT NULL DEFAULT 5,
                    bye_ids TEXT NOT NULL DEFAULT '[]',
                    position INTEGER NOT NULL,
                    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS registrations (
                    competition_id TEXT NOT NULL,
                    person_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (competition_id, person_id),
                    FOREIGN KEY (competition_id) REFERENCES competitions(id) ON DELETE CASCADE,
                    FOREIGN KEY (person_id) REFERENCES persons(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS live_operation_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tournament_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    phase_id TEXT,
                    group_id TEXT,
                    match_id TEXT,
                    table_number INTEGER,
                    signature TEXT UNIQUE
                );

                CREATE TABLE IF NOT EXISTS result_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tournament_id TEXT NOT NULL,
                    phase_id TEXT NOT NULL,
                    group_id TEXT,
                    match_id TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    changed_by TEXT NOT NULL DEFAULT 'Turnierleitung',
                    reason TEXT NOT NULL DEFAULT '',
                    old_result_json TEXT,
                    new_result_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY,
                    competition_id TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    home_id TEXT NOT NULL,
                    away_id TEXT NOT NULL,
                    home_score INTEGER,
                    away_score INTEGER,
                    position INTEGER NOT NULL,
                    FOREIGN KEY (competition_id) REFERENCES competitions(id) ON DELETE CASCADE,
                    FOREIGN KEY (home_id) REFERENCES persons(id),
                    FOREIGN KEY (away_id) REFERENCES persons(id)
                );
                """
            )
            tournament_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tournaments)")}
            for column in ("organizer", "location", "start_date", "end_date", "schedule_start_time", "scorer_names_json", "teams_json", "team_competitions_json", "playing_areas_json", "schedule_items_json", "phases_json"):
                if column not in tournament_columns:
                    default = "'[]'" if column.endswith('_json') else ("'17:10'" if column == 'schedule_start_time' else "''")
                    connection.execute(f"ALTER TABLE tournaments ADD COLUMN {column} TEXT NOT NULL DEFAULT {default}")
            tournament_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tournaments)")}
            for column, default in {"table_count": 3, "match_duration_minutes": 15, "scorer_count": 6, "best_of": 3, "qualification_enabled": 1, "intermediate_enabled": 1}.items():
                if column not in tournament_columns:
                    connection.execute(f"ALTER TABLE tournaments ADD COLUMN {column} INTEGER NOT NULL DEFAULT {default}")
            person_columns = {row["name"] for row in connection.execute("PRAGMA table_info(persons)")}
            if "club" not in person_columns:
                connection.execute("ALTER TABLE persons ADD COLUMN club TEXT NOT NULL DEFAULT ''")
            if "category" not in person_columns:
                connection.execute("ALTER TABLE persons ADD COLUMN category TEXT NOT NULL DEFAULT 'Offen'")
            if "start_number" not in person_columns:
                connection.execute("ALTER TABLE persons ADD COLUMN start_number INTEGER")
            if "license_number" not in person_columns:
                connection.execute("ALTER TABLE persons ADD COLUMN license_number TEXT NOT NULL DEFAULT ''")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(competitions)")}
            if "format" not in columns:
                connection.execute("ALTER TABLE competitions ADD COLUMN format TEXT NOT NULL DEFAULT 'round_robin'")
            if "waiting_ids" not in columns:
                connection.execute("ALTER TABLE competitions ADD COLUMN waiting_ids TEXT NOT NULL DEFAULT '[]'")
            if "swiss_rounds" not in columns:
                connection.execute("ALTER TABLE competitions ADD COLUMN swiss_rounds INTEGER NOT NULL DEFAULT 5")
            if "bye_ids" not in columns:
                connection.execute("ALTER TABLE competitions ADD COLUMN bye_ids TEXT NOT NULL DEFAULT '[]'")

    def load(self) -> Tournament | None:
        with self._connect() as connection:
            tournament_row = connection.execute(
                "SELECT id, name, organizer, location, start_date, end_date, table_count, match_duration_minutes, schedule_start_time, scorer_count, scorer_names_json, best_of, qualification_enabled, intermediate_enabled, teams_json, team_competitions_json, playing_areas_json, schedule_items_json, phases_json FROM tournaments LIMIT 1"
            ).fetchone()
            if tournament_row is None:
                return None

            tournament_id = UUID(tournament_row["id"])
            people = [
                Person(
                    id=UUID(row["id"]),
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    club=row["club"],
                    category=row["category"],
                    start_number=row["start_number"],
                    license_number=row["license_number"],
                )
                for row in connection.execute(
                    """
                    SELECT id, first_name, last_name, club, category, start_number, license_number
                    FROM persons
                    WHERE tournament_id = ?
                    ORDER BY position
                    """,
                    (str(tournament_id),),
                )
            ]

            competitions: list[Competition] = []
            competition_rows = connection.execute(
                """
                SELECT id, name, status, format, waiting_ids, swiss_rounds, bye_ids
                FROM competitions
                WHERE tournament_id = ?
                ORDER BY position
                """,
                (str(tournament_id),),
            ).fetchall()

            for row in competition_rows:
                competition_id = UUID(row["id"])
                registered_ids = [
                    UUID(registration["person_id"])
                    for registration in connection.execute(
                        """
                        SELECT person_id
                        FROM registrations
                        WHERE competition_id = ?
                        ORDER BY position
                        """,
                        (str(competition_id),),
                    )
                ]
                matches: list[Match] = []
                for match_row in connection.execute(
                    """
                    SELECT id, round_number, home_id, away_id, home_score, away_score
                    FROM matches
                    WHERE competition_id = ?
                    ORDER BY position
                    """,
                    (str(competition_id),),
                ):
                    result = None
                    if match_row["home_score"] is not None:
                        result = MatchResult(
                            home_score=match_row["home_score"],
                            away_score=match_row["away_score"],
                        )
                    matches.append(
                        Match(
                            id=UUID(match_row["id"]),
                            round_number=match_row["round_number"],
                            home_id=UUID(match_row["home_id"]),
                            away_id=UUID(match_row["away_id"]),
                            result=result,
                            table_number=None,
                            scheduled_time="",
                        )
                    )
                competitions.append(
                    Competition(
                        id=competition_id,
                        name=row["name"],
                        status=CompetitionStatus(row["status"]),
                        format=CompetitionFormat(row["format"]),
                        registered_ids=registered_ids,
                        matches=matches,
                        waiting_ids=[UUID(value) for value in json.loads(row["waiting_ids"])],
                        swiss_rounds=row["swiss_rounds"],
                        bye_ids=[UUID(value) for value in json.loads(row["bye_ids"])],
                    )
                )


            teams_data = json.loads(tournament_row["teams_json"] or "[]")
            teams = [Team(id=UUID(item["id"]), name=item["name"], club=item.get("club", ""), abbreviation=item.get("abbreviation", ""), captain=item.get("captain", ""), notes=item.get("notes", ""), members=[TeamMember(UUID(member["person_id"]), int(member["board_number"]), bool(member.get("substitute", False)), bool(member.get("active", True))) for member in item.get("members", [])]) for item in teams_data]
            team_competitions = []
            for item in json.loads(tournament_row["team_competitions_json"] or "[]"):
                matches = []
                for match in item.get("matches", []):
                    boards = [BoardGame(id=UUID(board["id"]), board_number=int(board["board_number"]), home_person_id=UUID(board["home_person_id"]), away_person_id=UUID(board["away_person_id"]), result=None if board.get("result") is None else MatchResult(int(board["result"]["home_score"]), int(board["result"]["away_score"]))) for board in match.get("boards", [])]
                    matches.append(TeamMatch(id=UUID(match["id"]), round_number=int(match["round_number"]), home_team_id=UUID(match["home_team_id"]), away_team_id=UUID(match["away_team_id"]), boards=boards))
                team_competitions.append(TeamCompetition(id=UUID(item["id"]), name=item["name"], boards=int(item.get("boards", 4)), status=CompetitionStatus(item["status"]), registered_team_ids=[UUID(value) for value in item.get("registered_team_ids", [])], matches=matches))

            playing_areas = [PlayingArea(id=UUID(item["id"]), name=item["name"], hall=item.get("hall", ""), room=item.get("room", ""), table_number=item.get("table_number", ""), notes=item.get("notes", ""), active=bool(item.get("active", True))) for item in json.loads(tournament_row["playing_areas_json"] or "[]")]
            schedule_items = [ScheduleItem(id=UUID(item["id"]), title=item["title"], start_time=item["start_time"], end_time=item.get("end_time", ""), location=item.get("location", ""), status=item.get("status", "geplant"), notes=item.get("notes", "")) for item in json.loads(tournament_row["schedule_items_json"] or "[]")]
            phases = [
                TournamentPhase(
                    id=UUID(item["id"]),
                    name=item["name"],
                    phase_type=PhaseType(item.get("phase_type", PhaseType.GROUP_STAGE.value)),
                    position=int(item.get("position", index + 1)),
                    participant_ids=[UUID(value) for value in item.get("participant_ids", [])],
                    matches=[
                        Match(
                            id=UUID(match["id"]),
                            round_number=int(match["round_number"]),
                            home_id=UUID(match["home_id"]),
                            away_id=UUID(match["away_id"]),
                            result=None if match.get("result") is None else MatchResult(
                                int(match["result"]["home_score"]), int(match["result"]["away_score"]),
                                tuple(tuple(int(value) for value in score) for score in match["result"].get("set_scores", [])),
                            ),
                            table_number=match.get("table_number"),
                            scheduled_time=match.get("scheduled_time", ""),
                            live_status=LiveMatchStatus(match.get("live_status", "finished" if match.get("result") is not None else "planned")),
                            started_at=match.get("started_at", ""),
                            finished_at=match.get("finished_at", ""),
                            match_kind=match.get("match_kind", "standard"),
                        )
                        for match in item.get("matches", [])
                    ],
                    waiting_ids=[UUID(value) for value in item.get("waiting_ids", [])],
                    next_phase_id=UUID(item["next_phase_id"]) if item.get("next_phase_id") else None,
                    auto_advance=bool(item.get("auto_advance", False)),
                    distribution_mode=item.get("distribution_mode", "snake"),
                    groups=[
                        Group(
                            id=UUID(group["id"]),
                            name=group["name"],
                            participant_ids=[UUID(value) for value in group.get("participant_ids", [])],
                            qualification_count=int(group.get("qualification_count", 2)),
                            qualification_playoff=bool(group.get("qualification_playoff", False)),
                            target_capacity=int(group.get("target_capacity", 0)),
                            playoff_match=None if group.get("playoff_match") is None else Match(
                                id=UUID(group["playoff_match"]["id"]), round_number=int(group["playoff_match"]["round_number"]),
                                home_id=UUID(group["playoff_match"]["home_id"]), away_id=UUID(group["playoff_match"]["away_id"]),
                                result=None if group["playoff_match"].get("result") is None else MatchResult(
                                    int(group["playoff_match"]["result"]["home_score"]), int(group["playoff_match"]["result"]["away_score"]),
                                    tuple(tuple(int(value) for value in score) for score in group["playoff_match"]["result"].get("set_scores", [])),
                                ), table_number=group["playoff_match"].get("table_number"), scheduled_time=group["playoff_match"].get("scheduled_time", ""),
                            ),
                            matches=[
                                Match(
                                    id=UUID(match["id"]),
                                    round_number=int(match["round_number"]),
                                    home_id=UUID(match["home_id"]),
                                    away_id=UUID(match["away_id"]),
                                    result=None if match.get("result") is None else MatchResult(
                                        int(match["result"]["home_score"]),
                                        int(match["result"]["away_score"]),
                                        tuple(tuple(int(value) for value in score) for score in match["result"].get("set_scores", [])),
                                    ),
                                    table_number=match.get("table_number"),
                                    scheduled_time=match.get("scheduled_time", ""),
                                    live_status=LiveMatchStatus(match.get("live_status", "finished" if match.get("result") is not None else "planned")),
                                    started_at=match.get("started_at", ""),
                                    finished_at=match.get("finished_at", ""),
                                    match_kind=match.get("match_kind", "standard"),
                                )
                                for match in group.get("matches", [])
                            ],
                        )
                        for group in item.get("groups", [])
                    ],
                )
                for index, item in enumerate(json.loads(tournament_row["phases_json"] or "[]"))
            ]

            return Tournament(
                id=tournament_id,
                name=tournament_row["name"],
                organizer=tournament_row["organizer"],
                location=tournament_row["location"],
                start_date=tournament_row["start_date"],
                end_date=tournament_row["end_date"],
                table_count=tournament_row["table_count"],
                match_duration_minutes=tournament_row["match_duration_minutes"],
                schedule_start_time=tournament_row["schedule_start_time"] or "17:10",
                scorer_count=tournament_row["scorer_count"],
                scorer_names=json.loads(tournament_row["scorer_names_json"] or "[]"),
                best_of=tournament_row["best_of"],
                qualification_enabled=bool(tournament_row["qualification_enabled"]),
                intermediate_enabled=bool(tournament_row["intermediate_enabled"]),
                people=people,
                competitions=competitions,
                teams=teams,
                team_competitions=team_competitions,
                playing_areas=playing_areas,
                schedule_items=schedule_items,
                phases=phases,
            )

    def save(self, tournament: Tournament) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM tournaments")

            teams_json = json.dumps([{
                "id": str(team.id), "name": team.name, "club": team.club, "abbreviation": team.abbreviation,
                "captain": team.captain, "notes": team.notes,
                "members": [{"person_id": str(member.person_id), "board_number": member.board_number, "substitute": member.substitute, "active": member.active} for member in team.members],
            } for team in tournament.teams], ensure_ascii=False)
            team_competitions_json = json.dumps([{
                "id": str(comp.id), "name": comp.name, "boards": comp.boards, "status": comp.status.value,
                "registered_team_ids": [str(value) for value in comp.registered_team_ids],
                "matches": [{"id": str(match.id), "round_number": match.round_number, "home_team_id": str(match.home_team_id), "away_team_id": str(match.away_team_id),
                    "boards": [{"id": str(board.id), "board_number": board.board_number, "home_person_id": str(board.home_person_id), "away_person_id": str(board.away_person_id), "result": None if board.result is None else {"home_score": board.result.home_score, "away_score": board.result.away_score}} for board in match.boards]} for match in comp.matches],
            } for comp in tournament.team_competitions], ensure_ascii=False)
            playing_areas_json = json.dumps([{"id": str(area.id), "name": area.name, "hall": area.hall, "room": area.room, "table_number": area.table_number, "notes": area.notes, "active": area.active} for area in tournament.playing_areas], ensure_ascii=False)
            schedule_items_json = json.dumps([{"id": str(item.id), "title": item.title, "start_time": item.start_time, "end_time": item.end_time, "location": item.location, "status": item.status, "notes": item.notes} for item in tournament.schedule_items], ensure_ascii=False)
            phases_json = json.dumps([{
                "id": str(phase.id),
                "name": phase.name,
                "phase_type": phase.phase_type.value,
                "position": phase.position,
                "participant_ids": [str(value) for value in phase.participant_ids],
                "matches": [{
                    "id": str(match.id),
                    "round_number": match.round_number,
                    "home_id": str(match.home_id),
                    "away_id": str(match.away_id),
                    "table_number": match.table_number,
                    "scheduled_time": match.scheduled_time,
                    "live_status": match.live_status.value,
                    "started_at": match.started_at,
                    "finished_at": match.finished_at,
                    "match_kind": match.match_kind,
                    "result": None if match.result is None else {
                        "home_score": match.result.home_score,
                        "away_score": match.result.away_score,
                        "set_scores": [list(score) for score in match.result.set_scores],
                    },
                } for match in phase.matches],
                "waiting_ids": [str(value) for value in phase.waiting_ids],
                "next_phase_id": str(phase.next_phase_id) if phase.next_phase_id else None,
                "auto_advance": phase.auto_advance,
                "distribution_mode": phase.distribution_mode,
                "groups": [{
                    "id": str(group.id),
                    "name": group.name,
                    "participant_ids": [str(value) for value in group.participant_ids],
                    "qualification_count": group.qualification_count,
                    "qualification_playoff": group.qualification_playoff,
                    "target_capacity": group.target_capacity,
                    "playoff_match": None if group.playoff_match is None else {
                        "id": str(group.playoff_match.id), "round_number": group.playoff_match.round_number,
                        "home_id": str(group.playoff_match.home_id), "away_id": str(group.playoff_match.away_id),
                        "table_number": group.playoff_match.table_number, "scheduled_time": group.playoff_match.scheduled_time,
                        "result": None if group.playoff_match.result is None else {
                            "home_score": group.playoff_match.result.home_score, "away_score": group.playoff_match.result.away_score,
                            "set_scores": [list(score) for score in group.playoff_match.result.set_scores],
                        },
                    },
                    "matches": [{
                        "id": str(match.id),
                        "round_number": match.round_number,
                        "home_id": str(match.home_id),
                        "away_id": str(match.away_id),
                        "table_number": match.table_number,
                        "scheduled_time": match.scheduled_time,
                        "live_status": match.live_status.value,
                        "started_at": match.started_at,
                        "finished_at": match.finished_at,
                        "match_kind": match.match_kind,
                        "result": None if match.result is None else {
                            "home_score": match.result.home_score,
                            "away_score": match.result.away_score,
                            "set_scores": [list(score) for score in match.result.set_scores],
                        },
                    } for match in group.matches],
                } for group in phase.groups],
            } for phase in tournament.phases], ensure_ascii=False)
            connection.execute(
                "INSERT INTO tournaments (id, name, organizer, location, start_date, end_date, table_count, match_duration_minutes, schedule_start_time, scorer_count, scorer_names_json, best_of, qualification_enabled, intermediate_enabled, teams_json, team_competitions_json, playing_areas_json, schedule_items_json, phases_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(tournament.id), tournament.name, tournament.organizer, tournament.location, tournament.start_date, tournament.end_date, tournament.table_count, tournament.match_duration_minutes, tournament.schedule_start_time, tournament.scorer_count, json.dumps(tournament.scorer_names, ensure_ascii=False), tournament.best_of, int(tournament.qualification_enabled), int(tournament.intermediate_enabled), teams_json, team_competitions_json, playing_areas_json, schedule_items_json, phases_json),
            )

            connection.executemany(
                """
                INSERT INTO persons
                    (id, tournament_id, first_name, last_name, club, category, start_number, license_number, position)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(person.id),
                        str(tournament.id),
                        person.first_name,
                        person.last_name,
                        person.club,
                        person.category,
                        person.start_number,
                        person.license_number,
                        position,
                    )
                    for position, person in enumerate(tournament.people)
                ],
            )

            for competition_position, competition in enumerate(tournament.competitions):
                connection.execute(
                    """
                    INSERT INTO competitions
                        (id, tournament_id, name, status, format, waiting_ids, swiss_rounds, bye_ids, position)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(competition.id),
                        str(tournament.id),
                        competition.name,
                        competition.status.value,
                        competition.format.value,
                        json.dumps([str(value) for value in competition.waiting_ids]),
                        competition.swiss_rounds,
                        json.dumps([str(value) for value in competition.bye_ids]),
                        competition_position,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO registrations
                        (competition_id, person_id, position)
                    VALUES (?, ?, ?)
                    """,
                    [
                        (str(competition.id), str(person_id), position)
                        for position, person_id in enumerate(competition.registered_ids)
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO matches
                        (id, competition_id, round_number, home_id, away_id,
                         home_score, away_score, position)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            str(match.id),
                            str(competition.id),
                            match.round_number,
                            str(match.home_id),
                            str(match.away_id),
                            match.result.home_score if match.result else None,
                            match.result.away_score if match.result else None,
                            position,
                        )
                        for position, match in enumerate(competition.matches)
                    ],
                )


    def append_live_operation_event(
        self, tournament_id: UUID, event_type: str, summary: str, *, details: str = "",
        phase_id: UUID | None = None, group_id: UUID | None = None, match_id: UUID | None = None,
        table_number: int | None = None, signature: str | None = None, occurred_at: str | None = None,
    ) -> bool:
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO live_operation_audit
                    (tournament_id, occurred_at, event_type, summary, details, phase_id, group_id, match_id, table_number, signature)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(tournament_id), occurred_at or __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                     event_type, summary, details, str(phase_id) if phase_id else None,
                     str(group_id) if group_id else None, str(match_id) if match_id else None, table_number, signature),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def live_operation_audit(self, limit: int = 200) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM live_operation_audit ORDER BY occurred_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
    def append_result_audit(
        self, *, tournament_id: UUID, phase_id: UUID, group_id: UUID | None,
        match_id: UUID, changed_at: str, changed_by: str, reason: str,
        old_result: dict | None, new_result: dict,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO result_audit
                    (tournament_id, phase_id, group_id, match_id, changed_at, changed_by, reason, old_result_json, new_result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (str(tournament_id), str(phase_id), str(group_id) if group_id else None, str(match_id),
                 changed_at, changed_by.strip() or "Turnierleitung", reason.strip(),
                 None if old_result is None else json.dumps(old_result, ensure_ascii=False),
                 json.dumps(new_result, ensure_ascii=False)),
            )

    def result_audit(self, match_id: UUID | None = None) -> list[dict]:
        query = "SELECT * FROM result_audit"
        params: tuple[str, ...] = ()
        if match_id is not None:
            query += " WHERE match_id = ?"
            params = (str(match_id),)
        query += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [{
            "id": row["id"], "tournament_id": row["tournament_id"],
            "phase_id": row["phase_id"], "group_id": row["group_id"],
            "match_id": row["match_id"], "changed_at": row["changed_at"],
            "changed_by": row["changed_by"], "reason": row["reason"],
            "old_result": None if row["old_result_json"] is None else json.loads(row["old_result_json"]),
            "new_result": json.loads(row["new_result_json"]),
        } for row in rows]

    def delete(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM tournaments")
