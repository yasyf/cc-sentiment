from __future__ import annotations

import pytest
from textual.widgets import Button, Input

from cc_sentiment.onboarding import Stage, State as GlobalState, VerifyTimeout
from cc_sentiment.onboarding.state import VerifyErrorCode
from cc_sentiment.onboarding.ui.screens import VerifyTroubleScreen

from .conftest import has_text, mounted


def gs_verify_trouble(error_code: VerifyErrorCode = "unknown") -> GlobalState:
    return GlobalState(
        stage=Stage.TROUBLE,
        trouble=VerifyTimeout(error_code=error_code),
    )


class TestVerifyTroubleScreen:
    """Strict codification of verify_trouble.py — restart-only; mapped error message."""

    async def test_title(self):
        async with mounted(VerifyTroubleScreen, gs_verify_trouble()) as pilot:
            assert (
                str(pilot.app.screen.query_one("#title").renderable)
                == "We couldn't verify your signature"
            )

    async def test_restart_button(self):
        async with mounted(VerifyTroubleScreen, gs_verify_trouble()) as pilot:
            btn = pilot.app.screen.query_one("#restart-btn", Button)
            assert btn.label.plain == "Restart setup"

    async def test_only_one_action(self):
        # Plan: "NO secondary actions, NO 'keep watching'".
        async with mounted(VerifyTroubleScreen, gs_verify_trouble()) as pilot:
            assert len(pilot.app.screen.query(Button)) == 1

    async def test_no_inputs(self):
        async with mounted(VerifyTroubleScreen, gs_verify_trouble()) as pilot:
            assert not pilot.app.screen.query(Input)

    # ─── Server error code mapping ───────────────────────────────────────

    async def test_message_key_not_found(self):
        async with mounted(VerifyTroubleScreen, gs_verify_trouble("key-not-found")) as pilot:
            msg = str(pilot.app.screen.query_one("#message").renderable)
            assert "couldn't see your published signature" in msg

    async def test_message_signature_failed(self):
        async with mounted(VerifyTroubleScreen, gs_verify_trouble("signature-failed")) as pilot:
            msg = str(pilot.app.screen.query_one("#message").renderable)
            assert "signature wasn't accepted" in msg

    async def test_message_rate_limited(self):
        async with mounted(VerifyTroubleScreen, gs_verify_trouble("rate-limited")) as pilot:
            msg = str(pilot.app.screen.query_one("#message").renderable)
            assert "busy" in msg
            assert "Wait a minute" in msg

    async def test_message_unknown_fallback(self):
        async with mounted(VerifyTroubleScreen, gs_verify_trouble("unknown")) as pilot:
            msg = str(pilot.app.screen.query_one("#message").renderable)
            assert "don't recognize" in msg

    # ─── Forbidden ───────────────────────────────────────────────────────

    async def test_no_raw_error_dump(self):
        async with mounted(VerifyTroubleScreen, gs_verify_trouble()) as pilot:
            assert not has_text(pilot, "Traceback")
            assert not has_text(pilot, "stack trace")

    @pytest.mark.parametrize(
        "error_code",
        ["key-not-found", "signature-failed", "rate-limited", "unknown"],
    )
    async def test_no_internal_codes_shown(self, error_code: VerifyErrorCode):
        # Plan: "no internal codes shown to the user". The raw kebab-case
        # identifier never appears in the rendered card.
        async with mounted(VerifyTroubleScreen, gs_verify_trouble(error_code)) as pilot:
            assert not has_text(pilot, error_code)
            assert not has_text(pilot, "code:")

    async def test_subhint_appears(self):
        async with mounted(VerifyTroubleScreen, gs_verify_trouble()) as pilot:
            assert has_text(pilot, "Try again with a fresh setup")

    async def test_no_docs_or_support_link(self):
        # Plan: "No links to docs / support inside this card".
        async with mounted(VerifyTroubleScreen, gs_verify_trouble()) as pilot:
            assert not has_text(pilot, "docs")
            assert not has_text(pilot, "support")
