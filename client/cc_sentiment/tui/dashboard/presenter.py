from __future__ import annotations

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
                self._update_status(
                    "[$success]All set. No conversations yet. Come back after using Claude Code.[/] "
                    "[dim]Press O to open aggregate stats.[/]"
                )
                self._maybe_prewarm()
            case IdleCaughtUp(total_buckets=b, total_sessions=s, total_files=f):
                self.view.show_stats(b, s, f)
                self._update_status(
                    f"[$success]All caught up.[/] "
                    f"{s} chat{'s' if s != 1 else ''}, "
                    f"{b} moment{'s' if b != 1 else ''} scored. "
                    f"[dim]Press R to rescan, O to open aggregate stats.[/]"
                )
                self._maybe_prewarm()
            case IdleAfterUpload(total_buckets=b, total_sessions=s, total_files=f):
                self.view.show_stats(b, s, f)
                self._update_status(self._uploaded_status_text())
                self._maybe_prewarm()
            case Error(message=m):
                self._update_status(m)
            case RescanConfirm():
                self._update_status(
                    "[$warning]Press R again within 5s to clear all state and rescan from scratch.[/]"
                )

    def _uploaded_status_text(self) -> str:
        polling = self._debug_state.card_stopped is None
        suffix = (
            "[dim]Building your shareable card...[/]"
            if polling
            else "[dim]Press O to open aggregate stats.[/]"
        )
        return (
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
