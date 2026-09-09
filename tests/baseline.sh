#!/usr/bin/env bash
#
# Baseline for the contracts that must survive the md-view session refactor.
#
# Written against the behaviour as it stands *before* tabs, so it is a
# regression baseline rather than a description of whatever replaces it.
# The thing most at risk is git difftool: md-diff and md-view share one
# binary, so restructuring the window for md-view can break the difftool
# path without touching a line that difftool runs.
#
# Most assertions use a stub viewer through MD_DIFF_VIEWER (gui.find_viewer
# honours it), which makes them headless and deterministic -- the stub
# records how it was called and what reached its stdin.  One test uses the
# real binary, because "the wrapper does not return before the window
# closes" is a property of the binary and nothing else can prove it.
#
# Usage: tests/baseline.sh          # all tests
#        tests/baseline.sh --no-gui # skip the test that opens a window
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

RUN_GUI=1
[[ "${1:-}" == "--no-gui" ]] && RUN_GUI=0

PASS=0
FAIL=0

ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; PASS=$((PASS + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL + 1)); }
note() { printf '  \033[33mSKIP\033[0m %s\n' "$1"; }

check() {  # check <description> <condition-as-exit-status>
    if [[ $2 -eq 0 ]]; then ok "$1"; else bad "$1"; fi
}

# --- Fixtures ---------------------------------------------------------------

BIN="$WORK/bin"
mkdir -p "$BIN"

# The stub viewer stands in for md-diff-view.  It logs a START/END pair so
# overlapping invocations are visible, and copies stdin so the test can see
# the document that would have been rendered.  Exits with $STUB_EXIT to
# prove the wrapper propagates a status.
cat > "$BIN/stub-viewer" <<'STUB'
#!/usr/bin/env bash
log="$STUB_LOG"
n=$(ls "$STUB_DIR"/doc.* 2>/dev/null | wc -l)
echo "START $*" >> "$log"
cat > "$STUB_DIR/doc.$n"
# Long enough that a concurrent invocation would interleave the log.
sleep 0.3
echo "END" >> "$log"
exit "${STUB_EXIT:-0}"
STUB

# Stands in for meld: records that a non-markdown pair was handed off.
cat > "$BIN/stub-fallback" <<'STUB'
#!/usr/bin/env bash
echo "FALLBACK $*" >> "$STUB_LOG"
exit 0
STUB

chmod +x "$BIN/stub-viewer" "$BIN/stub-fallback"
export PATH="$BIN:$PATH"
export PYTHONPATH="$REPO"

reset_log() {
    STUB_DIR="$WORK/capture.$RANDOM"
    mkdir -p "$STUB_DIR"
    STUB_LOG="$STUB_DIR/log"
    : > "$STUB_LOG"
    export STUB_DIR STUB_LOG
}

# A scratch repo with one markdown file and one that is not, each changed
# once, so `git difftool` has both a file to render and a file to hand off.
build_repo() {
    local repo="$WORK/scratch"
    rm -rf "$repo"
    mkdir -p "$repo"
    git -C "$repo" init -q
    git -C "$repo" config user.email t@example.com
    git -C "$repo" config user.name Test

    printf '# Title\n\nOriginal paragraph.\n' > "$repo/doc.md"
    printf 'plain text v1\n' > "$repo/notes.txt"
    git -C "$repo" add -A
    git -C "$repo" commit -qm one

    printf '# Title\n\nRewritten paragraph.\n' > "$repo/doc.md"
    printf 'plain text v2\n' > "$repo/notes.txt"
    git -C "$repo" add -A
    git -C "$repo" commit -qm two
    echo "$repo"
}

echo
echo "Baseline: contracts the session refactor must preserve"
echo

# --- 1. git difftool ---------------------------------------------------------

echo "git difftool"

REPO_DIR="$(build_repo)"
reset_log
export MD_DIFF_VIEWER="$BIN/stub-viewer"

CMD="python3 -m md_diff.gui \"\$LOCAL\" \"\$REMOTE\" \
--label-old \"\$BASE (old)\" --label-new \"\$BASE (new)\" --fallback stub-fallback"

git -C "$REPO_DIR" \
    -c "difftool.baseline.cmd=$CMD" \
    -c difftool.prompt=false \
    difftool -y --tool=baseline HEAD~1 >/dev/null 2>&1
status=$?

