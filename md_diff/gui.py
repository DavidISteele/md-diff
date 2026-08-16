#!/usr/bin/env python3
"""Meld-like window for rendered markdown diffs.

Renders the diff with `md_diff.rich_diff` and shows it in md-diff-view, the
WebKit window built from viewer/, so `git difftool` can display a rendered
diff directly instead of writing a file and handing it to a browser.

The window is a separate binary rather than PyGObject so that an AppArmor
profile has an executable to attach to; see packaging/apparmor/md-diff-view.

Usage: md-diff-gui old.md new.md [--label-old L] [--label-new L]
       md-diff-gui --install-difftool
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from md_diff.rich_diff import build_document, diff_files

MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdwn",
                     ".mdtext", ".text"}

# Highlight for the change the viewer has navigated to, plus the scrollbar
# overview map. Kept here rather than in the shared CSS so the CLI's HTML
# output is unaffected; the script that drives both is viewer/nav.js.
NAV_CSS = """
.md-diff-current {
    outline: 3px solid #0969da;
    outline-offset: 3px;
    border-radius: 3px;
    scroll-margin: 40vh;
}

/* Replace the platform scrollbar with our own. WebKit's is an overlay that
   fades out, and nothing can be painted into its trough; the map below is
   always visible and carries the change markers. Scoped to the document
   scrollbar so <pre> keeps its normal horizontal one. */
html { scrollbar-width: none; }
html::-webkit-scrollbar { width: 0; height: 0; }

body { padding-right: 22px; }

.md-diff-map {
    position: fixed;
    top: 0;
    right: 0;
    width: 14px;
    height: 100vh;
    background: #f6f8fa;
    border-left: 1px solid #d1d9e0;
    z-index: 10;
}

/* Markers are decoration: clicks fall through to the track below. */
.md-diff-map-mark {
    position: absolute;
    left: 1px;
    right: 1px;
    border-radius: 1px;
    pointer-events: none;
}

