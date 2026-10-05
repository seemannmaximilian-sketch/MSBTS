from __future__ import annotations
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QVBoxLayout, QWidget
)

from .ko_model import KOTournament, KOValidationError
from .ko_layout import recommended_window_dimensions

AUTOSAVE = Path.home() / ".fts_ko_autosave.json"
BACKUP_DIR = Path.home() / ".fts_ko_backups"
APP_ICON = Path(__file__).resolve().parent / "resources" / "fts_table_tennis_icon.png"

CREAM = "#F5EFE3"
IVORY = "#FFFDF8"
BROWN = "#68472F"
BROWN_DARK = "#3E2D20"
BROWN_MID = "#8A6748"
GOLD = "#C9A66B"
SAND = "#D8C7AD"
SAND_LIGHT = "#E9DDCB"
SUCCESS = "#607A55"
MUTED = "#7A6A5A"



from .ko_theme import spectator_palette


class SetDialog(QDialog):
    def __init__(self, home: str, away: str, current: list[tuple[int, int]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Ergebnis: {home} – {away}")
        self.setMinimumWidth(430)
        self.setObjectName("resultDialog")
        self.rows: list[tuple[QSpinBox, QSpinBox]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        title = QLabel(f"<b>{home}</b> gegen <b>{away}</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-size: 17px; color: {BROWN_DARK};")
        layout.addWidget(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.addWidget(QLabel("Satz"), 0, 0)
        grid.addWidget(QLabel(home), 0, 1)
        grid.addWidget(QLabel(away), 0, 2)
        for i in range(3):
            h = QSpinBox(); a = QSpinBox()
            h.setRange(0, 99); a.setRange(0, 99)
            h.setMinimumHeight(36); a.setMinimumHeight(36)
            if i < len(current):
                h.setValue(current[i][0]); a.setValue(current[i][1])
            grid.addWidget(QLabel(str(i + 1)), i + 1, 0)
            grid.addWidget(h, i + 1, 1); grid.addWidget(a, i + 1, 2)
            self.rows.append((h, a))
        layout.addLayout(grid)
        info = QLabel("Nicht benötigten dritten Satz auf 0:0 lassen.")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED};")
        layout.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Ergebnis speichern")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def scores(self) -> list[tuple[int, int]]:
        result: list[tuple[int, int]] = []
        gap = False
        for home, away in self.rows:
            pair = (home.value(), away.value())
            if pair == (0, 0):
                gap = True
                continue
            if gap:
                raise KOValidationError("Sätze müssen ohne Lücke eingetragen werden.")
            result.append(pair)
        return result


class MatchBox(QGroupBox):
    def __init__(self, key: str, on_result, on_clear, on_active, compact: bool = True):
        super().__init__()
        self.key = key
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(184 if compact else 198)
        self.players = QLabel()
        self.players.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.players.setWordWrap(True)
        self.players.setStyleSheet(f"font-size: 13px; font-weight: 650; color: {BROWN_DARK};")
        self.result = QLabel("Noch offen")
        self.result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result.setWordWrap(True)
        self.result.setStyleSheet(f"font-weight: 700; color: {BROWN};")
        self.live = QPushButton("○ Auf Beamer")
        self.live.setCheckable(True)
        self.live.setObjectName("liveButton")
        self.button = QPushButton("Ergebnis")
        self.clear = QPushButton("Löschen")
        self.live.setMinimumHeight(30); self.button.setMinimumHeight(32); self.clear.setMinimumHeight(32)
        self.live.toggled.connect(lambda checked: on_active(self.key, checked))
        self.button.clicked.connect(lambda: on_result(self.key))
        self.clear.clicked.connect(lambda: on_clear(self.key))
        row = QHBoxLayout(); row.setSpacing(6); row.addWidget(self.button, 2); row.addWidget(self.clear, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(5)
        layout.addWidget(self.players); layout.addWidget(self.result); layout.addWidget(self.live); layout.addLayout(row)

    def update_match(self, match, highlighted: bool = False, active: bool = False):
        self.setTitle(match.title)
        home = match.home or "noch offen"
        away = match.away or "noch offen"
        self.players.setText(f"{home}\ngegen\n{away}")
        playable = bool(match.home and match.away and not match.complete)
        self.button.setEnabled(bool(match.home and match.away))
        self.clear.setEnabled(bool(match.set_scores))
        self.live.blockSignals(True)
        self.live.setChecked(active)
        self.live.setText("● Läuft auf Beamer" if active else "○ Auf Beamer")
        self.live.setEnabled(playable or active)
        self.live.blockSignals(False)
        if match.complete:
            sets = " · ".join(f"{h}:{a}" for h, a in match.set_scores)
            self.result.setText(f"{match.match_score} ({sets})\nSieger: {match.winner}")
        else:
            self.result.setText("Noch offen")
        if highlighted:
            self.setProperty("matchState", "highlighted")
        else:
            self.setProperty("matchState", "normal")
        self.style().unpolish(self)
        self.style().polish(self)


class SpectatorWindow(QMainWindow):
    def __init__(self, tournament: KOTournament):
        super().__init__()
        self.tournament = tournament
        self.presentation_mode = "light"
        self.setWindowTitle("FTS KO – Beamer-Modus")
        if APP_ICON.exists():
            self.setWindowIcon(QIcon(str(APP_ICON)))

        self.root = QWidget()
        layout = QVBoxLayout(self.root)
        layout.setContentsMargins(34, 24, 34, 22)
        layout.setSpacing(18)

        toolbar = QHBoxLayout()
        toolbar.addStretch()
        self.mode_button = QPushButton("Dunkler Präsentationsmodus")
        self.mode_button.setMinimumHeight(38)
        self.mode_button.clicked.connect(self.toggle_presentation_mode)
        toolbar.addWidget(self.mode_button)
        close_button = QPushButton("Beamer schließen")
        close_button.setMinimumHeight(38)
        close_button.clicked.connect(self.close)
        toolbar.addWidget(close_button)
        layout.addLayout(toolbar)

        self.title = QLabel()
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.live_row = QHBoxLayout()
        self.live_row.setSpacing(16)
        self.live_cards: list[QLabel] = []
        for _ in range(3):
            card = QLabel()
            card.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card.setWordWrap(True)
            card.setMinimumHeight(190)
            self.live_cards.append(card)
            self.live_row.addWidget(card, 1)
        self.tree = QLabel()
        self.tree.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tree.setWordWrap(True)
        self.podium = QLabel()
        self.podium.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.podium.setWordWrap(True)

        footer = QHBoxLayout()
        self.progress = QLabel()
        self.clock = QLabel()
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        footer.addWidget(self.progress)
        footer.addStretch()
        footer.addWidget(self.clock)

        layout.addWidget(self.title)
        layout.addLayout(self.live_row)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.podium)
        layout.addLayout(footer)
        self.setCentralWidget(self.root)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_clock)
        self.timer.start(1000)
        self.apply_presentation_theme()
        self.refresh()

    def toggle_presentation_mode(self):
        self.presentation_mode = "dark" if self.presentation_mode == "light" else "light"
        self.apply_presentation_theme()

    def apply_presentation_theme(self):
        palette = spectator_palette(self.presentation_mode)
        dark = self.presentation_mode == "dark"
        self.mode_button.setText("Braun-Creme-Modus" if dark else "Dunkler Präsentationsmodus")
        self.root.setStyleSheet(f"background: {palette['background']}; color: {palette['text']};")
        self.title.setStyleSheet(
            f"font-size: 42px; font-weight: 850; color: {palette['winner']}; padding: 8px;"
        )
        for card in self.live_cards:
            card.setStyleSheet(
                f"font-size: 29px; font-weight: 780; padding: 22px; color: {palette['text']}; "
                f"background: {palette['panel']}; border: 3px solid {palette['accent']}; border-radius: 16px;"
            )
        self.tree.setStyleSheet(
            f"font-size: 23px; line-height: 1.4; color: {palette['text']}; padding: 12px; "
            f"background: {palette['panel']}; border: 1px solid {palette['border']}; border-radius: 14px;"
        )
        self.podium.setStyleSheet(
            f"font-size: 28px; font-weight: 800; color: {palette['winner']}; padding: 12px;"
        )
        footer_style = f"font-size: 18px; font-weight: 650; color: {palette['muted']}; padding: 4px;"
        self.progress.setStyleSheet(footer_style)
        self.clock.setStyleSheet(footer_style)
        self.setStyleSheet(f"""
            QPushButton {{
                background: {palette['panel']}; color: {palette['text']};
                border: 1px solid {palette['border']}; border-radius: 9px;
                padding: 8px 14px; font-weight: 700;
            }}
            QPushButton:hover {{ border: 2px solid {palette['accent']}; }}
            QPushButton:pressed {{ background: {palette['accent']}; }}
        """)

    def update_clock(self):
        self.clock.setText(datetime.now().strftime("%H:%M Uhr"))

    def refresh(self):
        self.title.setText(f"🏓  {self.tournament.name}")
        active_matches = self.tournament.active_matches
        if active_matches:
            for index, card in enumerate(self.live_cards):
                if index < len(active_matches):
                    match = active_matches[index]
                    card.setText(
                        f"TISCH {index + 1}  •  LÄUFT JETZT\n{match.title}\n\n"
                        f"{match.home}\ngegen\n{match.away}"
                    )
                    card.show()
                else:
                    card.hide()
        else:
            self.live_cards[0].setText(
                "Keine laufenden Spiele ausgewählt\n\n"
                "In der Turnierleitung bei einem Spiel\n‚Auf Beamer‘ aktivieren."
            )
            self.live_cards[0].show()
            for card in self.live_cards[1:]:
                card.hide()

        def line(key: str) -> str:
            m = self.tournament.matches[key]
            names = f"{m.home or 'offen'} – {m.away or 'offen'}"
            return f"{m.title}: {names}" + (f"   |   {m.match_score}" if m.complete else "")

        self.tree.setText("\n\n".join(line(k) for k in ("VF1", "VF2", "VF3", "VF4", "HF1", "HF2", "P3", "F")))
        first, second, third = self.tournament.placements
        self.podium.setText(f"🥇 {first or '—'}     🥈 {second or '—'}     🥉 {third or '—'}")
        finished = sum(1 for match in self.tournament.matches.values() if match.complete)
        total = len(self.tournament.matches)
        self.progress.setText(f"Turnierfortschritt: {finished} von {total} Spielen abgeschlossen")
        self.update_clock()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FTS KO Edition 1.6 – Live-Spiele auf Beamer")
        if APP_ICON.exists():
            self.setWindowIcon(QIcon(str(APP_ICON)))
        screen = QApplication.primaryScreen()
        if screen:
            geometry = screen.availableGeometry()
            width, height = recommended_window_dimensions(geometry.width(), geometry.height())
            self.resize(width, height)
        else:
            self.resize(1120, 720)
        self.setMinimumSize(980, 650)
        self.tournament = self._load_autosave()
        self.history: list[dict] = []
        self.spectator: SpectatorWindow | None = None
        self.name_edit = QLineEdit(self.tournament.name)
        self.player_edits = [QLineEdit() for _ in range(8)]
        for edit, name in zip(self.player_edits, self.tournament.participants):
            edit.setText(name)
        self.boxes: dict[str, MatchBox] = {}
        self._build(); self.refresh()

    def _build(self):
        root = QWidget(); outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("FTS KO 1.6")
        title.setStyleSheet(f"font-size: 22px; font-weight: 800; color: {BROWN};")
        header.addWidget(title)
        header.addWidget(QLabel("Turnier:"))
        self.name_edit.setMinimumHeight(34)
        header.addWidget(self.name_edit, 1)
        spectator = QPushButton("Beamer")
        spectator.setObjectName("beamerButton")
        spectator.setMinimumHeight(34)
        spectator.clicked.connect(self.show_spectator)
        header.addWidget(spectator)
        outer.addLayout(header)

        self.next_button = QPushButton("▶ Nächstes offenes Spiel")
        self.next_button.setMinimumHeight(46)
        self.next_button.setObjectName("primaryButton")
        self.next_button.clicked.connect(self.enter_next_result)
        outer.addWidget(self.next_button)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Left: participant setup, always visible and scrollable on smaller heights.
        sidebar = QWidget(); side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 6, 0); side.setSpacing(7)
        participant_group = QGroupBox("Viertelfinalteilnehmer")
        pgrid = QGridLayout(participant_group)
        pgrid.setContentsMargins(8, 12, 8, 8); pgrid.setSpacing(5)
        for i, edit in enumerate(self.player_edits):
            edit.setMinimumHeight(30)
            edit.setPlaceholderText(f"Spieler {i + 1}")
            pgrid.addWidget(QLabel(f"{i + 1}."), i, 0)
            pgrid.addWidget(edit, i, 1)
        side.addWidget(participant_group)
        save_players = QPushButton("Viertelfinale übernehmen")
        save_players.setMinimumHeight(38)
        save_players.clicked.connect(self.apply_players)
        side.addWidget(save_players)
        self.podium = QLabel()
        self.podium.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.podium.setWordWrap(True)
        self.podium.setMinimumHeight(90)
        self.podium.setObjectName("podiumCard")
        side.addWidget(self.podium)
        side.addStretch()
        splitter.addWidget(sidebar)

        # Center: bracket in three compact columns.
        bracket = QWidget(); grid = QGridLayout(bracket)
        grid.setContentsMargins(6, 0, 6, 0); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(6)
        headings = [("Viertelfinale", 0), ("Halbfinale", 1), ("Finale", 2)]
        for text, col in headings:
            label = QLabel(text); label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(f"font-size: 15px; font-weight: 800; color: {BROWN};")
            grid.addWidget(label, 0, col)
        for i, key in enumerate(("VF1", "VF2", "VF3", "VF4")):
            box = MatchBox(key, self.enter_result, self.clear_result, self.toggle_active_match); self.boxes[key] = box
            grid.addWidget(box, i + 1, 0)
        for row, key in ((1, "HF1"), (3, "HF2")):
            box = MatchBox(key, self.enter_result, self.clear_result, self.toggle_active_match); self.boxes[key] = box
            grid.addWidget(box, row + 1, 1)
        for row, key in ((1, "F"), (3, "P3")):
            box = MatchBox(key, self.enter_result, self.clear_result, self.toggle_active_match); self.boxes[key] = box
            grid.addWidget(box, row + 1, 2)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1); grid.setColumnStretch(2, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(bracket)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 850])
        outer.addWidget(splitter, 1)
        self.setCentralWidget(root)

        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {CREAM}; color: {BROWN_DARK}; font-size: 12px; }}
            QMenuBar, QMenu {{ background: {IVORY}; color: {BROWN_DARK}; }}
            QMenuBar::item:selected, QMenu::item:selected {{ background: {SAND_LIGHT}; }}
            QLineEdit, QSpinBox {{ background: {IVORY}; color: {BROWN_DARK}; border: 1px solid {SAND}; border-radius: 7px; padding: 5px 8px; selection-background-color: {GOLD}; }}
            QLineEdit:focus, QSpinBox:focus {{ border: 2px solid {BROWN_MID}; }}
            QPushButton {{ background: {IVORY}; color: {BROWN}; border: 1px solid {SAND}; border-radius: 7px; padding: 6px 10px; font-weight: 650; }}
            QPushButton:hover {{ background: {SAND_LIGHT}; border-color: {GOLD}; }}
            QPushButton:pressed {{ background: {SAND}; }}
            QPushButton:disabled {{ color: #A99A89; background: #EFE8DD; border-color: #DED3C5; }}
            QPushButton#primaryButton {{ font-size: 16px; font-weight: 750; background: {BROWN}; color: {IVORY}; border: 1px solid {BROWN_DARK}; border-radius: 9px; }}
            QPushButton#primaryButton:hover {{ background: {BROWN_MID}; }}
            QPushButton#beamerButton {{ background: {BROWN}; color: #FFFFFF; border: 1px solid {BROWN_DARK}; font-weight: 750; }}
            QPushButton#beamerButton:hover {{ background: {BROWN_MID}; color: #FFFFFF; }}
            QPushButton#beamerButton:pressed {{ background: {BROWN_DARK}; color: #FFFFFF; }}
            QPushButton#liveButton {{ background: {IVORY}; color: {BROWN}; border: 1px solid {GOLD}; font-weight: 750; }}
            QPushButton#liveButton:checked {{ background: {SUCCESS}; color: #FFFFFF; border: 1px solid #49613F; }}
            QPushButton#liveButton:checked:hover {{ background: #6D8A61; color: #FFFFFF; }}
            QLabel#podiumCard {{ font-size: 15px; font-weight: 700; padding: 10px; background: {IVORY}; color: {BROWN}; border: 1px solid {SAND}; border-radius: 9px; }}
            QGroupBox {{ background: {IVORY}; color: {BROWN}; font-weight: 700; border: 1px solid {SAND}; border-radius: 9px; margin-top: 10px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {BROWN}; background: {CREAM}; }}
            MatchBox {{ background: {IVORY}; border: 1px solid {SAND}; border-radius: 9px; margin-top: 10px; }}
            MatchBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {BROWN}; background: {CREAM}; font-weight: 750; }}
            MatchBox[matchState="highlighted"] {{ background: #FFF8E8; border: 3px solid {GOLD}; }}
            MatchBox[matchState="highlighted"]::title {{ color: {BROWN_DARK}; }}
            QScrollArea {{ border: none; background: {CREAM}; }}
            QScrollArea > QWidget > QWidget {{ background: {CREAM}; }}
            QSplitter::handle {{ background: {SAND_LIGHT}; width: 2px; }}
            QDialog#resultDialog {{ background: {CREAM}; }}
            QDialogButtonBox QPushButton {{ min-width: 110px; min-height: 32px; }}
        """)

        menu = self.menuBar().addMenu("Datei")
        new = QAction("Neues KO-Turnier", self); new.triggered.connect(self.new_tournament)
        open_a = QAction("Öffnen …", self); open_a.triggered.connect(self.open_file)
        save_a = QAction("Speichern unter …", self); save_a.triggered.connect(self.save_file)
        undo = QAction("Letzte Änderung rückgängig", self); undo.setShortcut("Ctrl+Z"); undo.triggered.connect(self.undo)
        menu.addActions([new, open_a, save_a, undo])

    def snapshot(self): return self.tournament.to_dict()
    def push_history(self): self.history.append(self.snapshot()); self.history = self.history[-30:]

    def apply_players(self):
        if any(m.complete for m in self.tournament.matches.values()):
            if QMessageBox.question(self, "Viertelfinale ändern", "Bereits erfasste Ergebnisse werden verworfen. Fortfahren?") != QMessageBox.StandardButton.Yes:
                return
        try:
            self.push_history(); self.tournament.name = self.name_edit.text().strip() or "KO-Turnier"
            self.tournament.set_participants([e.text() for e in self.player_edits])
            self.persist(); self.refresh()
        except Exception as error:
            if self.history: self.history.pop()
            QMessageBox.warning(self, "FTS KO", str(error))

    def enter_next_result(self):
        match = self.tournament.next_open_match()
        if match is None:
            QMessageBox.information(self, "FTS KO", "Alle verfügbaren Spiele sind abgeschlossen.")
            return
        self.enter_result(match.key)

    def toggle_active_match(self, key: str, active: bool):
        try:
            self.tournament.set_match_active(key, active)
            self.persist()
            self.refresh()
        except Exception as error:
            QMessageBox.warning(self, "FTS KO", str(error))
            self.refresh()

    def enter_result(self, key: str):
        match = self.tournament.matches[key]
        if match.complete:
            if QMessageBox.question(self, "Ergebnis überschreiben", f"Das Ergebnis von {match.title} ist bereits gespeichert. Wirklich ändern?") != QMessageBox.StandardButton.Yes:
                return
        dialog = SetDialog(match.home, match.away, match.set_scores, self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try:
            scores = dialog.scores(); self.push_history(); self.tournament.record(key, scores)
            self.persist(); self.refresh()
        except Exception as error:
            if self.history: self.history.pop()
            QMessageBox.warning(self, "FTS KO", str(error))

    def clear_result(self, key: str):
        match = self.tournament.matches[key]
        if QMessageBox.question(self, "Ergebnis löschen", f"Ergebnis von {match.title} wirklich löschen? Nachfolgende Ergebnisse können ebenfalls entfallen.") != QMessageBox.StandardButton.Yes:
            return
        self.push_history(); self.tournament.clear_result(key); self.persist(); self.refresh()

    def undo(self):
        if not self.history:
            QMessageBox.information(self, "FTS KO", "Keine Änderung zum Rückgängigmachen vorhanden."); return
        self.tournament = KOTournament.from_dict(self.history.pop())
        for edit, name in zip(self.player_edits, self.tournament.participants): edit.setText(name)
        self.name_edit.setText(self.tournament.name); self.persist(); self.refresh()

    def refresh(self):
        next_match = self.tournament.next_open_match()
        for key, box in self.boxes.items():
            box.update_match(
                self.tournament.matches[key],
                bool(next_match and key == next_match.key),
                key in self.tournament.active_match_keys,
            )
        if next_match:
            self.next_button.setText(f"▶ {next_match.title}: {next_match.home} gegen {next_match.away}")
            self.next_button.setEnabled(True)
        else:
            self.next_button.setText("✓ Alle verfügbaren Spiele abgeschlossen"); self.next_button.setEnabled(False)
        first, second, third = self.tournament.placements
        self.podium.setText(f"🥇 {first or '—'}\n🥈 {second or '—'}\n🥉 {third or '—'}")
        if self.spectator:
            self.spectator.tournament = self.tournament; self.spectator.refresh()

    def persist(self):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        if AUTOSAVE.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            shutil.copy2(AUTOSAVE, BACKUP_DIR / f"autosave_{stamp}.json")
            backups = sorted(BACKUP_DIR.glob("autosave_*.json"))
            for old in backups[:-20]: old.unlink(missing_ok=True)
        self.tournament.save(AUTOSAVE)

    def _load_autosave(self):
        try:
            if AUTOSAVE.exists(): return KOTournament.load(AUTOSAVE)
        except Exception:
            pass
        return KOTournament()

    def show_spectator(self):
        if self.spectator is None:
            self.spectator = SpectatorWindow(self.tournament)
        self.spectator.show(); self.spectator.showFullScreen(); self.spectator.raise_()

    def new_tournament(self):
        if QMessageBox.question(self, "Neues Turnier", "Aktuelles KO-Turnier zurücksetzen?") != QMessageBox.StandardButton.Yes: return
        self.push_history(); self.tournament = KOTournament()
        for edit in self.player_edits: edit.clear()
        self.name_edit.setText(self.tournament.name); self.persist(); self.refresh()

    def save_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "KO-Turnier speichern", "fts_ko_turnier.json", "FTS KO (*.json)")
        if path:
            self.tournament.name = self.name_edit.text().strip() or self.tournament.name; self.tournament.save(path)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "KO-Turnier öffnen", "", "FTS KO (*.json)")
        if not path: return
        try:
            self.push_history(); self.tournament = KOTournament.load(path); self.name_edit.setText(self.tournament.name)
            for edit, name in zip(self.player_edits, self.tournament.participants): edit.setText(name)
            self.persist(); self.refresh()
        except Exception as error:
            QMessageBox.warning(self, "FTS KO", str(error))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("FTS KO Edition")
    app.setApplicationDisplayName("FTS KO Edition")
    if APP_ICON.exists():
        app.setWindowIcon(QIcon(str(APP_ICON)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