check "difftool run exits 0" $status

grep -q 'START' "$STUB_LOG"; check "markdown reached the viewer" $?

grep -q 'FALLBACK' "$STUB_LOG"; check "non-markdown went to the fallback" $?

# git invokes the tool once per file and waits for each.  A viewer that
# returned early -- an attaching, single-instance one -- would let the next
# invocation start before the previous finished, and the log would show a
# second START before the first END.
awk '/^START/ { if (open) exit 1; open = 1 }
     /^END/   { open = 0 }
     END      { exit 0 }' "$STUB_LOG"
check "difftool invocations do not overlap (git's wait is honoured)" $?

# md-diff must never join a session.  Sharing an instance would let an
# invocation return before its window closed, breaking git's wait -- and
# would put a diff in the window documents are being read in.
grep -q -- '--session' "$STUB_LOG"
[[ $? -ne 0 ]]; check "difftool never asks to join a session" $?

# The document is a whole standalone page, built before the viewer starts:
# nothing reads the temp checkouts afterwards.
grep -q '<!DOCTYPE html>' "$STUB_DIR/doc.0"; check "viewer receives a standalone document on stdin" $?
# Word-level, so the changed word is wrapped rather than left in the run
# of text: <ins>Rewritten</ins> <del>Original</del> paragraph.
grep -q '<ins>Rewritten</ins>' "$STUB_DIR/doc.0" &&
    grep -q '<del>Original</del>' "$STUB_DIR/doc.0"
check "document carries the word-level diff" $?

# --- 2. exit status ----------------------------------------------------------

echo
echo "exit status"

reset_log
STUB_EXIT=3 python3 -m md_diff.gui \
    "$REPO_DIR/doc.md" "$REPO_DIR/doc.md" >/dev/null 2>&1
[[ $? -eq 3 ]]; check "md-diff-gui propagates the viewer's exit status" $?

reset_log
python3 -m md_diff.gui "$WORK/missing.md" "$REPO_DIR/doc.md" >/dev/null 2>&1
[[ $? -eq 1 ]]; check "md-diff-gui reports a missing file as exit 1" $?

# --- 3. md-view --------------------------------------------------------------

echo
echo "md-view"

reset_log
python3 -m md_diff.view "$REPO_DIR/doc.md" >/dev/null 2>&1
status=$?
check "md-view opens a file" $status
grep -q 'START' "$STUB_LOG"; check "md-view reaches the viewer" $?
grep -q -- '--no-navigation' "$STUB_LOG"; check "md-view suppresses the change stepper" $?
grep -q -- '--session org.user.local.md-view' "$STUB_LOG"
check "md-view joins its own session" $?

reset_log
python3 -m md_diff.view "$REPO_DIR/doc.md" --new-window >/dev/null 2>&1
grep -q -- '--session' "$STUB_LOG"
[[ $? -ne 0 ]]; check "md-view --new-window opts out of the session" $?

# --output is a pure renderer.  It must never reach the viewer -- and once
# md-view attaches to a running window, must never attach either.
reset_log
python3 -m md_diff.view "$REPO_DIR/doc.md" -o "$WORK/out.html" >/dev/null 2>&1
status=$?
check "md-view --output exits 0" $status
[[ -s "$WORK/out.html" ]]; check "md-view --output writes HTML" $?
[[ ! -s "$STUB_LOG" ]]; check "md-view --output never starts a viewer" $?

# Markdown on stdin, for the end of a pipeline.
reset_log
printf '# Piped\n\nBody.\n' | python3 -m md_diff.view >/dev/null 2>&1
status=$?
check "md-view reads markdown from a pipe" $status
grep -q 'Piped' "$STUB_DIR/doc.0" 2>/dev/null; check "piped document reaches the viewer" $?

# Several files open together; the arguments that cannot mean anything are
# refused before a viewer is started.
reset_log
python3 -m md_diff.view "$REPO_DIR/doc.md" "$WORK/nothing.md" >/dev/null 2>&1
[[ $? -eq 1 ]]; check "md-view rejects a missing file among several" $?
[[ ! -s "$STUB_LOG" ]]; check "a missing file starts no viewer at all" $?

reset_log
printf '# Two\n' > "$WORK/two.md"
python3 -m md_diff.view "$REPO_DIR/doc.md" "$WORK/two.md" -o "$WORK/both.html" \
    >/dev/null 2>&1
