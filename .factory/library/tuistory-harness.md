## Tuistory harness notes

- The global `tuistory` shim at `~/.bun/bin/tuistory` fails under Node on this machine because `node-pty` is missing. Invoke Tuistory with Bun instead: `bun ~/.bun/install/global/node_modules/tuistory/dist/cli.js`.
- `client/tests/tuistory/bin/run_scenario.sh` allocates a fresh PTY with `script -q /dev/null`, redirects `stdin` to `/dev/null`, records the session shell PID/PGID, and kills that process group during cleanup. This is required to avoid orphaned `cc-sentiment setup` subprocesses after tests.
- The harness uses a unique `TUISTORY_PORT` per wrapper run and leaves the machine’s long-lived default `tuistory-relay` untouched.
- Remote-elision checks can assert runtime stage history directly: `SetupScreen` writes `transition_history_names` to the path in `CC_SENTIMENT_TRANSITION_HISTORY_PATH`, `run_scenario.sh` stores it as `.transition-history.json`, and `driver.py` loads it into `state.json["transition_history"]`.
