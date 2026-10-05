from __future__ import annotations

from typing import Callable
from uuid import UUID

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from fts.domain import ValidationError
from fts.engine import TournamentEngine
from fts.gui.tournament_designer_model import PhaseCardData, build_phase_cards


class TournamentDesignerPage(QWidget):
    def __init__(
        self,
        service: TournamentEngine,
        on_saved: Callable[[], None],
        on_continue: Callable[[], None] | None = None,
        on_open_results: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.on_saved = on_saved
        self.on_continue = on_continue
        self.on_open_results = on_open_results
        self.title = QLabel("Turnier-Designer · MSBTS 1.0")
        self.title.setStyleSheet("font-size: 24px; font-weight: 700;")
        self.subtitle = QLabel(
            "Phasenfolge und automatische Übergänge grafisch verwalten. "
            "Die Reihenfolge ergibt sich aus den vorhandenen Turnierphasen."
        )
        self.subtitle.setWordWrap(True)
        self.canvas = QWidget()
        self.canvas_layout = QVBoxLayout(self.canvas)
        self.canvas_layout.setContentsMargins(12, 12, 12, 12)
        self.canvas_layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        standard_button = QPushButton("Turnierablauf automatisch reparieren")
        standard_button.setToolTip(
            "Erstellt Vorrunde A-E, Qualifikationsspiele 3. gegen 4., vier Zwischenrundengruppen und die K.-o.-Phase."
        )
        standard_button.clicked.connect(lambda _checked=False: self._configure_standard_flow())
        results_button = QPushButton("1. Ergebnisse und Gruppen öffnen")
        results_button.setToolTip("Öffnet die Gruppenphase zur Ergebniseingabe.")
        results_button.clicked.connect(lambda _checked=False: self._open_results())
        continue_button = QPushButton("2. Turniertag jetzt fortsetzen")
        continue_button.setObjectName("primaryAction")
        continue_button.setToolTip(
            "Erzeugt nach der Vorrunde die Qualifikationsspiele, danach die Zwischenrunde und anschließend das Viertelfinale."
        )
        continue_button.clicked.connect(lambda _checked=False: self._continue_tournament())
        refresh_button = QPushButton("Ansicht aktualisieren")
        refresh_button.clicked.connect(lambda _checked=False: self.refresh())
        actions = QHBoxLayout()
        actions.addWidget(standard_button)
        actions.addStretch()
        actions.addWidget(refresh_button)
        workflow_actions = QHBoxLayout()
        workflow_actions.addWidget(results_button)
        workflow_actions.addWidget(continue_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)
        layout.addLayout(actions)
        layout.addLayout(workflow_actions)
        layout.addWidget(scroll, 1)


    def _open_results(self) -> None:
        if self.on_open_results is None:
            QMessageBox.warning(self, "Turnier-Designer", "Die Ergebniseingabe ist nicht verbunden.")
            return
        self.on_open_results()

    def _continue_tournament(self) -> None:
        if self.on_continue is None:
            QMessageBox.warning(self, "Turnier-Designer", "Der Turnierfortschritt ist nicht verbunden.")
            return
        self.on_continue()
        self.refresh()

    def _configure_standard_flow(self) -> None:
        """Repair the complete flow while preserving players and results."""
        try:
            self.service.ensure_freudenholm_flow()
            self.on_saved()
            self.refresh()
            QMessageBox.information(
                self,
                "Turnierablauf repariert",
                "Der vorhandene Turnierstand wurde beibehalten.\n\n"
                "Ergänzt bzw. repariert wurden:\n"
                "• Qualifikationsregeln der Gruppen A-E\n"
                "• Zwischenrunde mit vier Vierergruppen\n"
                "• Übergang zur K.-o.-Phase\n"
                "• Viertelfinale, Halbfinale und Finale",
            )
        except ValidationError as error:
            QMessageBox.warning(self, "FTS", str(error))

    def activate(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        while self.canvas_layout.count():
            item = self.canvas_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        tournament = self.service.require_tournament()
        cards = build_phase_cards(tournament)
        if not cards:
            message = QLabel("Noch keine Turnierphasen vorhanden. Lege sie unter „Gruppen und Phasen“ an.")
            message.setWordWrap(True)
            self.canvas_layout.addWidget(message)
            self.canvas_layout.addStretch()
            return
        for index, card in enumerate(cards):
            self.canvas_layout.addWidget(self._phase_card(card, cards))
            if index < len(cards) - 1:
                arrow = QLabel("↓")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                arrow.setStyleSheet("font-size: 24px; font-weight: 700;")
                self.canvas_layout.addWidget(arrow)
        self.canvas_layout.addStretch()

    def _phase_card(self, card: PhaseCardData, cards: list[PhaseCardData]) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setMinimumWidth(360)
        frame.setMaximumWidth(720)
        frame.setStyleSheet("QFrame { border: 1px solid #5E2A8A; border-radius: 12px; padding: 8px; background: #120724; }")
        heading = QLabel(f"{card.position}. {card.title}")
        heading.setStyleSheet("font-size: 17px; font-weight: 700; border: none;")
        kind = QLabel(card.kind)
        kind.setStyleSheet("font-weight: 600; border: none;")
        detail = QLabel(card.detail)
        detail.setStyleSheet("border: none;")
        target = QComboBox()
        target.addItem("Kein automatischer Übergang", None)
        for candidate in cards:
            if candidate.position > card.position:
                target.addItem(f"{candidate.position}. {candidate.title}", candidate.id)
        selected = target.findData(card.next_phase_id)
        target.setCurrentIndex(selected if selected >= 0 else 0)
        auto = QCheckBox("Nach Abschluss automatisch übertragen")
        auto.setChecked(card.auto_advance)
        mode = QComboBox()
        mode.addItem("Snake-Verteilung", "snake")
        mode.addItem("Ausgeglichen", "balanced")
        mode.setCurrentIndex(max(0, mode.findData(card.distribution_mode)))
        save = QPushButton("Übergang speichern")
        save.clicked.connect(
            lambda _checked=False, source_id=card.id, target_box=target, auto_box=auto, mode_box=mode:
            self._save_transition(
                source_id, target_box.currentData(), auto_box.isChecked(), mode_box.currentData()
            )
        )
        if target.count() == 1:
            target.setEnabled(False)
            auto.setEnabled(False)
            mode.setEnabled(False)
            save.setEnabled(card.next_phase_id is not None)
        layout = QVBoxLayout(frame)
        layout.addWidget(heading)
        layout.addWidget(kind)
        layout.addWidget(detail)
        layout.addSpacing(10)
        layout.addWidget(QLabel("Folgephase"))
        layout.addWidget(target)
        layout.addWidget(auto)
        layout.addWidget(mode)
        layout.addStretch()
        layout.addWidget(save)
        return frame

    def _save_transition(self, source_id: UUID, target_id: UUID | None, auto: bool, mode: str) -> None:
        try:
            if target_id is None:
                self.service.clear_phase_transition(source_id)
            else:
                self.service.configure_phase_transition(
                    source_id,
                    target_id,
                    auto_advance=auto,
                    distribution_mode=mode,
                )
            self.on_saved()
            self.refresh()
            QMessageBox.information(self, "Turnier-Designer", "Der Phasenübergang wurde gespeichert.")
        except ValidationError as error:
            QMessageBox.warning(self, "FTS", str(error))
