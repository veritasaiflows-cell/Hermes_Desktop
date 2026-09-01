#!/usr/bin/env bash
# WF commerce isolated browser launcher.
#
# Purpose
#   Starts (or reuses) a dedicated Chrome instance for WF-1000..WF-1003 commerce
#   account work. The profile is isolated from the operator's personal Chrome
#   profile: separate cookies, separate storage, separate logins.
#
# Why isolated
#   Account setup for suppliers / storefront / ad channels should not run inside
#   the operator's personal browser identity. This profile is the only browser
#   surface the agent drives for commerce onboarding.
#
# Usage
#   bash scripts/wf_commerce_browser.sh start    # launch / verify the browser
#   bash scripts/wf_commerce_browser.sh status   # report CDP readiness
#   bash scripts/wf_commerce_browser.sh stop     # close the isolated browser
#
# Agent attach (browser_exec / browser-use):
#   export BU_CDP_URL="http://127.0.0.1:9333"
#   export BU_NAME="wfcommerce"
#
# Stop lines (do not automate past these):
#   - passwords, SMS/email verification codes, CAPTCHA
#   - identity documents, banking or payment details
#   - Terms acceptance / final account submission
#   - OAuth authorization, publication, ad spend

set -uo pipefail

PORT="${WF_COMMERCE_CDP_PORT:-9333}"
PROFILE_DIR="${WF_COMMERCE_PROFILE_DIR:-C:/Users/$USERNAME/AppData/Local/hermes/browser-profiles/wf-commerce}"

CHROME_CANDIDATES=(
  "/c/Program Files/Google/Chrome/Application/chrome.exe"
  "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"
)

find_chrome() {
  for c in "${CHROME_CANDIDATES[@]}"; do
    [ -x "$c" ] && { printf '%s' "$c"; return 0; }
  done
  return 1
}

cdp_ready() {
  curl -s --max-time 5 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1
}

cmd_status() {
  if cdp_ready; then
    echo "wf-commerce browser: READY on http://127.0.0.1:${PORT}"
    curl -s --max-time 5 "http://127.0.0.1:${PORT}/json/version"
    echo
    echo "profile: ${PROFILE_DIR}"
    return 0
  fi
  echo "wf-commerce browser: NOT RUNNING (port ${PORT} closed)"
  return 1
}

cmd_start() {
  if cdp_ready; then
    echo "wf-commerce browser already running on port ${PORT}; reusing it."
    cmd_status
    return 0
  fi

  local chrome
  if ! chrome="$(find_chrome)"; then
    echo "ERROR: Chrome not found in known install paths." >&2
    return 1
  fi

  mkdir -p "${PROFILE_DIR}"

  "${chrome}" \
    --remote-debugging-port="${PORT}" \
    --user-data-dir="${PROFILE_DIR}" \
    --no-first-run \
    --no-default-browser-check \
    --new-window "about:blank" \
    >/dev/null 2>&1 &

  for _ in $(seq 1 20); do
    sleep 1
    if cdp_ready; then
      echo "wf-commerce browser started on port ${PORT}."
      cmd_status
      return 0
    fi
  done

  echo "ERROR: browser did not expose CDP on port ${PORT} within timeout." >&2
  return 1
}

cmd_stop() {
  if ! cdp_ready; then
    echo "wf-commerce browser already stopped."
    return 0
  fi
  # Only kill the chrome process bound to this dedicated debug port.
  local pid
  pid="$(netstat -ano | grep -E "127\.0\.0\.1:${PORT}[[:space:]].*LISTENING" | awk '{print $NF}' | head -1)"
  if [ -z "${pid}" ]; then
    echo "ERROR: could not resolve PID for port ${PORT}." >&2
    return 1
  fi
  taskkill //PID "${pid}" //T //F >/dev/null 2>&1
  echo "wf-commerce browser stopped (pid ${pid})."
}

case "${1:-status}" in
  start)  cmd_start ;;
  status) cmd_status ;;
  stop)   cmd_stop ;;
  *)
    echo "usage: $0 {start|status|stop}" >&2
    exit 2
    ;;
esac
