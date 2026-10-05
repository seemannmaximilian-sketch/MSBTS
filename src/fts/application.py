import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .gui.main_window import MainWindow
from .version import DISPLAY_NAME, PRODUCT_NAME, VERSION
from .repository import SQLiteTournamentRepository
from .config import default_database_path


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(PRODUCT_NAME)
    app.setApplicationDisplayName(DISPLAY_NAME)
    app.setApplicationVersion(VERSION)
    icon_path = Path(__file__).resolve().parent / "gui" / "assets" / "fts_icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    repository = SQLiteTournamentRepository(default_database_path())
    window = MainWindow(repository)
    window.show()
    return app.exec()
