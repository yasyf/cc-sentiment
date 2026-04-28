# DELETE_AFTER_SCREENS: This whole package is the old multi-stage SetupScreen
# system. Once cc_sentiment/onboarding/ui/screens/ is wired into a runner that
# replaces SetupScreen in app.py, delete this directory.
from __future__ import annotations

from cc_sentiment.tui.legacy.setup import SetupScreen
from cc_sentiment.tui.legacy.setup_state import SetupStage

__all__ = ["SetupScreen", "SetupStage"]
