from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

from cc_sentiment.models import (
    AssistantMessage,
    BucketIndex,
    ConversationBucket,
    SentimentScore,
    SessionId,
    TranscriptMessage,
    UserMessage,
)

LABELED_CLAUDE_MODEL = "claude-sonnet-4-20250514"


class LabeledBucket(NamedTuple):
    bucket: ConversationBucket
    expected_score: SentimentScore


def _message(session_id: SessionId, uuid: str, ts: datetime, role: str, content: str) -> TranscriptMessage:
    match role:
        case "user":
            return UserMessage(
                content=content,
                timestamp=ts,
                session_id=session_id,
                uuid=uuid,
                tool_names=(),
                thinking_chars=0,
                cc_version="",
            )
        case "assistant":
            return AssistantMessage(
                content=content,
                timestamp=ts,
                session_id=session_id,
                uuid=uuid,
                tool_names=(),
                thinking_chars=0,
                cc_version="",
                claude_model=LABELED_CLAUDE_MODEL,
            )
        case _:
            raise ValueError(f"unknown role: {role}")


def _bucket(label: str, index: int, messages: list[tuple[str, str]]) -> ConversationBucket:
    session_id = SessionId(f"labeled-{label}-{index}")
    return ConversationBucket(
        session_id=session_id,
        bucket_index=BucketIndex(0),
        bucket_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        messages=tuple(
            _message(
                session_id,
                f"labeled-{label}-{index}-{i}",
                datetime(2026, 1, 1, 0, 0, i, tzinfo=timezone.utc),
                role,
                content,
            )
            for i, (role, content) in enumerate(messages)
        ),
    )


SCORE_1_CONVERSATIONS: list[list[tuple[str, str]]] = [
    [
        ("user", "Fix the login page"),
        ("assistant", "I'll update the login component to fix the styling issue."),
        ("user", "That's completely wrong. I said fix it, not break it more."),
        ("assistant", "I apologize, let me revert that change and try a different approach."),
        ("user", "Fuck this. You keep making it worse. I give up."),
    ],
    [
        ("user", "Why did you delete my database migration?"),
        ("assistant", "I removed the migration file as part of the cleanup."),
        ("user", "That was NOT part of the cleanup. That migration was critical. What the fuck."),
        ("assistant", "I'm sorry, let me restore it immediately."),
        ("user", "This is the third time you've destroyed my work. I'm done."),
    ],
    [
        ("user", "Implement the payment flow"),
        ("assistant", "Here's the payment processing implementation."),
        ("user", "This is completely wrong. None of this follows our API spec."),
        ("assistant", "Let me review the API spec and redo this."),
        ("user", "You already had the spec. This is useless garbage. Giving up on this approach entirely."),
    ],
    [
        ("user", "The tests are failing, fix them"),
        ("assistant", "I've updated the test assertions to match the new output."),
        ("user", "You just changed the assertions to pass? That's not fixing them, that's hiding the bug!"),
        ("assistant", "You're right, I should fix the actual code. Let me look at the root cause."),
        ("user", "I've been telling you this for 20 minutes. This is fucking horrible."),
    ],
    [
        ("user", "Deploy to staging"),
        ("assistant", "I've pushed the changes and triggered the deployment pipeline."),
        ("user", "It crashed immediately. Did you even test this?"),
        ("assistant", "Let me check the logs."),
        ("user", "The logs show a null pointer in the code you wrote. Every single thing you touch breaks. I'm so frustrated."),
    ],
    [
        ("user", "Refactor the auth middleware"),
        ("assistant", "I've rewritten the middleware with the new pattern."),
        ("user", "This doesn't compile. There are 47 type errors."),
        ("assistant", "I see, let me fix the type errors."),
        ("user", "WTF, you introduced even more errors. This is a disaster. Piece of shit code."),
    ],
    [
        ("user", "Add rate limiting to the API"),
        ("assistant", "I've added rate limiting middleware with a 100 req/min limit."),
        ("user", "That's not what I asked for. I said per-user rate limiting."),
        ("assistant", "Let me update it to track rates per authenticated user."),
        ("user", "It's still wrong. I've explained this three times now. No, that's not right either. Screw this."),
    ],
    [
        ("user", "The app is crashing in production"),
        ("assistant", "I see the issue - there's a memory leak in the connection pool."),
        ("user", "That's not what the stack trace shows at all. Did you even read it?"),
        ("assistant", "Let me re-examine the stack trace more carefully."),
        ("user", "You misunderstood again. I'm losing money every minute this is down and you can't even read a stack trace."),
    ],
    [
        ("user", "Write unit tests for the payment module"),
        ("assistant", "Here are the unit tests for the payment processing functions."),
        ("user", "These tests don't test anything. They're all just assertTrue(True)."),
        ("assistant", "You're right, let me write meaningful assertions."),
        ("user", "The new tests have hardcoded wrong values. You clearly don't understand the code at all. This is terrible."),
    ],
    [
        ("user", "Set up the CI pipeline"),
        ("assistant", "I've created the GitHub Actions workflow."),
        ("user", "It doesn't even run. The YAML is invalid."),
        ("assistant", "Let me fix the indentation issue."),
        ("user", "Now it runs but every step fails. I can't believe I'm wasting my time on this. Completely useless."),
    ],
    [
        ("user", "Update the README with the new API endpoints"),
        ("assistant", "I've updated the README with the endpoint documentation."),
        ("user", "Half of these endpoints don't exist and the ones that do have wrong parameters."),
        ("assistant", "I apologize, let me cross-reference with the actual routes."),
        ("user", "Every single one is wrong. How is that possible? I literally just told you what they are. Undo that. I'll do it myself."),
    ],
    [
        ("user", "Fix the CSS on the dashboard"),
        ("assistant", "I've adjusted the flexbox layout and fixed the overflow."),
        ("user", "Nothing changed visually. At all."),
        ("assistant", "Let me check if there's a caching issue or conflicting styles."),
        ("user", "It's because you edited the wrong file entirely. FFS, how many times do I have to tell you which file to edit?"),
    ],
]

