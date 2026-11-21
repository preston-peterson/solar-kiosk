#!/usr/bin/env bash
# solar-kiosk installer, updater and uninstaller (install mode 1).
#
#   Install:    curl -fsSL https://github.com/preston-peterson/solar-kiosk/releases/latest/download/install.sh | bash
#   Options:    ... | bash -s -- --no-kiosk --port 8081        (see --help)
#   Update:     sudo /opt/solar-kiosk/install.sh --update      (also run nightly by a systemd timer)
#   Uninstall:  sudo /opt/solar-kiosk/install.sh --uninstall [--purge]
#
# It only installs published GitHub releases, and checks every file against the release's SHA256SUMS. Python
# packages are installed exactly as locked, each checked against its hash. If a new version doesn't start, it
# goes back to the previous one by itself.
#
# Everything runs inside main(), called on the last line, so a half-downloaded script does nothing.

set -euo pipefail

REPO="preston-peterson/solar-kiosk"
PREFIX="/opt/solar-kiosk"            # code: releases/<version>, current -> the active one, uv, this script
DATA="/var/lib/solar-kiosk"          # settings and state, owned by the service user (mode 700)
SVC_USER="solar-kiosk"
UNIT="solar-kiosk"
WRAPPER="/usr/local/bin/solar-kiosk"
CONF="$PREFIX/install.conf"
SKIP="$PREFIX/skip-versions"         # versions that failed their health check; not retried
TIMER_STAMP="/var/lib/systemd/timers/stamp-$UNIT-update.timer"   # last nightly run; removed on uninstall
KEEP_RELEASES=2

# uv, pinned: version and the SHA-256 of its release archives
UV_VERSION="0.12.21"
uv_sha256() {
  case "$1" in
    aarch64) echo "030b69227b40af8c1981b7301793dc66e71ed3c796ea8688209dd268bd91ec51" ;;
    x86_64)  echo "23f02075b652bb1df64178cfae41b5caf160822e720e2663568f3f5d63bc52c0" ;;
  esac
}

say()  { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die()  { printf '\nsolar-kiosk: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
solar-kiosk installer

  curl -fsSL https://github.com/preston-peterson/solar-kiosk/releases/latest/download/install.sh | bash
  curl -fsSL .../install.sh | bash -s -- [options]

Options:
  --port N            web port (default 8080)
  --kiosk             open the solar page full screen on this Pi's own display at login
  --no-kiosk          don't (default: only if a desktop and a connected screen are found)
  --no-auto-update    don't check for new releases every night (default: check around 3 am)
  --auto-update       turn nightly updates back on
  --version X.Y.Z     install that release instead of the latest
  --from DIR          install from a local folder holding a release's files and SHA256SUMS
  --update            update to the latest release (what the nightly timer runs)
  --uninstall         remove solar-kiosk but keep its settings
  --purge             with --uninstall: also delete the settings, saved logins and the service user
  -h, --help          show this help

Re-running the installer is safe: it keeps the settings and upgrades or repairs the install.
EOF
}

# --- privileges ------------------------------------------------------------------------------------------

SUDO=()
need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null || die "This needs administrator rights. Run it as root, or install sudo."
    SUDO=(sudo)
    # `sudo true`, not `sudo -v`: when one sudo rule needs a password and another doesn't (Raspberry Pi
    # OS's default user is set up that way), `sudo -v` asks for a password the user may never have set.
    sudo true || die "Couldn't get administrator rights (sudo)."
  fi
}
as_root() { "${SUDO[@]}" "$@"; }

# --- checks ----------------------------------------------------------------------------------------------

ARCH=""
check_system() {
  [ "$(uname -s)" = Linux ] || die "solar-kiosk runs on Linux (Raspberry Pi OS)."
  [ -d /run/systemd/system ] || die "This system doesn't use systemd, which solar-kiosk needs to run in the background."
  case "$(uname -m)" in
    aarch64|arm64) ARCH=aarch64 ;;
    x86_64|amd64)  ARCH=x86_64 ;;
    armv7l|armv6l) die "This is a 32-bit system. solar-kiosk needs a 64-bit OS: install Raspberry Pi OS (64-bit) on a Pi 4 or 5." ;;
    *)             die "Unsupported processor: $(uname -m)." ;;
  esac
}

