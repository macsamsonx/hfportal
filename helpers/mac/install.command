#!/bin/zsh
# HF Hub helper — installer for Mac (install once; it starts by itself after every restart).
# Downloaded from HF Hub → Office PCs. Double-click it, then enter this Mac's password.
# If macOS blocks it: System Settings → Privacy & Security → Open Anyway.

PORTAL="__PORTAL__"
CODE="__CODE__"
ORIGINS="__ORIGINS__"
DIR="/Library/Application Support/HFHub"
PLIST="/Library/LaunchDaemons/digital.hundredfold.hfhelper.plist"
LABEL="digital.hundredfold.hfhelper"

if [ "$EUID" -ne 0 ]; then
  echo "Installing the HF Hub helper — enter this Mac's password when asked."
  exec sudo /bin/zsh "$0" "$@"
fi

UUID=$(ioreg -rd1 -c IOPlatformExpertDevice | awk -F'"' '/IOPlatformUUID/{print $4}')
NAME=$(scutil --get ComputerName | tr -d '"\\')
echo "Computer: $NAME"

RESP=$(curl -fsS -m 20 -H 'Content-Type: application/json' \
  -d "{\"code\":\"$CODE\",\"device_uid\":\"$UUID\",\"hostname\":\"$NAME\",\"platform\":\"mac\"}" \
  "$PORTAL/api/pc/enroll") || {
  echo "✗ Could not register with HF Hub ($PORTAL). Check the network, or download a fresh installer (codes last 24 hours)."
  read -k1 "?Press any key to close…"; exit 1
}
DEVICE_ID=$(print -r -- "$RESP" | /usr/bin/perl -ne 'print $1 if /"device_id":\s*(\d+)/')
SECRET=$(print -r -- "$RESP" | /usr/bin/perl -ne 'print $1 if /"secret":\s*"([0-9a-f]+)"/')
if [ -z "$DEVICE_ID" ] || [ -z "$SECRET" ]; then
  echo "✗ HF Hub refused the installer: $RESP"
  read -k1 "?Press any key to close…"; exit 1
fi

mkdir -p "$DIR"
cat > "$DIR/hf-helper.pl" <<'HFHELPER'
__HELPER__
HFHELPER
printf 'device_id=%s\nsecret=%s\norigins=%s\nport=47600\n' "$DEVICE_ID" "$SECRET" "$ORIGINS" > "$DIR/helper.conf"
chown -R root:wheel "$DIR"
chmod 755 "$DIR" "$DIR/hf-helper.pl"
chmod 600 "$DIR/helper.conf"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/usr/bin/perl</string><string>$DIR/hf-helper.pl</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/var/log/hf-helper.log</string>
</dict></plist>
PLISTEOF
chown root:wheel "$PLIST"; chmod 644 "$PLIST"
launchctl bootout system/$LABEL 2>/dev/null
launchctl bootstrap system "$PLIST"
sleep 2

FIRST_ORIGIN=${ORIGINS%%,*}
if curl -fsS -m 10 -H "Origin: $FIRST_ORIGIN" http://127.0.0.1:47600/snapshot >/dev/null; then
  echo "✓ Installed. This Mac now shows up in HF Hub → Office PCs."
  echo "  Use Chrome for HF Hub on this Mac, and click Allow if Chrome asks about local devices."
else
  echo "✗ Installed, but the helper isn't answering yet. See /var/log/hf-helper.log"
fi
read -k1 "?Press any key to close…"