SCORE_2_CONVERSATIONS: list[list[tuple[str, str]]] = [
    [
        ("user", "Add pagination to the users list"),
        ("assistant", "I've added offset-based pagination to the users endpoint."),
        ("user", "I wanted cursor-based pagination, not offset. The ticket says cursor."),
    ],
    [
        ("user", "The search is returning wrong results"),
        ("assistant", "I've updated the search query to use full-text search."),
        ("user", "It's still not matching partial words. Try again with a different approach."),
    ],
    [
        ("user", "Can you speed up the build?"),
        ("assistant", "I've enabled parallel compilation and caching."),
        ("user", "It's actually slower now. The cache directory is on a network mount."),
        ("assistant", "I'll change the cache to use the local filesystem instead."),
        ("user", "That's better but still not great. The parallel compilation is causing OOM on CI."),
    ],
    [
        ("user", "Update the email templates"),
        ("assistant", "I've redesigned the email templates with the new branding."),
        ("user", "The colors are wrong. Our brand blue is #1a73e8 not #0066cc."),
        ("assistant", "Fixed the color values."),
        ("user", "Still off. The font is wrong too. We use Inter, not Helvetica."),
    ],
    [
        ("user", "Write the data migration script"),
        ("assistant", "Here's the migration script to transform the legacy data."),
        ("user", "This would corrupt records that have null timestamps. You need to handle that case."),
    ],
    [
        ("user", "Add error handling to the file upload"),
        ("assistant", "I've added try-catch blocks and error messages for the upload."),
        ("user", "The error messages aren't user-friendly. 'IOError at line 45' doesn't help anyone."),
        ("assistant", "Updated to show friendlier messages like 'Upload failed - file may be too large'."),
        ("user", "OK but you also lost the actual error for debugging. We need both."),
    ],
    [
        ("user", "Implement the webhook handler"),
        ("assistant", "I've created the webhook endpoint that processes incoming events."),
        ("user", "It doesn't verify the webhook signature. That's a security issue."),
    ],
    [
        ("user", "Fix the flaky test in the CI"),
        ("assistant", "I've added retry logic to the flaky test."),
        ("user", "That's not fixing it, that's hiding it. The test is flaky because of a race condition."),
    ],
    [
        ("user", "Add dark mode support"),
        ("assistant", "I've implemented dark mode by inverting all colors."),
        ("user", "Inverting colors doesn't work for images and some components look terrible. We need proper dark theme tokens."),
    ],
    [
        ("user", "The API response time is too slow"),
        ("assistant", "I've added an index on the frequently queried columns."),
        ("user", "The index helped for one query but the main bottleneck is the N+1 query in the serializer. You need to fix that."),
    ],
    [
        ("user", "Set up logging"),
        ("assistant", "I've added console.log statements throughout the codebase."),
        ("user", "We need structured logging with proper levels, not console.log. Use the winston logger."),
    ],
    [
        ("user", "The mobile layout is broken"),
        ("assistant", "I've adjusted the breakpoints for mobile screens."),
        ("user", "The navigation still overlaps the content on small screens."),
        ("assistant", "I'll convert it to a hamburger menu on mobile."),
        ("user", "The hamburger menu doesn't close after tapping a link. Annoying."),
    ],
]

