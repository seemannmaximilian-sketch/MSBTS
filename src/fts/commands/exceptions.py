class CommandError(RuntimeError):
    """Base error for command execution failures."""


class NothingToUndoError(CommandError):
    """Raised when undo is requested with an empty undo stack."""


class NothingToRedoError(CommandError):
    """Raised when redo is requested with an empty redo stack."""
