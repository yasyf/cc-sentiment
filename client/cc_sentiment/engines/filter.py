"""Frustration introspection for snippets and highlighting.

cc-sentiment owns this (it's a highlighting/snippet concern, not core scoring):
the compiled frustration pattern and the helper that picks which user message in a
bucket triggered it, built over cc-transcript's shared ``FRUSTRATION_GROUPS`` data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cc_transcript.filterspec import FRUSTRATION_GROUPS, compile_groups

if TYPE_CHECKING:
    from cc_transcript.sentiment import ConversationBucket


FRUSTRATION_PATTERN = compile_groups(FRUSTRATION_GROUPS, True)


def matches_frustration(text: str) -> bool:
    return FRUSTRATION_PATTERN.search(text) is not None


def matched_user_message(bucket: ConversationBucket) -> str | None:
    return next((m.content for m in bucket.messages if m.role == "user" and matches_frustration(m.content)), None)
