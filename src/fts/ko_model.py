
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
from typing import Optional

class KOValidationError(ValueError):
    pass

def validate_set_score(home: int, away: int) -> None:
    if home < 0 or away < 0:
        raise KOValidationError("Satzpunkte dürfen nicht negativ sein.")
    if home == away:
        raise KOValidationError("Ein Satz darf nicht unentschieden enden.")
    winner, loser = max(home, away), min(home, away)
    if winner < 11 or winner - loser < 2:
        raise KOValidationError("Ein Satz endet ab 11 Punkten mit mindestens zwei Punkten Vorsprung.")

def match_result_from_sets(scores: list[tuple[int, int]]) -> tuple[int, int]:
    if not 2 <= len(scores) <= 3:
        raise KOValidationError("Ein Best-of-3-Spiel benötigt zwei oder drei Sätze.")
    home_sets = away_sets = 0
    for index, (home, away) in enumerate(scores, 1):
        validate_set_score(home, away)
        home_sets += int(home > away)
        away_sets += int(away > home)
        if max(home_sets, away_sets) == 2 and index != len(scores):
            raise KOValidationError("Nach dem zweiten gewonnenen Satz dürfen keine weiteren Sätze folgen.")
    if max(home_sets, away_sets) != 2:
        raise KOValidationError("Das Spiel ist noch nicht entschieden.")
    return home_sets, away_sets

