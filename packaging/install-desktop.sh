#!/usr/bin/env bash
#
# Install the desktop entry and icon for the current user, so md-view shows
# up in the application menu and as a handler for markdown files.
#
# Nothing here needs root: everything lands under $XDG_DATA_HOME.
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
# The entry and the window that opens from it are md-view's. The icon is
# installed under the diff's id as well, so a window git difftool opens is
# not left with the generic default.
ID=org.user.local.md-view
DIFF_ID=org.user.local.md-diff-view

mkdir -p "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"

install -m644 "$REPO/packaging/icons/$DIFF_ID.svg" \
              "$DATA/icons/hicolor/scalable/apps/$ID.svg"
install -m644 "$REPO/packaging/icons/$DIFF_ID.svg" \
              "$DATA/icons/hicolor/scalable/apps/$DIFF_ID.svg"

# Rewrite Exec/TryExec to an absolute path: a desktop session's PATH is not
# the shell's, and ~/bin is often missing from it.
sed -e "s|^Exec=md-view|Exec=$REPO/scripts/md-view.sh|" \
    -e "s|^TryExec=md-view|TryExec=$REPO/scripts/md-view.sh|" \
    "$REPO/packaging/desktop/$ID.desktop" > "$DATA/applications/$ID.desktop"
chmod 644 "$DATA/applications/$ID.desktop"

# Each of these is absent on some desktops; none of them is fatal.
command -v update-desktop-database >/dev/null &&
    update-desktop-database "$DATA/applications" || true
command -v gtk4-update-icon-cache >/dev/null &&
    gtk4-update-icon-cache -qtf "$DATA/icons/hicolor" || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 --noincremental >/dev/null 2>&1 ||
    { command -v kbuildsycoca5 >/dev/null && kbuildsycoca5 >/dev/null 2>&1; } || true

echo "Installed:"
echo "  $DATA/applications/$ID.desktop"
echo "  $DATA/icons/hicolor/scalable/apps/$ID.svg"
echo "  $DATA/icons/hicolor/scalable/apps/$DIFF_ID.svg"
echo
echo "Make it the default for markdown with:"
echo "  xdg-mime default $ID.desktop text/markdown"
