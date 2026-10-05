CREAM = "#F5EFE3"
IVORY = "#FFFDF8"
BROWN = "#68472F"
BROWN_DARK = "#3E2D20"
BROWN_MID = "#8A6748"
GOLD = "#C9A66B"
SAND = "#D8C7AD"


def spectator_palette(mode: str = "light") -> dict[str, str]:
    """Return the projector palette for the requested presentation mode."""
    if mode == "dark":
        return {
            "background": "#2B1D15",
            "panel": "#3A281D",
            "text": "#FFF8EC",
            "muted": "#E5D3B7",
            "accent": "#D8BE8A",
            "border": "#A67C52",
            "winner": "#F1D79A",
        }
    return {
        "background": CREAM,
        "panel": IVORY,
        "text": BROWN_DARK,
        "muted": BROWN_MID,
        "accent": GOLD,
        "border": SAND,
        "winner": BROWN,
    }
