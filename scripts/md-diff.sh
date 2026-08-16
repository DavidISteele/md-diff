#!/usr/bin/env bash
set -euo pipefail

#SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR=/home/david/Git/md-diff

# The window is viewer/md-diff-view, a separate binary, so the Python side
# needs nothing but lxml -- the system python3 has it.  Running the repo in
# place via PYTHONPATH means edits take effect immediately.
if ! python3 -c 'import lxml' 2>/dev/null; then
    echo "md-diff.sh: python3 is missing lxml. Install with:" >&2
    echo "  sudo apt install python3-lxml" >&2
    exit 1
fi

if [[ ! -x "$SCRIPT_DIR/viewer/md-diff-view" ]]; then
    echo "md-diff.sh: the viewer is not built. Build it with:" >&2
    echo "  make -C $SCRIPT_DIR/viewer" >&2
    exit 1
fi

# git difftool passes $LOCAL $REMOTE $BASE.  $BASE is the repo-relative path,
# which reads better in the window than git's temporary checkout names.
old=$1
new=$2
label=${3:-$(basename "$2")}

# No --no-sandbox: packaging/apparmor/md-diff-view is installed, which grants
# that one binary the unprivileged user namespaces WebKit's bubblewrap sandbox
# needs on this machine.  `md-diff-view --check-sandbox` reports the state.
exec env PYTHONPATH="$SCRIPT_DIR" python3 -m md_diff.gui "$old" "$new" \
     --label-old "$label (old)" \
     --label-new "$label (new)" \
     --fallback meld
