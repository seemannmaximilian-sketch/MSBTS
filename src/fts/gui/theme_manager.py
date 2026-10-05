from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor


@dataclass(frozen=True)
class ThemePreset:
    key: str
    name: str
    primary: str


THEME_PRESETS: tuple[ThemePreset, ...] = (
    ThemePreset("purple", "MSBTS Lila / Neon", "#A934FF"),
    ThemePreset("violet", "Violett", "#7A4DFF"),
    ThemePreset("blue", "Blau", "#2F6FA3"),
    ThemePreset("green", "Grün", "#3F7D58"),
    ThemePreset("red", "Rot", "#A64242"),
    ThemePreset("orange", "Orange", "#B428FF"),
    ThemePreset("teal", "Petrol", "#267A78"),
    ThemePreset("black_gold", "Schwarz / Gold", "#B58A2D"),
)

_HEX_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}")


def _is_brand_brown(color: QColor) -> bool:
    """Return True for the brown/bronze colors in the base stylesheet.

    Neutral whites, greys and semantic red/green status colors are retained.
    """
    hue = color.hslHueF()
    saturation = color.hslSaturationF()
    lightness = color.lightnessF()
    if hue < 0 or saturation < 0.08:
        return False
    degrees = hue * 360.0
    return 8.0 <= degrees <= 48.0 and 0.10 <= lightness <= 0.93


def _recolor(hex_color: str, primary: QColor) -> str:
    source = QColor(hex_color)
    if not source.isValid() or not _is_brand_brown(source):
        return hex_color

    # Preserve the carefully tuned contrast of the original stylesheet while
    # replacing its brown hue with the selected club color.
    target_hue = primary.hslHueF()
    if target_hue < 0:
        return hex_color
    source_saturation = source.hslSaturationF()
    primary_saturation = primary.hslSaturationF()
    saturation = max(0.10, min(0.92, source_saturation * 0.62 + primary_saturation * 0.58))
    lightness = source.lightnessF()
    result = QColor.fromHslF(target_hue, saturation, lightness, source.alphaF())
    return result.name(QColor.NameFormat.HexRgb)


def build_stylesheet(base_path: Path, primary_hex: str) -> str:
    text = base_path.read_text(encoding="utf-8")
    primary = QColor(primary_hex)
    if not primary.isValid():
        primary = QColor("#A934FF")
    return _HEX_PATTERN.sub(lambda match: _recolor(match.group(0), primary), text)


def preset_by_key(key: str) -> ThemePreset:
    return next((preset for preset in THEME_PRESETS if preset.key == key), THEME_PRESETS[0])


def stored_theme() -> tuple[str, str]:
    settings = QSettings()
    key = str(settings.value("appearance/theme_key", "purple"))
    custom = str(settings.value("appearance/custom_primary", "#A934FF"))
    if key == "custom" and QColor(custom).isValid():
        return key, custom
    preset = preset_by_key(key)
    return preset.key, preset.primary


def save_theme(key: str, primary_hex: str) -> None:
    settings = QSettings()
    settings.setValue("appearance/theme_key", key)
    settings.setValue("appearance/custom_primary", primary_hex)
    settings.sync()
