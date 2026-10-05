from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from fts.engine import TournamentEngine
from fts.gui.presentation_model import build_presentation_snapshot


TABLE_THEMES = {
    1: {"main": "#673AB7", "dark": "#4A258C", "soft": "#EEE7FA", "border": "#8E6CCC"},
    2: {"main": "#B86731", "dark": "#8A4824", "soft": "#F8E9DF", "border": "#D28A5B"},
    3: {"main": "#2F8A4F", "dark": "#23683C", "soft": "#E3F2E8", "border": "#58A870"},
}


class PresentationWindow(QWidget):
    """Read-only, self-refreshing fullscreen view for spectators and beamers."""

    def __init__(self, service: TournamentEngine, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.service = service
        self.setWindowTitle("MSBTS – Präsentationsmodus")
        self.setMinimumSize(900, 600)
        self.setStyleSheet("""
            QWidget { background: transparent; color: #F8F3FF; font-family: Arial; }
            QFrame#presentationCard { background: rgba(19,7,38,0.94); border: 1px solid #7D35C8; border-radius: 13px; }
            QFrame#matchRow { background: rgba(255,255,255,0.035); border: 1px solid #63309F; border-radius: 9px; }
            QFrame#championCard { background: #1A0A34; border: 2px solid #A447F4; border-radius: 18px; }
            QFrame#podiumCard { background: #140827; border: 1px solid #7337B4; border-radius: 14px; }
            QLabel#muted { color: #CDBBE8; }
            QProgressBar { border: 1px solid #8147C0; border-radius: 8px; text-align: center; background: #0E061B; color: #FFFFFF; min-height: 20px; font-weight: 900; }
            QProgressBar::chunk { background: #8D21F5; border-radius: 7px; }
            QPushButton { background: #5F20A6; color: #FFFFFF; border: 1px solid #9B45E7; border-radius: 9px; padding: 8px 14px; font-weight: 900; }
            QPushButton:hover { background: #8129DA; }
        """)

        self.logo = QLabel()
        logo_path = Path(__file__).resolve().parent / "assets" / "msbts_logo.png"
        if logo_path.exists():
            pix = QPixmap(str(logo_path))
            self.logo.setPixmap(pix.scaled(82, 82, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.logo.setFixedSize(86, 86)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title = QLabel()
        self.title.setStyleSheet("font-size: 32px; font-weight: 950; color: #FFFFFF;")
        self.subtitle = QLabel("B A D M I N T O N  ·  T U R N I E R")
        self.subtitle.setStyleSheet("font-size: 13px; font-weight: 900; letter-spacing: 1px; color: #DCCBFF;")
        self.slogan = QLabel("Fair Play\nViel Erfolg!")
        self.slogan.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.slogan.setStyleSheet("font-size: 16px; font-weight: 900; font-style: italic; color: #D16CFF;")

        self.date_label = QLabel()
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.date_label.setStyleSheet("font-size: 11px; font-weight: 800; color: #DCCBFF;")
        self.clock = QLabel()
        self.clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock.setStyleSheet("font-size: 23px; font-weight: 900; color: #FFFFFF;")
        self.time_card = QFrame()
        self.time_card.setStyleSheet("background: rgba(31,12,56,0.96); border: 1px solid #8B45D6; border-radius: 12px;")
        time_box = QVBoxLayout(self.time_card)
        time_box.setContentsMargins(11, 5, 11, 5)
        time_box.setSpacing(0)
        time_box.addWidget(self.date_label)
        time_box.addWidget(self.clock)

        self.phase = QLabel()
        self.phase.setStyleSheet("font-size: 17px; font-weight: 900; color: white; background: #7D1FF2; border:1px solid #A84CFF; padding: 7px 14px; border-radius: 8px;")
        # STARTFIX 122: spectator-facing phase banner mirrors the Leitstand.
        self.phase_banner = QFrame()
        self.phase_banner.setObjectName("presentationPhaseBanner")
        self.phase_banner.setMinimumHeight(92)
        self.phase_banner.setStyleSheet("background: rgba(25,8,50,0.96); border: 2px solid #A843F2; border-radius: 16px;")
        phase_banner_layout = QHBoxLayout(self.phase_banner)
        phase_banner_layout.setContentsMargins(16, 7, 16, 7)
        self.phase_banner_icon = QLabel("🏆")
        self.phase_banner_icon.setStyleSheet("font-size: 27px; color: #E45CFF; border: none;")
        phase_banner_layout.addWidget(self.phase_banner_icon)
        phase_banner_text = QVBoxLayout()
        phase_banner_text.setSpacing(0)
        phase_banner_eyebrow = QLabel("AKTUELLE TURNIERPHASE")
        phase_banner_eyebrow.setObjectName("presentationPhaseEyebrow")
        phase_banner_eyebrow.setStyleSheet("font-size: 11px; font-weight: 900; color: #D45DFF; border: none;")
        self.phase_banner_title = QLabel("Gruppenphase")
        self.phase_banner_title.setObjectName("presentationPhaseTitle")
        self.phase_banner_title.setStyleSheet("font-size: 30px; font-weight: 950; color: #F8F3FF; border: none; background: transparent;")
        self.phase_banner_detail = QLabel("Die aktuelle Turnierphase läuft.")
        self.phase_banner_detail.setObjectName("presentationPhaseDetail")
        self.phase_banner_detail.setStyleSheet("font-size: 12px; font-weight: 800; color: #D9C8F3; border: none; background: transparent;")
        phase_banner_text.addWidget(phase_banner_eyebrow)
        phase_banner_text.addWidget(self.phase_banner_title)
        phase_banner_text.addWidget(self.phase_banner_detail)
        phase_banner_layout.addLayout(phase_banner_text, 1)
        self.progress = QProgressBar()
        self.progress_text = QLabel()
        self.progress_text.setObjectName("muted")
        self.progress_text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.progress_text.setStyleSheet("font-size: 12px; color: #E8DCFF; font-weight: 900;")

        self.tables_widget = QWidget()
        self.tables = QGridLayout(self.tables_widget)
        self.tables.setContentsMargins(0, 0, 0, 0)
        self.tables.setSpacing(10)

        self.upcoming = QVBoxLayout()
        self.recent = QVBoxLayout()
        self.finish_area = QVBoxLayout()
        self.finish_widget = QWidget()
        self.finish_widget.setLayout(self.finish_area)
        self.finish_widget.setVisible(False)

        close = QPushButton("Präsentation schließen")
        close.clicked.connect(self.close)

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(self.logo)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title_box.addWidget(self.title)
        title_box.addWidget(self.subtitle)
        header.addLayout(title_box, 1)
        header.addWidget(self.slogan)
        header.addWidget(self.time_card)

        body = QVBoxLayout(self)
        body.setContentsMargins(18, 12, 18, 12)
        body.setSpacing(8)
        body.addLayout(header)
        body.addWidget(self.phase_banner)

        phase_row = QHBoxLayout()
        phase_row.setSpacing(8)
        phase_row.addWidget(self.phase)
        phase_row.addWidget(self.progress, 1)
        phase_row.addWidget(self.progress_text)
        body.addLayout(phase_row)
        body.addWidget(self.tables_widget, 3)

        lower = QHBoxLayout()
        lower.setSpacing(10)
        self.upcoming_section = self._section("ALS NÄCHSTES", self.upcoming, "#4E1B75")
        self.recent_section = self._section("LETZTE ERGEBNISSE", self.recent, "#4E1B75")
        lower.addWidget(self.upcoming_section, 1)
        lower.addWidget(self.recent_section, 1)
        self.lower_widget = QWidget()
        self.lower_widget.setLayout(lower)
        body.addWidget(self.lower_widget, 3)
        body.addWidget(self.finish_widget, 1)

        footer = QHBoxLayout()
        footer.addWidget(QLabel("Starke Spiele. Starke Menschen."))
        footer.addStretch()
        footer_brand = QLabel("MSBTS – BADMINTON TOURNAMENT SYSTEM   ·   FAIR PLAY · RESPEKT · GEMEINSCHAFT")
        footer_brand.setStyleSheet("font-size: 10px; font-weight: 800; color: #CDBBE8;")
        footer.addWidget(footer_brand)
        footer.addWidget(close)
        body.addLayout(footer)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#10071F"))
        logo_path = Path(__file__).resolve().parent / "assets" / "msbts_logo.png"
        pix = QPixmap(str(logo_path)) if logo_path.exists() else QPixmap()
        if not pix.isNull():
            target = int(max(self.width() * 0.68, self.height() * 1.12))
            scaled = pix.scaled(target, target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            x = self.width() - int(scaled.width() * 0.54)
            y = (self.height() - scaled.height()) // 2
            painter.setOpacity(0.15)
            painter.drawPixmap(x, y, scaled)
        painter.setOpacity(0.06)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#8C35D8"), 2))
        diameter = int(max(self.width() * 0.60, self.height() * 1.04))
        cx = self.width() - int(diameter * 0.22)
        cy = self.height() // 2
        painter.drawEllipse(cx - diameter // 2, cy - diameter // 2, diameter, diameter)
        painter.end()

    @staticmethod
    def _theme(table_number: int) -> dict[str, str]:
        return TABLE_THEMES.get(table_number, TABLE_THEMES[1])

    @staticmethod
    def _section(title: str, layout: QVBoxLayout, color: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("presentationCard")
        box = QVBoxLayout(frame)
        box.setContentsMargins(0, 0, 0, 7)
        box.setSpacing(5)
        heading = QLabel(title)
        heading.setStyleSheet(
            f"background: {color}; color: white; border-radius: 10px; "
            "padding: 7px 12px; font-size: 16px; font-weight: 900;"
        )
        box.addWidget(heading)
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        inner.setLayout(layout)
        layout.setContentsMargins(8, 0, 8, 0)
        box.addWidget(inner, 1)
        return frame

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                PresentationWindow._clear(child)

    def _table_card(self, item) -> QFrame:
        theme = self._theme(item.table_number)
        card = QFrame()
        card.setObjectName("tableCard")
        # STARTFIX 85 compatibility marker: setMinimumHeight(175)
        card.setMinimumHeight(158)
        card.setStyleSheet(
            f"QFrame {{ background: #130824; border: 1px solid {theme['border']}; border-radius: 12px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_frame = QFrame()
        top_frame.setStyleSheet(f"background: {theme['main']}; border: none; border-top-left-radius: 13px; border-top-right-radius: 13px;")
        top = QHBoxLayout(top_frame)
        top.setContentsMargins(10, 6, 10, 6)
        title = QLabel(f"●  FELD {item.table_number}")
        title.setStyleSheet("font-size: 18px; font-weight: 900; color: white; border: none;")
        state = QLabel(item.state.upper())
        state.setStyleSheet(
            f"background: {theme['dark']}; color: #FFFFFF; border: 1px solid rgba(255,255,255,0.35); "
            "border-radius: 7px; padding: 4px 8px; font-size: 10px; font-weight: 900;"
        )
        top.addWidget(title)
        top.addStretch()
        top.addWidget(state)
        layout.addWidget(top_frame)

        context = QLabel(f"◷  {item.scheduled_time}   |   {item.phase_name}   ·   {item.group_name}".strip(" ·"))
        context.setStyleSheet(f"background: #160A2B; color: #E4D4FF; padding: 4px 8px; font-size: 11px; font-weight: 800; border: none;")
        context.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(context)

        if item.away_name:
            versus_wrap = QWidget()
            versus_wrap.setStyleSheet("background: #130824; border: none;")
            versus = QHBoxLayout(versus_wrap)
            versus.setContentsMargins(7, 5, 7, 5)
            versus.setSpacing(8)
            home = QLabel(item.home_name)
            away = QLabel(item.away_name)
            for player in (home, away):
                player.setAlignment(Qt.AlignmentFlag.AlignCenter)
                player.setWordWrap(True)
                player.setStyleSheet(
                    "background: rgba(255,255,255,0.045); border: 1px solid #5D3587; border-radius: 8px; "
                    "padding: 8px 6px; font-size: 16px; font-weight: 900; color: #FFFFFF;"
                )
            against = QLabel("VS")
            against.setAlignment(Qt.AlignmentFlag.AlignCenter)
            against.setFixedWidth(38)
            against.setStyleSheet(
                f"background: {theme['main']}; color: white; border-radius: 20px; "
                "font-size: 11px; font-weight: 900; padding: 6px;"
            )
            versus.addWidget(home, 1)
            versus.addWidget(against)
            versus.addWidget(away, 1)
            layout.addWidget(versus_wrap, 1)

            scorer = QLabel(f"●  Schiedsrichter: {item.scorer_name or 'noch offen'}")
            scorer.setAlignment(Qt.AlignmentFlag.AlignCenter)
            scorer.setStyleSheet(
                f"background: rgba(245,239,255,0.94); color: {theme['dark']}; border: none; "
                "padding: 5px 7px; font-size: 10px; font-weight: 900; "
                "border-bottom-left-radius: 13px; border-bottom-right-radius: 13px;"
            )
            layout.addWidget(scorer)
        else:
            free = QLabel(item.home_name or "Feld frei")
            free.setAlignment(Qt.AlignmentFlag.AlignCenter)
            free.setStyleSheet("font-size: 18px; font-weight: 900; color:#FFFFFF; padding: 14px; border: none;")
            layout.addWidget(free, 1)

        if item.result_text:
            result = QLabel(item.result_text)
            result.setAlignment(Qt.AlignmentFlag.AlignCenter)
            result.setStyleSheet(f"font-size: 24px; font-weight: 900; color: {theme['dark']}; border: none;")
            layout.addWidget(result)
        return card

    def _match_row(self, item, show_result: bool) -> QFrame:
        theme = self._theme(item.table_number or 1)
        row = QFrame()
        row.setObjectName("matchRow")
        # STARTFIX 85 compatibility marker: setMinimumHeight(62)
        row.setMinimumHeight(48)
        row.setMaximumHeight(60)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(10)

        meta = QLabel(f"{item.scheduled_time or '–'}\nFeld {item.table_number or '–'}")
        meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta.setFixedWidth(74)
        meta.setStyleSheet(
            f"background: {theme['main']}; color: white; border-radius: 8px; "
            "padding: 4px; font-size: 10px; font-weight: 900;"
        )
        pairing_box = QVBoxLayout()
        pairing_box.setSpacing(1)
        pairing = QLabel(f"{item.home_name}  –  {item.away_name}")
        pairing.setWordWrap(False)
        pairing.setStyleSheet("font-size: 13px; font-weight: 900; color: #FFFFFF;")
        detail_parts = [x for x in (item.phase_name, item.group_name) if x]
        if item.scorer_name and not show_result:
            detail_parts.append(f"Schiedsrichter: {item.scorer_name}")
        detail = QLabel(" · ".join(detail_parts))
        detail.setStyleSheet("font-size: 9px; color: #CDBBE8; font-weight: 800;")
        pairing_box.addWidget(pairing)
        pairing_box.addWidget(detail)

        layout.addWidget(meta)
        layout.addLayout(pairing_box, 1)
        if show_result:
            result = QLabel(item.result_text or "–")
            result.setAlignment(Qt.AlignmentFlag.AlignCenter)
            result.setFixedWidth(54)
            result.setStyleSheet(
                "background: #6F24B9; color: white; border-radius: 7px; "
                "padding: 5px; font-size: 16px; font-weight: 900;"
            )
            layout.addWidget(result)
        else:
            arrow = QLabel("›")
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            arrow.setStyleSheet(f"font-size: 25px; font-weight: 900; color: {theme['dark']};")
            layout.addWidget(arrow)
        return row

    @staticmethod
    def _podium_card(place: int, name: str, emphasized: bool = False) -> QFrame:
        card = QFrame()
        card.setObjectName("championCard" if emphasized else "podiumCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 16, 20, 16)
        medal = {1: "#FFD15A", 2: "#D8DDE3", 3: "#D58A52"}.get(place, "#F8F3FF")
        border = {1: "#E6A72A", 2: "#AEB7C2", 3: "#A96335"}.get(place, "#7337B4")
        card.setStyleSheet(
            f"background:#160A2B; border:{'2px' if emphasized else '1px'} solid {border}; border-radius:{'18px' if emphasized else '14px'};"
        )
        label = QLabel(f"{place}. PLATZ")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(f"font-size: 20px; font-weight: 950; color: {medal}; background:transparent; border:none;")
        player = QLabel(name or "–")
        player.setAlignment(Qt.AlignmentFlag.AlignCenter)
        player.setWordWrap(True)
        player.setStyleSheet(
            f"font-size: {'38px' if emphasized else '34px'}; font-weight: 950; color: {medal}; "
            "background:transparent; border:none;"
        )
        box.addWidget(label)
        box.addWidget(player, 1)
        if emphasized:
            subtitle = QLabel("TURNIERSIEGER/IN")
            subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            subtitle.setStyleSheet(f"font-size: 15px; font-weight: 950; color: {medal}; background:transparent; border:none;")
            box.addWidget(subtitle)
        return card

    def _render_finished(self, snapshot) -> None:
        self._clear(self.finish_area)
        banner = QLabel("TURNIER BEENDET · HERZLICHEN GLÜCKWUNSCH!")
        banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner.setStyleSheet("background: #3D1E58; color: white; border-radius: 14px; padding: 12px; font-size: 24px; font-weight: 900;")
        self.finish_area.addWidget(banner)

        first, second, third = snapshot.placements
        podium = QHBoxLayout()
        podium.setSpacing(16)
        podium.addWidget(self._podium_card(2, second), 1)
        podium.addWidget(self._podium_card(1, first, True), 1)
        podium.addWidget(self._podium_card(3, third), 1)
        self.finish_area.addLayout(podium, 2)

        recent_frame = QFrame()
        recent_frame.setObjectName("presentationCard")
        recent_box = QVBoxLayout(recent_frame)
        recent_title = QLabel("LETZTE ERGEBNISSE")
        recent_title.setStyleSheet("font-size: 19px; font-weight: 900; color: #D45DFF;")
        recent_box.addWidget(recent_title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        for index, item in enumerate(snapshot.recent_results[:6]):
            grid.addWidget(self._match_row(item, True), index // 2, index % 2)
        recent_box.addLayout(grid)
        self.finish_area.addWidget(recent_frame, 2)

    def refresh(self) -> None:
        snapshot = build_presentation_snapshot(self.service.require_tournament())
        now = datetime.now()
        self.title.setText(snapshot.tournament_name)
        self.phase.setText(f"Aktuelle Phase: {snapshot.current_phase}")
        phase_key = snapshot.current_phase.casefold()
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
        self.phase_banner_title.setText(snapshot.current_phase)
        self.phase_banner_detail.setText(detail)
        self.phase_banner_icon.setText(icon)
        self.date_label.setText(now.strftime("%d.%m.%Y"))
        self.clock.setText(now.strftime("%H:%M:%S"))
        self.progress.setValue(snapshot.progress_percent)
        self.progress.setFormat(f"{snapshot.progress_percent} %")
        self.progress_text.setText(snapshot.progress_text)
        finished = snapshot.current_phase == "Turnier beendet"

        self.tables_widget.setVisible(not finished)
        self.lower_widget.setVisible(not finished)
        self.finish_widget.setVisible(finished)

        self._clear(self.tables)
        if not finished:
            columns = min(3, max(1, len(snapshot.tables)))
            for index, item in enumerate(snapshot.tables):
                self.tables.addWidget(self._table_card(item), index // columns, index % columns)

            for layout, rows, show_result, empty_text in (
                (self.upcoming, snapshot.upcoming[:4], False, "Keine weiteren Begegnungen geplant."),
                (self.recent, snapshot.recent_results[:4], True, "Noch keine Ergebnisse vorhanden."),
            ):
                self._clear(layout)
                layout.setSpacing(7)
                if not rows:
                    label = QLabel(empty_text)
                    label.setObjectName("muted")
                    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    label.setStyleSheet("font-size: 16px; color: #C9B7E6; padding: 20px; font-style: italic;")
                    layout.addWidget(label, 1)
                else:
                    for item in rows:
                        layout.addWidget(self._match_row(item, show_result))
                    layout.addStretch(1)
        else:
            self._render_finished(snapshot)
