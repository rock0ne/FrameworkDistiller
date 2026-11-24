#!/usr/bin/env bash
# Owner-side release signing. Password is entered only at apksigner's prompt.
# Usage: KEY_ALIAS=frameworkdistiller ANDROID_HOME=... bash sign-apk.sh key.jks unsigned.apk signed.apk
set -euo pipefail
[ "$#" -eq 3 ] || { echo "Usage: $0 <keystore> <unsigned.apk> <signed.apk>" >&2; exit 2; }
KS=$1 IN=$2 OUT=$3
: "${ANDROID_HOME:?Set ANDROID_HOME to the Android SDK}"
[ -f "$KS" ] || { echo "Keystore does not exist" >&2; exit 2; }
[ -f "$IN" ] || { echo "Unsigned APK does not exist" >&2; exit 2; }
[ ! -e "$OUT" ] || { echo "Signed output already exists; choose a new name" >&2; exit 2; }
if [ -n "${JAVA_HOME:-}" ]; then export PATH="$JAVA_HOME/bin:$PATH"; fi
BT=$(ls -d "$ANDROID_HOME"/build-tools/*/ | sort -V | tail -1)
"$BT/zipalign" -c -p 4 "$IN" >/dev/null || { echo "Unsigned APK is not zip-aligned" >&2; exit 1; }
"$BT/apksigner" sign --ks "$KS" --ks-key-alias "${KEY_ALIAS:-frameworkdistiller}" --out "$OUT" "$IN"
"$BT/apksigner" verify --verbose --print-certs "$OUT" | grep -E 'Verified|certificate (DN|SHA-256)'
sha256sum "$OUT"
