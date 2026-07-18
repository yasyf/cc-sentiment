# Changelog

Notable changes to the cc-sentiment client are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); continuous
deploy mints versions (`v0.2.<run>`) at merge time, so changes land under
Unreleased and ride the next release.

## [Unreleased]

### Changed

- **Metric semantics: `subagent_count` counts typed `Task` dispatches.** Bucket
  metrics classify tool calls through cc-transcript's typed tool-call layer
  (`parse_tool_call`) instead of literal tool-name lookups: Task-alias subagent
  dispatches now count, and a dispatch must carry a well-formed input to count
  at all. This field is uploaded and averaged into server aggregates, so
  fleet-level subagent numbers shift from this release on.
- **Metric semantics: malformed `Edit`/`Write` calls are excluded from edit
  ratios.** A call whose input doesn't parse as a typed `EditCall`/`WriteCall`
  no longer enters `read_edit_ratio`, `write_edit_ratio`, or
  `edits_without_prior_read_ratio`; the exclusion applies to all three ratios
  consistently. These ratios also propagate to server aggregates.