SCORE_3_CONVERSATIONS: list[list[tuple[str, str]]] = [
    [
        ("user", "Add a created_at field to the User model"),
        ("assistant", "I've added a created_at datetime field with auto_now_add."),
        ("user", "OK. Now add the migration."),
    ],
    [
        ("user", "Rename the variable from 'data' to 'payload'"),
        ("assistant", "Done. I've updated all references in the file."),
        ("user", "Thanks."),
    ],
    [
        ("user", "What does this function do?"),
        ("assistant", "This function processes incoming webhook events by validating the signature and dispatching to the appropriate handler."),
        ("user", "Got it. Move it to the webhooks module."),
    ],
    [
        ("user", "Run the test suite"),
        ("assistant", "All 156 tests pass. 2 were skipped due to missing credentials."),
        ("user", "OK."),
    ],
    [
        ("user", "Add a health check endpoint"),
        ("assistant", "I've added GET /health that returns 200 with uptime and version."),
        ("user", "Add a database connectivity check too."),
    ],
    [
        ("user", "Update the dependency versions"),
        ("assistant", "I've bumped all dependencies to their latest compatible versions."),
        ("user", "Run the tests to make sure nothing broke."),
    ],
    [
        ("user", "List all the API routes"),
        ("assistant", "Here are the 23 API routes grouped by resource: users (5), posts (6), comments (4), auth (3), admin (5)."),
        ("user", "OK, now add OpenAPI annotations to each one."),
    ],
    [
        ("user", "Create a new branch for the feature"),
        ("assistant", "Created branch feature/user-notifications from main."),
        ("user", "Switch to it and start the implementation."),
    ],
    [
        ("user", "What's the current test coverage?"),
        ("assistant", "Overall coverage is 73%. The auth module is at 45%, which is the lowest."),
        ("user", "Write tests to get auth to 80%."),
    ],
    [
        ("user", "Remove the deprecated endpoints"),
        ("assistant", "I've removed the 4 deprecated v1 endpoints and updated the router."),
        ("user", "Did you update the client SDK too?"),
        ("assistant", "Yes, I've removed the corresponding methods from the SDK."),
        ("user", "OK."),
    ],
    [
        ("user", "Change the port from 3000 to 8080"),
        ("assistant", "Updated the port in the config and docker-compose files."),
        ("user", "Also update the nginx config."),
    ],
    [
        ("user", "Show me the database schema"),
        ("assistant", "Here's the current schema with 12 tables and their relationships."),
        ("user", "OK. Add an index on users.email."),
    ],
]

