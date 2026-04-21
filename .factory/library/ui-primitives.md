# UI Primitives (Setup Flow)

Shared widget primitives the setup flow redesign introduces. One concept per file under `client/cc_sentiment/tui/widgets/`.

## `StepHeader(title: str, explainer: str | None)`

Vertical container. Bold `$text` title (`class="step-title"`) + single-line `$text-muted` explainer (`class="step-explainer"`). Consistent 1-line bottom margin. Replaces the per-step `.step-title` + `.status` `Label` pair.

## `StepBody(*children)`

`Vertical` container. CSS establishes body rhythm:
- `height: auto`
- `margin: 0 0 1 0`
- `& > Input { margin: 0 0 1 0 }`
- `& > RadioSet { margin: 0; max-height: 12; overflow-y: auto }`
- `& > DataTable { max-height: 12 }`

Establishes consistent spacing without every step redeclaring margins.

## `StepActions(*buttons, primary: Button)`

Subclass of `Horizontal` (NOT the existing `ButtonRow`). CSS:
- `width: 100%`
- `height: auto`
- `margin: 1 0 0 0`
- `align-horizontal: right`
- A leading `Spacer` absorbs slack.

Compose order (left → right): `[Destructive, Secondary..., Primary]`. API enforces **exactly one** `variant="primary"` button, always rightmost. Enter activates it (via dialog-level `BINDINGS`); Escape cancels.

The existing `ButtonRow` widget is deprecated in setup-flow code (may stay for other screens).

## `KeyPreview(text: str)`

Scrollable monospace panel for public key text.
- `max-height: 6`
- `overflow-y: auto`
- `border: round $panel-lighten-2`
- `background: $boost`
- `padding: 0 1`

Replaces the hand-rolled `pub_text[:200]+"..."` truncation. Shows the full key, scrollable.

## `PendingStatus(label_reactive: reactive[str])`

Horizontal container: Textual `LoadingIndicator` + muted `Static`. Used on `loading` step and on the `Pending` end-state.

On the Pending end-state, the label is driven by a reactive that updates every second with elapsed time formatted as `Waiting for your key to propagate… 0:42`. The elapsed counter MUST use `time.monotonic()` (not wall clock) so system-clock skew does not poison it.

## Global CSS palette (add to `Dialog.DEFAULT_CSS` or similar shared scope)

```css
.muted   { color: $text-muted; }
.success { color: $success; }
.warning { color: $warning; }
.error   { color: $error; }
.code {
    background: $boost;
    border: round $panel-lighten-2;
    padding: 0 1;
}
```

All semantic color in setup code goes through these classes. Inline `[red]` / `[yellow]` / `[green]` Rich markup is DEPRECATED for setup-flow status — use classes on `Static`/`Label`. Rich markup is still fine for emphasis spans inside copy (`[b]`, `[dim]`).

## Copy hierarchy (every step)

1. **Title** — `StepHeader` title; bold; `$text`; ≤ 60 chars.
2. **Explainer** — `StepHeader` muted single-line; ≤ 90 chars; wraps cleanly at 50 cols.
3. **Body** — inputs / radios / tables. NO re-explanation inside.
4. **Inline status** — muted / success / error `Label`; reserved for runtime feedback (`Validating alice…`).
5. **Actions** — `StepActions` row.

Multi-sentence explainers and duplicate reassurance are banned. FAQ prose, if kept on the Done step, lives AFTER the buttons under a `Rule`, class `.muted`, max 2 lines.

## Button contract

- Exactly one `variant="primary"` per step, always rightmost.
- Secondary: `variant="default"`.
- Destructive: `variant="error"`, far left.
- Primary tab-order last (= focus lands on primary when tabbing forward).
- Enter → activate primary. Escape → `action_cancel`.
- No `Button` may live outside a `StepActions` on any setup step (enforced by the state-machine compose methods).

## Per-step primaries (post-redesign)

| Step | Primary | Secondary | Destructive |
|------|---------|-----------|-------------|
| loading | (none — non-interactive) | — | — |
| username | `Next` | `I don't use GitHub` | — |
| discovery | `Next` | `Back` | — |
| remote (when shown) | `Link my key` | `Skip` | `Back` |
| link | `Link my key` | `Back` | — |
| done:verified | `Contribute my stats` | — | — |
| done:pending | `Retry now` | `Exit, continue later` | — |
| done:failed | `Retry` | `Exit` | — |
