from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)


@dataclass(frozen=True)
class Colors:
    espresso: str = "#241A15"
    walnut: str = "#5A4033"
    cognac: str = "#A934FF"
    sand: str = "#E9E0D7"
    ivory: str = "#F8F5F1"
    surface: str = "#FFFDFC"
    text: str = "#2F241E"
    muted: str = "#7B6A5F"
    border: str = "#DDD0C4"
    success: str = "#47704B"
    warning: str = "#A2642F"
    danger: str = "#9C433C"


COLORS = Colors()


class SectionHeader(QFrame):
    """Uniform page header used by every main workspace.

    Existing pages can adopt this component without changing their business
    logic.  An optional action widget is placed consistently on the right.
    """

    def __init__(self, title: str, subtitle: str = "", action: QPushButton | None = None) -> None:
        super().__init__()
        self.setObjectName("sectionHeader")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 14)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(16)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(4)

        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        text.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("pageSubtitle")
            subtitle_label.setWordWrap(True)
            text.addWidget(subtitle_label)

        top.addLayout(text, 1)
        if action is not None:
            top.addWidget(action, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)

        divider = QFrame()
        divider.setObjectName("pageHeaderDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(divider)


class _StyledButton(QPushButton):
    object_name = "secondaryButton"

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setObjectName(self.object_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(38)


class PrimaryButton(_StyledButton):
    object_name = "primaryButton"


class SecondaryButton(_StyledButton):
    object_name = "secondaryButton"


class DangerButton(_StyledButton):
    object_name = "dangerButton"


class SurfaceCard(QFrame):
    """Standard content card with optional heading and body layout."""

    def __init__(self, title: str = "", subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("surfaceCard")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 18)
        self.body.setSpacing(12)

        if title:
            heading = QLabel(title)
            heading.setObjectName("cardHeading")
            self.body.addWidget(heading)
        if subtitle:
            description = QLabel(subtitle)
            description.setObjectName("cardDescription")
            description.setWordWrap(True)
            self.body.addWidget(description)
