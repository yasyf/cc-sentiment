# Changelog

Notable changes to the cc-sentiment client are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); continuous
deploy mints versions (`v0.2.<run>`) at merge time, so changes land under
Unreleased and ride the next release.

## [Unreleased]

### Changed

- **`cc-sentiment install` starts a resident daemon.** The launchd job now
  keeps `cc-sentiment daemon` alive instead of firing `cc-sentiment run` once a
  day. The daemon scans every five minutes, loads the model only while there
  is something to score, and restarts itself onto a new release. Re-run
  `cc-sentiment install` to move an existing daily schedule over; `run` is
  unchanged for cron and other schedulers.
- **Transcripts still being written are left for the next scan.** A transcript
  modified within the last bucket window (three minutes) is skipped, so a
  bucket is scored once it is complete instead of frozen half-written.
- **spawnllm 0.17 and cc-transcript 14.39.** The Claude engine keeps
  inheriting `ANTHROPIC_API_KEY`, which spawnllm now strips by default.
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