@dataclass
class KOMatch:
    key: str
    title: str
    home: str = ""
    away: str = ""
    set_scores: list[tuple[int, int]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        if not self.home or not self.away or not self.set_scores:
            return False
        try:
            match_result_from_sets(self.set_scores)
            return True
        except KOValidationError:
            return False

    @property
    def match_score(self) -> str:
        if not self.complete:
            return ""
        h, a = match_result_from_sets(self.set_scores)
        return f"{h}:{a}"

    @property
    def winner(self) -> str:
        if not self.complete:
            return ""
        h, a = match_result_from_sets(self.set_scores)
        return self.home if h > a else self.away

    @property
    def loser(self) -> str:
        if not self.complete:
            return ""
        return self.away if self.winner == self.home else self.home

@dataclass
class KOTournament:
    name: str = "Freudenholm 2026 – KO-System"
    participants: list[str] = field(default_factory=lambda: [""] * 8)
    matches: dict[str, KOMatch] = field(default_factory=dict)
    active_match_keys: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.matches:
            self.matches = {
                "VF1": KOMatch("VF1", "Viertelfinale 1"),
                "VF2": KOMatch("VF2", "Viertelfinale 2"),
                "VF3": KOMatch("VF3", "Viertelfinale 3"),
                "VF4": KOMatch("VF4", "Viertelfinale 4"),
                "HF1": KOMatch("HF1", "Halbfinale 1"),
                "HF2": KOMatch("HF2", "Halbfinale 2"),
                "P3": KOMatch("P3", "Spiel um Platz 3"),
                "F": KOMatch("F", "Finale"),
            }
        self.recalculate()

    def set_participants(self, names: list[str]) -> None:
        clean = [n.strip() for n in names]
        if len(clean) != 8:
            raise KOValidationError("Es werden genau acht Viertelfinalteilnehmer benötigt.")
        if any(not n for n in clean):
            raise KOValidationError("Bitte alle acht Viertelfinalteilnehmer eintragen.")
        if len(set(n.casefold() for n in clean)) != 8:
            raise KOValidationError("Jeder Teilnehmer darf nur einmal vorkommen.")
        self.participants = clean
        self.active_match_keys = []
        for i in range(4):
            match = self.matches[f"VF{i+1}"]
            match.home = clean[i*2]
            match.away = clean[i*2+1]
        self._clear_from("HF1")
        self.recalculate()

    def record(self, key: str, scores: list[tuple[int, int]]) -> None:
        match = self.matches[key]
        if not match.home or not match.away:
            raise KOValidationError("Die Teilnehmer dieses Spiels stehen noch nicht fest.")
        match_result_from_sets(scores)
        match.set_scores = list(scores)
        self.recalculate()

    def clear_result(self, key: str) -> None:
        self.matches[key].set_scores = []
        self.recalculate()

    def _clear_from(self, key: str) -> None:
        order = ["HF1", "HF2", "P3", "F"]
        if key in order:
            start = order.index(key)
            for item in order[start:]:
                self.matches[item].set_scores = []

    def recalculate(self) -> None:
        # Viertelfinalpaarungen aus Teilnehmerliste
        if len(self.participants) == 8 and all(self.participants):
            for i in range(4):
                self.matches[f"VF{i+1}"].home = self.participants[i*2]
                self.matches[f"VF{i+1}"].away = self.participants[i*2+1]

        old_slots = {k: (m.home, m.away) for k, m in self.matches.items()}

        self.matches["HF1"].home = self.matches["VF1"].winner
        self.matches["HF1"].away = self.matches["VF2"].winner
        self.matches["HF2"].home = self.matches["VF3"].winner
        self.matches["HF2"].away = self.matches["VF4"].winner

        self.matches["F"].home = self.matches["HF1"].winner
        self.matches["F"].away = self.matches["HF2"].winner
        self.matches["P3"].home = self.matches["HF1"].loser
        self.matches["P3"].away = self.matches["HF2"].loser

        for key in ("HF1", "HF2", "F", "P3"):
            m = self.matches[key]
            if old_slots.get(key) != (m.home, m.away):
                m.set_scores = []

        self.active_match_keys = [
            key for key in self.active_match_keys
            if key in self.matches
            and self.matches[key].home
            and self.matches[key].away
            and not self.matches[key].complete
        ]


    def set_match_active(self, key: str, active: bool) -> None:
        """Mark or unmark a playable match as currently running on the projector."""
        if key not in self.matches:
            raise KOValidationError("Unbekanntes Spiel.")
        match = self.matches[key]
        if active:
            if not match.home or not match.away:
                raise KOValidationError("Dieses Spiel kann noch nicht gestartet werden.")
            if match.complete:
                raise KOValidationError("Ein abgeschlossenes Spiel kann nicht als laufend markiert werden.")
            if key not in self.active_match_keys:
                if len(self.active_match_keys) >= 3:
                    raise KOValidationError("Es können höchstens drei Spiele gleichzeitig auf dem Beamer angezeigt werden.")
                self.active_match_keys.append(key)
        else:
            self.active_match_keys = [item for item in self.active_match_keys if item != key]

    @property
    def active_matches(self) -> list[KOMatch]:
        """Return currently running matches in the user's selection order."""
        return [self.matches[key] for key in self.active_match_keys if key in self.matches]


    def next_open_match(self) -> KOMatch | None:
        """Return the next playable unfinished match in tournament order."""
        for key in ("VF1", "VF2", "VF3", "VF4", "HF1", "HF2", "P3", "F"):
            match = self.matches[key]
            if match.home and match.away and not match.complete:
                return match
        return None

    @property
    def placements(self) -> tuple[str, str, str]:
        final = self.matches["F"]
        third = self.matches["P3"]
        return (
            final.winner if final.complete else "",
            final.loser if final.complete else "",
            third.winner if third.complete else "",
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "participants": self.participants,
            "active_match_keys": self.active_match_keys,
            "matches": {
                key: {
                    "key": m.key, "title": m.title, "home": m.home, "away": m.away,
                    "set_scores": [list(s) for s in m.set_scores],
                } for key, m in self.matches.items()
            }
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KOTournament":
        matches = {
            key: KOMatch(
                key=value["key"],
                title=value["title"],
                home=value.get("home", ""),
                away=value.get("away", ""),
                set_scores=[tuple(x) for x in value.get("set_scores", [])],
            )
            for key, value in data.get("matches", {}).items()
        }
        return cls(
            name=data.get("name", "Freudenholm 2026 – KO-System"),
            participants=list(data.get("participants", [""] * 8)),
            matches=matches,
            active_match_keys=list(data.get("active_match_keys", [])),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "KOTournament":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