install_packages() {  # install_packages pkg...  (only the ones that are missing)
  local missing=() p
  for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
  [ ${#missing[@]} -eq 0 ] && return 0
  command -v apt-get >/dev/null || die "Please install: ${missing[*]}"
  step "Installing system packages: ${missing[*]}"
  as_root env DEBIAN_FRONTEND=noninteractive apt-get update -qq
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}" >/dev/null
}

port_busy_by_other() {  # true if something other than our service listens on $PORT
  command -v ss >/dev/null || return 1
  ss -Hltn "sport = :$PORT" 2>/dev/null | grep -q . || return 1
  ! systemctl is-active --quiet "$UNIT"
}

# --- settings for this install (port, kiosk, updates), kept between runs ---------------------------------

PORT=8080; KIOSK=auto; AUTO_UPDATE=yes; DESKTOP_USER=""
load_conf() {
  [ -f "$CONF" ] || return 0
  local key value
  while IFS='=' read -r key value; do
    case "$key" in
      PORT)         [[ "$value" =~ ^[0-9]+$ ]] && PORT="$value" ;;
      KIOSK)        [[ "$value" =~ ^(auto|yes|no)$ ]] && KIOSK="$value" ;;
      AUTO_UPDATE)  [[ "$value" =~ ^(yes|no)$ ]] && AUTO_UPDATE="$value" ;;
      DESKTOP_USER) [[ "$value" =~ ^[a-z_][a-z0-9_-]*$ ]] && DESKTOP_USER="$value" ;;
    esac
  done < "$CONF"
}
save_conf() {
  printf 'PORT=%s\nKIOSK=%s\nAUTO_UPDATE=%s\nDESKTOP_USER=%s\n' "$PORT" "$KIOSK" "$AUTO_UPDATE" "$DESKTOP_USER" \
    | as_root tee "$CONF" >/dev/null
}

# --- download and verify a release -----------------------------------------------------------------------

TMP=""; VERSION=""; WHEEL=""
latest_tag() {
  curl -fsSL --retry 3 "https://api.github.com/repos/$REPO/releases/latest" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])'
}

fetch_release() {  # fetch_release <tag or empty>  -> files in $TMP/release, verified
  local tag="$1" dir="$TMP/release" f
  mkdir -p "$dir"
  if [ -n "$FROM" ]; then
    cp -- "$FROM"/* "$dir/" 2>/dev/null || die "Nothing to install in $FROM."
  else
    local base="https://github.com/$REPO/releases/download/$tag"
    curl -fsSL --retry 3 -o "$dir/SHA256SUMS" "$base/SHA256SUMS" || die "Couldn't download release $tag from GitHub."
    while read -r _ f; do
      case "$f" in */*|.*|"") die "Unexpected file name in SHA256SUMS: $f" ;; esac
      curl -fsSL --retry 3 -o "$dir/$f" "$base/$f" || die "Couldn't download $f from release $tag."
    done < "$dir/SHA256SUMS"
  fi
  [ -f "$dir/SHA256SUMS" ] || die "The release has no SHA256SUMS, so it can't be checked."
  (cd "$dir" && sha256sum --check --strict --quiet SHA256SUMS) \
    || die "The download didn't match its checksums. Nothing was changed."
  for f in requirements.txt install.sh; do
    grep -q "  $f\$" "$dir/SHA256SUMS" || die "The release is missing a checked $f."
  done
  WHEEL=$(find "$dir" -maxdepth 1 -name 'solar_kiosk-*-py3-none-any.whl' | head -n 1)
  if [ -z "$WHEEL" ] || ! grep -q "  $(basename "$WHEEL")\$" "$dir/SHA256SUMS"; then
    die "The release has no checked solar-kiosk package."
  fi
  VERSION=$(basename "$WHEEL" | sed -E 's/^solar_kiosk-([^-]+)-.*/\1/')
  [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([a-z0-9.+-]*)?$ ]] || die "Unexpected version: $VERSION"
}

ensure_uv() {
  local uv="$PREFIX/bin/uv" name="uv-$ARCH-unknown-linux-gnu"
  if [ -x "$uv" ] && "$uv" --version 2>/dev/null | grep -q "^uv $UV_VERSION\b"; then return 0; fi
  step "Installing uv $UV_VERSION (the Python package installer)"
  curl -fsSL --retry 3 -o "$TMP/$name.tar.gz" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$name.tar.gz" \
    || die "Couldn't download uv."
  printf '%s  %s\n' "$(uv_sha256 "$ARCH")" "$TMP/$name.tar.gz" | sha256sum --check --strict --quiet \
    || die "The uv download didn't match its pinned checksum. Nothing was changed."
  tar -xzf "$TMP/$name.tar.gz" -C "$TMP"
  as_root install -D -m 0755 "$TMP/$name/uv" "$uv"
}

