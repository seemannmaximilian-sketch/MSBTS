from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QInputDialog, QFileDialog, QMenu,
    QVBoxLayout, QWidget,
)

from fts.domain import LiveMatchStatus, ValidationError
from fts.engine import TournamentEngine
from fts.tournament_health import inspect_tournament
from fts.scorekeepers import build_scorekeeper_assignments


from fts.ko_layout import notebook_layout_columns
from fts.gui.live_center_model import FlowWarning, LiveMatchRef, build_live_snapshot, knockout_phase_label
from fts.gui.result_entry import evaluate_set_entry, validated_set_scores


class QuickResultDialog(QDialog):
    def __init__(self, match_ref: LiveMatchRef, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.match_ref = match_ref
        self.setWindowTitle("Ergebnis erfassen")
        self.setObjectName("professionalDialog")
        self.setMinimumWidth(500)
        self._rows: list[tuple[QSpinBox, QSpinBox]] = []

        form = QFormLayout(self)
        heading = QLabel(f"{match_ref.home_name} gegen {match_ref.away_name}")
        heading.setStyleSheet("font-size: 17px; font-weight: 700;")
        form.addRow(heading)

        name_row = QWidget()
        name_layout = QHBoxLayout(name_row)
        name_layout.setContentsMargins(0, 0, 0, 0)
        home_name = QLabel(match_ref.home_name)
        away_name = QLabel(match_ref.away_name)
        home_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        away_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_layout.addWidget(home_name)
        name_layout.addWidget(QLabel(":"))
        name_layout.addWidget(away_name)
        form.addRow("", name_row)

        winner_box = QWidget()
        winner_layout = QHBoxLayout(winner_box)
        winner_layout.setContentsMargins(0, 0, 0, 0)
        self.winner_group = QButtonGroup(self)
        self.winner_group.setExclusive(True)
        self.home_winner = QPushButton(match_ref.home_name)
        self.away_winner = QPushButton(match_ref.away_name)
        for button in (self.home_winner, self.away_winner):
            button.setCheckable(True)
            button.setMinimumHeight(42)
            button.clicked.connect(self._update_state)
            self.winner_group.addButton(button)
            winner_layout.addWidget(button)
        form.addRow("Sieger", winner_box)

        for number in range(1, 4):
            home = QSpinBox()
            away = QSpinBox()
            for editor in (home, away):
                editor.setRange(0, 99)
                editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
                editor.setSpecialValueText("–")
                editor.setMinimumWidth(82)
                editor.valueChanged.connect(self._update_state)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(home)
            row_layout.addWidget(QLabel(":"))
            row_layout.addWidget(away)
            form.addRow(f"Satz {number}", row)
            self._rows.append((home, away))

        hint = QLabel(
            "Punkte eingeben, zum Beispiel 21:17 oder 24:22. Nicht gespielte "
            "Sätze bleiben leer. Mit Tab wechselst du zum nächsten Feld."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedText")
        form.addRow(hint)

        self.summary = QLabel("0:0")
        self.summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary.setStyleSheet("font-size: 24px; font-weight: 800;")
        form.addRow("Spielergebnis", self.summary)

        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form.addRow("", self.feedback)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setObjectName("dialogPrimaryButton")
        self.save_button.setText("Ergebnis speichern")
        cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            cancel_button.setObjectName("dialogSecondaryButton")
            cancel_button.setText("Abbrechen")
        self.save_button.setEnabled(False)
        self.buttons.accepted.connect(self._validate)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        if match_ref.match.result is not None:
            for index, score in enumerate(match_ref.match.result.set_scores[:len(self._rows)]):
                self._rows[index][0].setValue(score[0])
                self._rows[index][1].setValue(score[1])
            if match_ref.match.result.home_score > match_ref.match.result.away_score:
                self.home_winner.setChecked(True)
            else:
                self.away_winner.setChecked(True)
        self._update_state()
        self._rows[0][0].setFocus()

    def _raw_rows(self) -> list[tuple[int, int]]:
        return [(home.value(), away.value()) for home, away in self._rows]

    def _update_state(self) -> None:
        state = evaluate_set_entry(self._raw_rows(), self.match_ref.home_name, self.match_ref.away_name)
        self.summary.setText(state.match_score)
        self.feedback.setText(state.message)
        self.feedback.setProperty("valid", state.valid)
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)
        declared = 1 if self.home_winner.isChecked() else 2 if self.away_winner.isChecked() else 0
        calculated = 1 if state.home_sets > state.away_sets else 2 if state.away_sets > state.home_sets else 0
        winner_matches = bool(declared and state.complete and declared == calculated)
        if state.complete and declared and declared != calculated:
            self.feedback.setText("Der ausgewählte Sieger passt nicht zu den Satzständen.")
        elif state.complete and not declared:
            self.feedback.setText("Bitte zuerst den Sieger auswählen.")
        self.save_button.setEnabled(state.complete and state.valid and winner_matches)

        # Once a player has two sets, unused later rows are locked. If the
        # result becomes incomplete again they are immediately editable.
        decided = False
        home_sets = away_sets = 0
        for index, (home, away) in enumerate(self._rows):
            score = (home.value(), away.value())
            if decided and score == (0, 0):
                home.setEnabled(False)
                away.setEnabled(False)
                continue
            home.setEnabled(True)
            away.setEnabled(True)
            if score != (0, 0):
                home_sets += int(score[0] > score[1])
                away_sets += int(score[1] > score[0])
                decided = max(home_sets, away_sets) == 2

    def _validate(self) -> None:
        try:
            validated_set_scores(self._raw_rows())
        except ValidationError as error:
            QMessageBox.warning(self, "FTS", str(error))
            return
        self.accept()

    def values(self) -> list[tuple[int, int]]:
        return validated_set_scores(self._raw_rows())


class TableCard(QFrame):
    result_requested = Signal(object)
    status_requested = Signal(object, object)
    pause_requested = Signal(object)

    def __init__(self, table_number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.table_number = table_number
        self.match_ref: LiveMatchRef | None = None
        self.setObjectName("liveTableCard")
        self.setProperty("tableNumber", table_number)
        self.title = QLabel(f"Feld {table_number}")
        self.title.setObjectName("liveTableTitle")
        self.time = QLabel("--:--")
        self.time.setObjectName("liveTableTime")
        self.time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.context = QLabel("Kein Spiel geplant")
        self.context.setObjectName("mutedText")
        self.players = QLabel("Feld frei")
        self.players.setObjectName("liveTablePlayers")
        self.players.setWordWrap(True)
        self.home_player = QLabel("Feld frei")
        self.home_player.setObjectName("livePlayerChip")
        self.home_player.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.home_player.setWordWrap(True)
        self.vs_badge = QLabel("VS")
        self.vs_badge.setObjectName("liveVsBadge")
        self.vs_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.vs_badge.setFixedWidth(38)
        self.away_player = QLabel("")
        self.away_player.setObjectName("livePlayerChip")
        self.away_player.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.away_player.setWordWrap(True)
        self.scorer = QLabel("Schiedsrichter: noch nicht eingeteilt")
        self.scorer.setObjectName("liveTableScorer")
        self.scorer.setWordWrap(True)
        self.status = QLabel("FREI")
        self.status.setObjectName("liveStatus")
        self.action_button = QPushButton("Spiel vorbereiten")
        self.action_button.setObjectName("liveTablePrimaryAction")
        self.action_button.clicked.connect(self._advance_status)
        self.action_button.setEnabled(False)
        self.action_button.setVisible(False)
        self.pause_button = QPushButton("Spiel pausieren")
        self.pause_button.setObjectName("liveTableSecondaryAction")
        self.pause_button.setProperty("secondary", True)
        self.pause_button.clicked.connect(self._pause)
        self.pause_button.setEnabled(False)
        self.pause_button.setVisible(False)
        self.button = QPushButton("Ergebnis eingeben")
        self.button.setObjectName("liveTableResultButton")
        self.button.clicked.connect(self._emit)
        self.button.setEnabled(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(3)
        self.setMinimumHeight(164)
        self.setMaximumHeight(184)
        top = QHBoxLayout()
        top.addWidget(self.title)
        top.addStretch()
        top.addWidget(self.time)
        layout.addLayout(top)
        layout.addWidget(self.context)
        self.players.setVisible(False)
        versus_row = QHBoxLayout()
        versus_row.setContentsMargins(0, 2, 0, 2)
        versus_row.setSpacing(7)
        versus_row.addWidget(self.home_player, 1)
        versus_row.addWidget(self.vs_badge)
        versus_row.addWidget(self.away_player, 1)
        layout.addLayout(versus_row)
        layout.addWidget(self.scorer)
        layout.addStretch()
        footer = QHBoxLayout()
        footer.addWidget(self.status)
        footer.addStretch()
        footer.addWidget(self.button)
        layout.addLayout(footer)
        layout.addWidget(self.action_button)
        layout.addWidget(self.pause_button)

    def set_match(self, match_ref: LiveMatchRef | None, scorer_name: str = "") -> None:
        self.match_ref = match_ref
        if match_ref is None:
            self.setProperty("matchState", "free")
            self.style().unpolish(self)
            self.style().polish(self)
            self.time.setText("--:--")
            self.context.setText("Kein offenes Spiel")
            self.players.setText("Feld frei")
            self.home_player.setText("Feld frei")
            self.away_player.setText("")
            self.away_player.setVisible(False)
            self.vs_badge.setVisible(False)
            self.scorer.setText("Schiedsrichter: —")
            self.status.setText("FREI")
            self.action_button.setEnabled(False)
            self.pause_button.setEnabled(False)
            self.button.setEnabled(False)
            return
        self.time.setText(match_ref.scheduled_time)
        self.context.setText(f"{match_ref.phase_name} · {match_ref.group_name}")
        self.players.setText(f"{match_ref.home_name}\ngegen\n{match_ref.away_name}")
        self.home_player.setText(match_ref.home_name)
        self.away_player.setText(match_ref.away_name)
        self.away_player.setVisible(True)
        self.vs_badge.setVisible(True)
        self.scorer.setText(f"Schiedsrichter: {scorer_name or 'noch nicht eingeteilt'}")
        state = match_ref.match.live_status
        self.setProperty("matchState", state.value)
        self.style().unpolish(self)
        self.style().polish(self)
        self.status.setText(state.label)
        self.action_button.setEnabled(state is not LiveMatchStatus.RESULT_PENDING)
        self.pause_button.setEnabled(state in {LiveMatchStatus.PREPARING, LiveMatchStatus.RUNNING, LiveMatchStatus.RESULT_PENDING})
        # Results may be entered directly for every unfinished scheduled match.
        # The explicit prepare/start/end controls remain available, but are no
        # longer mandatory for ordinary tournament operation.
        self.button.setEnabled(state is not LiveMatchStatus.FINISHED)
        self.action_button.setText({
            LiveMatchStatus.PLANNED: "Spiel vorbereiten",
            LiveMatchStatus.PREPARING: "Spiel starten",
            LiveMatchStatus.RUNNING: "Spiel beenden",
            LiveMatchStatus.RESULT_PENDING: "Ergebnis fehlt",
            LiveMatchStatus.FINISHED: "Beendet",
        }[state])

    def _advance_status(self) -> None:
        if self.match_ref is None:
            return
        current = self.match_ref.match.live_status
        target = {
            LiveMatchStatus.PLANNED: LiveMatchStatus.PREPARING,
            LiveMatchStatus.PREPARING: LiveMatchStatus.RUNNING,
            LiveMatchStatus.RUNNING: LiveMatchStatus.RESULT_PENDING,
        }.get(current)
        if target is not None:
            self.status_requested.emit(self.match_ref, target)

    def _pause(self) -> None:
        if self.match_ref is not None:
            self.pause_requested.emit(self.match_ref)

    def _emit(self) -> None:
        if self.match_ref is not None:
            self.result_requested.emit(self.match_ref)


class LiveCenterPage(QWidget):
    """Kompakter Leitstand fuer den Turniertag auf 13-Zoll-Displays."""

    def __init__(self, service: TournamentEngine, on_saved, on_continue=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("liveCenterPage")
        # STARTFIX 139: apply the dark Leitstand theme directly to the page.
        # This avoids the unstable runtime re-polish from STARTFIX 138 and
        # does not depend on an ancestor dynamic-property selector.
        self.setStyleSheet(r"""
QWidget#liveCenterPage {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #090319, stop:0.50 #140925, stop:1 #070214);
    color:#F8F3FF;
}
QWidget#liveCenterContent, QScrollArea#liveCenterScroll,
QScrollArea#liveCenterScroll > QWidget, QScrollArea#liveCenterScroll > QWidget > QWidget {
    background:transparent; color:#F8F3FF; border:none;
}
QFrame#liveStatusStrip, QFrame#liveOperatorFocus, QFrame#liveSummaryCard {
    background:rgba(22,10,43,0.98); border:1px solid #7D43BC; border-radius:12px;
}
QFrame#liveOperatorFocus { border-color:#8147C0; }
QLabel#liveOperatorFocusTitle { color:#E45CFF; font-size:13px; font-weight:900; }
QFrame#liveStatusStrip QLabel, QFrame#liveOperatorFocus QLabel,
QFrame#liveSummaryCard QLabel, QLabel#sectionEyebrow {
    color:#F0D2B7; background:transparent;
}
QFrame#liveTableCard {
    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #100824,stop:.55 #1A0B35,stop:1 #25104A);
    border:2px solid #8147C0; border-radius:13px;
}
QFrame#liveTableCard[matchState="free"] { background:#120724; border-color:#6F35A8; }
QFrame#liveTableCard[tableIndex="0"] { border-color:#9B42FF; }
QFrame#liveTableCard[tableIndex="1"] { border-color:#B85CFF; }
QFrame#liveTableCard[tableIndex="2"] { border-color:#7D43BC; }
QFrame#liveTableCard QLabel, QLabel#liveTableTitle { color:#FFF7F0; background:transparent; }
QLabel#liveTableTime {
    background:#211047; color:#F8F3FF; border:1px solid #7D43BC; border-radius:7px; padding:2px 7px;
}
QLabel#livePlayerChip {
    background:#180A31; color:#FFF8FF; border:1px solid #8147C0; border-radius:8px; padding:5px;
}
QLabel#liveVsBadge {
    background:#8D2CFF; color:#FFFFFF; border:1px solid #B85CFF; border-radius:7px;
}
QLabel#liveTableScorer {
    background:#140925; color:#E9D9FF; border:1px solid #7D43BC; border-radius:7px; padding:3px 7px;
}
QPushButton#liveTableResultButton, QPushButton#liveTablePrimaryAction,
QPushButton#liveOperatorFocusAction {
    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8D2CFF,stop:1 #6517C9);
    color:#FFFFFF; border:1px solid #B85CFF; border-radius:8px; font-weight:900;
}
QPushButton#liveTableSecondaryAction {
    background:#211047; color:#E9D9FF; border:1px solid #7D43BC; border-radius:8px;
}
QProgressBar { background:#120724; border:1px solid #6F35A8; color:#F4EEFF; }
QProgressBar::chunk { background:#9B42FF; border-radius:4px; }
QScrollBar:vertical { background:#120724; }
QScrollBar::handle:vertical { background:#7440B0; }
/* STARTFIX 156: final dark-brown polish for operator controls and phase hero. */
QLabel#livePhaseLabel, QLabel#liveClock {
    background:#140925; color:#E9DEFA; border:1px solid #7D43BC; border-radius:8px; padding:6px 10px;
}
QPushButton#liveContinueButton, QPushButton#liveToolsButton {
    background:#21103B; color:#FFF2E6; border:1px solid #7132A8; border-radius:9px;
    min-height:34px; padding:0 14px; font-weight:900;
}
QPushButton#liveContinueButton { background:#8D2CFF; border-color:#B85CFF; }
QPushButton#liveContinueButton:hover { background:#A94CFF; }
QPushButton#liveToolsButton:hover { background:#281047; border-color:#B85CFF; }
QFrame#livePhaseHero, QFrame#livePhaseHero QLabel { background:transparent; }
QFrame#livePhaseHero { background:#140925; border:1px solid #8D2CFF; border-radius:14px; }
QLabel#livePhaseHeroIcon { background:#100824; color:#E45CFF; border:1px solid #7D43BC; border-radius:8px; }
QLabel#livePhaseHeroEyebrow { background:transparent; color:#D14BFF; }
QLabel#livePhaseHeroTitle { background:transparent; color:#F8F3FF; }
QLabel#livePhaseHeroDetail { background:transparent; color:#CDBBE8; }
QFrame#liveTableCard, QFrame#liveTableCard QWidget { background:transparent; }
QFrame#liveTableCard { background:#100824; }
QFrame#liveTableCard QLabel { background:transparent; }
QMenu { background:#160A2B; color:#F4EEFF; border:1px solid #7337B4; }
QMenu::item:selected { background:#8D2CFF; color:#FFFFFF; }
""")
        self.service = service
        self.on_saved = on_saved
        self.on_continue = on_continue
        self._snapshot = None
        self._preferred_phase_id = None
        self._focus_action_callback = None

        self.title = QLabel("Turniertag – Leitstand")
        self.title.setVisible(False)
        self.phase_label = QLabel("Aktuelle Phase wird ermittelt")
        self.phase_label.setObjectName("livePhaseLabel")
        self.phase_label.setStyleSheet("font-size: 13px; font-weight: 700;")

        # STARTFIX 122: unmistakable phase hero for the tournament operator.
        self.phase_hero = QFrame()
        self.phase_hero.setObjectName("livePhaseHero")
        self.phase_hero.setFixedHeight(86)
        self.phase_hero.setStyleSheet(
            "QFrame#livePhaseHero { background: #160A2B; border: 1px solid #B85CFF; "
            "border-radius: 14px; }"
        )
        phase_hero_layout = QHBoxLayout(self.phase_hero)
        phase_hero_layout.setContentsMargins(14, 4, 14, 4)
        phase_hero_layout.setSpacing(10)
        self.phase_hero_icon = QLabel("🏆")
        self.phase_hero_icon.setObjectName("livePhaseHeroIcon")
        self.phase_hero_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.phase_hero_icon.setFixedWidth(38)
        self.phase_hero_icon.setStyleSheet("font-size: 22px; color: #E45CFF;")
        phase_hero_layout.addWidget(self.phase_hero_icon)
        phase_text = QVBoxLayout()
        phase_text.setSpacing(0)
        eyebrow = QLabel("AKTUELLE TURNIERPHASE")
        eyebrow.setObjectName("livePhaseHeroEyebrow")
        eyebrow.setStyleSheet("font-size: 11px; font-weight: 900; color: #D14BFF; letter-spacing: 1px;")
        self.phase_hero_title = QLabel("Gruppenphase")
        self.phase_hero_title.setObjectName("livePhaseHeroTitle")
        self.phase_hero_title.setStyleSheet("font-size: 29px; font-weight: 950; color: #F8F3FF; background: transparent;")
        self.phase_hero_detail = QLabel("Die aktuelle Turnierphase läuft.")
        self.phase_hero_detail.setObjectName("livePhaseHeroDetail")
        self.phase_hero_detail.setStyleSheet("font-size: 12px; font-weight: 750; color: #CDBBE8; background: transparent;")
        phase_text.addWidget(eyebrow)
        phase_text.addWidget(self.phase_hero_title)
        phase_text.addWidget(self.phase_hero_detail)
        phase_hero_layout.addLayout(phase_text, 1)
        self.clock = QLabel()
        self.clock.setObjectName("liveClock")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.clock.setStyleSheet("font-size: 16px; font-weight: 700;")

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        self.progress_label = QLabel()
        self.progress_label.setStyleSheet("font-size: 13px; font-weight: 700;")

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title_box.addWidget(self.title)
        title_box.addWidget(self.phase_label)
        header.addLayout(title_box)
        header.addStretch()
        self.continue_button = QPushButton("Nächster Turnierschritt")
        self.continue_button.setObjectName("liveContinueButton")
        self.continue_button.setMinimumHeight(34)
        self.continue_button.clicked.connect(self._continue_tournament)
        header.addWidget(self.continue_button)
        self.save_button = QPushButton("Sichern")
        self.save_button.setVisible(False)
        self.save_button.clicked.connect(self.save_now)
        tools_button = QPushButton("Mehr …")
        tools_button.setObjectName("liveToolsButton")
        tools_button.setProperty("secondary", True)
        tools_menu = QMenu(tools_button)
        tools_menu.addAction("Jetzt sichern", self.save_now)
        tools_menu.addSeparator()
        tools_menu.addAction("Sicherungen verwalten", self.show_backup_manager)
        tools_menu.addAction("Protokoll", self.show_operation_log)
        tools_menu.addAction("Zeitplan anpassen", self.adjust_schedule)
        tools_button.setMenu(tools_menu)
        header.addWidget(tools_button)
        header.addSpacing(8)
        header.addWidget(self.clock)

        # Startfix 8: a quiet one-line tournament status instead of a second row of cards.
        summary = QFrame()
        summary.setObjectName("liveStatusStrip")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(0, 2, 0, 4)
        summary_layout.setSpacing(10)

        self.completed_metric = QLabel("0 / 0")
        self.open_metric = QLabel("0 offen")
        self.running_metric = QLabel("0 aktiv")
        self.next_metric = QLabel("Kein nächstes Spiel")
        for value in (self.completed_metric, self.open_metric, self.running_metric, self.next_metric):
            value.setObjectName("liveStatusItem")
            summary_layout.addWidget(value)
        summary_layout.addStretch()
        self.progress.setMaximumWidth(150)
        self.progress_label.setObjectName("liveProgressLabel")
        summary_layout.addWidget(self.progress_label)
        summary_layout.addWidget(self.progress)

        # Startfix 33: one operator focus instead of several competing actions.
        self.focus_strip = QFrame()
        self.focus_strip.setObjectName("liveOperatorFocus")
        self.focus_strip.setProperty("state", "idle")
        focus_layout = QHBoxLayout(self.focus_strip)
        focus_layout.setContentsMargins(10, 4, 10, 4)
        focus_layout.setSpacing(8)
        focus_text = QVBoxLayout()
        focus_text.setContentsMargins(0, 0, 0, 0)
        focus_text.setSpacing(1)
        self.focus_title = QLabel("Turniertag bereit")
        self.focus_title.setObjectName("liveOperatorFocusTitle")
        self.focus_detail = QLabel("Der Leitstand ermittelt die wichtigste nächste Aktion.")
        self.focus_detail.setObjectName("mutedText")
        self.focus_detail.setWordWrap(True)
        focus_text.addWidget(self.focus_title)
        focus_text.addWidget(self.focus_detail)
        focus_layout.addLayout(focus_text, 1)
        self.focus_action = QPushButton("Aktualisieren")
        self.focus_action.setObjectName("liveOperatorFocusAction")
        self.focus_action.setMinimumHeight(32)
        focus_layout.addWidget(self.focus_action)

        self.table_grid = QGridLayout()
        self.table_grid.setContentsMargins(0, 0, 0, 0)
        self.table_grid.setHorizontalSpacing(10)
        self.table_grid.setVerticalSpacing(10)
        self.table_cards: list[TableCard] = []
        self._rebuild_table_cards()

        tables = QWidget()
        tables.setLayout(self.table_grid)

        self.next_game_card, self.next_game_layout = self._section_card("Nächste Begegnung")
        self.queue_card, self.queue_layout = self._section_card("Warteschlange")
        # Legacy section title retained for regression compatibility: "Ergebnis / aktives Spiel"
        self.result_card, self.result_layout = self._section_card("Aktive Spiele & Ergebnisse")

        # The live working area has only two primary columns. The queue is secondary
        # and sits below them instead of competing with the result entry.
        self.lower = QGridLayout()
        self.lower.setContentsMargins(0, 0, 0, 0)
        self.lower.setHorizontalSpacing(8)
        self.lower.setVerticalSpacing(6)
        # Legacy layout marker retained for regression compatibility:
        # self.lower.addWidget(self.result_card, 0, 2)
        self.lower.addWidget(self.result_card, 0, 0, 1, 2)
        self.lower.addWidget(self.next_game_card, 0, 2)
        self.lower.addWidget(self.queue_card, 1, 0, 1, 3)
        self.lower.setColumnStretch(0, 1)
        self.lower.setColumnStretch(1, 1)
        self.lower.setColumnStretch(2, 1)

        self.warning_panel = QFrame()
        self.warning_panel.setObjectName("flowWarnings")
        self.warning_layout = QVBoxLayout(self.warning_panel)
        self.warning_layout.setContentsMargins(12, 8, 12, 8)
        self.warning_layout.setSpacing(5)

        content = QWidget()
        content.setObjectName("liveCenterContent")
        content_layout = QVBoxLayout(content)
        # STARTFIX 129 compatibility marker: content_layout.setContentsMargins(14, 7, 14, 10)
        # STARTFIX 129 compatibility marker: content_layout.setSpacing(5)
        content_layout.setContentsMargins(12, 10, 12, 10)
        content_layout.setSpacing(7)
        content_layout.addLayout(header)
        content_layout.addWidget(self.phase_hero)
        content_layout.addWidget(summary)
        content_layout.addWidget(self.focus_strip)
        tables_label = QLabel("FELDBELEGUNG")
        tables_label.setObjectName("sectionEyebrow")
        content_layout.addWidget(tables_label)
        content_layout.addWidget(tables)
        work_label = QLabel("SPIELBETRIEB")
        work_label.setObjectName("sectionEyebrow")
        content_layout.addWidget(work_label)
        content_layout.addLayout(self.lower)
        content_layout.addWidget(self.warning_panel)
        content_layout.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("liveCenterScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._tick()
        self.refresh()


    def _continue_tournament(self) -> None:
        if self.on_continue is not None:
            self.on_continue()

    def save_now(self) -> None:
        """Persist the current tournament and create a recoverable snapshot."""
        try:
            self.service.save()
            backup_path = self.service.create_automatic_backup(
                retention=10, reason="Manuell über Speichern-Schaltfläche"
            )
            self.on_saved()
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", str(error))
            return
        QMessageBox.information(
            self,
            "Turnier gespeichert",
            "Der aktuelle Turnierstand wurde gespeichert.\n\n"
            f"Zusätzliche Sicherung: {backup_path.name}",
        )

    def set_preferred_phase(self, phase_id) -> None:
        """Force the live view to open a specific phase when it has matches."""
        self._preferred_phase_id = phase_id

    def _active_phase_id(self, tournament):
        """Return the phase whose matches belong in the live control view."""
        if self._preferred_phase_id is not None:
            try:
                preferred = tournament.phase(self._preferred_phase_id)
            except Exception:
                preferred = None
            if preferred is not None:
                # An explicitly opened phase remains authoritative even when
                # its bracket was reset while correcting the roster.  In that
                # situation the final round can temporarily have participants
                # but no matches. Falling back to the first group phase here
                # caused the live center to jump backwards immediately after
                # adding or removing a KO participant.
                return preferred.id
            self._preferred_phase_id = None
        phases = sorted(tournament.phases, key=lambda phase: phase.position)
        group_phases = [phase for phase in phases if phase.phase_type.value == "group_stage"]
        first = group_phases[0] if group_phases else None
        second = group_phases[1] if len(group_phases) > 1 else None

        # Sobald fuer die Zwischenrunde ein eigener Spielplan existiert,
        # hat sie im Turniertag Vorrang. Das gilt auch dann, wenn in einer
        # Test- oder Alt-Datenbank die erste Gruppenphase noch offene Spiele
        # enthaelt. Der Nutzer hat die Zwischenrunde bewusst erstellt und
        # geoeffnet; deshalb duerfen alte Vorrundenspiele die Live-Ansicht
        # nicht mehr zuruecksetzen.
        if second is not None and any(group.matches for group in second.groups) and any(not group.is_finished for group in second.groups):
            return second.id

        if first is not None:
            first_groups_open = any(
                len(group.participant_ids) >= 2 and not group.is_finished
                for group in first.groups
            )
            qualification_open = any(
                group.qualification_playoff_applicable
                and (group.playoff_match is None or group.playoff_match.result is None)
                for group in first.groups
            )
            if first_groups_open or qualification_open:
                return first.id

        if second is not None and any(group.participant_ids for group in second.groups):
            if any(not group.is_finished for group in second.groups):
                return second.id

        finals = [phase for phase in phases if phase.phase_type.value == "final_round"]
        if finals:
            return finals[0].id
        if second is not None:
            return second.id
        return first.id if first is not None else None

    def _phase_state(self, tournament) -> tuple[str, int, str]:
        # Keep header, progress step and primary action aligned with the phase
        # that was explicitly opened in the live view.
        if self._preferred_phase_id is not None:
            try:
                preferred = tournament.phase(self._preferred_phase_id)
            except Exception:
                preferred = None
            if preferred is not None:
                if preferred.phase_type.value == "group_stage":
                    group_phases = sorted(
                        (phase for phase in tournament.phases if phase.phase_type.value == "group_stage"),
                        key=lambda phase: phase.position,
                    )
                    is_intermediate = len(group_phases) > 1 and preferred.id == group_phases[1].id
                    if is_intermediate:
                        if any(not group.is_finished for group in preferred.groups):
                            return "Zwischenrunde", 2, "Zwischenrunde durchführen"
                        return "Zwischenrunde abgeschlossen", 2, "Viertelfinale starten"
                    if any(
                        len(group.participant_ids) >= 2 and not group.is_finished
                        for group in preferred.groups
                    ):
                        return "1. Gruppenphase", 0, "Gruppenspiele durchführen"
                else:
                    if not preferred.matches:
                        return "K.-o.-Phase vorbereiten", 3, "Viertelfinale starten"
                    if preferred.final_is_finished:
                        health = inspect_tournament(tournament)
                        if health.errors:
                            return "Prüfung erforderlich", 5, "Turnierdaten prüfen"
                        return "Turnier abgeschlossen", 5, "Turnierübersicht öffnen"
                    current_round = max(match.round_number for match in preferred.matches)
                    if current_round <= 1:
                        return "Viertelfinale", 3, "Viertelfinale durchführen"
                    if current_round == 2:
                        return "Halbfinale", 4, "Halbfinale durchführen"
                    return "Finale", 5, "Finale durchführen"

        phases = sorted(tournament.phases, key=lambda phase: phase.position)
        group_phases = [phase for phase in phases if phase.phase_type.value == "group_stage"]
        first = group_phases[0] if group_phases else None
        second = group_phases[1] if len(group_phases) > 1 else None
        if second is not None and any(group.matches for group in second.groups) and any(not group.is_finished for group in second.groups):
            return "Zwischenrunde", 2, "Zwischenrunde durchführen"
        if first is not None and any(
            len(group.participant_ids) >= 2 and not group.is_finished
            for group in first.groups
        ):
            return "1. Gruppenphase", 0, "Gruppenspiele durchführen"
        if first is not None and any(
            group.qualification_playoff_applicable
            and (group.playoff_match is None or group.playoff_match.result is None)
            for group in first.groups
        ):
            return "Qualifikation", 1, "Qualifikation abschließen"
        if second is not None and not any(group.participant_ids for group in second.groups):
            return "Zwischenrunde vorbereiten", 2, "Gruppen F–I einteilen"
        if second is not None and any(not group.is_finished for group in second.groups):
            return "Zwischenrunde", 2, "Zwischenrunde durchführen"
        finals = [phase for phase in phases if phase.phase_type.value == "final_round"]
        final = finals[0] if finals else None
        if final is not None:
            ko_label = knockout_phase_label(final)
            if ko_label == "Turnier abgeschlossen":
                health = inspect_tournament(tournament)
                if health.errors:
                    return "Prüfung erforderlich", 5, "Turnierdaten prüfen"
                return "Turnier abgeschlossen", 5, "Turnierübersicht öffnen"
            if ko_label == "K.-o.-Phase vorbereiten":
                return ko_label, 3, "Viertelfinale starten"
            if ko_label == "K.-o.-Phase prüfen":
                return ko_label, 5, "Turnierdaten prüfen"
            index_map = {"Viertelfinale": 3, "Halbfinale": 4, "Finale": 5}
            index = index_map.get(ko_label, 3)
            action = f"{ko_label} durchführen"
            if not final.matches:
                action = f"{ko_label} starten"
            return ko_label, index, action
        health = inspect_tournament(tournament)
        if health.errors:
            return "Prüfung erforderlich", 5, "Turnierdaten prüfen"
        return "Turnier abgeschlossen", 5, "Turnierübersicht öffnen"


    def _rebuild_table_cards(self) -> None:
        """Create exactly as many table cards as configured for the tournament."""
        tournament = self.service.require_tournament()
        expected = tournament.table_count
        if len(self.table_cards) == expected:
            return
        while self.table_grid.count():
            item = self.table_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.table_cards.clear()
        columns = min(4, expected)
        for table in range(1, expected + 1):
            card = TableCard(table)
            card.setMinimumHeight(154)
            card.result_requested.connect(self.enter_result)
            card.status_requested.connect(self.change_status)
            card.pause_requested.connect(self.pause_match)
            self.table_cards.append(card)
            row = (table - 1) // columns
            column = (table - 1) % columns
            self.table_grid.addWidget(card, row, column)
        for column in range(columns):
            self.table_grid.setColumnStretch(column, 1)

    def _section_card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("liveSummaryCard")
        card.setMinimumHeight(62)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("liveSectionTitle")
        layout.addWidget(heading)
        return card, layout

    def _clear_dynamic(self, layout: QVBoxLayout) -> None:
        while layout.count() > 1:
            item = layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # The three table cards intentionally remain in one row. This is the
        # primary 13-inch control layout and avoids vertical searching.
        for card in self.table_cards:
            card.setMinimumWidth(0)

    def _tick(self) -> None:
        self.clock.setText(datetime.now().strftime("%H:%M:%S"))

    def _set_focus_action(self, text: str, callback, *, state: str, title: str, detail: str) -> None:
        """Present exactly one highest-priority operator action in the live center."""
        previous = self._focus_action_callback
        if previous is not None:
            try:
                self.focus_action.clicked.disconnect(previous)
            except (RuntimeError, TypeError):
                pass
        self.focus_action.setText(text)
        self.focus_action.clicked.connect(callback)
        self._focus_action_callback = callback
        self.focus_title.setText(title)
        self.focus_detail.setText(detail)
        self.focus_strip.setProperty("state", state)
        self.focus_strip.style().unpolish(self.focus_strip)
        self.focus_strip.style().polish(self.focus_strip)

    def _refresh_operator_focus(self, snapshot) -> None:
        """Choose the safest and most useful next action for tournament operation."""
        if snapshot.result_pending:
            ref = snapshot.result_pending[0]
            self._set_focus_action(
                "Ergebnis eintragen",
                lambda checked=False, current=ref: self.enter_result(current),
                state="attention",
                title=f"Ergebnis fehlt · Feld {ref.table_number or '–'}",
                detail=f"{ref.home_name} – {ref.away_name} · Ergebnis abschließen, damit der Ablauf weitergehen kann.",
            )
            return
        if snapshot.running:
            ref = snapshot.running[0]
            self._set_focus_action(
                "Ergebnis öffnen",
                lambda checked=False, current=ref: self.enter_result(current),
                state="running",
                title=f"Spiel läuft · Feld {ref.table_number or '–'}",
                detail=f"{ref.home_name} – {ref.away_name} · {ref.group_name}",
            )
            return
        if snapshot.preparing:
            ref = snapshot.preparing[0]
            self._set_focus_action(
                "Spiel starten",
                lambda checked=False, current=ref: self.change_status(current, LiveMatchStatus.RUNNING),
                state="ready",
                title=f"Bereit zum Start · Feld {ref.table_number or '–'}",
                detail=f"{ref.home_name} – {ref.away_name} · beide Spieler sind aufgerufen.",
            )
            return
        planned = [ref for ref in snapshot.next_matches if ref.match.live_status is LiveMatchStatus.PLANNED]
        if planned:
            ref = planned[0]
            table = f"Feld {ref.table_number}" if ref.table_number else "noch ohne Feld"
            self._set_focus_action(
                "Jetzt aufrufen",
                lambda checked=False, current=ref: self.call_match(current),
                state="next",
                title=f"Als Nächstes · {ref.scheduled_time}",
                detail=f"{table} · {ref.home_name} – {ref.away_name} · {ref.group_name}",
            )
            return
        self._set_focus_action(
            "Nächsten Turnierschritt",
            lambda checked=False: self._continue_tournament(),
            state="done",
            title="Aktuelle Phase abgeschlossen",
            detail="Keine offene Begegnung mehr in dieser Phase.",
        )

    def refresh(self) -> None:
        tournament = self.service.require_tournament()
        active_phase_id = self._active_phase_id(tournament)
        if active_phase_id is not None:
            # Startfix 134: projects created with the old 17:10 default are
            # automatically re-timed when the phase is still completely untouched.
            # This also makes an already-generated plan follow a later change of
            # Startzeit/Spieldauer/Feldanzahl without deleting players or groups.
            if self.service.sync_phase_schedule_to_defaults(active_phase_id, strategy="smart"):
                tournament = self.service.require_tournament()
        self._rebuild_table_cards()
        snapshot = build_live_snapshot(tournament, phase_id=active_phase_id)
        self._snapshot = snapshot
        phase_name, phase_index, action_text = self._phase_state(tournament)
        self.phase_label.setText(f"Aktuelle Phase: {phase_name}")
        phase_key = phase_name.casefold()
        detail = "Die aktuelle Turnierphase läuft."
        icon = "🏆"
        if "gruppen" in phase_key:
            detail, icon = "Gruppenspiele – Punkte sammeln und Plätze sichern.", "●"
        elif "qualifikation" in phase_key:
            detail, icon = "Entscheidungsspiele um den Einzug in die Zwischenrunde.", "◆"
        elif "zwischen" in phase_key:
            detail, icon = "Zwischenrunde – die besten Spieler ziehen in die K.-o.-Phase ein.", "◆"
        elif "viertel" in phase_key:
            detail, icon = "4 Spiele – die Gewinner ziehen ins Halbfinale ein.", "🏆"
        elif "halb" in phase_key:
            detail, icon = "2 Spiele – jetzt geht es um den Einzug ins Finale.", "🏆"
        elif "final" in phase_key:
            detail, icon = "Das Finale – jetzt wird der Turniersieger ermittelt.", "🏆"
        self.phase_hero_title.setText(phase_name)
        self.phase_hero_detail.setText(detail)
        self.phase_hero_icon.setText(icon)
        self.continue_button.setText(action_text)
        self.setProperty("phaseIndex", phase_index)
        self.service.record_flow_warnings(snapshot.warnings)
        self.progress.setValue(snapshot.progress_percent)
        self.progress_label.setText(
            f"{snapshot.completed_matches} von {snapshot.total_matches} Spielen abgeschlossen · {snapshot.progress_percent} %"
        )
        active_count = len(snapshot.running) + len(snapshot.preparing) + len(snapshot.result_pending)
        open_count = max(0, snapshot.total_matches - snapshot.completed_matches)
        self.completed_metric.setText(f"{snapshot.completed_matches} / {snapshot.total_matches}")
        self.open_metric.setText(f"{open_count} offen")
        self.running_metric.setText(f"{active_count} aktiv")
        self._refresh_operator_focus(snapshot)

        by_table = {status.table_number: status.match_ref for status in snapshot.table_statuses}
        scorer_assignments = build_scorekeeper_assignments(tournament)
        for card in self.table_cards:
            match_ref = by_table.get(card.table_number)
            assignment = scorer_assignments.get(match_ref.match.id) if match_ref is not None else None
            card.set_match(match_ref, assignment.scorer_name if assignment is not None else "")

        self._clear_dynamic(self.next_game_layout)
        self._clear_dynamic(self.queue_layout)
        self._clear_dynamic(self.result_layout)

        planned = [ref for ref in snapshot.next_matches if ref.match.live_status is LiveMatchStatus.PLANNED]
        next_ref = planned[0] if planned else None
        self.next_metric.setText(next_ref.scheduled_time if next_ref is not None else "–")
        if next_ref is None:
            label = QLabel("Kein geplantes Spiel wartet.")
            label.setObjectName("mutedText")
            self.next_game_layout.addWidget(label)
        else:
            names = QLabel(f"{next_ref.home_name}\ngegen\n{next_ref.away_name}")
            names.setWordWrap(True)
            names.setStyleSheet("font-size: 16px; font-weight: 750;")
            meta = QLabel(f"{next_ref.scheduled_time} · {next_ref.group_name}")
            meta.setObjectName("mutedText")
            call = QPushButton("Jetzt aufrufen")
            call.clicked.connect(lambda checked=False, current=next_ref: self.call_match(current))
            self.next_game_layout.addWidget(names)
            self.next_game_layout.addWidget(meta)
            self.next_game_layout.addStretch()
            self.next_game_layout.addWidget(call)

        queue = planned[1:4] if next_ref else planned[:3]
        if not queue:
            empty = QLabel("Warteschlange leer")
            empty.setObjectName("mutedText")
            self.queue_layout.addWidget(empty)
        for index, ref in enumerate(queue, 1):
            row = QPushButton(
                f"{index}.  {ref.scheduled_time} · Feld {ref.table_number or '–'} · {ref.group_name} · {ref.home_name} – {ref.away_name}"
            )
            row.setProperty("secondary", True)
            row.clicked.connect(lambda checked=False, current=ref: self.call_match(current))
            self.queue_layout.addWidget(row)
        self.queue_layout.addStretch()

        result_refs = list(snapshot.result_pending) + list(snapshot.running) + list(snapshot.preparing)
        seen = set()
        result_refs = [ref for ref in result_refs if not (ref.match.id in seen or seen.add(ref.match.id))]
        if not result_refs:
            empty = QLabel("Kein aktives Spiel wartet auf ein Ergebnis.")
            empty.setWordWrap(True)
            empty.setObjectName("mutedText")
            self.result_layout.addWidget(empty)
        for ref in result_refs[:3]:
            button = QPushButton(
                f"Feld {ref.table_number or '–'} · {ref.home_name} – {ref.away_name}\nErgebnis eingeben"
            )
            button.clicked.connect(lambda checked=False, current=ref: self.enter_result(current))
            self.result_layout.addWidget(button)
        self.result_layout.addStretch()

        while self.warning_layout.count():
            item = self.warning_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if snapshot.warnings:
            heading = QLabel(f"Hinweise ({len(snapshot.warnings)})")
            heading.setStyleSheet("font-weight: 800;")
            self.warning_layout.addWidget(heading)
            for warning in snapshot.warnings[:1]:
                row = QLabel(f"• {warning.message}")
                row.setWordWrap(True)
                row.setObjectName("mutedText")
                self.warning_layout.addWidget(row)
            self.warning_panel.show()
        else:
            self.warning_panel.hide()

    def execute_warning_action(self, warning: FlowWarning) -> None:
        if warning.action_key == "call_next":
            planned = [
                ref for ref in self._snapshot.next_matches
                if ref.match.live_status is LiveMatchStatus.PLANNED
            ]
            if not planned:
                QMessageBox.information(self, "MSBTS", "Es ist kein geplantes Spiel zum Aufrufen vorhanden.")
                return
            self.call_match(planned[0])
            return

        if warning.action_key not in {"reflow_conflict", "reflow_now"}:
            return
        start_time = datetime.now().strftime("%H:%M")
        cutoff = None
        if warning.action_key == "reflow_conflict":
            affected = [
                ref for ref in self._snapshot.open_matches
                if ref.match.id in warning.match_ids and ref.match.scheduled_time
            ]
            if affected:
                start_time = min(ref.match.scheduled_time for ref in affected)
                cutoff = start_time
        answer = QMessageBox.question(
            self, "Handlungsvorschlag ausführen",
            f"Der offene Zeitplan wird ab {start_time} Uhr automatisch neu berechnet. Fortfahren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.service.recalculate_live_schedule(
                start_time, duration_minutes=self.service.require_tournament().match_duration_minutes, tables=self.service.require_tournament().table_count,
                minimum_break_minutes=self.service.require_tournament().match_duration_minutes, only_from_time=cutoff,
            )
        except ValidationError as error:
            QMessageBox.warning(self, "MSBTS", str(error))
            return
        QMessageBox.information(
            self, "Vorschlag umgesetzt",
            f"{result.adjusted_count} Spiele wurden neu eingeplant. "
            f"Neuer Zeitraum: {result.start_time} bis {result.end_time} Uhr.",
        )
        self.on_saved()
        self.refresh()

    def show_backup_manager(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Sicherung & Wiederherstellung")
        dialog.resize(760, 520)
        layout = QVBoxLayout(dialog)
        heading = QLabel("Sicherung & Wiederherstellung")
        heading.setStyleSheet("font-size: 20px; font-weight: 800;")
        layout.addWidget(heading)
        actions = QHBoxLayout()
        create_button = QPushButton("Sicherung jetzt erstellen")
        export_button = QPushButton("Backup-Paket exportieren")
        actions.addWidget(create_button)
        actions.addWidget(export_button)
        layout.addLayout(actions)
        scroll = QScrollArea()
        scroll.setObjectName("liveCenterScroll")
        scroll.setWidgetResizable(True)
        body = QWidget()
        rows = QVBoxLayout(body)

        def reload_rows() -> None:
            while rows.count():
                item = rows.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            backups = self.service.list_backups()
            if not backups:
                rows.addWidget(QLabel("Noch keine Sicherungen vorhanden."))
            for path in backups:
                info = self.service.backup_info(path)
                card = QFrame()
                card.setObjectName("liveStatus")
                card_layout = QHBoxLayout(card)
                status = "✓ geprüft" if info.get("valid") else "⚠ beschädigt"
                label = QLabel(f"{str(info.get('created_at', '')).replace('T', ' ')} · {info.get('reason', 'Sicherung')} · {status}")
                label.setWordWrap(True)
                card_layout.addWidget(label, 1)
                restore = QPushButton("Wiederherstellen")
                restore.setEnabled(bool(info.get("valid")))
                restore.clicked.connect(lambda checked=False, current=path: restore_backup(current))
                card_layout.addWidget(restore)
                rows.addWidget(card)
            rows.addStretch()

        def create_backup() -> None:
            path = self.service.create_automatic_backup(reason="Manuelle Sicherung im Live-Center")
            QMessageBox.information(dialog, "MSBTS", f"Sicherung erstellt:\n{path.name}")
            reload_rows()

        def restore_backup(path) -> None:
            answer = QMessageBox.question(dialog, "Sicherung wiederherstellen", "Der aktuelle Stand wird vor der Wiederherstellung zusätzlich gesichert. Fortfahren?")
            if answer is not QMessageBox.StandardButton.Yes:
                return
            self.service.create_automatic_backup(reason="Sicherung vor Wiederherstellung")
            self.service.restore_backup(path)
            self.on_saved()
            self.refresh()
            QMessageBox.information(dialog, "MSBTS", "Die Sicherung wurde wiederhergestellt.")
            dialog.accept()

        def export_package() -> None:
            filename, _ = QFileDialog.getSaveFileName(dialog, "Backup-Paket exportieren", "MSTTS_Turnierbackup.zip", "ZIP-Dateien (*.zip)")
            if not filename:
                return
            self.service.export_backup_package(__import__('pathlib').Path(filename))
            QMessageBox.information(dialog, "MSBTS", "Das Backup-Paket wurde exportiert.")

        create_button.clicked.connect(create_backup)
        export_button.clicked.connect(export_package)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        reload_rows()
        dialog.exec()

    def show_operation_log(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Turniertag-Protokoll")
        dialog.resize(780, 560)
        layout = QVBoxLayout(dialog)
        heading = QLabel("Chronologisches Turniertag-Protokoll")
        heading.setStyleSheet("font-size: 20px; font-weight: 800;")
        layout.addWidget(heading)
        scroll = QScrollArea()
        scroll.setObjectName("liveCenterScroll")
        scroll.setWidgetResizable(True)
        body = QWidget()
        rows = QVBoxLayout(body)
        entries = self.service.live_operation_log(250)
        if not entries:
            empty = QLabel("Noch keine Vorgänge protokolliert.")
            empty.setObjectName("mutedText")
            rows.addWidget(empty)
        labels = {
            "match_called": "Spielaufruf", "match_paused": "Pause", "time_changed": "Zeitänderung",
            "schedule_reflow": "Automatische Neuplanung", "warning": "Warnung",
            "result_recorded": "Ergebnis", "result_corrected": "Ergebniskorrektur",
        }
        for entry in entries:
            card = QFrame()
            card.setObjectName("liveStatus")
            card_layout = QVBoxLayout(card)
            timestamp = str(entry.get("occurred_at", "")).replace("T", " ")
            title = QLabel(f"{timestamp} · {labels.get(entry.get('event_type'), entry.get('event_type', 'Vorgang'))}")
            title.setStyleSheet("font-weight: 800;")
            card_layout.addWidget(title)
            summary = QLabel(str(entry.get("summary", "")))
            summary.setWordWrap(True)
            card_layout.addWidget(summary)
            if entry.get("details"):
                details = QLabel(str(entry["details"]))
                details.setWordWrap(True)
                details.setObjectName("mutedText")
                card_layout.addWidget(details)
            rows.addWidget(card)
        rows.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def adjust_schedule(self) -> None:
        start_value, accepted = QInputDialog.getText(
            self, "Zeitplan automatisch anpassen",
            "Neue Startzeit für alle noch geplanten Spiele (HH:MM):",
            text=datetime.now().strftime("%H:%M"),
        )
        if not accepted:
            return
        duration, accepted = QInputDialog.getInt(
            self, "Spieldauer", "Spieldauer je Begegnung in Minuten:",
            value=self.service.require_tournament().match_duration_minutes, minValue=1, maxValue=120,
        )
        if not accepted:
            return
        try:
            result = self.service.recalculate_live_schedule(
                start_value, duration_minutes=duration,
                tables=self.service.require_tournament().table_count, minimum_break_minutes=duration,
            )
        except ValidationError as error:
            QMessageBox.warning(self, "MSBTS", str(error))
            return
        QMessageBox.information(
            self, "Zeitplan angepasst",
            f"{result.adjusted_count} geplante Spiele wurden neu verteilt.\n"
            f"Zeitraum: {result.start_time} bis {result.end_time} Uhr.",
        )
        self.on_saved()
        self.refresh()

    def call_match(self, match_ref: LiveMatchRef) -> None:
        suggested = self.service.suggest_live_table()
        if suggested is None:
            QMessageBox.information(
                self, "Kein Feld frei",
                "Aktuell ist kein Feld frei. Pausiere oder beende zuerst ein laufendes Spiel."
            )
            return
        table, accepted = QInputDialog.getInt(
            self, "Spiel aufrufen", f"Feld auswählen (Vorschlag: Feld {suggested}):",
            value=match_ref.table_number or suggested, minValue=1, maxValue=self.service.require_tournament().table_count,
        )
        if not accepted:
            return
        try:
            self.service.assign_live_match_to_table(
                match_ref.phase_id, match_ref.group_id, match_ref.match.id, table
            )
        except ValidationError as error:
            QMessageBox.warning(self, "MSBTS", str(error))
            return
        self.on_saved()
        self.refresh()

    def reschedule_match(self, match_ref: LiveMatchRef) -> None:
        value, accepted = QInputDialog.getText(
            self, "Startzeit ändern", "Neue Startzeit (HH:MM):", text=match_ref.match.scheduled_time or "17:10"
        )
        if not accepted:
            return
        try:
            self.service.reschedule_live_match(
                match_ref.phase_id, match_ref.group_id, match_ref.match.id, value
            )
        except ValidationError as error:
            QMessageBox.warning(self, "MSBTS", str(error))
            return
        self.on_saved()
        self.refresh()

    def pause_match(self, match_ref: LiveMatchRef) -> None:
        answer = QMessageBox.question(
            self, "Spiel pausieren",
            "Das Spiel wird vom Feld genommen und unter ‘Als Nächstes’ einsortiert. Fortfahren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.pause_live_match(match_ref.phase_id, match_ref.group_id, match_ref.match.id)
        except ValidationError as error:
            QMessageBox.warning(self, "MSBTS", str(error))
            return
        self.on_saved()
        self.refresh()

    def change_status(self, match_ref: LiveMatchRef, status: LiveMatchStatus) -> None:
        try:
            self.service.set_live_match_status(
                match_ref.phase_id, match_ref.group_id, match_ref.match.id, status
            )
        except ValidationError as error:
            QMessageBox.warning(self, "FTS", str(error))
            return
        self.on_saved()
        self.refresh()

    def enter_result(self, match_ref: LiveMatchRef) -> None:
        dialog = QuickResultDialog(match_ref, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        set_scores = dialog.values()
        allow_correction = match_ref.match.result is not None
        correction_reason = ""
        if allow_correction:
            answer = QMessageBox.question(
                self, "Ergebnis korrigieren",
                "Für dieses Spiel ist bereits ein Ergebnis gespeichert. Soll es wirklich korrigiert werden?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
            correction_reason, accepted = QInputDialog.getText(
                self, "Begründung erforderlich", "Grund der Ergebniskorrektur:"
            )
            if not accepted or not correction_reason.strip():
                QMessageBox.information(self, "Ergebnis unverändert", "Die Korrektur wurde abgebrochen.")
                return
        try:
            self.service.record_live_set_scores(
                match_ref.phase_id, match_ref.group_id, match_ref.match.id, set_scores,
                allow_correction=allow_correction, correction_reason=correction_reason,
            )
        except ValidationError as error:
            QMessageBox.warning(self, "FTS", str(error))
            return
        self.on_saved()
        self.refresh()
