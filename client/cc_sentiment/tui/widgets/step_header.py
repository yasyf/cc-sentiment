from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Static

from cc_sentiment.tui.setup_state import Tone


class StepHeader(Vertical):
    DEFAULT_CSS = """
    StepHeader {
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
    }
    StepHeader > .step-title {
        width: 100%;
        text-style: bold;
        color: $text;
    }
    StepHeader > .step-explainer {
        width: 100%;
    }
    """

    def __init__(
        self,
        title: str,
        explainer: str | None,
        **kwargs,
    ) -> None:
        super().__init__(
            Static(title, classes="step-title"),
            *(
                [Static(explainer, classes="step-explainer muted")]
                if explainer is not None
                else []
            ),
            **kwargs,
        )

    def set_content(self, title: str, explainer: str, tone: Tone | None = None) -> None:
        title_widget = self.query_one(".step-title", Static)
        for member in Tone:
            title_widget.remove_class(member.value)
        if tone is not None:
            title_widget.add_class(tone.value)
        title_widget.update(title)
        self.query_one(".step-explainer", Static).update(explainer)