# --- install a release into its own folder ---------------------------------------------------------------

uv_env() {
  as_root env UV_CACHE_DIR="$PREFIX/cache" UV_PYTHON_INSTALL_DIR="$PREFIX/python" UV_PYTHON_PREFERENCE=system \
    UV_NO_PROGRESS=1 UV_NO_CONFIG=1 "$PREFIX/bin/uv" "$@"
}

install_release() {
  local dir="$PREFIX/releases/$VERSION"
  if [ -x "$dir/bin/solar-kiosk" ] && [ "$("$dir/bin/solar-kiosk" --version 2>/dev/null)" = "solar-kiosk $VERSION" ]; then
    say "solar-kiosk $VERSION is already installed in $dir."
    return 0
  fi
  step "Installing solar-kiosk $VERSION"
  as_root rm -rf "$dir"
  as_root install -d -m 0755 "$PREFIX/releases"
  if ! { uv_env venv --quiet --python ">=3.11" "$dir" \
         && uv_env pip install --quiet --python "$dir/bin/python" --require-hashes --only-binary :all: \
              -r "$TMP/release/requirements.txt" \
         && uv_env pip install --quiet --python "$dir/bin/python" --no-deps "$WHEEL"; }; then
    as_root rm -rf "$dir"
    die "Installing solar-kiosk $VERSION failed. The previous install (if any) is untouched."
  fi
  as_root chmod -R go-w "$dir"
}

current_version() {  # the version `current` points at, or empty
  local target
  target=$(readlink "$PREFIX/current" 2>/dev/null) || return 0
  basename "$target"
}

switch_to() {  # switch_to <version>  (atomic)
  as_root ln -sfn "releases/$1" "$PREFIX/current.new"
  as_root mv -T "$PREFIX/current.new" "$PREFIX/current"
}

