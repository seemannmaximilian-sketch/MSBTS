from __future__ import annotations


def recommended_window_dimensions(available_width: int, available_height: int) -> tuple[int, int]:
    """Return a window size that fits common 13-inch notebook work areas.

    The dimensions always stay inside the available desktop geometry, including
    smaller scaled displays and macOS menu/dock reservations.
    """
    usable_width = max(760, available_width - 40)
    usable_height = max(520, available_height - 64)
    return min(1180, usable_width), min(760, usable_height)


def notebook_layout_columns(content_width: int) -> int:
    """Number of columns for card/button grids at the current content width."""
    if content_width < 720:
        return 1
    if content_width < 1040:
        return 2
    return 3
