# DELETE_AFTER_SCREENS: Entire cc_sentiment/tui/screens/setup/ tree is the
# old multi-stage SetupScreen + mixins. Once the new FSM-driven onboarding
# screens (cc_sentiment/onboarding/ui/screens/) are wired into a runner that
# replaces SetupScreen in app.py, delete this whole directory plus
# cc_sentiment/tui/setup_helpers.py and cc_sentiment/tui/setup_state.py
# (keep only the bits — Tone, Browser, Clipboard probes — that still have
# non-onboarding consumers).
from __future__ import annotations

from cc_sentiment.tui.legacy.setup.screen import SetupScreen
from cc_sentiment.tui.legacy.setup_state import SetupStage

__all__ = ["SetupScreen", "SetupStage"]
