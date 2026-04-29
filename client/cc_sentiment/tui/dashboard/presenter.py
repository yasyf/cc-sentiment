from __future__ import annotations

from cc_sentiment.engines.protocol import DEFAULT_MODEL
from cc_sentiment.upload import DASHBOARD_URL

from cc_sentiment.tui.dashboard.stages import (
    Authenticating,
    Booting,
    Discovering,
    Error,
    IdleAfterUpload,
    IdleCaughtUp,
    IdleEmpty,
    RescanConfirm,
    Scoring,
    Stage,
    Uploading,
)

__all__ = ["DashboardStagePresenter"]


AUTO_SWAP_HINT = (
    " [dim]Using Claude this run. Free up RAM and rerun to score locally.[/]"
)


class DashboardStagePresenter:
    def watch_stage(self, stage: Stage) -> None:
        if self.view is None:
            return
        if isinstance(stage, (Uploading, IdleEmpty, IdleCaughtUp, IdleAfterUpload)):
            self.view.hide_moments()
        if isinstance(stage, (IdleEmpty, IdleCaughtUp, IdleAfterUpload)):
            self.view.activate_cta()
        match stage:
            case Booting():
                self._update_status("[dim]Starting up...[/]")
            case Authenticating():
                self._update_status("[dim]Connecting to sentiments.cc...[/]")
            case Discovering():
                self._update_status("[dim]Looking for new conversations...[/]")
            case Scoring():
                self._update_status("")
            case Uploading():
                self.view.update_upload(self._upload)
                self._update_status("[dim]Done scoring. Uploading to sentiments.cc...[/]")
            case IdleEmpty():
                self.view.show_stats(0, 0, 0)
                self._update_status(self._with_swap_hint(
                    "[$success]All set. No conversations yet. Come back after using Claude Code.[/] "
                    "[dim]Press O to open aggregate stats.[/]"
                ))
                self._kick_off_model_load_if_needed()
            case IdleCaughtUp(total_buckets=b, total_sessions=s, total_files=f):
                self.view.show_stats(b, s, f)
                self._update_status(self._with_swap_hint(
                    f"[$success]All caught up.[/] "
                    f"{s} chat{'s' if s != 1 else ''}, "
                    f"{b} moment{'s' if b != 1 else ''} scored. "
                    f"[dim]Press R to rescan, O to open aggregate stats.[/]"
                ))
                self._kick_off_model_load_if_needed()
            case IdleAfterUpload(total_buckets=b, total_sessions=s, total_files=f):
                self.view.show_stats(b, s, f)
                self._update_status(self._uploaded_status_text())
                self._kick_off_model_load_if_needed()
            case Error(message=m):
                self._update_status(m)
            case RescanConfirm():
                self._update_status(
                    "[$warning]Press R again within 5s to clear all state and rescan from scratch.[/]"
                )

    def _with_swap_hint(self, text: str) -> str:
        return text + AUTO_SWAP_HINT if self._auto_swapped_to_claude else text

    def _kick_off_model_load_if_needed(self) -> None:
        if self.engine != "mlx":
            return
        self.run_worker(
            self._model_cache.ensure_started(self.model_repo or DEFAULT_MODEL),
            name="model-load", group="model-load", exclusive=True, exit_on_error=False,
        )

    def _uploaded_status_text(self) -> str:
        suffix = (
            "[dim]Building your shareable card...[/]"
            if self._debug_state.card_stopped is None
            else "[dim]Press O to open aggregate stats.[/]"
        )
        return self._with_swap_hint(
            "[$success]Uploaded to[/] "
            f"[link='{DASHBOARD_URL}'][b]sentiments.cc[/b][/link]. "
            f"{suffix}"
        )

    def _update_status(self, text: str) -> None:
        self.status_text = text
        if self.view is not None:
            self.view.update_status(text)

    def _append_status(self, addition: str) -> None:
        self._update_status(f"{self.status_text}\n{addition}".strip())
