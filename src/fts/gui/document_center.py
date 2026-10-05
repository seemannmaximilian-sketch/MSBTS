from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
    QSpinBox, QInputDialog, QLineEdit, QColorDialog, QSplitter, QSizePolicy, QDialog, QScrollArea,
    QStackedWidget,
)

from fts.documents import (DocumentSelection, DocumentProfile, BrandingProfile, PrintSettings, apply_print_settings, combined_selected_document_html)
from fts.domain import PhaseType, ValidationError
from fts.engine import TournamentEngine

try:
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView
except ImportError:  # QtPdf may be missing in minimal PySide6 installations.
    QPdfDocument = None
    QPdfView = None


class DocumentCenterPage(QWidget):
    """Phase 3.3 document selection, preview and export control."""

    def __init__(self, service: TournamentEngine) -> None:
        super().__init__()
        self.setObjectName("documentCenterPage")
        self.service = service
        self.last_output_dir: Path | None = None
        self.last_exported_pdf: Path | None = None

        title = QLabel("Druck, Design und Export")
        title.setObjectName("pageTitle")
        title.setVisible(False)
        subtitle = QLabel("Hier erstellen Sie professionelle Turnierunterlagen als PDF, Word und Excel.")
        subtitle.setWordWrap(True)
        subtitle.setObjectName("pageSubtitle")
        subtitle.setVisible(False)

        top_tabs = QFrame()
        top_tabs.setObjectName("documentCommandBar")
        top_tabs_layout = QHBoxLayout(top_tabs)
        top_tabs_layout.setContentsMargins(0, 0, 0, 0)
        top_tabs_layout.setSpacing(6)
        self.export_tab_button = QPushButton("▣  Export")
        self.export_tab_button.setObjectName("activeTopTab")
        self.design_tab_button = QPushButton("◉  Design")
        self.design_tab_button.setObjectName("topTab")
        self.print_tab_button = QPushButton("▤  Druck")
        self.print_tab_button.setObjectName("topTab")
        self.preview_tab_button = QPushButton("◉  Vorschau")
        self.preview_tab_button.setObjectName("topTab")
        self.design_tab_button.clicked.connect(self.open_advanced_dialog)
        self.print_tab_button.clicked.connect(self.open_advanced_dialog)
        self.preview_tab_button.clicked.connect(lambda: self.toggle_preview(True))
        for button in (self.export_tab_button, self.design_tab_button, self.print_tab_button, self.preview_tab_button):
            button.setMinimumHeight(38)
            top_tabs_layout.addWidget(button)
        top_tabs_layout.addStretch()

        profile_box = QFrame()
        profile_box.setObjectName("uiCard")
        profile_layout = QHBoxLayout(profile_box)
        profile_layout.setContentsMargins(12, 7, 12, 7)
        profile_layout.setSpacing(8)
        profile_layout.addWidget(QLabel("<b>Profil</b>"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(180)
        self.profile_combo.currentTextChanged.connect(self.apply_profile)
        profile_layout.addWidget(self.profile_combo, 1)
        self.save_profile_button = QPushButton("Speichern")
        self.save_profile_button.clicked.connect(self.save_profile)
        self.delete_profile_button = QPushButton("Löschen")
        self.delete_profile_button.clicked.connect(self.delete_profile)
        profile_layout.addWidget(self.save_profile_button)
        profile_layout.addWidget(self.delete_profile_button)

        selection_box = QFrame()
        selection_box.setObjectName("documentWorkspace")
        selection_layout = QVBoxLayout(selection_box)
        selection_layout.setContentsMargins(14, 12, 14, 12)
        selection_layout.setSpacing(8)

        export_intro = QHBoxLayout()
        export_icon = QLabel("▤")
        export_icon.setObjectName("exportIcon")
        export_intro_text = QVBoxLayout()
        export_intro_title = QLabel("Ausgabeumfang")
        export_intro_title.setObjectName("cardTitle")
        export_intro_subtitle = QLabel("Inhalte für die Turnierunterlagen auswählen.")
        export_intro_subtitle.setWordWrap(True)
        export_intro_subtitle.setObjectName("cardSubtitle")
        export_intro_text.addWidget(export_intro_title)
        export_intro_text.addWidget(export_intro_subtitle)
        export_intro.addWidget(export_icon, 0, Qt.AlignmentFlag.AlignTop)
        export_intro.addLayout(export_intro_text, 1)
        selection_layout.addLayout(export_intro)

        hero_export_button = QPushButton("Turnierunterlagen exportieren")
        hero_export_button.setObjectName("heroExport")
        hero_export_button.setMinimumHeight(38)
        hero_export_button.setMaximumHeight(42)
        hero_export_button.clicked.connect(self.export_selection)
        selection_layout.addWidget(hero_export_button)

        selection_header = QHBoxLayout()
        selection_header.addWidget(QLabel("<b>Enthaltene Dokumente</b>"))
        selection_header.addStretch()
        select_all_button = QPushButton("Alle")
        select_all_button.setObjectName("quietButton")
        select_all_button.clicked.connect(self.select_all)
        clear_all_button = QPushButton("Keine")
        clear_all_button.setObjectName("quietButton")
        clear_all_button.clicked.connect(self.clear_selection)
        selection_header.addWidget(select_all_button)
        selection_header.addWidget(clear_all_button)
        selection_layout.addLayout(selection_header)

        self.document_checks: dict[str, QCheckBox] = {}
        labels = {
            "overview": ("Übersicht", "Turnierdaten und Kurzüberblick"),
            "participants": ("Teilnehmer", "Teilnehmerlisten und Gruppenzuordnung"),
            "groups": ("Gruppen", "Gruppentabellen und Gruppenspiele"),
            "schedule": ("Spielplan", "Zeiten und Felderbelegung"),
            "score_sheets": ("Ergebniszettel", "Ausfüllbare Ergebniszettel"),
            "scorekeeper_plan": ("Schiedsrichter", "Einsatzplan der Schiedsrichter"),
            "knockout_bracket": ("K.-o.-Baum", "Viertelfinale bis Finale"),
        }
        document_grid = QGridLayout()
        document_grid.setHorizontalSpacing(8)
        document_grid.setVerticalSpacing(6)
        for index, (key, (label, help_text)) in enumerate(labels.items()):
            check = QCheckBox(label)
            check.setToolTip(help_text)
            check.setObjectName("documentRow")
            check.setChecked(True)
            check.setMinimumHeight(30)
            check.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            check.stateChanged.connect(self.refresh)
            self.document_checks[key] = check
            document_grid.addWidget(check, index // 2, index % 2)
        selection_layout.addLayout(document_grid)

        action_row = QHBoxLayout()
        self.filter_toggle = QPushButton("Filter …")
        self.filter_toggle.setObjectName("secondaryAction")
        self.filter_toggle.clicked.connect(self.open_filter_dialog)
        self.advanced_toggle = QPushButton("Druck & Design …")
        self.advanced_toggle.setObjectName("secondaryAction")
        self.advanced_toggle.clicked.connect(self.open_advanced_dialog)
        self.preview_toggle = QPushButton("Vorschau anzeigen")
        self.preview_toggle.setObjectName("secondaryAction")
        self.preview_toggle.setCheckable(True)
        self.preview_toggle.toggled.connect(self.toggle_preview)
        action_row.addWidget(self.filter_toggle)
        action_row.addWidget(self.advanced_toggle)
        action_row.addStretch()
        action_row.addWidget(self.preview_toggle)
        selection_layout.addLayout(action_row)

        # Filter are kept in a separate dialog so long group and match lists never
        # squeeze the main workspace.
        self.filter_dialog = QDialog(self)
        self.filter_dialog.setWindowTitle("Dokumentfilter")
        self.filter_dialog.resize(760, 480)
        filter_dialog_layout = QVBoxLayout(self.filter_dialog)
        filter_dialog_layout.addWidget(QLabel("<b>Ausgabe eingrenzen</b>"))
        filter_hint = QLabel("Ohne Auswahl werden automatisch alle Gruppen und Spiele berücksichtigt.")
        filter_hint.setWordWrap(True)
        filter_hint.setObjectName("dialogHint")
        filter_dialog_layout.addWidget(filter_hint)
        filter_columns = QHBoxLayout()
        group_column = QVBoxLayout()
        group_column.addWidget(QLabel("Gruppen"))
        self.group_list = QListWidget()
        self.group_list.itemSelectionChanged.connect(self.refresh)
        group_column.addWidget(self.group_list)
        match_column = QVBoxLayout()
        match_column.addWidget(QLabel("Einzelspiele"))
        self.match_list = QListWidget()
        self.match_list.itemSelectionChanged.connect(self.refresh)
        match_column.addWidget(self.match_list)
        filter_columns.addLayout(group_column, 1)
        filter_columns.addLayout(match_column, 2)
        filter_dialog_layout.addLayout(filter_columns, 1)
        filter_buttons = QHBoxLayout()
        clear_filters = QPushButton("Filter löschen")
        clear_filters.clicked.connect(self.clear_filters)
        close_filters = QPushButton("Übernehmen und schließen")
        close_filters.setObjectName("primaryAction")
        close_filters.clicked.connect(self.filter_dialog.accept)
        filter_buttons.addWidget(clear_filters)
        filter_buttons.addStretch()
        filter_buttons.addWidget(close_filters)
        filter_dialog_layout.addLayout(filter_buttons)

        # Advanced controls live in their own scrollable dialog.
        self.advanced_dialog = QDialog(self)
        self.advanced_dialog.setWindowTitle("PDF, Word, Excel und Design")
        self.advanced_dialog.resize(720, 600)
        advanced_root = QVBoxLayout(self.advanced_dialog)
        advanced_scroll = QScrollArea()
        advanced_scroll.setWidgetResizable(True)
        advanced_widget = QWidget()
        advanced_layout = QVBoxLayout(advanced_widget)
        advanced_layout.setContentsMargins(14, 12, 14, 12)
        advanced_layout.setSpacing(11)

        options = QHBoxLayout()
        self.pdf_option = QCheckBox("PDF")
        self.pdf_option.setChecked(True)
        self.word_option = QCheckBox("Word (.docx)")
        self.word_option.setChecked(True)
        self.excel_option = QCheckBox("Excel (.xlsx)")
        self.excel_option.setChecked(True)
        self.archive_option = QCheckBox("Zusätzlich als ZIP-Paket")
        options.addWidget(self.pdf_option)
        options.addWidget(self.word_option)
        options.addWidget(self.excel_option)
        options.addWidget(self.archive_option)
        options.addStretch()
        advanced_layout.addWidget(QLabel("<b>Export</b>"))
        advanced_layout.addLayout(options)

        page_row = QHBoxLayout()
        page_row.addWidget(QLabel("Format"))
        self.page_size = QComboBox()
        self.page_size.addItems(["A4", "A3"])
        self.page_size.currentTextChanged.connect(self.refresh)
        page_row.addWidget(self.page_size)
        self.orientation = QComboBox()
        self.orientation.addItem("Hochformat", "portrait")
        self.orientation.addItem("Querformat", "landscape")
        self.orientation.currentIndexChanged.connect(self.refresh)
        page_row.addWidget(self.orientation)
        page_row.addWidget(QLabel("Rand"))
        self.margin_mm = QSpinBox()
        self.margin_mm.setRange(5, 30)
        self.margin_mm.setValue(14)
        self.margin_mm.setSuffix(" mm")
        self.margin_mm.valueChanged.connect(self.refresh)
        page_row.addWidget(self.margin_mm)
        page_row.addStretch()
        advanced_layout.addWidget(QLabel("<b>Seitenlayout</b>"))
        advanced_layout.addLayout(page_row)

        chrome_row = QHBoxLayout()
        self.header_option = QCheckBox("Kopfzeile")
        self.header_option.setChecked(True)
        self.header_option.stateChanged.connect(self.refresh)
        self.logo_option = QCheckBox("Logo")
        self.logo_option.setChecked(True)
        self.logo_option.stateChanged.connect(self.refresh)
        self.footer_option = QCheckBox("Fußzeile")
        self.footer_option.setChecked(True)
        self.footer_option.stateChanged.connect(self.refresh)
        self.page_number_option = QCheckBox("Seitenzahlen")
        self.page_number_option.setChecked(True)
        self.page_number_option.stateChanged.connect(self.refresh)
        self.footer_text = QLineEdit("MSBTS – Badminton-Turnierzentrale")
        self.footer_text.setMaxLength(120)
        self.footer_text.textChanged.connect(self.refresh)
        for widget in (self.header_option, self.logo_option, self.footer_option, self.page_number_option):
            chrome_row.addWidget(widget)
        advanced_layout.addLayout(chrome_row)
        footer_row = QHBoxLayout()
        footer_row.addWidget(QLabel("Fußzeilentext"))
        footer_row.addWidget(self.footer_text, 1)
        advanced_layout.addLayout(footer_row)

        branding_row = QHBoxLayout()
        self.organizer_name = QLineEdit()
        self.organizer_name.setPlaceholderText("Veranstalter")
        self.organizer_name.setMaxLength(100)
        self.organizer_name.textChanged.connect(self.refresh)
        self.primary_color = QLineEdit("#6d35a5")
        self.primary_color.setMaxLength(7)
        self.primary_color.setFixedWidth(86)
        self.primary_color.textChanged.connect(self._branding_changed)
        self.accent_color = QLineEdit("#b99a80")
        self.accent_color.setMaxLength(7)
        self.accent_color.setFixedWidth(86)
        self.accent_color.textChanged.connect(self._branding_changed)
        branding_row.addWidget(QLabel("Veranstalter"))
        branding_row.addWidget(self.organizer_name, 1)
        branding_row.addWidget(self.primary_color)
        branding_row.addWidget(self.accent_color)
        advanced_layout.addWidget(QLabel("<b>Branding</b>"))
        advanced_layout.addLayout(branding_row)

        logo_row = QHBoxLayout()
        self.custom_logo_path = QLineEdit()
        self.custom_logo_path.setPlaceholderText("Eigenes Logo – optional")
        self.custom_logo_path.textChanged.connect(self.refresh)
        logo_button = QPushButton("Logo wählen")
        logo_button.clicked.connect(self.choose_branding_logo)
        clear_logo_button = QPushButton("Zurücksetzen")
        clear_logo_button.clicked.connect(lambda: self.custom_logo_path.clear())
        logo_row.addWidget(self.custom_logo_path, 1)
        logo_row.addWidget(logo_button)
        logo_row.addWidget(clear_logo_button)
        advanced_layout.addLayout(logo_row)

        design_row = QHBoxLayout()
        design_row.addWidget(QLabel("Design"))
        self.branding_profile_combo = QComboBox()
        self.branding_profile_combo.currentTextChanged.connect(self.apply_branding_profile)
        design_row.addWidget(self.branding_profile_combo, 1)
        self.primary_preview = QLabel("  ")
        self.primary_preview.setFixedSize(30, 20)
        self.accent_preview = QLabel("  ")
        self.accent_preview.setFixedSize(30, 20)
        design_row.addWidget(self.primary_preview)
        design_row.addWidget(self.accent_preview)
        primary_picker = QPushButton("Farben")
        primary_picker.clicked.connect(lambda: self.choose_color(self.primary_color))
        accent_picker = QPushButton("Akzent")
        accent_picker.clicked.connect(lambda: self.choose_color(self.accent_color))
        design_row.addWidget(primary_picker)
        design_row.addWidget(accent_picker)
        advanced_layout.addLayout(design_row)

        profile_actions = QHBoxLayout()
        self.save_branding_button = QPushButton("Design speichern")
        self.save_branding_button.clicked.connect(self.save_branding_profile)
        self.delete_branding_button = QPushButton("Design löschen")
        self.delete_branding_button.clicked.connect(self.delete_branding_profile)
        export_branding_button = QPushButton("Designprofil exportieren (.json)")
        export_branding_button.setToolTip("Exportiert nur Farben und Logo-Einstellungen – keine Turnierunterlagen.")
        export_branding_button.clicked.connect(self.export_branding_profile)
        import_branding_button = QPushButton("Designprofil importieren")
        import_branding_button.clicked.connect(self.import_branding_profile)
        profile_actions.addWidget(self.save_branding_button)
        profile_actions.addWidget(self.delete_branding_button)
        profile_actions.addStretch()
        profile_actions.addWidget(import_branding_button)
        profile_actions.addWidget(export_branding_button)
        advanced_layout.addLayout(profile_actions)

        design_hint = QLabel("Hinweis: Der Designprofil-Export speichert nur die Gestaltung als JSON-Datei. Für PDF, Word und Excel verwenden Sie den großen Dokumenten-Export unten.")
        design_hint.setWordWrap(True)
        design_hint.setObjectName("dialogHint")
        advanced_layout.addWidget(design_hint)
        advanced_layout.addStretch()
        advanced_scroll.setWidget(advanced_widget)
        advanced_root.addWidget(advanced_scroll, 1)
        advanced_actions = QHBoxLayout()
        advanced_close = QPushButton("Einstellungen übernehmen")
        advanced_close.clicked.connect(self.advanced_dialog.accept)
        advanced_export = QPushButton("Turnierunterlagen jetzt exportieren")
        advanced_export.setObjectName("primaryAction")
        advanced_export.clicked.connect(self.export_selection)
        advanced_actions.addWidget(advanced_close)
        advanced_actions.addStretch()
        advanced_actions.addWidget(advanced_export)
        advanced_root.addLayout(advanced_actions)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("selectionStatus")

        # The preview must show the PDF that was actually written to disk.
        # Before the first export we deliberately show a neutral message instead
        # of the branding image or an HTML approximation.
        self.preview_stack = QStackedWidget()
        self.preview_stack.setObjectName("documentPreviewStack")
        self.preview_stack.setMinimumWidth(330)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview.setObjectName("documentPreview")
        self.preview.setHtml(
            "<div style='text-align:center; padding:70px 28px; color:#CDBBE8;'>"
            "<div style='font-size:42px; margin-bottom:16px;'>▤</div>"
            "<h3 style='color:#F4EEFF;'>Noch keine PDF-Vorschau verfügbar</h3>"
            "<p>Exportieren Sie zuerst die Turnierunterlagen als PDF.<br>"
            "Danach wird hier genau die erzeugte PDF-Datei angezeigt.</p>"
            "</div>"
        )
        self.preview_stack.addWidget(self.preview)

        self.pdf_document = None
        self.pdf_view = None
        if QPdfDocument is not None and QPdfView is not None:
            self.pdf_document = QPdfDocument(self)
            self.pdf_view = QPdfView()
            self.pdf_view.setObjectName("pdfPreview")
            self.pdf_view.setDocument(self.pdf_document)
            self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
            self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            self.preview_stack.addWidget(self.pdf_view)

        design_card = QFrame()
        design_card.setObjectName("uiCard")
        design_layout = QVBoxLayout(design_card)
        design_layout.setContentsMargins(16, 14, 16, 14)
        design_layout.setSpacing(8)
        design_title = QLabel("◉  Designprofil exportieren (JSON)")
        design_title.setObjectName("cardTitle")
        design_text = QLabel("Exportiert Farben, Logo und Layout als Designprofil. Erzeugt keine Turnierunterlagen.")
        design_text.setWordWrap(True)
        design_text.setObjectName("cardSubtitle")
        design_button = QPushButton("Designprofil und Einstellungen öffnen")
        design_button.setObjectName("secondaryAction")
        design_button.clicked.connect(self.open_advanced_dialog)
        design_layout.addWidget(design_title)
        design_layout.addWidget(design_text)
        design_layout.addWidget(design_button)

        folder_card = QFrame()
        folder_card.setObjectName("documentOutputBar")
        folder_layout = QVBoxLayout(folder_card)
        folder_layout.setContentsMargins(16, 12, 16, 12)
        folder_layout.setSpacing(6)
        folder_title = QLabel("ⓘ  Ausgabeordner")
        folder_title.setObjectName("cardTitle")
        self.output_path_label = QLabel("Der Zielordner wird beim Export ausgewählt.")
        self.output_path_label.setWordWrap(True)
        self.output_path_label.setObjectName("outputPath")
        folder_open_button = QPushButton("Ordner im Finder öffnen")
        folder_open_button.setObjectName("secondaryAction")
        folder_open_button.clicked.connect(self.open_last_output)
        folder_layout.addWidget(folder_title)
        folder_layout.addWidget(self.output_path_label)
        folder_layout.addWidget(folder_open_button, 0, Qt.AlignmentFlag.AlignLeft)

        left_widget = QWidget()
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        profile_box.setVisible(False)
        left.addWidget(profile_box)
        left.addWidget(selection_box)
        design_card.setVisible(False)
        left.addWidget(design_card)
        left.addWidget(folder_card)
        left.addWidget(self.status)
        left.addStretch()
        # Startfix 66: compact tool column; the preview is the primary workspace.
        left_scroll = QScrollArea()
        left_scroll.setObjectName("documentToolScroll")
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_widget)
        left_scroll.setMinimumWidth(330)
        left_scroll.setMaximumWidth(520)

        preview_container = QFrame()
        preview_container.setObjectName("previewCard")
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(12, 10, 12, 12)
        preview_layout.setSpacing(8)
        self.preview_header = QLabel("Vorschau: Turnierunterlagen")
        self.preview_header.setObjectName("previewTitle")
        preview_layout.addWidget(self.preview_header)
        preview_layout.addWidget(self.preview_stack, 1)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("documentSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(left_scroll)
        self.splitter.addWidget(preview_container)
        self.splitter.setStretchFactor(0, 38)
        self.splitter.setStretchFactor(1, 62)
        self.splitter.setSizes([390, 640])
        self.splitter.setStyleSheet("QSplitter::handle { background:#7337B4; border:none; width:4px; } QSplitter::handle:hover { background:#B34CFF; }")
        self.preview_stack.setVisible(True)

        export_bar = QFrame()
        export_bar.setObjectName("exportStatusBar")
        buttons = QHBoxLayout(export_bar)
        buttons.setContentsMargins(14, 8, 14, 8)
        buttons.setSpacing(12)
        self.export_state_label = QLabel("●  Noch kein Export in dieser Sitzung")
        self.export_state_label.setObjectName("exportState")
        self.export_file_labels = []
        buttons.addWidget(self.export_state_label)
        buttons.addStretch()
        for text in ("PDF  Teilnehmerliste", "PDF  Turnierunterlagen", "XLSX  Turnierdaten"):
            label = QLabel(text)
            label.setObjectName("fileChip")
            label.setVisible(False)
            self.export_file_labels.append(label)
            buttons.addWidget(label)
        open_button = QPushButton("Im Finder anzeigen")
        open_button.setObjectName("secondaryAction")
        open_button.clicked.connect(self.open_last_output)
        buttons.addWidget(open_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(6)
        header_row = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.addWidget(title)
        header_text.addWidget(subtitle)
        header_row.addLayout(header_text, 1)
        info_banner = QLabel("ⓘ  Professionelle Turnierunterlagen als PDF, Word und Excel")
        info_banner.setObjectName("infoBanner")
        info_banner.setWordWrap(True)
        info_banner.setVisible(False)
        header_row.addWidget(info_banner)
        layout.addLayout(header_row)
        layout.addWidget(top_tabs)
        layout.addWidget(self.splitter, 1)
        layout.addWidget(export_bar)

        self.setStyleSheet("""
            QLabel#pageTitle { font-size: 24px; font-weight: 800; color: #F4EEFF; }
            QLabel#pageSubtitle { color: #CDBBE8; font-size: 12px; padding-bottom: 1px; }
            QLabel#dialogHint { color: #CDBBE8; font-size: 12px; }
            QFrame#uiCard { background: #120724; border: 1px solid #5E2A8A; border-radius: 11px; }
            QCheckBox#documentRow { background: #160A2B; border: 1px solid #dfcdbd; border-radius: 7px; padding: 5px 9px; font-size: 12px; font-weight: 650; color: #DCCCF2; }
            QCheckBox#documentRow:checked { background: #3A1760; border: 1px solid #8147C0; color: #FFFFFF; }
            QPushButton#primaryAction { background: #8D2CFF; color: white; border-radius: 8px; padding: 6px 15px; font-weight: 700; }
            QPushButton#secondaryAction { background: #21103B; color: #E9DEFA; border: 1px solid #5E2A8A; border-radius: 7px; padding: 6px 10px; font-weight: 650; }
            QPushButton#secondaryAction:checked { background: #3A1760; border-color: #8147C0; }
            QPushButton#quietButton { min-width: 44px; padding: 4px 8px; }
            QTextBrowser#documentPreview, QPdfView#pdfPreview { background: #090619; border: 1px solid #5E2A8A; border-radius: 11px; padding: 5px; }
            QStackedWidget#documentPreviewStack { background: #090619; border: 1px solid #5E2A8A; border-radius: 11px; }
            QLabel#selectionStatus { padding: 7px 10px; background: #f5efe7; border-radius: 7px; color: #5b4333; font-size: 11px; }
            QSplitter::handle { background: transparent; width: 6px; }
            QScrollArea#documentToolScroll { background: transparent; border: none; }
            QFrame#topTabs { border-bottom: 1px solid #decfc2; }
            QPushButton#topTab, QPushButton#activeTopTab { background: transparent; border: none; border-radius: 0; padding: 8px 18px; font-weight: 700; color: #5f493a; }
            QPushButton#activeTopTab { color: #F4EEFF; border-bottom: 3px solid #8D2CFF; }
            QPushButton#topTab:hover { background: #21103B; border-radius: 8px; }
            QLabel#infoBanner { background: #160A2B; border: 1px solid #5E2A8A; border-radius: 10px; padding: 10px 14px; color: #DCCCF2; max-width: 350px; }
            QLabel#exportIcon { font-size: 28px; min-width: 44px; color: #8D2CFF; background: #281244; border-radius: 9px; padding: 8px; }
            QLabel#cardTitle { font-size: 15px; font-weight: 800; color: #F4EEFF; }
            QLabel#cardSubtitle { font-size: 11px; color: #CDBBE8; }
            QPushButton#heroExport { text-align: left; background: #6d3e1e; color: white; border: none; border-radius: 9px; padding: 10px 18px; font-size: 13px; font-weight: 800; }
            QPushButton#heroExport:hover { background: #7b4b2a; }
            QFrame#infoCard { background: #160A2B; border: 1px solid #5E2A8A; border-radius: 11px; }
            QLabel#outputPath { background: #10071F; border: 1px solid #5E2A8A; border-radius: 6px; padding: 7px; color: #E9DEFA; font-family: monospace; }
            QFrame#previewCard { background: #120724; border: 1px solid #5E2A8A; border-radius: 11px; }
            QLabel#previewTitle { font-size: 15px; font-weight: 800; color: #FFFFFF; padding: 4px 2px 8px 2px; border-bottom: 1px solid #e9e2dc; }
            QFrame#exportStatusBar { background: #120724; border-top: 1px solid #5E2A8A; }
            QLabel#exportState { color: #57614f; font-weight: 700; }
            QLabel#fileChip { background: #21103B; border: 1px solid #5E2A8A; border-radius: 7px; padding: 7px 10px; color: #E9DEFA; font-weight: 650; }
        """)


        # Alpha 15: quiet professional document workspace.
        self.setStyleSheet(self.styleSheet() + """
            QFrame#documentCommandBar {
                background: transparent;
                border: none;
                border-bottom: 1px solid #E8E0D9;
                padding-bottom: 4px;
            }
            QFrame#documentWorkspace {
                background: transparent;
                border: none;
            }
            QFrame#documentOutputBar {
                background: #10071F;
                border: 1px solid #ECE5DF;
                border-radius: 8px;
            }
            QPushButton#heroExport {
                text-align: center;
                background: #6D4A34;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 18px;
                font-size: 12px;
                font-weight: 750;
            }
            QCheckBox#documentRow {
                background: transparent;
                border: none;
                border-bottom: 1px solid #EEE7E1;
                border-radius: 0px;
                padding: 7px 5px;
                font-size: 12px;
                font-weight: 600;
                color: #55463C;
            }
            QCheckBox#documentRow:checked {
                background: #F7F1EC;
                border: none;
                border-bottom: 1px solid #E5D9CF;
                color: #3F2A1E;
            }
            QFrame#previewCard {
                background: #F4F0EC;
                border: 1px solid #DED4CB;
                border-radius: 10px;
            }
            QFrame#exportStatusBar {
                background: transparent;
                border-top: 1px solid #E8E0D9;
            }
        """)

        self.setStyleSheet(self.styleSheet() + r"""
            QWidget#documentCenterPage { background:transparent; color:#F2EBFF; }
            QWidget#documentCenterPage QFrame#documentCommandBar,
            QWidget#documentCenterPage QFrame#documentWorkspace,
            QWidget#documentCenterPage QFrame#documentOutputBar,
            QWidget#documentCenterPage QFrame#previewCard,
            QWidget#documentCenterPage QFrame#exportStatusBar,
            QWidget#documentCenterPage QFrame#uiCard {
                background:#140925; border:1px solid #7337B4; color:#F2EBFF;
            }
            QWidget#documentCenterPage QFrame#documentCommandBar { border-radius:10px; }
            QWidget#documentCenterPage QFrame#documentWorkspace,
            QWidget#documentCenterPage QFrame#previewCard { border-radius:12px; }
            QWidget#documentCenterPage QLabel { background:transparent; color:#E8CDB6; }
            QWidget#documentCenterPage QLabel#cardTitle,
            QWidget#documentCenterPage QLabel#previewTitle,
            QWidget#documentCenterPage QLabel#pageTitle { color:#F8F3FF; font-weight:900; }
            QWidget#documentCenterPage QLabel#cardSubtitle,
            QWidget#documentCenterPage QLabel#dialogHint { color:#D4B399; }
            QWidget#documentCenterPage QLabel#exportIcon {
                background:#2A1248; color:#DFA06A; border:1px solid #7337B4; border-radius:9px;
            }
            QWidget#documentCenterPage QLabel#selectionStatus,
            QWidget#documentCenterPage QLabel#fileChip,
            QWidget#documentCenterPage QLabel#outputPath {
                background:#140925; color:#E7C8AE; border:1px solid #7337B4; border-radius:7px; padding:7px 10px;
            }
            QWidget#documentCenterPage QCheckBox#documentRow {
                background:#160A2B; color:#F4DDC9; border:1px solid #5F3824; border-radius:7px; padding:7px 9px;
            }
            QWidget#documentCenterPage QCheckBox#documentRow:checked {
                background:#201044; color:#F8F3FF; border-color:#B34CFF;
            }
            QWidget#documentCenterPage QPushButton#secondaryAction,
            QWidget#documentCenterPage QPushButton#quietButton,
            QWidget#documentCenterPage QPushButton#topTab {
                background:#21103B; color:#EBDFFF; border:1px solid #7337B4; border-radius:8px; padding:7px 11px;
            }
            QWidget#documentCenterPage QPushButton#activeTopTab,
            QWidget#documentCenterPage QPushButton#heroExport,
            QWidget#documentCenterPage QPushButton#primaryAction {
                background:#8D2CFF; color:#FFFFFF; border:1px solid #B85CFF; border-radius:8px; padding:8px 14px; font-weight:900;
            }
            QWidget#documentCenterPage QTextBrowser#documentPreview,
            QWidget#documentCenterPage QStackedWidget#documentPreviewStack {
                background:#090619; color:#F2DDCA; border:1px solid #7337B4; border-radius:10px;
            }
            QWidget#documentCenterPage QScrollArea#documentToolScroll,
            QWidget#documentCenterPage QScrollArea#documentToolScroll QWidget#qt_scrollarea_viewport { background:#160B07; border:none; }
            QWidget#documentCenterPage QSplitter::handle { background:#7337B4; width:4px; }
            QWidget#documentCenterPage QComboBox, QWidget#documentCenterPage QLineEdit,
            QWidget#documentCenterPage QSpinBox, QWidget#documentCenterPage QListWidget {
                background:#090619; color:#F2EBFF; border:1px solid #7337B4; border-radius:7px; padding:6px;
            }
            QWidget#documentCenterPage QAbstractScrollArea,
            QWidget#documentCenterPage QAbstractItemView,
            QWidget#documentCenterPage QAbstractScrollArea QWidget#qt_scrollarea_viewport,
            QWidget#documentCenterPage QAbstractItemView QWidget#qt_scrollarea_viewport {
                background:#090619; color:#F2EBFF; border-color:#7337B4;
            }
            QWidget#documentCenterPage QScrollBar:vertical { background:#070512; width:11px; margin:0px; border:none; }
            QWidget#documentCenterPage QScrollBar:horizontal { background:#070512; height:11px; margin:0px; border:none; }
            QWidget#documentCenterPage QScrollBar::handle:vertical,
            QWidget#documentCenterPage QScrollBar::handle:horizontal {
                background:#7B36AF; border:1px solid #A94CFF; border-radius:5px; min-height:30px; min-width:30px;
            }
            QWidget#documentCenterPage QScrollBar::add-line,
            QWidget#documentCenterPage QScrollBar::sub-line,
            QWidget#documentCenterPage QScrollBar::add-page,
            QWidget#documentCenterPage QScrollBar::sub-page { background:#070512; border:none; width:0px; height:0px; }
            QWidget#documentCenterPage QAbstractScrollArea::corner { background:#070512; border:none; }
        """)
        self._load_profiles()
        self._load_branding_profiles()
        self._load_filters()
        self.refresh()

    def open_filter_dialog(self) -> None:
        self._load_filters()
        self.filter_dialog.exec()
        self.refresh()

    def open_advanced_dialog(self) -> None:
        self.advanced_dialog.exec()
        self.refresh()

    def clear_filters(self) -> None:
        self.group_list.clearSelection()
        self.match_list.clearSelection()
        self.refresh()

    def toggle_preview(self, visible: bool) -> None:
        self.preview_stack.setVisible(True)
        self.preview_toggle.setChecked(True)
        self.preview_toggle.setText("Vorschau anzeigen")
        self.splitter.setSizes([410, 850])
        if self.last_exported_pdf is not None and self.last_exported_pdf.is_file():
            self._load_pdf_preview(self.last_exported_pdf)
        else:
            self._show_empty_preview()

    def _update_filter_toggle(self, checked: bool) -> None:
        self.filter_toggle.setText("Filter …")

    def _update_advanced_toggle(self, checked: bool) -> None:
        self.advanced_toggle.setText("Druck & Design …")

    def activate(self) -> None:
        self._load_profiles()
        self._load_branding_profiles()
        self._load_filters()
        self.refresh()

    def _load_profiles(self, selected: str | None = None) -> None:
        current = selected or self.profile_combo.currentText()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem("Benutzerdefiniert")
        for name in self.service.document_profiles():
            self.profile_combo.addItem(name)
        index = self.profile_combo.findText(current)
        self.profile_combo.setCurrentIndex(index if index >= 0 else 0)
        self.profile_combo.blockSignals(False)
        self._update_profile_buttons()

    def _load_branding_profiles(self, selected: str | None = None) -> None:
        current = selected or self.branding_profile_combo.currentText()
        self.branding_profile_combo.blockSignals(True)
        self.branding_profile_combo.clear()
        self.branding_profile_combo.addItem("Benutzerdefiniert")
        for name in self.service.branding_profiles():
            self.branding_profile_combo.addItem(name)
        index = self.branding_profile_combo.findText(current)
        self.branding_profile_combo.setCurrentIndex(index if index >= 0 else 0)
        self.branding_profile_combo.blockSignals(False)
        self._update_branding_buttons()
        self._update_color_preview()

    def _branding_changed(self) -> None:
        self._update_color_preview()
        self.refresh()

    def _update_color_preview(self) -> None:
        for label, value in ((self.primary_preview, self.primary_color.text()), (self.accent_preview, self.accent_color.text())):
            color = value.strip()
            if len(color) == 7 and color.startswith("#"):
                label.setStyleSheet(f"background:{color}; border:1px solid #8a7a6d; border-radius:4px;")
            else:
                label.setStyleSheet("background:transparent; border:1px dashed #b91c1c; border-radius:4px;")

    def choose_color(self, target: QLineEdit) -> None:
        color = QColorDialog.getColor()
        if color.isValid():
            target.setText(color.name())

    def apply_branding_profile(self, name: str) -> None:
        if not name or name == "Benutzerdefiniert":
            self._update_branding_buttons()
            return
        profile = self.service.branding_profiles().get(name)
        if profile is None:
            return
        self.organizer_name.setText(profile.organizer_name)
        self.primary_color.setText(profile.primary_color)
        self.accent_color.setText(profile.accent_color)
        self.custom_logo_path.setText(profile.custom_logo_path)
        self._update_branding_buttons()
        self.refresh()

    def save_branding_profile(self) -> None:
        name, accepted = QInputDialog.getText(self, "Designprofil speichern", "Name des neuen Designprofils:")
        if not accepted:
            return
        try:
            profile = BrandingProfile(
                name.strip(), self.organizer_name.text(), self.primary_color.text().strip(),
                self.accent_color.text().strip(), self.custom_logo_path.text().strip(),
            )
            self.service.save_branding_profile(profile)
            self._load_branding_profiles(profile.name)
            QMessageBox.information(self, "Designprofil", f"Das Designprofil „{profile.name}“ wurde gespeichert.")
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Designprofil", str(error))

    def delete_branding_profile(self) -> None:
        name = self.branding_profile_combo.currentText()
        if not name or name == "Benutzerdefiniert":
            return
        try:
            self.service.delete_branding_profile(name)
            self._load_branding_profiles("Benutzerdefiniert")
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Designprofil", str(error))

    def export_branding_profile(self) -> None:
        name = self.branding_profile_combo.currentText()
        if not name or name == "Benutzerdefiniert":
            QMessageBox.information(self, "Designprofil", "Bitte zuerst ein gespeichertes Designprofil auswählen.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Designprofil exportieren (keine PDF-Datei)", f"{name}.mstts-design.json", "MSTTS Designprofil (*.mstts-design.json *.json)")
        if not path:
            return
        try:
            self.service.export_branding_profile(name, Path(path))
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Designprofil", str(error))

    def import_branding_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Designprofil importieren", "", "MSTTS Designprofil (*.json)")
        if not path:
            return
        try:
            profile = self.service.import_branding_profile(Path(path))
            self._load_branding_profiles(profile.name)
            self.apply_branding_profile(profile.name)
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Designprofil", str(error))

    def _update_branding_buttons(self) -> None:
        profile = self.service.branding_profiles().get(self.branding_profile_combo.currentText())
        self.delete_branding_button.setEnabled(profile is not None and not profile.built_in)

    def _print_settings(self) -> PrintSettings:
        return PrintSettings(
            page_size=self.page_size.currentText(),
            orientation=self.orientation.currentData(),
            margin_mm=self.margin_mm.value(),
            show_header=self.header_option.isChecked(),
            show_footer=self.footer_option.isChecked(),
            show_page_numbers=self.page_number_option.isChecked(),
            show_logo=self.logo_option.isChecked(),
            footer_text=self.footer_text.text(),
            organizer_name=self.organizer_name.text(),
            primary_color=self.primary_color.text().strip(),
            accent_color=self.accent_color.text().strip(),
            custom_logo_path=self.custom_logo_path.text().strip(),
        )

    def choose_branding_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Branding-Logo auswählen", "", "Bilddateien (*.png *.jpg *.jpeg *.gif *.svg)"
        )
        if path:
            self.custom_logo_path.setText(path)

    def apply_profile(self, name: str) -> None:
        if not name or name == "Benutzerdefiniert":
            self._update_profile_buttons()
            return
        profile = self.service.document_profiles().get(name)
        if profile is None:
            return
        for key, check in self.document_checks.items():
            check.blockSignals(True)
            check.setChecked(getattr(profile.selection, key))
            check.blockSignals(False)
        self.group_list.clearSelection()
        self.match_list.clearSelection()
        self.page_size.setCurrentText(profile.print_settings.page_size)
        self.orientation.setCurrentIndex(0 if profile.print_settings.orientation == "portrait" else 1)
        self.margin_mm.setValue(profile.print_settings.margin_mm)
        self.header_option.setChecked(profile.print_settings.show_header)
        self.footer_option.setChecked(profile.print_settings.show_footer)
        self.page_number_option.setChecked(profile.print_settings.show_page_numbers)
        self.logo_option.setChecked(profile.print_settings.show_logo)
        self.footer_text.setText(profile.print_settings.footer_text)
        self.organizer_name.setText(profile.print_settings.organizer_name)
        self.primary_color.setText(profile.print_settings.primary_color)
        self.accent_color.setText(profile.print_settings.accent_color)
        self.custom_logo_path.setText(profile.print_settings.custom_logo_path)
        self._update_profile_buttons()
        self.refresh()

    def save_profile(self) -> None:
        name, accepted = QInputDialog.getText(self, "Druckprofil speichern", "Name des neuen Profils:")
        if not accepted:
            return
        try:
            profile = DocumentProfile(name.strip(), self._selection(), self._print_settings())
            self.service.save_document_profile(profile)
            self._load_profiles(profile.name)
            QMessageBox.information(self, "Druckprofil", f"Das Profil „{profile.name}“ wurde gespeichert.")
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Druckprofil", str(error))

    def delete_profile(self) -> None:
        name = self.profile_combo.currentText()
        if not name or name == "Benutzerdefiniert":
            return
        try:
            self.service.delete_document_profile(name)
            self._load_profiles("Benutzerdefiniert")
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Druckprofil", str(error))

    def _update_profile_buttons(self) -> None:
        name = self.profile_combo.currentText()
        profile = self.service.document_profiles().get(name)
        self.delete_profile_button.setEnabled(profile is not None and not profile.built_in)

    def _load_filters(self) -> None:
        try:
            tournament = self.service.require_tournament()
        except ValidationError:
            return
        selected_groups = {item.data(256) for item in self.group_list.selectedItems()}
        selected_matches = {item.data(256) for item in self.match_list.selectedItems()}
        self.group_list.clear()
        self.match_list.clear()
        names = {person.id: person.full_name for person in tournament.people}
        for phase in sorted(tournament.phases, key=lambda value: value.position):
            if phase.phase_type is PhaseType.GROUP_STAGE:
                for group in phase.groups:
                    item = QListWidgetItem(f"{phase.name} – {group.name}")
                    item.setData(256, group.name)
                    self.group_list.addItem(item)
                    item.setSelected(group.name in selected_groups)
            matches = []
            if phase.phase_type is PhaseType.GROUP_STAGE:
                for group in phase.groups:
                    matches.extend((group.name, match) for match in group.matches)
                    if group.playoff_match is not None:
                        matches.append((f"{group.name} Qualifikation", group.playoff_match))
            else:
                matches.extend((phase.name, match) for match in phase.matches)
            for stage, match in matches:
                label = f"{match.scheduled_time or '--:--'} · {stage}: {names.get(match.home_id, 'Offen')} – {names.get(match.away_id, 'Offen')}"
                item = QListWidgetItem(label)
                item.setData(256, str(match.id))
                self.match_list.addItem(item)
                item.setSelected(str(match.id) in selected_matches)

    def _selection(self) -> DocumentSelection:
        return DocumentSelection(
            **{key: check.isChecked() for key, check in self.document_checks.items()},
            group_names=tuple(item.data(256) for item in self.group_list.selectedItems()),
            match_ids=tuple(item.data(256) for item in self.match_list.selectedItems()),
        )

    def select_all(self) -> None:
        for check in self.document_checks.values():
            check.setChecked(True)
        self.group_list.clearSelection()
        self.match_list.clearSelection()
        self.refresh()

    def clear_selection(self) -> None:
        for check in self.document_checks.values():
            check.setChecked(False)
        self.group_list.clearSelection()
        self.match_list.clearSelection()
        self.refresh()

    def refresh(self) -> None:
        try:
            tournament = self.service.require_tournament()
            selection = self._selection()
            settings = self._print_settings()
            if self.last_exported_pdf is None or not self.last_exported_pdf.is_file():
                self._show_empty_preview()
            count = sum(check.isChecked() for check in self.document_checks.values())
            group_count = len(selection.group_names) or sum(len(phase.groups) for phase in tournament.phases)
            match_count = len(selection.match_ids) or sum(
                len(phase.matches) + sum(len(group.matches) + (1 if group.playoff_match else 0) for group in phase.groups)
                for phase in tournament.phases
            )
            orientation = "Hochformat" if settings.orientation == "portrait" else "Querformat"
            self.status.setText(f"Auswahl bereit: {count} Dokumentarten · {group_count} Gruppen · {match_count} Spiele · {settings.page_size} {orientation} · Rand {settings.margin_mm} mm · Kopf/Fuß {'aktiv' if settings.show_header or settings.show_footer else 'aus'}")
        except ValidationError as error:
            self.preview_stack.setCurrentWidget(self.preview)
            self.preview.setPlainText(str(error))
            self.status.setText(str(error))

    def _show_empty_preview(self) -> None:
        """Show a neutral state until MSTTS itself has exported a PDF."""
        self.preview_stack.setCurrentWidget(self.preview)
        self.preview_header.setText("Vorschau: Noch keine MSBTS-PDF exportiert")
        self.preview.setHtml(
            "<div style='text-align:center; padding:70px 28px; color:#CDBBE8;'>"
            "<h3 style='color:#F4EEFF;'>Noch keine Turnierunterlagen erstellt</h3>"
            "<p>Exportieren Sie zuerst eine PDF mit MSBTS. Danach wird genau diese Datei hier angezeigt.</p>"
            "<p><b>Andere PDF-Dateien auf Ihrem Mac werden niemals automatisch geöffnet.</b></p>"
            "</div>"
        )

    def _load_pdf_preview(self, pdf_path: Path) -> None:
        """Load exactly the PDF returned by the latest MSTTS export."""
        pdf_path = pdf_path.resolve()
        if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
            self.last_exported_pdf = None
            self._show_empty_preview()
            return

        self.last_exported_pdf = pdf_path
        self.preview_header.setText(f"Vorschau: {pdf_path.name}")
        if self.pdf_document is None or self.pdf_view is None:
            self.preview_stack.setCurrentWidget(self.preview)
            self.preview.setHtml(
                "<div style='text-align:center; padding:70px 28px; color:#CDBBE8;'>"
                f"<h3 style='color:#F4EEFF;'>{pdf_path.name}</h3>"
                "<p>Die PDF wurde erfolgreich von MSBTS erstellt. Die integrierte Vorschau ist in dieser PySide6-Installation nicht verfügbar.</p>"
                "<p>Öffnen Sie die Datei über <b>Im Finder anzeigen</b>.</p>"
                "</div>"
            )
            return

        self.pdf_document.close()
        self.pdf_document.load(str(pdf_path))
        self.preview_stack.setCurrentWidget(self.pdf_view)

    def export_selection(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Ausgabeordner auswählen")
        if not directory:
            return
        try:
            result = self.service.export_selected_document_package(
                Path(directory), self._selection(),
                create_pdf=self.pdf_option.isChecked(),
                create_word=self.word_option.isChecked(),
                create_excel=self.excel_option.isChecked(),
                create_archive=self.archive_option.isChecked(),
                print_settings=self._print_settings(),
            )
            self.last_output_dir = result.output_dir
            self.output_path_label.setText(str(result.output_dir))
            created_files = [path for path in result.files if path.is_file()]
            requested_suffixes = set()
            if self.pdf_option.isChecked():
                requested_suffixes.add(".pdf")
            if self.word_option.isChecked():
                requested_suffixes.add(".docx")
            if self.excel_option.isChecked():
                requested_suffixes.add(".xlsx")
            created_suffixes = {path.suffix.lower() for path in created_files}
            missing = requested_suffixes - created_suffixes
            if missing:
                raise OSError(
                    "Der Export wurde nicht vollständig erstellt. Es fehlen: "
                    + ", ".join(sorted(missing))
                )
            file_list = "\n".join(f"• {path.name}" for path in created_files)
            self.status.setText(f"Erstellt: {len(created_files)} Dateien in {result.output_dir}")
            self.export_state_label.setText("●  Letzter Export erfolgreich")
            for label, path in zip(self.export_file_labels, created_files[:3]):
                label.setText(f"{path.suffix[1:].upper()}  {path.name}")
                label.setVisible(True)
            exported_pdfs = [path for path in created_files if path.suffix.lower() == ".pdf"]
            if exported_pdfs:
                self._load_pdf_preview(exported_pdfs[0])
            else:
                self.last_exported_pdf = None
                self._show_empty_preview()
            QMessageBox.information(
                self,
                "Dokumente erfolgreich exportiert",
                f"Die Turnierunterlagen wurden erstellt.\n\n{file_list}\n\nOrdner:\n{result.output_dir}",
            )
        except (OSError, ValidationError) as error:
            QMessageBox.critical(self, "Dokumentenexport", str(error))

    def open_last_output(self) -> None:
        if self.last_output_dir is None or not self.last_output_dir.exists():
            QMessageBox.information(self, "Dokumentenzentrale", "Es wurden noch keine Dokumente erstellt.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output_dir.resolve())))
