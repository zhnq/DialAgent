#!/bin/zsh
set -euo pipefail

root=${0:A:h:h}
out="$root/work/bin"
app="$root/work/DialAgentVoiceControl.app"
mkdir -p "$out" "$app/Contents/MacOS"

clang "$root/scripts/coreaudio_defaults.c" -o "$out/coreaudio-defaults" \
  -Wall -Wextra -framework CoreAudio -framework CoreFoundation

clang -x objective-c "$root/scripts/codex_voice_hotkey.c" \
  -o "$app/Contents/MacOS/DialAgentVoiceControl" \
  -Wall -Wextra -framework AppKit -framework ApplicationServices \
  -framework CoreFoundation
cp "$root/scripts/CodexVoiceHotkey.Info.plist" "$app/Contents/Info.plist"
codesign --force --deep --sign - "$app"

pj_lib=(/opt/homebrew/lib/libpjmedia-audiodev-*.a)
pj_media=(/opt/homebrew/lib/libpjmedia-*.a)
pj_core=(/opt/homebrew/lib/libpj-*.a)
if (( ${#pj_lib} == 0 || ${#pj_media} == 0 || ${#pj_core} == 0 )); then
  print -u2 "PJSIP static libraries were not found. Install pjproject with Homebrew."
  exit 2
fi
clang "$root/scripts/pj_audio_devices.c" -o "$out/pj-audio-devices" \
  -I/opt/homebrew/include -DPJ_AUTOCONF=1 \
  -DPJ_IS_BIG_ENDIAN=0 -DPJ_IS_LITTLE_ENDIAN=1 \
  "$pj_lib[1]" "$pj_media[1]" "$pj_core[1]" \
  -framework CoreAudio -framework AudioToolbox -framework AudioUnit \
  -framework Foundation

echo "Built helpers in $out and $app"
echo "Launch the app once, then grant its macOS accessibility/device-control permission."
