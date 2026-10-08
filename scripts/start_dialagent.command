#!/bin/zsh
set -euo pipefail

root=${0:A:h:h}
config="$root/config.env"
if [[ ! -f "$config" ]]; then
  echo "Missing config.env. Copy config.example.env and fill in your own device values."
  exit 2
fi
source "$config"

required=(DIALAGENT_BIND_IP DIALAGENT_PHONE_IP DIALAGENT_PHONE_DEVICE_NAME
          DIALAGENT_PHONE_CERT_SHA256 DIALAGENT_SIP_PORT DIALAGENT_RTP_PORT
          DIALAGENT_ACTION_PORT DIALAGENT_RUNTIME_SECONDS DIALAGENT_WEEK_REMAINING)
for name in $required; do
  if [[ -z ${(P)name:-} ]]; then
    echo "Missing required setting: $name"
    exit 2
  fi
done

export DIALAGENT_HELPER_DIR="$root/work/bin"
export DIALAGENT_VOICE_CONTROL_APP="$root/work/DialAgentVoiceControl.app"
state="$root/work/state/face.json"
hangup="$root/work/state/hangup.request"
mkdir -p "$root/work/state" "$root/work/logs"

action_log="$root/work/logs/action.log"
screen_log="$root/work/logs/screen.log"
action_pid=""
screen_pid=""
cleanup() {
  [[ -n "$screen_pid" ]] && kill "$screen_pid" 2>/dev/null || true
  [[ -n "$action_pid" ]] && kill "$action_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM HUP

open -gj "$DIALAGENT_VOICE_CONTROL_APP" || true
python3 "$root/scripts/t31g_action_server.py" \
  --bind-ip "$DIALAGENT_BIND_IP" --expected-source "$DIALAGENT_PHONE_IP" \
  --dial-target "$DIALAGENT_BIND_IP" --state-file "$state" \
  --hangup-request-file "$hangup" --port "$DIALAGENT_ACTION_PORT" \
  >"$action_log" 2>&1 &
action_pid=$!

python3 "$root/scripts/t31g_face_pusher.py" \
  --phone-ip "$DIALAGENT_PHONE_IP" \
  --device-name "$DIALAGENT_PHONE_DEVICE_NAME" \
  --fingerprint "$DIALAGENT_PHONE_CERT_SHA256" --state-file "$state" \
  --screen-timeout 0 \
  --action-uri "http://$DIALAGENT_BIND_IP:$DIALAGENT_ACTION_PORT/dialagent-button" \
  --week-remaining "$DIALAGENT_WEEK_REMAINING" --interval 1.0 \
  >"$screen_log" 2>&1 &
screen_pid=$!

receiver=(python3 "$root/scripts/field_sip_receiver.py"
  --bind-ip "$DIALAGENT_BIND_IP" --codec PCMU
  --sip-port "$DIALAGENT_SIP_PORT" --rtp-port "$DIALAGENT_RTP_PORT"
  --seconds "$DIALAGENT_RUNTIME_SECONDS" --auto-answer
  --demo-auto-answer-confirmed --auto-audio-route --auto-voice
  --face-state-file "$state" --hangup-request-file "$hangup")
if [[ -n "${DIALAGENT_VOICE_THREAD_ID:-}" ]]; then
  receiver+=(--voice-thread-id "$DIALAGENT_VOICE_THREAD_ID")
fi

echo "DialAgent is ready. Dial $DIALAGENT_BIND_IP:$DIALAGENT_SIP_PORT from the phone."
echo "Logs: $root/work/logs"
"$receiver[@]"