[[ $? -eq 1 ]]; check "md-view refuses --output for several files" $?
[[ ! -s "$STUB_LOG" ]]; check "a refused --output starts no viewer" $?

# --- 4. the real binary blocks ----------------------------------------------

echo
echo "viewer process"

unset MD_DIFF_VIEWER

if [[ $RUN_GUI -eq 0 ]]; then
    note "real-binary blocking test (--no-gui)"
elif [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    note "real-binary blocking test (no display)"
elif [[ ! -x "$REPO/viewer/md-diff-view" ]]; then
    note "real-binary blocking test (viewer not built)"
else
    # The contract git depends on: md-diff.sh must not return while the
    # window is up.  Proven by watching it still be alive with a window
    # open, then exit once the viewer is gone.
    python3 -m md_diff.gui "$REPO_DIR/doc.md" "$REPO_DIR/doc.md" \
        --label-old old --label-new new >/dev/null 2>&1 &
    wrapper=$!

    for _ in $(seq 30); do
        pgrep -x md-diff-view >/dev/null && break
        sleep 0.2
    done

    sleep 1
    kill -0 "$wrapper" 2>/dev/null
    check "md-diff blocks while the window is open" $?

    pkill -x md-diff-view

    exited=1
    for _ in $(seq 25); do
        kill -0 "$wrapper" 2>/dev/null || { exited=0; break; }
        sleep 0.2
    done
    check "md-diff returns once the window closes" $exited
    wait "$wrapper" 2>/dev/null
fi

# --- 5. sessions, end to end -------------------------------------------------

echo
echo "md-view session"

viewers() { pgrep -xc md-diff-view 2>/dev/null || true; }

if [[ $RUN_GUI -eq 0 ]]; then
    note "session tests (--no-gui)"
elif [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    note "session tests (no display)"
elif [[ ! -x "$REPO/viewer/md-diff-view" ]]; then
    note "session tests (viewer not built)"
elif [[ $(viewers) -ne 0 ]]; then
    note "session tests (a viewer is already running)"
else
    # The first invocation becomes the primary instance and holds the
    # window, so it waits -- as md-view has always done.
    python3 -m md_diff.view "$REPO_DIR/doc.md" >/dev/null 2>&1 &
    first=$!
    for _ in $(seq 40); do
        [[ $(viewers) -gt 0 ]] && break
        sleep 0.25
    done
    sleep 1
    [[ $(viewers) -eq 1 ]]; check "md-view starts one viewer process" $?

    # The second joins it: it must come back rather than wait, and must not
    # have started a process of its own.
    printf '# Second\n\nAnother document.\n' > "$WORK/second.md"
    start=$(date +%s%N)
    timeout 20 python3 -m md_diff.view "$WORK/second.md" >/dev/null 2>&1
    second_status=$?
    elapsed=$(( ($(date +%s%N) - start) / 1000000 ))

    check "second md-view exits 0" $second_status
    [[ $elapsed -lt 5000 ]]
    check "second md-view returns instead of waiting (${elapsed}ms)" $?
    kill -0 "$first" 2>/dev/null
    check "the first md-view is still holding its window" $?
    sleep 0.5
    [[ $(viewers) -eq 1 ]]
    check "the second document joined the running process" $?

    # Never `xwininfo | grep` directly: pipefail reports xwininfo's status,
    # not grep's, and xwininfo fails whenever a window disappears while it is
    # walking the tree.  The answer looked like "no such window".
    window_named() {
        local tree
        tree=$(xwininfo -root -tree 2>/dev/null) || true
        grep -q "\"$1\"" <<<"$tree"
    }

    # One process is only half of it: the document has to be on screen, and
    # in the window that was already open rather than one of its own.
    # Windows are titled by the tab in front, so X can be asked directly.
    # GTK keeps a 1x1 helper window off-screen that is not one of ours.
    app_windows() {
        xwininfo -root -tree 2>/dev/null |
            grep '("md-diff-view" "md-diff-view")' |
            grep -vc '1x1+-100+-100'
    }

    if command -v xwininfo >/dev/null && [[ -n "${DISPLAY:-}" ]]; then
        for _ in $(seq 20); do
            window_named second.md && break
            sleep 0.25
        done
        window_named second.md
        check "the joined document is the tab in front" $?
        [[ $(app_windows) -eq 1 ]]
        check "both documents share one window" $?
        ! window_named doc.md
        check "the first document did not keep a window of its own" $?
    else
        note "window checks (no xwininfo)"
    fi

    # Undocking: the tab in front moves to a window of its own.  Driven
    # through the application action rather than a drag, which is the same
    # move by the same code and is the only half a script can perform.
    if command -v gapplication >/dev/null && [[ -n "${DISPLAY:-}" ]]; then
        gapplication action org.user.local.md-view detach-tab >/dev/null 2>&1
        check "detach-tab action is accepted" $?
        for _ in $(seq 20); do
            [[ $(app_windows) -eq 2 ]] && break
            sleep 0.25
        done
        [[ $(app_windows) -eq 2 ]]
        check "undocking gives the document a window of its own" $?
        [[ $(viewers) -eq 1 ]]
        check "undocking starts no second process" $?

        window_named second.md && window_named doc.md
        check "both documents are now on screen at once" $?
    else
        note "undock test (no gapplication)"
    fi

    # Pinning the tab strip is what makes two single-document windows
    # dockable again: each needs a strip, one to drag from and one to drop
    # onto.  Whether the strip is drawn is not visible from outside the
    # process, so this asserts what is: the action works and disturbs
    # nothing.
    if command -v gapplication >/dev/null && [[ -n "${DISPLAY:-}" ]]; then
        gapplication action org.user.local.md-view toggle-tabs >/dev/null 2>&1
        check "toggle-tabs action is accepted" $?
        sleep 1
        [[ $(app_windows) -eq 2 ]]
        check "pinning the tab strip keeps both windows" $?
        [[ $(viewers) -eq 1 ]]
        check "pinning the tab strip starts no process" $?
    else
        note "tab pin test (no gapplication)"
    fi

    # The separation that matters: a diff opened while md-view is up gets
    # its own process, and waits.
    python3 -m md_diff.gui "$REPO_DIR/doc.md" "$REPO_DIR/doc.md" \
        --label-old old --label-new new >/dev/null 2>&1 &
    diff_pid=$!
    for _ in $(seq 40); do
        [[ $(viewers) -gt 1 ]] && break
        sleep 0.25
    done
    sleep 0.5
    [[ $(viewers) -eq 2 ]]
    check "md-diff opens a process of its own beside md-view" $?
    kill -0 "$diff_pid" 2>/dev/null
    check "md-diff still blocks while md-view is running" $?

    # And a window of its own: a diff must never arrive as a tab among the
    # documents being read.
    if command -v xwininfo >/dev/null && [[ -n "${DISPLAY:-}" ]]; then
        for _ in $(seq 20); do
            window_named 'old . new' && break
            sleep 0.25
        done
        # Three: the two md-view windows left by the undock, plus this one.
        [[ $(app_windows) -eq 3 ]]
        check "the diff opened a window of its own rather than a tab" $?
    fi

    pkill -x md-diff-view
    wait "$first" "$diff_pid" 2>/dev/null
    for _ in $(seq 20); do
        [[ $(viewers) -eq 0 ]] && break
        sleep 0.25
    done

    # Several files named at once become tabs in one window, in order.  The
    # window is titled by the tab in front, and the last one named is the one
    # left in front, so the title is what says the order came out right.
    printf '# Third\n\nThird document.\n' > "$WORK/third.md"
    printf '# Fourth\n\nFourth document.\n' > "$WORK/fourth.md"
    # It waits, like any md-view that ends up holding the window.
    timeout 60 python3 -m md_diff.view "$WORK/third.md" "$WORK/fourth.md" \
        >/dev/null 2>&1 &
    multi=$!
    for _ in $(seq 80); do
        xwininfo -root -tree 2>/dev/null | grep -q '"fourth.md"' && break
        sleep 0.25
    done
    kill -0 "$multi" 2>/dev/null
    check "md-view holds the window for several files" $?
    [[ $(viewers) -eq 1 ]]
    check "several files join the one process" $?
    [[ $(app_windows) -eq 1 ]]
    check "several files share one window" $?
    window_named fourth.md
    check "the last file named is the tab in front" $?
    ! window_named third.md
    check "the first file named is a tab, not a window of its own" $?

    pkill -x md-diff-view
    sleep 1
fi

# --- Summary -----------------------------------------------------------------

echo
printf 'baseline: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]]
