from __future__ import annotations

from collections.abc import Callable
import csv
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import Qt, QSize, QTime, QSettings, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QIntValidator, QPainter, QPen, QPixmap, QPalette
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QButtonGroup,
    QApplication,
    QDialog,
    QComboBox,
    QCheckBox,
    QColorDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QFrame,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QTimeEdit,
    QToolButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fts.domain import Competition, CompetitionFormat, CompetitionStatus, PhaseType, Tournament, ValidationError
from fts.repository.protocol import TournamentRepository
from fts.engine import TournamentEngine
from fts.tournament_health import inspect_tournament
from fts.scorekeepers import build_scorekeeper_assignments, normalize_scorer_names
from fts.gui.dashboard import DashboardPage
from fts.gui.document_center import DocumentCenterPage
from fts.gui.live_center import LiveCenterPage, QuickResultDialog
from fts.gui.live_center_model import LiveMatchRef
from fts.gui.presentation import PresentationWindow
from fts.gui.tournament_designer import TournamentDesignerPage
from fts.gui.theme_manager import THEME_PRESETS, build_stylesheet, save_theme, stored_theme
from fts.ko_layout import notebook_layout_columns, recommended_window_dimensions
from fts.version import DISPLAY_NAME, PRODUCT_NAME


def show_error(parent: QWidget, error: Exception) -> None:
    # Use an explicitly styled QMessageBox. On macOS the native message panel can
    # otherwise keep its light content background while inheriting the app's light
    # label color, making validation messages unreadable.
    box = QMessageBox(parent)
    box.setWindowTitle("MSBTS")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(str(error))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.setStyleSheet("""
        QMessageBox {
            background-color: #160629;
            color: #FFFFFF;
        }
        QMessageBox QLabel {
            color: #FFFFFF;
            background: transparent;
            min-width: 440px;
            font-size: 14px;
            font-weight: 700;
        }
        QMessageBox QPushButton {
            min-width: 130px;
            min-height: 34px;
            padding: 7px 18px;
            border-radius: 8px;
            border: 1px solid #C45CFF;
            background-color: #8D16F5;
            color: #FFFFFF;
            font-weight: 900;
        }
        QMessageBox QPushButton:hover {
            background-color: #A62BFF;
        }
    """)
    box.exec()


def prepare_dialog(dialog: QDialog, title: str, *, width: int = 480) -> None:
    """Apply one consistent dialog shell across the application."""
    dialog.setWindowTitle(title)
    dialog.setObjectName("professionalDialog")
    dialog.setMinimumWidth(width)


class GlobalWatermarkOverlay(QWidget):
    """One subtle MSBTS watermark spanning the complete workspace.

    The overlay is mouse-transparent and keeps the *complete* logo visible on
    every workspace size.  The mark is deliberately smaller and softer than in
    Alpha 6 so it never gets clipped by the viewport and never competes with
    tables, buttons or page headings.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("globalWatermarkOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        logo_path = Path(__file__).resolve().parent / "assets" / "msbts_logo.png"
        self._watermark = QPixmap(str(logo_path)) if logo_path.exists() else QPixmap()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()

        # STARTFIX 106: one large image watermark instead of per-page decoration.
        if not self._watermark.isNull() and rect.width() > 240 and rect.height() > 220:
            # MSBTS Alpha 7: fit the complete square logo inside the visible
            # workspace.  Alpha 6 intentionally overscaled the image, which made
            # the right/bottom portions look cropped on smaller Mac displays.
            # Keep generous margins and bias it slightly to the right so it reads
            # as a background motif rather than a centered badge.
            max_w = int(rect.width() * 0.56)
            max_h = int(rect.height() * 0.72)
            target = max(280, min(max_w, max_h))
            pixmap = self._watermark.scaled(
                target, target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            right_margin = int(rect.width() * 0.08)
            x = rect.right() - pixmap.width() - right_margin
            y = rect.center().y() - pixmap.height() // 2 + int(rect.height() * 0.02)
            x = max(rect.left() + 24, min(x, rect.right() - pixmap.width() - 24))
            y = max(rect.top() + 24, min(y, rect.bottom() - pixmap.height() - 24))
            painter.setOpacity(0.072)
            painter.drawPixmap(x, y, pixmap)

        # A few ultra-light bronze rings make the watermark read as one
        # continuous page motif even where opaque cards cover parts of the logo.
        painter.setOpacity(0.032)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#A934FF"), 2.0))
        diameter = int(min(rect.width() * 0.66, rect.height() * 0.88))
        cx = rect.right() - int(diameter * 0.54)
        cy = rect.center().y() + int(rect.height() * 0.03)
        painter.drawEllipse(cx - diameter // 2, cy - diameter // 2, diameter, diameter)
        painter.setOpacity(0.024)
        painter.setPen(QPen(QColor("#7132A8"), 1.4))
        painter.drawArc(
            rect.adjusted(int(rect.width() * 0.08), int(rect.height() * 0.10),
                          -int(rect.width() * 0.05), -int(rect.height() * 0.08)),
            28 * 16, 112 * 16,
        )
        painter.end()


def style_dialog_buttons(box: QDialogButtonBox, primary_text: str | None = None) -> None:
    """Use the same primary/secondary hierarchy in all custom dialogs."""
    primary = box.button(QDialogButtonBox.StandardButton.Ok) or box.button(QDialogButtonBox.StandardButton.Save)
    cancel = box.button(QDialogButtonBox.StandardButton.Cancel)
    if primary is not None:
        primary.setObjectName("dialogPrimaryButton")
        if primary_text:
            primary.setText(primary_text)
    if cancel is not None:
        cancel.setObjectName("dialogSecondaryButton")
        cancel.setText("Abbrechen")


class NewTournamentDialog(QDialog):
    """Three-step tournament setup: basics, participants, tournament mode."""

    def __init__(self, parent: QWidget | None = None, *, has_tournament: bool = False) -> None:
        super().__init__(parent)
        prepare_dialog(self, "Neues Turnier", width=760)
        self.setMinimumHeight(650)
        self.resize(800, 700)
        self._has_tournament = has_tournament
        self._step = 0

        self.step_label = QLabel()
        self.step_label.setObjectName("dialogEyebrow")
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 24px; font-weight: 700;")
        self.intro = QLabel()
        self.intro.setWordWrap(True)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_basics_page())
        self.pages.addWidget(self._build_participants_page())
        self.pages.addWidget(self._build_mode_page())
        self.pages.addWidget(self._build_summary_page())

        self.back_button = QPushButton("Zurück")
        self.back_button.setObjectName("secondaryButton")
        self.next_button = QPushButton("Weiter")
        self.next_button.setObjectName("primaryButton")
        self.cancel_button = QPushButton("Abbrechen")
        self.cancel_button.setObjectName("secondaryButton")
        self.back_button.clicked.connect(self._back)
        self.next_button.clicked.connect(self._next)
        self.cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(14)
        layout.addWidget(self.step_label)
        layout.addWidget(self.title)
        layout.addWidget(self.intro)
        layout.addWidget(self.pages, 1)
        layout.addLayout(buttons)
        self._update_step()

    def _build_basics_page(self) -> QWidget:
        page = QWidget(); form = QFormLayout(page)
        self.name_edit = QLineEdit(); self.name_edit.setPlaceholderText("z. B. Vereinsmeisterschaft 2027")
        self.name_edit.textChanged.connect(self._update_step)
        self.organizer_edit = QLineEdit(); self.location_edit = QLineEdit()
        self.table_count = QSpinBox(); self.table_count.setRange(1, 16); self.table_count.setValue(3); self.table_count.setSuffix(" Felder")
        self.backup_checkbox = QCheckBox("Vor dem Ersetzen eine Sicherung erstellen")
        self.backup_checkbox.setChecked(self._has_tournament); self.backup_checkbox.setVisible(self._has_tournament)
        self.confirm_checkbox = QCheckBox("Aktuelles Turnier vollständig ersetzen")
        self.confirm_checkbox.setVisible(self._has_tournament); self.confirm_checkbox.toggled.connect(self._update_step)
        form.addRow("Turniername", self.name_edit); form.addRow("Veranstalter", self.organizer_edit)
        form.addRow("Ort", self.location_edit); form.addRow("Anzahl der Felder", self.table_count)
        form.addRow(self.backup_checkbox); form.addRow(self.confirm_checkbox)
        return page

    def _build_participants_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page)
        hint = QLabel("Optional: Namen direkt einfügen – ein Teilnehmer pro Zeile. Du kannst Teilnehmer auch später ergänzen.")
        hint.setWordWrap(True); hint.setObjectName("mutedText")
        self.participant_text = QTextEdit(); self.participant_text.setPlaceholderText("Anna Becker\nBen Fischer\nClara Hoffmann")
        self.participant_text.textChanged.connect(self._update_recommendations)
        self.participant_count_hint = QLabel("Noch keine Teilnehmer eingetragen")
        self.participant_count_hint.setObjectName("mutedText")
        layout.addWidget(hint); layout.addWidget(self.participant_text, 1); layout.addWidget(self.participant_count_hint)
        return page

    def _build_mode_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 8, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        grid.setColumnMinimumWidth(0, 185)
        grid.setColumnStretch(1, 1)
        scroll.setWidget(content)

        self.recommendation = QLabel()
        self.recommendation.setObjectName("wizardRecommendation")
        self.recommendation.setWordWrap(True)

        self.group_count = QSpinBox()
        self.group_count.setRange(1, 26)
        self.group_count.setValue(4)
        self.group_count.setSuffix(" Gruppen")
        self.group_count.valueChanged.connect(self._sync_group_plan_from_count)

        self.first_group_sizes = QLineEdit()
        self.first_group_sizes.setPlaceholderText("optional, z. B. 3,3,4,4,4")
        self.first_group_sizes.setToolTip("Exakte Zielgrößen der Vorrundengruppen. Leer = automatisch gleichmäßig.")
        self.first_group_sizes.textChanged.connect(self._update_recommendations)

        self.distribution_strategy = QComboBox()
        self.distribution_strategy.addItem("Fair – Verein, Kategorie & Setzliste berücksichtigen", "fair")
        self.distribution_strategy.addItem("Vereine möglichst trennen", "club")
        self.distribution_strategy.addItem("Kategorien möglichst verteilen", "category")
        self.distribution_strategy.addItem("Setzliste / Startnummern verteilen", "seeded")
        self.distribution_strategy.addItem("Nur gleichmäßig verteilen", "balanced")

        self.qualifiers = QSpinBox()
        self.qualifiers.setRange(0, 16)
        self.qualifiers.setValue(2)
        self.qualifiers.setSuffix(" pro Gruppe")

        self.qualifier_mode = QComboBox()
        self.qualifier_mode.addItem("Top 1 direkt", "top1")
        self.qualifier_mode.addItem("Top 2 direkt", "top2")
        self.qualifier_mode.addItem("Top 3 direkt", "top3")
        self.qualifier_mode.addItem("Top 2 + Platz 3 gegen 4", "top2_playoff")
        self.qualifier_mode.addItem("Individuell später je Gruppe", "individual")
        self.qualifier_mode.setCurrentIndex(1)
        self.qualifier_mode.currentIndexChanged.connect(self._apply_qualifier_template)

        self.qualification = QCheckBox("Qualifikationsphase verwenden")
        self.intermediate = QCheckBox("Zwischenrunde verwenden")
        self.intermediate.toggled.connect(self._update_recommendations)

        self.intermediate_sizes = QLineEdit()
        self.intermediate_sizes.setPlaceholderText("optional, z. B. 4,3,3,3")
        self.intermediate_sizes.setToolTip("Zielgrößen der Zwischenrunde. Summe muss zur Zahl der Qualifizierten passen.")
        self.intermediate_sizes.textChanged.connect(self._update_recommendations)

        self.intermediate_qualifiers = QSpinBox()
        self.intermediate_qualifiers.setRange(1, 16)
        self.intermediate_qualifiers.setValue(2)
        self.intermediate_qualifiers.setSuffix(" pro Gruppe")

        self.final_round = QCheckBox("K.-o.-Finalrunde verwenden")
        self.final_round.setChecked(True)
        self.plan_check = QLabel()
        self.plan_check.setObjectName("infoBox")
        self.plan_check.setWordWrap(True)

        note = QLabel("Qualifikation und Zwischenrunde bleiben optional und werden nur aktiv, wenn die Teilnehmerzahl die Voraussetzungen erfüllt.")
        note.setWordWrap(True)
        note.setObjectName("infoBox")

        for widget in (self.group_count, self.first_group_sizes, self.distribution_strategy, self.qualifiers,
                       self.qualifier_mode, self.intermediate_sizes, self.intermediate_qualifiers):
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            widget.setMinimumWidth(360)

        row = 0
        grid.addWidget(self.recommendation, row, 0, 1, 2); row += 1

        def add_field(label_text: str, widget: QWidget) -> None:
            nonlocal row
            label = QLabel(label_text)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(label, row, 0)
            grid.addWidget(widget, row, 1)
            row += 1

        add_field("Gruppenanzahl", self.group_count)
        add_field("Vorrundengrößen", self.first_group_sizes)
        add_field("Einteilung", self.distribution_strategy)
        add_field("Qualifikationsregel", self.qualifier_mode)
        add_field("Direkt weiter", self.qualifiers)

        self.qualification_availability = QLabel()
        self.qualification_availability.setObjectName("mutedText")
        self.qualification_availability.setWordWrap(True)
        grid.addWidget(self.qualification, row, 0, 1, 2); row += 1
        grid.addWidget(self.qualification_availability, row, 0, 1, 2); row += 1

        self.intermediate_availability = QLabel()
        self.intermediate_availability.setObjectName("mutedText")
        self.intermediate_availability.setWordWrap(True)
        grid.addWidget(self.intermediate, row, 0, 1, 2); row += 1
        grid.addWidget(self.intermediate_availability, row, 0, 1, 2); row += 1
        add_field("Zwischenrundengrößen", self.intermediate_sizes)
        add_field("Weiter aus Zwischenrunde", self.intermediate_qualifiers)

        grid.addWidget(self.final_round, row, 0, 1, 2); row += 1
        grid.addWidget(self.plan_check, row, 0, 1, 2); row += 1
        grid.addWidget(note, row, 0, 1, 2); row += 1
        grid.setRowStretch(row, 1)

        self._update_recommendations()
        return page

    def _participant_count(self) -> int:
        return len([line for line in self.participant_text.toPlainText().splitlines() if line.strip()])

    def _recommended_group_count(self, count: int) -> int:
        if count <= 0:
            return 4
        # Prefer groups of about four players; cap at the application maximum.
        return max(1, min(26, round(count / 4)))

    @staticmethod
    def _parse_sizes(text: str) -> tuple[int, ...]:
        raw = text.strip()
        if not raw:
            return ()
        try:
            sizes = tuple(int(part.strip()) for part in raw.split(",") if part.strip())
        except ValueError:
            return ()
        return sizes if sizes and all(size >= 2 for size in sizes) else ()

    @staticmethod
    def _balanced_sizes(total: int, groups: int) -> tuple[int, ...]:
        if total <= 0 or groups <= 0:
            return ()
        groups = min(groups, total)
        base, extra = divmod(total, groups)
        return tuple(base + (1 if index < extra else 0) for index in range(groups))

    def _sync_group_plan_from_count(self) -> None:
        if self._participant_count() and not self.first_group_sizes.hasFocus():
            sizes = self._balanced_sizes(self._participant_count(), self.group_count.value())
            if sizes and min(sizes) >= 2:
                self.first_group_sizes.setText(",".join(map(str, sizes)))
        self._update_recommendations()

    def _apply_qualifier_template(self) -> None:
        mode = self.qualifier_mode.currentData()
        if mode == "top1": self.qualifiers.setValue(1)
        elif mode in ("top2", "top2_playoff"): self.qualifiers.setValue(2)
        elif mode == "top3": self.qualifiers.setValue(3)
        self._update_recommendations()

    def _projected_qualifiers(self) -> int:
        sizes = self._parse_sizes(self.first_group_sizes.text())
        if not sizes:
            count = self._participant_count()
            sizes = self._balanced_sizes(count, self.group_count.value()) if count else tuple(4 for _ in range(self.group_count.value()))
        direct = sum(min(self.qualifiers.value(), size) for size in sizes)
        playoff = sum(1 for size in sizes if size >= 4) if self.qualifier_mode.currentData() == "top2_playoff" else 0
        return direct + playoff

    def _update_recommendations(self) -> None:
        if not hasattr(self, "group_count"):
            return
        count = self._participant_count()
        recommended = self._recommended_group_count(count)
        if hasattr(self, "participant_count_hint"):
            self.participant_count_hint.setText(f"{count} Teilnehmer eingetragen" if count else "Noch keine Teilnehmer eingetragen")
        if count and not self.first_group_sizes.text().strip():
            self.group_count.blockSignals(True); self.group_count.setValue(recommended); self.group_count.blockSignals(False)
            sizes = self._balanced_sizes(count, recommended)
            if sizes and min(sizes) >= 2:
                self.first_group_sizes.setText(",".join(map(str, sizes)))
            approx = count / recommended
            self.recommendation.setText(f"Empfehlung für {count} Teilnehmer: {recommended} Gruppen · etwa {approx:.1f} Spieler je Gruppe")
        elif count:
            self.recommendation.setText(f"{count} Teilnehmer · Gruppen und Zielgrößen können frei angepasst werden.")
        else:
            self.recommendation.setText("Noch keine Teilnehmer eingetragen. Gruppenanzahl und Zielgrößen können frei festgelegt werden.")
        qualification_available = count == 0 or count >= 4
        intermediate_available = count == 0 or count >= 4
        self.qualification.setEnabled(qualification_available)
        self.intermediate.setEnabled(intermediate_available)
        self.intermediate_sizes.setEnabled(self.intermediate.isChecked())
        self.intermediate_qualifiers.setEnabled(self.intermediate.isChecked())
        self.qualification_availability.setText("Optional: Regeln können auch später je Gruppe verfeinert werden.")
        self.intermediate_availability.setText("Optional: beliebige Gruppengrößen, z. B. 4,3,3,3. Leer = später planen.")
        first_sizes = self._parse_sizes(self.first_group_sizes.text())
        issues = []
        if self.first_group_sizes.text().strip() and not first_sizes:
            issues.append("Vorrundengrößen ungültig")
        if count and first_sizes and sum(first_sizes) != count:
            issues.append(f"Vorrunde hat {sum(first_sizes)} Plätze für {count} Teilnehmer")
        if first_sizes and len(first_sizes) != self.group_count.value():
            issues.append("Anzahl der Vorrundengrößen passt nicht zur Gruppenanzahl")
        projected = self._projected_qualifiers()
        second_sizes = self._parse_sizes(self.intermediate_sizes.text()) if self.intermediate.isChecked() else ()
        if self.intermediate.isChecked() and self.intermediate_sizes.text().strip() and not second_sizes:
            issues.append("Zwischenrundengrößen ungültig")
        if second_sizes and sum(second_sizes) != projected:
            issues.append(f"Zwischenrunde hat {sum(second_sizes)} Plätze, erwartet werden {projected}")
        ko_count = (len(second_sizes) * self.intermediate_qualifiers.value()) if second_sizes else projected
        ko_ok = ko_count in (2, 4, 8, 16, 32, 64)
        if self.final_round.isChecked() and not ko_ok:
            issues.append(f"K.-o.-Ziel aktuell {ko_count}; ideal sind 4, 8, 16 oder 32")
        self.plan_check.setText(("Prüfung: " + " · ".join(issues)) if issues else f"Plan plausibel · voraussichtlich {projected} Qualifizierte" + (f" · {ko_count} fürs K.-o." if self.final_round.isChecked() else ""))

    def _build_summary_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page)
        self.summary_headline = QLabel(); self.summary_headline.setObjectName("wizardSummaryHeadline"); self.summary_headline.setWordWrap(True)
        self.summary_details = QLabel(); self.summary_details.setObjectName("wizardSummaryDetails"); self.summary_details.setWordWrap(True)
        hint = QLabel("Mit „Turnier anlegen“ wird diese Struktur erstellt. Teilnehmer und Einstellungen können anschließend weiterhin bearbeitet werden.")
        hint.setObjectName("mutedText"); hint.setWordWrap(True)
        layout.addWidget(self.summary_headline); layout.addWidget(self.summary_details); layout.addStretch(); layout.addWidget(hint)
        return page

    def _refresh_summary(self) -> None:
        count = self._participant_count(); groups = self.group_count.value(); tables = self.table_count.value()
        if count and groups:
            base, extra = divmod(count, groups)
            group_text = f"{groups} Gruppen à ca. {count / groups:.1f} Spieler"
            if extra == 0: group_text = f"{groups} Gruppen à {base} Spieler"
        else:
            group_text = f"{groups} Gruppen"
        route = ["Gruppenphase"]
        if self.qualification.isChecked(): route.append("Qualifikation")
        if self.intermediate.isChecked(): route.append("Zwischenrunde")
        if self.final_round.isChecked(): route.append("K.-o.-Phase")
        participant_text = f"{count} Teilnehmer" if count else "Teilnehmer werden später ergänzt"
        self.summary_headline.setText(f"{participant_text}  ·  {group_text}  ·  {tables} Felder")
        strategy_text = self.distribution_strategy.currentText()
        self.summary_details.setText(
            f"Turnier: {self.tournament_name()}\n"
            f"Einteilung: {strategy_text}\n"
            f"Ablauf: {' → '.join(route)}\n"
            f"Vorrundengrößen: {self.first_group_sizes.text().strip() or 'automatisch'}\n"
            f"Qualifikationsregel: {self.qualifier_mode.currentText()}\n"
            f"Weiterkommen: {self.qualifiers.value()} direkt pro Gruppe\n"
            f"Zwischenrunde: {self.intermediate_sizes.text().strip() or ('später planen' if self.intermediate.isChecked() else 'nein')}\n"
            f"Ort: {self.location_edit.text().strip() or 'nicht angegeben'}\n"
            f"Veranstalter: {self.organizer_edit.text().strip() or 'nicht angegeben'}"
        )

    def _update_step(self) -> None:
        titles = [("SCHRITT 1 VON 4", "Turnierdaten", "Name, Ort und verfügbare Felder festlegen."),
                  ("SCHRITT 2 VON 4", "Teilnehmer", "Teilnehmer direkt erfassen oder diesen Schritt überspringen."),
                  ("SCHRITT 3 VON 4", "Turniermodus", "Gruppen und optionale Turnierphasen festlegen."),
                  ("SCHRITT 4 VON 4", "Zusammenfassung", "Turnieraufbau vor dem Anlegen noch einmal kontrollieren.")]
        eyebrow, title, intro = titles[self._step]
        self.step_label.setText(eyebrow); self.title.setText(title); self.intro.setText(intro)
        self.pages.setCurrentIndex(self._step); self.back_button.setEnabled(self._step > 0)
        self.next_button.setText("Turnier anlegen" if self._step == 3 else "Weiter")
        if self._step == 3:
            self._refresh_summary()
        valid = True
        if self._step == 0:
            valid = bool(self.name_edit.text().strip()) and ((not self._has_tournament) or self.confirm_checkbox.isChecked())
        self.next_button.setEnabled(valid)

    def _back(self) -> None:
        if self._step > 0: self._step -= 1; self._update_step()

    def _next(self) -> None:
        if self._step < 3:
            self._step += 1; self._update_step(); return
        count = self._participant_count()
        first_sizes = self._parse_sizes(self.first_group_sizes.text())
        if count and first_sizes and sum(first_sizes) != count:
            QMessageBox.warning(self, "Turnierplan prüfen", f"Die Vorrundengruppen bieten {sum(first_sizes)} Plätze, eingetragen sind aber {count} Teilnehmer.")
            return
        if first_sizes and len(first_sizes) != self.group_count.value():
            QMessageBox.warning(self, "Turnierplan prüfen", "Die Zahl der angegebenen Vorrundengruppen passt nicht zur Gruppenanzahl.")
            return
        second_sizes = self._parse_sizes(self.intermediate_sizes.text()) if self.intermediate.isChecked() else ()
        if second_sizes and sum(second_sizes) != self._projected_qualifiers():
            QMessageBox.warning(self, "Turnierplan prüfen", "Die Zwischenrundenplätze passen noch nicht zur Zahl der Qualifizierten.")
            return
        self.accept()

    def tournament_name(self) -> str: return self.name_edit.text().strip()
    def create_backup(self) -> bool: return self.backup_checkbox.isChecked() and self._has_tournament
    def participant_names(self) -> list[str]: return [line.strip() for line in self.participant_text.toPlainText().splitlines() if line.strip()]
    def settings(self) -> dict[str, object]:
        return {"organizer": self.organizer_edit.text().strip(), "location": self.location_edit.text().strip(),
                "table_count": self.table_count.value(), "group_count": self.group_count.value(),
                "distribution_strategy": self.distribution_strategy.currentData(),
                "qualifiers": self.qualifiers.value(), "qualification": self.qualification.isChecked(),
                "qualifier_mode": self.qualifier_mode.currentData(),
                "first_group_sizes": self._parse_sizes(self.first_group_sizes.text()),
                "intermediate": self.intermediate.isChecked(),
                "intermediate_sizes": self._parse_sizes(self.intermediate_sizes.text()),
                "intermediate_qualifiers": self.intermediate_qualifiers.value(),
                "final_round": self.final_round.isChecked()}



class GroupDropList(QListWidget):
    """A group player list that can hand a player to another group via drag & drop."""

    player_dropped = Signal(object, object, object)

    def __init__(self, group_id: UUID, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.group_id = group_id
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def dragEnterEvent(self, event) -> None:
        if isinstance(event.source(), GroupDropList):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        source = event.source()
        if isinstance(source, GroupDropList) and source.group_id != self.group_id:
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        source = event.source()
        if not isinstance(source, GroupDropList) or source.group_id == self.group_id:
            event.ignore()
            return
        selected = source.selectedItems()
        if not selected:
            event.ignore()
            return
        person_id = selected[0].data(Qt.ItemDataRole.UserRole)
        self.player_dropped.emit(source.group_id, self.group_id, person_id)
        event.acceptProposedAction()


class GroupPreviewDialog(QDialog):
    """Interactive, staged review of the automatically created first group stage."""

    def __init__(
        self,
        service: TournamentEngine,
        phase_id: UUID,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        prepare_dialog(self, "Gruppeneinteilung", width=760)
        self.setMinimumHeight(560)
        self.service = service
        self.tournament = service.require_tournament()
        self.phase_id = phase_id
        self.phase = self.tournament.phase(phase_id)
        self.people = {person.id: person for person in self.tournament.people}
        self.assignments = {
            group.id: list(group.participant_ids)
            for group in self.phase.groups
        }
        self.group_lists: dict[UUID, QListWidget] = {}

        title = QLabel("Gruppeneinteilung prüfen")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        intro = QLabel(
            "Die automatische Verteilung ist noch nicht endgültig. "
            "Ziehe Spieler einfach mit der Maus in eine andere Gruppe. "
            "Alternativ kannst du die Schaltflächen unten verwenden oder komplette Gruppen tauschen. "
            "Gespeichert wird erst mit „Einteilung bestätigen“."
        )
        intro.setWordWrap(True)
        intro.setObjectName("mutedText")

        self.summary = QLabel()
        self.summary.setObjectName("groupPreviewSummary")
        self.fairness_summary = QLabel()
        self.fairness_summary.setObjectName("groupFairnessSummary")
        self.fairness_summary.setWordWrap(True)

        self.cards_layout = QGridLayout()
        self.cards_layout.setHorizontalSpacing(12)
        self.cards_layout.setVerticalSpacing(12)

        scroll_content = QWidget()
        scroll_content.setLayout(self.cards_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(scroll_content)

        tools = QFrame()
        tools.setObjectName("groupPreviewTools")
        tools_layout = QHBoxLayout(tools)
        tools_layout.setContentsMargins(12, 10, 12, 10)
        tools_layout.setSpacing(8)

        source_label = QLabel("Quelle")
        source_label.setObjectName("fieldCaption")
        self.source_group = QComboBox()
        target_label = QLabel("Ziel")
        target_label.setObjectName("fieldCaption")
        self.target_group = QComboBox()
        for group in self.phase.groups:
            self.source_group.addItem(group.name, group.id)
            self.target_group.addItem(group.name, group.id)

        self.move_button = QPushButton("Spieler verschieben →")
        self.move_button.setObjectName("primaryButton")
        self.swap_button = QPushButton("Gruppen tauschen")
        self.swap_button.setObjectName("secondaryButton")
        self.improve_button = QPushButton("Einteilung automatisch verbessern")
        self.improve_button.setObjectName("secondaryButton")
        self.improve_button.setToolTip(
            "Sucht nach Spielertauschen, die Vereins- und Setzlisten-Konflikte reduzieren, "
            "ohne die Gruppengrößen zu verändern."
        )
        self.move_button.clicked.connect(self._move_selected_player)
        self.swap_button.clicked.connect(self._swap_groups)
        self.improve_button.clicked.connect(self._auto_improve)
        self.source_group.currentIndexChanged.connect(self._sync_source_selection)
        self.target_group.currentIndexChanged.connect(self._update_actions)

        tools_layout.addWidget(source_label)
        tools_layout.addWidget(self.source_group)
        tools_layout.addWidget(target_label)
        tools_layout.addWidget(self.target_group)
        tools_layout.addWidget(self.move_button)
        tools_layout.addWidget(self.swap_button)
        tools_layout.addWidget(self.improve_button)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        style_dialog_buttons(self.buttons, "Einteilung bestätigen")
        self.buttons.accepted.connect(self._commit)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addWidget(self.summary)
        layout.addWidget(self.fairness_summary)
        layout.addWidget(scroll, 1)
        layout.addWidget(tools)
        layout.addWidget(self.buttons)

        self._refresh_cards()
        self._update_actions()

    def _group_fairness_issues(self, group_id: UUID, sizes: list[int]) -> list[str]:
        ids = self.assignments[group_id]
        people = [self.people[person_id] for person_id in ids]
        issues: list[str] = []

        if sizes and len(ids) > min(sizes) + 1:
            issues.append("Gruppe deutlich größer")

        clubs: dict[str, int] = {}
        for person in people:
            club = person.club.strip()
            if club:
                key = club.casefold()
                clubs[key] = clubs.get(key, 0) + 1
        repeated_clubs = [count for count in clubs.values() if count >= 2]
        if repeated_clubs:
            maximum = max(repeated_clubs)
            issues.append(f"{maximum} Spieler aus demselben Verein")

        seeded = [person for person in people if person.start_number is not None]
        if len(seeded) >= 2:
            issues.append(f"{len(seeded)} gesetzte Spieler")

        categories: dict[str, int] = {}
        for person in people:
            category = person.category.strip()
            if category and category.casefold() != "offen":
                key = category.casefold()
                categories[key] = categories.get(key, 0) + 1
        if len(people) >= 4 and categories and max(categories.values()) == len(people):
            issues.append("nur eine Kategorie vertreten")

        return issues

    def _group_fairness_rating(self, group_id: UUID, sizes: list[int]) -> tuple[str, str, str]:
        """Return a human-readable rating, explanation and style object name."""
        issues = self._group_fairness_issues(group_id, sizes)
        if not issues:
            return (
                "Sehr gut",
                "Keine auffälligen Vereins-, Setzlisten-, Kategorie- oder Größenkonflikte erkannt.",
                "groupRatingVeryGood",
            )

        severe = 0
        moderate = 0
        for issue in issues:
            lowered = issue.casefold()
            if "deutlich größer" in lowered or "gesetzte spieler" in lowered:
                severe += 1
            elif "demselben verein" in lowered:
                # Three or more club mates is considered a stronger conflict.
                try:
                    count = int(issue.split()[0])
                except (ValueError, IndexError):
                    count = 2
                if count >= 3:
                    severe += 1
                else:
                    moderate += 1
            else:
                moderate += 1

        if severe >= 1 or len(issues) >= 3:
            return (
                "Prüfen",
                " · ".join(issues),
                "groupRatingCheck",
            )
        return (
            "Gut",
            "Kleine Auffälligkeit: " + " · ".join(issues),
            "groupRatingGood",
        )

    def _fairness_report(self, sizes: list[int]) -> tuple[int, int, list[str]]:
        warning_groups = 0
        total_issues = 0
        details: list[str] = []
        for group in self.phase.groups:
            issues = self._group_fairness_issues(group.id, sizes)
            if issues:
                warning_groups += 1
                total_issues += len(issues)
                details.append(f"{group.name}: " + "; ".join(issues))
        return warning_groups, total_issues, details

    def _fairness_score(self) -> int:
        """Lower is better. The score only uses explicit participant metadata."""
        score = 0
        sizes = [len(self.assignments[group.id]) for group in self.phase.groups]
        if sizes:
            score += (max(sizes) - min(sizes)) * 50

        for group in self.phase.groups:
            people = [self.people[person_id] for person_id in self.assignments[group.id]]

            clubs: dict[str, int] = {}
            categories: dict[str, int] = {}
            seeded = 0
            for person in people:
                club = person.club.strip().casefold()
                if club:
                    clubs[club] = clubs.get(club, 0) + 1
                category = person.category.strip().casefold()
                if category and category != "offen":
                    categories[category] = categories.get(category, 0) + 1
                if person.start_number is not None:
                    seeded += 1

            # Penalize repeated club mates strongly.
            score += sum((count - 1) * 30 for count in clubs.values() if count > 1)
            # Multiple seeded players should be spread when possible.
            if seeded > 1:
                score += (seeded - 1) * 40
            # Homogeneous category groups are a weaker warning.
            if len(people) >= 4 and categories and max(categories.values()) == len(people):
                score += 8
        return score

    def _auto_improve(self) -> None:
        """Greedily apply only swaps that strictly improve the fairness score."""
        if len(self.phase.groups) < 2:
            return

        before = self._fairness_score()
        current = before
        swaps = 0
        max_swaps = max(10, len(self.tournament.people) * 2)

        for _ in range(max_swaps):
            best = None
            best_score = current

            groups = list(self.phase.groups)
            for left_index, left_group in enumerate(groups):
                left_ids = list(self.assignments[left_group.id])
                if not left_ids:
                    continue
                for right_group in groups[left_index + 1:]:
                    right_ids = list(self.assignments[right_group.id])
                    if not right_ids:
                        continue
                    for left_person in left_ids:
                        for right_person in right_ids:
                            li = self.assignments[left_group.id].index(left_person)
                            ri = self.assignments[right_group.id].index(right_person)
                            self.assignments[left_group.id][li] = right_person
                            self.assignments[right_group.id][ri] = left_person
                            candidate = self._fairness_score()
                            self.assignments[left_group.id][li] = left_person
                            self.assignments[right_group.id][ri] = right_person

                            if candidate < best_score:
                                best_score = candidate
                                best = (
                                    left_group.id, right_group.id,
                                    left_person, right_person,
                                )

            if best is None:
                break

            left_group_id, right_group_id, left_person, right_person = best
            li = self.assignments[left_group_id].index(left_person)
            ri = self.assignments[right_group_id].index(right_person)
            self.assignments[left_group_id][li] = right_person
            self.assignments[right_group_id][ri] = left_person
            current = best_score
            swaps += 1

        self._refresh_cards()
        if swaps:
            self.fairness_summary.setText(
                f"✓ Automatisch verbessert: {swaps} gezielte Spielertausch/Tausche. "
                f"Fairness-Wert {before} → {current}. Bitte die Gruppen vor dem Bestätigen kurz prüfen."
            )
            self.fairness_summary.setObjectName("groupFairnessOverallOk")
        else:
            self.fairness_summary.setText(
                "✓ Automatische Prüfung: Kein Spielertausch würde die aktuelle Einteilung "
                "nach den vorhandenen Vereins-, Kategorie- und Setzinformationen verbessern."
            )
            self.fairness_summary.setObjectName("groupFairnessOverallOk")
        self.fairness_summary.style().unpolish(self.fairness_summary)
        self.fairness_summary.style().polish(self.fairness_summary)

    def _refresh_cards(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.group_lists.clear()
        sizes = [len(self.assignments[group.id]) for group in self.phase.groups]
        for index, group in enumerate(self.phase.groups):
            ids = self.assignments[group.id]

            card = QFrame()
            card.setObjectName("groupPreviewCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(6)

            heading = QLabel(f"{group.name} · {len(ids)} Spieler")
            heading.setObjectName("groupPreviewTitle")
            card_layout.addWidget(heading)

            rating, explanation, rating_style = self._group_fairness_rating(group.id, sizes)
            rating_row = QHBoxLayout()
            rating_row.setSpacing(8)
            rating_label = QLabel(rating)
            rating_label.setObjectName(rating_style)
            rating_text = QLabel(explanation)
            rating_text.setObjectName("groupRatingExplanation")
            rating_text.setWordWrap(True)
            rating_row.addWidget(rating_label)
            rating_row.addWidget(rating_text, 1)
            card_layout.addLayout(rating_row)

            player_list = GroupDropList(group.id)
            player_list.setObjectName("groupPreviewList")
            player_list.setMinimumHeight(105)
            for person_id in ids:
                person = self.people[person_id]
                suffix = []
                if person.club:
                    suffix.append(person.club)
                if person.category and person.category != "Offen":
                    suffix.append(person.category)
                text = person.full_name
                if suffix:
                    text += "  ·  " + " · ".join(suffix)
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, person_id)
                player_list.addItem(item)
            player_list.itemSelectionChanged.connect(
                lambda gid=group.id: self._card_selection_changed(gid)
            )
            player_list.player_dropped.connect(self._drag_move_player)
            self.group_lists[group.id] = player_list
            card_layout.addWidget(player_list)

            self.cards_layout.addWidget(card, index // 2, index % 2)

        if sizes:
            smallest, largest = min(sizes), max(sizes)
            balance = "gleichmäßig" if largest - smallest <= 1 else "prüfen"
            self.summary.setText(
                f"{len(self.tournament.people)} Teilnehmer · {len(self.phase.groups)} Gruppen · "
                f"{self.tournament.table_count} Felder · Verteilung: {balance}"
            )
            warning_groups, total_issues, details = self._fairness_report(sizes)
            ratings = [
                self._group_fairness_rating(group.id, sizes)[0]
                for group in self.phase.groups
            ]
            very_good = ratings.count("Sehr gut")
            good = ratings.count("Gut")
            check = ratings.count("Prüfen")
            if warning_groups == 0:
                self.fairness_summary.setObjectName("groupFairnessOverallOk")
                self.fairness_summary.setText(
                    f"✓ Fairness: {very_good}× Sehr gut · {good}× Gut · {check}× Prüfen"
                )
            else:
                self.fairness_summary.setObjectName(
                    "groupFairnessOverallWarning" if check else "groupFairnessOverallOk"
                )
                self.fairness_summary.setText(
                    f"Fairness: {very_good}× Sehr gut · {good}× Gut · {check}× Prüfen · "
                    f"{total_issues} Hinweis(e) insgesamt"
                )
            # Repolish after changing the dynamic object name.
            self.fairness_summary.style().unpolish(self.fairness_summary)
            self.fairness_summary.style().polish(self.fairness_summary)
            self.fairness_summary.setToolTip("\n".join(details))
        else:
            self.summary.setText("Keine Gruppen vorhanden")
            self.fairness_summary.setText("Keine Fairness-Prüfung möglich.")

        self._sync_source_selection()
        self._update_actions()

    def _card_selection_changed(self, group_id: UUID) -> None:
        current = self.group_lists[group_id]
        if not current.selectedItems():
            return
        for other_id, widget in self.group_lists.items():
            if other_id != group_id:
                widget.clearSelection()
        index = self.source_group.findData(group_id)
        if index >= 0:
            self.source_group.blockSignals(True)
            self.source_group.setCurrentIndex(index)
            self.source_group.blockSignals(False)
        self._update_actions()

    def _sync_source_selection(self) -> None:
        source_id = self.source_group.currentData()
        if source_id is None:
            return
        for group_id, widget in self.group_lists.items():
            if group_id != source_id:
                widget.clearSelection()
        self._update_actions()

    def _selected_person_id(self) -> UUID | None:
        source_id = self.source_group.currentData()
        widget = self.group_lists.get(source_id)
        if widget is None:
            return None
        selected = widget.selectedItems()
        if not selected:
            return None
        return selected[0].data(Qt.ItemDataRole.UserRole)

    def _update_actions(self) -> None:
        source_id = self.source_group.currentData()
        target_id = self.target_group.currentData()
        different = source_id is not None and target_id is not None and source_id != target_id
        self.move_button.setEnabled(different and self._selected_person_id() is not None)
        self.swap_button.setEnabled(different)
        non_empty = sum(1 for ids in self.assignments.values() if ids)
        self.improve_button.setEnabled(non_empty >= 2)

    def _drag_move_player(self, source_id: UUID, target_id: UUID, person_id: UUID) -> None:
        if source_id == target_id or person_id not in self.assignments.get(source_id, []):
            return
        self.assignments[source_id].remove(person_id)
        self.assignments[target_id].append(person_id)
        self._refresh_cards()
        target_index = self.source_group.findData(target_id)
        if target_index >= 0:
            self.source_group.setCurrentIndex(target_index)
        target_list = self.group_lists.get(target_id)
        if target_list is not None:
            for row in range(target_list.count()):
                item = target_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == person_id:
                    item.setSelected(True)
                    target_list.scrollToItem(item)
                    break

    def _move_selected_player(self) -> None:
        source_id = self.source_group.currentData()
        target_id = self.target_group.currentData()
        person_id = self._selected_person_id()
        if source_id is None or target_id is None or person_id is None or source_id == target_id:
            return
        if person_id not in self.assignments[source_id]:
            return
        self.assignments[source_id].remove(person_id)
        self.assignments[target_id].append(person_id)
        self._refresh_cards()
        target_index = self.source_group.findData(target_id)
        if target_index >= 0:
            self.source_group.setCurrentIndex(target_index)

    def _swap_groups(self) -> None:
        source_id = self.source_group.currentData()
        target_id = self.target_group.currentData()
        if source_id is None or target_id is None or source_id == target_id:
            return
        self.assignments[source_id], self.assignments[target_id] = (
            self.assignments[target_id],
            self.assignments[source_id],
        )
        self._refresh_cards()

    def _commit(self) -> None:
        try:
            self.service.apply_group_assignments(self.phase_id, self.assignments)
        except ValidationError as error:
            show_error(self, error)
            return
        self.accept()



class TournamentStartCheckDialog(QDialog):
    """Compact preflight check before the first schedule is generated."""

    def __init__(
        self,
        tournament: Tournament,
        phase,
        *,
        qualification_active: bool,
        intermediate_active: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        prepare_dialog(self, "Turnierstart prüfen", width=620)
        self.setMinimumHeight(470)

        # Compatibility with older tournament project files: fields added in later
        # versions may be absent on deserialized objects. The preflight check must
        # never crash because of that.
        table_count = int(getattr(tournament, "table_count", 3) or 3)
        phases = list(getattr(tournament, "phases", []) or [])
        people = list(getattr(tournament, "people", []) or [])

        assigned_ids = {
            person_id
            for group in phase.groups
            for person_id in group.participant_ids
        }
        total = len(people)
        assigned = len(assigned_ids)
        unassigned = max(0, total - assigned)
        playable = [group for group in phase.groups if len(group.participant_ids) >= 2]
        empty_or_small = [group for group in phase.groups if len(group.participant_ids) < 2]

        blockers: list[str] = []
        warnings: list[str] = []
        if total < 2:
            blockers.append("Mindestens zwei Teilnehmer sind erforderlich.")
        if not phase.groups:
            blockers.append("Es sind noch keine Gruppen angelegt.")
        if not playable:
            blockers.append("Mindestens eine Gruppe benötigt zwei oder mehr Spieler.")
        if table_count < 1:
            blockers.append("Es muss mindestens eine Feld verfügbar sein.")
        if unassigned:
            warnings.append(f"{unassigned} Teilnehmer sind noch keiner Gruppe zugeordnet.")
        if empty_or_small and playable:
            warnings.append(
                f"{len(empty_or_small)} Gruppe(n) haben weniger als zwei Spieler und erzeugen keine Begegnungen."
            )
        if table_count > max(1, len(playable)):
            warnings.append(
                f"{table_count} Felder sind eingestellt, aber nur {len(playable)} Gruppe(n) sind spielbereit."
            )

        route = ["Gruppenphase"]
        if qualification_active:
            route.append("Qualifikation")
        if intermediate_active:
            route.append("Zwischenrunde")
        final_round = any(item.phase_type is PhaseType.FINAL_ROUND for item in phases)
        if final_round:
            route.append("K.-o.-Phase")

        title = QLabel("Bereit für den Turnierstart?")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        intro = QLabel(
            "MSTTS prüft nur Punkte, die für einen sicheren Start relevant sind. "
            "Hinweise blockieren den Start nicht."
        )
        intro.setWordWrap(True)
        intro.setObjectName("mutedText")

        summary = QLabel(
            f"{total} Teilnehmer · {len(phase.groups)} Gruppen · "
            f"{len(playable)} spielbereit · {table_count} Felder"
        )
        summary.setObjectName("startCheckSummary")

        route_label = QLabel("Turnierweg: " + " → ".join(route))
        route_label.setObjectName("startCheckRoute")
        route_label.setWordWrap(True)

        checks = QFrame()
        checks.setObjectName("startCheckPanel")
        checks_layout = QVBoxLayout(checks)
        checks_layout.setContentsMargins(14, 12, 14, 12)
        checks_layout.setSpacing(6)

        positive = [
            f"✓ {assigned}/{total} Teilnehmer Gruppen zugeordnet",
            f"✓ {len(playable)} spielbereite Gruppe(n)",
            f"✓ {table_count} Feld(n) verfügbar",
        ]
        for text in positive:
            label = QLabel(text)
            label.setObjectName("startCheckOk")
            checks_layout.addWidget(label)

        for text in warnings:
            label = QLabel("Hinweis: " + text)
            label.setObjectName("startCheckWarning")
            label.setWordWrap(True)
            checks_layout.addWidget(label)

        for text in blockers:
            label = QLabel("Problem: " + text)
            label.setObjectName("startCheckBlocker")
            label.setWordWrap(True)
            checks_layout.addWidget(label)

        if not warnings and not blockers:
            state = QLabel("✓ Keine Probleme erkannt. Der Spielplan kann erstellt werden.")
            state.setObjectName("startCheckReady")
        elif blockers:
            state = QLabel(
                f"{len(blockers)} Problem(e) müssen vor dem Start behoben werden."
            )
            state.setObjectName("startCheckBlocked")
        else:
            state = QLabel(
                f"{len(warnings)} Hinweis(e) – der Turnierstart ist trotzdem möglich."
            )
            state.setObjectName("startCheckNotice")
        state.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        style_dialog_buttons(buttons, "Turnier starten")
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setEnabled(not blockers)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addWidget(summary)
        layout.addWidget(route_label)
        layout.addWidget(checks)
        layout.addWidget(state)
        layout.addStretch()
        layout.addWidget(buttons)

class PersonDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, first_name: str = "", last_name: str = "", club: str = "", category: str = "Offen", start_number: int | None = None, license_number: str = "") -> None:
        super().__init__(parent)
        prepare_dialog(self, "Teilnehmer bearbeiten", width=500)
        self.first_name = QLineEdit(first_name)
        self.last_name = QLineEdit(last_name)
        self.club = QLineEdit(club)
        self.category = QComboBox()
        self.category.addItems(["Offen", "Herren", "Damen", "Amateur", "Jugend U13", "Jugend U15", "Jugend U18", "Senioren"])
        self.category.setCurrentText(category)
        self.start_number = QLineEdit("" if start_number is None else str(start_number))
        self.start_number.setValidator(QIntValidator(1, 999999, self))
        self.license_number = QLineEdit(license_number)
        form = QFormLayout(self)
        form.addRow("Vorname", self.first_name)
        form.addRow("Nachname", self.last_name)
        form.addRow("Verein", self.club)
        form.addRow("Kategorie", self.category)
        form.addRow("Startnummer", self.start_number)
        form.addRow("Lizenznummer", self.license_number)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        style_dialog_buttons(buttons, "Speichern")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, str, str, str, int | None, str]:
        number = self.start_number.text().strip()
        return self.first_name.text(), self.last_name.text(), self.club.text(), self.category.currentText(), int(number) if number else None, self.license_number.text()


class BulkPlayerInputDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        prepare_dialog(self, "Namensliste einfügen", width=580)
        self.setMinimumHeight(420)
        info = QLabel(
            "Eine Person pro Zeile. Erlaubt sind „Vorname Nachname“ oder "
            "„Vorname;Nachname;Verein;Kategorie“. Doppelte Namen werden übersprungen."
        )
        info.setWordWrap(True)
        self.text = QTextEdit()
        self.text.setPlaceholderText(
            "Petra Lühr\nBella Betz\nKarl;Kinzel;TSV Beispiel;Herren"
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        style_dialog_buttons(buttons, "Teilnehmer übernehmen")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.text)
        layout.addWidget(buttons)

    def entries(self) -> list[tuple[str, str, str, str]]:
        result: list[tuple[str, str, str, str]] = []
        for line_number, raw in enumerate(self.text.toPlainText().splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            if ";" in line:
                parts = [part.strip() for part in line.split(";")]
                if len(parts) > 4:
                    raise ValidationError(f"Zeile {line_number}: zu viele Felder.")
                parts += [""] * (4 - len(parts))
                first, last, club, category = parts
            else:
                parts = line.split()
                if len(parts) < 2:
                    raise ValidationError(f"Zeile {line_number}: Bitte Vor- und Nachname eingeben.")
                first, last = parts[0], " ".join(parts[1:])
                club, category = "", "Offen"
            result.append((first, last, club, category or "Offen"))
        if not result:
            raise ValidationError("Die Namensliste enthält keine Spieler.")
        return result


class TournamentDetailsDialog(QDialog):
    def __init__(self, tournament: Tournament, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        prepare_dialog(self, "Turnierdetails", width=520)
        self.organizer = QLineEdit(tournament.organizer)
        self.location = QLineEdit(tournament.location)
        self.start_date = QLineEdit(tournament.start_date)
        self.start_date.setPlaceholderText("z. B. 20.07.2026")
        self.end_date = QLineEdit(tournament.end_date)
        self.end_date.setPlaceholderText("optional")
        self.table_count = QSpinBox(); self.table_count.setRange(1, 16); self.table_count.setValue(tournament.table_count); self.table_count.setSuffix(" Felder")
        self.match_duration = QSpinBox(); self.match_duration.setRange(5, 180); self.match_duration.setValue(tournament.match_duration_minutes); self.match_duration.setSuffix(" Minuten")
        self.scorer_count = QSpinBox(); self.scorer_count.setRange(0, 99); self.scorer_count.setValue(tournament.scorer_count); self.scorer_count.setSuffix(" Schiedsrichter")
        self.scorer_names = QTextEdit()
        self.scorer_names.setPlaceholderText("Ein Name pro Zeile, z. B.\nAnne\nAnja\nTim\nFrank")
        self.scorer_names.setPlainText("\n".join(getattr(tournament, "scorer_names", [])))
        self.scorer_names.setMaximumHeight(110)
        self.scorer_names.setToolTip("Die Namen werden automatisch und möglichst gleichmäßig auf die geplanten Felder verteilt. Spieler werden zu ihren eigenen Spielzeiten nicht als Schiedsrichter eingeteilt.")
        self.best_of = QComboBox(); self.best_of.addItems(["Best of 3", "Best of 5", "Best of 7"]); self.best_of.setCurrentText(f"Best of {tournament.best_of}")
        form = QFormLayout(self)
        form.addRow("Veranstalter", self.organizer)
        form.addRow("Ort", self.location)
        form.addRow("Beginn", self.start_date)
        form.addRow("Ende", self.end_date)
        form.addRow("Anzahl der Felder", self.table_count)
        form.addRow("Spieldauer", self.match_duration)
        form.addRow("Schiedsrichter", self.scorer_count)
        form.addRow("Schiedsrichter-Namen", self.scorer_names)
        hint = QLabel("Automatische faire Verteilung: gleiche Einsatzanzahl, keine Doppelbelegung und keine Einteilung während eines eigenen Spiels.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #BCA8D9; font-size: 11px;")
        form.addRow("", hint)
        form.addRow("Spielmodus", self.best_of)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        style_dialog_buttons(buttons, "Speichern")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, str, str, str, int, int, int, int, list[str]]:
        names = [line.strip() for line in self.scorer_names.toPlainText().splitlines() if line.strip()]
        count = max(self.scorer_count.value(), len(names))
        return (self.organizer.text(), self.location.text(), self.start_date.text(), self.end_date.text(), self.table_count.value(), self.match_duration.value(), count, int(self.best_of.currentText().split()[-1]), names)


class ScorekeeperPage(QWidget):
    """Dedicated preparation page for scorekeeper management and fair allocation."""

    def __init__(self, service: TournamentEngine, saved: Callable[[], None]) -> None:
        super().__init__()
        self.service = service
        self.saved_callback = saved
        self.setObjectName("scorekeeperContent")
        self._name_edits: list[QLineEdit] = []

        # STARTFIX 157: approved premium scorekeeper workspace.
        self.setStyleSheet(r"""
            QWidget#scorekeeperContent { background:transparent; color:#F8F3FF; }
            QFrame#scorekeeperHero, QFrame#scorekeeperPanel, QFrame#scorekeeperActions, QFrame#scorekeeperHint {
                background:rgba(43,22,14,0.96); border:1px solid #5E2A8A; border-radius:14px;
            }
            QLabel#scorekeeperEyebrow { background:transparent; border:none; color:#D99B62; font-weight:900; letter-spacing:1px; }
            QLabel#scorekeeperTitle { background:transparent; border:none; color:#FFF6ED; font-size:26px; font-weight:950; }
            QLabel#scorekeeperSubtitle { background:transparent; border:none; color:#D9B79B; font-size:14px; }
            QLabel#scorekeeperSectionTitle { background:transparent; border:none; color:#F0BE91; font-size:16px; font-weight:900; }
            QLabel#scorekeeperCount { background:transparent; border:none; color:#E8AA73; font-weight:900; }
            QFrame#scorekeeperNameRow {
                background:#170A2E; border:1px solid #7337B4; border-radius:10px;
            }
            QLineEdit#scorekeeperNameEdit {
                background:transparent; color:#FFF2E7; border:none; padding:8px 10px; font-size:14px;
            }
            QPushButton#scorekeeperRowRemove {
                background:transparent; color:#E77A6A; border:none; font-size:16px; font-weight:900; padding:6px 9px;
            }
            QPushButton#scorekeeperRowRemove:hover { background:#31105F; border-radius:8px; }
            QPushButton#scorekeeperAdd, QPushButton#scorekeeperPrimary, QPushButton#scorekeeperSecondary, QPushButton#scorekeeperDanger {
                min-height:42px; border-radius:10px; padding:9px 14px; font-weight:900;
            }
            QPushButton#scorekeeperAdd, QPushButton#scorekeeperPrimary {
                background:#8D2CFF; color:#FFFFFF; border:1px solid #B85CFF;
            }
            QPushButton#scorekeeperAdd:hover, QPushButton#scorekeeperPrimary:hover { background:#A33BFF; }
            QPushButton#scorekeeperSecondary {
                background:#14082E; color:#EBDFFF; border:1px solid #8147C0;
            }
            QPushButton#scorekeeperSecondary:hover { background:#261050; border-color:#A934FF; }
            QPushButton#scorekeeperDanger {
                background:#27102F; color:#F08BDE; border:1px solid #8E3D8F;
            }
            QPushButton#scorekeeperDanger:hover { background:#481A14; }
            QLabel#scorekeeperHintTitle { background:transparent; border:none; color:#E6A66D; font-weight:900; }
            QLabel#scorekeeperHintText { background:transparent; border:none; color:#D8B89D; }
            QTableWidget {
                background:#25120C; alternate-background-color:#2F180F; color:#F7E5D5;
                border:1px solid #7337B4; border-radius:10px; gridline-color:#3B1B5D;
            }
            QHeaderView::section {
                background:#21103B; color:#E7D8FA; border:none; border-right:1px solid #7337B4;
                padding:7px; font-weight:800;
            }
            QTableWidget::item:selected { background:#6F25B7; color:#FFFFFF; }
            QTableCornerButton::section { background:#21103B; border:none; border-right:1px solid #7337B4; border-bottom:1px solid #7337B4; }
            QScrollBar:vertical { background:#05020D; width:11px; margin:0; border:none; }
            QScrollBar:horizontal { background:#05020D; height:11px; margin:0; border:none; }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#7B36AF; border:1px solid #A94CFF; border-radius:5px; min-height:30px; min-width:30px; }
            QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background:#05020D; border:none; width:0; height:0; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 24)
        layout.setSpacing(16)

        hero = QFrame(); hero.setObjectName("scorekeeperHero")
        hero_layout = QVBoxLayout(hero); hero_layout.setContentsMargins(20, 16, 20, 16); hero_layout.setSpacing(4)
        eyebrow = QLabel("TURNIERVORBEREITUNG  ·  PUNKTEZÄHLER"); eyebrow.setObjectName("scorekeeperEyebrow")
        title = QLabel("Schiedsrichter"); title.setObjectName("scorekeeperTitle")
        intro = QLabel("Namen verwalten und automatisch fair auf die geplanten Felder verteilen."); intro.setObjectName("scorekeeperSubtitle")
        hero_layout.addWidget(eyebrow); hero_layout.addWidget(title); hero_layout.addWidget(intro)
        layout.addWidget(hero)

        content_row = QHBoxLayout(); content_row.setSpacing(16)

        names_panel = QFrame(); names_panel.setObjectName("scorekeeperPanel")
        names_layout = QVBoxLayout(names_panel); names_layout.setContentsMargins(16, 14, 16, 16); names_layout.setSpacing(10)
        names_head = QHBoxLayout()
        label = QLabel("👥  Schiedsrichter-Namen"); label.setObjectName("scorekeeperSectionTitle")
        self.count_label = QLabel(); self.count_label.setObjectName("scorekeeperCount")
        names_head.addWidget(label); names_head.addStretch(); names_head.addWidget(self.count_label)
        names_layout.addLayout(names_head)
        self.name_rows = QVBoxLayout(); self.name_rows.setSpacing(7)
        names_layout.addLayout(self.name_rows)
        add_button = QPushButton("＋  Schiedsrichter hinzufügen"); add_button.setObjectName("scorekeeperAdd"); add_button.clicked.connect(self.add_scorekeeper)
        names_layout.addWidget(add_button)

        # Hidden compatibility backing field: older internal helpers refer to self.names.
        self.names = QTextEdit(); self.names.hide()
        names_layout.addWidget(self.names)

        actions = QFrame(); actions.setObjectName("scorekeeperActions")
        actions_layout = QVBoxLayout(actions); actions_layout.setContentsMargins(16, 14, 16, 16); actions_layout.setSpacing(12)
        action_title = QLabel("⚙  Aktionen"); action_title.setObjectName("scorekeeperSectionTitle")
        distribute = QPushButton("⤨  Automatisch fair verteilen\nAuf alle geplanten Felder verteilen")
        distribute.setObjectName("scorekeeperPrimary"); distribute.clicked.connect(self.refresh_assignments)
        save = QPushButton("▣  Schiedsrichter speichern"); save.setObjectName("scorekeeperSecondary"); save.clicked.connect(self.save_names)
        clear = QPushButton("▥  Alle Schiedsrichter entfernen"); clear.setObjectName("scorekeeperDanger"); clear.clicked.connect(self.clear_scorekeepers)
        actions_layout.addWidget(action_title); actions_layout.addWidget(distribute); actions_layout.addWidget(save); actions_layout.addWidget(clear); actions_layout.addStretch()

        content_row.addWidget(names_panel, 3); content_row.addWidget(actions, 2)
        layout.addLayout(content_row)

        hint_card = QFrame(); hint_card.setObjectName("scorekeeperHint")
        hint_layout = QVBoxLayout(hint_card); hint_layout.setContentsMargins(16, 12, 16, 12); hint_layout.setSpacing(4)
        hint_title = QLabel("●  Hinweis"); hint_title.setObjectName("scorekeeperHintTitle")
        hint = QLabel("Automatik: möglichst gleiche Einsatzanzahl, keine Doppelbelegung im selben Zeitslot und keine Einteilung während eines eigenen Spiels. Die Verteilung berücksichtigt die geplanten Feld- und Spielzeiten des aktuellen Turniertages.")
        hint.setWordWrap(True); hint.setObjectName("scorekeeperHintText")
        hint_layout.addWidget(hint_title); hint_layout.addWidget(hint)
        layout.addWidget(hint_card)

        self.stats = QLabel(); self.stats.setObjectName("scorekeeperSubtitle")
        layout.addWidget(self.stats)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Zeit", "Feld", "Phase / Gruppe", "Spieler 1", "Spieler 2", "Schiedsrichter"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(180)
        layout.addWidget(self.table, 1)
        self.refresh()

    def _clear_name_rows(self) -> None:
        while self.name_rows.count():
            item = self.name_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._name_edits.clear()

    def _append_name_row(self, name: str = "") -> None:
        row_widget = QFrame(); row_widget.setObjectName("scorekeeperNameRow")
        row = QHBoxLayout(row_widget); row.setContentsMargins(8, 3, 6, 3); row.setSpacing(6)
        grip = QLabel("⋮⋮"); grip.setStyleSheet("color:#C05CFF; font-weight:900; background:transparent;")
        edit = QLineEdit(name); edit.setObjectName("scorekeeperNameEdit"); edit.setPlaceholderText("Name des Schiedsrichters")
        remove = QPushButton("✕"); remove.setObjectName("scorekeeperRowRemove")
        remove.clicked.connect(lambda _=False, w=row_widget, e=edit: self.remove_scorekeeper(w, e))
        row.addWidget(grip); row.addWidget(edit, 1); row.addWidget(remove)
        self.name_rows.addWidget(row_widget)
        self._name_edits.append(edit)

    def _current_names(self) -> list[str]:
        return normalize_scorer_names([edit.text() for edit in self._name_edits])

    def _sync_backing_text(self) -> list[str]:
        names = self._current_names()
        self.names.setPlainText("\n".join(names))
        self.count_label.setText(f"{len(names)} Schiedsrichter eingetragen")
        return names

    def add_scorekeeper(self) -> None:
        self._append_name_row("")
        self._sync_backing_text()
        if self._name_edits:
            self._name_edits[-1].setFocus()

    def remove_scorekeeper(self, row_widget: QWidget, edit: QLineEdit) -> None:
        if edit in self._name_edits:
            self._name_edits.remove(edit)
        self.name_rows.removeWidget(row_widget)
        row_widget.deleteLater()
        self._sync_backing_text()

    def clear_scorekeepers(self) -> None:
        answer = QMessageBox.question(
            self, "Schiedsrichter entfernen",
            "Sollen wirklich alle Schiedsrichter aus der Liste entfernt werden?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._clear_name_rows()
        self._sync_backing_text()

    def refresh(self) -> None:
        tournament = self.service.require_tournament()
        names = list(getattr(tournament, "scorer_names", []))
        self._clear_name_rows()
        for name in names:
            self._append_name_row(name)
        if not names:
            self._append_name_row("")
        self._sync_backing_text()
        self.refresh_assignments()

    def save_names(self) -> None:
        tournament = self.service.require_tournament()
        names = self._sync_backing_text()
        self.service.update_tournament_details(
            tournament.organizer, tournament.location, tournament.start_date, tournament.end_date,
            tournament.table_count, tournament.match_duration_minutes, len(names), tournament.best_of, names,
        )
        self.saved_callback()
        self.refresh_assignments()

    def refresh_assignments(self) -> None:
        tournament = self.service.require_tournament()
        names = self._sync_backing_text()
        # Keep fair-distribution preview in sync with unsaved edits without permanently
        # changing tournament data.  Previously the preview silently used only the last
        # saved list, so newly typed names did not appear until after saving.
        saved_names = list(getattr(tournament, "scorer_names", []))
        tournament.scorer_names = names
        try:
            assignments = build_scorekeeper_assignments(tournament)
        finally:
            tournament.scorer_names = saved_names
        people = {person.id: person.full_name for person in tournament.people}
        rows = []
        for phase in sorted(tournament.phases, key=lambda x: x.position):
            if phase.phase_type is PhaseType.GROUP_STAGE:
                sources = [(group.name, match) for group in phase.groups for match in group.matches]
                sources += [(f"{group.name} Qualifikation", group.playoff_match) for group in phase.groups if group.playoff_match]
            else:
                sources = [("K.-o.-Runde", match) for match in phase.matches]
            for group_name, match in sources:
                a = assignments.get(match.id)
                rows.append((match.scheduled_time or "—", match.table_number or "—", f"{phase.name} · {group_name}", people.get(match.home_id, "—"), people.get(match.away_id, "—"), a.scorer_name if a else "offen"))
        rows.sort(key=lambda r: (r[0] == "—", r[0], 999 if r[1] == "—" else int(r[1])))
        self.table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, value in enumerate(values): self.table.setItem(r, c, QTableWidgetItem(str(value)))
        counts = {name: 0 for name in names}
        for assignment in assignments.values():
            if assignment.scorer_name in counts:
                counts[assignment.scorer_name] += 1
        if counts:
            summary = " · ".join(f"{name}: {count}" for name, count in counts.items())
            self.stats.setText(f"Geplante Einsätze: {summary}")
        else:
            self.stats.setText("Noch keine Schiedsrichter gespeichert.")


class StartPage(QWidget):
    """Reduced professional landing page for project creation and reopening."""

    def __init__(
        self,
        create_tournament: Callable[[], None],
        open_tournament: Callable[[], None],
        open_recent_tournament: Callable[[Path], None],
        recent_tournaments: tuple[Path, ...],
        create_demo_tournament: Callable[[], None],
        create_freudenholm_demo: Callable[[], None],
    ) -> None:
        super().__init__()
        self.setObjectName("startPage")

        shell = QFrame()
        shell.setObjectName("startPageShell")
        shell.setStyleSheet("""
            QFrame#startPageShell { background:#120724; border:1px solid #7132A8; border-radius:18px; }
            QFrame#startPageShell QLabel { background:transparent; border:none; color:#F7F1FF; }
            QLabel#startEyebrow { color:#E455FF; font-weight:900; }
            QLabel#startTitle { color:#FFFFFF; font-size:32px; font-weight:900; }
            QLabel#mutedText { color:#CDBBE8; }
            QLabel#startSectionTitle { color:#E455FF; font-weight:900; }
            QLabel#startEmptyRecent { background:#0B0418; color:#BFAED7; border:1px solid #452066; border-radius:10px; padding:16px; }
            QPushButton#startPrimaryButton { background:#8B24D8; color:#FFFFFF; border:1px solid #E05BFF; border-radius:12px; font-weight:900; padding:12px 18px; }
            QPushButton#startPrimaryButton:hover { background:#A52CEB; }
            QPushButton#startSecondaryButton { background:#21103B; color:#FFFFFF; border:1px solid #7B36AF; border-radius:12px; font-weight:850; padding:12px 18px; }
            QPushButton#startSecondaryButton:hover { background:#321653; border-color:#C04AF3; }
            QListWidget#recentTournamentList { background:#0B0418; color:#F5EEFF; border:1px solid #452066; border-radius:10px; padding:8px; }
        """)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(34, 30, 34, 30)
        shell_layout.setSpacing(18)

        eyebrow = QLabel("MSBTS · BADMINTON")
        eyebrow.setObjectName("startEyebrow")
        title = QLabel("Badminton-Turnierverwaltung")
        title.setObjectName("startTitle")
        subtitle = QLabel("Turniere planen, spielen und auswerten – modern, klar und vollständig in MSBTS.")
        subtitle.setObjectName("mutedText")
        subtitle.setWordWrap(True)
        shell_layout.addWidget(eyebrow)
        shell_layout.addWidget(title)
        shell_layout.addWidget(subtitle)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        new_button = QPushButton("＋  Neues Turnier")
        new_button.setObjectName("startPrimaryButton")
        new_button.setMinimumHeight(46)
        new_button.clicked.connect(create_tournament)
        open_button = QPushButton("Turnier öffnen …")
        open_button.setObjectName("startSecondaryButton")
        open_button.setMinimumHeight(46)
        open_button.clicked.connect(open_tournament)
        actions.addWidget(new_button)
        actions.addWidget(open_button)
        shell_layout.addLayout(actions)

        recent_title = QLabel("ZULETZT VERWENDET")
        recent_title.setObjectName("startSectionTitle")
        shell_layout.addWidget(recent_title)

        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recentTournamentList")
        self.recent_list.setAlternatingRowColors(False)
        self.recent_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.recent_list.setMinimumHeight(124)
        self.recent_list.setMaximumHeight(190)
        if recent_tournaments:
            for path in recent_tournaments:
                item = QListWidgetItem(path.stem)
                item.setToolTip(str(path))
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                self.recent_list.addItem(item)
            self.recent_list.itemDoubleClicked.connect(
                lambda item: open_recent_tournament(Path(item.data(Qt.ItemDataRole.UserRole)))
            )
            open_recent_button = QPushButton("Ausgewähltes Turnier öffnen")
            open_recent_button.setObjectName("secondaryButton")
            open_recent_button.clicked.connect(
                lambda: self._open_selected_recent(open_recent_tournament)
            )
            shell_layout.addWidget(self.recent_list)
            shell_layout.addWidget(open_recent_button, alignment=Qt.AlignmentFlag.AlignLeft)
        else:
            self.recent_list.setVisible(False)
            empty = QLabel("Noch keine zuletzt verwendeten Turnierdateien.")
            empty.setObjectName("startEmptyRecent")
            shell_layout.addWidget(empty)

        demo_title = QLabel("BADMINTON-DEMOTURNIER")
        demo_title.setObjectName("startSectionTitle")
        shell_layout.addWidget(demo_title)

        demo_card = QFrame()
        demo_card.setStyleSheet("QFrame { background:#180A2E; border:1px solid #6E31A7; border-radius:12px; }")
        demo_layout = QHBoxLayout(demo_card)
        demo_layout.setContentsMargins(18, 14, 18, 14)
        demo_layout.setSpacing(14)
        demo_text = QVBoxLayout()
        demo_name = QLabel("Komplettes Badminton-Testturnier")
        demo_name.setStyleSheet("color:#FFFFFF; font-weight:900; font-size:15px;")
        demo_info = QLabel("21 Spieler · 5 Gruppen · Qualifikation · Zwischenrunde · K.-o.-Phase")
        demo_info.setObjectName("mutedText")
        demo_info.setWordWrap(True)
        demo_text.addWidget(demo_name)
        demo_text.addWidget(demo_info)
        demo_button = QPushButton("▶  Demo-Turnier starten")
        demo_button.setObjectName("startPrimaryButton")
        demo_button.setMinimumHeight(44)
        demo_button.clicked.connect(create_demo_tournament)
        demo_layout.addLayout(demo_text, 1)
        demo_layout.addWidget(demo_button)
        shell_layout.addWidget(demo_card)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addStretch()
        layout.addWidget(shell)
        layout.addStretch()

    def _open_selected_recent(self, callback: Callable[[Path], None]) -> None:
        item = self.recent_list.currentItem()
        if item is None:
            return
        callback(Path(item.data(Qt.ItemDataRole.UserRole)))



class WorkflowPage(QWidget):
    """Compact, guided assistant with a visible workflow and one recommended action."""

    STEP_TITLES = ("Teilnehmer", "Gruppen", "Spielplan", "Ergebnisse", "Turniertag")

    def __init__(
        self,
        service: TournamentEngine,
        open_players: Callable[[], None],
        open_groups: Callable[[], None],
        create_schedule: Callable[[], None],
        open_results: Callable[[], None],
        open_tournament_day: Callable[[], None],
    ) -> None:
        super().__init__()
        self.setObjectName("workflowPage")
        self.service = service
        self._callbacks = (
            open_players,
            open_groups,
            create_schedule,
            open_results,
            open_tournament_day,
        )

        title = QLabel("Turnierassistent")
        title.setObjectName("wizardTitle")
        title.setVisible(False)
        subtitle = QLabel("MSTTS führt dich durch die Vorbereitung und zeigt immer nur den nächsten sinnvollen Schritt.")
        subtitle.setObjectName("mutedText")
        subtitle.setWordWrap(True)

        self.progress_labels: list[QLabel] = []
        progress = QHBoxLayout()
        progress.setSpacing(6)
        for index, step_title in enumerate(self.STEP_TITLES, start=1):
            label = QLabel(f"{index}  {step_title}")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(38)
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.progress_labels.append(label)
            progress.addWidget(label)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setObjectName("wizardStatus")
        self.summary.setStyleSheet("padding: 4px 0; font-weight: 600;")

        self.next_title = QLabel()
        self.next_title.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.next_description = QLabel()
        self.next_description.setWordWrap(True)
        self.next_button = QPushButton()
        self.next_button.setObjectName("primaryAction")
        self.next_button.setMinimumHeight(48)
        self.next_button.clicked.connect(self._open_recommended_step)
        self._recommended_step = 0

        next_card = QFrame()
        next_card.setObjectName("wizardNextCard")
        next_card.setStyleSheet(
            "QFrame#wizardNextCard { background:#160A2B; border:1px solid #5E2A8A; border-radius:12px; } "
            "QFrame#wizardNextCard QLabel { background:transparent; color:#F2EBFF; border:none; }"
        )
        next_layout = QVBoxLayout(next_card)
        next_layout.setContentsMargins(16, 14, 16, 14)
        next_layout.setSpacing(7)
        next_layout.addWidget(QLabel("EMPFOHLENER NÄCHSTER SCHRITT"))
        next_layout.addWidget(self.next_title)
        next_layout.addWidget(self.next_description)
        next_layout.addWidget(self.next_button)

        self.check_title = QLabel("TURNIER-CHECK")
        self.check_title.setStyleSheet("font-size: 13px; font-weight: 800;")
        self.checklist = QLabel()
        self.checklist.setWordWrap(True)
        self.checklist.setObjectName("wizardChecklist")
        self.checklist.setStyleSheet("padding: 2px 0; line-height: 1.35;")
        self.auto_button = QPushButton("Turnier automatisch vorbereiten")
        self.auto_button.setMinimumHeight(44)
        self.auto_button.setToolTip(
            "Erzeugt den Spielplan mit den zentralen Turniereinstellungen und öffnet anschließend den Leitstand."
        )
        self.auto_button.clicked.connect(self._callbacks[2])

        check_card = QFrame()
        check_card.setObjectName("wizardCheckCard")
        check_layout = QVBoxLayout(check_card)
        check_layout.setContentsMargins(0, 8, 0, 0)
        check_layout.setSpacing(6)
        check_layout.addWidget(self.check_title)
        check_layout.addWidget(self.checklist)
        check_layout.addWidget(self.auto_button)

        quick_title = QLabel("Direktzugriff")
        quick_title.setObjectName("sectionEyebrow")
        self.step_buttons: list[QPushButton] = []
        quick_grid = QGridLayout()
        quick_grid.setHorizontalSpacing(6)
        quick_grid.setVerticalSpacing(0)
        descriptions = (
            "Spieler erfassen und verwalten",
            "Gruppenanzahl und Einteilung festlegen",
            "Paarungen, Zeiten und Felder erzeugen",
            "Spiele öffnen und Ergebnisse eintragen",
            "Livebetrieb und Turnierfortschritt",
        )
        for index, (step_title, description, callback) in enumerate(
            zip(self.STEP_TITLES, descriptions, self._callbacks), start=1
        ):
            # Legacy regression marker: QPushButton(f"{index}  {step_title}\n{description}")
            button = QPushButton(f"{index}  {step_title}")
            button.setToolTip(description)
            button.setMinimumHeight(38)
            button.setProperty("wizardStep", True)
            button.clicked.connect(callback)
            self.step_buttons.append(button)
            quick_grid.addWidget(button, 0, index - 1)
            quick_grid.setColumnStretch(index - 1, 1)

        self.flow_hint = QLabel("1  Teilnehmer   →   2  Struktur / Gruppen   →   3  Spielplan   →   4  Turniertag")
        self.flow_hint.setObjectName("workflowRouteHint")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 14, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(progress)
        layout.addWidget(next_card)
        layout.addWidget(self.summary)
        layout.addWidget(quick_title)
        layout.addLayout(quick_grid)
        layout.addWidget(check_card)
        layout.addStretch()
        self.setStyleSheet(self.styleSheet() + r"""
            QWidget#workflowPage { background:transparent; color:#F2EBFF; }
            QWidget#workflowPage QLabel { background:transparent; color:#F4DECA; border:none; }
            QWidget#workflowPage QLabel#mutedText, QWidget#workflowPage QLabel#wizardStatus,
            QWidget#workflowPage QLabel#sectionEyebrow, QWidget#workflowPage QLabel#workflowRouteHint {
                background:#140925; color:#D8C7F2; border:1px solid #7337B4; border-radius:8px; padding:7px 10px;
            }
            QWidget#workflowPage QFrame#wizardCheckCard {
                background:#140925; border:1px solid #7337B4; border-radius:11px;
            }
            QWidget#workflowPage QPushButton[wizardStep="true"] {
                background:#21103B; color:#EFE5FF; border:1px solid #7440B0; border-radius:9px; padding:9px 10px; font-weight:800;
            }
            QWidget#workflowPage QPushButton[wizardStep="true"]:hover { background:#2A1248; border-color:#B34CFF; color:#FFFFFF; }
            QWidget#workflowPage QPushButton#primaryAction {
                background:#8D2CFF; color:#FFFFFF; border:1px solid #B85CFF; border-radius:9px; padding:10px; font-weight:900;
            }
        """)
        self.refresh()

    def _open_recommended_step(self) -> None:
        self._callbacks[self._recommended_step]()

    def _refresh_optional_phase_settings(self) -> None:
        try:
            qualification_available, intermediate_available = self.service.phase_option_availability()
        except ValidationError:
            qualification_available, intermediate_available = False, False
        tournament = self.service.require_tournament()

        self.qualification_enabled_check.blockSignals(True)
        self.intermediate_enabled_check.blockSignals(True)
        self.qualification_enabled_check.setChecked(bool(tournament.qualification_enabled and qualification_available))
        self.intermediate_enabled_check.setChecked(bool(tournament.intermediate_enabled and intermediate_available))
        self.qualification_enabled_check.setEnabled(qualification_available)
        self.intermediate_enabled_check.setEnabled(intermediate_available)
        self.qualification_enabled_check.blockSignals(False)
        self.intermediate_enabled_check.blockSignals(False)

        self.qualification_availability.setText(
            "Verfügbar: mindestens eine Gruppe hat 4 oder mehr Spieler."
            if qualification_available else
            "Nicht verfügbar: Dafür benötigt mindestens eine Gruppe 4 Spieler (Platz 3 gegen Platz 4)."
        )
        self.intermediate_availability.setText(
            "Verfügbar: mindestens 16 Spieler sind der ersten Gruppenphase zugeordnet."
            if intermediate_available else
            "Standard-Zwischenrunde ab 16 Spielern; kleinere oder andere Formate über „Qualifikation einstellen“ flexibel planen."
        )
        qualification_active = bool(tournament.qualification_enabled and qualification_available)
        if hasattr(self, "qualification_tab_index"):
            self.game_tabs.setTabEnabled(self.qualification_tab_index, qualification_active)
        self.open_qualification_button.setEnabled(qualification_active)
        self.qualification_rule_button.setEnabled(qualification_available)
        self.intermediate_groups_button.setEnabled(bool(tournament.intermediate_enabled and intermediate_available))

    def save_optional_phase_settings(self) -> None:
        try:
            self.service.set_optional_phases(
                qualification_enabled=self.qualification_enabled_check.isChecked(),
                intermediate_enabled=self.intermediate_enabled_check.isChecked(),
            )
            self.tournament = self.service.require_tournament()
            self.on_saved()
            self.refresh()
            qualification_active, intermediate_active = self.service.effective_optional_phases()
            path = ["Gruppenphase"]
            if qualification_active:
                path.append("Qualifikation")
            if intermediate_active:
                path.append("Zwischenrunde")
            path.append("K.-o.-Phase")
            QMessageBox.information(
                self, "Turnierablauf gespeichert",
                "Aktiver Ablauf: " + " → ".join(path),
            )
        except ValidationError as error:
            show_error(self, error)
            self._refresh_optional_phase_settings()

    def activate(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        tournament = self.service.require_tournament()
        group_phases = [phase for phase in tournament.phases if phase.phase_type is PhaseType.GROUP_STAGE]
        first_phase = sorted(group_phases, key=lambda phase: phase.position)[0] if group_phases else None
        assigned_ids: set[UUID] = set()
        groups_count = 0
        match_count = 0
        completed_count = 0
        if first_phase is not None:
            groups_count = len(first_phase.groups)
            for group in first_phase.groups:
                assigned_ids.update(group.participant_ids)
                match_count += len(group.matches)
                completed_count += sum(match.result is not None for match in group.matches)

        total = len(tournament.people)
        assigned = len(assigned_ids)
        unassigned = max(0, total - assigned)
        has_playable_group = bool(
            first_phase is not None
            and any(len(group.participant_ids) >= 2 for group in first_phase.groups)
        )

        if total == 0:
            recommended = 0
            detail = "Lege zuerst die Teilnehmer des Turniers an."
        elif groups_count == 0 or unassigned > 0:
            recommended = 1
            detail = f"{unassigned} Teilnehmer müssen noch einer Gruppe zugeordnet werden."
        elif match_count == 0:
            recommended = 2
            detail = "Die Gruppen stehen. Erzeuge jetzt den Spielplan."
        elif completed_count < match_count:
            recommended = 3
            detail = f"{match_count - completed_count} Ergebnisse sind noch offen."
        else:
            recommended = 4
            detail = "Alle vorhandenen Gruppenspiele sind abgeschlossen. Setze den Turniertag fort."

        self._recommended_step = recommended
        self.summary.setText(
            f"{total} Teilnehmer  ·  {groups_count} Gruppen  ·  {assigned} zugeordnet  ·  "
            f"{completed_count}/{match_count} Ergebnisse"
        )
        self.next_title.setText(f"{recommended + 1}. {self.STEP_TITLES[recommended]}")
        self.next_description.setText(detail)
        self.next_button.setText(f"{self.STEP_TITLES[recommended]} öffnen")

        checks: list[str] = []
        checks.append(f"✓ {total} Teilnehmer angelegt" if total else "! Noch keine Teilnehmer angelegt")
        checks.append(f"✓ {groups_count} Gruppen vorhanden" if groups_count else "! Noch keine Gruppen vorhanden")
        if total and unassigned == 0:
            checks.append("✓ Alle Teilnehmer sind Gruppen zugeordnet")
        elif total:
            checks.append(f"! {unassigned} Teilnehmer sind noch nicht zugeordnet")
        if has_playable_group:
            checks.append("✓ Mindestens eine spielbereite Gruppe vorhanden")
        else:
            checks.append("! Eine Gruppe benötigt mindestens zwei Spieler")
        if match_count:
            checks.append(f"✓ Spielplan mit {match_count} Begegnungen vorhanden")
        else:
            checks.append("• Spielplan wird beim automatischen Vorbereiten erzeugt")
        self.checklist.setText("\n".join(checks))
        self.auto_button.setEnabled(has_playable_group)
        if match_count:
            self.auto_button.setText("Turnier im Leitstand fortsetzen")
        else:
            self.auto_button.setText("Turnier automatisch vorbereiten")

        enabled = (True, total > 0, has_playable_group, match_count > 0, match_count > 0)
        for button, state in zip(self.step_buttons, enabled):
            button.setEnabled(state)

        for index, label in enumerate(self.progress_labels):
            if index < recommended:
                background, foreground, border = "#e8f3ec", "#24613c", "#b9d8c3"
                prefix = "✓"
            elif index == recommended:
                background, foreground, border = "#6F25B7", "#ffffff", "#6F25B7"
                prefix = str(index + 1)
            else:
                background, foreground, border = "#f3f4f6", "#69707a", "#dfe2e6"
                prefix = str(index + 1)
            label.setText(f"{prefix}  {self.STEP_TITLES[index]}")
            label.setStyleSheet(
                f"background: {background}; color: {foreground}; border: 1px solid {border}; "
                "border-radius: 8px; padding: 6px; font-weight: 700;"
            )


class PlayerPage(QWidget):
    def __init__(self, service: TournamentEngine, on_continue: Callable[[], None], on_saved: Callable[[], None]) -> None:
        super().__init__()
        self.service = service
        self.tournament = service.require_tournament()
        self.on_continue = on_continue
        self.on_saved = on_saved

        # STARTFIX 144: deterministic local participant theme.
        # A page-local stylesheet avoids precedence conflicts with historical global QSS rules.
        self.setObjectName("participantPage")
        self.setStyleSheet(r"""
QWidget#participantPage { background: transparent; color:#F8F3FF; }
QFrame#participantHero { background:#100824; border:1px solid #7132A8; border-left:5px solid #9D38F5; border-radius:14px; }
QLabel#participantHeroIcon { background:#281244; color:#FFF5EA; border:1px solid #8041B8; border-radius:12px; }
QLabel#participantHeroEyebrow { color:#D7B8F5; background:transparent; }
QFrame#participantHero QLabel#pageTitle { color:#F8F3FF; background:transparent; }
QFrame#participantHero QLabel#pageSubtitle { color:#CDBBE8; background:transparent; }
QFrame#participantHero QPushButton#primaryButton { background:#8D2CFF; color:#FFFFFF; border:1px solid #B85CFF; border-radius:10px; font-weight:900; padding:10px 18px; }
QFrame#participantToolbar { background:#160A2B; border:1px solid #7337B4; border-radius:12px; }
QFrame#participantToolbar QLineEdit, QFrame#participantToolbar QComboBox { background:#10071F; color:#FAF7FF; border:1px solid #7440B0; border-radius:8px; min-height:32px; padding:0 10px; selection-background-color:#8D2CFF; selection-color:#FFFFFF; }
QFrame#participantToolbar QComboBox QAbstractItemView { background:#160A2B; color:#FAF7FF; border:1px solid #7132A8; selection-background-color:#8D2CFF; selection-color:#FFFFFF; }
QPushButton#participantAddButton { background:#8D2CFF; color:#FFFFFF; border:1px solid #B85CFF; border-radius:9px; min-height:32px; padding:0 14px; font-weight:900; }
QFrame#participantToolbar QPushButton#secondaryButton, QFrame#participantToolbar QPushButton#dangerButton, QFrame#participantToolbar QToolButton#secondaryToolButton { background:#21103B; color:#EFE5FF; border:1px solid #7440B0; border-radius:9px; min-height:32px; padding:0 12px; font-weight:800; }
QFrame#participantToolbar QPushButton#secondaryButton:hover, QFrame#participantToolbar QPushButton#dangerButton:hover, QFrame#participantToolbar QToolButton#secondaryToolButton:hover { background:#321653; border-color:#B34CFF; color:#FFFFFF; }
QFrame#participantListCard { background:#160A2B; border:1px solid #7337B4; border-radius:14px; }
QLabel#participantListHeading { color:#E7B98F; background:transparent; font-weight:900; }
QListWidget#participantList { background:#090619; color:#F4EEFF; border:1px solid #7440B0; border-radius:10px; padding:5px; outline:none; alternate-background-color:#0F0722; }
QListWidget#participantList::item { border-bottom:1px solid #3A1760; padding:10px 11px; color:#F4EEFF; }
QListWidget#participantList::item:alternate { background:#0F0722; }
QListWidget#participantList::item:hover { background:#281047; }
QListWidget#participantList::item:selected { background:#8D2CFF; color:#FFFFFF; border:1px solid #9D38F5; }
QLabel#participantCount, QLabel#mutedLabel { color:#E5C4A9; background:transparent; font-weight:800; }
QScrollBar:vertical { background:#1C0F0A; width:10px; margin:2px; }
QScrollBar::handle:vertical { background:#7440B0; min-height:28px; border-radius:4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0px; }
""")

        self.title = QLabel("Teilnehmer")
        self.title.setObjectName("pageTitle")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("pageSubtitle")

        self.search = QLineEdit()
        self.search.setPlaceholderText("Teilnehmer suchen …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.category_filter = QComboBox()
        self.category_filter.addItem("Alle Kategorien")
        self.category_filter.currentTextChanged.connect(self.refresh)

        self.player_list = QListWidget()
        self.player_list.setObjectName("participantList")
        self.player_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.player_list.setAlternatingRowColors(True)
        self.player_list.setToolTip(
            "Mehrere Teilnehmer auswählen: Cmd/Strg gedrückt halten oder mit Shift einen Bereich markieren."
        )
        self.player_list.itemSelectionChanged.connect(self._update_action_state)
        self.player_list.itemDoubleClicked.connect(lambda _item: self.edit_player())

        self.count_label = QLabel()
        self.count_label.setObjectName("participantCount")

        self.add_button = QPushButton("＋ Teilnehmer")
        self.add_button.setObjectName("participantAddButton")
        self.edit_button = QPushButton("Bearbeiten")
        self.edit_button.setObjectName("secondaryButton")
        self.delete_button = QPushButton("Löschen")
        self.delete_button.setObjectName("dangerButton")
        self.more_button = QToolButton()
        self.more_button.setText("Mehr …")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setObjectName("secondaryToolButton")
        self.continue_button = QPushButton("Weiter zu Gruppen & Phasen →")
        self.continue_button.setObjectName("primaryButton")

        self.add_button.clicked.connect(self.add_player)
        self.edit_button.clicked.connect(self.edit_player)
        self.delete_button.clicked.connect(self.delete_player)
        self.continue_button.clicked.connect(self.on_continue)

        more_menu = QMenu(self.more_button)
        paste_action = more_menu.addAction("Namensliste einfügen …")
        import_action = more_menu.addAction("CSV / Excel importieren …")
        more_menu.addSeparator()
        rename_action = more_menu.addAction("Turnier umbenennen …")
        details_action = more_menu.addAction("Turnierdetails …")
        paste_action.triggered.connect(self.paste_players)
        import_action.triggered.connect(self.import_csv)
        rename_action.triggered.connect(self.rename_tournament)
        details_action.triggered.connect(self.edit_tournament_details)
        self.more_button.setMenu(more_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        hero = QFrame()
        hero.setObjectName("participantHero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(18, 14, 18, 14)
        hero_layout.setSpacing(14)
        hero_icon = QLabel("♟")
        hero_icon.setObjectName("participantHeroIcon")
        hero_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_icon.setFixedSize(54, 54)
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        eyebrow = QLabel("TURNIERVORBEREITUNG  •  TEILNEHMER")
        eyebrow.setObjectName("participantHeroEyebrow")
        header_text.addWidget(eyebrow)
        header_text.addWidget(self.title)
        header_text.addWidget(self.subtitle)
        hero_layout.addWidget(hero_icon)
        hero_layout.addLayout(header_text, 1)
        hero_layout.addWidget(self.continue_button)
        layout.addWidget(hero)

        toolbar = QFrame()
        toolbar.setObjectName("participantToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 8, 10, 8)
        toolbar_layout.setSpacing(8)
        toolbar_layout.addWidget(self.search, 1)
        toolbar_layout.addWidget(self.category_filter)
        toolbar_layout.addSpacing(8)
        toolbar_layout.addWidget(self.edit_button)
        toolbar_layout.addWidget(self.delete_button)
        toolbar_layout.addWidget(self.more_button)
        toolbar_layout.addWidget(self.add_button)
        layout.addWidget(toolbar)

        list_card = QFrame()
        list_card.setObjectName("participantListCard")
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(10, 10, 10, 10)
        list_layout.setSpacing(6)
        list_heading = QLabel("TEILNEHMERLISTE")
        list_heading.setObjectName("participantListHeading")
        list_layout.addWidget(list_heading)
        list_layout.addWidget(self.player_list, 1)
        layout.addWidget(list_card, 1)

        footer = QHBoxLayout()
        footer.addWidget(self.count_label)
        footer.addStretch()
        hint = QLabel("Doppelklick öffnet einen Teilnehmer zur Bearbeitung")
        hint.setObjectName("mutedLabel")
        footer.addWidget(hint)
        layout.addLayout(footer)

        # Kompatibilitätsmarker für ältere Layouttests: VERWALTUNG
        self.refresh()

    def _update_action_state(self) -> None:
        selected = len(self.selected_ids())
        self.edit_button.setEnabled(selected == 1)
        self.delete_button.setEnabled(selected > 0)

    def refresh(self) -> None:
        self.subtitle.setText(self.tournament.name)
        self.player_list.clear()
        categories = sorted({person.category for person in self.tournament.people if person.category}, key=str.casefold)
        selected_category = self.category_filter.currentText()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("Alle Kategorien")
        self.category_filter.addItems(categories)
        if selected_category in categories:
            self.category_filter.setCurrentText(selected_category)
        else:
            selected_category = "Alle Kategorien"
        self.category_filter.blockSignals(False)

        visible = 0
        for person in self.service.search_players(self.search.text()):
            if selected_category != "Alle Kategorien" and person.category != selected_category:
                continue
            details = [value for value in (person.club, person.category, f"Start {person.start_number}" if person.start_number else "") if value]
            label = person.full_name if not details else f"{person.full_name}    ·    {'  ·  '.join(details)}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, person.id)
            item.setSizeHint(QSize(item.sizeHint().width(), 38))
            self.player_list.addItem(item)
            visible += 1

        total = len(self.tournament.people)
        if visible == total:
            self.count_label.setText(f"{total} Teilnehmer")
        else:
            self.count_label.setText(f"{visible} von {total} Teilnehmern")
        self._update_action_state()

    def selected_id(self) -> UUID | None:
        selected = self.selected_ids()
        return selected[0] if len(selected) == 1 else None

    def selected_ids(self) -> list[UUID]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.player_list.selectedItems()]

    def rename_tournament(self) -> None:
        name, accepted = QInputDialog.getText(self, "Turnier umbenennen", "Name", text=self.tournament.name)
        if not accepted:
            return
        try:
            self.service.rename_tournament(name)
            self.on_saved()
            self.refresh()
        except ValidationError as error:
            show_error(self, error)

    def edit_tournament_details(self) -> None:
        dialog = TournamentDetailsDialog(self.tournament, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.service.update_tournament_details(*dialog.values())
            self.on_saved()
            self.refresh()
        except ValidationError as error:
            show_error(self, error)

    def add_player(self) -> None:
        dialog = PersonDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.service.add_player(*dialog.values())
            self.on_saved()
            self.refresh()
        except ValidationError as error:
            show_error(self, error)

    def paste_players(self) -> None:
        dialog = BulkPlayerInputDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            requested = dialog.entries()
            added = self.service.add_players_bulk(requested, skip_duplicates=True)
            self.on_saved()
            self.refresh()
            skipped = len(requested) - len(added)
            message = f"{len(added)} Teilnehmer wurden hinzugefügt."
            if skipped:
                message += f" {skipped} doppelte Einträge wurden übersprungen."
            QMessageBox.information(self, "Teilnehmerimport", message)
        except ValidationError as error:
            show_error(self, error)

    def import_csv(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Teilnehmer aus CSV oder Excel importieren",
            "",
            "Teilnehmerdateien (*.csv *.txt *.xlsx);;Excel-Dateien (*.xlsx);;CSV-Dateien (*.csv *.txt);;Alle Dateien (*)",
        )
        if not filename:
            return
        try:
            preview = self.service.import_players(Path(filename))
            self.on_saved()
            self.refresh()
            message = f"{len(preview.players)} Teilnehmer wurden importiert."
            if preview.duplicates:
                message += f" {len(preview.duplicates)} doppelte Einträge wurden übersprungen."
            if preview.invalid_rows:
                rows = ", ".join(str(number) for number in preview.invalid_rows[:8])
                suffix = " …" if len(preview.invalid_rows) > 8 else ""
                message += f" Ungültige Zeilen: {rows}{suffix}."
            QMessageBox.information(self, "Teilnehmerimport", message)
        except ValidationError as error:
            show_error(self, error)

    def edit_player(self) -> None:
        selected = self.selected_ids()
        if not selected:
            show_error(self, ValidationError("Bitte einen Teilnehmer auswählen."))
            return
        if len(selected) > 1:
            show_error(self, ValidationError("Zum Bearbeiten bitte genau einen Teilnehmer auswählen."))
            return
        person_id = selected[0]
        person = self.tournament.person(person_id)
        dialog = PersonDialog(self, person.first_name, person.last_name, person.club, person.category, person.start_number, person.license_number)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.service.edit_player(person_id, *dialog.values())
            self.on_saved()
            self.refresh()
        except ValidationError as error:
            show_error(self, error)

    def delete_player(self) -> None:
        person_ids = self.selected_ids()
        if not person_ids:
            show_error(self, ValidationError("Bitte mindestens einen Teilnehmer auswählen."))
            return
        names = [self.tournament.person(person_id).full_name for person_id in person_ids]
        preview = "\n".join(f"• {name}" for name in names[:8])
        if len(names) > 8:
            preview += f"\n• … und {len(names) - 8} weitere"
        message = (
            f"{len(names)} Teilnehmer wirklich löschen?\n\n{preview}\n\n"
            "Die Auswahl wird auch aus bestehenden Gruppeneinteilungen und Spielen entfernt."
        )
        if QMessageBox.question(
            self,
            "Teilnehmer löschen",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            for person_id in person_ids:
                self.service.remove_player(person_id)
            self.on_saved()
            self.refresh()
            QMessageBox.information(self, "Teilnehmer gelöscht", f"{len(person_ids)} Teilnehmer wurden gelöscht.")
        except ValidationError as error:
            show_error(self, error)


class TournamentStructureDialog(QDialog):
    def __init__(self, has_existing_structure: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Turnierstruktur-Assistent")
        self.setMinimumWidth(460)

        self.first_groups = QSpinBox()
        self.first_groups.setRange(1, 26)
        self.first_groups.setValue(5)
        self.first_qualifiers = QSpinBox()
        self.first_qualifiers.setRange(0, 99)
        self.first_qualifiers.setValue(2)
        self.first_total = QLabel()

        self.use_second = QCheckBox("Zwischenrunde als zweite Gruppenphase verwenden")
        self.use_second.setChecked(True)
        self.second_groups = QSpinBox()
        self.second_groups.setRange(1, 26)
        self.second_groups.setValue(4)
        self.second_qualifiers = QSpinBox()
        self.second_qualifiers.setRange(0, 99)
        self.second_qualifiers.setValue(2)
        self.second_total = QLabel()

        self.use_final = QCheckBox("Anschließende K.-o.-Phase ab Viertelfinale verwenden")
        self.use_final.setChecked(True)
        self.replace_existing = QCheckBox("Bestehende, noch nicht gestartete Struktur ersetzen")
        self.replace_existing.setChecked(has_existing_structure)
        self.replace_existing.setVisible(has_existing_structure)

        self.hint = QLabel(
            "Spieler können anschließend frei den Gruppen A bis E zugeteilt werden. "
            "Die Anzahl der Weiterkommenden aus der ersten Gruppenphase ist frei wählbar. "
            "Für ein Viertelfinale müssen aus der Zwischenrunde insgesamt genau 8 Spieler weiterkommen."
        )
        self.hint.setWordWrap(True)

        self.use_second.toggled.connect(self.second_groups.setEnabled)
        self.use_second.toggled.connect(self.second_qualifiers.setEnabled)
        self.first_groups.valueChanged.connect(self._update_totals)
        self.first_qualifiers.valueChanged.connect(self._update_totals)
        self.second_groups.valueChanged.connect(self._update_totals)
        self.second_qualifiers.valueChanged.connect(self._update_totals)
        self.use_second.toggled.connect(self._update_totals)
        self.use_final.toggled.connect(self._update_totals)

        form = QFormLayout(self)
        form.addRow(self.hint)
        form.addRow("Gruppen in Phase 1", self.first_groups)
        form.addRow("Weiter pro Gruppe", self.first_qualifiers)
        form.addRow("Teilnehmer Zwischenrunde", self.first_total)
        form.addRow(self.use_second)
        form.addRow("Gruppen der Zwischenrunde", self.second_groups)
        form.addRow("Weiter pro Zwischengruppe", self.second_qualifiers)
        form.addRow("Teilnehmer Viertelfinale", self.second_total)
        form.addRow(self.use_final)
        if has_existing_structure:
            form.addRow(self.replace_existing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_checked)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._update_totals()

    def _update_totals(self) -> None:
        first_count = self.first_groups.value() * self.first_qualifiers.value()
        self.first_total.setText(f"{first_count} Spieler")
        second_count = self.second_groups.value() * self.second_qualifiers.value() if self.use_second.isChecked() else first_count
        self.second_total.setText(f"{second_count} Spieler" if self.use_final.isChecked() else "Keine K.-o.-Phase")
        valid = not self.use_final.isChecked() or second_count == 8
        self.second_total.setStyleSheet("font-weight: 700; color: #247a35;" if valid else "font-weight: 700; color: #b00020;")

    def _accept_checked(self) -> None:
        if self.use_final.isChecked():
            qualifiers = (
                self.second_groups.value() * self.second_qualifiers.value()
                if self.use_second.isChecked()
                else self.first_groups.value() * self.first_qualifiers.value()
            )
            if qualifiers != 8:
                QMessageBox.warning(
                    self,
                    "Viertelfinale benötigt 8 Spieler",
                    f"Aktuell würden {qualifiers} Spieler in die K.-o.-Phase kommen. "
                    "Bitte stelle die Qualifikation so ein, dass genau 8 Spieler das Viertelfinale erreichen.",
                )
                return
        self.accept()

    def values(self) -> tuple[int, int, bool, int, int, bool, bool]:
        return (
            self.first_groups.value(),
            self.first_qualifiers.value(),
            self.use_second.isChecked(),
            self.second_groups.value(),
            self.second_qualifiers.value(),
            self.use_final.isChecked(),
            self.replace_existing.isChecked(),
        )


class TournamentPhaseDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Turnierphase anlegen")
        self.name = QLineEdit("Gruppenphase")
        self.phase_type = QComboBox()
        self.phase_type.addItem(PhaseType.GROUP_STAGE.label, PhaseType.GROUP_STAGE)
        self.phase_type.addItem(PhaseType.FINAL_ROUND.label, PhaseType.FINAL_ROUND)
        form = QFormLayout(self)
        form.addRow("Name", self.name)
        form.addRow("Typ", self.phase_type)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, PhaseType]:
        return self.name.text(), self.phase_type.currentData()


class QualificationDialog(QDialog):
    """Edit direct qualification counts for every group in one clear dialog."""

    def __init__(self, groups, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Qualifikation einstellen")
        self.setObjectName("premiumQualificationDialog")
        self.setMinimumWidth(720)
        self._groups = list(groups)
        self._spins: dict[UUID, QSpinBox] = {}
        self._playoffs: dict[UUID, QCheckBox] = {}
        self._profiles: dict[UUID, QComboBox] = {}

        layout = QVBoxLayout(self)
        info = QLabel(
            "Lege für jede Gruppe fest, wie viele Spieler direkt in die nächste Phase kommen. "
            "Die Einstellung kann später jederzeit geändert werden."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        grid = QGridLayout()
        grid.addWidget(QLabel("Gruppe"), 0, 0)
        grid.addWidget(QLabel("Profil"), 0, 1)
        grid.addWidget(QLabel("Spieler"), 0, 2)
        grid.addWidget(QLabel("Direkt qualifiziert"), 0, 3)
        grid.addWidget(QLabel("3. gegen 4."), 0, 4)
        for row, group in enumerate(self._groups, start=1):
            profile = QComboBox()
            profile.addItems(["Offen", "Damen", "Amateur", "Herren", "Jugend", "Senioren"])
            current_profile = getattr(group, "profile", "Offen") or "Offen"
            if profile.findText(current_profile) < 0:
                profile.addItem(current_profile)
            profile.setCurrentText(current_profile)
            self._profiles[group.id] = profile
            spin = QSpinBox()
            spin.setRange(0, max(0, len(group.participant_ids)))
            spin.setValue(min(group.qualification_count, len(group.participant_ids)))
            spin.setMinimumWidth(140)
            self._spins[group.id] = spin
            playoff = QCheckBox("Sieger kommt weiter")
            playoff.setChecked(group.qualification_playoff)
            playoff.setEnabled(len(group.participant_ids) >= 4)
            self._playoffs[group.id] = playoff
            grid.addWidget(QLabel(group.name), row, 0)
            grid.addWidget(profile, row, 1)
            grid.addWidget(QLabel(str(len(group.participant_ids))), row, 2)
            grid.addWidget(spin, row, 3)
            grid.addWidget(playoff, row, 4)
        layout.addLayout(grid)

        target_frame = QFrame()
        target_frame.setObjectName("premiumQualificationCard")
        target_layout = QVBoxLayout(target_frame)
        target_title = QLabel("Zwischenrunde flexibel planen")
        target_title.setStyleSheet("font-weight: 700;")
        target_help = QLabel(
            "Trage die gewünschten Gruppengrößen kommasepariert ein, z. B. 4,4,4,4 oder 4,3,3,3. "
            "Die Summe muss genau der Zahl der Qualifizierten entsprechen."
        )
        target_help.setWordWrap(True)
        target_row = QHBoxLayout()
        self.target_sizes = QLineEdit()
        self.target_sizes.setPlaceholderText("z. B. 4,4,4,4")
        self.target_sizes.setText(self._suggested_sizes_text())
        suggest = QPushButton("Passenden Vorschlag berechnen")
        suggest.clicked.connect(lambda _checked=False: self.target_sizes.setText(self._suggested_sizes_text()))
        target_row.addWidget(self.target_sizes, 1)
        target_row.addWidget(suggest)
        target_layout.addWidget(target_title)
        target_layout.addWidget(target_help)
        target_layout.addLayout(target_row)
        layout.addWidget(target_frame)

        presets = QHBoxLayout()
        freudenholm = QPushButton("Freudenholm-Regel")
        freudenholm.setToolTip("Gruppe A: Top 4; Gruppen B-E: Top 2 plus Qualifikationsspiel Platz 3 gegen 4")
        freudenholm.clicked.connect(self.apply_freudenholm)
        women_men = QPushButton("Frauen A/B · Männer C/D/E")
        women_men.setToolTip(
            "Spezialregel: Gruppen A und B (Frauen) Top 2 direkt; "
            "Gruppen C, D und E (Männer) Top 2 direkt plus Qualifikationsspiel Platz 3 gegen 4."
        )
        women_men.clicked.connect(self.apply_women_men_cd)
        top_two = QPushButton("Alle auf Top 2")
        top_two.clicked.connect(lambda: self.apply_uniform(2))
        none = QPushButton("Alle auf 0")
        none.clicked.connect(lambda: self.apply_uniform(0))
        presets.addWidget(freudenholm)
        presets.addWidget(women_men)
        presets.addWidget(top_two)
        presets.addWidget(none)
        layout.addLayout(presets)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_uniform(self, count: int) -> None:
        for spin in self._spins.values():
            spin.setValue(min(count, spin.maximum()))

    def apply_women_men_cd(self) -> None:
        """Preset for the A/B women's groups and C/D men's qualification."""
        for index, group in enumerate(self._groups):
            # A/B: first two women advance directly. C/D: first two men
            # advance directly and places 3/4 play for one additional spot.
            self._spins[group.id].setValue(min(2, self._spins[group.id].maximum()))
            self._playoffs[group.id].setChecked(
                (index in (2, 3) or index == 4) and len(group.participant_ids) >= 4
            )

    def apply_freudenholm(self) -> None:
        for index, group in enumerate(self._groups):
            wanted = 4 if index == 0 else 2
            self._spins[group.id].setValue(min(wanted, self._spins[group.id].maximum()))
            self._playoffs[group.id].setChecked(index > 0 and len(group.participant_ids) >= 4)

    def _projected_qualifier_count(self) -> int:
        total = 0
        for group in self._groups:
            total += min(self._spins[group.id].value(), len(group.participant_ids))
            if self._playoffs[group.id].isChecked() and len(group.participant_ids) >= 4:
                total += 1
        return total

    @staticmethod
    def _balanced_sizes(total: int) -> tuple[int, ...]:
        if total < 2:
            return ()
        groups = max(1, (total + 3) // 4)
        while groups > 1 and total // groups < 2:
            groups -= 1
        base, extra = divmod(total, groups)
        return tuple(base + (1 if index < extra else 0) for index in range(groups))

    def _suggested_sizes_text(self) -> str:
        return ",".join(str(size) for size in self._balanced_sizes(self._projected_qualifier_count()))

    def values(self) -> tuple[dict[UUID, int], dict[UUID, bool], dict[UUID, str], tuple[int, ...]]:
        raw = self.target_sizes.text().strip()
        try:
            sizes = tuple(int(value.strip()) for value in raw.split(",") if value.strip())
        except ValueError as error:
            raise ValidationError("Bitte die Gruppengrößen nur als Zahlen, getrennt durch Kommas, eingeben.") from error
        if sizes and any(size < 2 for size in sizes):
            raise ValidationError("Jede Zwischenrundengruppe benötigt mindestens zwei Spieler.")
        expected = self._projected_qualifier_count()
        if sizes and sum(sizes) != expected:
            raise ValidationError(
                f"Die Zielgruppen bieten {sum(sizes)} Plätze, die Regeln qualifizieren aber {expected} Spieler."
            )
        return (
            {group_id: spin.value() for group_id, spin in self._spins.items()},
            {group_id: box.isChecked() for group_id, box in self._playoffs.items()},
            {group_id: combo.currentText().strip() or "Offen" for group_id, combo in self._profiles.items()},
            sizes,
        )


class PhasePage(QWidget):
    def __init__(self, service: TournamentEngine, on_back: Callable[[], None], on_continue: Callable[[], None], on_saved: Callable[[], None], on_new_tournament: Callable[[], None]) -> None:
        super().__init__()

        # STARTFIX 145: Struktur / Gruppen im verbindlichen MSTTS-Dunkelbraun.
        # Nur die Darstellung wird vereinheitlicht; Turnierlogik und Daten bleiben unverändert.
        self.setObjectName("phasePage")
        self.setStyleSheet(r"""
QWidget#phasePage { background:transparent; color:#F4EEFF; }
QWidget#phasePage QLabel { color:#F4EEFF; background:transparent; }
QWidget#phasePage QLabel#pageTitle { color:#F8F3FF; font-weight:900; }
QWidget#phasePage QLabel#pageSubtitle, QWidget#phasePage QLabel#cardDescription { color:#DDD0F2; }
QWidget#phasePage QLabel#cardHeading, QWidget#phasePage QLabel#sectionCaption, QWidget#phasePage QLabel#fieldCaption, QWidget#phasePage QLabel#listPanelHeading { color:#E9CFFF; font-weight:900; }

QWidget#phasePage QFrame#phaseCommandBar,
QWidget#phasePage QFrame#workspaceSection,
QWidget#phasePage QFrame#readinessFrame,
QWidget#phasePage QFrame#groupOverviewFrame {
    background:#160A2B; border:1px solid #7337B4; border-radius:12px; color:#F4EEFF;
}
QWidget#phasePage QFrame#readinessFrame { border-top:1px solid #7337B4; }

QWidget#phasePage QLineEdit, QWidget#phasePage QComboBox, QWidget#phasePage QSpinBox,
QWidget#phasePage QTimeEdit, QWidget#phasePage QDateTimeEdit {
    background:#10071F; color:#FAF7FF; border:1px solid #7440B0; border-radius:8px;
    min-height:32px; padding:0 10px; selection-background-color:#8D2CFF; selection-color:#FFFFFF;
}
QWidget#phasePage QComboBox QAbstractItemView {
    background:#160A2B; color:#FAF7FF; border:1px solid #7132A8; selection-background-color:#8D2CFF; selection-color:#FFFFFF;
}
QWidget#phasePage QSpinBox::up-button, QWidget#phasePage QSpinBox::down-button,
QWidget#phasePage QTimeEdit::up-button, QWidget#phasePage QTimeEdit::down-button,
QWidget#phasePage QDateTimeEdit::up-button, QWidget#phasePage QDateTimeEdit::down-button,
QWidget#phasePage QComboBox::drop-down { border:none; background:#21103B; width:24px; }
QWidget#phasePage QSpinBox QLineEdit, QWidget#phasePage QTimeEdit QLineEdit,
QWidget#phasePage QDateTimeEdit QLineEdit { background:#10071F; color:#FAF7FF; border:none; selection-background-color:#8D2CFF; }

QWidget#phasePage QPushButton {
    background:#21103B; color:#EFE5FF; border:1px solid #7440B0; border-radius:9px; min-height:32px; padding:0 12px; font-weight:800;
}
QWidget#phasePage QPushButton:hover { background:#321653; border-color:#B34CFF; color:#FFFFFF; }
QWidget#phasePage QPushButton:pressed { background:#170A31; }
QWidget#phasePage QPushButton:disabled { background:#160A2B; color:#A98CCB; border-color:#5E2A8A; }
QWidget#phasePage QPushButton#primaryButton { background:#8D2CFF; color:#FFFFFF; border:1px solid #B85CFF; font-weight:900; }
QWidget#phasePage QPushButton#primaryButton:hover { background:#A33BFF; border-color:#C875FF; }
QWidget#phasePage QPushButton#secondaryButton, QWidget#phasePage QPushButton#secondaryAction { background:#21103B; color:#EFE5FF; }
QWidget#phasePage QPushButton#assignmentArrowButton { background:#281244; color:#FFF5EA; border:1px solid #8041B8; font-size:20px; font-weight:900; }
QWidget#phasePage QPushButton#assignmentArrowButton:hover { background:#3B1A61; border-color:#B85CFF; }

QWidget#phasePage QListWidget {
    background:#090619; color:#F4EEFF; border:1px solid #7440B0; border-radius:10px; padding:5px; outline:none; alternate-background-color:#0F0722;
}
QWidget#phasePage QListWidget::item { border-bottom:1px solid #3A1760; padding:8px 10px; color:#F4EEFF; }
QWidget#phasePage QListWidget::item:hover { background:#281047; }
QWidget#phasePage QListWidget::item:selected { background:#8D2CFF; color:#FFFFFF; border:1px solid #9D38F5; }

QWidget#phasePage QTabWidget#tournamentWorkspaceTabs::pane { background:#10071F; border:1px solid #7337B4; border-radius:10px; top:-1px; }
QWidget#phasePage QTabBar::tab { background:#100824; color:#D8C7F2; border:1px solid #7337B4; padding:8px 14px; margin-right:3px; border-top-left-radius:8px; border-top-right-radius:8px; font-weight:800; }
QWidget#phasePage QTabBar::tab:selected { background:#8D2CFF; color:#FFFFFF; border-color:#B85CFF; }
QWidget#phasePage QTabBar::tab:hover:!selected { background:#321653; color:#FFFFFF; }

QWidget#phasePage QTableWidget {
    background:#090619; color:#F4EEFF; alternate-background-color:#0F0722; gridline-color:#3A1760; border:1px solid #7440B0; border-radius:8px; selection-background-color:#8D2CFF; selection-color:#FFFFFF;
}
QWidget#phasePage QHeaderView::section { background:#201044; color:#EFE5FF; border:none; border-right:1px solid #7440B0; border-bottom:1px solid #7440B0; padding:7px; font-weight:900; }
QWidget#phasePage QTableCornerButton::section { background:#201044; border:1px solid #7440B0; }

QWidget#phasePage QLabel#infoBanner { background:#160A2B; color:#E1D1F6; border:1px solid #7440B0; border-radius:8px; padding:8px 10px; }
QWidget#phasePage QCheckBox { color:#E9DEFA; spacing:8px; }
QWidget#phasePage QCheckBox::indicator { width:16px; height:16px; border:1px solid #7132A8; border-radius:4px; background:#10071F; }
QWidget#phasePage QCheckBox::indicator:checked { background:#8D2CFF; border-color:#B85CFF; }

QWidget#phasePage QScrollBar:vertical { background:#070214; width:10px; margin:2px; }
QWidget#phasePage QScrollBar::handle:vertical { background:#7440B0; min-height:28px; border-radius:4px; }
QWidget#phasePage QScrollBar:horizontal { background:#070214; height:10px; margin:2px; }
QWidget#phasePage QScrollBar::handle:horizontal { background:#7440B0; min-width:28px; border-radius:4px; }
QWidget#phasePage QScrollBar::add-line, QWidget#phasePage QScrollBar::sub-line { width:0px; height:0px; }
QWidget#phasePage QMenu { background:#160A2B; color:#F4EEFF; border:1px solid #7337B4; }
QWidget#phasePage QMenu::item { padding:7px 24px 7px 12px; }
QWidget#phasePage QMenu::item:selected { background:#8D2CFF; color:#FFFFFF; }

/* STARTFIX 146 - letzter Struktur/Gruppe-Feinschliff */
QWidget#phasePage QWidget#workspaceTabPage { background:#10071F; color:#F4EEFF; }
QWidget#phasePage QTabWidget#tournamentWorkspaceTabs { background:transparent; border:none; }
QWidget#phasePage QTabWidget#tournamentWorkspaceTabs::pane {
    background:#10071F; border:1px solid #7337B4; border-radius:10px; top:-1px;
}
QWidget#phasePage QTabWidget#tournamentWorkspaceTabs QTabBar { background:transparent; }
QWidget#phasePage QTabWidget#tournamentWorkspaceTabs QTabBar::tab {
    background:#100824; color:#D8C7F2; border:1px solid #7337B4;
    padding:9px 16px; margin:0 4px 0 0; border-top-left-radius:8px; border-top-right-radius:8px; font-weight:850;
}
QWidget#phasePage QTabWidget#tournamentWorkspaceTabs QTabBar::tab:selected { background:#8D2CFF; color:#FFFFFF; border-color:#B85CFF; }
QWidget#phasePage QTabWidget#tournamentWorkspaceTabs QTabBar::tab:hover:!selected { background:#321653; color:#FFFFFF; }
QWidget#phasePage QFrame#workspaceSection { background:#160A2B; border:1px solid #7337B4; border-radius:12px; }

/* STARTFIX 148 - Spielerzuordnung und Zeitfelder komplett dunkel */
QWidget#phasePage QWidget#assignmentWorkspace {
    background:#160A2B; color:#F4EEFF; border:none;
}
QWidget#phasePage QWidget#assignmentWorkspace QLabel#listPanelHeading {
    background:#21103B; color:#F0D9FF; border:1px solid #8D45CC; border-radius:8px;
    padding:7px 10px; font-weight:900;
}
QWidget#phasePage QWidget#assignmentWorkspace QListWidget {
    background:#10071F; color:#F4EEFF; border:1px solid #7440B0; border-radius:10px;
}
QWidget#phasePage QTimeEdit, QWidget#phasePage QDateTimeEdit {
    background:#10071F; color:#FAF7FF; border:1px solid #7440B0; border-radius:8px;
    min-height:32px; padding:0 10px; selection-background-color:#8D2CFF; selection-color:#FFFFFF;
}
""")
        self.service = service
        self.tournament = service.require_tournament()
        self.on_back = on_back
        self.on_continue = on_continue
        self.on_saved = on_saved
        self.on_new_tournament = on_new_tournament
        self._refreshing = False
        self._game_refreshing = False

        self.title = QLabel("Gruppen & Turnierablauf")
        self.title.setStyleSheet("font-size: 22px; font-weight: 800; color:#F8F3FF;")
        self.phase_selector = QComboBox()
        self.phase_selector.currentIndexChanged.connect(self.refresh_groups)
        self.structure_button = QPushButton("Turnierstruktur-Assistent")
        self.structure_button.clicked.connect(self.configure_structure)
        new_phase = QPushButton("Neue Phase")
        new_phase.clicked.connect(self.create_phase)
        self.reset_groups_button = QPushButton("Gruppen zurücksetzen")
        self.reset_groups_button.clicked.connect(self.reset_groups)
        self.new_tournament_button = QPushButton("Neues Turnier anlegen")
        self.new_tournament_button.clicked.connect(self.on_new_tournament)
        phase_row = QHBoxLayout()
        phase_row.setSpacing(8)
        phase_label = QLabel("Aktive Phase")
        phase_label.setObjectName("fieldCaption")
        phase_row.addWidget(phase_label)
        phase_row.addWidget(self.phase_selector, 1)
        self.phase_tools_button = QPushButton("Phase verwalten ▾")
        self.phase_tools_button.setObjectName("secondaryButton")
        phase_menu = QMenu(self.phase_tools_button)
        action_structure = phase_menu.addAction("Turnierstruktur bearbeiten")
        action_structure.triggered.connect(self.configure_structure)
        phase_menu.addSeparator()
        action_new_phase = phase_menu.addAction("Neue Phase anlegen")
        action_new_phase.triggered.connect(self.create_phase)
        action_reset_groups = phase_menu.addAction("Gruppen dieser Phase zurücksetzen")
        action_reset_groups.triggered.connect(self.reset_groups)
        self.phase_tools_button.setMenu(phase_menu)
        phase_row.addWidget(self.phase_tools_button)

        self.phase_info = QLabel()

        # Kompakte, klickbare Gruppenübersicht. Alle Gruppen sind auf einen
        # Blick sichtbar, ohne sie einzeln im Auswahlfeld durchklicken zu müssen.
        self.group_overview_frame = QFrame()
        self.group_overview_frame.setObjectName("groupOverviewFrame")
        self.group_overview_grid = QGridLayout(self.group_overview_frame)
        self.group_overview_grid.setContentsMargins(0, 2, 0, 2)
        self.group_overview_grid.setHorizontalSpacing(8)
        self.group_overview_grid.setVerticalSpacing(8)
        self.group_overview_buttons: list[QPushButton] = []

        self.group_count = QSpinBox()
        self.group_count.setRange(1, 26)
        self.group_count.setSuffix(" Gruppen")
        self.group_count.setToolTip("Legt die Anzahl der Gruppen für die ausgewählte Gruppenphase fest.")
        self.apply_group_count_button = QPushButton("Gruppenanzahl übernehmen")
        self.apply_group_count_button.setObjectName("primaryButton")
        self.apply_group_count_button.clicked.connect(self.apply_group_count)
        self.group_selector = QComboBox()
        self.group_selector.currentIndexChanged.connect(self.refresh_players)
        self.new_group_button = QPushButton("Neue Gruppe")
        self.new_group_button.clicked.connect(self.create_group)
        self.rename_group_button = QPushButton("Umbenennen")
        self.rename_group_button.clicked.connect(self.rename_group)
        self.delete_group_button = QPushButton("Löschen")
        self.delete_group_button.clicked.connect(self.delete_group)
        self.create_five_groups_button = QPushButton("Gruppen A–E neu erstellen")
        self.create_five_groups_button.clicked.connect(self.create_five_groups)
        self.auto_distribute_button = QPushButton("Automatisch verteilen")
        self.auto_distribute_button.clicked.connect(self.auto_distribute)
        self.freudenholm_button = QPushButton("Freudenholm-Vorlage anwenden")
        self.freudenholm_button.clicked.connect(self.apply_freudenholm_template)
        group_row = QGridLayout()
        group_row.setHorizontalSpacing(8)
        group_row.setVerticalSpacing(8)
        groups_label = QLabel("Gruppen")
        groups_label.setObjectName("fieldCaption")
        group_row.addWidget(groups_label, 0, 0)
        group_row.addWidget(self.group_count, 0, 1)
        group_row.addWidget(self.apply_group_count_button, 0, 2)
        current_group_label = QLabel("Auswahl")
        current_group_label.setObjectName("fieldCaption")
        group_row.addWidget(current_group_label, 1, 0)
        group_row.addWidget(self.group_selector, 1, 1, 1, 2)
        self.group_tools_button = QPushButton("Gruppe bearbeiten ▾")
        self.group_tools_button.setObjectName("secondaryButton")
        group_menu = QMenu(self.group_tools_button)
        group_menu.addAction("Neue Gruppe", self.create_group)
        group_menu.addAction("Gruppe umbenennen", self.rename_group)
        group_menu.addSeparator()
        group_menu.addAction("Gruppe löschen", self.delete_group)
        self.group_tools_button.setMenu(group_menu)
        group_row.addWidget(self.group_tools_button, 1, 3)
        group_row.setColumnStretch(1, 1)
        self.edit_intermediate_button = QPushButton("Spieler einteilen")
        self.edit_intermediate_button.setObjectName("primaryButton")
        self.edit_intermediate_button.setMinimumHeight(38)
        self.edit_intermediate_button.setToolTip(
            "Öffnet die Zwischenrundengruppen und ermöglicht eine neue Einteilung der Qualifizierten."
        )
        self.edit_intermediate_button.clicked.connect(self.edit_current_intermediate_groups)
        self.edit_intermediate_button.setVisible(False)
        group_row.addWidget(self.edit_intermediate_button, 2, 3, 1, 1)

        self.start_intermediate_button = QPushButton("Spielplan erstellen")
        self.start_intermediate_button.setObjectName("primaryButton")
        self.start_intermediate_button.setMinimumHeight(38)
        self.start_intermediate_button.setToolTip(
            "Erstellt bei Bedarf den gemeinsamen Spielplan für Gruppen F bis I und öffnet anschließend die Spiele."
        )
        self.start_intermediate_button.clicked.connect(self.start_current_intermediate_round)
        self.start_intermediate_button.setVisible(False)
        group_row.addWidget(self.start_intermediate_button, 2, 4, 1, 2)

        self.edit_final_pairings_button = QPushButton("Viertelfinal-Begegnungen festlegen")
        self.edit_final_pairings_button.setObjectName("primaryButton")
        self.edit_final_pairings_button.setMinimumHeight(40)
        self.edit_final_pairings_button.setToolTip(
            "Ordnet die acht K.-o.-Teilnehmer frei den vier Viertelfinalspielen zu."
        )
        self.edit_final_pairings_button.clicked.connect(self.edit_final_pairings)
        self.edit_final_pairings_button.setVisible(False)
        group_row.addWidget(self.edit_final_pairings_button, 1, 3, 1, 3)
        self.new_group_button.setVisible(False)
        self.rename_group_button.setVisible(False)
        self.delete_group_button.setVisible(False)
        self.group_action_buttons = (self.create_five_groups_button, self.auto_distribute_button, self.freudenholm_button)
        for button in self.group_action_buttons:
            button.setMinimumHeight(38)
            button.setMinimumWidth(145)
        self.create_five_groups_button.setMinimumWidth(245)
        self.auto_distribute_button.setMinimumWidth(185)
        self.freudenholm_button.setMinimumWidth(225)

        self.available = QListWidget()
        self.assigned = QListWidget()
        self.available.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.assigned.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.available.setMinimumHeight(180)
        self.assigned.setMinimumHeight(180)
        self.assign_button = QPushButton("→")
        self.move_button = QPushButton("⇄")
        self.remove_button = QPushButton("←")
        self.assign_button.setObjectName("assignmentArrowButton")
        self.move_button.setObjectName("assignmentArrowButton")
        self.remove_button.setObjectName("assignmentArrowButton")
        self.assign_button.setToolTip("Ausgewählte Spieler der aktuellen Gruppe zuordnen")
        self.move_button.setToolTip("Ausgewählte Spieler in eine andere Gruppe verschieben")
        self.remove_button.setToolTip("Ausgewählte Spieler aus der Gruppe entfernen")
        for button in (self.assign_button, self.move_button, self.remove_button):
            button.setFixedSize(52, 48)
        self.assign_button.clicked.connect(self.assign_player)
        self.move_button.clicked.connect(self.move_player)
        self.remove_button.clicked.connect(self.remove_player)
        self.available.itemDoubleClicked.connect(lambda _item: self.assign_player())
        self.assigned.itemDoubleClicked.connect(lambda _item: self.remove_player())

        assignment = QHBoxLayout()
        assignment.setSpacing(18)
        available_label = QLabel("Noch nicht zugeordnet")
        available_label.setObjectName("listPanelHeading")
        left = QVBoxLayout(); left.setSpacing(8); left.addWidget(available_label); left.addWidget(self.available)
        middle = QVBoxLayout(); middle.setSpacing(10); middle.addStretch(); middle.addWidget(self.assign_button, 0, Qt.AlignmentFlag.AlignHCenter); middle.addWidget(self.move_button, 0, Qt.AlignmentFlag.AlignHCenter); middle.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignHCenter); middle.addStretch()
        self.assigned_label = QLabel("Zugeordnete Spieler")
        self.assigned_label.setObjectName("listPanelHeading")
        right = QVBoxLayout(); right.setSpacing(8); right.addWidget(self.assigned_label); right.addWidget(self.assigned)
        assignment.addLayout(left); assignment.addLayout(middle); assignment.addLayout(right)

        self.group_status = QLabel("Noch kein Spielplan erzeugt.")
        self.group_status.setObjectName("phaseStatusPill")
        self.final_round_banner = QLabel()
        self.final_round_banner.setObjectName("finalRoundBanner")
        self.final_round_banner.setWordWrap(True)
        self.final_round_banner.setVisible(False)

        self.schedule_tables = QSpinBox()
        self.schedule_tables.setRange(1, 20)
        self.schedule_tables.setValue(self.tournament.table_count)
        self.schedule_tables.setSuffix(" Felder")
        saved_start = QTime.fromString(getattr(self.tournament, "schedule_start_time", "17:10"), "HH:mm")
        self.schedule_start = QTimeEdit(saved_start if saved_start.isValid() else QTime(17, 10))
        self.schedule_start.setDisplayFormat("HH:mm")
        self.schedule_duration = QSpinBox()
        self.schedule_duration.setRange(1, 180)
        self.schedule_duration.setValue(self.tournament.match_duration_minutes)
        self.schedule_duration.setSuffix(" Min.")
        self.schedule_strategy = QComboBox()
        self.schedule_strategy.addItem("Intelligent (empfohlen)", "smart")
        self.schedule_strategy.addItem("Fair (Pausen bevorzugen)", "fair")
        self.schedule_strategy.addItem("Schnell (Felder zuerst füllen)", "fast")
        self.schedule_tables.valueChanged.connect(self._save_planning_defaults)
        self.schedule_start.timeChanged.connect(self._save_planning_defaults)
        self.schedule_duration.valueChanged.connect(self._save_planning_defaults)
        schedule_settings = QGridLayout()
        schedule_settings.setHorizontalSpacing(12)
        schedule_settings.setVerticalSpacing(10)
        schedule_settings.addWidget(QLabel("Geplante Felder"), 0, 0)
        schedule_settings.addWidget(self.schedule_tables, 0, 1)
        schedule_settings.addWidget(QLabel("Startzeit"), 1, 0)
        schedule_settings.addWidget(self.schedule_start, 1, 1)
        schedule_settings.addWidget(QLabel("Spieldauer"), 2, 0)
        schedule_settings.addWidget(self.schedule_duration, 2, 1)
        schedule_settings.addWidget(QLabel("Planungsstrategie"), 3, 0)
        schedule_settings.addWidget(self.schedule_strategy, 3, 1)
        schedule_settings.setColumnStretch(1, 1)

        self.generate_schedule_button = QPushButton("Turnierplan aus Gruppen erstellen")
        self.generate_schedule_button.clicked.connect(self.generate_schedule)
        self.generate_all_schedules_button = QPushButton("Nur aktuelle Gruppe planen")
        self.generate_all_schedules_button.clicked.connect(self.generate_current_group_schedule)
        self.reset_schedule_button = QPushButton("Spielplan zurücksetzen")
        self.reset_schedule_button.clicked.connect(self.reset_schedule)
        self.schedule_quality_button = QPushButton("Spielplan prüfen")
        self.schedule_quality_button.setObjectName("secondaryButton")
        self.schedule_quality_button.clicked.connect(self.show_schedule_quality)
        self.qualification_rule_button = QPushButton("Qualifikation einstellen")
        self.qualification_rule_button.clicked.connect(self.configure_qualification)
        self.qualify_button = QPushButton("Qualifizierte übertragen")
        self.qualify_button.clicked.connect(self.qualify_players)
        self.qualify_all_button = QPushButton("Alle Qualifizierten übertragen")
        self.qualify_all_button.clicked.connect(self.qualify_all_players)
        self.continue_day_button = QPushButton("Turniertag fortsetzen")
        self.continue_day_button.clicked.connect(self.continue_tournament_day)
        self.intermediate_groups_button = QPushButton("Zwischenrunde einteilen")
        self.intermediate_groups_button.setObjectName("primaryButton")
        self.intermediate_groups_button.clicked.connect(self.open_intermediate_assignment)
        self.simulate_tournament_button = QPushButton("Testdurchlauf starten")
        self.simulate_tournament_button.clicked.connect(self.simulate_tournament)
        self.generate_schedule_button.setToolTip("Erzeugt aus allen eingeteilten Gruppen einen gemeinsamen Turnierplan mit Uhrzeiten und Feldern.")
        self.generate_all_schedules_button.setToolTip("Erzeugt nur die Paarungen der aktuell ausgewählten Gruppe.")
        self.reset_schedule_button.setToolTip("Löscht den Spielplan der aktuellen Gruppe nach Sicherheitsabfrage.")
        self.schedule_quality_button.setToolTip(
            "Prüft Auslastung, Doppelbelegungen, direkte Folgepartien und die Verteilung auf die Felder."
        )
        self.qualification_rule_button.setToolTip("Öffnet die Qualifikationseinstellungen für alle Gruppen dieser Phase.")
        self.qualify_button.setToolTip("Überträgt die direkten Qualifikanten der aktuellen Gruppe.")
        self.qualify_all_button.setToolTip("Überträgt alle direkten Qualifikanten und Sieger aus 3. gegen 4. aller fertigen Gruppen.")
        self.continue_day_button.setToolTip("Führt den Turniertag phasenweise fort: Qualifikationsspiele, Zwischenrunde, Viertelfinale, Halbfinale und Finale.")
        self.intermediate_groups_button.setToolTip("Öffnet die freie Einteilung der Qualifizierten auf vier Zwischenrundengruppen.")
        self.simulate_tournament_button.setToolTip("Simuliert alle noch offenen Spiele reproduzierbar bis zum Turniersieger. Bestehende Ergebnisse bleiben erhalten.")
        game_header = QHBoxLayout()
        self.game_title = QLabel("Spielbetrieb")
        self.game_title.setStyleSheet("font-weight: 600;")
        game_header.addWidget(self.game_title)
        game_header.addStretch()
        self.open_qualification_button = QPushButton("Qualifikationsspiele öffnen")
        self.open_qualification_button.setMinimumHeight(34)
        self.open_qualification_button.clicked.connect(self.open_qualification_games)
        game_header.addWidget(self.open_qualification_button)
        game_header.addWidget(self.group_status)

        # Die Aktionsschaltflächen werden bewusst auf zwei Zeilen verteilt.
        # So bleiben die Beschriftungen auch auf kleineren MacBook-Displays
        # vollständig sichtbar und werden nicht mehr abgeschnitten.
        self.game_actions = QGridLayout()
        self.game_actions.setHorizontalSpacing(10)
        self.game_actions.setVerticalSpacing(8)
        self.action_buttons = (
            self.generate_schedule_button,
            self.generate_all_schedules_button,
            self.schedule_quality_button,
            self.reset_schedule_button,
            self.qualification_rule_button,
            self.qualify_button,
            self.qualify_all_button,
            self.continue_day_button,
            self.intermediate_groups_button,
            self.simulate_tournament_button,
        )
        for button in self.action_buttons:
            button.setMinimumWidth(150)
            button.setMinimumHeight(34)
        self._layout_game_actions(3)

        self.group_matches = QTableWidget(0, 7)
        self.group_matches.setObjectName("professionalMatchTable")
        self.group_matches.setHorizontalHeaderLabels(["Zeit", "Feld", "Runde", "Spieler 1", "Spieler 2", "Ergebnis", "Aktion"])
        self.group_matches.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.group_matches.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.group_matches.setAlternatingRowColors(True)
        self.group_matches.verticalHeader().setDefaultSectionSize(46)
        header = self.group_matches.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.group_matches.cellDoubleClicked.connect(self._open_result_from_group_row)
        self.group_standings = QTableWidget(0, 11)
        self.group_standings.setObjectName("professionalStandingsTable")
        self.group_standings.setHorizontalHeaderLabels(["Pl.", "Spieler", "Sp", "S", "N", "Sätze", "Diff.", "Bälle", "Ball-Diff.", "Direkt", "Pkt."])
        self.group_standings.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.group_standings.setAlternatingRowColors(True)
        self.group_standings.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.group_standings.verticalHeader().setVisible(False)
        standings_header = self.group_standings.horizontalHeader()
        standings_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        standings_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 11):
            standings_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        self.game_tabs = QTabWidget()
        self.game_tabs.setObjectName("tournamentWorkspaceTabs")
        # Auf kleinen MacBook-Displays darf der eigentliche Spielbereich nicht
        # auf eine schmale Tab-Leiste zusammenschrumpfen.
        self.game_tabs.setMinimumHeight(300)
        self.game_tabs.setDocumentMode(True)
        matches_page = QWidget()
        matches_page.setObjectName("workspaceTabPage")
        matches_layout = QVBoxLayout(matches_page)
        matches_layout.setContentsMargins(0, 8, 0, 0)
        matches_layout.addWidget(self.group_matches)
        standings_page = QWidget()
        standings_page.setObjectName("workspaceTabPage")
        standings_layout = QVBoxLayout(standings_page)
        standings_layout.setContentsMargins(0, 8, 0, 0)
        standings_layout.addWidget(self.group_standings)
        self.game_tabs.addTab(matches_page, "Spielplan")
        self.game_tabs.addTab(standings_page, "Tabelle")

        # Eigener, gruppenübergreifender Bereich für die Qualifikationsspiele.
        # Dadurch müssen die Nutzer nicht mehr jede Gruppe einzeln auswählen,
        # um die Spiele Platz 3 gegen Platz 4 zu finden.
        self.qualification_table = QTableWidget(0, 7)
        self.qualification_table.setHorizontalHeaderLabels(
            ["Zeit", "Feld", "Runde", "Spieler 1", "Spieler 2", "Ergebnis 1", "Ergebnis 2"]
        )
        self.qualification_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.qualification_table.setMinimumHeight(230)
        self.qualification_table.verticalHeader().setDefaultSectionSize(42)
        self.qualification_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.qualification_info = QLabel(
            "Nach Abschluss aller Gruppenspiele hier die Qualifikationsspiele erzeugen und die Ergebnisse eintragen."
        )
        self.qualification_info.setWordWrap(True)
        self.prepare_qualification_button = QPushButton("Qualifikationsspiele erzeugen / anzeigen")
        self.large_qualification_button = QPushButton("Qualifikationsspiele groß anzeigen")
        self.large_qualification_button.setMinimumHeight(38)
        self.large_qualification_button.clicked.connect(self.show_qualification_games_dialog)
        self.prepare_qualification_button.clicked.connect(self.prepare_qualification_view)
        self.save_qualification_button = QPushButton("Ergebnisse speichern und Zwischenrunde erzeugen")
        self.save_qualification_button.clicked.connect(self.save_qualification_and_continue)
        qualification_actions = QHBoxLayout()
        qualification_actions.addWidget(self.prepare_qualification_button)
        qualification_actions.addWidget(self.large_qualification_button)
        qualification_actions.addWidget(self.save_qualification_button)
        qualification_page = QWidget()
        qualification_page.setObjectName("workspaceTabPage")
        qualification_layout = QVBoxLayout(qualification_page)
        qualification_layout.setContentsMargins(0, 8, 0, 0)
        qualification_layout.addWidget(self.qualification_info)
        qualification_layout.addLayout(qualification_actions)
        qualification_layout.addWidget(self.qualification_table)
        self.qualification_tab_index = self.game_tabs.addTab(qualification_page, "Qualifikation")
        self.game_tabs.currentChanged.connect(self._game_tab_changed)

        back = QPushButton("Zurück zur Spielerauswahl")
        back.clicked.connect(self.on_back)
        forward = QPushButton("Zum Turniertag – Ergebnisse eingeben")
        forward.setObjectName("primaryButton")
        forward.clicked.connect(self.open_current_phase_live_center)

        # MSTTS 9.3.2: reduzierte Hauptansicht mit klar getrennten Arbeitsreitern.
        self.title.setObjectName("pageTitle")
        self.phase_info.setObjectName("pageSubtitle")
        self.phase_info.setWordWrap(True)
        self.structure_button.setObjectName("secondaryAction")
        self.reset_groups_button.setObjectName("secondaryAction")
        self.new_tournament_button.setVisible(False)
        new_phase.setObjectName("primaryButton")

        selector_card = QFrame()
        selector_card.setObjectName("phaseCommandBar")
        selector_layout = QVBoxLayout(selector_card)
        selector_layout.setContentsMargins(14, 10, 14, 10)
        selector_layout.setSpacing(8)
        selector_layout.addLayout(phase_row)
        selector_layout.addWidget(self.phase_info)
        overview_caption = QLabel("Gruppenstatus")
        overview_caption.setObjectName("sectionCaption")
        selector_layout.addWidget(overview_caption)
        selector_layout.addWidget(self.group_overview_frame)
        selector_layout.addLayout(group_row)

        # Alpha 8: Turnierbereitschaft direkt unter der Gruppenauswahl.
        # Die wichtigsten Kennzahlen und die nächste sinnvolle Aktion bleiben
        # damit sichtbar, ohne erst in einen anderen Reiter wechseln zu müssen.
        self.readiness_frame = QFrame()
        self.readiness_frame.setObjectName("readinessFrame")
        self.readiness_frame.setStyleSheet(
            "QFrame#readinessFrame { background:#160A2B; border:1px solid #7337B4; border-radius:10px; }"
        )
        readiness_layout = QHBoxLayout(self.readiness_frame)
        readiness_layout.setContentsMargins(14, 10, 14, 10)
        readiness_layout.setSpacing(18)
        self.readiness_title = QLabel("TURNIERBEREITSCHAFT")
        self.readiness_title.setStyleSheet("font-size: 12px; font-weight: 900; color:#D7B8F5;")
        self.readiness_players = QLabel()
        self.readiness_groups = QLabel()
        self.readiness_matches = QLabel()
        self.readiness_open = QLabel()
        for metric in (self.readiness_players, self.readiness_groups, self.readiness_matches, self.readiness_open):
            metric.setStyleSheet("font-weight: 700;")
        self.readiness_action = QPushButton("Turnier vorbereiten")
        self.readiness_action.setObjectName("primaryButton")
        self.readiness_action.setMinimumHeight(36)
        self.readiness_action.clicked.connect(self._run_readiness_action)
        readiness_layout.addWidget(self.readiness_title)
        readiness_layout.addWidget(self.readiness_players)
        readiness_layout.addWidget(self.readiness_groups)
        readiness_layout.addWidget(self.readiness_matches)
        readiness_layout.addWidget(self.readiness_open)
        readiness_layout.addStretch(1)
        readiness_layout.addWidget(self.readiness_action)
        selector_layout.addWidget(self.readiness_frame)

        # Planung links: nur die vier tatsächlich häufig benötigten Werte.
        settings_card = QFrame()
        settings_card.setObjectName("workspaceSection")
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(16, 14, 16, 14)
        settings_layout.setSpacing(12)
        settings_heading = QLabel("Planung")
        settings_heading.setObjectName("cardHeading")
        settings_description = QLabel("Lege die grundlegenden Einstellungen für die Gruppenphase fest.")
        settings_description.setObjectName("cardDescription")
        settings_description.setWordWrap(True)
        settings_layout.addWidget(settings_heading)
        settings_layout.addWidget(settings_description)
        settings_layout.addLayout(schedule_settings)
        settings_layout.addStretch()
        self.overview_status = QLabel("Spieler zuerst einer Gruppe zuordnen, danach den Spielplan erstellen.")
        self.overview_status.setWordWrap(True)
        self.overview_status.setObjectName("infoBanner")
        settings_layout.addWidget(self.overview_status)

        # Spielerzuordnung rechts. Die Listen erhalten ausreichend Höhe und
        # werden nicht mehr zwischen Spielplan und Aktionsleisten eingequetscht.
        assignment_card = QFrame()
        assignment_card.setObjectName("workspaceSection")
        assignment_card_layout = QVBoxLayout(assignment_card)
        assignment_card_layout.setContentsMargins(16, 14, 16, 14)
        assignment_card_layout.setSpacing(10)
        assignment_heading = QLabel("Spielerzuordnung")
        assignment_heading.setObjectName("cardHeading")
        assignment_description = QLabel("Ordne Spieler den Gruppen zu oder verteile sie automatisch.")
        assignment_description.setObjectName("cardDescription")
        assignment_description.setWordWrap(True)
        assignment_card_layout.addWidget(assignment_heading)
        assignment_card_layout.addWidget(assignment_description)
        assignment_widget = QWidget()
        assignment_widget.setObjectName("assignmentWorkspace")
        assignment_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        assignment_widget.setLayout(assignment)
        assignment_card_layout.addWidget(assignment_widget, 1)

        overview_page = QWidget()
        overview_page.setObjectName("workspaceTabPage")
        overview_layout = QHBoxLayout(overview_page)
        overview_layout.setContentsMargins(8, 12, 8, 8)
        overview_layout.setSpacing(12)
        overview_layout.addWidget(settings_card, 1)
        overview_layout.addWidget(assignment_card, 2)

        # Der Überblick ist der erste Reiter; alle datenreichen Bereiche sind
        # getrennt und können nicht mehr übereinander liegen.
        self.game_tabs.insertTab(0, overview_page, "Einteilung")
        self.qualification_tab_index += 1
        self.game_tabs.setCurrentIndex(0)

        # Turnieroptionen: fokussierter Arbeitsbereich statt mehrerer gleichzeitig
        # sichtbarer Aktionszeilen. Links wird eine Kategorie gewählt, rechts
        # erscheinen ausschließlich die dazugehörigen Aktionen.
        advanced_page = QWidget()
        advanced_page.setObjectName("workspaceTabPage")
        advanced_layout = QVBoxLayout(advanced_page)
        advanced_layout.setContentsMargins(10, 8, 10, 8)
        advanced_layout.setSpacing(8)

        advanced_header = QHBoxLayout()
        advanced_header.setSpacing(12)
        advanced_header_text = QVBoxLayout()
        advanced_header_text.setSpacing(2)
        advanced_heading = QLabel("Turnieroptionen")
        advanced_heading.setObjectName("cardHeading")
        advanced_intro = QLabel("Wähle links einen Bereich. Rechts werden nur die passenden Aktionen angezeigt.")
        advanced_intro.setObjectName("pageSubtitle")
        advanced_intro.setWordWrap(True)
        advanced_header_text.addWidget(advanced_heading)
        advanced_header_text.addWidget(advanced_intro)
        advanced_header.addLayout(advanced_header_text, 1)
        self.group_status.setWordWrap(True)
        self.group_status.setMaximumWidth(300)
        advanced_header.addWidget(self.group_status, 0, Qt.AlignmentFlag.AlignTop)
        advanced_layout.addLayout(advanced_header)

        phase_options_card = QFrame()
        phase_options_card.setObjectName("workspaceSection")
        phase_options_layout = QVBoxLayout(phase_options_card)
        phase_options_layout.setContentsMargins(14, 12, 14, 12)
        phase_options_layout.setSpacing(7)
        phase_options_title = QLabel("Optionaler Turnierablauf")
        phase_options_title.setObjectName("cardHeading")
        phase_options_text = QLabel(
            "Qualifikation und Zwischenrunde können je nach Teilnehmerzahl ein- oder ausgeschaltet werden. "
            "Nicht verfügbare Phasen werden automatisch übersprungen."
        )
        phase_options_text.setObjectName("pageSubtitle")
        phase_options_text.setWordWrap(True)
        self.qualification_enabled_check = QCheckBox("Qualifikationsspiele verwenden")
        self.intermediate_enabled_check = QCheckBox("Zwischenrunde verwenden")
        self.qualification_availability = QLabel()
        self.intermediate_availability = QLabel()
        self.qualification_availability.setObjectName("pageSubtitle")
        self.intermediate_availability.setObjectName("pageSubtitle")
        self.save_phase_options_button = QPushButton("Turnierablauf übernehmen")
        self.save_phase_options_button.setObjectName("primaryButton")
        self.save_phase_options_button.setMinimumHeight(38)
        self.save_phase_options_button.clicked.connect(self.save_optional_phase_settings)
        phase_options_layout.addWidget(phase_options_title)
        phase_options_layout.addWidget(phase_options_text)
        phase_options_layout.addWidget(self.qualification_enabled_check)
        phase_options_layout.addWidget(self.qualification_availability)
        phase_options_layout.addWidget(self.intermediate_enabled_check)
        phase_options_layout.addWidget(self.intermediate_availability)
        phase_options_layout.addWidget(self.save_phase_options_button, 0, Qt.AlignmentFlag.AlignLeft)
        advanced_layout.addWidget(phase_options_card)

        workspace = QFrame()
        workspace.setObjectName("workspaceSection")
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(10, 10, 10, 10)
        workspace_layout.setSpacing(12)

        category_panel = QFrame()
        category_panel.setObjectName("optionCategoryPanel")
        category_layout = QVBoxLayout(category_panel)
        category_layout.setContentsMargins(6, 6, 6, 6)
        category_layout.setSpacing(6)

        option_stack = QStackedWidget()
        option_stack.setObjectName("optionStack")

        category_buttons: list[QPushButton] = []

        def action_page(title: str, description: str, buttons: tuple[QPushButton, ...]) -> QWidget:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(16, 14, 16, 14)
            page_layout.setSpacing(10)
            heading = QLabel(title)
            heading.setObjectName("cardHeading")
            text = QLabel(description)
            text.setObjectName("pageSubtitle")
            text.setWordWrap(True)
            page_layout.addWidget(heading)
            page_layout.addWidget(text)

            action_grid = QGridLayout()
            action_grid.setHorizontalSpacing(10)
            action_grid.setVerticalSpacing(10)
            for index, button in enumerate(buttons):
                button.setMinimumHeight(42)
                button.setMaximumHeight(46)
                button.setMinimumWidth(190)
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                action_grid.addWidget(button, index // 2, index % 2)
            action_grid.setColumnStretch(0, 1)
            action_grid.setColumnStretch(1, 1)
            page_layout.addLayout(action_grid)
            page_layout.addStretch(1)
            return page

        categories = (
            ("Gruppen", "Gruppen vorbereiten",
             "Vorlage verwenden, fünf Gruppen anlegen oder Spieler automatisch verteilen.",
             (self.freudenholm_button, self.create_five_groups_button, self.auto_distribute_button)),
            ("Spielplan", "Spielplan verwalten",
             "Spielpläne erstellen, Qualität prüfen oder vorhandene Pläne zurücksetzen.",
             (self.generate_all_schedules_button, self.generate_schedule_button, self.schedule_quality_button, self.reset_schedule_button)),
            ("Übergang", "Qualifikation & nächste Phase",
             "Qualifikationsregel bearbeiten, Qualifikationsspiele öffnen und den Turniertag fortsetzen.",
             (self.qualification_rule_button, self.open_qualification_button, self.continue_day_button)),
            ("Werkzeuge", "Test & Werkzeuge",
             "Zwischenrunde einteilen, Qualifizierte übertragen oder einen Testdurchlauf starten.",
             (self.intermediate_groups_button, self.qualify_button, self.simulate_tournament_button)),
        )

        for index, (label, title, description, buttons) in enumerate(categories):
            selector = QPushButton(label)
            selector.setCheckable(True)
            selector.setAutoExclusive(True)
            selector.setObjectName("optionCategoryButton")
            selector.setMinimumHeight(42)
            selector.clicked.connect(lambda checked=False, i=index: option_stack.setCurrentIndex(i))
            category_layout.addWidget(selector)
            category_buttons.append(selector)
            option_stack.addWidget(action_page(title, description, buttons))

        category_layout.addStretch(1)
        category_buttons[0].setChecked(True)
        option_stack.setCurrentIndex(0)

        workspace_layout.addWidget(category_panel, 0)
        workspace_layout.addWidget(option_stack, 1)
        advanced_layout.addWidget(workspace, 1)

        self.game_tabs.addTab(advanced_page, "Ablauf & Optionen")

        back = QPushButton("← Spieler")
        back.setToolTip("Zurück zur Spielerverwaltung")
        back.setMinimumHeight(32)
        back.setMaximumWidth(105)
        back.clicked.connect(self.on_back)
        forward = QPushButton("Zum Turniertag – Ergebnisse eingeben")
        forward.setObjectName("primaryButton")
        forward.setMinimumHeight(34)
        forward.setMaximumWidth(255)
        forward.clicked.connect(self.open_current_phase_live_center)
        self.generate_all_schedules_button.setText("Nur diese Gruppe planen")
        self.open_qualification_button.setText("Qualifikationsspiele öffnen")

        # Die Navigation sitzt nun in der Kopfzeile. Dadurch bleibt der gesamte
        # untere Bereich für Kategorien und Aktionen frei und kein Button kann
        # Inhalte der Turnieroptionen überdecken.
        title_bar = QHBoxLayout()
        title_bar.setSpacing(7)
        title_bar.addWidget(self.title)
        title_bar.addStretch(1)
        title_bar.addWidget(back)
        title_bar.addWidget(forward)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 8, 18, 10)
        layout.setSpacing(7)
        layout.addLayout(title_bar)
        layout.addWidget(selector_card)
        layout.addWidget(self.final_round_banner)
        layout.addWidget(self.game_tabs, 1)
        self.refresh()


    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "global_watermark") and hasattr(self, "main_content"):
            self.global_watermark.setGeometry(self.main_content.rect())
            self.global_watermark.raise_()
        compact = self.width() < 900
        columns = notebook_layout_columns(self.width())
        self._layout_game_actions(columns)
        for button in self.group_action_buttons:
            button.setMinimumWidth(95 if compact else 145)
        self.create_five_groups_button.setMinimumWidth(145 if compact else 205)
        self.auto_distribute_button.setMinimumWidth(135 if compact else 185)
        self.freudenholm_button.setMinimumWidth(155 if compact else 225)

    def _layout_game_actions(self, columns: int) -> None:
        while self.game_actions.count():
            self.game_actions.takeAt(0)
        for index, button in enumerate(self.action_buttons):
            button.setMinimumWidth(135 if columns < 3 else 185)
            self.game_actions.addWidget(button, index // columns, index % columns)
        for column in range(3):
            self.game_actions.setColumnStretch(column, 1 if column < columns else 0)

    def _run_readiness_action(self) -> None:
        phase = self.current_phase()
        if phase is None:
            return
        if phase.phase_type is PhaseType.GROUP_STAGE and not any(group.matches for group in phase.groups):
            self.generate_all_schedules()
            return
        if phase.phase_type is PhaseType.FINAL_ROUND and not phase.matches:
            self.generate_schedule()
            return
        self.open_current_phase_live_center()

    def _refresh_readiness(self, phase) -> None:
        if phase is None:
            self.readiness_frame.setVisible(False)
            return
        self.readiness_frame.setVisible(True)
        if phase.phase_type is PhaseType.FINAL_ROUND:
            participants = len(phase.participant_ids)
            matches = len(phase.matches)
            completed = sum(match.result is not None for match in phase.matches)
            self.readiness_players.setText(f"{participants} Teilnehmer")
            self.readiness_groups.setText("K.-o.-Phase")
            self.readiness_matches.setText(f"{matches} Spiele")
            self.readiness_open.setText(f"{max(0, matches - completed)} offen")
            self.readiness_action.setText("K.-o.-Leitstand öffnen" if matches else "Finalrunde auslosen")
            self.readiness_action.setEnabled(participants >= 2)
            return

        playable = [group for group in phase.groups if len(group.participant_ids) >= 2]
        assigned = sum(len(group.participant_ids) for group in phase.groups)
        matches = sum(len(group.matches) for group in phase.groups)
        completed = sum(
            sum(match.result is not None for match in group.matches)
            for group in phase.groups
        )
        self.readiness_players.setText(f"{assigned} Spieler zugeordnet")
        self.readiness_groups.setText(f"{len(playable)}/{len(phase.groups)} Gruppen spielbereit")
        self.readiness_matches.setText(f"{matches} Begegnungen")
        self.readiness_open.setText(f"{max(0, matches - completed)} offen")
        if not playable:
            self.readiness_action.setText("Mindestens 2 Spieler zuordnen")
            self.readiness_action.setEnabled(False)
        elif matches == 0:
            self.readiness_action.setText("Turnierplan jetzt erstellen")
            self.readiness_action.setEnabled(True)
        else:
            self.readiness_action.setText("Turniertag im Leitstand öffnen")
            self.readiness_action.setEnabled(True)

    def open_current_phase_live_center(self) -> None:
        """Open the live center with the phase currently selected in this page.

        Passing the phase id explicitly prevents the live view from falling
        back to an older phase after saving or recording a result.
        """
        phase = self.current_phase()
        self.on_continue(phase.id if phase is not None else None)

    def activate(self) -> None:
        if not self.tournament.phases:
            try:
                self.service.ensure_group_stage()
                self.on_saved()
            except ValidationError as error:
                show_error(self, error)
        self.refresh()

    def _refresh_optional_phase_settings(self) -> None:
        try:
            qualification_available, intermediate_available = self.service.phase_option_availability()
        except ValidationError:
            qualification_available, intermediate_available = False, False
        tournament = self.service.require_tournament()

        self.qualification_enabled_check.blockSignals(True)
        self.intermediate_enabled_check.blockSignals(True)
        self.qualification_enabled_check.setChecked(bool(tournament.qualification_enabled and qualification_available))
        self.intermediate_enabled_check.setChecked(bool(tournament.intermediate_enabled and intermediate_available))
        self.qualification_enabled_check.setEnabled(qualification_available)
        self.intermediate_enabled_check.setEnabled(intermediate_available)
        self.qualification_enabled_check.blockSignals(False)
        self.intermediate_enabled_check.blockSignals(False)

        self.qualification_availability.setText(
            "Verfügbar: mindestens eine Gruppe hat 4 oder mehr Spieler."
            if qualification_available else
            "Nicht verfügbar: Dafür benötigt mindestens eine Gruppe 4 Spieler (Platz 3 gegen Platz 4)."
        )
        self.intermediate_availability.setText(
            "Verfügbar: mindestens 16 Spieler sind der ersten Gruppenphase zugeordnet."
            if intermediate_available else
            "Standard-Zwischenrunde ab 16 Spielern; kleinere oder andere Formate über „Qualifikation einstellen“ flexibel planen."
        )
        qualification_active = bool(tournament.qualification_enabled and qualification_available)
        if hasattr(self, "qualification_tab_index"):
            self.game_tabs.setTabEnabled(self.qualification_tab_index, qualification_active)
        self.open_qualification_button.setEnabled(qualification_active)
        self.qualification_rule_button.setEnabled(qualification_available)
        self.intermediate_groups_button.setEnabled(bool(tournament.intermediate_enabled and intermediate_available))

    def save_optional_phase_settings(self) -> None:
        try:
            self.service.set_optional_phases(
                qualification_enabled=self.qualification_enabled_check.isChecked(),
                intermediate_enabled=self.intermediate_enabled_check.isChecked(),
            )
            self.tournament = self.service.require_tournament()
            self.on_saved()
            self.refresh()
            qualification_active, intermediate_active = self.service.effective_optional_phases()
            path = ["Gruppenphase"]
            if qualification_active:
                path.append("Qualifikation")
            if intermediate_active:
                path.append("Zwischenrunde")
            path.append("K.-o.-Phase")
            QMessageBox.information(self, "Turnierablauf gespeichert", "Aktiver Ablauf: " + " → ".join(path))
        except ValidationError as error:
            show_error(self, error)
            self._refresh_optional_phase_settings()

    def current_phase(self):
        phase_id = self.phase_selector.currentData()
        return self.tournament.phase(phase_id) if phase_id else None

    def current_group(self):
        phase = self.current_phase()
        group_id = self.group_selector.currentData()
        return phase.group(group_id) if phase is not None and group_id else None

    def qualification_source_phase(self):
        """Return the source group stage for qualification playoffs.

        Qualification results must remain viewable after the UI has already
        switched to the intermediate round.  If an intermediate round exists,
        every source group with four or more players automatically receives the
        universal places-3-vs-4 playoff, regardless of group name or profile.
        """
        phases = sorted(
            (phase for phase in self.tournament.phases if phase.phase_type is PhaseType.GROUP_STAGE),
            key=lambda phase: phase.position,
        )
        if not phases:
            return None
        source = phases[0]
        try:
            self.service.sync_intermediate_qualification_playoffs(source.id)
            self.tournament = self.service.require_tournament()
            source = self.tournament.phase(source.id)
        except ValidationError:
            pass
        return source

    def refresh(self) -> None:
        current = self.phase_selector.currentData()
        self._refreshing = True
        self.phase_selector.clear()
        selected = -1
        for index, phase in enumerate(sorted(self.tournament.phases, key=lambda value: value.position)):
            self.phase_selector.addItem(f"{phase.position}. {phase.name}", phase.id)
            if phase.id == current:
                selected = index
        if selected >= 0:
            self.phase_selector.setCurrentIndex(selected)
        self._refreshing = False
        self.refresh_groups()
        self._refresh_optional_phase_settings()

    def _select_group_from_overview(self, group_id: UUID) -> None:
        index = self.group_selector.findData(group_id)
        if index >= 0:
            self.group_selector.setCurrentIndex(index)
            self.game_tabs.setCurrentIndex(0)

    def _refresh_group_overview(self, phase) -> None:
        while self.group_overview_grid.count():
            item = self.group_overview_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.group_overview_buttons.clear()

        if phase is None or phase.phase_type is not PhaseType.GROUP_STAGE or not phase.groups:
            self.group_overview_frame.setVisible(False)
            return

        self.group_overview_frame.setVisible(True)
        selected_id = self.group_selector.currentData()
        columns = 4 if self.width() < 1050 else min(6, max(1, len(phase.groups)))
        for index, group in enumerate(phase.groups):
            total_matches = len(group.matches)
            completed = sum(match.result is not None for match in group.matches)
            open_matches = max(0, total_matches - completed)
            if total_matches == 0:
                status_text = "noch nicht geplant"
                background, border, foreground = "#140925", "#7440B0", "#CDBBE8"
            elif open_matches:
                status_text = f"{open_matches} offen"
                background, border, foreground = "#160A2B", "#9A592F", "#E3C7FF"
            else:
                status_text = "abgeschlossen"
                background, border, foreground = "#140925", "#7132A8", "#CDBBE8"

            button = QPushButton(
                f"{group.name}\n{len(group.participant_ids)} Spieler · {total_matches} Spiele\n{status_text}"
            )
            button.setCheckable(True)
            button.setChecked(group.id == selected_id)
            button.setMinimumHeight(68)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setToolTip(f"{group.name} auswählen und Spieler sowie Spielplan anzeigen")
            button.setStyleSheet(
                "QPushButton { text-align: left; padding: 8px 11px; border-radius: 9px; "
                f"background: {background}; border: 1px solid {border}; color: {foreground}; "
                "font-weight: 650; } "
                "QPushButton:checked { border: 2px solid #b96b2b; padding: 7px 10px; }"
            )
            button.clicked.connect(lambda checked=False, group_id=group.id: self._select_group_from_overview(group_id))
            self.group_overview_buttons.append(button)
            self.group_overview_grid.addWidget(button, index // columns, index % columns)
        for column in range(columns):
            self.group_overview_grid.setColumnStretch(column, 1)

    def refresh_groups(self, _index: int = -1) -> None:
        if self._refreshing:
            return
        phase = self.current_phase()
        is_intermediate = bool(
            phase is not None
            and phase.phase_type is PhaseType.GROUP_STAGE
            and phase.position > 1
            and bool(phase.groups)
        )
        self.edit_intermediate_button.setVisible(is_intermediate)
        if is_intermediate:
            self.game_tabs.setCurrentIndex(0)
        self.group_selector.blockSignals(True)
        self.group_selector.clear()
        if phase is None:
            self.phase_info.setText("Lege zuerst eine Gruppenphase oder Finalrunde an.")
            self.group_count.setEnabled(False)
            self.apply_group_count_button.setEnabled(False)
            self.new_group_button.setEnabled(False)
            self.rename_group_button.setEnabled(False)
            self.delete_group_button.setEnabled(False)
            self.create_five_groups_button.setEnabled(False)
            self.auto_distribute_button.setEnabled(False)
            self.freudenholm_button.setEnabled(False)
        elif phase.phase_type is PhaseType.FINAL_ROUND:
            self.phase_info.setText(f"Finalrunde mit {len(phase.participant_ids)} Teilnehmer(n)")
            self.group_count.setEnabled(False)
            self.apply_group_count_button.setEnabled(False)
            self.new_group_button.setEnabled(False)
            self.rename_group_button.setEnabled(False)
            self.delete_group_button.setEnabled(False)
            self.create_five_groups_button.setEnabled(False)
            self.auto_distribute_button.setEnabled(False)
            self.freudenholm_button.setEnabled(False)
        else:
            self.group_count.blockSignals(True)
            self.group_count.setValue(max(1, len(phase.groups)))
            self.group_count.blockSignals(False)
            self.group_count.setEnabled(True)
            self.apply_group_count_button.setEnabled(True)
            self.phase_info.setText(f"Zwischenrunde mit {len(phase.groups)} Gruppen – Ergebnisse werden im Turniertag eingetragen." if is_intermediate else f"Gruppenphase mit {len(phase.groups)} Gruppe(n)")
            # Buttons bleiben bedienbar. Falls bereits Spielpläne existieren,
            # erklärt die jeweilige Aktion den Konflikt und bietet ein sicheres
            # Zurücksetzen an, statt scheinbar kommentarlos deaktiviert zu sein.
            self.new_group_button.setEnabled(True)
            self.rename_group_button.setEnabled(bool(phase.groups))
            self.delete_group_button.setEnabled(bool(phase.groups))
            self.create_five_groups_button.setEnabled(True)
            self.auto_distribute_button.setEnabled(bool(phase.groups) and bool(self.tournament.people))
            self.freudenholm_button.setEnabled(bool(self.tournament.people))
            for group in phase.groups:
                self.group_selector.addItem(group.name, group.id)
        is_intermediate = bool(
            phase is not None
            and phase.phase_type is PhaseType.GROUP_STAGE
            and phase.position > 1
            and bool(phase.groups)
        )
        self.edit_intermediate_button.setVisible(is_intermediate)
        self.start_intermediate_button.setVisible(is_intermediate)
        is_final = bool(phase is not None and phase.phase_type is PhaseType.FINAL_ROUND)
        self.edit_final_pairings_button.setVisible(is_final)
        self.edit_final_pairings_button.setEnabled(is_final and len(phase.participant_ids) == 8)
        self.group_selector.blockSignals(False)
        self._refresh_group_overview(phase)
        self._refresh_readiness(phase)
        self.refresh_players()

    def refresh_players(self, _index: int = -1) -> None:
        self.available.clear()
        self.assigned.clear()
        phase = self.current_phase()
        group = self.current_group()
        if phase is not None and phase.phase_type is PhaseType.GROUP_STAGE:
            selected_id = group.id if group is not None else None
            for button, overview_group in zip(self.group_overview_buttons, phase.groups):
                button.setChecked(overview_group.id == selected_id)
        if phase is None:
            self.refresh_gameplay()
            return
        if phase.phase_type is PhaseType.FINAL_ROUND:
            self.assign_button.setEnabled(True)
            self.remove_button.setEnabled(True)
            self.move_button.setEnabled(False)
            self.assigned_label.setText("Teilnehmer der Finalrunde")
            for person in sorted(self.tournament.people, key=lambda value: (value.last_name.casefold(), value.first_name.casefold())):
                item = QListWidgetItem(person.full_name)
                item.setData(Qt.ItemDataRole.UserRole, person.id)
                (self.assigned if person.id in phase.participant_ids else self.available).addItem(item)
            self.refresh_gameplay()
            return
        enabled = group is not None
        roster_editable = enabled and not bool(group.matches if group is not None else [])
        # Die Aktionen bleiben verfügbar. Existiert bereits ein Spielplan,
        # fragt die Aktion vor einer Änderung nach und setzt den Plan auf Wunsch zurück.
        self.assign_button.setEnabled(enabled)
        self.move_button.setEnabled(enabled and len(phase.groups) > 1)
        self.remove_button.setEnabled(enabled)
        self.assigned_label.setText("Spieler dieser Gruppe")
        if not enabled:
            self.refresh_gameplay()
            return
        assigned_in_phase = {person_id for item in phase.groups for person_id in item.participant_ids}
        for person in sorted(self.tournament.people, key=lambda value: (value.last_name.casefold(), value.first_name.casefold())):
            item = QListWidgetItem(person.full_name)
            item.setData(Qt.ItemDataRole.UserRole, person.id)
            if person.id in group.participant_ids:
                self.assigned.addItem(item)
            elif person.id not in assigned_in_phase:
                self.available.addItem(item)
        self.refresh_gameplay()

    def _person_name(self, person_id: UUID) -> str:
        try:
            return self.tournament.person(person_id).full_name
        except ValidationError:
            return str(person_id)

    def refresh_gameplay(self) -> None:
        self._game_refreshing = True
        self.group_matches.setRowCount(0)
        self.group_standings.setRowCount(0)
        self.qualification_table.setRowCount(0)
        phase = self.current_phase()
        group = self.current_group()
        self._refresh_readiness(phase)
        if phase is None:
            self.open_qualification_button.setEnabled(False)
            self.group_status.setText("Keine Phase ausgewählt.")
            self.generate_schedule_button.setEnabled(False)
            self.generate_all_schedules_button.setEnabled(False)
            self.reset_schedule_button.setEnabled(False)
            self.schedule_quality_button.setEnabled(False)
            self.qualification_rule_button.setEnabled(False)
            self.qualify_button.setEnabled(False)
            self.qualify_all_button.setEnabled(False)
            self.intermediate_groups_button.setEnabled(False)
            self._game_refreshing = False
            return
        if phase.phase_type is PhaseType.FINAL_ROUND:
            self.open_qualification_button.setEnabled(False)
            self.open_qualification_button.setVisible(False)
            self.game_title.setText("Finalrunde")
            self.game_tabs.setTabText(0, "Einteilung")
            self.game_tabs.setTabText(1, "K.-o.-Begegnungen")
            self.game_tabs.setTabText(2, "Tabelle")
            self.group_standings.setVisible(False)
            self.final_round_banner.setVisible(True)
            self.generate_schedule_button.setText("Finalrunde auslosen")
            self.generate_schedule_button.setEnabled(len(phase.participant_ids) >= 2 and not phase.matches)
            self.generate_all_schedules_button.setEnabled(False)
            self.schedule_quality_button.setEnabled(False)
            self.qualification_rule_button.setEnabled(False)
            self.qualify_button.setEnabled(False)
            self.qualify_all_button.setEnabled(False)
            self.intermediate_groups_button.setEnabled(False)
            if not phase.matches:
                self.group_status.setText(f"{len(phase.participant_ids)} Teilnehmer")
                self.final_round_banner.setText("Finalrunde bereit · Viertelfinal-Begegnungen festlegen und anschließend die Ergebnisse direkt im K.-o.-Plan erfassen.")
                self._game_refreshing = False
                return
            if phase.final_is_finished:
                winner_name = self._person_name(phase.final_winner_id)
                self.group_status.setText("Turnier beendet")
                self.final_round_banner.setText(f"Turniersieger: {winner_name} · Die Finalrunde ist vollständig abgeschlossen.")
            else:
                current_round = max(match.round_number for match in phase.matches)
                open_count = sum(match.result is None for match in phase.matches if match.round_number == current_round)
                round_names = {1: "Viertelfinale", 2: "Halbfinale", 3: "Finale"}
                round_name = round_names.get(current_round, f"Runde {current_round}")
                self.group_status.setText(f"{round_name} · {open_count} offen")
                self.final_round_banner.setText(f"{round_name} läuft · {open_count} Begegnung(en) warten noch auf ein Ergebnis.")
            validator = QIntValidator(0, 999, self)
            for row, match in enumerate(phase.matches):
                self.group_matches.insertRow(row)
                self.group_matches.setItem(row, 0, QTableWidgetItem(match.scheduled_time or "—"))
                self.group_matches.setItem(row, 1, QTableWidgetItem(str(match.table_number or "—")))
                self.group_matches.setItem(row, 2, QTableWidgetItem("Platz 3" if match.match_kind == "third_place" else ("Finale" if match.match_kind == "final" else str(match.round_number))))
                self.group_matches.setItem(row, 3, QTableWidgetItem(self._person_name(match.home_id)))
                self.group_matches.setItem(row, 4, QTableWidgetItem(self._person_name(match.away_id)))
                locked = match.round_number < max(item.round_number for item in phase.matches) or phase.final_is_finished
                self._set_quick_result_row(row, phase, None, match, locked=locked)
            self.group_matches.resizeColumnsToContents()
            self.prepare_qualification_button.setEnabled(False)
            self.save_qualification_button.setEnabled(False)
            self.game_tabs.setTabEnabled(self.qualification_tab_index, False)
            self._game_refreshing = False
            return
        self.open_qualification_button.setVisible(True)
        self.open_qualification_button.setEnabled(True)
        self.final_round_banner.setVisible(False)
        self.game_tabs.setTabText(0, "Einteilung")
        self.game_tabs.setTabText(1, "Spielplan")
        self.game_tabs.setTabText(2, "Tabelle")
        self.game_title.setText("Gruppenspielbetrieb")
        # Qualification belongs to the first/source group stage, even when the
        # operator is currently viewing the intermediate round.  Repair the
        # five-group qualification rules BEFORE rendering the table.  STARTFIX
        # 112 repaired the source phase, but the old order rendered the stale
        # pre-repair object first; the later repair therefore became visible
        # only after a second manual refresh.  This is why only one playoff
        # could be shown although B, D and E were applicable.
        qualification_phase = self.qualification_source_phase()
        try:
            self.service.ensure_freudenholm_flow()
            self.tournament = self.service.require_tournament()
            qualification_phase = self.qualification_source_phase()
        except ValidationError:
            # Variable/custom tournaments may not use the Freudenholm repair;
            # their persisted qualification flags are still rendered normally.
            pass
        self.refresh_qualification_table(qualification_phase or phase)
        self.group_standings.setVisible(True)
        self.generate_schedule_button.setText("Turnierplan aus Gruppen erstellen")
        enabled = group is not None
        playable_groups = [item for item in phase.groups if len(item.participant_ids) >= 2]
        # Der Gesamtplan hängt nicht von der aktuell ausgewählten Gruppe ab.
        # Zuvor blieb der Button deaktiviert, sobald eine leere Gruppe ausgewählt
        # war oder die aktuelle Gruppe bereits einen Einzelplan besaß. Dadurch
        # konnte aus korrekt eingeteilten anderen Gruppen kein Turnierplan
        # erstellt werden.
        self.generate_schedule_button.setEnabled(bool(playable_groups))
        self.generate_all_schedules_button.setEnabled(bool(group is not None and len(group.participant_ids) >= 2))
        self.reset_schedule_button.setEnabled(bool(group is not None and group.matches))
        self.schedule_quality_button.setEnabled(any(item.matches for item in phase.groups))
        later_targets = [p for p in self.tournament.phases if p.position > phase.position]
        self.qualification_rule_button.setEnabled(bool(enabled and group is not None))
        self.qualify_button.setEnabled(bool(enabled and group is not None and group.is_finished and later_targets and group.qualification_count > 0))
        later_group_phases = [p for p in later_targets if p.phase_type is PhaseType.GROUP_STAGE and p.groups and not any(g.matches for g in p.groups)]
        source_ready = bool(phase.groups) and all(g.qualification_ready for g in phase.groups if g.qualification_count > 0 or g.qualification_playoff_applicable)
        self.qualify_all_button.setEnabled(bool(source_ready and later_group_phases))
        try:
            first_group_phase = next(
                (item for item in sorted(self.tournament.phases, key=lambda value: value.position)
                 if item.phase_type is PhaseType.GROUP_STAGE),
                None,
            )
            target_phase = (
                self.tournament.phase(first_group_phase.next_phase_id)
                if first_group_phase is not None and first_group_phase.next_phase_id is not None
                else None
            )
            assignment_ready = bool(
                first_group_phase is not None
                and target_phase is not None
                and target_phase.phase_type is PhaseType.GROUP_STAGE
                and bool(target_phase.groups)
                and all(group.qualification_ready for group in first_group_phase.groups
                        if group.qualification_count > 0 or group.qualification_playoff_applicable)
                and not any(group.matches for group in target_phase.groups)
            )
        except ValidationError:
            assignment_ready = False
        self.intermediate_groups_button.setEnabled(assignment_ready)
        if not enabled:
            self.group_status.setText("Keine Gruppe ausgewählt.")
            self._game_refreshing = False
            return
        if not group.matches:
            self.group_status.setText(f"{len(group.participant_ids)} Spieler · Top {group.qualification_count} direkt" + (" + Sieger aus 3. gegen 4." if group.qualification_playoff_applicable else "") + " · noch kein Spielplan")
            self._game_refreshing = False
            return
        total = len(group.matches); played = group.played_matches
        state = "Fertig" if group.is_finished else ("Nicht begonnen" if played == 0 else f"{total - played} offen")
        self.group_status.setText(f"{played}/{total} Gruppenspiele · {state} · Top {group.qualification_count} direkt" + (" + Sieger aus 3. gegen 4." if group.qualification_playoff_applicable else ""))
        validator = QIntValidator(0, 999, self)
        for row, match in enumerate(group.matches):
            self.group_matches.insertRow(row)
            self.group_matches.setItem(row, 0, QTableWidgetItem(match.scheduled_time or "—"))
            self.group_matches.setItem(row, 1, QTableWidgetItem(str(match.table_number or "—")))
            self.group_matches.setItem(row, 2, QTableWidgetItem("Platz 3" if match.match_kind == "third_place" else ("Finale" if match.match_kind == "final" else str(match.round_number))))
            self.group_matches.setItem(row, 3, QTableWidgetItem(self._person_name(match.home_id)))
            self.group_matches.setItem(row, 4, QTableWidgetItem(self._person_name(match.away_id)))
            self._set_quick_result_row(row, phase, group, match)
        if group.qualification_playoff_applicable and group.is_finished:
            try:
                playoff = self.service.ensure_group_playoff(phase.id, group.id)
            except ValidationError:
                playoff = None
            if playoff is not None:
                row = self.group_matches.rowCount(); self.group_matches.insertRow(row)
                self.group_matches.setItem(row, 0, QTableWidgetItem(playoff.scheduled_time or "nach Gruppenphase"))
                self.group_matches.setItem(row, 1, QTableWidgetItem(str(playoff.table_number or "—")))
                self.group_matches.setItem(row, 2, QTableWidgetItem("Q"))
                self.group_matches.setItem(row, 3, QTableWidgetItem(self._person_name(playoff.home_id) + " (3.)"))
                self.group_matches.setItem(row, 4, QTableWidgetItem(self._person_name(playoff.away_id) + " (4.)"))
                self._set_quick_result_row(row, phase, group, playoff)
        self.group_matches.resizeColumnsToContents()
        for position, standing in enumerate(group.standings(self.tournament.people), start=1):
            row = self.group_standings.rowCount(); self.group_standings.insertRow(row)
            values = [position, self._person_name(standing.person_id), standing.played, standing.wins, standing.losses, f"{standing.scored}:{standing.conceded}", standing.difference, f"{standing.balls_scored}:{standing.balls_conceded}", standing.ball_difference, standing.direct_points, standing.points]
            for column, value in enumerate(values):
                self.group_standings.setItem(row, column, QTableWidgetItem(str(value)))
        self.group_standings.resizeColumnsToContents()
        self._game_refreshing = False

    def show_qualification_games_dialog(self) -> None:
        """Show qualification games with the same entry layout as group matches."""
        phase = self.qualification_source_phase()
        if phase is None:
            show_error(self, ValidationError("Es ist keine Gruppenphase mit Qualifikationsspielen vorhanden."))
            return
        try:
            if len(phase.groups) == 5:
                self.service.ensure_freudenholm_flow()
                self.tournament = self.service.require_tournament()
                phase = self.tournament.phase(phase.id)
            unfinished = [g.name for g in phase.groups if g.qualification_playoff_applicable and not g.is_finished]
            if not unfinished:
                self.service.prepare_qualification_playoffs(phase.id)
                self.tournament = self.service.require_tournament()
                phase = self.tournament.phase(phase.id)
        except ValidationError as error:
            show_error(self, error)
            return

        groups = [g for g in phase.groups if g.qualification_playoff_applicable]
        dialog = QDialog(self)
        dialog.setWindowTitle("Qualifikationsspiele")
        dialog.setMinimumSize(980, 440)
        title = QLabel("Qualifikationsspiele – Ergebniseingabe")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        info = QLabel("Die Eingabe funktioniert genauso wie in der Gruppenphase. Trage links und rechts das Satzergebnis ein.")
        info.setWordWrap(True)
        table = QTableWidget(len(groups), 7)
        table.setHorizontalHeaderLabels(["Zeit", "Feld", "Runde", "Spieler 1", "Spieler 2", "Ergebnis 1", "Ergebnis 2"])
        table.setMinimumHeight(270)
        table.verticalHeader().setDefaultSectionSize(48)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        validator = QIntValidator(0, 9, dialog)
        editors = {}
        for row, group in enumerate(groups):
            playoff = group.playoff_match
            if not group.is_finished:
                values = ["–", "–", f"Q {group.name}", "Gruppenphase noch offen", "–"]
                for column, value in enumerate(values):
                    table.setItem(row, column, QTableWidgetItem(value))
                continue
            if playoff is None:
                values = ["–", "–", f"Q {group.name}", "Noch nicht erzeugt", "–"]
                for column, value in enumerate(values):
                    table.setItem(row, column, QTableWidgetItem(value))
                continue
            values = [
                playoff.scheduled_time or "nach Gruppenphase",
                str(playoff.table_number or "–"),
                f"Q {group.name}",
                self._person_name(playoff.home_id) + " (3.)",
                self._person_name(playoff.away_id) + " (4.)",
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
            home = QLineEdit(); away = QLineEdit()
            home.setValidator(validator); away.setValidator(validator)
            home.setAlignment(Qt.AlignmentFlag.AlignCenter); away.setAlignment(Qt.AlignmentFlag.AlignCenter)
            home.setPlaceholderText("0"); away.setPlaceholderText("0")
            if playoff.result is not None:
                home.setText(str(playoff.result.home_score)); away.setText(str(playoff.result.away_score))
            table.setCellWidget(row, 5, home); table.setCellWidget(row, 6, away)
            editors[group.id] = (home, away)

        save_button = QPushButton("Ergebnisse speichern")
        save_button.setMinimumHeight(38)
        continue_button = QPushButton("Speichern und Zwischenrunde einteilen")
        continue_button.setMinimumHeight(38)
        continue_button.setObjectName("primaryButton")
        close_button = QPushButton("Schließen")

        def save_results(and_continue: bool = False) -> None:
            try:
                for group_id, (home, away) in editors.items():
                    home_text, away_text = home.text().strip(), away.text().strip()
                    if not home_text and not away_text:
                        continue
                    if not home_text or not away_text:
                        raise ValidationError("Bitte bei einem begonnenen Ergebnis beide Werte eintragen.")
                    self.service.record_group_playoff_result(phase.id, group_id, int(home_text), int(away_text))
                self.on_saved()
                self.tournament = self.service.require_tournament()
                self.refresh_gameplay()
                dialog.accept()
                if and_continue:
                    self.continue_tournament_day()
            except ValidationError as error:
                show_error(dialog, error)

        save_button.clicked.connect(lambda _checked=False: save_results(False))
        continue_button.clicked.connect(lambda _checked=False: save_results(True))
        close_button.clicked.connect(dialog.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(save_button)
        buttons.addWidget(continue_button)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout = QVBoxLayout(dialog)
        layout.addWidget(title)
        layout.addWidget(info)
        layout.addWidget(table, 1)
        layout.addLayout(buttons)
        dialog.exec()

    def edit_current_intermediate_groups(self) -> None:
        """Open a clear editor entry point for the current intermediate round."""
        phase = self.current_phase()
        if (
            phase is None
            or phase.phase_type is not PhaseType.GROUP_STAGE
            or phase.position <= 1
            or not phase.groups
        ):
            show_error(self, ValidationError("Bitte zuerst die Zwischenrunde auswählen."))
            return
        self.show_intermediate_groups_dialog(phase.id)

    def start_current_intermediate_round(self) -> None:
        """Create the intermediate schedule when needed and show its matches."""
        phase = self.current_phase()
        if (
            phase is None
            or phase.phase_type is not PhaseType.GROUP_STAGE
            or phase.position <= 1
            or not phase.groups
        ):
            show_error(self, ValidationError("Bitte zuerst die Zwischenrunde auswählen."))
            return

        source = self.qualification_source_phase()
        capacities = self.service.intermediate_group_capacities(source.id) if source is not None else tuple(
            group.target_capacity or len(group.participant_ids) for group in phase.groups
        )
        sizes = [len(group.participant_ids) for group in phase.groups]
        if tuple(sizes) != tuple(capacities):
            show_error(
                self,
                ValidationError(
                    "Vor dem Start müssen die Zwischenrundengruppen vollständig belegt sein. "
                    f"Erwartet: {', '.join(str(size) for size in capacities)} · aktuell: {', '.join(str(size) for size in sizes)}."
                ),
            )
            self.game_tabs.setCurrentIndex(0)
            return

        try:
            existing = sum(len(group.matches) for group in phase.groups)
            if existing == 0:
                planned = self.service.generate_phase_schedule(
                    phase.id,
                    tables=self.schedule_tables.value(),
                    start_time=self.schedule_start.time().toString("HH:mm"),
                    duration_minutes=self.schedule_duration.value(),
                    replace=False,
                    strategy=self.schedule_strategy.currentData() or "fair",
                )
                expected = sum(size * (size - 1) // 2 for size in capacities)
                if len(planned) != expected:
                    raise ValidationError(
                        f"Der Zwischenrunden-Spielplan ist unvollständig: {len(planned)} von {expected} Spielen."
                    )
                self.on_saved()
                self.tournament = self.service.require_tournament()
                self.refresh()
                index = self.phase_selector.findData(phase.id)
                if index >= 0:
                    self.phase_selector.setCurrentIndex(index)
                QMessageBox.information(
                    self,
                    "Zwischenrunde gestartet",
                    f"{len(planned)} Spiele in {len(phase.groups)} Zwischenrundengruppen wurden erstellt. "
                    "Der Reiter Spielplan wird jetzt geöffnet.",
                )
            self.game_tabs.setCurrentIndex(1)
            self.on_continue(phase.id)
        except ValidationError as error:
            show_error(self, error)
        except Exception as error:
            QMessageBox.critical(self, "Zwischenrunde konnte nicht gestartet werden", str(error))

    def open_intermediate_assignment(self) -> None:
        """Open the free intermediate-round assignment as an explicit workflow step."""
        try:
            self.tournament = self.service.require_tournament()
            phases = sorted(self.tournament.phases, key=lambda value: value.position)
            source = next((item for item in phases if item.phase_type is PhaseType.GROUP_STAGE), None)
            if source is None or source.next_phase_id is None:
                raise ValidationError("Die Zwischenrunde ist im Turnierablauf noch nicht eingerichtet.")
            target = self.tournament.phase(source.next_phase_id)
            if target.phase_type is not PhaseType.GROUP_STAGE or not target.groups:
                raise ValidationError("Die nächste Phase ist keine konfigurierte Zwischenrunde.")
            unfinished = [group.name for group in source.groups if not group.is_finished]
            if unfinished:
                raise ValidationError("Zuerst müssen alle Gruppenspiele abgeschlossen werden: " + ", ".join(unfinished))
            pending = [
                group.name for group in source.groups
                if group.qualification_playoff_applicable
                and (group.playoff_match is None or group.playoff_match.result is None)
            ]
            if pending:
                raise ValidationError(
                    "Zuerst müssen die Qualifikationsspiele abgeschlossen werden: " + ", ".join(pending)
                )
            if any(group.matches for group in target.groups):
                self.show_intermediate_groups_dialog(target.id)
                return
            preview = self.service.phase_advance_preview(source.id)
            capacities = self.service.intermediate_group_capacities(source.id)
            expected_total = sum(capacities)
            if len(preview.qualified_ids) != expected_total:
                raise ValidationError(
                    f"Für die Zwischenrunde werden genau {expected_total} qualifizierte Spieler benötigt; aktuell sind es {len(preview.qualified_ids)}."
                )
            if not self.assign_intermediate_groups_dialog(target.id, preview.qualified_ids):
                return
            self.on_saved()
            self.refresh()
            index = self.phase_selector.findData(target.id)
            if index >= 0:
                self.phase_selector.setCurrentIndex(index)
            self.show_intermediate_groups_dialog(target.id)
        except ValidationError as error:
            show_error(self, error)
            self.refresh_gameplay()

    def assign_intermediate_groups_dialog(self, target_phase_id: UUID, qualified_ids: tuple[UUID, ...]) -> bool:
        """Assign qualified players to the clearly separated groups F-I."""
        self.tournament = self.service.require_tournament()
        phase = self.tournament.phase(target_phase_id)
        if not phase.groups:
            raise ValidationError("Die Zwischenrunde enthält noch keine Zielgruppen.")
        if any(group.matches for group in phase.groups):
            raise ValidationError("Die Zwischenrunde besitzt bereits Spielpläne und muss zuerst zurückgesetzt werden.")
        source = self.qualification_source_phase()
        capacities = self.service.intermediate_group_capacities(source.id)
        expected_total = sum(capacities)
        gender_split = self.service.uses_gender_split_13_flow(source.id)
        women_ids = set(self.service.qualified_women_ids(qualified_ids, source.id))
        women_group_index = next((i for i, size in enumerate(capacities) if len(women_ids) >= 3 and size == len(women_ids)), None)
        amateur_ids = set(self.service.qualified_amateur_ids(qualified_ids))
        amateur_group_index = next((
            i for i, size in enumerate(capacities)
            if i != women_group_index and len(amateur_ids) >= 3 and size == len(amateur_ids)
        ), None)
        if len(qualified_ids) != expected_total:
            raise ValidationError(f"Für die Zwischenrunde werden genau {expected_total} Spieler benötigt; aktuell sind es {len(qualified_ids)}.")

        for index, group in enumerate(phase.groups):
            if gender_split and len(phase.groups) == 4 and index == 0:
                group.name = "Frauengruppe F"
            elif amateur_group_index is not None and index == amateur_group_index:
                group.name = f"Amateurgruppe {chr(70 + index)}"
            elif gender_split and len(phase.groups) == 4:
                group.name = f"Männergruppe {chr(70 + index)}"
            elif women_group_index is not None and index == women_group_index:
                group.name = f"Frauengruppe {chr(70 + index)}"
            elif not group.name.strip() or group.name.startswith(("Frauengruppe ", "Männergruppe ", "Amateurgruppe ")):
                group.name = f"Gruppe {chr(70 + index)}"

        dialog = QDialog(self)
        dialog.setWindowTitle("Zwischenrunde einteilen")
        dialog.setObjectName("premiumIntermediateDialog")
        dialog.setMinimumSize(980, 650)
        title = QLabel("Zwischenrunde einteilen")
        title.setObjectName("premiumDialogTitle")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        info = QLabel(
            "Die Qualifizierten werden automatisch fair auf die Zwischenrundengruppen verteilt. "
            "Du kannst die Einteilung vor dem Speichern weiterhin manuell ändern. "
            + (("Die qualifizierten Frauen werden gemeinsam in eine reine Frauengruppe eingeteilt. " if women_group_index is not None else "")
               + (f"{len(amateur_ids)} qualifizierte Amateure werden gemeinsam in eine reine Amateurgruppe eingeteilt. " if amateur_group_index is not None else "")
               + "Zielgrößen: " + " / ".join(str(size) for size in capacities) + ".")
        )
        info.setWordWrap(True)
        info.setObjectName("premiumDialogInfo")

        unassigned = QListWidget()
        unassigned.setObjectName("premiumUnassignedList")
        unassigned.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        unassigned.setMinimumWidth(260)

        group_lists: dict[UUID, QListWidget] = {}
        group_counts: dict[UUID, QLabel] = {}
        assigned: dict[UUID, UUID] = {}
        qualified_set = set(qualified_ids)
        for group in phase.groups:
            for person_id in group.participant_ids:
                if person_id in qualified_set:
                    assigned[person_id] = group.id

        def automatic_assignment() -> dict[UUID, UUID]:
            """Return the engine's fair automatic distribution for this intermediate round.

            The engine already orders qualifiers by their placing across source groups
            and distributes them according to the configured snake/balanced strategy
            while respecting capacities and category/gender profiles. Keeping that
            logic in one place prevents the dialog from inventing a second set of
            qualification rules.
            """
            preview = self.service.phase_advance_preview(source.id)
            if preview.target_phase_id != phase.id:
                raise ValidationError("Die automatische Verteilung verweist auf eine andere Folgephase.")
            mapping: dict[UUID, UUID] = {}
            for group_id, person_ids in preview.assignments.items():
                for person_id in person_ids:
                    if person_id in qualified_set:
                        mapping[person_id] = group_id
            if set(mapping) != qualified_set:
                raise ValidationError(
                    f"Die automatische Verteilung ist unvollständig: {len(mapping)} von {len(qualified_set)} Spielern wurden zugeordnet."
                )
            return mapping

        # Startfix 115: a fresh intermediate round opens already completely filled.
        # Manual editing remains possible, but the normal workflow needs no dragging
        # or clicking through every qualifier anymore. Existing assignments are kept.
        if not assigned:
            assigned.update(automatic_assignment())

        def add_person_item(widget: QListWidget, person_id: UUID) -> None:
            item = QListWidgetItem(self._person_name(person_id))
            item.setData(Qt.ItemDataRole.UserRole, person_id)
            widget.addItem(item)

        def rebuild_lists() -> None:
            unassigned.clear()
            for widget in group_lists.values():
                widget.clear()
            for person_id in qualified_ids:
                group_id = assigned.get(person_id)
                if group_id is None:
                    add_person_item(unassigned, person_id)
                elif group_id in group_lists:
                    add_person_item(group_lists[group_id], person_id)
            for group in phase.groups:
                count = group_lists[group.id].count()
                group_counts[group.id].setText(f"{count}/{capacities[list(group_lists).index(group.id)]}")
            remaining.setText(f"Noch nicht zugeordnet: {unassigned.count()} von {expected_total}")
            valid = unassigned.count() == 0 and all(group_lists[group.id].count() == capacities[index] for index, group in enumerate(phase.groups))
            save_button.setEnabled(valid)

        def selected_unassigned_id() -> UUID | None:
            item = unassigned.currentItem()
            return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

        def add_to_group(group_id: UUID) -> None:
            person_id = selected_unassigned_id()
            if person_id is None:
                show_error(dialog, ValidationError("Bitte zuerst links einen Spieler auswählen."))
                return
            target_index = list(group_lists).index(group_id)
            if group_lists[group_id].count() >= capacities[target_index]:
                show_error(dialog, ValidationError("Diese Gruppe ist bereits vollständig belegt."))
                return
            is_woman = person_id in women_ids
            is_amateur = person_id in amateur_ids
            if women_group_index is not None:
                if target_index == women_group_index and not is_woman:
                    show_error(dialog, ValidationError("In die Frauengruppe können nur Spielerinnen eingeteilt werden."))
                    return
                if target_index != women_group_index and is_woman:
                    show_error(dialog, ValidationError("Qualifizierte Spielerinnen werden gemeinsam in die Frauengruppe eingeteilt."))
                    return
            if amateur_group_index is not None:
                if target_index == amateur_group_index and not is_amateur:
                    show_error(dialog, ValidationError("In die Amateurgruppe können nur Spieler der Kategorie Amateur eingeteilt werden."))
                    return
                if target_index != amateur_group_index and is_amateur:
                    show_error(dialog, ValidationError("Qualifizierte Amateure werden gemeinsam in die Amateurgruppe eingeteilt."))
                    return
            assigned[person_id] = group_id
            rebuild_lists()

        def remove_selected(group_id: UUID) -> None:
            item = group_lists[group_id].currentItem()
            if item is None:
                show_error(dialog, ValidationError("Bitte zuerst einen Spieler in der Gruppe auswählen."))
                return
            assigned.pop(item.data(Qt.ItemDataRole.UserRole), None)
            rebuild_lists()

        def clear_group(group_id: UUID) -> None:
            for person_id in [pid for pid, gid in assigned.items() if gid == group_id]:
                assigned.pop(person_id, None)
            rebuild_lists()

        def clear_all() -> None:
            assigned.clear()
            rebuild_lists()

        left_card = QFrame()
        left_card.setObjectName("premiumIntermediateSidebar")
        left_layout = QVBoxLayout(left_card)
        left_heading = QLabel("Qualifizierte Spieler")
        left_heading.setObjectName("premiumIntermediateHeading")
        left_heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        remaining = QLabel()
        remaining.setWordWrap(True)
        left_layout.addWidget(left_heading)
        left_layout.addWidget(remaining)
        left_layout.addWidget(unassigned, 1)
        auto_button = QPushButton("Automatisch fair verteilen")
        auto_button.setObjectName("primaryButton")
        auto_button.setMinimumHeight(40)

        def auto_fill() -> None:
            assigned.clear()
            assigned.update(automatic_assignment())
            rebuild_lists()

        auto_button.clicked.connect(auto_fill)
        left_layout.addWidget(auto_button)
        clear_all_button = QPushButton("Alle Gruppen mit einem Klick leeren")
        clear_all_button.setMinimumHeight(38)
        clear_all_button.clicked.connect(clear_all)
        left_layout.addWidget(clear_all_button)

        groups_grid = QGridLayout()
        for index, group in enumerate(phase.groups):
            card = QFrame()
            card.setObjectName("premiumIntermediateGroupCard")
            card.setProperty("groupIndex", index)
            card_layout = QVBoxLayout(card)
            header = QHBoxLayout()
            name = QLabel(group.name)
            name.setObjectName("premiumIntermediateGroupTitle")
            name.setStyleSheet("font-size: 17px; font-weight: 700;")
            count = QLabel(f"0/{capacities[index]}")
            count.setObjectName("premiumIntermediateCount")
            count.setStyleSheet("font-weight: 700;")
            group_counts[group.id] = count
            header.addWidget(name)
            header.addStretch()
            header.addWidget(count)
            widget = QListWidget()
            widget.setObjectName("premiumIntermediateGroupList")
            widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            widget.setMinimumHeight(150)
            group_lists[group.id] = widget
            add_button = QPushButton("+ Ausgewählten Spieler hinzufügen")
            add_button.setObjectName("primaryButton")
            add_button.clicked.connect(lambda _checked=False, gid=group.id: add_to_group(gid))
            remove_button = QPushButton("Ausgewählten entfernen")
            remove_button.clicked.connect(lambda _checked=False, gid=group.id: remove_selected(gid))
            empty_button = QPushButton("Gruppe komplett leeren")
            empty_button.clicked.connect(lambda _checked=False, gid=group.id: clear_group(gid))
            card_layout.addLayout(header)
            card_layout.addWidget(widget, 1)
            card_layout.addWidget(add_button)
            row = QHBoxLayout()
            row.addWidget(remove_button)
            row.addWidget(empty_button)
            card_layout.addLayout(row)
            groups_grid.addWidget(card, index // 2, index % 2)

        save_button = QPushButton("Zwischenrunde speichern und Spielpläne erstellen")
        save_button.setObjectName("primaryButton")
        save_button.setMinimumHeight(42)
        cancel_button = QPushButton("Abbrechen")

        def save_assignment() -> None:
            if unassigned.count() or any(group_lists[group.id].count() != capacities[index] for index, group in enumerate(phase.groups)):
                show_error(dialog, ValidationError("Die Zwischenrundengruppen sind noch nicht vollständig belegt."))
                return
            for group in phase.groups:
                group.participant_ids.clear()
            for person_id in qualified_ids:
                phase.assign_participant(assigned[person_id], person_id)
            self.service.save()
            # Die Zwischenrunde braucht nicht nur Paarungen, sondern auch einen
            # gemeinsamen Zeit- und Feldplan. Einzelne Gruppenpläne besitzen
            # keine Uhrzeiten/Felder und werden deshalb im Turniertag bzw.
            # Leitstand nicht als anstehende Spiele angezeigt.
            tournament = self.service.require_tournament()
            planned = self.service.generate_phase_schedule(
                phase.id, tables=tournament.table_count, start_time=tournament.schedule_start_time,
                duration_minutes=tournament.match_duration_minutes, replace=True, strategy="smart",
            )
            expected = sum(len(group.participant_ids) * (len(group.participant_ids) - 1) // 2 for group in phase.groups)
            if len(planned) != expected:
                raise ValidationError(
                    f"Der Zwischenrunden-Spielplan ist unvollständig: {len(planned)} von {expected} Spielen wurden erzeugt."
                )
            dialog.accept()

        save_button.clicked.connect(save_assignment)
        cancel_button.clicked.connect(dialog.reject)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)

        content = QHBoxLayout()
        content.addWidget(left_card, 1)
        group_widget = QWidget()
        group_widget.setLayout(groups_grid)
        content.addWidget(group_widget, 2)
        layout = QVBoxLayout(dialog)
        layout.addWidget(title)
        layout.addWidget(info)
        layout.addLayout(content, 1)
        layout.addLayout(buttons)
        rebuild_lists()
        return dialog.exec() == QDialog.DialogCode.Accepted

    def show_intermediate_groups_dialog(self, phase_id: UUID) -> None:
        """Show the newly created intermediate groups immediately and clearly."""
        self.tournament = self.service.require_tournament()
        phase = self.tournament.phase(phase_id)
        dialog = QDialog(self)
        dialog.setWindowTitle("Zwischenrunde")
        dialog.setObjectName("premiumIntermediateSummaryDialog")
        dialog.setMinimumSize(820, 560)
        title = QLabel("Zwischenrunde wurde erstellt")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        source = self.qualification_source_phase()
        gender_split = self.service.uses_gender_split_13_flow(source.id)
        info = QLabel(
            "Die 13 Qualifizierten wurden in eine Frauengruppe mit 4 Spielerinnen und drei Männergruppen mit je 3 Spielern verteilt."
            if gender_split else
            "Die 16 Qualifizierten wurden auf die Gruppen F, G, H und I verteilt."
        )
        info.setWordWrap(True)
        max_players = max((len(group.participant_ids) for group in phase.groups), default=4)
        table = QTableWidget(len(phase.groups), 1 + max_players)
        table.setHorizontalHeaderLabels(["Gruppe"] + [f"Spieler {index}" for index in range(1, max_players + 1)])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setDefaultSectionSize(58)
        table.setMinimumHeight(300)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, group in enumerate(phase.groups):
            values = [group.name] + [self._person_name(person_id) for person_id in group.participant_ids]
            values += ["–"] * ((1 + max_players) - len(values))
            for column, value in enumerate(values[:1 + max_players]):
                table.setItem(row, column, QTableWidgetItem(value))
        results_button = QPushButton("Qualifikationsergebnisse anzeigen")
        results_button.clicked.connect(lambda _checked=False: self.show_qualification_games_dialog())
        reset_button = QPushButton("Alle Zwischenrundengruppen leeren und neu einteilen")
        reset_button.setToolTip("Löscht nur die Zwischenrunde und daraus entstandene K.-o.-Runden. Gruppenphase und Qualifikation bleiben erhalten.")

        def reset_and_reassign() -> None:
            answer = QMessageBox.question(
                dialog,
                "Zwischenrunde neu einteilen",
                "Sollen die Gruppen, Spielpläne und Ergebnisse der Zwischenrunde wirklich gelöscht werden?\n\n"
                "Gruppenphase und Qualifikation bleiben erhalten. Spätere K.-o.-Runden werden zurückgesetzt.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.service.reset_intermediate_round(phase.id)
            preview = self.service.phase_advance_preview(self.qualification_source_phase().id)
            dialog.accept()
            if self.assign_intermediate_groups_dialog(phase.id, preview.qualified_ids):
                self.on_saved()
                self.refresh()
                self.show_intermediate_groups_dialog(phase.id)

        reset_button.clicked.connect(reset_and_reassign)
        close_button = QPushButton("Zwischenrunde öffnen")
        close_button.setMinimumHeight(38)
        close_button.clicked.connect(dialog.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(results_button)
        buttons.addWidget(reset_button)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout = QVBoxLayout(dialog)
        layout.addWidget(title)
        layout.addWidget(info)
        layout.addWidget(table, 1)
        layout.addLayout(buttons)
        dialog.exec()

    def _game_tab_changed(self, index: int) -> None:
        """Keep workspace tabs independent and refresh only the selected view.

        Earlier versions opened the large qualification dialog from the tab-change
        signal. During a refresh Qt can emit ``currentChanged`` while tab indices
        are being restored, so clicking the standings tab could unexpectedly open
        the qualification window. Qualification results now stay embedded in their
        own tab; the large dialog is opened only by its explicit button.
        """
        if self._game_refreshing:
            return
        if index == self.qualification_tab_index:
            phase = self.qualification_source_phase()
            if phase is not None:
                self.refresh_qualification_table(phase)

    def open_qualification_games(self) -> None:
        """Open qualification and create every eligible 3rd-vs-4th playoff."""
        phase = self.qualification_source_phase()
        if phase is None:
            show_error(self, ValidationError("Es ist keine Gruppenphase mit Qualifikationsspielen vorhanden."))
            return
        try:
            # The operational Freudenholm stage is always repaired here. This
            # also fixes old databases in which the B-E playoff flags were not
            # persisted, which previously left the tab disabled or empty.
            if len(phase.groups) == 5:
                self.service.ensure_freudenholm_flow()
                self.tournament = self.service.require_tournament()
                phase = self.tournament.phase(phase.id)
            qualification_active, _intermediate_active = self.service.effective_optional_phases()
            if not qualification_active:
                raise ValidationError("Die Qualifikation ist für dieses Turnier deaktiviert oder wegen zu weniger Spieler nicht verfügbar.")
            unfinished = [group.name for group in phase.groups if group.qualification_playoff_applicable and not group.is_finished]
            if not unfinished:
                self.service.prepare_qualification_playoffs(phase.id)
                self.tournament = self.service.require_tournament()
                phase = self.tournament.phase(phase.id)
            self.on_saved()
            self.refresh_gameplay()
            self.game_tabs.setCurrentIndex(self.qualification_tab_index)
            if unfinished:
                self.qualification_info.setText(
                    f"Die {len([g for g in phase.groups if g.qualification_playoff_applicable])} Qualifikationsspiele sind vorbereitet. Noch offene Gruppenspiele: "
                    + ", ".join(unfinished)
                )
            self.show_qualification_games_dialog()
        except ValidationError as error:
            show_error(self, error)

    def refresh_qualification_table(self, phase) -> None:
        """Show each playable qualification match exactly once.

        Three-player groups keep their configured preference but are not playable
        and therefore never get placeholder rows. Repeated refreshes rebuild the
        table from scratch so stale/duplicate rows cannot accumulate.
        """
        self.qualification_table.setRowCount(0)
        enabled_groups = [g for g in phase.groups if g.qualification_playoff_applicable]
        self.game_tabs.setTabEnabled(self.qualification_tab_index, True)
        all_groups_finished = bool(enabled_groups) and all(group.is_finished for group in enabled_groups)
        self.prepare_qualification_button.setEnabled(bool(enabled_groups and all_groups_finished))
        pending = False
        validator = QIntValidator(0, 9, self)
        for group in enabled_groups:
            playoff = group.playoff_match
            row = self.qualification_table.rowCount()
            self.qualification_table.insertRow(row)
            key = QTableWidgetItem(playoff.scheduled_time if playoff and playoff.scheduled_time else "–")
            key.setData(Qt.ItemDataRole.UserRole, group.id)
            self.qualification_table.setItem(row, 0, key)
            self.qualification_table.setItem(row, 1, QTableWidgetItem(str(playoff.table_number or "–") if playoff else "–"))
            self.qualification_table.setItem(row, 2, QTableWidgetItem(f"Q {group.name}"))
            if not group.is_finished:
                self.qualification_table.setItem(row, 3, QTableWidgetItem("Gruppenphase offen"))
                self.qualification_table.setItem(row, 4, QTableWidgetItem("–")); pending = True; continue
            if playoff is None:
                self.qualification_table.setItem(row, 3, QTableWidgetItem("Noch nicht erzeugt"))
                self.qualification_table.setItem(row, 4, QTableWidgetItem("–")); pending = True; continue
            self.qualification_table.setItem(row, 3, QTableWidgetItem(self._person_name(playoff.home_id) + " (3.)"))
            self.qualification_table.setItem(row, 4, QTableWidgetItem(self._person_name(playoff.away_id) + " (4.)"))
            home = QLineEdit(); away = QLineEdit()
            home.setValidator(validator); away.setValidator(validator)
            home.setAlignment(Qt.AlignmentFlag.AlignCenter); away.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if playoff.result is not None:
                home.setText(str(playoff.result.home_score)); away.setText(str(playoff.result.away_score))
            else:
                pending = True
            self.qualification_table.setCellWidget(row, 5, home)
            self.qualification_table.setCellWidget(row, 6, away)
        ready_to_continue = bool(enabled_groups) and all(g.playoff_match is not None and g.playoff_match.result is not None for g in enabled_groups)
        self.save_qualification_button.setEnabled(bool(enabled_groups and all_groups_finished))
        self.save_qualification_button.setText("Ergebnisse speichern und Zwischenrunde einteilen")
        if not enabled_groups:
            self.qualification_info.setText("Für diese Phase sind keine Qualifikationsspiele eingestellt.")
        elif not all_groups_finished:
            self.qualification_info.setText("Zuerst müssen alle Gruppenspiele abgeschlossen werden.")
        elif ready_to_continue:
            self.qualification_info.setText("Alle Qualifikationsspiele sind abgeschlossen. Jetzt kannst du die Zwischenrunde frei einteilen.")
        elif pending:
            self.qualification_info.setText("Trage die Ergebnisse genauso wie in der Gruppenphase ein.")

    def prepare_qualification_view(self) -> None:
        phase = self.current_phase()
        if phase is None or phase.phase_type is not PhaseType.GROUP_STAGE:
            show_error(self, ValidationError("Bitte die erste Gruppenphase auswählen."))
            return
        try:
            unfinished = [group.name for group in phase.groups if not group.is_finished]
            if unfinished:
                raise ValidationError("Zuerst müssen alle Gruppenspiele abgeschlossen werden: " + ", ".join(unfinished))
            self.service.prepare_qualification_playoffs(phase.id)
            self.on_saved(); self.refresh()
            self.game_tabs.setCurrentIndex(self.qualification_tab_index)
        except ValidationError as error:
            show_error(self, error)

    def save_qualification_results(self) -> None:
        # Always persist qualification results against their source stage.
        # The UI may already have switched to the Zwischenrunde.
        phase = self.qualification_source_phase()
        if phase is None:
            raise ValidationError("Es ist keine Gruppenphase mit Qualifikationsspielen vorhanden.")
        for row in range(self.qualification_table.rowCount()):
            item = self.qualification_table.item(row, 0)
            if item is None:
                continue
            group_id = item.data(Qt.ItemDataRole.UserRole)
            group = phase.group(group_id)
            if group.playoff_match is None:
                continue
            home = self.qualification_table.cellWidget(row, 5)
            away = self.qualification_table.cellWidget(row, 6)
            if not isinstance(home, QLineEdit) or not isinstance(away, QLineEdit):
                continue
            home_text = home.text().strip(); away_text = away.text().strip()
            if not home_text or not away_text:
                continue
            values = (int(home_text), int(away_text))
            current = None
            if group.playoff_match.result is not None:
                current = (group.playoff_match.result.home_score, group.playoff_match.result.away_score)
            if values != current:
                self.service.record_group_playoff_result(phase.id, group.id, *values)
        self.on_saved()

    def save_qualification_and_continue(self) -> None:
        try:
            # Qualification remains owned by the first group stage after the
            # phase selector has advanced to the Zwischenrunde.
            phase = self.qualification_source_phase()
            if phase is None:
                raise ValidationError("Es ist keine Gruppenphase mit Qualifikationsspielen vorhanden.")
            if any(group.qualification_playoff_applicable and group.playoff_match is None for group in phase.groups):
                self.service.prepare_qualification_playoffs(phase.id)
                self.tournament = self.service.require_tournament()
                phase = self.tournament.phase(phase.id)
            self.save_qualification_results()
            self.tournament = self.service.require_tournament()
            phase = self.tournament.phase(phase.id)
            missing = [group.name for group in phase.groups if group.qualification_playoff_applicable and (group.playoff_match is None or group.playoff_match.result is None)]
            if missing:
                raise ValidationError("Für diese Qualifikationsspiele fehlt noch ein Ergebnis: " + ", ".join(missing))
            self.continue_tournament_day()
        except ValidationError as error:
            show_error(self, error)
            self.refresh_gameplay()

    def _schedule_quality_text(self, phase_id: UUID) -> str:
        report = self.service.phase_schedule_quality(phase_id)
        rating = str(report["rating"])
        lines = [
            f"Bewertung: {rating}",
            f"Geplante Spiele: {report['scheduled_matches']} von {report['total_matches']}",
            f"Zeitslots: {report['slots']} · maximal parallel: {report['max_parallel']}",
            f"Ø Felderauslastung: {report['average_table_utilization']} %",
            f"Direkte Folgeauftritte: {report['back_to_back_appearances']}",
            f"Doppelbelegungen: {report['simultaneous_conflicts']}",
            f"Abweichung Feldernutzung: {report['table_balance_spread']} Spiel(e)",
        ]
        if report["simultaneous_conflicts"]:
            lines.append("Achtung: Der Plan enthält eine gleichzeitige Doppelbelegung.")
        elif report["back_to_back_appearances"]:
            lines.append("Hinweis: Einzelne direkte Folgepartien waren nicht vollständig vermeidbar.")
        else:
            lines.append("Sehr gut: keine direkten Folgepartien und keine Doppelbelegungen erkannt.")
        return "\n".join(lines)

    def show_schedule_quality(self) -> None:
        phase = self.current_phase()
        if phase is None or phase.phase_type is not PhaseType.GROUP_STAGE:
            show_error(self, ValidationError("Bitte eine Gruppenphase mit Spielplan auswählen."))
            return
        try:
            text = self._schedule_quality_text(phase.id)
        except ValidationError as error:
            show_error(self, error)
            return
        QMessageBox.information(self, "Spielplan-Qualitätscheck", text)

    def generate_schedule(self) -> None:
        """Create the usable tournament plan from all assigned groups.

        In a group stage this action creates pairings for every playable group
        and immediately assigns times and tables. This avoids the previous
        confusing state where only untimed pairings were created for one group.
        """
        phase = self.current_phase()
        if phase is None:
            show_error(self, ValidationError("Bitte zuerst eine Phase auswählen."))
            return
        if phase.phase_type is PhaseType.FINAL_ROUND:
            try:
                self.service.generate_final_schedule(phase.id)
                self.on_saved()
                self.refresh_players()
            except ValidationError as error:
                show_error(self, error)
            return
        self.generate_all_schedules()

    def generate_current_group_schedule(self) -> None:
        """Create round-robin pairings for the selected group only."""
        phase = self.current_phase()
        group = self.current_group()
        if phase is None or phase.phase_type is not PhaseType.GROUP_STAGE:
            show_error(self, ValidationError("Bitte eine Gruppenphase auswählen."))
            return
        if group is None:
            show_error(self, ValidationError("Bitte zuerst eine Gruppe auswählen."))
            return
        if len(group.participant_ids) < 2:
            show_error(self, ValidationError("Für einen Gruppenspielplan werden mindestens zwei Spieler benötigt."))
            return
        try:
            self.service.generate_group_schedule(phase.id, group.id)
            self.on_saved()
            self.refresh_players()
            self.game_tabs.setCurrentIndex(0)
            QMessageBox.information(
                self,
                "Gruppenplan erstellt",
                f"Für {group.name} wurden {len(group.matches)} Paarungen erzeugt. "
                "Für einen vollständigen Turnierplan mit Zeiten und Feldern bitte "
                "'Turnierplan aus Gruppen erstellen' verwenden.",
            )
        except ValidationError as error:
            show_error(self, error)

    def _save_planning_defaults(self, *_args) -> None:
        """Persist planning defaults and immediately re-time an untouched schedule."""
        tournament = self.service.require_tournament()
        tournament.table_count = self.schedule_tables.value()
        tournament.match_duration_minutes = self.schedule_duration.value()
        tournament.set_schedule_start_time(self.schedule_start.time().toString("HH:mm"))
        self.service.save()
        self.tournament = tournament

        phase = self.current_phase()
        if phase is not None and phase.phase_type is PhaseType.GROUP_STAGE:
            try:
                changed = self.service.sync_phase_schedule_to_defaults(
                    phase.id, strategy=self.schedule_strategy.currentData() or "smart"
                )
            except ValidationError:
                changed = False
            if changed:
                self.refresh_gameplay()
        elif phase is not None and phase.phase_type is PhaseType.FINAL_ROUND:
            # STARTFIX 152: K.-o.-Startzeiten sofort in die offenen Spiele übernehmen.
            try:
                changed = self.service.sync_final_schedule_to_defaults(phase.id)
            except ValidationError:
                changed = False
            if changed:
                self.refresh_gameplay()

    def generate_all_schedules(self) -> None:
        phase = self.current_phase()
        if phase is None or phase.phase_type is not PhaseType.GROUP_STAGE:
            show_error(self, ValidationError("Bitte eine Gruppenphase auswählen."))
            return
        if not phase.groups:
            show_error(self, ValidationError("Bitte zuerst Gruppen anlegen."))
            return
        playable_groups = [group for group in phase.groups if len(group.participant_ids) >= 2]
        if not playable_groups:
            show_error(self, ValidationError("Mindestens eine Gruppe mit zwei Spielern wird benötigt."))
            return
        skipped_groups = [group.name for group in phase.groups if len(group.participant_ids) < 2]
        replace = any(group.matches for group in playable_groups)
        if replace:
            answer = QMessageBox.question(
                self,
                "Spielplan neu erzeugen",
                "Mindestens eine Gruppe besitzt bereits einen Spielplan.\n\n"
                "Alle vorhandenen Gruppenspielpläne neu erzeugen? Bereits eingetragene Ergebnisse gehen verloren.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            tables = self.schedule_tables.value()
            start_time = self.schedule_start.time().toString("HH:mm")
            duration = self.schedule_duration.value()
            strategy = self.schedule_strategy.currentData() or "fair"
            planned = self.service.generate_phase_schedule(
                phase.id, tables=tables, start_time=start_time, duration_minutes=duration,
                replace=replace, strategy=strategy,
            )
            self.on_saved()
            self.refresh_players()
            self.game_tabs.setCurrentIndex(0)
            message = (
                f"{len(planned)} Spiele wurden auf {tables} Feld(er) "
                f"ab {start_time} Uhr in {duration}-Minuten-Slots verteilt.\n"
                f"Strategie: {self.schedule_strategy.currentText()}\n\n"
                + self._schedule_quality_text(phase.id)
            )
            if skipped_groups:
                message += (
                    "\n\nNicht eingeplante Gruppen (weniger als 2 Spieler): "
                    + ", ".join(skipped_groups)
                )
            QMessageBox.information(self, "Spielplan", message)
        except ValidationError as error:
            show_error(self, error)
        except Exception as error:
            QMessageBox.critical(self, "Spielplan konnte nicht erzeugt werden", str(error))

    def group_results_changed(self) -> None:
        if self._game_refreshing:
            return
        phase = self.current_phase(); group = self.current_group()
        if phase is None:
            return
        try:
            matches = phase.matches if phase.phase_type is PhaseType.FINAL_ROUND else (group.matches if group is not None else [])
            for row, match in enumerate(matches):
                home = self.group_matches.cellWidget(row, 5); away = self.group_matches.cellWidget(row, 6)
                if not isinstance(home, QLineEdit) or not isinstance(away, QLineEdit) or home.isReadOnly():
                    continue
                home_text = home.text().strip(); away_text = away.text().strip()
                if not home_text or not away_text:
                    continue
                values = (int(home_text), int(away_text))
                current = (match.result.home_score, match.result.away_score) if match.result is not None else None
                if values != current:
                    if phase.phase_type is PhaseType.FINAL_ROUND:
                        self.service.record_final_result(phase.id, match.id, *values)
                        break
                    else:
                        self.service.record_group_result(phase.id, group.id, match.id, *values)
            if phase.phase_type is PhaseType.GROUP_STAGE and group is not None and group.playoff_match is not None:
                row = len(group.matches)
                home = self.group_matches.cellWidget(row, 5); away = self.group_matches.cellWidget(row, 6)
                if isinstance(home, QLineEdit) and isinstance(away, QLineEdit):
                    home_text = home.text().strip(); away_text = away.text().strip()
                    if home_text and away_text:
                        values = (int(home_text), int(away_text))
                        current = (group.playoff_match.result.home_score, group.playoff_match.result.away_score) if group.playoff_match.result is not None else None
                        if values != current:
                            self.service.record_group_playoff_result(phase.id, group.id, *values)
            self.on_saved(); self.refresh_gameplay()
        except ValidationError as error:
            show_error(self, error)
            self.refresh_gameplay()

    def _set_quick_result_row(self, row: int, phase, group, match, locked: bool = False) -> None:
        result_text = "Offen"
        if match.result is not None:
            result_text = f"{match.result.home_score}:{match.result.away_score}"
        result_item = QTableWidgetItem(result_text)
        result_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        result_item.setData(Qt.ItemDataRole.UserRole, (phase.id, group.id if group is not None else None, match.id))
        self.group_matches.setItem(row, 5, result_item)

        action = QPushButton("Ansehen" if match.result is not None else "Ergebnis eintragen")
        action.setMinimumHeight(34)
        action.setEnabled(not locked)
        if match.result is None:
            action.setObjectName("primaryButton")
        action.clicked.connect(lambda _checked=False, r=row: self._open_result_from_group_row(r, 6))
        self.group_matches.setCellWidget(row, 6, action)

    def _open_result_from_group_row(self, row: int, _column: int = 0) -> None:
        item = self.group_matches.item(row, 5)
        if item is None:
            return
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not payload:
            return
        phase_id, group_id, match_id = payload
        try:
            phase = self.tournament.phase(phase_id)
            if group_id is None:
                match = next(value for value in phase.matches if value.id == match_id)
                group_name = "K.-o.-Runde"
            else:
                group = phase.group(group_id)
                candidates = list(group.matches)
                if group.playoff_match is not None:
                    candidates.append(group.playoff_match)
                match = next(value for value in candidates if value.id == match_id)
                group_name = group.name
            match_ref = LiveMatchRef(
                phase_id=phase.id,
                phase_name=phase.name,
                group_id=group_id,
                group_name=group_name,
                match=match,
                home_name=self._person_name(match.home_id),
                away_name=self._person_name(match.away_id),
            )
        except (ValidationError, StopIteration):
            return

        dialog = QuickResultDialog(match_ref, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        allow_correction = match.result is not None
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
                return
        try:
            self.service.record_live_set_scores(
                phase_id, group_id, match_id, dialog.values(),
                allow_correction=allow_correction, correction_reason=correction_reason,
            )
            self.tournament = self.service.require_tournament()
            self.on_saved()
            self.refresh_gameplay()
        except ValidationError as error:
            show_error(self, error)

    def configure_structure(self) -> None:
        dialog = TournamentStructureDialog(bool(self.tournament.phases), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        first_groups, first_qualifiers, use_second, second_groups, second_qualifiers, use_final, replace = dialog.values()
        if replace:
            answer = QMessageBox.question(
                self,
                "Turnierstruktur ersetzen",
                "Die bestehende, noch nicht gestartete Phasen- und Gruppeneinteilung wird gelöscht. Fortfahren?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            result = self.service.configure_tournament_structure(
                first_groups,
                first_qualifiers,
                use_second,
                second_groups,
                second_qualifiers,
                use_final,
                replace_existing=replace,
            )
            self.on_saved()
            self.refresh()
            self.phase_selector.setCurrentIndex(self.phase_selector.findData(result.first_phase_id))
            summary = [
                f"1. Gruppenphase: {first_groups} Gruppen, zunächst Top {first_qualifiers}",
            ]
            if use_second:
                summary.append(f"Zwischenrunde: {second_groups} Gruppen, zunächst Top {second_qualifiers}")
            if use_final:
                summary.append("K.-o.-Phase: aktiviert")
            QMessageBox.information(
                self,
                "Turnierstruktur erstellt",
                "\n".join(summary) + "\n\nDie Qualifikationszahl kann für jede Gruppe separat angepasst werden.",
            )
        except ValidationError as error:
            show_error(self, error)

    def create_phase(self) -> None:
        dialog = TournamentPhaseDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            phase = self.service.create_phase(*dialog.values())
            self.on_saved()
            self.refresh()
            self.phase_selector.setCurrentIndex(self.phase_selector.findData(phase.id))
        except ValidationError as error:
            show_error(self, error)


    def reset_groups(self) -> None:
        phase = self.current_phase()
        if phase is None or phase.phase_type is not PhaseType.GROUP_STAGE:
            show_error(self, ValidationError("Bitte zuerst eine Gruppenphase auswählen."))
            return
        if not phase.groups:
            show_error(self, ValidationError("In dieser Phase existieren noch keine Gruppen."))
            return
        assigned = sum(len(group.participant_ids) for group in phase.groups)
        schedules = sum(1 for group in phase.groups if group.matches)
        answer = QMessageBox.warning(
            self,
            "Gruppen zurücksetzen",
            f"Die Gruppen der Phase „{phase.name}“ wirklich zurücksetzen?\n\n"
            f"{assigned} Spielerzuordnung(en) und {schedules} Spielplan/Spielpläne werden entfernt. "
            "Die Spieler selbst bleiben erhalten. Abhängige spätere Runden werden ebenfalls geleert.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.reset_phase_groups(phase.id)
            self.on_saved()
            self.refresh_groups()
            QMessageBox.information(
                self,
                "Gruppen zurückgesetzt",
                "Alle Gruppenzuordnungen und Spielpläne dieser Phase wurden entfernt. Die Spieler sind weiterhin vorhanden.",
            )
        except ValidationError as error:
            show_error(self, error)

    def reset_schedule(self) -> None:
        phase = self.current_phase(); group = self.current_group()
        if phase is None or group is None or phase.phase_type is not PhaseType.GROUP_STAGE:
            show_error(self, ValidationError("Bitte zuerst eine Gruppe mit Spielplan auswählen."))
            return
        if not group.matches:
            show_error(self, ValidationError("Für diese Gruppe existiert noch kein Spielplan."))
            return
        answer = QMessageBox.warning(
            self,
            "Spielplan zurücksetzen",
            f"Den Spielplan von {group.name} wirklich löschen?\n\n"
            "Alle Ergebnisse, Zeiten und Feldzuordnungen dieser Gruppe gehen verloren. "
            "Danach können Spieler wieder hinzugefügt oder entfernt werden.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.clear_group_schedule(phase.id, group.id)
            self.on_saved()
            self.refresh_players()
            QMessageBox.information(
                self,
                "Spielplan zurückgesetzt",
                f"Der Spielplan von {group.name} wurde gelöscht. Die Gruppe kann jetzt frei bearbeitet werden.",
            )
        except ValidationError as error:
            show_error(self, error)

    def configure_qualification(self) -> None:
        phase = self.current_phase()
        if phase is None or phase.phase_type is not PhaseType.GROUP_STAGE or not phase.groups:
            show_error(self, ValidationError("Bitte zuerst eine Gruppenphase mit Gruppen auswählen."))
            return
        dialog = QualificationDialog(phase.groups, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            qualification_counts, playoff_flags, profiles, target_sizes = dialog.values()
            for group in phase.groups:
                self.service.set_group_profile(phase.id, group.id, profiles[group.id])
                self.service.set_group_qualification_count(
                    phase.id, group.id, qualification_counts[group.id]
                )
                self.service.set_group_qualification_playoff(
                    phase.id, group.id, playoff_flags[group.id]
                )
            if self.tournament.intermediate_enabled and target_sizes:
                self.service.configure_variable_intermediate_round(phase.id, target_sizes)
                self.tournament = self.service.require_tournament()
            self.on_saved()
            self.refresh_gameplay()
            summary = "\n".join(
                f"{group.name} [{profiles[group.id]}]: Top {qualification_counts[group.id]} direkt"
                + (" + 3. gegen 4." if playoff_flags[group.id] else "")
                for group in phase.groups
            )
            if target_sizes:
                summary += "\n\nZwischenrunde: " + " / ".join(str(size) for size in target_sizes) + " Spieler"
            QMessageBox.information(self, "Qualifikation gespeichert", summary)
        except ValidationError as error:
            show_error(self, error)

    def qualify_players(self) -> None:
        source_phase = self.current_phase(); source_group = self.current_group()
        if source_phase is None or source_group is None:
            show_error(self, ValidationError("Bitte eine abgeschlossene Quellgruppe auswählen.")); return
        targets = []
        for phase in sorted(self.tournament.phases, key=lambda item: item.position):
            if phase.position <= source_phase.position:
                continue
            if phase.phase_type is PhaseType.GROUP_STAGE:
                targets.extend((f"{phase.position}. {phase.name} / {group.name}", phase, group) for group in phase.groups if not group.matches)
            elif not phase.matches:
                targets.append((f"{phase.position}. {phase.name} / Finalrunde", phase, None))
        if not targets:
            show_error(self, ValidationError("Es gibt kein geeignetes Ziel in einer späteren Phase.")); return
        labels = [item[0] for item in targets]
        label, accepted = QInputDialog.getItem(self, "Qualifikation", "Ziel", labels, editable=False)
        if not accepted:
            return
        _, target_phase, target_group = targets[labels.index(label)]
        count = min(source_group.qualification_count, len(source_group.participant_ids))
        if count < 1:
            show_error(self, ValidationError("Für diese Gruppe ist keine direkte Qualifikation eingestellt."))
            return
        answer = QMessageBox.question(
            self,
            "Qualifikation",
            f"Die besten {count} Spieler aus {source_group.name} nach {label} übertragen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if target_group is None:
                qualified = self.service.qualify_top_players_to_final(source_phase.id, source_group.id, target_phase.id, count)
                target_name = target_phase.name
            else:
                result = self.service.qualify_top_players(source_phase.id, source_group.id, target_phase.id, target_group.id, count)
                qualified = result.qualified_ids; target_name = target_group.name
            names = ", ".join(self._person_name(person_id) for person_id in qualified)
            self.on_saved(); self.refresh()
            QMessageBox.information(self, "Qualifikation", f"Übertragen nach {target_name}:\n{names}")
        except ValidationError as error:
            show_error(self, error)

    def qualify_all_players(self) -> None:
        source_phase = self.current_phase()
        if source_phase is None or source_phase.phase_type is not PhaseType.GROUP_STAGE:
            show_error(self, ValidationError("Bitte eine abgeschlossene Gruppenphase auswählen."))
            return
        targets = [
            phase for phase in sorted(self.tournament.phases, key=lambda item: item.position)
            if phase.position > source_phase.position
            and phase.phase_type is PhaseType.GROUP_STAGE
            and phase.groups
            and not any(group.matches for group in phase.groups)
        ]
        if not targets:
            show_error(self, ValidationError("Bitte zuerst eine spätere Gruppenphase mit Zielgruppen anlegen."))
            return
        labels = [f"{phase.position}. {phase.name} ({len(phase.groups)} Gruppen)" for phase in targets]
        label, accepted = QInputDialog.getItem(self, "Sammelqualifikation", "Zielphase", labels, editable=False)
        if not accepted:
            return
        target_phase = targets[labels.index(label)]
        occupied = any(group.participant_ids for group in target_phase.groups)
        replace = False
        if occupied:
            answer = QMessageBox.question(
                self, "Zielgruppen ersetzen",
                "Die Zielgruppen enthalten bereits Spieler. Bestehende Zuordnungen ersetzen?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            replace = True
        try:
            result = self.service.qualify_all_groups(source_phase.id, target_phase.id, replace=replace)
            self.on_saved(); self.refresh()
            lines = []
            for group in target_phase.groups:
                names = ", ".join(self._person_name(person_id) for person_id in result.assignments.get(group.id, ()))
                lines.append(f"{group.name}: {names or '—'}")
            QMessageBox.information(
                self, "Sammelqualifikation",
                f"{len(result.qualified_ids)} Spieler wurden gleichmäßig übertragen.\n\n" + "\n".join(lines),
            )
        except ValidationError as error:
            show_error(self, error)

    def continue_tournament_day(self) -> None:
        """Move the tournament through its configured phases in the correct order."""
        phase = self.current_phase()
        if phase is None:
            show_error(self, ValidationError("Bitte zuerst eine Turnierphase auswählen."))
            return
        try:
            # Bei älteren Projekten fehlen teilweise die gespeicherten
            # Freudenholm-Übergänge oder Playoff-Schalter. Vor jeder
            # Fortsetzung wird der Ablauf deshalb verlustfrei repariert.
            if phase.phase_type is PhaseType.GROUP_STAGE and len(phase.groups) == 5:
                self.service.ensure_freudenholm_flow()
                self.tournament = self.service.require_tournament()
                phase = self.tournament.phase(phase.id)

            if phase.phase_type is PhaseType.FINAL_ROUND:
                if not phase.matches:
                    if len(phase.participant_ids) < 2:
                        raise ValidationError(
                            f"Für die K.-o.-Phase werden mindestens zwei Spieler benötigt; aktuell sind es {len(phase.participant_ids)}."
                        )
                    self.service.generate_final_schedule(phase.id)
                    first_label = phase.knockout_round_label(1)
                    byes = len(phase.waiting_ids)
                    bye_text = f" {byes} Freilos(e) wurden automatisch vergeben." if byes else ""
                    message = f"Die K.-o.-Phase wurde mit {len(phase.participant_ids)} Spielern gestartet ({first_label}).{bye_text}"
                elif phase.final_is_finished:
                    message = "Der Turniertag ist abgeschlossen. Das Finale wurde beendet."
                else:
                    message = "Die K.-o.-Phase läuft. Trage die Ergebnisse ein; die nächste Runde wird automatisch erzeugt."
                self.on_saved(); self.refresh()
                QMessageBox.information(self, "Turniertag", message)
                return

            # Der Turniertag hat einen eigenen Qualifikationsabschnitt. Beim
            # ersten Fortsetzen nach der Gruppenphase werden die Spiele Platz 3
            # gegen Platz 4 erzeugt und sichtbar gemacht. Erst ein weiterer
            # Klick nach deren Ergebniseingabe überträgt die Qualifizierten.
            enabled_playoffs = [
                group for group in phase.groups
                if group.qualification_playoff_applicable
            ]
            missing_playoffs = [group for group in enabled_playoffs if group.playoff_match is None]
            if missing_playoffs:
                self.service.prepare_qualification_playoffs(phase.id)
                self.on_saved(); self.refresh()
                self.game_tabs.setCurrentIndex(self.qualification_tab_index)
                first = next((g for g in phase.groups if g.qualification_playoff_applicable), None)
                if first is not None:
                    index = self.group_selector.findData(first.id)
                    if index >= 0:
                        self.group_selector.setCurrentIndex(index)
                QMessageBox.information(
                    self,
                    "Qualifikationsspiele gestartet",
                    "Die erste Gruppenphase ist abgeschlossen. Jetzt folgen die Spiele Platz 3 gegen Platz 4. "
                    "Trage für jede aktivierte Gruppe das Ergebnis ein und klicke danach erneut auf "
                    "„Turniertag fortsetzen“.",
                )
                return

            pending_playoffs = [
                group.name for group in enabled_playoffs
                if group.playoff_match is None or group.playoff_match.result is None
            ]
            unfinished_groups = [
                group.name for group in phase.groups
                if len(group.participant_ids) >= 2 and not group.is_finished
            ]
            if unfinished_groups:
                raise ValidationError(
                    "Zuerst müssen alle Gruppenspiele abgeschlossen werden: " + ", ".join(unfinished_groups)
                )
            if pending_playoffs:
                raise ValidationError(
                    "Bitte zuerst die Qualifikationsspiele Platz 3 gegen Platz 4 abschließen: "
                    + ", ".join(pending_playoffs)
                )

            if phase.next_phase_id is None:
                # Alte Projekte können nur die erste Gruppenphase enthalten.
                # Der Assistent ergänzt die fehlenden Phasen, ohne Ergebnisse
                # oder Spielerzuordnungen der laufenden Gruppenphase zu löschen.
                self.service.ensure_freudenholm_flow()
                self.tournament = self.service.require_tournament()
                phase = self.tournament.phase(phase.id)
            if phase.next_phase_id is None:
                raise ValidationError("Der Turnierablauf konnte nicht automatisch repariert werden.")
            target = self.tournament.phase(phase.next_phase_id)
            preview = self.service.phase_advance_preview(phase.id)

            if target.phase_type is PhaseType.GROUP_STAGE:
                total = len(preview.qualified_ids)
                capacities = self.service.intermediate_group_capacities(phase.id)
                expected_total = sum(capacities)
                if len(target.groups) != len(capacities) or total != expected_total:
                    raise ValidationError(
                        f"Die Zwischenrunde benötigt {len(capacities)} Gruppen mit insgesamt {expected_total} Spielern. "
                        f"Eingerichtet sind {len(target.groups)} Gruppen und {total} Qualifizierte."
                    )
                preview_sizes = [len(preview.assignments.get(group.id, ())) for group in target.groups]
                if tuple(preview_sizes) != tuple(capacities):
                    raise ValidationError(
                        "Die Qualifizierten konnten nicht passend auf die Zwischenrundengruppen verteilt werden. "
                        f"Erwartet: {', '.join(str(size) for size in capacities)}; erhalten: {', '.join(str(size) for size in preview_sizes)}."
                    )
                if not self.assign_intermediate_groups_dialog(target.id, preview.qualified_ids):
                    return
                result = preview
                message = (
                    f"Die Einteilung der {expected_total} Qualifizierten wurde gespeichert. "
                    f"Die Spielpläne der {len(target.groups)} Zwischenrundengruppen wurden erstellt."
                )
            else:
                _qualification_active, intermediate_active = self.service.effective_optional_phases()
                if not intermediate_active:
                    if len(preview.qualified_ids) < 2:
                        raise ValidationError(
                            f"Für die direkte K.-o.-Phase werden mindestens zwei Spieler benötigt; aktuell qualifiziert sind {len(preview.qualified_ids)}."
                        )
                    result = self.service.advance_phase_to_final(phase.id, limit=None, replace=False)
                    message = (
                        f"Die Zwischenrunde wurde übersprungen. {len(result.qualified_ids)} Qualifizierte wurden direkt "
                        "in die passende K.-o.-Struktur übernommen."
                    )
                else:
                    if len(preview.qualified_ids) < 2:
                        raise ValidationError(
                            f"Für die K.-o.-Phase werden mindestens zwei Spieler benötigt; aktuell qualifiziert sind {len(preview.qualified_ids)}."
                        )
                    result = self.service.advance_phase(phase.id, replace=False)
                    message = (
                        f"Die Zwischenrunde ist abgeschlossen. {len(result.qualified_ids)} Qualifizierte wurden in die "
                        "passende K.-o.-Struktur übernommen; notwendige Freilose werden automatisch vergeben."
                    )
                self.service.generate_final_schedule(target.id)

            self.on_saved(); self.refresh()
            index = self.phase_selector.findData(target.id)
            if index >= 0:
                self.phase_selector.setCurrentIndex(index)
            QMessageBox.information(self, "Turniertag fortgesetzt", message)
            if target.phase_type is PhaseType.GROUP_STAGE:
                self.show_intermediate_groups_dialog(target.id)
        except ValidationError as error:
            show_error(self, error)
            self.refresh_gameplay()

    def simulate_tournament(self) -> None:
        answer = QMessageBox.warning(
            self,
            "Testdurchlauf starten",
            "Alle noch offenen Spiele werden mit Testresultaten bis zum Finale simuliert. "
            "Bereits eingetragene Ergebnisse bleiben erhalten.\n\n"
            "Der Testdurchlauf verändert und speichert das aktuelle Turnierprojekt. Fortfahren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            report = self.service.simulate_tournament(seed=2026)
            self.tournament = self.service.require_tournament()
            winner = self._person_name(report.winner_id) if report.winner_id else "nicht ermittelt"
            self.on_saved()
            self.refresh()
            QMessageBox.information(
                self,
                "Testdurchlauf abgeschlossen",
                f"Der Turnierdurchlauf wurde vollständig abgeschlossen.\n\n"
                f"Simulierte Spiele: {report.simulated_matches}\n"
                f"Neu erzeugte Spielpläne: {report.created_schedules}\n"
                f"Phasenübergänge: {report.advanced_phases}\n"
                f"Testsieger: {winner}",
            )
        except ValidationError as error:
            show_error(self, error)
            self.refresh_gameplay()

    def _ensure_phase_editable(self, title: str) -> bool:
        phase = self.current_phase()
        if phase is None or phase.phase_type is not PhaseType.GROUP_STAGE:
            show_error(self, ValidationError("Bitte zuerst eine Gruppenphase auswählen."))
            return False
        scheduled = [group for group in phase.groups if group.matches]
        if not scheduled:
            return True
        names = ", ".join(group.name for group in scheduled)
        answer = QMessageBox.question(
            self, title,
            "Für folgende Gruppen existieren bereits Spielpläne:\n" + names +
            "\n\nDamit die Gruppeneinteilung geändert werden kann, müssen diese Spielpläne "
            "zurückgesetzt werden. Eingetragene Ergebnisse gehen verloren. Fortfahren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        try:
            for group in scheduled:
                self.service.clear_group_schedule(phase.id, group.id)
            self.on_saved()
            self.refresh_players()
            return True
        except ValidationError as error:
            show_error(self, error)
            return False

    def _confirm_replace_groups(self, title: str) -> bool:
        phase = self.current_phase()
        if phase is None or not phase.groups:
            return True
        has_players = any(group.participant_ids for group in phase.groups)
        message = (
            "Die vorhandene Gruppeneinteilung wird durch genau fünf Gruppen A bis E ersetzt."
        )
        if has_players:
            message += "\n\nBereits zugeordnete Spieler werden wieder als nicht zugeordnet angezeigt."
        message += "\n\nFortfahren?"
        return QMessageBox.question(
            self, title, message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def create_five_groups(self) -> None:
        phase = self.current_phase()
        if phase is None:
            try:
                phase = self.service.ensure_group_stage()
            except ValidationError as error:
                show_error(self, error)
                return
        if not self._ensure_phase_editable("Gruppen A bis E neu erstellen"):
            return
        if not self._confirm_replace_groups("Gruppen A bis E neu erstellen"):
            return
        try:
            groups = self.service.replace_groups(phase.id, 5)
            self.on_saved()
            self.refresh()
            self.group_selector.setCurrentIndex(0)
            QMessageBox.information(
                self, "Gruppen erstellt",
                "Die Phase enthält jetzt genau fünf Gruppen: Gruppe A bis Gruppe E.",
            )
        except ValidationError as error:
            show_error(self, error)

    def auto_distribute(self) -> None:
        phase = self.current_phase()
        if phase is None:
            show_error(self, ValidationError("Bitte zuerst eine Gruppenphase auswählen."))
            return
        if not self._ensure_phase_editable("Spieler automatisch verteilen"):
            return
        try:
            self.service.auto_distribute_players(phase.id)
            self.on_saved()
            self.refresh_players()
        except ValidationError as error:
            show_error(self, error)

    def apply_freudenholm_template(self) -> None:
        phase = self.current_phase()
        if phase is None:
            try:
                phase = self.service.ensure_group_stage()
            except ValidationError as error:
                show_error(self, error)
                return
        if not self._ensure_phase_editable("Freudenholm-Vorlage anwenden"):
            return
        if not self._confirm_replace_groups("Freudenholm-Vorlage anwenden"):
            return
        try:
            groups = self.service.replace_groups(phase.id, 5)
            self.service.auto_distribute_players(phase.id, freudenholm=True)
            self.service.set_group_qualification_count(phase.id, groups[0].id, 4)
            for group in groups[1:5]:
                self.service.set_group_qualification_count(phase.id, group.id, 2)
            self.on_saved()
            self.refresh()
            self.group_selector.setCurrentIndex(0)
            QMessageBox.information(
                self,
                "Freudenholm-Vorlage",
                "Die Gruppen A bis E wurden neu erstellt und befüllt.\n\n"
                "Gruppe A: Spielerinnen, Top 4 qualifizieren sich.\n"
                "Gruppen B bis E: übrige Spieler, Top 2 qualifizieren sich.",
            )
        except ValidationError as error:
            show_error(self, error)

    def apply_group_count(self) -> None:
        phase = self.current_phase()
        if phase is None or phase.phase_type is not PhaseType.GROUP_STAGE:
            show_error(self, ValidationError("Bitte zuerst eine Gruppenphase auswählen."))
            return
        wanted = self.group_count.value()
        current = len(phase.groups)
        if wanted == current:
            QMessageBox.information(self, "Gruppenanzahl", f"Die Gruppenphase enthält bereits {current} Gruppen.")
            return
        if not self._ensure_phase_editable("Gruppenanzahl ändern"):
            return
        if wanted < current:
            answer = QMessageBox.question(
                self,
                "Gruppenanzahl verringern",
                f"Die Gruppenanzahl wird von {current} auf {wanted} verringert. "
                "Nur leere Gruppen am Ende können entfernt werden. Fortfahren?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.group_count.setValue(current)
                return
        try:
            groups = self.service.resize_groups(phase.id, wanted)
            self.on_saved()
            self.refresh_groups()
            QMessageBox.information(
                self,
                "Gruppenanzahl gespeichert",
                f"Die Gruppenphase enthält jetzt {len(groups)} Gruppen.",
            )
        except ValidationError as error:
            self.group_count.setValue(current)
            show_error(self, error)

    def create_group(self) -> None:
        phase = self.current_phase()
        if phase is None:
            show_error(self, ValidationError("Bitte zuerst eine Gruppenphase auswählen."))
            return
        if not self._ensure_phase_editable("Neue Gruppe anlegen"):
            return
        name, accepted = QInputDialog.getText(self, "Gruppe anlegen", "Gruppenname", text=chr(65 + len(phase.groups)) if len(phase.groups) < 26 else "Neue Gruppe")
        if not accepted:
            return
        try:
            group = self.service.create_group(phase.id, name)
            self.on_saved()
            self.refresh_groups()
            self.group_selector.setCurrentIndex(self.group_selector.findData(group.id))
        except ValidationError as error:
            show_error(self, error)

    def rename_group(self) -> None:
        phase = self.current_phase()
        group = self.current_group()
        if phase is None or group is None:
            show_error(self, ValidationError("Bitte zuerst eine Gruppe auswählen."))
            return
        if not self._ensure_phase_editable("Gruppe umbenennen"):
            return
        name, accepted = QInputDialog.getText(
            self, "Gruppe umbenennen", "Neuer Gruppenname", text=group.name
        )
        if not accepted:
            return
        try:
            renamed = self.service.rename_group(phase.id, group.id, name)
            self.on_saved()
            self.refresh_groups()
            self.group_selector.setCurrentIndex(self.group_selector.findData(renamed.id))
        except ValidationError as error:
            show_error(self, error)

    def delete_group(self) -> None:
        phase = self.current_phase()
        group = self.current_group()
        if phase is None or group is None:
            show_error(self, ValidationError("Bitte zuerst eine Gruppe auswählen."))
            return
        if not self._ensure_phase_editable("Gruppe löschen"):
            return
        text = f"Soll {group.name} wirklich gelöscht werden?"
        if group.participant_ids:
            text += "\n\nDie Spieler dieser Gruppe werden danach wieder als nicht zugeordnet angezeigt."
        if QMessageBox.question(
            self, "Gruppe löschen", text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_group(phase.id, group.id)
            self.on_saved()
            self.refresh_groups()
        except ValidationError as error:
            show_error(self, error)

    def edit_final_pairings(self) -> None:
        """Let the user assign the eight KO participants to four quarter-finals."""
        phase = self.current_phase()
        if phase is None or phase.phase_type is not PhaseType.FINAL_ROUND:
            show_error(self, ValidationError("Bitte zuerst die K.-o.-Phase auswählen."))
            return
        if len(phase.participant_ids) != 8:
            show_error(self, ValidationError(
                f"Für vier Viertelfinalspiele werden genau 8 Teilnehmer benötigt; aktuell sind es {len(phase.participant_ids)}."
            ))
            return

        round_one = sorted(
            (match for match in phase.matches if match.round_number == 1),
            key=lambda match: (match.scheduled_time or "", match.table_number or 0, str(match.id)),
        )
        initial_order: list[UUID] = []
        if len(round_one) == 4:
            for match in round_one:
                initial_order.extend((match.home_id, match.away_id))
        else:
            initial_order = list(phase.participant_ids)

        dialog = QDialog(self)
        dialog.setWindowTitle("Viertelfinal-Begegnungen festlegen")
        dialog.setMinimumWidth(720)
        title = QLabel("Spieler den vier Viertelfinalspielen zuordnen")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        info = QLabel(
            "Wähle für jedes Viertelfinale zwei Spieler aus. Jeder der acht Teilnehmer darf genau einmal vorkommen. "
            "Beim Speichern wird ein vorhandener K.-o.-Spielplan einschließlich seiner Ergebnisse neu aufgebaut."
        )
        info.setWordWrap(True)

        people = [(person_id, self._person_name(person_id)) for person_id in phase.participant_ids]
        selectors: list[QComboBox] = []
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.addWidget(QLabel("Begegnung"), 0, 0)
        grid.addWidget(QLabel("Spieler 1"), 0, 1)
        grid.addWidget(QLabel("Spieler 2"), 0, 2)
        for row in range(4):
            grid.addWidget(QLabel(f"Viertelfinale {row + 1}"), row + 1, 0)
            for side in range(2):
                combo = QComboBox()
                for person_id, name in people:
                    combo.addItem(name, person_id)
                wanted = initial_order[row * 2 + side]
                index = combo.findData(wanted)
                if index >= 0:
                    combo.setCurrentIndex(index)
                selectors.append(combo)
                grid.addWidget(combo, row + 1, side + 1)

        error_label = QLabel("")
        error_label.setWordWrap(True)
        error_label.setStyleSheet("color: #a32620; font-weight: 600;")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )

        def save_pairings() -> None:
            selected = [combo.currentData() for combo in selectors]
            if len(set(selected)) != 8:
                error_label.setText("Jeder Spieler muss genau einmal ausgewählt werden.")
                return
            if phase.matches:
                answer = QMessageBox.warning(
                    dialog,
                    "Viertelfinale neu anordnen",
                    "Der bestehende K.-o.-Spielplan und alle K.-o.-Ergebnisse werden gelöscht und mit den gewählten Begegnungen neu erstellt.\n\nFortfahren?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            phase.matches.clear()
            phase.waiting_ids.clear()
            phase.participant_ids[:] = selected
            self.service.save()
            self.service.generate_final_schedule(phase.id)
            self.on_saved()
            self.tournament = self.service.require_tournament()
            self.refresh_players()
            self.game_tabs.setCurrentIndex(1)
            dialog.accept()

        buttons.accepted.connect(save_pairings)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(title)
        layout.addWidget(info)
        layout.addLayout(grid)
        layout.addWidget(error_label)
        layout.addWidget(buttons)
        dialog.exec()

    def _prepare_final_roster_change(self, phase, action: str) -> bool:
        """Allow correcting the KO roster and safely discard an existing bracket."""
        if phase.phase_type is not PhaseType.FINAL_ROUND or not phase.matches:
            return True
        answer = QMessageBox.warning(
            self,
            "K.-o.-Teilnehmer ändern",
            f"Für die K.-o.-Phase wurde bereits ein Spielplan erzeugt.\n\n"
            f"Beim {action} werden der bestehende K.-o.-Spielplan und alle darin "
            "eingetragenen Ergebnisse gelöscht. Die abgeschlossene Zwischenrunde bleibt erhalten.\n\n"
            "Fortfahren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        phase.matches.clear()
        self.service.save()
        self.on_saved()
        return True

    def _rebuild_final_schedule_if_ready(self, phase_id: UUID) -> bool:
        """Recreate the quarter-final bracket once the corrected roster has 8 players.

        During a roster correction the previous bracket is deliberately removed.
        As soon as the eighth participant is present again, the four quarter-finals
        are generated immediately so the tournament day never shows an empty KO phase.
        """
        tournament = self.service.require_tournament()
        phase = tournament.phase(phase_id)
        if phase.phase_type is not PhaseType.FINAL_ROUND or phase.matches:
            return False
        if len(phase.participant_ids) != 8:
            return False
        self.service.generate_final_schedule(phase.id)
        return True

    def assign_player(self) -> None:
        phase = self.current_phase()
        group = self.current_group()
        items = self.available.selectedItems()
        if phase is None or not items:
            show_error(self, ValidationError("Bitte zuerst eine Phase, eine Gruppe und mindestens einen Spieler auswählen."))
            return
        if phase.phase_type is PhaseType.GROUP_STAGE and group is None:
            show_error(self, ValidationError("Bitte im Feld 'Gruppe' die Zielgruppe auswählen."))
            return
        if phase.phase_type is PhaseType.FINAL_ROUND and not self._prepare_final_roster_change(phase, "Hinzufügen eines Spielers"):
            return
        if phase.phase_type is PhaseType.GROUP_STAGE and group.matches:
            if not self._ensure_phase_editable("Spieler der Gruppe zuordnen"):
                return
            group = self.current_group()
            if group is None:
                return
        try:
            for item in items:
                person_id = item.data(Qt.ItemDataRole.UserRole)
                if phase.phase_type is PhaseType.FINAL_ROUND:
                    self.service.assign_player_to_final(phase.id, person_id)
                else:
                    self.service.assign_player_to_group(phase.id, group.id, person_id)
            rebuilt = False
            if phase.phase_type is PhaseType.FINAL_ROUND:
                rebuilt = self._rebuild_final_schedule_if_ready(phase.id)
            self.on_saved()
            self.refresh_players()
            if rebuilt:
                QMessageBox.information(
                    self,
                    "Viertelfinale neu erstellt",
                    "Die K.-o.-Teilnehmer sind wieder vollständig. Die vier Viertelfinalspiele wurden automatisch neu erstellt.",
                )
        except ValidationError as error:
            show_error(self, error)

    def move_player(self) -> None:
        phase = self.current_phase(); source_group = self.current_group(); item = self.assigned.currentItem()
        if phase is None or source_group is None or item is None:
            show_error(self, ValidationError("Bitte eine Gruppenphase, eine Quellgruppe und einen Spieler auswählen.")); return
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            show_error(self, ValidationError("Spieler können nur innerhalb einer Gruppenphase verschoben werden.")); return

        target_groups = [group for group in phase.groups if group.id != source_group.id]
        if not target_groups:
            show_error(self, ValidationError("Zum Verschieben wird mindestens eine weitere Gruppe benötigt.")); return

        labels = [group.name for group in target_groups]
        label, accepted = QInputDialog.getItem(
            self,
            "Spieler verschieben",
            "Zielgruppe:",
            labels,
            editable=False,
        )
        if not accepted:
            return
        target_group = target_groups[labels.index(label)]
        person_id = item.data(Qt.ItemDataRole.UserRole)
        reset_schedules = False
        scheduled_groups = [group.name for group in (source_group, target_group) if group.matches]
        if scheduled_groups:
            answer = QMessageBox.warning(
                self,
                "Spielpläne zurücksetzen",
                "Für folgende Gruppe(n) existiert bereits ein Spielplan: "
                + ", ".join(scheduled_groups)
                + ".\n\nBeim Verschieben werden diese Spielpläne einschließlich aller Ergebnisse gelöscht. Fortfahren?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            reset_schedules = True

        try:
            self.service.move_player_between_groups(
                phase.id,
                source_group.id,
                target_group.id,
                person_id,
                reset_schedules=reset_schedules,
            )
            self.on_saved(); self.refresh_players()
            self.group_selector.setCurrentIndex(self.group_selector.findData(target_group.id))
        except ValidationError as error:
            show_error(self, error)

    def remove_player(self) -> None:
        phase = self.current_phase()
        group = self.current_group()
        items = self.assigned.selectedItems()
        if phase is None or not items:
            show_error(self, ValidationError("Bitte mindestens einen zugeordneten Spieler auswählen."))
            return
        if phase.phase_type is PhaseType.GROUP_STAGE and group is None:
            show_error(self, ValidationError("Bitte zuerst eine Gruppe auswählen."))
            return
        if phase.phase_type is PhaseType.FINAL_ROUND and not self._prepare_final_roster_change(phase, "Entfernen eines Spielers"):
            return
        if phase.phase_type is PhaseType.GROUP_STAGE and group.matches:
            if not self._ensure_phase_editable("Spieler aus der Gruppe entfernen"):
                return
            group = self.current_group()
            if group is None:
                return
        try:
            for item in items:
                person_id = item.data(Qt.ItemDataRole.UserRole)
                if phase.phase_type is PhaseType.FINAL_ROUND:
                    self.service.remove_player_from_final(phase.id, person_id)
                else:
                    self.service.remove_player_from_group(phase.id, group.id, person_id)
            rebuilt = False
            if phase.phase_type is PhaseType.FINAL_ROUND:
                rebuilt = self._rebuild_final_schedule_if_ready(phase.id)
            self.on_saved()
            self.refresh_players()
            if rebuilt:
                QMessageBox.information(
                    self,
                    "Viertelfinale neu erstellt",
                    "Die K.-o.-Teilnehmer sind wieder vollständig. Die vier Viertelfinalspiele wurden automatisch neu erstellt.",
                )
        except ValidationError as error:
            show_error(self, error)


class CompetitionPage(QWidget):
    def __init__(self, service: TournamentEngine, on_back: Callable[[], None], on_saved: Callable[[], None]) -> None:
        super().__init__()
        # Startfix 149: page-local brown/bronze skin for the Spielplan workspace.
        # Keeping this local prevents the approved Dashboard/Teilnehmer/Struktur
        # pages and all tournament logic from being affected.
        self.setObjectName("competitionPage")
        self.setStyleSheet(r"""
QWidget#competitionPage { background:transparent; color:#F4EEFF; }
QWidget#competitionPage QLabel { color:#F4EEFF; background:transparent; }
QWidget#competitionPage QLabel#workflowRouteHint {
    background:#160A2B; color:#E9D9FF; border:1px solid #8D45CC;
    border-radius:9px; padding:8px 12px; font-weight:800;
}
QWidget#competitionPage QComboBox, QWidget#competitionPage QLineEdit {
    background:#10071F; color:#FAF7FF; border:1px solid #7440B0;
    border-radius:8px; min-height:32px; padding:0 10px;
    selection-background-color:#8D2CFF; selection-color:#FFFFFF;
}
QWidget#competitionPage QComboBox QAbstractItemView {
    background:#160A2B; color:#FAF7FF; border:1px solid #7132A8;
    selection-background-color:#8D2CFF; selection-color:#FFFFFF;
}
QWidget#competitionPage QComboBox::drop-down { border:none; background:#21103B; width:24px; }
QWidget#competitionPage QPushButton {
    background:#21103B; color:#EFE5FF; border:1px solid #7440B0;
    border-radius:9px; min-height:32px; padding:0 12px; font-weight:800;
}
QWidget#competitionPage QPushButton:hover { background:#321653; border-color:#B34CFF; color:#FFFFFF; }
QWidget#competitionPage QPushButton:pressed { background:#170A31; }
QWidget#competitionPage QPushButton:disabled { background:#160A2B; color:#A98CCB; border-color:#5E2A8A; }
QWidget#competitionPage QListWidget {
    background:#090619; color:#F4EEFF; border:1px solid #7440B0;
    border-radius:10px; padding:5px; outline:none; alternate-background-color:#0F0722;
}
QWidget#competitionPage QListWidget::item { border-bottom:1px solid #3A1760; padding:8px 10px; color:#F4EEFF; }
QWidget#competitionPage QListWidget::item:hover { background:#281047; }
QWidget#competitionPage QListWidget::item:selected { background:#8D2CFF; color:#FFFFFF; border:1px solid #9D38F5; }
QWidget#competitionPage QTableWidget {
    background:#090619; color:#F4EEFF; alternate-background-color:#0F0722;
    gridline-color:#3A1760; border:1px solid #7440B0; border-radius:8px;
    selection-background-color:#8D2CFF; selection-color:#FFFFFF;
}
QWidget#competitionPage QTableWidget QWidget#qt_scrollarea_viewport,
QWidget#competitionPage QTableView QWidget#qt_scrollarea_viewport {
    background:#090619; color:#F4EEFF; border:none;
}
QWidget#competitionPage QTableWidget QLineEdit {
    background:#10071F; color:#FAF7FF; border:1px solid #7440B0; border-radius:6px;
}
QWidget#competitionPage QHeaderView::section {
    background:#201044; color:#EFE5FF; border:none; border-right:1px solid #7440B0;
    border-bottom:1px solid #7440B0; padding:7px; font-weight:900;
}
QWidget#competitionPage QTableCornerButton::section { background:#201044; border:1px solid #7440B0; }
QWidget#competitionPage QScrollBar:vertical { background:#070214; width:10px; margin:2px; }
QWidget#competitionPage QScrollBar::handle:vertical { background:#7440B0; min-height:28px; border-radius:4px; }
QWidget#competitionPage QScrollBar:horizontal { background:#070214; height:10px; margin:2px; }
QWidget#competitionPage QScrollBar::handle:horizontal { background:#7440B0; min-width:28px; border-radius:4px; }
QWidget#competitionPage QScrollBar::add-line, QWidget#competitionPage QScrollBar::sub-line { width:0px; height:0px; }
""")
        self.service = service
        self.tournament = service.require_tournament()
        self.competition: Competition | None = None
        self.on_back = on_back
        self.on_saved = on_saved
        self._refreshing = False
        self.flow_hint = QLabel("1  Teilnehmer   →   2  Struktur / Gruppen   →   3  Spielplan   →   4  Turniertag")
        self.flow_hint.setObjectName("workflowRouteHint")
        self.title = QLabel("Spielplan & Ergebnisse")
        self.title.setStyleSheet("font-size: 22px; font-weight: 900; color:#F8F3FF; background:transparent;")
        self.competition_selector = QComboBox()
        self.competition_selector.currentIndexChanged.connect(self.select_competition)
        self.new_competition_button = QPushButton("Neuer Wettbewerb")
        self.new_competition_button.clicked.connect(self.create_competition)
        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("Wettbewerb:"))
        selector_row.addWidget(self.competition_selector, 1)
        selector_row.addWidget(self.new_competition_button)
        self.status = QLabel()
        self.available = QListWidget()
        self.registered = QListWidget()
        self.register_button = QPushButton("Anmelden →")
        self.unregister_button = QPushButton("← Entfernen")
        self.start_button = QPushButton("Wettbewerb starten")
        self.finish_button = QPushButton("Wettbewerb abschließen")
        back_button = QPushButton("Zurück zu Spielern")
        self.register_button.clicked.connect(self.register_selected)
        self.unregister_button.clicked.connect(self.unregister_selected)
        self.start_button.clicked.connect(self.start_competition)
        self.finish_button.clicked.connect(self.finish_competition)
        back_button.clicked.connect(self.on_back)
        registration = QHBoxLayout()
        left = QVBoxLayout(); left.addWidget(QLabel("Verfügbare Spieler")); left.addWidget(self.available)
        middle = QVBoxLayout(); middle.addStretch(); middle.addWidget(self.register_button); middle.addWidget(self.unregister_button); middle.addStretch()
        right = QVBoxLayout(); right.addWidget(QLabel("Angemeldete Spieler")); right.addWidget(self.registered)
        registration.addLayout(left); registration.addLayout(middle); registration.addLayout(right)
        self.matches = QTableWidget(0, 5)
        self.matches.setHorizontalHeaderLabels(["Runde", "Spieler 1", "Spieler 2", "Sätze 1", "Sätze 2"])
        self.matches.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.matches.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.standings = QTableWidget(0, 10)
        self.standings.setHorizontalHeaderLabels(["Platz", "Spieler", "Sp", "S", "U", "N", "Sätze +", "Sätze −", "Diff", "Pkt"])
        self.standings.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.standings.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        controls = QHBoxLayout(); controls.addWidget(back_button); controls.addStretch(); controls.addWidget(self.start_button); controls.addWidget(self.finish_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.flow_hint)
        layout.addWidget(self.title); layout.addLayout(selector_row); layout.addWidget(self.status); layout.addLayout(registration)
        layout.addWidget(QLabel("Paarungen und Ergebnisse")); layout.addWidget(self.matches)
        layout.addWidget(QLabel("Tabelle")); layout.addWidget(self.standings); layout.addLayout(controls)

    def activate(self) -> None:
        self.refresh_selector()
        if self.competition is None:
            if self.tournament.competitions:
                self.competition = self.service.search_competitions()[0]
                self.refresh_selector()
            else:
                self.create_competition()
        self.refresh()

    def refresh_selector(self) -> None:
        current_id = self.competition.id if self.competition else None
        self.competition_selector.blockSignals(True)
        self.competition_selector.clear()
        selected_index = -1
        for index, competition in enumerate(self.service.search_competitions()):
            self.competition_selector.addItem(competition.name, competition.id)
            if competition.id == current_id:
                selected_index = index
        if selected_index >= 0:
            self.competition_selector.setCurrentIndex(selected_index)
        self.competition_selector.blockSignals(False)

    def select_competition(self, index: int) -> None:
        competition_id = self.competition_selector.itemData(index)
        if competition_id is None:
            return
        self.competition = self.service.competition(competition_id)
        self.refresh()

    def create_competition(self) -> None:
        name, accepted = QInputDialog.getText(self, "Wettbewerb anlegen", "Wettbewerbsname", text="Wettbewerb")
        if not accepted:
            return
        format_label, accepted = QInputDialog.getItem(
            self,
            "Wettbewerbssystem",
            "System",
            [CompetitionFormat.ROUND_ROBIN.label, CompetitionFormat.SINGLE_ELIMINATION.label, CompetitionFormat.SWISS.label],
            editable=False,
        )
        if not accepted:
            return
        if format_label == CompetitionFormat.SINGLE_ELIMINATION.label:
            competition_format = CompetitionFormat.SINGLE_ELIMINATION
        elif format_label == CompetitionFormat.SWISS.label:
            competition_format = CompetitionFormat.SWISS
        else:
            competition_format = CompetitionFormat.ROUND_ROBIN
        swiss_rounds = 5
        if competition_format is CompetitionFormat.SWISS:
            swiss_rounds, accepted = QInputDialog.getInt(
                self, "Rundenzahl", "Anzahl Runden", value=5, min=1, max=50
            )
            if not accepted:
                return
        try:
            self.competition = self.service.create_competition(name, competition_format, swiss_rounds)
            self.on_saved()
            self.refresh_selector()
            self.refresh()
        except ValidationError as error:
            show_error(self, error)

    def _person_name(self, person_id: UUID) -> str:
        return self.tournament.person(person_id).full_name

    def _fill_list(self, widget: QListWidget, ids: list[UUID]) -> None:
        widget.clear()
        for person_id in sorted(ids, key=lambda pid: self._person_name(pid).casefold()):
            item = QListWidgetItem(self._person_name(person_id))
            item.setData(Qt.ItemDataRole.UserRole, person_id)
            widget.addItem(item)

    def refresh(self) -> None:
        if self.competition is None:
            return
        c = self.competition
        self.refresh_selector()
        labels = {CompetitionStatus.REGISTRATION: "Anmeldung", CompetitionStatus.RUNNING: "Läuft", CompetitionStatus.FINISHED: "Beendet"}
        self.title.setText(c.name)
        self.status.setText(f"System: {c.format.label} · Status: {labels[c.status]}")
        available_ids = [person.id for person in self.tournament.people if person.id not in c.registered_ids]
        self._fill_list(self.available, available_ids)
        self._fill_list(self.registered, c.registered_ids)
        registration_open = c.status is CompetitionStatus.REGISTRATION
        self.available.setEnabled(registration_open); self.registered.setEnabled(registration_open)
        self.register_button.setEnabled(registration_open); self.unregister_button.setEnabled(registration_open)
        self.start_button.setEnabled(registration_open); self.finish_button.setEnabled(c.status is CompetitionStatus.RUNNING and c.format is CompetitionFormat.ROUND_ROBIN)
        self.refresh_matches(); self.refresh_standings()

    def register_selected(self) -> None:
        if self.competition is None:
            return
        item = self.available.currentItem()
        if item is None:
            show_error(self, ValidationError("Bitte einen Spieler auswählen.")); return
        try:
            self.service.register_player(self.competition.id, item.data(Qt.ItemDataRole.UserRole)); self.on_saved(); self.refresh()
        except ValidationError as error:
            show_error(self, error)

    def unregister_selected(self) -> None:
        if self.competition is None:
            return
        item = self.registered.currentItem()
        if item is None:
            show_error(self, ValidationError("Bitte einen Spieler auswählen.")); return
        try:
            self.service.unregister_player(self.competition.id, item.data(Qt.ItemDataRole.UserRole)); self.on_saved(); self.refresh()
        except ValidationError as error:
            show_error(self, error)

    def start_competition(self) -> None:
        if self.competition is None:
            return
        try:
            self.service.start_competition(self.competition.id); self.on_saved(); self.refresh()
        except ValidationError as error:
            show_error(self, error)

    def finish_competition(self) -> None:
        if self.competition is None:
            return
        self.save_results()
        try:
            self.service.finish_competition(self.competition.id); self.on_saved(); self.refresh()
        except ValidationError as error:
            show_error(self, error)

    def refresh_matches(self) -> None:
        self._refreshing = True
        self.matches.setRowCount(0)
        if self.competition is None:
            self._refreshing = False; return
        editable = self.competition.status is CompetitionStatus.RUNNING
        current_round = max((match.round_number for match in self.competition.matches), default=0)
        validator = QIntValidator(0, 999, self)
        for row, match in enumerate(self.competition.matches):
            self.matches.insertRow(row)
            self.matches.setItem(row, 0, QTableWidgetItem(str(match.round_number)))
            self.matches.setItem(row, 1, QTableWidgetItem(self._person_name(match.home_id)))
            self.matches.setItem(row, 2, QTableWidgetItem(self._person_name(match.away_id)))
            home = QLineEdit(); away = QLineEdit()
            home.setValidator(validator); away.setValidator(validator)
            home.setAlignment(Qt.AlignmentFlag.AlignCenter); away.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row_editable = editable and (
                self.competition.format is CompetitionFormat.ROUND_ROBIN
                or match.round_number == current_round
            )
            home.setEnabled(row_editable); away.setEnabled(row_editable)
            if match.result is not None:
                home.setText(str(match.result.home_score)); away.setText(str(match.result.away_score))
            home.editingFinished.connect(self.results_changed); away.editingFinished.connect(self.results_changed)
            self.matches.setCellWidget(row, 3, home); self.matches.setCellWidget(row, 4, away)
        self.matches.resizeColumnsToContents()
        self._refreshing = False

    def results_changed(self) -> None:
        if self._refreshing:
            return
        try:
            self.save_results()
            self.on_saved()
            self.refresh()
        except ValidationError as error:
            show_error(self, error)

    def save_results(self) -> None:
        if self.competition is None or self.competition.status is not CompetitionStatus.RUNNING:
            return
        for row, match in enumerate(self.competition.matches):
            home = self.matches.cellWidget(row, 3); away = self.matches.cellWidget(row, 4)
            if isinstance(home, QLineEdit) and isinstance(away, QLineEdit):
                home_text = home.text().strip(); away_text = away.text().strip()
                home_score = int(home_text) if home_text else None
                away_score = int(away_text) if away_text else None
                current = match.result
                current_home = current.home_score if current else None
                current_away = current.away_score if current else None
                if (home_score, away_score) != (current_home, current_away):
                    self.service.record_result(
                        self.competition.id,
                        match.id,
                        home_score,
                        away_score,
                    )

    def refresh_standings(self) -> None:
        self.standings.setRowCount(0)
        if self.competition is None:
            return
        if self.competition.format is CompetitionFormat.SINGLE_ELIMINATION:
            winner_id = self.competition.winner_id
            if winner_id is not None:
                self.standings.insertRow(0)
                self.standings.setItem(0, 0, QTableWidgetItem("1"))
                self.standings.setItem(0, 1, QTableWidgetItem(self._person_name(winner_id)))
            self.standings.resizeColumnsToContents()
            return
        for position, standing in enumerate(self.competition.standings(self.tournament.people), start=1):
            row = self.standings.rowCount(); self.standings.insertRow(row)
            values = [position, self._person_name(standing.person_id), standing.played, standing.wins, standing.draws, standing.losses, standing.scored, standing.conceded, standing.difference, standing.points]
            for column, value in enumerate(values):
                self.standings.setItem(row, column, QTableWidgetItem(str(value)))
        self.standings.resizeColumnsToContents()


class SettingsPage(QScrollArea):
    """Scrollable, responsive settings page for smaller notebook displays."""

    def __init__(
        self,
        current_key: str,
        current_primary: str,
        apply_theme: Callable[[str, str], None],
        choose_custom: Callable[[], None],
        reset_theme: Callable[[], None],
    ) -> None:
        super().__init__()
        self._apply_theme = apply_theme
        self._choose_custom = choose_custom
        self._reset_theme = reset_theme
        self._buttons: dict[str, QRadioButton] = {}
        self._single_column = False
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("settingsContent")
        self.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 12, 16, 18)
        root.setSpacing(10)

        title = QLabel("Einstellungen")
        title.setObjectName("pageTitle")
        title.setVisible(False)
        subtitle = QLabel("Passe die Darstellung an die Farben deines Vereins an.")
        subtitle.setWordWrap(True)
        subtitle.setObjectName("pageSubtitle")
        subtitle.setVisible(False)
        root.addWidget(title)
        root.addWidget(subtitle)

        section_hint = QLabel("DARSTELLUNG")
        section_hint.setObjectName("settingsSectionLabel")
        root.addWidget(section_hint)

        card = QFrame()
        card.setObjectName("settingsWorkspace")
        self.card_layout = QGridLayout(card)
        self.card_layout.setContentsMargins(16, 14, 16, 16)
        self.card_layout.setHorizontalSpacing(18)
        self.card_layout.setVerticalSpacing(8)

        heading = QLabel("Vereinsfarbe")
        heading.setObjectName("cardHeading")
        description = QLabel("Die Vereinsfarbe wird als Akzent eingesetzt. Arbeitsflächen bleiben bewusst neutral und ruhig.")
        description.setWordWrap(True)
        description.setObjectName("mutedText")
        self.card_layout.addWidget(heading, 0, 0, 1, 2)
        self.card_layout.addWidget(description, 1, 0, 1, 2)

        self.options = QWidget()
        options_layout = QVBoxLayout(self.options)
        options_layout.setContentsMargins(0, 4, 0, 0)
        options_layout.setSpacing(5)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for preset in THEME_PRESETS:
            row = QFrame()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 1, 0, 1)
            swatch = QLabel()
            swatch.setFixedSize(20, 20)
            swatch.setStyleSheet(
                f"background: {preset.primary}; border-radius: 10px; border: 1px solid rgba(0,0,0,0.18);"
            )
            radio = QRadioButton(preset.name)
            radio.setChecked(preset.key == current_key)
            radio.toggled.connect(
                lambda checked, p=preset: checked and self._apply_theme(p.key, p.primary)
            )
            group.addButton(radio)
            self._buttons[preset.key] = radio
            row_layout.addWidget(swatch)
            row_layout.addWidget(radio, 1)
            options_layout.addWidget(row)

        custom_row = QFrame()
        custom_layout = QHBoxLayout(custom_row)
        custom_layout.setContentsMargins(0, 3, 0, 0)
        self.custom_swatch = QLabel()
        self.custom_swatch.setFixedSize(20, 20)
        self.custom_button = QRadioButton("Eigene Vereinsfarbe")
        self.custom_button.setChecked(current_key == "custom")
        group.addButton(self.custom_button)
        self._buttons["custom"] = self.custom_button
        choose_button = QPushButton("Farbe auswählen…")
        choose_button.setObjectName("secondaryButton")
        choose_button.clicked.connect(self._choose_custom)
        custom_layout.addWidget(self.custom_swatch)
        custom_layout.addWidget(self.custom_button, 1)
        custom_layout.addWidget(choose_button)
        options_layout.addWidget(custom_row)

        self.preview = QFrame()
        self.preview.setObjectName("settingsPreview")
        preview_layout = QVBoxLayout(self.preview)
        preview_layout.setContentsMargins(14, 12, 14, 14)
        preview_layout.setSpacing(8)
        preview_title = QLabel("Vorschau")
        preview_title.setObjectName("cardHeading")
        self.preview_bar = QFrame()
        self.preview_bar.setFixedHeight(38)
        self.preview_button = QPushButton("Beispielbutton")
        self.preview_button.setEnabled(False)
        self.preview_info = QLabel("Ausgewählte Vereinsfarbe")
        self.preview_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(preview_title)
        preview_layout.addWidget(self.preview_bar)
        preview_layout.addWidget(self.preview_button)
        preview_layout.addWidget(self.preview_info)

        self.reset_button = QPushButton("Auf Standardfarbe zurücksetzen")
        self.reset_button.setObjectName("secondaryButton")
        self.reset_button.clicked.connect(self._reset_theme)

        self.card_layout.addWidget(self.options, 2, 0)
        self.card_layout.addWidget(self.preview, 2, 1)
        self.card_layout.addWidget(self.reset_button, 3, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
        self.card_layout.setColumnStretch(0, 1)
        self.card_layout.setColumnStretch(1, 1)

        root.addWidget(card)
        root.addStretch()
        self.setStyleSheet(self.styleSheet() + """
            QLabel#settingsSectionLabel { color:#7C6E64; font-size:10px; font-weight:800; letter-spacing:1px; padding:2px 2px 4px 2px; }
            QFrame#settingsWorkspace { background:transparent; border:none; }
            QFrame#settingsPreview { background:#FAF8F6; border:1px solid #E9E2DC; border-radius:9px; }
            QRadioButton { padding:5px 2px; spacing:8px; }
        """)
        self.set_theme_state(current_key, current_primary)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "global_watermark") and hasattr(self, "main_content"):
            self.global_watermark.setGeometry(self.main_content.rect())
            self.global_watermark.raise_()
        single_column = self.viewport().width() < 900
        if single_column == self._single_column:
            return
        self._single_column = single_column
        self.card_layout.removeWidget(self.options)
        self.card_layout.removeWidget(self.preview)
        self.card_layout.removeWidget(self.reset_button)
        if single_column:
            self.card_layout.addWidget(self.options, 2, 0, 1, 2)
            self.card_layout.addWidget(self.preview, 3, 0, 1, 2)
            self.card_layout.addWidget(self.reset_button, 4, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
        else:
            self.card_layout.addWidget(self.options, 2, 0)
            self.card_layout.addWidget(self.preview, 2, 1)
            self.card_layout.addWidget(self.reset_button, 3, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)

    def set_theme_state(self, key: str, primary: str) -> None:
        button = self._buttons.get(key)
        if button is not None and not button.isChecked():
            button.blockSignals(True)
            button.setChecked(True)
            button.blockSignals(False)
        self.custom_swatch.setStyleSheet(
            f"background: {primary}; border-radius: 10px; border: 1px solid rgba(0,0,0,0.18);"
        )
        self.preview_bar.setStyleSheet(f"background: {primary}; border-radius: 8px;")
        self.preview_button.setStyleSheet(
            f"background: {primary}; color: white; border: none; border-radius: 8px; padding: 8px; font-weight: 600;"
        )
        self.preview_info.setText(primary.upper())


class MainWindow(QMainWindow):
    def __init__(self, repository: TournamentRepository) -> None:
        super().__init__()
        self.repository = repository
        self.service = TournamentEngine(repository)
        self.tournament = self.service.tournament
        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        self._page_wrappers: dict[QWidget, QScrollArea] = {}
        self._page_history: list[QWidget] = []
        self._history_navigation = False
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.nav_layout = QVBoxLayout(self.sidebar)
        self.nav_layout.setContentsMargins(14, 16, 14, 16)
        self.nav_layout.setSpacing(6)
        self.nav_buttons: dict[str, QPushButton] = {}
        self.nav_button_texts: dict[str, str] = {}
        self.sidebar_brand_widgets: list[QWidget] = []
        self.sidebar_status_card: QFrame | None = None
        self.sidebar_status_title: QLabel | None = None
        self.sidebar_status_detail: QLabel | None = None
        self.sidebar_collapsed = False
        self._page_titles: dict[QWidget, tuple[str, str]] = {}
        self.presentation_window: PresentationWindow | None = None
        self.theme_actions: dict[str, QAction] = {}
        self.theme_key, self.theme_primary = stored_theme()
        self._build_shell()
        self.setWindowTitle("M. Seemann Badminton Tournament System")
        screen = QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            width, height = recommended_window_dimensions(geometry.width(), geometry.height())
            self.resize(width, height)
        else:
            self.resize(1180, 720)
        self.setMinimumSize(820, 560)
        self._apply_branding()
        self._create_menu()
        self.statusBar().showMessage("Bereit")
        if self.tournament is None:
            self.show_start_page()
        else:
            self.show_tournament()


    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "global_watermark") and hasattr(self, "main_content"):
            self.global_watermark.setGeometry(self.main_content.rect())
            self.global_watermark.raise_()
        compact = self.width() < 1180
        very_compact = self.width() < 980
        if self.sidebar_collapsed:
            sidebar_width = 64
        else:
            sidebar_width = 176 if very_compact else 202 if compact else 218
        self.sidebar.setFixedWidth(sidebar_width)
        margins = (6, 8, 6, 8) if compact else (12, 14, 12, 14)
        self.nav_layout.setContentsMargins(*margins)
        self.nav_layout.setSpacing(3 if compact else 6)
        self.progress_header.setProperty("compact", compact)
        self.progress_header.setMaximumHeight(68 if compact else 94)
        # Header responsiveness is based on the usable workspace width, not only
        # on the outer window width. Preserve workflow labels before subtitles.
        workspace_width = self.workspace_header.width() if hasattr(self, "workspace_header") else self.width() - sidebar_width
        header_compact = workspace_width < 1050
        header_tight = workspace_width < 900
        if hasattr(self, "page_subtitle"):
            self.page_subtitle.setVisible(bool(self.page_subtitle.text()) and not header_compact)
        if hasattr(self, "workspace_tournament"):
            self.workspace_tournament.setVisible(not header_tight)
        if hasattr(self, "workflow_status_compact"):
            self.workflow_status_compact.setVisible(not header_tight)
        if hasattr(self, "workspace_action"):
            self.workspace_action.setMaximumWidth(160 if header_compact else 190)
        for button in getattr(self, "workflow_quick_buttons", []):
            button.setMinimumWidth(98 if header_compact else 104)
        self.progress_header.style().unpolish(self.progress_header)
        self.progress_header.style().polish(self.progress_header)
        self._refresh_progress_labels()
        current = self.stack.currentWidget()
        if isinstance(current, QScrollArea) and current.widget() is not None:
            current.widget().setProperty("compact", compact)
            current.widget().style().unpolish(current.widget())
            current.widget().style().polish(current.widget())

    def _refresh_progress_labels(self) -> None:
        compact = self.width() < 1180
        labels = getattr(self, "progress_step_labels", [])
        current_index = getattr(self, "_progress_index", 0)
        for position, button in enumerate(getattr(self, "progress_steps", [])):
            if position >= len(labels):
                continue
            number, text = labels[position]
            completed = position < current_index
            current = position == current_index
            # STARTFIX 100: phase text belongs exclusively below the circle.
            # Never put a phase name into the fixed-size round button; on wide
            # windows that caused clipped fragments such as "ppen"/"alifik".
            prefix = "✓" if completed else number
            button.setText(prefix)
            button.setToolTip(f"{number}. {text} direkt öffnen")




    def _apply_zero_white_native_controls(self, root: QWidget) -> None:
        """Force native Qt scroll/view chrome to the MSTTS brown palette.

        macOS can still paint a light gutter/corner even when the application QSS
        is correct. Applying the palette and the scrollbar stylesheet directly to
        the concrete widgets prevents those native fallbacks on all platforms.
        """
        dark = QColor("#05020D")
        base = QColor("#090619")
        text = QColor("#F2EBFF")
        scroll_qss = (
            "QScrollBar:vertical{background:#05020D;width:11px;margin:0;border:none;}"
            "QScrollBar:horizontal{background:#05020D;height:11px;margin:0;border:none;}"
            "QScrollBar::handle:vertical,QScrollBar::handle:horizontal{"
            "background:#7B36AF;border:1px solid #A94CFF;border-radius:5px;min-height:30px;min-width:30px;}"
            "QScrollBar::add-line,QScrollBar::sub-line,QScrollBar::add-page,QScrollBar::sub-page{"
            "background:#05020D;border:none;width:0;height:0;}"
        )
        areas = [root] if isinstance(root, QAbstractScrollArea) else []
        areas.extend(root.findChildren(QAbstractScrollArea))
        for area in areas:
            palette = area.palette()
            palette.setColor(QPalette.ColorRole.Base, base)
            palette.setColor(QPalette.ColorRole.Window, base)
            palette.setColor(QPalette.ColorRole.Text, text)
            area.setPalette(palette)
            area.setAutoFillBackground(True)
            viewport = area.viewport()
            viewport.setAutoFillBackground(True)
            vp = viewport.palette()
            vp.setColor(QPalette.ColorRole.Base, base)
            vp.setColor(QPalette.ColorRole.Window, base)
            vp.setColor(QPalette.ColorRole.Text, text)
            viewport.setPalette(vp)
            for bar in (area.verticalScrollBar(), area.horizontalScrollBar()):
                bar.setStyleSheet(scroll_qss)
                bar.setAutoFillBackground(True)
                bp = bar.palette()
                bp.setColor(QPalette.ColorRole.Window, dark)
                bp.setColor(QPalette.ColorRole.Base, dark)
                bar.setPalette(bp)
        for bar in root.findChildren(QScrollBar):
            bar.setStyleSheet(scroll_qss)
        for table in root.findChildren(QTableWidget):
            table.setAutoFillBackground(True)
            table.setStyleSheet(table.styleSheet() + (
                "QTableWidget{background:#090619;color:#F2EBFF;border:1px solid #7337B4;"
                "gridline-color:#3A1760;alternate-background-color:#0F0722;}"
                "QTableWidget::item{background:#090619;color:#F2EBFF;}"
                "QTableWidget::item:alternate{background:#0F0722;}"
                "QHeaderView::section{background:#21103B;color:#F2EBFF;border:none;"
                "border-right:1px solid #7337B4;border-bottom:1px solid #7337B4;}"
                "QTableCornerButton::section{background:#21103B;border:none;"
                "border-right:1px solid #7337B4;border-bottom:1px solid #7337B4;}"
            ))
            tvp = table.viewport()
            tvp.setAutoFillBackground(True)
            tp = tvp.palette()
            tp.setColor(QPalette.ColorRole.Base, base)
            tp.setColor(QPalette.ColorRole.Window, base)
            tp.setColor(QPalette.ColorRole.Text, text)
            tvp.setPalette(tp)

        # Known informational labels used on the scorekeeper page used to keep
        # their native light QLabel background on macOS.  Force them transparent
        # so the brown panel below remains visible.
        for name in ("scorekeeperHintTitle", "scorekeeperHintText"):
            label = root.findChild(QLabel, name)
            if label is not None:
                label.setStyleSheet(label.styleSheet() +
                    "background:transparent;color:#D8C7FF;border:none;")

    def _register_page(
        self,
        page: QWidget,
        *,
        horizontal_scroll: bool = False,
        title: str = "",
        subtitle: str = "",
    ) -> QScrollArea:
        """Place every application page in a resizable scroll area.

        This keeps all controls reachable on 13-inch notebooks without changing
        the individual page logic. Pages still expand to the available width,
        while vertical scrolling appears only when it is actually needed.
        """
        wrapper = QScrollArea()
        wrapper.setObjectName("pageScrollArea")
        wrapper.viewport().setObjectName("pageScrollViewport")
        wrapper.setWidgetResizable(True)
        wrapper.setFrameShape(QFrame.Shape.NoFrame)
        wrapper.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if horizontal_scroll
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        wrapper.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        wrapper.setStyleSheet(
            "QScrollArea#pageScrollArea { background:#080316; border:none; } "
            "QWidget#pageScrollViewport { background:#080316; border:none; } "
            "QAbstractScrollArea::corner { background:#080316; border:none; } "
            "QScrollBar:vertical, QScrollBar:horizontal { background:#140925; } "
            "QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#7B36AF; border-radius:5px; } "
            "QScrollBar::add-line, QScrollBar::sub-line { width:0px; height:0px; }"
        )
        wrapper.setWidget(page)
        self._apply_zero_white_native_controls(wrapper)
        self._apply_zero_white_native_controls(page)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._page_wrappers[page] = wrapper
        self._page_titles[page] = (title, subtitle)
        self.stack.addWidget(wrapper)
        return wrapper

    def _show_page(self, page: QWidget) -> None:
        title, subtitle = self._page_titles.get(page, ("", ""))
        if hasattr(self, "main_content"):
            self.main_content.setProperty("liveMode", page is getattr(self, "live_center_page", None))
            self.main_content.style().unpolish(self.main_content)
            self.main_content.style().polish(self.main_content)

        # STARTFIX 140: the approved dashboard uses the same dark-brown/bronze
        # visual language as the Leitstand.  A container stylesheet is used
        # only while the dashboard is visible, avoiding recursive repolish.
        dashboard_mode = page is getattr(self, "dashboard_page", None)
        live_mode = page is getattr(self, "live_center_page", None)
        if hasattr(self, "main_content"):
            self.main_content.setStyleSheet(r"""
                QFrame#workspaceHeader, QFrame#progressHeader, QFrame#workflowQuickActions {
                    background: rgba(18,7,36,0.98); border:1px solid #5E2A8A; border-radius:14px;
                }
                QLabel#workspaceTitle { color:#F8F3FF; }
                QLabel#workspaceSubtitle, QLabel#workspaceTournament { color:#CDBBE8; }
                QPushButton#sidebarToggle, QPushButton#workspaceBackButton {
                    background:#21103B; color:#F8F3FF; border:1px solid #7337B4; border-radius:9px;
                }
                QPushButton#workflowQuickButton {
                    background:#21103B; color:#E9DEFA; border:1px solid #7337B4; border-radius:9px;
                }
                QPushButton#workflowQuickButton[state="done"] { background:#21103B; color:#DCCCF2; border-color:#7132A8; }
                QPushButton#workflowQuickButton[state="current"] { background:#8D2CFF; color:white; border:1px solid #B85CFF; }
                QLabel#workflowStatusCompact { color:#D8C7F2; background:transparent; }
                QLabel#progressTitle { color:#E45CFF; }
                QLabel#progressSubtitle { color:#F4EEFF; background:#21103B; border:1px solid #7132A8; border-radius:8px; padding:3px 9px; }
                QFrame#progressRoute { background:transparent; border:none; }
                QPushButton#progressStep { background:#140925; color:#DCCCF2; border:1px solid #5E2A8A; }
                QPushButton#progressStep[current="true"] { background:#8D2CFF; color:#FFFFFF; border:2px solid #E05BFF; }
                QPushButton#progressStep[optionalDisabled="true"] { background:#0E0719; color:#8C78A8; border:1px solid #3B2358; }
                QLabel#progressStepLabel { color:#CDBBE8; }
                QLabel#progressStepLabel[current="true"] { color:#E45CFF; font-weight:900; }
                QLabel#progressStepLabel[optionalDisabled="true"] { color:#8C78A8; }

                /* STARTFIX 141: remove the remaining light dashboard islands. */
                QWidget#mainContent { background:#080316; }
                QScrollArea#pageScrollArea,
                QScrollArea#pageScrollArea > QWidget,
                QScrollArea#pageScrollArea > QWidget > QWidget,
                QWidget#dashboardContent {
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #0B041B,stop:.52 #120724,stop:1 #070214);
                    color:#F8F3FF; border:none;
                }
                QLabel#workspaceTitle, QLabel#workspaceSubtitle, QLabel#workspaceTournament,
                QLabel#progressTitle, QLabel#progressStepLabel, QLabel#workflowStatusCompact { background:transparent; }
                QFrame#dashboardHero {
                    background:#120724; border:1px solid #7132A8; border-radius:14px;
                }
                QFrame#dashboardHero QLabel { background:transparent; }
                QLabel#dashboardHeroEyebrow { color:#E45CFF; font-weight:900; }
                QLabel#dashboardWelcome { color:#FFF5EA; }
                QLabel#dashboardSubtitle { color:#CDBBE8; }
                QLabel#dashboardHeroSlogan { color:#C05CFF; }
                QFrame#dashboardHeroClock { background:#160A2B; border:1px solid #8147C0; border-radius:11px; }
                QLabel#dashboardHeroDate { color:#D8C7F2; }
                QLabel#dashboardHeroTime { color:#F8F3FF; }
                QFrame#dashboardStatusStrip { background:#090619; border:1px solid #7138AF; border-radius:10px; }
                QFrame#dashboardStatusStrip QLabel { color:#DCCCF2; background:transparent; }
                QFrame#dashboardStatCard, QFrame#dashboardStatCard[tone="violet"],
                QFrame#dashboardStatCard[tone="bronze"], QFrame#dashboardStatCard[tone="green"],
                QFrame#dashboardStatCard[tone="rose"] { background:#160A2B; border:1px solid #7337B4; border-radius:12px; }
                QFrame#dashboardStatCard QLabel { color:#FAF7FF; background:transparent; }
                QFrame#dashboardHealthPanel, QFrame#dashboardManagementBar, QFrame#dashboardPanel {
                    background:#160A2B; border:1px solid #7337B4; border-radius:12px; color:#FFF2E6;
                }
                QFrame#dashboardHealthPanel QLabel, QFrame#dashboardManagementBar QLabel, QFrame#dashboardPanel QLabel {
                    color:#EFE5FF; background:transparent;
                }

                /* STARTFIX 142: one coherent dashboard design down to the last card. */
                QLabel#dashboardSectionHeading {
                    color:#E45CFF; background:transparent; border:none; font-weight:900;
                }
                QPushButton#dashboardQuickCard {
                    background:#160A2B; color:#F4EEFF; border:1px solid #7337B4; border-radius:12px;
                    padding:10px 16px; text-align:left; font-weight:800;
                }
                QPushButton#dashboardQuickCard:hover { background:#281047; border-color:#B34CFF; }
                QPushButton#dashboardQuickCard:pressed { background:#170A31; }
                QFrame#dashboardNextAction {
                    background:#160A2B; border:1px solid #7337B4; border-radius:13px;
                }
                QLabel#dashboardNextActionIcon {
                    color:#FFF5EA; background:#6F25B7; border:1px solid #B85CFF; border-radius:30px;
                }
                QPushButton#dashboardNextActionButton, QPushButton#dashboardManagementPrimary {
                    background:#8D2CFF; color:#FFFFFF; border:1px solid #B85CFF; border-radius:10px;
                    padding:9px 16px; font-weight:900;
                }
                QPushButton#dashboardNextActionButton:hover, QPushButton#dashboardManagementPrimary:hover { background:#A33BFF; }
                QPushButton#dashboardManagementSecondary, QToolButton#dashboardManagementMore {
                    background:#140925; color:#E9DEFA; border:1px solid #5E2A8A; border-radius:10px;
                    padding:9px 14px; font-weight:800;
                }
                QPushButton#dashboardManagementSecondary:hover, QToolButton#dashboardManagementMore:hover {
                    background:#281047; border-color:#B34CFF;
                }
                QLabel#dashboardManagementTitle { color:#F4EEFF; font-weight:900; }
                QLabel#dashboardManagementSubtitle { color:#CDBBE8; }
                QLabel#dashboardStatValue { color:#F8F3FF; font-weight:950; }
                QLabel#dashboardStatLabel { color:#CDBBE8; }
                QLabel#dashboardStatIcon {
                    color:#E45CFF; background:#160A2B; border:1px solid #7132A8; border-radius:10px;
                }
                QProgressBar { background:#0B041B; border:1px solid #4A1D72; border-radius:5px; }
                QProgressBar::chunk { background:#8D2CFF; border-radius:4px; }
                QScrollBar:vertical { background:#070214; width:10px; margin:0; }
                QScrollBar::handle:vertical { background:#7337B4; min-height:34px; border-radius:5px; }
                QScrollBar:horizontal { background:#070214; height:10px; margin:0; }
                QScrollBar::handle:horizontal { background:#7337B4; min-width:34px; border-radius:5px; }
                QScrollBar::add-line, QScrollBar::sub-line { width:0px; height:0px; }
            """ if dashboard_mode else (r"""
                QWidget#mainContent { background:#080316; }
                QScrollArea#pageScrollArea, QScrollArea#pageScrollArea > QWidget, QScrollArea#pageScrollArea > QWidget > QWidget {
                    background:#080316; color:#F8F3FF; border:none;
                }
                QFrame#workspaceHeader, QFrame#progressHeader, QFrame#workflowQuickActions {
                    background:rgba(18,7,36,0.98); border:1px solid #5E2A8A; border-radius:14px;
                }
                QLabel#workspaceTitle { color:#F8F3FF; background:transparent; }
                QLabel#workspaceSubtitle, QLabel#workspaceTournament { color:#CDBBE8; background:transparent; }
                QPushButton#sidebarToggle, QPushButton#workspaceBackButton {
                    background:#21103B; color:#F8F3FF; border:1px solid #7337B4; border-radius:9px;
                }
                QPushButton#workflowQuickButton {
                    background:#21103B; color:#E9DEFA; border:1px solid #7337B4; border-radius:9px;
                }
                QPushButton#workflowQuickButton[state="done"] { background:#21103B; color:#DCCCF2; border-color:#7132A8; }
                QPushButton#workflowQuickButton[state="current"] { background:#8D2CFF; color:#FFFFFF; border-color:#B85CFF; }
                QLabel#workflowStatusCompact { color:#D8C7F2; background:transparent; }
                QLabel#progressTitle { color:#E45CFF; background:transparent; }
                QLabel#progressSubtitle { color:#F4EEFF; background:#21103B; border:1px solid #7132A8; border-radius:8px; padding:3px 9px; }
                QFrame#progressRoute { background:transparent; border:none; }
                QPushButton#progressStep { background:#140925; color:#DCCCF2; border:1px solid #5E2A8A; }
                QPushButton#progressStep[done="true"] { background:#8D2CFF; color:#FFFFFF; border:1px solid #B85CFF; }
                QPushButton#progressStep[current="true"] { background:#8D2CFF; color:#FFFFFF; border:2px solid #E05BFF; }
                QLabel#progressStepLabel { color:#CDBBE8; background:transparent; }
                QLabel#progressStepLabel[current="true"] { color:#E45CFF; font-weight:900; }
                QScrollBar:vertical { background:#070214; width:10px; margin:0; border:none; }
                QScrollBar::handle:vertical { background:#7337B4; min-height:34px; border-radius:5px; }
                QScrollBar:horizontal { background:#070214; height:10px; margin:0; border:none; }
                QScrollBar::handle:horizontal { background:#7337B4; min-width:34px; border-radius:5px; }
                QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background:#070214; width:0px; height:0px; border:none; }
                QAbstractScrollArea::corner { background:#080316; border:none; }
            """))
        if hasattr(self, "page_title"):
            self.page_title.setText(title or "MSBTS")
            self.page_subtitle.setText(subtitle)
            self.page_subtitle.setVisible(bool(subtitle))

        current_wrapper = self.stack.currentWidget()
        current_page = next(
            (candidate for candidate, wrapper in self._page_wrappers.items() if wrapper is current_wrapper),
            None,
        )
        if current_page is not None and current_page is not page and not self._history_navigation:
            self._page_history.append(current_page)
            self._page_history = self._page_history[-20:]

        wrapper = self._page_wrappers.get(page)
        if wrapper is not None:
            self._apply_zero_white_native_controls(wrapper)
            self._apply_zero_white_native_controls(page)
            self.stack.setCurrentWidget(wrapper)
            wrapper.verticalScrollBar().setValue(0)
        else:
            self.stack.setCurrentWidget(page)
        self._update_workspace_context(page)
        if hasattr(self, "global_watermark"):
            self.global_watermark.raise_()

    def _go_back(self) -> None:
        if not self._page_history:
            return
        previous = self._page_history.pop()
        self._history_navigation = True
        try:
            self._show_page(previous)
        finally:
            self._history_navigation = False
        self._sync_navigation_for_page(previous)

    def _sync_navigation_for_page(self, page: QWidget) -> None:
        mapping = {
            getattr(self, "dashboard_page", None): "dashboard",
            getattr(self, "workflow_page", None): "workflow",
            getattr(self, "player_page", None): "players",
            getattr(self, "phase_page", None): "phases",
            getattr(self, "competition_page", None): "competition",
            getattr(self, "scorekeeper_page", None): "scorekeepers",
            getattr(self, "live_center_page", None): "live",
            getattr(self, "document_center_page", None): "documents",
            getattr(self, "settings_page", None): "settings",
        }
        key = mapping.get(page)
        if key:
            self._select_nav(key)

    def _set_workspace_action(self, text: str = "", callback: Callable[[], None] | None = None) -> None:
        if not hasattr(self, "workspace_action"):
            return
        previous = getattr(self, "_workspace_action_callback", None)
        if previous is not None:
            try:
                self.workspace_action.clicked.disconnect(previous)
            except (RuntimeError, TypeError):
                pass
        self._workspace_action_callback = None
        self.workspace_action.setVisible(bool(text and callback))
        if text and callback:
            self.workspace_action.setText(text)
            self.workspace_action.clicked.connect(callback)
            self._workspace_action_callback = callback

    def _workflow_step_states(self) -> tuple[list[str], str]:
        """Return visual states for preparation workflow and the next recommended step."""
        if self.tournament is None:
            return ["current", "upcoming", "upcoming", "upcoming"], "Neues Turnier anlegen"

        people = list(getattr(self.tournament, "people", []) or [])
        first = self._first_group_phase()
        groups = list(getattr(first, "groups", []) or []) if first is not None else []
        assigned_ids = {
            person_id
            for group in groups
            for person_id in getattr(group, "participant_ids", [])
        }
        playable = [group for group in groups if len(getattr(group, "participant_ids", [])) >= 2]
        group_matches = [match for group in groups for match in getattr(group, "matches", [])]
        scheduled = [match for match in group_matches if match.scheduled_time and match.table_number is not None]

        players_ready = len(people) >= 2
        groups_ready = bool(players_ready and playable and len(assigned_ids) == len(people))
        schedule_ready = bool(groups_ready and group_matches and len(scheduled) == len(group_matches))
        tournament_finished = self._tournament_status_text() == "Abgeschlossen"

        if not players_ready:
            return ["current", "upcoming", "upcoming", "upcoming"], "Teilnehmer anlegen"
        if not groups_ready:
            return ["done", "current", "upcoming", "upcoming"], "Struktur & Gruppen fertigstellen"
        if not schedule_ready:
            return ["done", "done", "current", "upcoming"], "Spielplan erstellen"
        if tournament_finished:
            return ["done", "done", "done", "done"], "Turnier abgeschlossen"
        return ["done", "done", "done", "current"], "Turniertag öffnen"

    def _refresh_workflow_quick_actions(self) -> None:
        buttons = getattr(self, "workflow_quick_buttons", [])
        if not buttons:
            return
        states, next_step = self._workflow_step_states()
        labels = ("Teilnehmer", "Struktur", "Spielplan", "Turniertag")
        tooltips = (
            "Teilnehmer verwalten",
            "Turnierstruktur und Gruppen",
            "Spielplan und Ergebnisse",
            "Turniertag und Leitstand",
        )
        active_view = getattr(self, "_workflow_view_index", None)
        for index, button in enumerate(buttons):
            readiness_state = states[index] if index < len(states) else "upcoming"
            if active_view == index:
                visual_state = "active"
                prefix = "▶"
                suffix = "aktuell geöffnet"
            elif readiness_state == "current" and active_view is not None:
                visual_state = "recommended"
                prefix = str(index + 1)
                suffix = "empfohlener nächster Schritt"
            else:
                visual_state = readiness_state
                prefix = "✓" if readiness_state == "done" else "▶" if readiness_state == "current" else str(index + 1)
                suffix = "erledigt" if readiness_state == "done" else "nächster Schritt" if readiness_state == "current" else "folgt später"
            button.setText(f"{prefix}  {labels[index]}")
            button.setProperty("state", visual_state)
            button.setProperty("readinessState", readiness_state)
            button.setToolTip(f"{tooltips[index]} · {suffix}")
            button.style().unpolish(button)
            button.style().polish(button)
        if hasattr(self, "workflow_status_compact"):
            status = self._tournament_status_text()
            self.workflow_status_compact.setText(f"{status} · {next_step}")
            self.workflow_status_compact.setToolTip(f"{status} · Nächster Schritt: {next_step}")

    def _tournament_status_text(self) -> str:
        if self.tournament is None:
            return "Kein Turnier"
        matches = [
            match
            for phase in self.tournament.phases
            for group in getattr(phase, "groups", [])
            for match in group.matches
        ]
        matches.extend(
            match
            for phase in self.tournament.phases
            for match in getattr(phase, "matches", [])
        )
        if matches and all(match.result is not None for match in matches):
            final_phase = next(
                (phase for phase in sorted(self.tournament.phases, key=lambda item: item.position)
                 if phase.phase_type is PhaseType.FINAL_ROUND),
                None,
            )
            if final_phase is not None and not final_phase.final_is_finished:
                return "Turnier läuft"
            health = inspect_tournament(self.tournament)
            return "Prüfung erforderlich" if health.errors else "Abgeschlossen"
        if any(match.result is not None for match in matches):
            return "Turnier läuft"
        return "Vorbereitung"

    def _update_workspace_context(self, page: QWidget) -> None:
        # The workflow bar has two different meanings:
        # - readiness: what MSTTS recommends next
        # - location: which workspace is actually open right now
        # Keep them separate so the visible page is always highlighted correctly.
        workflow_page_map = {
            getattr(self, "player_page", None): 0,
            getattr(self, "phase_page", None): 1,
            getattr(self, "competition_page", None): 2,
            getattr(self, "live_center_page", None): 3,
        }
        self._workflow_view_index = workflow_page_map.get(page)
        if hasattr(self, "workspace_tournament"):
            self.workspace_tournament.setText(self.tournament.name if self.tournament else "Kein Turnier")
            self._refresh_workflow_quick_actions()
        if hasattr(self, "back_button"):
            self.back_button.setEnabled(bool(self._page_history))
        if hasattr(self, "quick_actions"):
            self.quick_actions.setVisible(self.tournament is not None)

        action_text = ""
        action = None
        # Startfix 37: a page owns its primary action. The global header no longer
        # duplicates buttons that already exist inside Teilnehmer, Struktur,
        # Spielplan or Turniertag. This leaves one clear dominant action per page
        # and frees horizontal space in the title row.
        if page is getattr(self, "workflow_page", None):
            action_text, action = "Nächsten Schritt öffnen", self.workflow_page._open_recommended_step
        elif page is getattr(self, "document_center_page", None):
            action_text, action = "Exportieren", self.document_center_page.export_selection
        self._set_workspace_action(action_text, action)

    # Legacy header action: "Turnierplan erstellen"; Startfix 14 replaces it contextually.
    def _phase_primary_action(self) -> tuple[str, Callable[[], None]]:
        """Return the single safest next action for the group workspace."""
        try:
            phase = self._first_group_phase()
            if phase is None:
                return "Gruppen einrichten", self.phase_page.configure_structure
            assigned = sum(len(group.participant_ids) for group in phase.groups)
            playable = [group for group in phase.groups if len(group.participant_ids) >= 2]
            if not self.tournament.people:
                return "+ Teilnehmer", self.open_players
            if assigned < len(self.tournament.people) or not playable:
                return "Gruppen einteilen", self.open_phases
            if not any(group.matches for group in playable):
                return "Turnier starten", self.start_tournament_workflow
            return "Turniertag öffnen", self.open_live_center
        except (ValidationError, AttributeError):
            return "Gruppen einrichten", self.phase_page.configure_structure

    def _workflow_route_text(self) -> str:
        """Compact route text for the status bar and operator orientation."""
        if self.tournament is None:
            return "Kein Turnier geöffnet"
        people = len(self.tournament.people)
        if people == 0:
            return "1 Teilnehmer erfassen → 2 Gruppen einteilen → 3 Turnier starten"
        phase = self._first_group_phase()
        if phase is None:
            return "Teilnehmer erfasst → Gruppen einrichten"
        assigned = sum(len(group.participant_ids) for group in phase.groups)
        if assigned < people:
            return f"{assigned}/{people} zugeordnet → Gruppen vervollständigen"
        matches = [match for group in phase.groups for match in group.matches]
        if not matches:
            return "Teilnehmer ✓ → Gruppen ✓ → Turnier starten"
        played = sum(match.result is not None for match in matches)
        if played < len(matches):
            return f"Turnier läuft · {played}/{len(matches)} Gruppenspiele"
        return "Gruppenphase abgeschlossen → nächsten Turnierschritt öffnen"

    def _apply_branding(self) -> None:
        base = Path(__file__).resolve().parent
        icon_path = base / "assets" / "fts_icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._apply_layout_theme(self.theme_key, self.theme_primary, persist=False)

    def _apply_layout_theme(self, key: str, primary_hex: str, *, persist: bool = True) -> None:
        base = Path(__file__).resolve().parent
        style_path = base / "styles" / "fts_brown.qss"
        if not style_path.exists():
            return
        color = QColor(primary_hex)
        if not color.isValid():
            return
        self.theme_key = key
        self.theme_primary = color.name(QColor.NameFormat.HexRgb)
        self.setStyleSheet(build_stylesheet(style_path, self.theme_primary))

        # STARTFIX 163: macOS may paint the QStatusBar/QAbstractScrollArea
        # viewport with the native window palette even when the application QSS
        # is dark.  Apply the brown shell directly to those outer surfaces so
        # no white/grey strips can appear between pages or at the window edge.
        self.statusBar().setStyleSheet(
            "QStatusBar { background:#05020D; color:#D8C7FF; "
            "border:none; border-top:1px solid #4C1975; } "
            "QStatusBar QLabel { background:transparent; color:#D8C7FF; border:none; } "
            "QSizeGrip { background:#05020D; width:0px; height:0px; }"
        )
        self.stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.stack.setAutoFillBackground(True)
        stack_palette = self.stack.palette()
        stack_palette.setColor(QPalette.ColorRole.Window, QColor("#080316"))
        self.stack.setPalette(stack_palette)
        self.stack.setStyleSheet(
            "QStackedWidget#pageStack { background:#080316; border:none; }"
        )
        for wrapper in self._page_wrappers.values():
            wrapper.setStyleSheet(
                "QScrollArea#pageScrollArea { background:#080316; border:none; } "
                "QWidget#pageScrollViewport { background:#080316; border:none; } "
                "QAbstractScrollArea::corner { background:#080316; border:none; }"
            )
        if persist:
            save_theme(self.theme_key, self.theme_primary)
            self.statusBar().showMessage("Layoutfarbe gespeichert", 3000)
        for action_key, action in self.theme_actions.items():
            action.setChecked(action_key == self.theme_key)
        if hasattr(self, "settings_page"):
            self.settings_page.set_theme_state(self.theme_key, self.theme_primary)

    def _choose_custom_layout_color(self) -> None:
        initial = QColor(self.theme_primary)
        color = QColorDialog.getColor(initial, self, "Vereinsfarbe auswählen")
        if color.isValid():
            self._apply_layout_theme("custom", color.name(QColor.NameFormat.HexRgb))

    def _reset_layout_color(self) -> None:
        default = THEME_PRESETS[0]
        self._apply_layout_theme(default.key, default.primary)

    def _build_shell(self) -> None:
        shell = QWidget()
        shell.setObjectName("appShell")
        shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        shell.setAutoFillBackground(True)
        shell_palette = shell.palette()
        shell_palette.setColor(QPalette.ColorRole.Window, QColor("#080316"))
        shell.setPalette(shell_palette)
        shell.setStyleSheet("QWidget#appShell { background:#080316; border:none; }")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)

        content = QWidget()
        content.setObjectName("mainContent")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content.setAutoFillBackground(True)
        content_palette = content.palette()
        content_palette.setColor(QPalette.ColorRole.Window, QColor("#080316"))
        content.setPalette(content_palette)
        content.setStyleSheet("QWidget#mainContent { background:#080316; border:none; }")
        self.main_content = content
        self.main_content = content
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(14, 8, 14, 8)
        content_layout.setSpacing(5)
        self.workspace_header = self._build_workspace_header()
        content_layout.addWidget(self.workspace_header)
        self.progress_header = self._build_progress_header()
        content_layout.addWidget(self.progress_header)
        content_layout.addWidget(self.stack, 1)
        layout.addWidget(content, 1)

        # STARTFIX 106: a single page-wide watermark overlay for the whole
        # workspace (header, progress route and all page content). The sidebar
        # deliberately stays untouched.
        self.global_watermark = GlobalWatermarkOverlay(content)
        self.global_watermark.setGeometry(content.rect())
        self.global_watermark.raise_()
        self.setCentralWidget(shell)


    def _build_workspace_header(self) -> QFrame:
        """Build a two-row header that remains readable on 13-inch displays."""
        frame = QFrame()
        frame.setObjectName("workspaceHeader")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(6)

        # Row 1: navigation, page identity, tournament and contextual primary action.
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        self.sidebar_toggle = QPushButton("☰")
        self.sidebar_toggle.setObjectName("sidebarToggle")
        self.sidebar_toggle.setFixedSize(40, 40)
        self.sidebar_toggle.setToolTip("Seitenleiste ein- oder ausklappen")
        self.sidebar_toggle.clicked.connect(self.toggle_sidebar)
        top.addWidget(self.sidebar_toggle)

        self.back_button = QPushButton("‹")
        self.back_button.setObjectName("workspaceBackButton")
        self.back_button.setFixedSize(40, 40)
        self.back_button.setToolTip("Zurück zum vorherigen Bereich")
        self.back_button.setEnabled(False)
        self.back_button.clicked.connect(self._go_back)
        top.addWidget(self.back_button)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        self.page_title = QLabel("MSTTS")
        self.page_title.setObjectName("workspaceTitle")
        self.page_subtitle = QLabel("")
        self.page_subtitle.setObjectName("workspaceSubtitle")
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        top.addLayout(titles, 1)

        self.workspace_tournament = QLabel("Kein Turnier")
        self.workspace_tournament.setObjectName("workspaceTournament")
        self.workspace_tournament.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.workspace_tournament.setMinimumWidth(110)
        self.workspace_tournament.setMaximumWidth(180)
        self.workspace_tournament.setToolTip('13"-Arbeitsansicht')
        top.addWidget(self.workspace_tournament)

        # Compatibility marker retained, but no longer consumes visible space.
        self.workspace_status = QLabel('13"-Arbeitsansicht')
        self.workspace_status.setObjectName("workspaceStatus")
        self.workspace_status.setVisible(False)

        self.workspace_action = QPushButton()
        self.workspace_action.setObjectName("workspacePrimaryAction")
        self.workspace_action.setMinimumHeight(34)
        self.workspace_action.setMaximumWidth(190)
        self.workspace_action.setVisible(False)
        top.addWidget(self.workspace_action)
        outer.addLayout(top)

        # Row 2: workflow only. This row owns the horizontal space, so no labels
        # have to be clipped to make room for the page title or primary action.
        self.quick_actions = QFrame()
        self.quick_actions.setObjectName("workflowQuickActions")
        quick = QHBoxLayout(self.quick_actions)
        quick.setContentsMargins(88, 0, 0, 0)
        quick.setSpacing(5)
        quick_items = [
            ("Teilnehmer", self.open_players, "Teilnehmer verwalten"),
            ("Struktur", self.open_phases, "Turnierstruktur und Gruppen"),
            ("Spielplan", self.open_competition, "Spielplan und Ergebnisse"),
            ("Turniertag", self.open_live_center, "Turniertag und Leitstand"),
        ]
        self.workflow_quick_buttons = []
        self.workflow_quick_items = quick_items
        self.workflow_status_compact = QLabel("Vorbereitung")
        self.workflow_status_compact.setObjectName("workflowStatusCompact")
        self.workflow_status_compact.setToolTip("Aktueller Turnierstatus und empfohlener nächster Schritt")
        self.workflow_status_compact.setMinimumWidth(130)
        self.workflow_status_compact.setMaximumWidth(230)
        quick.addWidget(self.workflow_status_compact)
        quick.addStretch(1)
        for text, callback, tooltip in quick_items:
            button = QPushButton(text)
            button.setObjectName("workflowQuickButton")
            button.setToolTip(tooltip)
            button.setProperty("state", "upcoming")
            button.setMinimumWidth(104)
            button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            button.clicked.connect(callback)
            quick.addWidget(button)
            self.workflow_quick_buttons.append(button)
        outer.addWidget(self.quick_actions)
        return frame

    def toggle_sidebar(self) -> None:
        self.sidebar_collapsed = not self.sidebar_collapsed
        for widget in self.sidebar_brand_widgets:
            widget.setVisible(not self.sidebar_collapsed)
        for key, button in self.nav_buttons.items():
            full_text = self.nav_button_texts.get(key, button.text())
            button.setToolTip(full_text.replace("  ", " "))
            if self.sidebar_collapsed:
                button.setText(full_text.split()[0] if full_text.split() else "•")
            else:
                button.setText(full_text)
        self.sidebar_toggle.setText("›" if self.sidebar_collapsed else "☰")
        compact = self.width() < 1180
        very_compact = self.width() < 980
        width = 64 if self.sidebar_collapsed else (176 if very_compact else 202 if compact else 218)
        self.sidebar.setFixedWidth(width)
        self.nav_layout.setContentsMargins(6 if self.sidebar_collapsed else 12, 8, 6 if self.sidebar_collapsed else 12, 8)

    def _build_progress_header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("progressHeader")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(18, 10, 18, 10)
        outer.setSpacing(7)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("TURNIERFORTSCHRITT")
        title.setObjectName("progressTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.progress_subtitle = QLabel("Aktuell: Gruppenphase")
        self.progress_subtitle.setObjectName("progressSubtitle")
        self.progress_subtitle.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.progress_subtitle)
        outer.addLayout(head)

        # STARTFIX 95: a real visual route instead of six unrelated tab buttons.
        route = QFrame()
        route.setObjectName("progressRoute")
        grid = QGridLayout(route)
        grid.setContentsMargins(4, 0, 4, 0)
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(2)

        self.progress_steps = []
        self.progress_connectors = []
        self.progress_step_labels = [
            ("1", "Gruppenphase"), ("2", "Qualifikation"),
            ("3", "Zwischenrunde"), ("4", "Viertelfinale"),
            ("5", "Halbfinale"), ("6", "Finale"),
        ]
        callbacks = [
            self.open_dashboard_group_stage, self.open_dashboard_qualification,
            self.open_dashboard_intermediate, lambda: self.open_dashboard_ko_round(1),
            lambda: self.open_dashboard_ko_round(2), lambda: self.open_dashboard_ko_round(3),
        ]
        for i, ((number, text), callback) in enumerate(zip(self.progress_step_labels, callbacks)):
            col = i * 2
            step = QPushButton(number)
            step.setObjectName("progressStep")
            step.setFixedSize(38, 38)
            step.setCursor(Qt.CursorShape.PointingHandCursor)
            step.setToolTip(f"{text} direkt öffnen")
            step.setProperty("active", i == 0)
            step.setProperty("current", i == 0)
            step.clicked.connect(callback)
            grid.addWidget(step, 0, col, alignment=Qt.AlignmentFlag.AlignHCenter)
            label = QLabel(text)
            label.setObjectName("progressStepLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            label.setProperty("active", i == 0)
            label.setProperty("current", i == 0)
            grid.addWidget(label, 1, col)
            step.phase_label = label
            self.progress_steps.append(step)
            grid.setColumnStretch(col, 1)
            if i < 5:
                connector = QFrame()
                connector.setObjectName("progressConnector")
                connector.setFixedHeight(2)
                connector.setProperty("active", False)
                grid.addWidget(connector, 0, col + 1)
                self.progress_connectors.append(connector)
                grid.setColumnStretch(col + 1, 1)
        outer.addWidget(route)
        return frame

    def _set_progress_step(self, index: int) -> None:
        """Show completed, current and upcoming phases without ambiguity."""
        index = max(0, min(index, len(getattr(self, "progress_steps", [])) - 1))
        self._progress_index = index
        labels = getattr(self, "progress_step_labels", [])
        for position, button in enumerate(getattr(self, "progress_steps", [])):
            completed = position < index
            current = position == index
            button.setProperty("active", position <= index)
            button.setProperty("completed", completed)
            button.setProperty("current", current)
            if position < len(labels):
                number, text = labels[position]
                button.setText("✓" if completed else number)
                phase_label = getattr(button, "phase_label", None)
                if phase_label is not None:
                    phase_label.setProperty("active", position <= index)
                    phase_label.setProperty("completed", completed)
                    phase_label.setProperty("current", current)
                    phase_label.style().unpolish(phase_label)
                    phase_label.style().polish(phase_label)
            button.style().unpolish(button)
            button.style().polish(button)
        for connector_position, connector in enumerate(getattr(self, "progress_connectors", [])):
            connector.setProperty("active", connector_position < index)
            connector.style().unpolish(connector)
            connector.style().polish(connector)
        if hasattr(self, "progress_subtitle") and labels:
            self.progress_subtitle.setText(f"Aktuell: {labels[index][1]}")
        self._refresh_progress_labels()

    def _refresh_optional_progress_visibility(self) -> None:
        if self.tournament is None or not hasattr(self, "progress_steps"):
            return
        try:
            qualification_active, intermediate_active = self.service.effective_optional_phases()
        except ValidationError:
            qualification_active, intermediate_active = False, False
        # MSBTS Alpha 23: Keep the complete tournament route visible at all times.
        # Optional phases are shown dimmed/disabled when they are not active instead of
        # disappearing from the progress header. This avoids the visual jump from
        # Gruppenphase directly to Viertelfinale and makes the tournament structure clear.
        for idx, active, name in (
            (1, qualification_active, "Qualifikation"),
            (2, intermediate_active, "Zwischenrunde"),
        ):
            step = self.progress_steps[idx]
            label = getattr(step, "phase_label", None)
            step.setVisible(True)
            step.setEnabled(active)
            step.setProperty("optionalDisabled", not active)
            if label is not None:
                label.setVisible(True)
                label.setProperty("optionalDisabled", not active)
                label.style().unpolish(label)
                label.style().polish(label)
            step.setToolTip(
                f"{name} direkt öffnen" if active
                else f"{name} ist für dieses Turnier derzeit nicht aktiv"
            )
            step.style().unpolish(step)
            step.style().polish(step)

    def _reset_navigation(self) -> None:
        while self.nav_layout.count():
            item = self.nav_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.nav_buttons.clear()
        self.nav_button_texts.clear()
        self.sidebar_brand_widgets.clear()

        # MSBTS Alpha 12: compact, collision-proof brand zone.
        # The logo is intentionally small and the header has one exact height,
        # so the first navigation button can never overlap it on 13-inch Macs.
        brand_header = QFrame()
        brand_header.setObjectName("sidebarBrandHeader")
        brand_header.setFixedHeight(112)
        brand_header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        brand_header.setStyleSheet(
            "QFrame#sidebarBrandHeader{background:#0B041B;border:none;}"
            "QLabel#brandLogo{background:#0B041B;border:none;padding:0;margin:0;}"
        )
        brand_layout = QVBoxLayout(brand_header)
        brand_layout.setContentsMargins(10, 8, 10, 8)
        brand_layout.setSpacing(0)

        logo = QLabel()
        logo.setObjectName("brandLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        logo.setFixedSize(104, 92)
        logo_path = Path(__file__).resolve().parent / "assets" / "msbts_logo.png"
        if logo_path.exists():
            logo.setPixmap(
                QPixmap(str(logo_path)).scaled(100, 88, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        brand_layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignCenter)
        self.nav_layout.addWidget(brand_header, 0, Qt.AlignmentFlag.AlignTop)
        self.nav_layout.addSpacing(8)
        self.sidebar_brand_widgets.extend([brand_header, logo])

        # MSBTS Alpha 14: the menu itself is a vertical slidebar.
        # The brand/logo stays fixed above it, while all navigation entries
        # remain reachable on compact 13-inch screens.
        self.sidebar_nav_scroll = QScrollArea()
        self.sidebar_nav_scroll.setObjectName("sidebarNavSlider")
        # MSBTS Alpha 15: force the native scroll viewport itself to the purple
        # sidebar palette. On macOS the QScrollArea viewport can otherwise fall
        # back to the system light background even when the surrounding QSS is
        # dark, which produced the large white menu panel seen in Alpha 14.
        self.sidebar_nav_scroll.setStyleSheet(
            "QScrollArea#sidebarNavSlider{background:#120625;border:none;}"
            "QScrollArea#sidebarNavSlider QWidget#qt_scrollarea_viewport{background:#120625;border:none;}"
            "QScrollArea#sidebarNavSlider QWidget{background:#120625;}"
            "QScrollArea#sidebarNavSlider QScrollBar:vertical{background:#17082D;width:9px;margin:2px 1px;border:none;}"
            "QScrollArea#sidebarNavSlider QScrollBar::handle:vertical{background:#8B20F5;border:1px solid #C04CFF;border-radius:4px;min-height:34px;}"
            "QScrollArea#sidebarNavSlider QScrollBar::handle:vertical:hover{background:#A414FF;}"
            "QScrollArea#sidebarNavSlider QScrollBar::add-line:vertical,QScrollArea#sidebarNavSlider QScrollBar::sub-line:vertical,"
            "QScrollArea#sidebarNavSlider QScrollBar::add-page:vertical,QScrollArea#sidebarNavSlider QScrollBar::sub-page:vertical{background:transparent;border:none;height:0px;}"
        )
        self.sidebar_nav_scroll.setWidgetResizable(True)
        self.sidebar_nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.sidebar_nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar_nav_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.sidebar_nav_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.sidebar_nav_content = QWidget()
        self.sidebar_nav_content.setObjectName("sidebarNavSliderContent")
        self.sidebar_nav_content.setStyleSheet("QWidget#sidebarNavSliderContent{background:#120625;border:none;}")
        nav_palette = self.sidebar_nav_content.palette()
        nav_palette.setColor(QPalette.ColorRole.Window, QColor("#120625"))
        nav_palette.setColor(QPalette.ColorRole.Base, QColor("#120625"))
        self.sidebar_nav_content.setPalette(nav_palette)
        self.sidebar_nav_content.setAutoFillBackground(True)
        self.nav_items_layout = QVBoxLayout(self.sidebar_nav_content)
        self.nav_items_layout.setContentsMargins(0, 2, 4, 2)
        self.nav_items_layout.setSpacing(4)
        self.sidebar_nav_scroll.setWidget(self.sidebar_nav_content)
        self.nav_layout.addWidget(self.sidebar_nav_scroll, 1)

        product = QLabel("MSBTS")
        product.setObjectName("sidebarProductName")
        product.setAlignment(Qt.AlignmentFlag.AlignCenter)
        product.setVisible(False)
        self.nav_items_layout.addWidget(product)
        self.sidebar_brand_widgets.append(product)

        product_subtitle = QLabel("Badminton Tournament System\nM. Seemann")
        product_subtitle.setObjectName("sidebarProductSubtitle")
        product_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        product_subtitle.setWordWrap(True)
        product_subtitle.setVisible(False)
        self.nav_items_layout.addWidget(product_subtitle)
        self.sidebar_brand_widgets.append(product_subtitle)

        # Alpha 10: no divider below the logo; the clean gap is enough and
        # saves vertical space on 13-inch displays.
        self.nav_items_layout.addSpacing(4)

    def _add_sidebar_signature(self) -> None:
        """Finish the premium sidebar with a compact live tournament status card."""
        separator = QFrame()
        separator.setObjectName("navDivider")
        separator.setFrameShape(QFrame.Shape.HLine)
        self.nav_items_layout.addWidget(separator)

        card = QFrame()
        card.setObjectName("sidebarStatusCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(3)

        title = QLabel("●  Turnier läuft" if self.tournament is not None else "○  Kein Turnier")
        title.setObjectName("sidebarStatusTitle")
        detail = QLabel("Automatisch gespeichert" if self.tournament is not None else "Neues Turnier anlegen")
        detail.setObjectName("sidebarStatusDetail")
        detail.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(detail)
        self.nav_items_layout.addWidget(card)
        self.sidebar_status_card = card
        self.sidebar_status_title = title
        self.sidebar_status_detail = detail
        self.sidebar_brand_widgets.append(card)

    def _add_nav_section(self, text: str) -> None:
        label = QLabel(text.upper())
        label.setObjectName("navSectionLabel")
        label.setProperty("sidebarSection", True)
        # Alpha 18: make sidebar section headings bright enough on the dark
        # purple navigation background, including macOS native rendering.
        label.setStyleSheet(
            "QLabel { background: transparent; color: #F3B2FF; "
            "font-weight: 900; letter-spacing: 1.2px; }"
        )
        self.nav_items_layout.addSpacing(5)
        self.nav_items_layout.addWidget(label)
        self.sidebar_brand_widgets.append(label)

    def _add_nav_button(self, key: str, text: str, callback: Callable[[], None]) -> None:
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.setCheckable(True)
        # Alpha 18: use a local stylesheet so the selected page cannot inherit
        # one of the old dark-text nav rules from earlier MSTTS themes.
        # This keeps every sidebar label bright and readable on all pages.
        button.setStyleSheet(
            "QPushButton#navButton {"
            "background: transparent; color: #FFF9FF; font-weight: 750; "
            "border: 1px solid transparent; border-radius: 9px; "
            "padding: 6px 9px; text-align: left;}"
            "QPushButton#navButton:hover {"
            "background: #2D1252; color: #FFFFFF; font-weight: 850; "
            "border: 1px solid #8141D8;}"
            "QPushButton#navButton:checked {"
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #7B1BEF, stop:1 #A414FF); "
            "color: #FFFFFF; font-weight: 900; "
            "border: 1px solid #C76BFF; border-left: 4px solid #F0B5FF;}"
            "QPushButton#navButton:disabled { color: #DCCCF3; }"
        )
        button.clicked.connect(callback)
        self.nav_items_layout.addWidget(button)
        self.nav_buttons[key] = button
        self.nav_button_texts[key] = text

    def _select_nav(self, key: str) -> None:
        for name, button in self.nav_buttons.items():
            button.setChecked(name == key)

    def open_dashboard(self) -> None:
        if hasattr(self, "dashboard_page"):
            self.dashboard_page.refresh()
            self._show_page(self.dashboard_page)
            self._select_nav("dashboard")
            self._set_progress_step(0)


    def open_document_center(self) -> None:
        """Open the document center reliably, even if refreshing its preview fails.

        The navigation state and stacked page are switched *before* loading document
        data.  Previously, an exception inside ``activate()`` left the settings page
        visible while the clicked Documents button stayed checked.
        """
        if not hasattr(self, "document_center_page"):
            return

        self._show_page(self.document_center_page)
        self._select_nav("documents")
        try:
            self.document_center_page.activate()
        except Exception as error:  # keep the page accessible and report the real cause
            self.statusBar().showMessage(f"Dokumentenzentrale konnte nicht vollständig aktualisiert werden: {error}", 8000)
            QMessageBox.warning(
                self,
                "Dokumentenzentrale",
                "Die Dokumentenzentrale wurde geöffnet, aber die Vorschau konnte "
                f"nicht vollständig aktualisiert werden.\n\n{error}",
            )

    def open_settings(self) -> None:
        if hasattr(self, "settings_page"):
            self.settings_page.set_theme_state(self.theme_key, self.theme_primary)
            self._show_page(self.settings_page)
            self._select_nav("settings")

    def _create_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&Datei")
        new_action = QAction("&Neues Turnier", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self.confirm_new_tournament)
        file_menu.addAction(new_action)
        file_menu.addSeparator()
        import_tournament_action = QAction("Turnierdatei öffnen…", self)
        import_tournament_action.triggered.connect(self.import_tournament_json)
        file_menu.addAction(import_tournament_action)
        export_tournament_action = QAction("Turnierdatei speichern unter…", self)
        export_tournament_action.triggered.connect(self.export_tournament_json)
        file_menu.addAction(export_tournament_action)
        archive_action = QAction("Turnierarchiv erstellen…", self)
        archive_action.triggered.connect(self.export_tournament_archive)
        file_menu.addAction(archive_action)
        file_menu.addSeparator()
        import_players_action = QAction("Spieler aus CSV importieren…", self)
        import_players_action.triggered.connect(self.import_players_csv)
        file_menu.addAction(import_players_action)
        export_players_action = QAction("Spieler als CSV exportieren…", self)
        export_players_action.triggered.connect(self.export_players_csv)
        file_menu.addAction(export_players_action)
        export_competition_action = QAction("Wettbewerb als CSV exportieren…", self)
        export_competition_action.triggered.connect(self.export_competition_csv)
        file_menu.addAction(export_competition_action)
        file_menu.addSeparator()
        print_players_action = QAction("Teilnehmerliste als PDF exportieren…", self)
        print_players_action.triggered.connect(self.export_players_html)
        file_menu.addAction(print_players_action)
        print_competition_action = QAction("Wettbewerb als PDF exportieren…", self)
        print_competition_action.triggered.connect(self.export_competition_html)
        file_menu.addAction(print_competition_action)
        certificates_action = QAction("Urkunden als PDF erstellen…", self)
        certificates_action.triggered.connect(self.export_certificates_html)
        file_menu.addAction(certificates_action)
        medals_action = QAction("Medaillenspiegel als PDF exportieren…", self)
        medals_action.triggered.connect(self.export_medal_table_html)
        file_menu.addAction(medals_action)
        presentation_action = QAction("Präsentationsmodus öffnen", self)
        presentation_action.setShortcut(QKeySequence("F11"))
        presentation_action.triggered.connect(self.open_presentation)
        file_menu.addAction(presentation_action)
        live_action = QAction("Live-Ansicht erstellen…", self)
        live_action.triggered.connect(self.export_live_html)
        file_menu.addAction(live_action)
        check_action = QAction("Turnierprüfung als PDF exportieren…", self)
        check_action.triggered.connect(self.export_validation_html)
        file_menu.addAction(check_action)
        file_menu.addSeparator()
        backup_action = QAction("Sicherung erstellen…", self)
        backup_action.triggered.connect(self.create_backup)
        file_menu.addAction(backup_action)
        auto_backup_action = QAction("Automatische Sicherung erstellen", self)
        auto_backup_action.triggered.connect(self.create_automatic_backup)
        file_menu.addAction(auto_backup_action)
        restore_action = QAction("Sicherung wiederherstellen…", self)
        restore_action.triggered.connect(self.restore_backup)
        file_menu.addAction(restore_action)
        file_menu.addSeparator()
        quit_action = QAction("&Beenden", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        appearance_menu = self.menuBar().addMenu("&Darstellung")
        color_menu = appearance_menu.addMenu("Layoutfarbe")
        for preset in THEME_PRESETS:
            action = QAction(preset.name, self)
            action.setCheckable(True)
            action.setChecked(preset.key == self.theme_key)
            action.triggered.connect(
                lambda checked=False, p=preset: self._apply_layout_theme(p.key, p.primary)
            )
            color_menu.addAction(action)
            self.theme_actions[preset.key] = action
        color_menu.addSeparator()
        custom_action = QAction("Eigene Vereinsfarbe auswählen…", self)
        custom_action.setCheckable(True)
        custom_action.setChecked(self.theme_key == "custom")
        custom_action.triggered.connect(self._choose_custom_layout_color)
        color_menu.addAction(custom_action)
        self.theme_actions["custom"] = custom_action
        reset_action = QAction("Standardfarbe wiederherstellen", self)
        reset_action.triggered.connect(self._reset_layout_color)
        appearance_menu.addAction(reset_action)

        help_menu = self.menuBar().addMenu("&Hilfe")
        about_action = QAction("&Über MSBTS", self)
        about_action.setShortcut(QKeySequence("F1"))
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def clear_stack(self) -> None:
        self._page_wrappers.clear()
        self._page_history.clear()
        while self.stack.count():
            widget = self.stack.widget(0)
            self.stack.removeWidget(widget)
            widget.deleteLater()

    def show_start_page(self) -> None:
        self.clear_stack()
        self._reset_navigation()
        self.nav_layout.addStretch()
        page = StartPage(
            self.confirm_new_tournament,
            self.import_tournament_json,
            self.open_recent_tournament,
            self._recent_tournament_files(),
            self.create_demo_tournament,
            self.create_freudenholm_demo,
        )
        self._register_page(page, title="MSBTS", subtitle="Badminton verbindet · Turnier neu anlegen oder vorhandenes Turnier öffnen")
        self._show_page(page)
        self.update_status()

    def show_tournament(self) -> None:
        if self.tournament is None:
            self.show_start_page(); return
        self.clear_stack()
        self._reset_navigation()
        icon_path = Path(__file__).resolve().parent / "assets" / "fts_icon.png"
        self.dashboard_page = DashboardPage(self.tournament, str(icon_path))
        self.workflow_page = WorkflowPage(
            self.service,
            self.open_players,
            self.open_phases,
            self.start_tournament_workflow,
            self.open_results_entry,
            self.open_live_center,
        )
        self.player_page = PlayerPage(self.service, self.open_phases, self.saved)
        self.phase_page = PhasePage(self.service, self.open_players, self.open_live_center, self.saved, self.confirm_new_tournament)
        self.competition_page = CompetitionPage(self.service, self.open_phases, self.saved)
        self.scorekeeper_page = ScorekeeperPage(self.service, self.saved)
        self.live_center_page = LiveCenterPage(self.service, self.saved, self.continue_tournament_flow)
        self.document_center_page = DocumentCenterPage(self.service)
        self.settings_page = SettingsPage(
            self.theme_key,
            self.theme_primary,
            self._apply_layout_theme,
            self._choose_custom_layout_color,
            self._reset_layout_color,
        )
        self.designer_page = TournamentDesignerPage(
            self.service,
            self.saved,
            self.continue_from_designer,
            self.open_results_entry,
        )
        self.dashboard_page.open_players_requested.connect(self.open_players)
        self.dashboard_page.open_phases_requested.connect(self.open_phases)
        self.dashboard_page.open_qualification_requested.connect(self.open_dashboard_qualification)
        self.dashboard_page.open_competition_requested.connect(self.open_competition)
        self.dashboard_page.open_live_requested.connect(self.open_live_center)
        self.dashboard_page.open_group_stage_requested.connect(self.open_dashboard_group_stage)
        self.dashboard_page.open_intermediate_requested.connect(self.open_dashboard_intermediate)
        self.dashboard_page.open_quarterfinal_requested.connect(lambda: self.open_dashboard_ko_round(1))
        self.dashboard_page.open_semifinal_requested.connect(lambda: self.open_dashboard_ko_round(2))
        self.dashboard_page.open_final_requested.connect(lambda: self.open_dashboard_ko_round(3))
        self.dashboard_page.new_tournament_requested.connect(self.confirm_new_tournament)
        self.dashboard_page.delete_tournament_requested.connect(self.confirm_delete_tournament)
        self.dashboard_page.import_tournament_requested.connect(self.import_tournament_json)
        self.dashboard_page.export_tournament_requested.connect(self.export_tournament_json)
        self.dashboard_page.settings_requested.connect(self.open_settings)
        self.dashboard_page.open_documents_requested.connect(self.open_document_center)
        self.dashboard_page.repair_tournament_requested.connect(self.repair_tournament_safely)
        self._register_page(self.dashboard_page, title="Dashboard", subtitle="Badminton verbindet - alles im Ueberblick.")
        self._register_page(self.workflow_page, title="Turnierassistent", subtitle="Schritt für Schritt vom Turnier bis zum Spielbetrieb")
        self._register_page(self.player_page, horizontal_scroll=True, title="Teilnehmer", subtitle="Spieler verwalten, importieren und Gruppen zuordnen")
        self._register_page(self.phase_page, horizontal_scroll=True, title="Gruppen und Phasen", subtitle="Gruppenanzahl, Qualifikation und Turnierstruktur")
        self._register_page(self.competition_page, horizontal_scroll=True, title="Wettbewerb", subtitle="Spielplan, Ergebnisse und Tabellen")
        self._register_page(self.scorekeeper_page, horizontal_scroll=True, title="Schiedsrichter", subtitle="Namen verwalten und automatisch fair auf die Felder verteilen")
        self._register_page(self.live_center_page, horizontal_scroll=True, title="Turniertag – Leitstand", subtitle="Aktuelle Spiele, nächste Begegnungen und offene Ergebnisse")
        self._register_page(self.document_center_page, title="Dokumentenzentrale", subtitle="Vorschau, Gestaltung und Export aller Turnierdokumente")
        self._register_page(self.settings_page, title="Einstellungen", subtitle="Design, Vereinsfarben und Programmeinstellungen")
        self._register_page(self.designer_page, horizontal_scroll=True, title="Turnierdesigner", subtitle="Turnierablauf und Runden individuell gestalten")
        # Alpha 9: the logo already establishes the product/turnier context;
        # start navigation directly with Dashboard, matching the target mockup.
        self._add_nav_button("dashboard", "⌂   Dashboard", self.open_dashboard)
        self._add_nav_button("new_tournament", "＋   Neues Turnier", self.confirm_new_tournament)
        # MSBTS Alpha 25: Demo access stays visible even while a tournament is open.
        # This avoids hiding the demo behind the start page and makes full workflow testing
        # (Gruppenphase -> Qualifikation -> Zwischenrunde -> K.-o.) available at any time.
        self._add_nav_button("demo_tournament", "▶   Demo-Turnier", self.create_demo_tournament)
        self._add_nav_section("Vorbereitung")
        self._add_nav_button("players", "♟   Teilnehmer", self.open_players)
        self._add_nav_button("phases", "▱   Struktur / Gruppen", self.open_phases)
        self._add_nav_button("competition", "▦   Spielplan", self.open_competition)
        self._add_nav_button("scorekeepers", "▥   Schiedsrichter", self.open_scorekeepers)
        self._add_nav_section("Durchführung")
        self._add_nav_button("live", "▶   Turniertag", self.open_live_center)
        self._add_nav_button("workflow", "✦   Assistent", self.open_workflow)
        self._add_nav_section("Ausgabe")
        self._add_nav_button("documents", "▤   Dokumente", self.open_document_center)
        self._add_nav_button("presentation", "▣   Präsentation", self.open_presentation)
        self._add_nav_section("System")
        self._add_nav_button("settings", "⚙   Einstellungen", self.open_settings)
        self._add_nav_button("data", "◉   Datenverwaltung", self.open_data_management)
        self.nav_items_layout.addStretch()
        self._add_sidebar_signature()
        self.open_dashboard()
        self.update_status()

    def open_data_management(self) -> None:
        """Readable brown data-management dialog with full-width actions."""
        if self.tournament is None:
            self.import_tournament_json()
            return

        dialog = QDialog(self)
        prepare_dialog(dialog, "Datenverwaltung", width=900)
        dialog.setModal(True)
        dialog.setStyleSheet("""
            QDialog#professionalDialog {
                background:#140925; color:#F4EEFF;
                border:1px solid #7337B4;
            }
            QLabel#dataDialogTitle {
                background:transparent; border:none;
                color:#F8F3FF; font-size:24px; font-weight:900;
                padding:0px;
            }
            QLabel#dataDialogText {
                background:transparent; border:none;
                color:#DDBD9F; font-size:15px;
                padding:0px;
            }
            QPushButton[dataAction=\"true\"] {
                background:#8D2CFF; color:#FFFFFF; border:1px solid #B66B36;
                border-radius:10px; min-height:48px; padding:0 18px;
                font-size:13px; font-weight:900;
            }
            QPushButton[dataAction=\"true\"]:hover {
                background:#A85B2A; border-color:#D18A52;
            }
            QPushButton#dataCloseButton {
                background:#21103B; color:#EFE5FF; border:1px solid #7440B0;
                border-radius:10px; min-height:44px; padding:0 22px;
                font-size:14px; font-weight:800;
            }
            QPushButton#dataCloseButton:hover { background:#281047; border-color:#B34CFF; }
        """)

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(28, 26, 28, 24)
        outer.setSpacing(12)

        title = QLabel("Datenverwaltung")
        title.setObjectName("dataDialogTitle")
        outer.addWidget(title)

        text = QLabel("Turnierdaten sichern, exportieren oder wiederherstellen")
        text.setObjectName("dataDialogText")
        text.setWordWrap(True)
        outer.addWidget(text)
        outer.addSpacing(8)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        backup = QPushButton("Sicherung erstellen")
        export = QPushButton("Turnier exportieren")
        restore = QPushButton("Sicherung wiederherstellen")
        for button in (backup, export, restore):
            button.setProperty("dataAction", True)
            button.setMinimumWidth(245)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button, 1)
        outer.addLayout(actions)
        outer.addSpacing(6)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("Schließen")
        close_button.setObjectName("dataCloseButton")
        close_button.setMinimumWidth(150)
        close_button.clicked.connect(dialog.reject)
        close_row.addWidget(close_button)
        outer.addLayout(close_row)

        selected = {"action": None}
        backup.clicked.connect(lambda: (selected.__setitem__("action", "backup"), dialog.accept()))
        export.clicked.connect(lambda: (selected.__setitem__("action", "export"), dialog.accept()))
        restore.clicked.connect(lambda: (selected.__setitem__("action", "restore"), dialog.accept()))

        dialog.exec()
        if selected["action"] == "backup":
            self.create_backup()
        elif selected["action"] == "export":
            self.export_tournament_json()
        elif selected["action"] == "restore":
            self.restore_backup()
        self._select_nav("data")

    def open_presentation(self) -> None:
        if self.tournament is None:
            return
        # STARTFIX 152: Ältere K.-o.-Pläne mit fehlenden Uhrzeiten beim Öffnen
        # der Präsentation automatisch aus der gespeicherten Startzeit reparieren.
        tournament = self.service.require_tournament()
        for phase in tournament.phases:
            if phase.phase_type is PhaseType.FINAL_ROUND and any(
                match.result is None and not match.scheduled_time for match in phase.matches
            ):
                self.service.sync_final_schedule_to_defaults(phase.id)
                self.tournament = self.service.require_tournament()
                break
        if self.presentation_window is None:
            self.presentation_window = PresentationWindow(self.service)
        self.presentation_window.refresh()
        self.presentation_window.showFullScreen()
        self.presentation_window.raise_()
        self.presentation_window.activateWindow()
        self._select_nav("presentation")

    def _recent_tournament_files(self) -> tuple[Path, ...]:
        settings = QSettings()
        raw = settings.value("recent_tournament_files", [])
        if isinstance(raw, str):
            raw = [raw]
        valid: list[Path] = []
        for value in raw or []:
            path = Path(str(value)).expanduser()
            if path.exists() and path.is_file() and path not in valid:
                valid.append(path)
        if raw and len(valid) != len(raw):
            settings.setValue("recent_tournament_files", [str(path) for path in valid])
        return tuple(valid[:6])

    def _remember_recent_tournament(self, path: Path) -> None:
        path = path.expanduser().resolve()
        current = [item for item in self._recent_tournament_files() if item != path]
        QSettings().setValue(
            "recent_tournament_files",
            [str(path), *[str(item) for item in current[:5]]],
        )

    def open_recent_tournament(self, path: Path) -> None:
        if not path.exists():
            QMessageBox.warning(self, "MSBTS", "Die ausgewählte Turnierdatei wurde nicht gefunden.")
            return
        if self.tournament is not None:
            answer = QMessageBox.warning(
                self,
                "Turnier öffnen",
                "Das aktuelle Turnier wird ersetzt. Fortfahren?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self.tournament = self.service.import_tournament_json(path)
            self._remember_recent_tournament(path)
            self.show_tournament()
            self.statusBar().showMessage(f'„{self.tournament.name}“ geöffnet', 4000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Importfehler", str(error))

    def create_tournament(self) -> None:
        name, accepted = QInputDialog.getText(self, "Neues Turnier", "Turniername")
        if not accepted:
            return
        try:
            tournament = self.service.create_tournament(name)
        except (ValidationError, OSError) as error:
            QMessageBox.critical(self, "FTS", str(error)); return
        self.tournament = tournament
        self.show_tournament()

    def _confirm_replace_for_demo(self, title: str) -> bool:
        if self.tournament is None:
            return True
        answer = QMessageBox.warning(
            self,
            title,
            "Das aktuell geladene Turnier wird vollständig ersetzt.\n\nFortfahren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def create_demo_tournament(self) -> None:
        if not self._confirm_replace_for_demo("Demo-Turnier"):
            return
        try:
            tournament = self.service.create_demo_tournament(replace=True)
        except (ValidationError, OSError) as error:
            QMessageBox.critical(self, "FTS", str(error))
            return
        self.tournament = tournament
        self.show_tournament()
        self.statusBar().showMessage("Badminton-Demoturnier mit Qualifikation und Zwischenrunde wurde erstellt.", 5000)

    def create_freudenholm_demo(self) -> None:
        if not self._confirm_replace_for_demo("Freudenholm 2026 testen"):
            return
        try:
            tournament = self.service.create_freudenholm_demo(replace=True)
        except (ValidationError, OSError) as error:
            QMessageBox.critical(self, "FTS", str(error))
            return
        self.tournament = tournament
        self.show_tournament()
        self.statusBar().showMessage("Freudenholm-2026-Testturnier wurde erstellt.", 5000)

    def confirm_new_tournament(self) -> None:
        dialog = NewTournamentDialog(self, has_tournament=self.tournament is not None)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        backup_path = None
        try:
            if dialog.create_backup():
                backup_path = self.service.create_automatic_backup(
                    retention=10, reason="Sicherung vor neuem Turnier"
                )
            tournament = self.service.create_tournament(dialog.tournament_name(), replace=True)
            options = dialog.settings()
            self.service.update_tournament_details(
                organizer=str(options["organizer"]), location=str(options["location"]),
                table_count=int(options["table_count"]),
            )
            first_sizes = tuple(options.get("first_group_sizes") or ())
            first_group_count = len(first_sizes) if first_sizes else int(options["group_count"])
            intermediate_sizes = tuple(options.get("intermediate_sizes") or ())
            structure = self.service.configure_tournament_structure(
                first_group_count=first_group_count,
                first_qualifiers_per_group=int(options["qualifiers"]),
                use_second_group_stage=bool(options["intermediate"]),
                second_group_count=(len(intermediate_sizes) if intermediate_sizes else max(1, min(26, first_group_count))),
                second_qualifiers_per_group=int(options.get("intermediate_qualifiers", 2)), use_final_round=bool(options["final_round"]),
                replace_existing=True,
            )
            first_phase = self.service.require_tournament().phase(structure.first_phase_id)
            if first_sizes and len(first_phase.groups) == len(first_sizes):
                for group, size in zip(first_phase.groups, first_sizes):
                    group.target_capacity = int(size)
            if options.get("qualifier_mode") == "top2_playoff":
                for group in first_phase.groups:
                    group.set_qualification_playoff((group.target_capacity or len(group.participant_ids) or 4) >= 4)
            participant_names = dialog.participant_names()
            for full_name in participant_names:
                parts = full_name.split()
                first_name = parts[0]; last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
                self.service.add_player(first_name, last_name)
            if participant_names:
                self.service.auto_distribute_players(
                    structure.first_phase_id,
                    strategy=str(options["distribution_strategy"]),
                )
            if bool(options["intermediate"]) and intermediate_sizes:
                self.service.configure_variable_intermediate_round(
                    structure.first_phase_id, intermediate_sizes,
                    qualifiers_per_group=int(options.get("intermediate_qualifiers", 2)),
                )
            # Store the user's preference now; availability is evaluated after the automatic distribution.
            tournament = self.service.require_tournament()
            tournament.qualification_enabled = bool(options["qualification"])
            tournament.intermediate_enabled = bool(options["intermediate"])
            self.service.save()
        except (ValidationError, OSError) as error:
            QMessageBox.critical(self, "MSBTS", str(error))
            return

        self.tournament = self.service.require_tournament()
        self.show_tournament()
        tournament_name = self.tournament.name
        participant_count = len(self.tournament.people)
        message = f'Neues Turnier „{tournament_name}“ wurde angelegt.'
        if participant_count:
            message += f" {participant_count} Teilnehmer wurden automatisch auf die Gruppen verteilt."
        if backup_path is not None:
            message += f" Sicherung: {backup_path.name}"
        self.statusBar().showMessage(message, 7000)
        if participant_count:
            GroupPreviewDialog(self.service, structure.first_phase_id, self).exec()
            self.open_phases()

    def confirm_delete_tournament(self) -> None:
        if self.tournament is None:
            return
        name = self.tournament.name or "Aktuelles Turnier"
        answer = QMessageBox.warning(
            self,
            "Turnier löschen",
            f'Das Turnier „{name}“ mit allen Teilnehmern, Gruppen, Spielen und Ergebnissen wirklich löschen?\n\nDiese Aktion kann nicht rückgängig gemacht werden.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.repository.delete()
            self.service.tournament = None
            self.tournament = None
        except OSError as error:
            QMessageBox.critical(self, "MSBTS", str(error))
            return
        self.show_start_page()
        self.statusBar().showMessage(f'Turnier „{name}“ wurde gelöscht.', 7000)

    def saved(self) -> None:
        self.tournament = self.service.tournament
        if hasattr(self, "dashboard_page"):
            self.dashboard_page.tournament = self.tournament
            self.dashboard_page.refresh()
        if hasattr(self, "workflow_page"):
            self.workflow_page.refresh()
        self._refresh_workflow_quick_actions()
        self.statusBar().showMessage("Gespeichert", 2500)

    def update_status(self, delayed: bool = False) -> None:
        if delayed:
            return
        if self.tournament is None:
            self.statusBar().showMessage("Kein Turnier geladen")
        else:
            if self.sidebar_status_title is not None:
                self.sidebar_status_title.setText("●  Turnier läuft")
            if self.sidebar_status_detail is not None:
                self.sidebar_status_detail.setText("Automatisch gespeichert")
            self._refresh_optional_progress_visibility()
            self._refresh_workflow_quick_actions()
            try:
                qualification_active, intermediate_active = self.service.effective_optional_phases()
            except ValidationError:
                qualification_active, intermediate_active = False, False
            route = ["Gruppen"]
            if qualification_active:
                route.append("Qualifikation")
            if intermediate_active:
                route.append("Zwischenrunde")
            route.append("K.-o.")
            self.statusBar().showMessage(self._workflow_route_text() + " · automatisch gespeichert")

    def open_workflow(self) -> None:
        if hasattr(self, "workflow_page"):
            self.workflow_page.activate()
            self._show_page(self.workflow_page)
            self._select_nav("workflow")

    def _first_group_phase(self):
        if self.tournament is None:
            return None
        phases = [
            phase for phase in self.tournament.phases
            if phase.phase_type is PhaseType.GROUP_STAGE
        ]
        return sorted(phases, key=lambda phase: phase.position)[0] if phases else None

    def start_tournament_workflow(self) -> None:
        """Generate the first playable schedule and open result entry."""
        phase = self._first_group_phase()
        if phase is None:
            show_error(self, ValidationError(
                "Bitte zuerst unter „Gruppen und Phasen“ eine Gruppenphase anlegen."
            ))
            return

        playable = [group for group in phase.groups if len(group.participant_ids) >= 2]
        if not playable:
            show_error(self, ValidationError(
                "Bitte zuerst mindestens zwei Spieler einer Gruppe zuordnen."
            ))
            return

        existing_matches = sum(len(group.matches) for group in playable)
        if existing_matches == 0:
            try:
                qualification_active, intermediate_active = self.service.effective_optional_phases()
            except ValidationError:
                qualification_active, intermediate_active = False, False

            start_check = TournamentStartCheckDialog(
                self.service.require_tournament(),
                phase,
                qualification_active=qualification_active,
                intermediate_active=intermediate_active,
                parent=self,
            )
            if start_check.exec() != QDialog.DialogCode.Accepted:
                return

            try:
                tournament = self.service.require_tournament()
                planned = self.service.generate_phase_schedule(
                    phase.id,
                    tables=tournament.table_count,
                    start_time=tournament.schedule_start_time,
                    duration_minutes=tournament.match_duration_minutes,
                    strategy="smart",
                    replace=False,
                )
                self.saved()
                self.statusBar().showMessage(
                    f"Turnier gestartet · {len(planned)} Spiele erstellt · Leitstand wird geöffnet", 6000
                )
            except ValidationError as error:
                show_error(self, error)
                return
            except Exception as error:
                QMessageBox.critical(self, "Turnier konnte nicht gestartet werden", str(error))
                return
        else:
            self.statusBar().showMessage(
                f"Turnier fortsetzen · {existing_matches} Spiele vorhanden", 5000
            )
        self.open_live_center()

    def open_results_entry(self) -> None:
        """Open the detailed group table as a secondary result-entry view."""
        self.open_phases()
        phase = self._first_group_phase()
        if phase is None:
            return
        index = self.phase_page.phase_selector.findData(phase.id)
        if index >= 0:
            self.phase_page.phase_selector.setCurrentIndex(index)
        playable_group = next((group for group in phase.groups if group.matches), None)
        if playable_group is not None:
            group_index = self.phase_page.group_selector.findData(playable_group.id)
            if group_index >= 0:
                self.phase_page.group_selector.setCurrentIndex(group_index)
        self.phase_page.game_tabs.setCurrentIndex(0)

    def _group_phase_by_position(self, position: int):
        if self.tournament is None:
            return None
        phases = sorted(
            (phase for phase in self.tournament.phases if phase.phase_type is PhaseType.GROUP_STAGE),
            key=lambda phase: phase.position,
        )
        return phases[position - 1] if 0 < position <= len(phases) else None

    def _final_phase(self):
        if self.tournament is None:
            return None
        return next(
            (phase for phase in sorted(self.tournament.phases, key=lambda value: value.position)
             if phase.phase_type is PhaseType.FINAL_ROUND),
            None,
        )

    def _open_phase_editor(self, phase) -> None:
        self.open_phases()
        if phase is None:
            return
        index = self.phase_page.phase_selector.findData(phase.id)
        if index >= 0:
            self.phase_page.phase_selector.setCurrentIndex(index)

    def open_dashboard_group_stage(self) -> None:
        self._open_phase_editor(self._group_phase_by_position(1))
        self._set_progress_step(0)

    def open_dashboard_qualification(self) -> None:
        phase = self._group_phase_by_position(1)
        if phase is None:
            QMessageBox.information(self, "Qualifikation", "Die erste Gruppenphase wurde noch nicht angelegt.")
            self.open_designer()
            return
        self._open_phase_editor(phase)
        if hasattr(self.phase_page, "game_tabs") and hasattr(self.phase_page, "qualification_tab_index"):
            self.phase_page.game_tabs.setCurrentIndex(self.phase_page.qualification_tab_index)
        self._set_progress_step(1)

    def open_dashboard_intermediate(self) -> None:
        phase = self._group_phase_by_position(2)
        if phase is None:
            QMessageBox.information(self, "Zwischenrunde", "Die Zwischenrunde wurde noch nicht angelegt.")
            self.open_designer()
            return
        self._open_phase_editor(phase)
        self._set_progress_step(2)

    def open_dashboard_ko_round(self, round_number: int) -> None:
        phase = self._final_phase()
        if phase is None:
            QMessageBox.information(self, "K.-o.-Phase", "Die K.-o.-Phase wurde noch nicht angelegt.")
            self.open_designer()
            return
        if not phase.matches:
            self._open_phase_editor(phase)
            QMessageBox.information(
                self,
                "K.-o.-Phase vorbereiten",
                "Lege zuerst die acht Teilnehmer und die Viertelfinal-Begegnungen fest.",
            )
            return
        available_rounds = {match.round_number for match in phase.matches}
        if round_number not in available_rounds:
            names = {1: "Viertelfinale", 2: "Halbfinale", 3: "Finale"}
            QMessageBox.information(
                self, names.get(round_number, "K.-o.-Runde"),
                f"{names.get(round_number, 'Diese Runde')} ist noch nicht freigeschaltet. "
                "Schließe zuerst die vorherige Runde ab.",
            )
            return
        self.open_live_center(phase.id)
        self._set_progress_step(min(5, round_number + 2))

    def open_phases(self) -> None:
        self.phase_page.activate()
        self._show_page(self.phase_page)
        self._select_nav("phases")
        self._set_progress_step(0)

    def open_competition(self) -> None:
        self.competition_page.activate()
        if self.competition_page.competition is not None:
            self._show_page(self.competition_page)
            self._select_nav("competition")
            # Do not call _set_progress_step() here: the progress header describes
            # tournament phases (Gruppenphase/Qualifikation/Zwischenrunde/K.-o.),
            # not the workspace navigation step "Spielplan".

    def open_scorekeepers(self) -> None:
        self.scorekeeper_page.refresh()
        self._show_page(self.scorekeeper_page)
        self._select_nav("scorekeepers")

    def open_players(self) -> None:
        self.player_page.refresh()
        self._show_page(self.player_page)
        self._select_nav("players")
        self._set_progress_step(0)

    def open_live_center(self, phase_id=None) -> None:
        # Ein vollständig beendeter erster Gruppenspielplan darf nicht in
        # einer Sackgasse enden. Beim Öffnen des Turniertags werden fehlende
        # Freudenholm-Übergänge und die vier Spiele 3. gegen 4. automatisch
        # erzeugt. Bestehende Ergebnisse und Zuordnungen bleiben erhalten.
        try:
            first = self._first_group_phase()
            if first is not None and len(first.groups) == 5:
                self.service.ensure_freudenholm_flow()
                tournament = self.service.require_tournament()
                first = tournament.phase(first.id)

            # Eine angelegte und mit mindestens zwei Spielern besetzte Gruppe
            # soll im Leitstand sofort Begegnungen zeigen. Leere Reservegruppen
            # (zum Beispiel B-E bei einem kleinen Testturnier) werden bewusst
            # ignoriert und blockieren den Spielbetrieb nicht.
            active_phase = first
            if phase_id is not None and not isinstance(phase_id, bool):
                try:
                    candidate = self.service.require_tournament().phase(phase_id)
                    if candidate.phase_type is PhaseType.GROUP_STAGE:
                        active_phase = candidate
                except ValidationError:
                    pass
            if active_phase is not None:
                playable = [group for group in active_phase.groups if len(group.participant_ids) >= 2]

                # Startfix 89: Older projects may contain schedules created by the
                # former "fast" planner, which could place the same player on
                # two tables at the same time. Repair such a schedule automatically
                # as long as the phase has not recorded results yet.
                existing_matches = [match for group in playable for match in group.matches]
                if existing_matches:
                    quality = self.service.phase_schedule_quality(active_phase.id)
                    has_results = any(match.result is not None for match in existing_matches)
                    if quality.get("simultaneous_conflicts", 0) and not has_results:
                        tournament = self.service.require_tournament()
                        scheduled_times = sorted(
                            match.scheduled_time for match in existing_matches if match.scheduled_time
                        )
                        start_time = scheduled_times[0] if scheduled_times else tournament.schedule_start_time
                        self.service.generate_phase_schedule(
                            active_phase.id,
                            tables=tournament.table_count,
                            start_time=start_time,
                            duration_minutes=tournament.match_duration_minutes,
                            replace=False,
                            strategy="smart",
                        )
                        self.tournament = self.service.require_tournament()
                        active_phase = self.tournament.phase(active_phase.id)
                        playable = [group for group in active_phase.groups if len(group.participant_ids) >= 2]
                        self.saved()

                if playable and any(not group.matches for group in playable):
                    tournament = self.service.require_tournament()
                    self.service.generate_phase_schedule(
                        active_phase.id,
                        tables=tournament.table_count,
                        start_time=tournament.schedule_start_time,
                        duration_minutes=tournament.match_duration_minutes,
                        replace=False,
                        strategy="smart",
                    )
                    self.tournament = self.service.require_tournament()
                    active_phase = self.tournament.phase(active_phase.id)
                    self.saved()

                qualifying = [
                    group for group in active_phase.groups
                    if group.qualification_playoff_applicable
                ]
                if qualifying and all(group.is_finished for group in playable):
                    self.service.prepare_qualification_playoffs(active_phase.id)
                    self.tournament = self.service.require_tournament()
                    self.saved()
        except ValidationError as error:
            show_error(self, error)
        # Wird der Turniertag aus einer konkreten Phase geöffnet, muss genau
        # diese Phase angezeigt werden. Dadurch kann die alte Vorrunde die
        # gerade gestartete Zwischenrunde nicht mehr übersteuern.
        if phase_id is not None and not isinstance(phase_id, bool):
            self.live_center_page.set_preferred_phase(phase_id)
        self.live_center_page.refresh()
        self._show_page(self.live_center_page)
        self._select_nav("live")
        tournament = self.service.require_tournament()
        phase_name, phase_index, _ = self.live_center_page._phase_state(tournament)
        self._set_progress_step(phase_index)

    def open_designer(self) -> None:
        self.designer_page.activate()
        self._show_page(self.designer_page)
        self._select_nav("designer")
        self._set_progress_step(1)

    def continue_tournament_flow(self) -> None:
        """Guide the user through exactly one logical tournament step."""
        self.phase_page.activate()
        tournament = self.service.require_tournament()
        phases = sorted(tournament.phases, key=lambda item: item.position)
        group_phases = [phase for phase in phases if phase.phase_type is PhaseType.GROUP_STAGE]
        first = group_phases[0] if group_phases else None
        second = group_phases[1] if len(group_phases) > 1 else None

        selected = first
        first_playable = [group for group in first.groups if len(group.participant_ids) >= 2] if first is not None else []
        if first_playable and all(group.is_finished for group in first_playable):
            qualification_pending = any(
                group.qualification_playoff_applicable and
                (group.playoff_match is None or group.playoff_match.result is None)
                for group in first.groups
            )
            selected = first if qualification_pending else (second or first)
        if second is not None and second.groups and all(group.is_finished for group in second.groups):
            selected = next((phase for phase in phases if phase.phase_type is PhaseType.FINAL_ROUND), second)

        if selected is None:
            show_error(self, ValidationError("Es ist noch keine Turnierphase eingerichtet."))
            return
        index = self.phase_page.phase_selector.findData(selected.id)
        if index >= 0:
            self.phase_page.phase_selector.setCurrentIndex(index)
        self.phase_page.continue_tournament_day()
        self.saved()
        self.open_live_center()

    def continue_from_designer(self) -> None:
        """Run the same verified phase progression directly from the designer."""
        self.phase_page.activate()
        tournament = self.service.require_tournament()
        phases = sorted(tournament.phases, key=lambda item: item.position)

        # Pick the phase that currently contains active work. This avoids the
        # designer depending on whatever phase happened to be selected earlier.
        selected = None
        for phase in phases:
            if phase.phase_type is PhaseType.GROUP_STAGE:
                target_filled = False
                if phase.next_phase_id is not None:
                    target = tournament.phase(phase.next_phase_id)
                    if target.phase_type is PhaseType.GROUP_STAGE:
                        target_filled = any(group.participant_ids for group in target.groups)
                    else:
                        target_filled = bool(target.participant_ids)
                if not target_filled:
                    selected = phase
                    break
            elif phase.phase_type is PhaseType.FINAL_ROUND:
                selected = phase
                break
        if selected is None and phases:
            selected = phases[-1]
        if selected is None:
            show_error(self, ValidationError("Es ist noch keine Turnierphase eingerichtet."))
            return

        index = self.phase_page.phase_selector.findData(selected.id)
        if index >= 0:
            self.phase_page.phase_selector.setCurrentIndex(index)
        self.phase_page.continue_tournament_day()
        self.saved()
        self.designer_page.refresh()


    def repair_tournament_safely(self) -> None:
        if self.tournament is None:
            return
        answer = QMessageBox.question(
            self,
            "Turnierdaten sicher reparieren",
            "MSTTS erstellt zuerst eine Sicherung. Danach werden nur eindeutig veraltete "
            "Teilnehmer-Referenzen und noch ergebnislose ungültige Spiele entfernt. "
            "Spiele mit bereits eingetragenem Ergebnis werden niemals automatisch gelöscht.\n\n"
            "Sichere Reparatur jetzt ausführen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.service.repair_safe_tournament_references()
            self.tournament = self.service.require_tournament()
            self.saved()
            repaired = result["groups"] + result["phases"] + result["registrations"] + result["matches"]
            restored = result.get("restored_people", 0)
            blocked = result["blocked_results"]
            message = f"{repaired} sichere Korrektur(en) durchgeführt."
            if restored:
                message += f" {restored} fehlende Teilnehmer wurden zur Erhaltung vorhandener Ergebnisse als Archiv-Einträge wiederhergestellt."
            if blocked:
                message += f" {blocked} Fall/Fälle konnten nicht automatisch bereinigt werden."
            QMessageBox.information(self, "Turnierreparatur abgeschlossen", message)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Turnierreparatur", str(error))

    def export_tournament_json(self) -> None:
        if self.tournament is None:
            show_error(self, ValidationError("Es ist kein Turnier geladen.")); return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Turnierdatei speichern", f"{self.tournament.name}.fts",
            "FTS-Projektdateien (*.fts);;Legacy-FTS-Dateien (*.fts.json);;JSON-Dateien (*.json)"
        )
        if not filename:
            return
        try:
            export_path = Path(filename)
            self.service.export_tournament_json(export_path)
            self._remember_recent_tournament(export_path)
            self.statusBar().showMessage("Turnierdatei gespeichert", 3000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Exportfehler", str(error))

    def import_tournament_json(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Turnierdatei öffnen", "",
            "FTS-Projektdateien (*.fts);;Legacy-FTS-Dateien (*.fts.json *.json);;Alle Dateien (*)"
        )
        if not filename:
            return
        if self.tournament is not None:
            answer = QMessageBox.warning(
                self, "Turnierdatei öffnen",
                "Das aktuelle Turnier wird ersetzt. Fortfahren?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            import_path = Path(filename)
            self.tournament = self.service.import_tournament_json(import_path)
            self._remember_recent_tournament(import_path)
            self.show_tournament()
            self.statusBar().showMessage("Turnierdatei geöffnet", 3000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Importfehler", str(error))

    def export_tournament_archive(self) -> None:
        if self.tournament is None:
            show_error(self, ValidationError("Es ist kein Turnier geladen.")); return
        filename, _ = QFileDialog.getSaveFileName(self, "Turnierarchiv erstellen", "fts-turnierarchiv.zip", "ZIP-Archive (*.zip)")
        if not filename:
            return
        try:
            path = Path(filename)
            self.service.export_tournament_archive(path)
            self.statusBar().showMessage(f"Turnierarchiv erstellt: {path.name}", 5000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Archivfehler", str(error))

    def import_players_csv(self) -> None:
        if self.tournament is None:
            show_error(self, ValidationError("Es ist kein Turnier geladen.")); return
        filename, _ = QFileDialog.getOpenFileName(self, "Spieler importieren", "", "CSV-Dateien (*.csv);;Alle Dateien (*)")
        if not filename:
            return
        try:
            preview = self.service.preview_player_import(Path(filename))
            message = f"{len(preview.players)} Spieler werden importiert."
            if preview.duplicates:
                message += f"\n{len(preview.duplicates)} Dublette(n) werden übersprungen."
            if preview.invalid_rows:
                message += f"\n{len(preview.invalid_rows)} ungültige Zeile(n) werden übersprungen."
            if not preview.players:
                QMessageBox.information(self, "CSV-Import", message); return
            if QMessageBox.question(self, "CSV-Import", message + "\n\nImport starten?") != QMessageBox.StandardButton.Yes:
                return
            self.service.import_players_csv(Path(filename))
            self.tournament = self.service.tournament
            self.show_tournament()
            self.statusBar().showMessage("Spieler importiert", 3000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Importfehler", str(error))

    def create_backup(self) -> None:
        if self.tournament is None:
            show_error(self, ValidationError("Es ist kein Turnier geladen.")); return
        default_backup = self.service._database_path().parent / "backups" / "fts-backup.db"
        filename, _ = QFileDialog.getSaveFileName(
            self, "Sicherung erstellen", str(default_backup), "FTS-Sicherung (*.db)"
        )
        if not filename:
            return
        try:
            path = self.service.create_backup(Path(filename))
            self.statusBar().showMessage(f"Sicherung erstellt: {path.name}", 4000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Sicherungsfehler", str(error))

    def create_automatic_backup(self) -> None:
        if self.tournament is None:
            show_error(self, ValidationError("Es ist kein Turnier geladen.")); return
        try:
            path = self.service.create_automatic_backup(retention=10)
            self.statusBar().showMessage(f"Automatische Sicherung erstellt: {path.name}", 4000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Sicherungsfehler", str(error))

    def restore_backup(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Sicherung wiederherstellen", "", "FTS-Sicherung (*.db);;Alle Dateien (*)")
        if not filename:
            return
        if QMessageBox.warning(self, "Sicherung wiederherstellen", "Das aktuelle Turnier wird durch die Sicherung ersetzt. Fortfahren?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.tournament = self.service.restore_backup(Path(filename))
            self.show_tournament()
            self.statusBar().showMessage("Sicherung wiederhergestellt", 4000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Wiederherstellungsfehler", str(error))

    def export_players_csv(self) -> None:
        if self.tournament is None:
            show_error(self, ValidationError("Es ist kein Turnier geladen."))
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Spielerliste exportieren",
            "spieler.csv",
            "CSV-Dateien (*.csv)",
        )
        if not filename:
            return
        try:
            self.service.export_players_csv(Path(filename))
            self.statusBar().showMessage("Spielerliste exportiert", 3000)
        except OSError as error:
            QMessageBox.critical(self, "Exportfehler", str(error))

    def _selected_competition(self) -> Competition | None:
        page = getattr(self, "competition_page", None)
        if page is not None and page.competition is not None:
            return page.competition
        if self.tournament and self.tournament.competitions:
            return self.tournament.competitions[0]
        return None

    def export_competition_csv(self) -> None:
        competition = self._selected_competition()
        if competition is None:
            show_error(self, ValidationError("Es ist noch kein Wettbewerb vorhanden."))
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Wettbewerb exportieren",
            f"{competition.name}.csv",
            "CSV-Dateien (*.csv)",
        )
        if not filename:
            return
        try:
            self.service.export_competition_csv(competition.id, Path(filename))
            self.statusBar().showMessage("Wettbewerb exportiert", 3000)
        except OSError as error:
            QMessageBox.critical(self, "Exportfehler", str(error))

    def export_players_html(self) -> None:
        if self.tournament is None:
            show_error(self, ValidationError("Es ist kein Turnier geladen.")); return
        filename, _ = QFileDialog.getSaveFileName(self, "Teilnehmerliste als PDF exportieren", "teilnehmerliste.pdf", "PDF-Dateien (*.pdf)")
        if not filename:
            return
        try:
            path = Path(filename)
            self.service.export_players_html(path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
            self.statusBar().showMessage("PDF erstellt", 3000)
        except OSError as error:
            QMessageBox.critical(self, "Exportfehler", str(error))

    def export_competition_html(self) -> None:
        competition = self._selected_competition()
        if competition is None:
            show_error(self, ValidationError("Es ist noch kein Wettbewerb vorhanden.")); return
        filename, _ = QFileDialog.getSaveFileName(self, "Wettbewerb als PDF exportieren", f"{competition.name}.pdf", "PDF-Dateien (*.pdf)")
        if not filename:
            return
        try:
            path = Path(filename)
            self.service.export_competition_html(competition.id, path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
            self.statusBar().showMessage("PDF erstellt", 3000)
        except OSError as error:
            QMessageBox.critical(self, "Exportfehler", str(error))

    def export_certificates_html(self) -> None:
        competition = self._selected_competition()
        if competition is None:
            show_error(self, ValidationError("Es ist noch kein Wettbewerb vorhanden.")); return
        filename, _ = QFileDialog.getSaveFileName(self, "Urkunden als PDF erstellen", f"Urkunden-{competition.name}.pdf", "PDF-Dateien (*.pdf)")
        if not filename:
            return
        try:
            path = Path(filename)
            self.service.export_certificates_html(competition.id, path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
            self.statusBar().showMessage("Urkunden-PDF erstellt", 3000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Exportfehler", str(error))

    def export_medal_table_html(self) -> None:
        if self.tournament is None:
            show_error(self, ValidationError("Es ist kein Turnier geladen.")); return
        filename, _ = QFileDialog.getSaveFileName(self, "Medaillenspiegel als PDF exportieren", "medaillenspiegel.pdf", "PDF-Dateien (*.pdf)")
        if not filename:
            return
        try:
            path = Path(filename)
            self.service.export_medal_table_html(path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
            self.statusBar().showMessage("Medaillenspiegel-PDF erstellt", 3000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Exportfehler", str(error))

    def export_live_html(self) -> None:
        if self.tournament is None:
            show_error(self, ValidationError("Es ist kein Turnier geladen.")); return
        interval, accepted = QInputDialog.getInt(self, "Live-Ansicht", "Aktualisierung in Sekunden", 15, 5, 3600)
        if not accepted:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Live-Ansicht erstellen", "fts-live.html", "HTML-Dateien (*.html)")
        if not filename:
            return
        try:
            path = Path(filename)
            self.service.export_live_html(path, interval)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
            self.statusBar().showMessage("Live-Ansicht erstellt", 3000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Exportfehler", str(error))

    def export_validation_html(self) -> None:
        if self.tournament is None:
            show_error(self, ValidationError("Es ist kein Turnier geladen.")); return
        filename, _ = QFileDialog.getSaveFileName(self, "Turnierprüfung als PDF exportieren", "turnierpruefung.pdf", "PDF-Dateien (*.pdf)")
        if not filename:
            return
        try:
            path = Path(filename)
            self.service.export_validation_html(path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
            issues = self.service.validate_tournament()
            errors = sum(issue.severity == "Fehler" for issue in issues)
            warnings = sum(issue.severity == "Warnung" for issue in issues)
            self.statusBar().showMessage(f"Turnierprüfung: {errors} Fehler, {warnings} Warnungen", 5000)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Prüffehler", str(error))

    def show_about(self) -> None:
        QMessageBox.about(self, "Über MSBTS", "M. Seemann Badminton Tournament System\nVersion 1.0 Alpha 6\nProfessionelle Badminton-Turnierverwaltung")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.tournament is not None:
            try:
                self.service.save()
            except OSError as error:
                answer = QMessageBox.critical(
                    self,
                    "Speicherfehler",
                    f"Das Turnier konnte nicht gespeichert werden:\n{error}\n\nTrotzdem beenden?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore(); return
        event.accept()
