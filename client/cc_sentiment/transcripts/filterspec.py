"""cc-sentiment's event-filter and score-pipeline policy, composed from
cc-transcript primitives. Both the events kept for scoring and the deterministic
score adjustments are declared here, not baked into the shared library."""

from __future__ import annotations

from cc_transcript import (
    ASSISTANTS,
    USERS,
    FilterSpec,
    build_spec,
    drop_compacted,
    drop_empty,
    drop_entrypoints,
    drop_junk,
    drop_sidechain,
    drop_synthetic,
    keep_only,
)
from cc_transcript.domains.sentiment import (
    ScoreSpec,
    build_score_spec,
    clamp_positive,
    clamp_resume,
    demote_mild_irritation,
    flag_frustration,
)


SENTIMENT_SPEC: FilterSpec = build_spec(
    keep_only("user", "assistant"),
    drop_synthetic(),
    drop_empty(only_from=ASSISTANTS),
    drop_empty(only_from=USERS),
    drop_junk("structural", "interrupt", "stop_hook"),
    drop_sidechain(except_assistants=True),
    drop_compacted(),
    drop_entrypoints({"sdk-cli"}),
)

SENTIMENT_SCORE_SPEC: ScoreSpec = build_score_spec(
    flag_frustration(),
    clamp_positive(),
    demote_mild_irritation(),
    clamp_resume(),
)
