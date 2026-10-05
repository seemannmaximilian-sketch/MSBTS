from __future__ import annotations

from dataclasses import dataclass

from fts.domain import MatchResult, ValidationError


@dataclass(frozen=True, slots=True)
class SetEntryState:
    """Pure presentation state for the Best-of-3 result editor."""

    scores: tuple[tuple[int, int], ...]
    home_sets: int
    away_sets: int
    complete: bool
    valid: bool
    message: str
    next_row: int | None

    @property
    def match_score(self) -> str:
        return f"{self.home_sets}:{self.away_sets}"


def evaluate_set_entry(
    rows: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    home_name: str,
    away_name: str,
) -> SetEntryState:
    """Evaluate up to three input rows without raising UI-facing exceptions.

    A 0:0 row means "not played". Scores must be contiguous. Once one player
    has won two sets, later rows must remain empty.
    """

    played: list[tuple[int, int]] = []
    gap_seen = False
    home_sets = 0
    away_sets = 0

    for index, raw_score in enumerate(rows[:3]):
        home, away = int(raw_score[0]), int(raw_score[1])
        if (home, away) == (0, 0):
            gap_seen = True
            continue
        if gap_seen:
            return SetEntryState(tuple(played), home_sets, away_sets, False, False,
                                 "Sätze müssen ohne Lücke eingetragen werden.", len(played))
        if max(home_sets, away_sets) == 2:
            return SetEntryState(tuple(played), home_sets, away_sets, False, False,
                                 "Das Spiel ist bereits entschieden. Weitere Sätze bitte auf 0:0 setzen.", None)
        try:
            # Validate this set using standard badminton scoring rules.
            winner_home = home > away
            winner_away = away > home
            if home < 0 or away < 0:
                raise ValidationError("Satzpunkte dürfen nicht negativ sein.")
            if not winner_home and not winner_away:
                raise ValidationError("Ein Satz darf nicht unentschieden enden.")
            winner_points = max(home, away)
            loser_points = min(home, away)
            if winner_points > 30:
                raise ValidationError("Ein Badminton-Satz endet spätestens bei 30 Punkten.")
            if winner_points < 21:
                raise ValidationError("Ein Badminton-Satz endet ab 21 Punkten.")
            if winner_points < 30 and winner_points - loser_points < 2:
                raise ValidationError("Ab 20:20 sind zwei Punkte Vorsprung nötig; bei 29:29 entscheidet der 30. Punkt.")
            if winner_points == 30 and loser_points > 29:
                raise ValidationError("Bei 29:29 entscheidet der 30. Punkt.")
        except ValidationError as error:
            return SetEntryState(tuple(played), home_sets, away_sets, False, False,
                                 f"Satz {index + 1}: {error}", index)

        played.append((home, away))
        home_sets += int(home > away)
        away_sets += int(away > home)

    complete = max(home_sets, away_sets) == 2
    if complete:
        winner = home_name if home_sets > away_sets else away_name
        return SetEntryState(tuple(played), home_sets, away_sets, True, True,
                             f"{winner} gewinnt mit {home_sets}:{away_sets}.", None)

    next_row = len(played)
    if not played:
        message = "Bitte den ersten Satz eingeben."
    else:
        message = f"Zwischenstand {home_sets}:{away_sets} – nächster Satz fehlt."
    return SetEntryState(tuple(played), home_sets, away_sets, False, False, message, next_row)


def validated_set_scores(
    rows: list[tuple[int, int]] | tuple[tuple[int, int], ...],
) -> list[tuple[int, int]]:
    """Return normalized set scores or raise the domain validation message."""

    played: list[tuple[int, int]] = []
    gap_seen = False
    for home, away in rows[:3]:
        score = (int(home), int(away))
        if score == (0, 0):
            gap_seen = True
            continue
        if gap_seen:
            raise ValidationError("Sätze müssen ohne Lücke eingetragen werden.")
        played.append(score)
    MatchResult.from_set_scores(played)
    return played