SCORE_4_CONVERSATIONS: list[list[tuple[str, str]]] = [
    [
        ("user", "Can you refactor the authentication to use JWT?"),
        ("assistant", "I've replaced the session-based auth with JWT tokens, including refresh token rotation and secure cookie storage."),
        ("user", "Nice, that looks clean. Does it handle token expiry gracefully?"),
        ("assistant", "Yes, the client automatically uses the refresh token when the access token expires, and shows a re-login prompt only if the refresh token is also expired."),
        ("user", "Good approach. Ship it."),
    ],
    [
        ("user", "Optimize the database queries on the dashboard"),
        ("assistant", "I've consolidated the N+1 queries into 3 batch queries with proper joins, reducing page load from 2.3s to 180ms."),
        ("user", "Great improvement. The page feels much snappier now."),
    ],
    [
        ("user", "Add input validation to the signup form"),
        ("assistant", "I've added real-time validation with proper error messages, email format checking, and password strength requirements."),
        ("user", "Works well. I like that the errors appear inline without a page reload."),
    ],
    [
        ("user", "Help me debug this intermittent test failure"),
        ("assistant", "The test is failing because it relies on insertion order from a HashMap. Switching to a BTreeMap makes the test deterministic."),
        ("user", "That makes sense. Good catch on the ordering issue."),
    ],
    [
        ("user", "Implement the CSV export feature"),
        ("assistant", "I've added CSV export with streaming response for large datasets, proper header encoding, and date formatting matching the UI."),
        ("user", "That works perfectly. Even handles the unicode characters in user names."),
    ],
    [
        ("user", "Add retry logic for the external API calls"),
        ("assistant", "I've added exponential backoff with jitter, configurable max retries, and circuit breaker for persistent failures."),
        ("user", "Solid implementation. The circuit breaker is a nice touch."),
    ],
    [
        ("user", "Set up the Docker configuration"),
        ("assistant", "I've created a multi-stage Dockerfile that builds a slim production image, plus docker-compose for local dev with hot reload."),
        ("user", "Clean setup. The dev compose starts up fast too. Thanks."),
    ],
    [
        ("user", "Migrate the config from YAML to TOML"),
        ("assistant", "Done. Migrated all config files, updated the parser, and added validation for required fields."),
        ("user", "Smooth migration. No complaints."),
    ],
    [
        ("user", "The websocket connections are dropping"),
        ("assistant", "The issue was missing heartbeat pings. I've added ping/pong frames every 30s with automatic reconnection on the client side."),
        ("user", "That fixed it. Connections are stable now."),
    ],
    [
        ("user", "Add caching for the API responses"),
        ("assistant", "I've implemented Redis caching with configurable TTLs per endpoint, cache invalidation on writes, and ETag support for conditional requests."),
        ("user", "Response times dropped significantly. Good work."),
    ],
    [
        ("user", "Can you add TypeScript types for the API?"),
        ("assistant", "I've generated TypeScript interfaces from the OpenAPI spec and added a build step to keep them in sync automatically."),
        ("user", "Nice, the auto-generation is helpful. No more manual type updates."),
    ],
    [
        ("user", "Implement role-based access control"),
        ("assistant", "I've added a permission system with roles, permissions, and middleware that checks access at the route level. Includes admin UI for managing roles."),
        ("user", "This is thorough. The admin UI for role management saves a lot of time."),
    ],
]

