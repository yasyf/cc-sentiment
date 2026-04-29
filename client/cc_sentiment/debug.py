from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from cc_sentiment.models import (
    BucketIndex,
    BucketKey,
    ConversationBucket,
    SentimentRecord,
    SessionId,
)
from cc_sentiment.repo import Repository
from cc_sentiment.transcripts import TranscriptDiscovery, TranscriptParser


class BucketHash:
    HASH_LEN: ClassVar[int] = 8

    @classmethod
    def of(cls, session_id: SessionId, bucket_index: BucketIndex) -> str:
        return hashlib.sha256(
            f"{session_id}:{bucket_index}".encode()
        ).hexdigest()[: cls.HASH_LEN]

    @classmethod
    def of_bucket(cls, bucket: ConversationBucket) -> str:
        return cls.of(bucket.session_id, bucket.bucket_index)

    @classmethod
    def of_record(cls, record: SentimentRecord) -> str:
        return cls.of(record.conversation_id, record.bucket_index)


@dataclass(frozen=True)
class BucketLookupResult:
    record: SentimentRecord
    bucket: ConversationBucket
    transcript_path: Path


class BucketLookup:
    @classmethod
    async def find(cls, repo: Repository, prefix: str) -> BucketLookupResult | None:
        prefix = prefix.lower().strip().lstrip("#")
        records = repo.all_records()
        match = next(
            (r for r in records if BucketHash.of_record(r).startswith(prefix)),
            None,
        )
        if match is None:
            return None
        target_session = match.conversation_id
        target_idx = match.bucket_index
        for path in TranscriptDiscovery.find_transcripts():
            mtime = TranscriptDiscovery.transcript_mtime(path)
            async for parsed in TranscriptParser.stream_transcripts([(path, mtime)]):
                bucket = cls.locate_bucket(parsed.messages, target_session, target_idx)
                if bucket is not None:
                    return BucketLookupResult(
                        record=match,
                        bucket=bucket,
                        transcript_path=parsed.path,
                    )
        return None

    @staticmethod
    def locate_bucket(
        messages: tuple, target_session: SessionId, target_idx: BucketIndex
    ) -> ConversationBucket | None:
        from cc_sentiment.transcripts import ConversationBucketer
        return next(
            (
                b
                for b in ConversationBucketer.bucket_messages(list(messages))
                if b.session_id == target_session and b.bucket_index == target_idx
            ),
            None,
        )

    @staticmethod
    def format(result: BucketLookupResult) -> str:
        h = BucketHash.of_record(result.record)
        lines = [
            f"# {h}  score={int(result.record.sentiment_score)}  {result.record.time.isoformat()}",
            f"  session={result.record.conversation_id}  bucket={result.record.bucket_index}",
            f"  path={result.transcript_path}",
            f"  cc_version={result.record.cc_version}  client_version={result.record.client_version}",
            "",
        ]
        for msg in result.bucket.messages:
            tag = "USER" if msg.role == "user" else "AI  "
            ts = msg.timestamp.strftime("%H:%M:%S")
            content = msg.content.replace("\n", " ⏎ ")
            lines.append(f"  [{ts}] {tag}: {content}")
        return "\n".join(lines)


__all__ = [
    "BucketHash",
    "BucketLookup",
    "BucketLookupResult",
    "BucketKey",
]
