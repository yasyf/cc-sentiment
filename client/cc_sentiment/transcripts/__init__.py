from __future__ import annotations

from cc_transcript import discover

from .backend import ParsedTranscript
from .bucketer import BUCKET_MINUTES, MIN_USER_TURNS_PER_SESSION, ConversationBucketer
from .parser import (
    CLAUDE_PROJECTS_DIR,
    JUNK_USER_MESSAGE_RE,
    TranscriptParser,
)
