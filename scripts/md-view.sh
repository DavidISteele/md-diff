#!/usr/bin/env bash
set -euo pipefail

#SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR=/home/david/Git/md-diff

# No venv: the window needs the system PyGObject (GTK4 + WebKit), which is not
# pip-installable, and the system python3 already has lxml too.  Running the
# repo in place via PYTHONPATH also means edits take effect immediately.
if ! python3 -c 'import gi, lxml' 2>/dev/null; then
    echo "md-view.sh: python3 is missing gi and/or lxml. Install with:" >&2
    echo "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0 python3-lxml" >&2
    exit 1
fi

# --no-sandbox: this machine sets kernel.apparmor_restrict_unprivileged_userns=1,
# which stops WebKit's bubblewrap sandbox from starting.  See the README.
# Harmless with --output, which never opens a window.
exec env PYTHONPATH="$SCRIPT_DIR" python3 -m md_diff.view --no-sandbox "$@"