prune_releases() {
  local keep=() v
  keep+=("$(current_version)")
  # keep the newest others, up to KEEP_RELEASES in total
  while IFS= read -r v; do
    [ ${#keep[@]} -ge $KEEP_RELEASES ] && break
    [ "$v" = "${keep[0]}" ] || keep+=("$v")
  done < <(ls -1t "$PREFIX/releases" 2>/dev/null)
  local d
  for d in "$PREFIX"/releases/*; do
    [ -d "$d" ] || continue
    v="${d##*/}"
    printf '%s\n' "${keep[@]}" | grep -qx -- "$v" || as_root rm -rf "${PREFIX:?}/releases/$v"
  done
  uv_env cache prune --quiet 2>/dev/null || true
}

# --- the service user, systemd units and the command wrapper ---------------------------------------------

setup_user() {
  if ! id "$SVC_USER" >/dev/null 2>&1; then
    as_root useradd --system --user-group --home-dir "$DATA" --no-create-home --shell /usr/sbin/nologin \
      --comment "solar-kiosk" "$SVC_USER"
  fi
  as_root install -d -m 0700 -o "$SVC_USER" -g "$SVC_USER" "$DATA"
}

write_units() {
  as_root tee "/etc/systemd/system/$UNIT.service" >/dev/null <<EOF
# Written by solar-kiosk's install.sh; re-running the installer rewrites it.
[Unit]
Description=solar-kiosk solar and battery page
Documentation=https://github.com/$REPO
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
Group=$SVC_USER
Environment=XDG_CONFIG_HOME=$DATA/config
Environment=XDG_STATE_HOME=$DATA/state
ExecStart=$PREFIX/current/bin/solar-kiosk serve --port $PORT
Restart=always
RestartSec=5
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$DATA
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources
SystemCallErrorNumber=EPERM
CapabilityBoundingSet=
AmbientCapabilities=
PrivateUsers=yes
ProtectKernelLogs=yes
ProtectProc=invisible
ProcSubset=pid
RemoveIPC=yes
MemoryDenyWriteExecute=yes

[Install]
WantedBy=multi-user.target
EOF
  as_root tee "/etc/systemd/system/$UNIT-update.service" >/dev/null <<EOF
# Written by solar-kiosk's install.sh.
[Unit]
Description=Update solar-kiosk to the latest release
Documentation=https://github.com/$REPO
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$PREFIX/install.sh --update
EOF
  as_root tee "/etc/systemd/system/$UNIT-update.timer" >/dev/null <<EOF
# Written by solar-kiosk's install.sh.
[Unit]
Description=Check for a new solar-kiosk release overnight

[Timer]
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=45min
Persistent=true

[Install]
WantedBy=timers.target
EOF
  as_root tee "$WRAPPER" >/dev/null <<EOF
#!/bin/sh
# solar-kiosk commands (e.g. reset-password, watch, --version), run as the service user with its settings.
# Written by solar-kiosk's install.sh.
set -e
cmd="$PREFIX/current/bin/solar-kiosk"
if [ "\$(id -un)" = "$SVC_USER" ]; then
  exec env XDG_CONFIG_HOME="$DATA/config" XDG_STATE_HOME="$DATA/state" "\$cmd" "\$@"
fi
exec sudo -u "$SVC_USER" env XDG_CONFIG_HOME="$DATA/config" XDG_STATE_HOME="$DATA/state" "\$cmd" "\$@"
EOF
  as_root chmod 0755 "$WRAPPER"
  as_root systemctl daemon-reload
}

apply_auto_update() {
  if [ "$AUTO_UPDATE" = yes ]; then
    as_root systemctl enable --now --quiet "$UNIT-update.timer"
  else
    as_root systemctl disable --now --quiet "$UNIT-update.timer" 2>/dev/null || true
  fi
}

# --- start and check it ----------------------------------------------------------------------------------

healthy_version() {
  curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null \
    | python3 -c 'import json, sys; print(json.load(sys.stdin).get("version", ""))' 2>/dev/null || true
}

wait_healthy() {  # wait_healthy <version>: up to 90 s for the new version to answer
  local _
  for _ in $(seq 1 90); do
    [ "$(healthy_version)" = "$1" ] && return 0
    sleep 1
  done
  return 1
}

start_and_check() {  # start_and_check <previous version or empty>
  local prev="$1"
  as_root systemctl enable --quiet "$UNIT.service"
  as_root systemctl restart "$UNIT.service"
  if wait_healthy "$VERSION"; then
    return 0
  fi
  warn "solar-kiosk $VERSION didn't start properly. Recent log:"
  as_root journalctl -u "$UNIT" -n 15 --no-pager >&2 || true
  printf '%s\n' "$VERSION" | as_root tee -a "$SKIP" >/dev/null
  if [ -n "$prev" ] && [ "$prev" != "$VERSION" ] && [ -d "$PREFIX/releases/$prev" ]; then
    switch_to "$prev"
    as_root systemctl restart "$UNIT.service"
    if wait_healthy "$prev"; then
      die "Went back to solar-kiosk $prev, which is running. $VERSION won't be tried again automatically."
    fi
    die "Went back to solar-kiosk $prev, but it isn't answering either. See: journalctl -u $UNIT"
  fi
  die "solar-kiosk didn't start. See: journalctl -u $UNIT"
}

# --- full-screen browser on this Pi's own display (optional) ---------------------------------------------

has_screen() {
  [ "$(systemctl get-default 2>/dev/null)" = graphical.target ] || return 1
  grep -qx connected /sys/class/drm/*/status 2>/dev/null
}

desktop_home() { getent passwd "$1" | cut -d: -f6; }

setup_kiosk() {
  local want="$KIOSK" user="$DESKTOP_USER" home browser
  if [ "$want" = auto ]; then
    if [ -n "$user" ] && [ "$user" != root ] && has_screen; then want=yes; else want=no; fi
  fi
  if [ "$want" != yes ]; then
    remove_kiosk
    return 0
  fi
  [ -n "$user" ] && [ "$user" != root ] || die "--kiosk needs to be run by the desktop user (not root), e.g. with sudo."
  home=$(desktop_home "$user")
  [ -n "$home" ] && [ -d "$home" ] || die "Can't find the home folder of $user."
  browser=$(command -v chromium || command -v chromium-browser || true)
  if [ -z "$browser" ]; then
    install_packages chromium
    browser=$(command -v chromium || command -v chromium-browser || true)
    [ -n "$browser" ] || die "Couldn't install the Chromium browser."
  fi
  step "Setting up the full-screen solar page for $user's desktop"
  as_root tee "$PREFIX/kiosk-browser.sh" >/dev/null <<EOF
#!/bin/sh
# Opens the solar page full screen. Started at login by ~/.config/autostart/solar-kiosk.desktop.
# Written by solar-kiosk's install.sh.
i=0
while [ \$i -lt 90 ] && ! curl -fs --max-time 2 http://localhost:$PORT/api/health >/dev/null 2>&1; do
  i=\$((i + 1)); sleep 1
done
exec "$browser" --kiosk --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble \\
  --password-store=basic --user-data-dir="\$HOME/.config/solar-kiosk-browser" http://localhost:$PORT/
EOF
  as_root chmod 0755 "$PREFIX/kiosk-browser.sh"
  as_root install -d -o "$user" -g "$(id -gn "$user")" "$home/.config" "$home/.config/autostart"
  as_root tee "$home/.config/autostart/solar-kiosk.desktop" >/dev/null <<EOF
[Desktop Entry]
Type=Application
Name=solar-kiosk
Comment=The solar page, full screen
Exec=$PREFIX/kiosk-browser.sh
X-GNOME-Autostart-enabled=true
EOF
  as_root chown "$user:$(id -gn "$user")" "$home/.config/autostart/solar-kiosk.desktop"
  if command -v raspi-config >/dev/null; then
    as_root raspi-config nonint do_blanking 1 || warn "Couldn't turn off screen blanking."
  fi
  KIOSK_ON=yes
}

remove_kiosk() {
  local home
  if [ -n "$DESKTOP_USER" ] && home=$(desktop_home "$DESKTOP_USER") && [ -n "$home" ]; then
    as_root rm -f "$home/.config/autostart/solar-kiosk.desktop"
  fi
  as_root rm -f "$PREFIX/kiosk-browser.sh"
}

# --- the actions -----------------------------------------------------------------------------------------

lan_address() { hostname -I 2>/dev/null | awk '{print $1}'; }

finish_message() {
  local host addr
  host=$(hostname)
  addr=$(lan_address)
  cat <<EOF

solar-kiosk $VERSION is running.

  Open it on any computer or phone on this network:
      http://$host.local:$PORT/${addr:+     (or http://$addr:$PORT/)}
  Then click "Open settings" to connect your solar, battery and weather station.

EOF
  [ "${KIOSK_ON:-no}" = yes ] && say "  This Pi's screen will show it full screen after the next login or restart."
  if [ "$AUTO_UPDATE" = yes ]; then
    say "  Updates: checked every night around 3 am. To turn off: sudo $PREFIX/install.sh --no-auto-update"
  else
    say "  Updates: off. Update by hand with: sudo $PREFIX/install.sh --update"
  fi
  say "  Remove:  sudo $PREFIX/install.sh --uninstall"
  say ""
}

do_install() {
  install_packages curl ca-certificates python3
  if port_busy_by_other; then
    die "Port $PORT is already in use by another program. Choose another with --port, e.g. --port 8081."
  fi
  ensure_uv
  local tag="" prev
  if [ -z "$FROM" ]; then
    tag="${WANT_VERSION:+v${WANT_VERSION#v}}"
    [ -n "$tag" ] || tag=$(latest_tag) || die "Couldn't find the latest release on GitHub. Check the internet connection."
  fi
  step "Downloading and checking the release${tag:+ ($tag)}"
  fetch_release "$tag"
  prev=$(current_version)
  setup_user
  install_release
  switch_to "$VERSION"
  as_root install -m 0755 "$TMP/release/install.sh" "$PREFIX/install.sh"
  write_units
  save_conf
  step "Starting solar-kiosk"
  start_and_check "$prev"
  apply_auto_update
  setup_kiosk
  save_conf
  prune_releases
  finish_message
}

do_update() {
  [ -L "$PREFIX/current" ] || die "solar-kiosk isn't installed here. Run the installer first."
  local current tag prev
  current=$(current_version)
  if [ -z "$FROM" ]; then
    tag=$(latest_tag) || die "Couldn't reach GitHub to check for updates."
    if [ "${tag#v}" = "$current" ]; then say "solar-kiosk $current is up to date."; return 0; fi
    if [ -f "$SKIP" ] && grep -qx -- "${tag#v}" "$SKIP"; then
      say "solar-kiosk ${tag#v} failed to start before, so it's skipped. Staying on $current."
      return 0
    fi
  fi
  step "Updating solar-kiosk $current to ${tag:-the release in $FROM}"
  fetch_release "${tag:-}"
  [ "$VERSION" = "$current" ] && { say "solar-kiosk $current is up to date."; return 0; }
  ensure_uv
  prev="$current"
  install_release
  switch_to "$VERSION"
  write_units
  start_and_check "$prev"
  as_root install -m 0755 "$TMP/release/install.sh" "$PREFIX/install.sh"  # the updater updates itself last
  prune_releases
  say "Updated solar-kiosk $prev -> $VERSION."
}

do_uninstall() {
  step "Removing solar-kiosk"
  as_root systemctl disable --now --quiet "$UNIT-update.timer" "$UNIT.service" 2>/dev/null || true
  as_root rm -f "/etc/systemd/system/$UNIT.service" "/etc/systemd/system/$UNIT-update.service" \
    "/etc/systemd/system/$UNIT-update.timer" "$WRAPPER" "$TIMER_STAMP"
  as_root systemctl daemon-reload
  remove_kiosk
  as_root rm -rf "${PREFIX:?}"
  if [ "$PURGE" = yes ]; then
    as_root rm -rf "${DATA:?}"
    id "$SVC_USER" >/dev/null 2>&1 && as_root userdel "$SVC_USER" 2>/dev/null || true
    say "solar-kiosk, its settings and saved logins are removed."
  else
    say "solar-kiosk is removed. Its settings (including saved logins) are kept in $DATA."
    say "To delete those too: curl -fsSL https://github.com/$REPO/releases/latest/download/install.sh | bash -s -- --uninstall --purge"
  fi
}

# --- main ------------------------------------------------------------------------------------------------

ACTION=install; FROM=""; WANT_VERSION=""; PURGE=no
OPT_PORT=""; OPT_KIOSK=""; OPT_AUTO=""

main() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --port)           [[ "${2:-}" =~ ^[0-9]+$ ]] || die "--port needs a number"; OPT_PORT="$2"; shift ;;
      --kiosk)          OPT_KIOSK=yes ;;
      --no-kiosk)       OPT_KIOSK=no ;;
      --auto-update)    OPT_AUTO=yes ;;
      --no-auto-update) OPT_AUTO=no ;;
      --version)        [ -n "${2:-}" ] || die "--version needs a version, e.g. 0.1.0"; WANT_VERSION="$2"; shift ;;
      --from)           [ -d "${2:-}" ] || die "--from needs a folder"; FROM=$(cd "$2" && pwd); shift ;;
      --update)         ACTION=update ;;
      --uninstall)      ACTION=uninstall ;;
      --purge)          PURGE=yes ;;
      -h|--help)        usage; exit 0 ;;
      *)                die "Unknown option: $1 (see --help)" ;;
    esac
    shift
  done
  [ "$PURGE" = yes ] && [ "$ACTION" != uninstall ] && die "--purge only goes with --uninstall."

  check_system
  need_root
  load_conf
  # who sees the full-screen page: the person who ran this (through sudo, or directly)
  if [ -z "$DESKTOP_USER" ]; then
    DESKTOP_USER="${SUDO_USER:-$(id -un)}"
    [ "$DESKTOP_USER" = root ] && DESKTOP_USER=""
  fi
  PORT="${OPT_PORT:-$PORT}"; KIOSK="${OPT_KIOSK:-$KIOSK}"; AUTO_UPDATE="${OPT_AUTO:-$AUTO_UPDATE}"

  as_root install -d -m 0755 "$PREFIX"
  # One root-owned lock file, opened read-only (flock works on any open file), so a run by hand and the
  # nightly update (as root) always see each other's lock, whoever created the file.
  [ -e "$PREFIX/.lock" ] || as_root install -m 0644 /dev/null "$PREFIX/.lock"
  exec 9<"$PREFIX/.lock"
  flock -n 9 || die "Another install or update is running. Try again in a few minutes."
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT

  case "$ACTION" in
    install)   do_install ;;
    update)    do_update ;;
    uninstall) do_uninstall ;;
  esac
}

main "$@"