.md-diff-map-mark.add { background: #10b981; }
.md-diff-map-mark.del { background: #ef4444; }
.md-diff-map-mark.chg { background: #eab308; }
.md-diff-map-mark.current { box-shadow: 0 0 0 1.5px #0969da; }

/* Translucent so the markers underneath stay readable. */
.md-diff-map-thumb {
    position: absolute;
    left: 1px;
    right: 1px;
    border-radius: 6px;
    background: rgba(101, 109, 118, 0.35);
    border: 1px solid rgba(101, 109, 118, 0.55);
}

.md-diff-map-thumb:hover, .md-diff-map.dragging .md-diff-map-thumb {
    background: rgba(101, 109, 118, 0.55);
}
"""

VIEWER = "md-diff-view"

VIEWER_MISSING = """\
Error: {viewer} not found.

The window is a small C program, built separately from the Python package
because it links GTK4 and WebKit:

    make -C {source}

Point MD_DIFF_VIEWER at the binary if you keep it somewhere else.
"""


def find_viewer() -> str | None:
    """Locate the viewer binary: environment override, repo build, then PATH."""
    override = os.environ.get("MD_DIFF_VIEWER")
    if override:
        return override if Path(override).exists() else None
    local = Path(__file__).resolve().parent.parent / "viewer" / VIEWER
    if local.exists():
        return str(local)
    return shutil.which(VIEWER)


def show_document(document: str, title: str, heading: str, subheading: str = "",
                  navigation: bool = True, no_sandbox: bool = False) -> int:
    """Show an HTML document in the viewer. Returns a process exit code.

    The document goes over stdin rather than a temporary file: it is the
    whole of what the viewer is trusted with, and nothing else on the system
    needs to be able to read it.

    `navigation` shows the change stepper -- the header buttons, the counter
    and the n/p keys.  A single rendered file has nothing to step through, so
    md-view turns it off.
    """
    viewer = find_viewer()
    if viewer is None:
        source = Path(__file__).resolve().parent.parent / "viewer"
        print(VIEWER_MISSING.format(viewer=VIEWER, source=source),
              file=sys.stderr)
        return 1

    command = [viewer, "--title", title, "--heading", heading]
    if subheading:
        command += ["--subheading", subheading]
    if not navigation:
        command.append("--no-navigation")
    if no_sandbox:
        command.append("--no-sandbox")

    return subprocess.run(command, input=document, text=True).returncode


def show_diff(old: Path, new: Path, label_old: str, label_new: str,
              no_sandbox: bool = False) -> int:
    """Open the rendered diff in a window. Returns a process exit code."""
    title = f"{label_old} → {label_new}"
    document = build_document(diff_files(old, new), title, extra_css=NAV_CSS)
    return show_document(document, title, label_new, f"was: {label_old}",
                         no_sandbox=no_sandbox)


def userns_restricted() -> str | None:
    """Return why unprivileged user namespaces are unavailable, or None.

    The viewer settles this properly at run time by trying to create one --
    the restriction is AppArmor-mediated, so a profile can grant what the
    sysctl appears to deny.  This coarser check exists for --install-difftool,
    which has no viewer invocation to learn from and should still warn.
    """
    checks = [
        ("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", "1",
         "AppArmor restricts unprivileged user namespaces "
         "(kernel.apparmor_restrict_unprivileged_userns=1)"),
        ("/proc/sys/user/max_user_namespaces", "0",
         "user namespaces are disabled (user.max_user_namespaces=0)"),
    ]
    for path, bad_value, reason in checks:
        try:
            if Path(path).read_text().strip() == bad_value:
                return reason
        except OSError:
            continue
    return None


def is_markdown(path: Path, label: str | None = None) -> bool:
    """True if this looks like a markdown file.

    git difftool preserves the original basename in its temp checkout, so
    the suffix is reliable; the label (git's $BASE) is checked as a fallback
    for tools that don't.
    """
    for candidate in (label, str(path)):
        if candidate and Path(candidate).suffix.lower() in MARKDOWN_SUFFIXES:
            return True
    return False


def run_fallback(command: str, old: Path, new: Path) -> int:
    """Hand a non-markdown pair off to another diff tool."""
    exe = shutil.which(command)
    if exe is None:
        print(f"Error: fallback diff tool {command!r} not found on PATH",
              file=sys.stderr)
        return 127
    return subprocess.call([exe, str(old), str(new)])


DIFFTOOL_CMD = ('md-diff-gui "$LOCAL" "$REMOTE" '
                '--label-old "$BASE (old)" --label-new "$BASE (new)" '
                '--fallback {fallback}{extra}')


def install_difftool(name: str, fallback: str, scope: str,
                     no_sandbox: bool) -> int:
    """Register this GUI as a git difftool that falls back for non-markdown."""
    cmd = DIFFTOOL_CMD.format(fallback=fallback,
                              extra=" --no-sandbox" if no_sandbox else "")

    reason = userns_restricted()
    if reason and not no_sandbox:
        print(f"Warning: {reason}.\n"
              f"         WebKit's sandbox cannot start, so the viewer will\n"
              f"         refuse to run when git invokes it. Install\n"
              f"         packaging/apparmor/md-diff-view to grant that one\n"
              f"         binary the permission, or re-run with --no-sandbox\n"
              f"         to bake that flag into the config instead.\n",
              file=sys.stderr)

    settings = [
        (f"difftool.{name}.cmd", cmd),
        ("diff.tool", name),
    ]
    for key, value in settings:
        subprocess.run(["git", "config", f"--{scope}", key, value], check=True)
        print(f"  {key} = {value}")
    print(f"\nRegistered difftool {name!r} ({scope}). "
          f"Markdown files open in this GUI; everything else goes to {fallback}.")
    print("Run it with:  git difftool <ref>")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", type=Path, nargs="?", help="Original markdown file")
    parser.add_argument("new", type=Path, nargs="?", help="Updated markdown file")
    parser.add_argument("--label-old", default=None,
                        help="Display name for the original (default: filename)")
    parser.add_argument("--label-new", default=None,
                        help="Display name for the update (default: filename)")
    parser.add_argument("--fallback", default=None, metavar="TOOL",
                        help="Diff tool to exec for non-markdown files, e.g. meld")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="Run WebKit without its content sandbox, for "
                             "systems that block unprivileged user namespaces")
    parser.add_argument("--install-difftool", action="store_true",
                        help="Configure git to use this GUI via `git difftool`")
    parser.add_argument("--name", default="md-diff",
                        help="difftool name to register (default: md-diff)")
    parser.add_argument("--local", action="store_true",
                        help="Install into the current repo instead of ~/.gitconfig")
    args = parser.parse_args()

    if args.install_difftool:
        return install_difftool(args.name, args.fallback or "meld",
                                "local" if args.local else "global",
                                args.no_sandbox)

    if args.old is None or args.new is None:
        parser.error("the following arguments are required: old, new")

    for path in (args.old, args.new):
        if not path.exists():
            print(f"Error: {path} not found", file=sys.stderr)
            return 1

    label_old = args.label_old or args.old.name
    label_new = args.label_new or args.new.name

    if args.fallback and not (is_markdown(args.old, args.label_old)
                              or is_markdown(args.new, args.label_new)):
        return run_fallback(args.fallback, args.old, args.new)

    if shutil.which("pandoc") is None:
        print("Error: pandoc not found on PATH (required to render markdown)",
              file=sys.stderr)
        return 1

    return show_diff(args.old, args.new, label_old, label_new, args.no_sandbox)


if __name__ == "__main__":
    sys.exit(main())
