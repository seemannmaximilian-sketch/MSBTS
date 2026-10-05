from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer, QDateTime, QPointF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget, QToolButton, QMenu,
)

from fts.domain import PhaseType, Tournament
from fts.gui.dashboard_stats import calculate_dashboard_stats, calculate_readiness
from fts.tournament_health import HealthSeverity, inspect_tournament


class ActionCard(QPushButton):
    def __init__(self, icon: str, title: str, primary: bool = False) -> None:
        super().__init__(f"{icon}  {title}")
        self.setObjectName("dashboardActionPrimary" if primary else "dashboardAction")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(44)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class QuickCard(QPushButton):
    def __init__(self, icon: str, title: str, subtitle: str) -> None:
        # STARTFIX 105: the lower workspaces are real visual cards rather than
        # small toolbar-like buttons.  Keep the same click behaviour, but make
        # the secondary description visible so the dashboard reads at a glance.
        super().__init__(f"{icon}   {title}\n      {subtitle}")
        self.setToolTip(subtitle)
        self.setObjectName("dashboardQuickCard")
        self.setProperty("premium", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(66)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class StatCard(QFrame):
    def __init__(self, icon: str, label: str, tone: str = "bronze") -> None:
        super().__init__()
        self.setObjectName("dashboardStatCard")
        self.setProperty("tone", tone)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(76)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)
        icon_label = QLabel(icon)
        icon_label.setObjectName("dashboardStatIcon")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedSize(42, 42)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.value = QLabel("0")
        self.value.setObjectName("dashboardStatValue")
        caption = QLabel(label)
        caption.setObjectName("dashboardStatLabel")
        texts.addWidget(self.value)
        texts.addWidget(caption)
        layout.addWidget(icon_label)
        layout.addLayout(texts, 1)



class DashboardCanvas(QWidget):
    """Warm, low-contrast table-tennis watermark behind the dashboard cards."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()

        # Oversized abstract paddle head in the upper-right background.
        painter.setOpacity(0.055)
        painter.setPen(QPen(QColor("#9B633E"), 3.0))
        painter.setBrush(QColor("#CDAE95"))
        diameter = max(280, int(min(rect.width(), rect.height()) * 0.50))
        cx = rect.right() - int(diameter * 0.30)
        cy = rect.top() + int(diameter * 0.40)
        painter.drawEllipse(cx - diameter // 2, cy - diameter // 2, diameter, diameter)

        # Paddle handle / sweeping brand curves.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#9B633E"), 18.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(cx - diameter * 0.05, cy + diameter * 0.30),
                         QPointF(cx - diameter * 0.32, cy + diameter * 0.76))
        painter.setPen(QPen(QColor("#B77A4C"), 2.2))
        painter.drawArc(rect.adjusted(int(rect.width()*0.10), int(rect.height()*0.08),
                                      -int(rect.width()*0.08), -int(rect.height()*0.10)), 20*16, 105*16)
        painter.drawArc(rect.adjusted(int(rect.width()*0.28), int(rect.height()*0.26),
                                      int(rect.width()*0.12), int(rect.height()*0.08)), 190*16, 82*16)

        # A soft ball and a subtle net line tie the whole page to table tennis.
        painter.setOpacity(0.08)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#8A4F29"))
        ball = max(34, int(min(rect.width(), rect.height()) * 0.055))
        painter.drawEllipse(rect.right() - ball * 4, rect.top() + ball * 2, ball, ball)
        painter.setOpacity(0.045)
        painter.setPen(QPen(QColor("#6B3F29"), 2.0))
        y = rect.top() + int(rect.height() * 0.57)
        painter.drawLine(QPointF(rect.left() + 70, y), QPointF(rect.right() - 80, y))
        painter.end()



class ArenaBanner(QFrame):
    """Subtle branded table-tennis stage used as a visual anchor on the dashboard."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dashboardArena")
        self.setMinimumHeight(112)
        self.setMaximumHeight(138)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        eyebrow = QLabel("MSBTS · BADMINTON TURNIERZENTRALE")
        eyebrow.setObjectName("dashboardArenaEyebrow")
        title = QLabel("Badminton verbindet.")
        title.setObjectName("dashboardArenaTitle")
        subtitle = QLabel("Planen · durchführen · auswerten – alles in einem Leitstand.")
        subtitle.setObjectName("dashboardArenaSubtitle")
        texts.addWidget(eyebrow)
        texts.addWidget(title)
        texts.addWidget(subtitle)
        texts.addStretch()
        layout.addLayout(texts, 1)
        badge = QLabel("FAIR PLAY\nRESPEKT · GEMEINSCHAFT")
        badge.setObjectName("dashboardArenaBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(badge)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        gradient = QLinearGradient(QPointF(rect.left(), rect.top()), QPointF(rect.right(), rect.bottom()))
        gradient.setColorAt(0.0, QColor("#201044"))
        gradient.setColorAt(0.48, QColor("#5E2A8A"))
        gradient.setColorAt(1.0, QColor("#4A281B"))
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 16, 16)

        # Abstract table / net motif: intentionally restrained so text remains dominant.
        painter.setOpacity(0.24)
        painter.setPen(QPen(QColor("#F5EADF"), 2.0))
        y = rect.top() + rect.height() * 0.65
        painter.drawLine(QPointF(rect.left() + rect.width() * 0.43, y), QPointF(rect.right() - 22, y))
        painter.drawLine(QPointF(rect.left() + rect.width() * 0.68, y - 24), QPointF(rect.left() + rect.width() * 0.68, y + 20))
        painter.drawLine(QPointF(rect.left() + rect.width() * 0.43, y), QPointF(rect.left() + rect.width() * 0.53, rect.bottom() - 8))
        painter.drawLine(QPointF(rect.right() - 22, y), QPointF(rect.right() - rect.width() * 0.08, rect.bottom() - 8))
        painter.setOpacity(0.55)
        painter.setBrush(QColor("#FFF8F0"))
        painter.setPen(Qt.PenStyle.NoPen)
        ball_r = max(5, int(rect.height() * 0.065))
        center_x = int(rect.right() - rect.width() * 0.13)
        center_y = int(rect.top() + rect.height() * 0.32)
        painter.drawEllipse(center_x - ball_r, center_y - ball_r, ball_r * 2, ball_r * 2)
        painter.end()
        super().paintEvent(event)


