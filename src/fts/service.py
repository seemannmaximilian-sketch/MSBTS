"""Backward-compatible import for older FTS integrations.

New code should import :class:`fts.engine.TournamentEngine`.
"""

from fts.engine import (
    PlayerImportPreview,
    QualificationResult,
    TournamentCheckIssue,
    TournamentEngine,
)

TournamentService = TournamentEngine

__all__ = [
    "PlayerImportPreview",
    "QualificationResult",
    "TournamentCheckIssue",
    "TournamentEngine",
    "TournamentService",
]