SCORE_5_CONVERSATIONS: list[list[tuple[str, str]]] = [
    [
        ("user", "I need to build a real-time collaborative editor"),
        ("assistant", "I've implemented CRDT-based real-time collaboration using Y.js, with WebSocket transport, offline support, and conflict resolution. The cursor positions of other users show up in real-time."),
        ("user", "Holy shit this is incredible. The collaboration works flawlessly and the cursor tracking is beautiful. This would have taken me weeks."),
    ],
    [
        ("user", "Can you make the app work offline?"),
        ("assistant", "I've added a service worker with intelligent caching, background sync for pending mutations, and a seamless online/offline transition with a status indicator."),
        ("user", "Wow, this is amazing. The offline experience is indistinguishable from online. Even the sync when coming back online is instant."),
    ],
    [
        ("user", "Help me optimize our build pipeline"),
        ("assistant", "I've restructured the build with incremental compilation, shared dependency caching across CI jobs, and parallel test sharding. Build time went from 45 minutes to 4 minutes."),
        ("user", "From 45 to 4 minutes?! That's incredible. The team is going to love this. You've saved us hours every day."),
    ],
    [
        ("user", "I need an admin dashboard"),
        ("assistant", "I've built a full admin dashboard with real-time metrics, user management, content moderation queue, and customizable widgets. Everything updates live via WebSocket and there's a full audit log."),
        ("user", "This is exactly what I envisioned, and then some. The real-time updates and audit log are perfect. I'm genuinely impressed."),
    ],
    [
        ("user", "The app is too slow for our enterprise customers"),
        ("assistant", "I've done a full performance audit and optimization: implemented virtual scrolling for large lists, added query result caching with smart invalidation, lazy-loaded heavy components, and added request deduplication. P95 latency dropped from 3.2s to 120ms."),
        ("user", "120ms P95! That's phenomenal. Our enterprise customers are going to notice this immediately. You just solved our biggest customer complaint."),
    ],
    [
        ("user", "Set up our monitoring and alerting"),
        ("assistant", "I've set up Prometheus metrics collection, Grafana dashboards with SLO tracking, PagerDuty integration with intelligent alert routing, and runbooks for common incidents. Added distributed tracing with Jaeger too."),
        ("user", "This is beyond what I expected. The SLO dashboards are beautiful and the runbooks are actually useful. We went from zero observability to best-in-class. Amazing work."),
    ],
    [
        ("user", "We need to migrate from MongoDB to PostgreSQL"),
        ("assistant", "I've built a zero-downtime migration pipeline: dual-write proxy, background data migration with consistency verification, automated rollback triggers, and a gradual traffic cutover. All existing queries translated with proper indexes."),
        ("user", "A zero-downtime migration?! I was expecting at least a maintenance window. The dual-write approach is brilliant. I'm in awe of how thoroughly you planned this."),
    ],
    [
        ("user", "Build an API rate limiter"),
        ("assistant", "I've implemented a distributed rate limiter using token bucket algorithm with Redis, supporting per-user, per-IP, and per-endpoint limits. Includes a developer portal showing usage, automatic quota upgrades for verified accounts, and graceful degradation with 429 responses that include retry-after headers."),
        ("user", "This is production-grade from day one. The developer portal is a fantastic touch. You've thought of everything I would have forgotten."),
    ],
    [
        ("user", "Help me write a complex SQL query for the analytics report"),
        ("assistant", "I've written the query using window functions for the rolling averages, CTEs for readability, and materialized views for the heavy aggregations. Also added an explain plan showing it runs in 200ms even on the full dataset."),
        ("user", "That query is a work of art. The CTEs make it so readable and the performance is excellent. I've been struggling with this for days and you nailed it instantly."),
    ],
    [
        ("user", "Our deployment process is a mess"),
        ("assistant", "I've rebuilt the entire deployment pipeline: GitOps with ArgoCD, blue-green deployments with automatic canary analysis, database migration checks, and one-click rollback. Deployments that used to take an hour of manual work now happen in 3 minutes with zero interaction."),
        ("user", "3 minutes! And the canary analysis catches issues automatically? This is transformative. The whole team was dreading deploy days and now it's completely painless. Best improvement we've made all quarter."),
    ],
    [
        ("user", "I need to implement end-to-end encryption for messages"),
        ("assistant", "I've implemented the Signal Protocol with X3DH key agreement, double ratchet for perfect forward secrecy, and sealed sender for metadata protection. Keys are managed client-side with WebCrypto, and there's a key verification UX with QR codes."),
        ("user", "The Signal Protocol, properly implemented? With sealed sender? I honestly didn't think this was possible in our timeframe. The key verification UX is so smooth. This is incredibly impressive work."),
    ],
    [
        ("user", "Can you add accessibility to the entire app?"),
        ("assistant", "I've done a comprehensive accessibility overhaul: semantic HTML throughout, proper ARIA labels and roles, keyboard navigation for all interactions, focus management for modals and dynamic content, screen reader announcements for state changes, and high contrast mode. Passes WCAG 2.1 AA on all pages."),
        ("user", "Full WCAG 2.1 AA compliance? I'm blown away. You even handled the dynamic content announcements which I've seen teams struggle with for months. This is exceptional work."),
    ],
]

ALL_CONVERSATIONS: dict[int, list[list[tuple[str, str]]]] = {
    1: SCORE_1_CONVERSATIONS,
    2: SCORE_2_CONVERSATIONS,
    3: SCORE_3_CONVERSATIONS,
    4: SCORE_4_CONVERSATIONS,
    5: SCORE_5_CONVERSATIONS,
}


def build_labeled_dataset() -> list[LabeledBucket]:
    dataset: list[LabeledBucket] = []
    for score, conversations in ALL_CONVERSATIONS.items():
        for i, messages in enumerate(conversations):
            bucket = _bucket(f"s{score}", i, messages)
            dataset.append(LabeledBucket(bucket, SentimentScore(score)))
    return dataset