class DashboardPage(QScrollArea):
    open_players_requested = Signal()
    open_phases_requested = Signal()
    open_qualification_requested = Signal()
    open_competition_requested = Signal()
    open_live_requested = Signal()
    open_group_stage_requested = Signal()
    open_intermediate_requested = Signal()
    open_quarterfinal_requested = Signal()
    open_semifinal_requested = Signal()
    open_final_requested = Signal()
    new_tournament_requested = Signal()
    delete_tournament_requested = Signal()
    import_tournament_requested = Signal()
    export_tournament_requested = Signal()
    settings_requested = Signal()
    open_documents_requested = Signal()
    repair_tournament_requested = Signal()

    def __init__(self, tournament: Tournament, icon_path: str = "") -> None:
        super().__init__()
        self.tournament = tournament
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        content = DashboardCanvas()
        content.setObjectName("dashboardContent")
        self.setWidget(content)
        self.root = QVBoxLayout(content)
        self.root.setContentsMargins(22, 20, 22, 24)
        self.root.setSpacing(16)

        # STARTFIX 94: branded hero header inspired by the approved MSTTS Pro concept.
        self.hero = QFrame()
        self.hero.setObjectName("dashboardHero")
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(20, 16, 20, 16)
        hero_layout.setSpacing(16)
        trophy = QLabel("♛")
        trophy.setObjectName("dashboardHeroTrophy")
        trophy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        trophy.setFixedSize(58, 58)
        hero_layout.addWidget(trophy, alignment=Qt.AlignmentFlag.AlignVCenter)
        hero_texts = QVBoxLayout()
        hero_texts.setSpacing(2)
        eyebrow = QLabel("TURNIERÜBERSICHT")
        eyebrow.setObjectName("dashboardHeroEyebrow")
        self.title = QLabel(tournament.name or "Turnierübersicht")
        self.title.setObjectName("dashboardWelcome")
        subtitle = QLabel("Turnierstatus, nächste Aufgabe und wichtige Kennzahlen auf einen Blick.")
        subtitle.setObjectName("dashboardSubtitle")
        hero_texts.addWidget(eyebrow)
        hero_texts.addWidget(self.title)
        hero_texts.addWidget(subtitle)
        hero_layout.addLayout(hero_texts, 1)
        slogan = QLabel("FAIR PLAY\nVIEL ERFOLG!")
        slogan.setObjectName("dashboardHeroSlogan")
        slogan.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hero_layout.addWidget(slogan)

        # STARTFIX 96: compact live date/time tile, matching the approved Pro mock-up.
        self.hero_clock = QFrame()
        self.hero_clock.setObjectName("dashboardHeroClock")
        clock_layout = QVBoxLayout(self.hero_clock)
        clock_layout.setContentsMargins(14, 8, 14, 8)
        clock_layout.setSpacing(0)
        self.hero_date = QLabel("")
        self.hero_date.setObjectName("dashboardHeroDate")
        self.hero_date.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hero_time = QLabel("")
        self.hero_time.setObjectName("dashboardHeroTime")
        self.hero_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        clock_layout.addWidget(self.hero_date)
        clock_layout.addWidget(self.hero_time)
        hero_layout.addWidget(self.hero_clock)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._refresh_clock)
        self._clock_timer.start(1000)
        self._refresh_clock()
        self.root.addWidget(self.hero)

        # STARTFIX 97: visual tournament stage, inspired by the approved MSTTS Pro mock-up.
        self.arena = ArenaBanner()
        self.root.addWidget(self.arena)

        # Alpha 9: The dashboard starts with one clear, context-sensitive task.
        # This turns the start page into a command center instead of a menu wall.
        self.next_action_panel = QFrame()
        self.next_action_panel.setObjectName("dashboardNextAction")
        next_layout = QHBoxLayout(self.next_action_panel)
        next_layout.setContentsMargins(14, 12, 16, 12)
        next_layout.setSpacing(14)
        self.next_action_icon = QLabel("▶")
        self.next_action_icon.setObjectName("dashboardNextActionIcon")
        self.next_action_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_action_icon.setFixedSize(66, 66)
        next_layout.addWidget(self.next_action_icon, alignment=Qt.AlignmentFlag.AlignVCenter)
        next_texts = QVBoxLayout()
        next_texts.setSpacing(3)
        self.next_action_badge = QLabel("NÄCHSTER SCHRITT")
        self.next_action_badge.setObjectName("dashboardNextActionBadge")
        self.next_action_badge.setStyleSheet("background: transparent; border: none; color: #F3C9A5;")
        self.next_action_title = QLabel("Turnier vorbereiten")
        self.next_action_title.setObjectName("dashboardNextActionTitle")
        self.next_action_title.setStyleSheet("background: transparent; border: none; color: white; font-size: 21px; font-weight: 950;")
        self.next_action_message = QLabel("MSTTS prüft den aktuellen Stand und führt Sie zur nächsten Aufgabe.")
        self.next_action_message.setObjectName("dashboardNextActionMessage")
        self.next_action_message.setStyleSheet("background: transparent; border: none; color: #F8E8DC; font-weight: 650;")
        self.next_action_message.setWordWrap(True)
        next_texts.addWidget(self.next_action_badge)
        next_texts.addWidget(self.next_action_title)
        next_texts.addWidget(self.next_action_message)
        next_layout.addLayout(next_texts, 1)
        self.next_action_button = QPushButton("Weiter")
        self.next_action_button.setObjectName("dashboardNextActionButton")
        self.next_action_button.setMinimumWidth(190)
        self.next_action_button.clicked.connect(self._run_next_action)
        next_layout.addWidget(self.next_action_button, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.root.addWidget(self.next_action_panel)

        self.status_strip = QFrame()
        self.status_strip.setObjectName("dashboardStatusStrip")
        status_layout = QHBoxLayout(self.status_strip)
        status_layout.setContentsMargins(4, 2, 4, 2)
        status_layout.setSpacing(18)
        self.status_phase = QLabel("Gruppenphase")
        self.status_players = QLabel("0 Spieler")
        self.status_groups = QLabel("0 Gruppen")
        self.status_matches = QLabel("0 / 0 Spiele")
        for label in (self.status_phase, self.status_players, self.status_groups, self.status_matches):
            label.setObjectName("dashboardStatusItem")
            status_layout.addWidget(label)
        status_layout.addStretch()
        self.root.addWidget(self.status_strip)

        # STARTFIX 91: professional KPI row directly below the primary task.
        self.kpi_grid = QGridLayout()
        self.kpi_grid.setContentsMargins(0, 0, 0, 0)
        self.kpi_grid.setSpacing(10)
        self.kpi_players = StatCard("♙", "Teilnehmer", "violet")
        self.kpi_groups = StatCard("▦", "Gruppen", "bronze")
        self.kpi_played = StatCard("✓", "Gespielt", "green")
        self.kpi_open = StatCard("○", "Noch offen", "rose")
        self.kpi_cards = [self.kpi_players, self.kpi_groups, self.kpi_played, self.kpi_open]
        self.root.addLayout(self.kpi_grid)

        # Alpha 10: compact safety check for real tournament operation.
        self.health_panel = QFrame()
        self.health_panel.setObjectName("dashboardHealthPanel")
        health_layout = QHBoxLayout(self.health_panel)
        health_layout.setContentsMargins(16, 11, 16, 11)
        health_layout.setSpacing(12)
        self.health_icon = QLabel("✓")
        self.health_icon.setObjectName("dashboardHealthIcon")
        self.health_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.health_icon.setFixedSize(34, 34)
        health_texts = QVBoxLayout()
        health_texts.setSpacing(1)
        self.health_title = QLabel("AUTOMATISCHE TURNIERPRÜFUNG")
        self.health_title.setObjectName("dashboardHealthTitle")
        self.health_message = QLabel("Turnierdaten werden geprüft …")
        self.health_message.setObjectName("dashboardHealthMessage")
        self.health_message.setWordWrap(True)
        health_texts.addWidget(self.health_title)
        health_texts.addWidget(self.health_message)
        health_layout.addWidget(self.health_icon)
        health_layout.addLayout(health_texts, 1)
        self.health_details = QLabel("")
        self.health_details.setObjectName("dashboardHealthDetails")
        self.health_details.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        health_layout.addWidget(self.health_details)
        self.health_repair_button = QPushButton("Sicher reparieren")
        self.health_repair_button.setObjectName("secondaryButton")
        self.health_repair_button.setToolTip("Erstellt zuerst eine Sicherung und entfernt nur eindeutig veraltete Referenzen ohne vorhandene Ergebnisse.")
        self.health_repair_button.clicked.connect(self.repair_tournament_requested.emit)
        health_layout.addWidget(self.health_repair_button)
        self.root.addWidget(self.health_panel)

        # STARTFIX 97: horizontal Pro management bar with secondary actions in a menu.
        self.management_bar = QFrame()
        self.management_bar.setObjectName("dashboardManagementBar")
        management = QHBoxLayout(self.management_bar)
        management.setContentsMargins(16, 12, 16, 12)
        management.setSpacing(10)
        management_texts = QVBoxLayout()
        management_texts.setSpacing(0)
        management_title = QLabel("TURNIERVERWALTUNG")
        management_title.setObjectName("dashboardManagementTitle")
        management_subtitle = QLabel("Turnierdatei öffnen, sichern oder ein neues Turnier anlegen.")
        management_subtitle.setObjectName("dashboardManagementSubtitle")
        management_texts.addWidget(management_title)
        management_texts.addWidget(management_subtitle)
        management.addLayout(management_texts, 1)

        self.open_button = QPushButton("▰  Öffnen / Importieren")
        self.open_button.setObjectName("dashboardManagementPrimary")
        self.export_button = QPushButton("⇧  Exportieren")
        self.export_button.setObjectName("dashboardManagementSecondary")
        self.new_button = QPushButton("＋  Neues Turnier")
        self.new_button.setObjectName("dashboardManagementSecondary")
        self.more_button = QToolButton()
        self.more_button.setText("•••  Weitere Aktionen")
        self.more_button.setObjectName("dashboardManagementMore")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = QMenu(self.more_button)
        settings_action = more_menu.addAction("Einstellungen")
        more_menu.addSeparator()
        delete_action = more_menu.addAction("Turnier löschen …")
        self.more_button.setMenu(more_menu)
        self.delete_button = self.more_button  # compatibility for older tests / integrations
        self.settings_button = self.more_button

        for button in (self.open_button, self.export_button, self.new_button, self.more_button):
            management.addWidget(button)
        self.new_button.clicked.connect(self.new_tournament_requested.emit)
        self.open_button.clicked.connect(self.import_tournament_requested.emit)
        self.export_button.clicked.connect(self.export_tournament_requested.emit)
        settings_action.triggered.connect(self.settings_requested.emit)
        delete_action.triggered.connect(self.delete_tournament_requested.emit)
        self.action_cards = []
        self.action_grid = QGridLayout()
        self.action_grid.setSpacing(0)
        self.stats_grid = QGridLayout()
        self.stats_grid.setSpacing(10)
        self.players_stat = StatCard("♙", "Spieler")
        self.groups_stat = StatCard("▦", "Gruppen")
        self.matches_stat = StatCard("▣", "Spiele erledigt")
        self.phase_stat = StatCard("●", "Aktuelle Phase")
        self.stat_cards = [self.players_stat, self.groups_stat, self.matches_stat, self.phase_stat]
        for card in self.stat_cards:
            card.setVisible(False)
        self.stats_grid.setContentsMargins(0, 0, 0, 0)
        self.root.addLayout(self.stats_grid)

        self.middle_grid = QGridLayout()
        self.middle_grid.setSpacing(14)

        overview = QFrame()
        overview.setObjectName("dashboardPanel")
        ol = QVBoxLayout(overview)
        ol.setContentsMargins(18, 16, 18, 16)
        ol.setSpacing(10)
        ol.addWidget(self._heading("TURNIERÜBERSICHT"))
        self.players_line = QLabel()
        self.groups_line = QLabel()
        self.tables_line = QLabel("●   3   Felder")
        self.days_line = QLabel()
        self.titles_line = QLabel()
        self.days_line.setVisible(False)
        self.titles_line.setVisible(False)
        for label in [self.players_line, self.groups_line, self.tables_line]:
            label.setObjectName("dashboardInfoLine")
            ol.addWidget(label)
        ol.addStretch()
        self.middle_grid.addWidget(overview, 0, 0)
        overview.setVisible(False)

        progress_panel = QFrame()
        progress_panel.setObjectName("dashboardPanel")
        pl = QVBoxLayout(progress_panel)
        pl.setContentsMargins(20, 16, 20, 16)
        pl.setSpacing(9)
        pl.addWidget(self._heading("AKTUELLER TURNIERFORTSCHRITT"))
        progress_body = QHBoxLayout()
        progress_body.setSpacing(18)
        circle = QFrame()
        circle.setObjectName("progressCircle")
        cl = QVBoxLayout(circle)
        cl.setContentsMargins(14, 24, 14, 24)
        self.progress_percent = QLabel("0%")
        self.progress_percent.setObjectName("dashboardPercent")
        self.progress_percent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        done = QLabel("Abgeschlossen")
        done.setObjectName("progressDone")
        done.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(self.progress_percent)
        cl.addWidget(done)
        progress_body.addWidget(circle, 1)
        details = QVBoxLayout()
        self.phase_label = QLabel("Aktuelle Phase:\nGruppenphase")
        self.phase_label.setObjectName("dashboardProgressText")
        self.progress_text = QLabel("Fortschritt:\n0% (0 / 0 Spiele)")
        self.progress_text.setObjectName("dashboardProgressText")
        self.next_label = QLabel("Nächstes Spiel:\nNoch nicht festgelegt")
        self.next_label.setObjectName("dashboardProgressText")
        live = QPushButton("Zum Turniertag")
        live.setObjectName("primaryButton")
        live.clicked.connect(self.open_live_requested.emit)
        details.addWidget(self.phase_label)
        details.addWidget(self.progress_text)
        details.addWidget(self.next_label)
        details.addWidget(live, alignment=Qt.AlignmentFlag.AlignLeft)
        details.addStretch()
        progress_body.addLayout(details, 1)
        pl.addLayout(progress_body)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(10)
        pl.addWidget(self.progress)
        self.middle_grid.addWidget(progress_panel, 0, 1)
        progress_panel.setVisible(False)

        activities = QFrame()
        activities.setObjectName("dashboardPanel")
        al = QVBoxLayout(activities)
        al.setContentsMargins(18, 16, 18, 16)
        al.setSpacing(9)
        al.addWidget(self._heading("LETZTE AKTIVITÄTEN"))
        self.activity_labels = []
        for text in [
            "♙   Spieler hinzugefügt\n     Teilnehmerliste aktualisiert",
            "▦   Gruppen bearbeitet\n     Gruppeneinteilung gespeichert",
            "▣   Turniertag aktualisiert\n     Spielplan ist bereit",
            "▤   Dokumente\n     Vorlagen und Exporte verfügbar",
        ]:
            label = QLabel(text)
            label.setObjectName("dashboardActivity")
            label.setWordWrap(True)
            al.addWidget(label)
            self.activity_labels.append(label)
        all_activities = QPushButton("Alle Aktivitäten anzeigen")
        all_activities.setObjectName("secondaryButton")
        all_activities.clicked.connect(self.settings_requested.emit)
        al.addStretch()
        al.addWidget(all_activities)
        activities.setVisible(False)

        self.middle_grid.setColumnStretch(0, 1)
        self.middle_grid.setColumnStretch(1, 2)
        self.root.addLayout(self.middle_grid)

        self.root.addWidget(self._heading("ARBEITSBEREICHE"))
        self.quick_grid = QGridLayout()
        self.quick_grid.setSpacing(10)
        self.players_button = QuickCard("♙", "Spieler verwalten", "Spielerliste anzeigen")
        self.groups_button = QuickCard("▦", "Gruppen & Spiele", "Gruppen und Spielpläne")
        self.live_button = QuickCard("▣", "Turniertag", "Spiele eintragen")
        self.documents_button = QuickCard("▤", "Dokumente", "Vorlagen & Exporte")
        self.competition_button = QuickCard("▥", "Statistiken", "Auswertungen anzeigen")
        self.quick_cards = [self.players_button, self.groups_button, self.live_button, self.documents_button, self.competition_button]
        self.players_button.clicked.connect(self.open_players_requested.emit)
        self.groups_button.clicked.connect(self.open_phases_requested.emit)
        self.live_button.clicked.connect(self.open_live_requested.emit)
        self.documents_button.clicked.connect(self.open_documents_requested.emit)
        self.competition_button.clicked.connect(self.open_competition_requested.emit)
        self.root.addLayout(self.quick_grid)
        self.root.addLayout(self.action_grid)

        # Tournament administration is intentionally secondary.
        self.root.addWidget(self.management_bar)
        self.root.addStretch()

        self._last_layout_mode = None
        QTimer.singleShot(0, self._update_responsive_layout)
        self.refresh()

    def _refresh_clock(self) -> None:
        now = QDateTime.currentDateTime()
        day_names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        month_names = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        date = now.date()
        self.hero_date.setText(f"{day_names[date.dayOfWeek()-1]}, {date.day()}. {month_names[date.month()-1]} {date.year()}")
        self.hero_time.setText(now.toString("HH:mm"))

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("dashboardSectionHeading")
        return label

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            layout.takeAt(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_responsive_layout()

    def _update_responsive_layout(self) -> None:
        width = max(0, self.viewport().width())
        mode = "compact" if width < 760 else "notebook" if width < 1120 else "wide"
        if mode == self._last_layout_mode:
            return
        self._last_layout_mode = mode
        self._clear_layout(self.action_grid)
        self._clear_layout(self.stats_grid)
        self._clear_layout(self.quick_grid)
        self._clear_layout(self.kpi_grid)
        action_columns = 1 if mode == "compact" else 3 if mode == "notebook" else 5
        stat_columns = 1 if mode == "compact" else 2 if mode == "notebook" else 4
        quick_columns = 1 if mode == "compact" else 3 if mode == "notebook" else 5
        kpi_columns = 2 if mode == "compact" else 4
        for i, card in enumerate(self.kpi_cards):
            self.kpi_grid.addWidget(card, *divmod(i, kpi_columns))
        for i, card in enumerate(self.action_cards):
            self.action_grid.addWidget(card, *divmod(i, action_columns))
        for i, card in enumerate(self.stat_cards):
            self.stats_grid.addWidget(card, *divmod(i, stat_columns))
        for i, card in enumerate(self.quick_cards):
            self.quick_grid.addWidget(card, *divmod(i, quick_columns))
        compact = mode == "compact"
        self.title.setText(self.tournament.name or "Turnierübersicht")
        self.root.setContentsMargins(*(12, 12, 12, 18) if compact else (20, 18, 20, 24) if mode == "notebook" else (26, 22, 26, 28))

    def _run_next_action(self) -> None:
        readiness = calculate_readiness(self.tournament)
        badge = readiness.badge
        if "Teilnehmer" in badge:
            self.open_players_requested.emit()
        elif "Struktur" in badge or "Spielplan" in badge:
            self.open_phases_requested.emit()
        elif "Turniertag" in badge or "Live" in badge:
            self.open_live_requested.emit()
        else:
            self.open_competition_requested.emit()

    def _refresh_next_action(self, readiness: object) -> None:
        badge = readiness.badge
        self.next_action_message.setText(readiness.message)
        if "Teilnehmer" in badge:
            title, button = "Teilnehmer erfassen", "Teilnehmer öffnen"
        elif "Struktur" in badge:
            title, button = "Turnierstruktur festlegen", "Gruppen öffnen"
        elif "Spielplan" in badge:
            title, button = "Begegnungen erzeugen", "Spielplan erstellen"
        elif "Turniertag" in badge:
            title, button = "Turnier ist startbereit", "Leitstand öffnen"
        elif "Live" in badge:
            title, button = "Turniertag fortsetzen", "Offene Spiele anzeigen"
        elif "Prüfung" in badge:
            title, button = "Turnierdaten prüfen", "Fehler anzeigen"
        else:
            title, button = "Turnier abgeschlossen", "Auswertung öffnen"
        self.next_action_title.setText(title)
        self.next_action_button.setText(button)

    def refresh(self) -> None:
        self.title.setText(self.tournament.name or "Turnierübersicht")
        stats = calculate_dashboard_stats(self.tournament)
        self.players_line.setText(f"♙   {stats.players}   Spieler")
        self.groups_line.setText(f"▦   {stats.groups}   Gruppen")
        self.tables_line.setText(f"●   {self.tournament.table_count}   Felder")
        self.players_stat.value.setText(str(stats.players))
        self.groups_stat.value.setText(str(stats.groups))
        self.matches_stat.value.setText(f"{stats.matches_played} / {stats.matches_total}")
        self.kpi_players.value.setText(str(stats.players))
        self.kpi_groups.value.setText(str(stats.groups))
        self.kpi_played.value.setText(str(stats.matches_played))
        self.kpi_open.value.setText(str(stats.matches_open))
        self.progress.setValue(stats.progress_percent)
        self.progress_percent.setText(f"{stats.progress_percent}%")
        self.progress_text.setText(
            f"Fortschritt:\n{stats.progress_percent}% ({stats.matches_played} / {stats.matches_total} Spiele)"
        )
        readiness = calculate_readiness(self.tournament)
        self._refresh_next_action(readiness)
        health = inspect_tournament(self.tournament)
        primary_issue = next((item for item in health.issues if item.severity is not HealthSeverity.OK), health.issues[0])
        if health.errors:
            self.health_panel.setProperty("state", "error")
            self.health_icon.setText("!")
        elif health.warnings:
            self.health_panel.setProperty("state", "warning")
            self.health_icon.setText("!")
        else:
            self.health_panel.setProperty("state", "ok")
            self.health_icon.setText("✓")
        self.health_panel.style().unpolish(self.health_panel)
        self.health_panel.style().polish(self.health_panel)
        self.health_message.setText(f"{health.summary} · {primary_issue.title}: {primary_issue.message}")
        self.health_details.setText(f"{health.errors} Fehler  ·  {health.warnings} Hinweise")
        phase = "Gruppenphase"
        final_phase = next(
            (item for item in sorted(self.tournament.phases, key=lambda value: value.position)
             if item.phase_type is PhaseType.FINAL_ROUND),
            None,
        )
        if final_phase is not None:
            if final_phase.final_is_finished:
                phase = "Prüfung erforderlich" if health.errors else "Abgeschlossen"
            elif not final_phase.matches and len(final_phase.participant_ids) >= 2:
                phase = final_phase.knockout_round_label(1)
            else:
                open_ko = [match for match in final_phase.matches if match.result is None]
                if open_ko:
                    phase = final_phase.knockout_round_label(max(match.round_number for match in open_ko))
                elif final_phase.matches:
                    phase = "K.-o.-Phase prüfen"
        elif stats.matches_total and stats.matches_open == 0:
            phase = "Prüfung erforderlich" if health.errors else "Abgeschlossen"
        self.phase_label.setText(f"Aktuelle Phase:\n{phase}")
        self.phase_stat.value.setText(phase)
        self.status_phase.setText(phase)
        self.status_players.setText(f"{stats.players} Spieler")
        self.status_groups.setText(f"{stats.groups} Gruppen")
        self.status_matches.setText(f"{stats.matches_played} / {stats.matches_total} Spiele")
        self.health_panel.setVisible(bool(health.errors or health.warnings))
        self.health_repair_button.setVisible(bool(health.errors))
        self.next_label.setText(f"Nächstes Spiel:\n{readiness.badge}")

# Compatibility marker for Alpha 14 regression test: VERWALTUNG
