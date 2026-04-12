from __future__ import annotations

import copy
import json
import re

from client.models import (
    ConversationBucket,
    SentimentScore,
    DEFAULT_MODEL_REPO,
)
from client.patches import apply_kv_cache_patch

SYSTEM_PROMPT = """You are a sentiment classifier for developer-AI interactions. Given a conversation between a developer and an AI coding assistant, rate the developer's sentiment on a 1-5 scale:

1 - Deeply frustrated: angry, giving up, repeated failures, expressions of disgust
2 - Annoyed: things aren't working, visible irritation, corrections needed
3 - Neutral: transactional, neither positive nor negative, routine work
4 - Satisfied: things are working, productive session, mild approval
5 - Delighted: impressed, grateful, flow state, praising the AI

Focus ONLY on the developer's messages and tone. Ignore the AI's messages except as context for what the developer is reacting to.

Respond with exactly this JSON format, nothing else:
{"score": <1-5>, "reason": "<10 words max>"}"""


class SentimentClassifier:
    def __init__(self, model_repo: str = DEFAULT_MODEL_REPO) -> None:
        apply_kv_cache_patch()

        from mlx_lm import load
        from mlx_lm.models.cache import make_prompt_cache

        self.model, self.tokenizer = load(model_repo)
        self.system_cache = make_prompt_cache(self.model)
        self._warm_system_cache()

    def _warm_system_cache(self) -> None:
        import mlx.core as mx
        from mlx_lm.generate import generate_step

        system_tokens = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": SYSTEM_PROMPT}],
            add_generation_prompt=False,
            tokenize=True,
        )
        prompt_array = mx.array(system_tokens)
        for _ in generate_step(
            prompt_array, self.model, max_tokens=0, prompt_cache=self.system_cache
        ):
            pass

    @staticmethod
    def format_conversation(bucket: ConversationBucket) -> str:
        lines = []
        for msg in bucket.messages:
            prefix = "DEVELOPER" if msg.role == "user" else "AI"
            lines.append(f"{prefix}: {msg.content}")
        return "\n".join(lines)

    @staticmethod
    def extract_score(response: str) -> SentimentScore:
        try:
            data = json.loads(response)
            score = int(data["score"])
        except (json.JSONDecodeError, KeyError, ValueError):
            match = re.search(r'"score"\s*:\s*(\d)', response)
            score = int(match.group(1)) if match else 3
        assert 1 <= score <= 5
        return SentimentScore(score)

    def score_bucket(self, bucket: ConversationBucket) -> SentimentScore:
        from mlx_lm import generate

        conversation_text = self.format_conversation(bucket)
        prompt_text = f"CONVERSATION:\n{conversation_text}"
        cache = copy.deepcopy(self.system_cache)

        continuation_tokens = self.tokenizer.encode(
            prompt_text, add_special_tokens=False
        )
        result = generate(
            self.model,
            self.tokenizer,
            prompt=continuation_tokens,
            max_tokens=100,
            temp=0.0,
            prompt_cache=cache,
        )
        return self.extract_score(result)

    def score_buckets(self, buckets: list[ConversationBucket]) -> list[SentimentScore]:
        return [self.score_bucket(bucket) for bucket in buckets]
