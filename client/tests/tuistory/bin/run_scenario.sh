#!/usr/bin/env bash
set -euo pipefail

exec </dev/null

scenario_name="$1"
output_dir="$2"
size="$3"
proxy_port="$4"
confdir="$5"
fake_bin_dir="$6"
home_dir="$7"
scenario_config_path="$8"

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
mkdir -p "$output_dir" "$home_dir"

bun_path="$(command -v bun)"
tuistory_cli="${HOME}/.bun/install/global/node_modules/tuistory/dist/cli.js"
daemon_port="$(python3 - <<'PY'
import socket

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
)"
app_exit_path="${output_dir}/.app-exit.json"
session_meta_path="${output_dir}/.session-meta.json"
state_path="${output_dir}/state.json"
fake_bin_log="${output_dir}/fake-bin.jsonl"
pid_file="/tmp/tuistory/relay-${daemon_port}.pid"
real_gpg="$(command -v gpg || true)"
real_gh="$(command -v gh || true)"
launch_started="$(python3 - <<'PY'
import time

print(time.monotonic())
PY
)"
probe_url="$(python3 - "$scenario_config_path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
print(data.get("probe_url", ""))
PY
)"

read -r cols rows <<EOF
$(python3 - "$size" <<'PY'
import sys

size = sys.argv[1]
delimiter = "x" if "x" in size else "X"
cols, rows = size.split(delimiter, 1)
print(cols, rows)
PY
)
EOF

session_name="$(python3 - "$scenario_name" "$daemon_port" <<'PY'
import re
import sys

name, port = sys.argv[1:3]
print(f"cc-sentiment-{re.sub(r'[^a-z0-9_-]+', '-', name.lower())}-{port}")
PY
)"

command_string="$(python3 - "$project_root" "$fake_bin_dir" "$home_dir" "$proxy_port" "$confdir" "$scenario_config_path" "$app_exit_path" "$session_meta_path" "$fake_bin_log" "$real_gpg" "$real_gh" "$PATH" <<'PY'
import shlex
import sys

project_root, fake_bin_dir, home_dir, proxy_port, confdir, scenario_config_path, app_exit_path, session_meta_path, fake_bin_log, real_gpg, real_gh, original_path = sys.argv[1:13]
env = {
    "PATH": f"{fake_bin_dir}:{original_path}",
    "HOME": home_dir,
    "HTTPS_PROXY": f"http://127.0.0.1:{proxy_port}",
    "HTTP_PROXY": f"http://127.0.0.1:{proxy_port}",
    "SSL_CERT_FILE": f"{confdir}/mitmproxy-ca-cert.pem",
    "NO_PROXY": "localhost,127.0.0.1,::1",
    "CC_SENTIMENT_SCENARIO": scenario_config_path,
    "CC_SENTIMENT_FAKE_BIN_LOG": fake_bin_log,
    "CC_SENTIMENT_REAL_GPG": real_gpg,
    "CC_SENTIMENT_REAL_GH": real_gh,
    "TERM": "xterm-truecolor",
    "COLORTERM": "truecolor",
    "LANG": "en_US.UTF-8",
    "UV_NO_SYNC": "1",
    "UV_OFFLINE": "1",
}
pairs = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
print(
    f"printf '{{\"pid\":%s,\"pgid\":%s}}\\n' \"$$\" \"$(ps -o pgid= -p $$ | tr -d ' ')\" > {shlex.quote(session_meta_path)}; "
    f"trap \"printf '%s\\\\n' '{{\\\"returncode\\\":0}}' > {shlex.quote(app_exit_path)}; exit 0\" HUP INT TERM; "
    f"status=0; cd {shlex.quote(project_root)} && env -i {pairs} uv run cc-sentiment setup "
    f"|| status=$?; printf '{{\"returncode\":%s}}\\n' \"$status\" > {shlex.quote(app_exit_path)}; "
    f"exit \"$status\""
)
PY
)"

cleanup() {
  set +e
  if [ -f "$session_meta_path" ]; then
    read -r session_pid session_pgid <<EOF
$(python3 - "$session_meta_path" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
print(data["pid"], data["pgid"])
PY
)
EOF
    kill -- "-${session_pgid}" >/dev/null 2>&1 || kill "$session_pid" >/dev/null 2>&1 || true
    for _ in $(seq 1 50); do
      kill -0 "$session_pid" >/dev/null 2>&1 || break
      sleep 0.1
    done
    kill -9 -- "-${session_pgid}" >/dev/null 2>&1 || kill -9 "$session_pid" >/dev/null 2>&1 || true
  fi
  if [ -f "$pid_file" ]; then
    pid="$(cat "$pid_file")"
    kill "$pid" >/dev/null 2>&1 || true
    for _ in $(seq 1 50); do
      kill -0 "$pid" >/dev/null 2>&1 || break
      sleep 0.1
    done
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if ! TUISTORY_PORT="$daemon_port" script -q /dev/null "$bun_path" "$tuistory_cli" launch "$command_string" -s "$session_name" --cols "$cols" --rows "$rows" --cwd "$project_root" >"${output_dir}/launch.log" 2>&1; then
  printf '{"session":"%s","size":"%s","snapshots":{},"commands":[],"app_exit":null,"error":{"type":"launch","message":"launch failed"}}\n' "$session_name" "$size" >"$state_path"
  exit 1
fi

if [ -n "$probe_url" ]; then
  env -i \
    HTTPS_PROXY="http://127.0.0.1:${proxy_port}" \
    HTTP_PROXY="http://127.0.0.1:${proxy_port}" \
    SSL_CERT_FILE="${confdir}/mitmproxy-ca-cert.pem" \
    NO_PROXY="localhost,127.0.0.1,::1" \
    PATH="$PATH" \
    python3 - "$probe_url" "${confdir}/mitmproxy-ca-cert.pem" <<'PY'
import ssl
import sys
import urllib.request

url, cert = sys.argv[1:3]
request = urllib.request.Request(url)
handler = urllib.request.ProxyHandler()
opener = urllib.request.build_opener(handler, urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=cert)))
with opener.open(request, timeout=10):
    pass
PY
fi

cd "$project_root"
python3 -m tests.tuistory.driver \
  "$session_name" \
  "$output_dir" \
  "$size" \
  "$scenario_config_path" \
  "$bun_path" \
  "$tuistory_cli" \
  "$daemon_port" \
  "$app_exit_path" \
  "$launch_started"
