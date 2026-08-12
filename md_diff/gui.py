#!/usr/bin/env python3
"""Meld-like window for rendered markdown diffs.

Hosts the HTML produced by `md_diff.rich_diff` in a WebKit view, so
`git difftool` can show a rendered diff directly instead of writing a
file and handing it to a browser.

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

# Highlight for the change the user has navigated to. Kept here rather than
# in the shared CSS so the CLI's HTML output is unaffected.
NAV_CSS = """
.md-diff-current {
    outline: 3px solid #0969da;
    outline-offset: 3px;
    border-radius: 3px;
    scroll-margin: 40vh;
}
"""

# Collects the top-level change elements and scrolls between them. Returns
# "<position>/<total>" so the header bar can show progress. Nested changes
# (e.g. an <ins> inside an added block) are folded into their parent so one
# keypress moves one visible change.
NAV_JS = r"""
(function () {
  var SEL = 'ins, del, .diff-added-section, .diff-removed-section,' +
            '.diff-added-block, .diff-removed-block,' +
            'tr.diff-row-inserted, tr.diff-row-deleted, tr.diff-row-changed';

  if (!window.__mdDiffNav) {
    window.__mdDiffNav = {
      idx: -1,
      nodes: [],
      refresh: function () {
        var all = Array.prototype.slice.call(document.querySelectorAll(SEL))
          .filter(function (n) { return !n.closest('.diff-legend'); });
        this.nodes = all.filter(function (n) {
          return !all.some(function (o) { return o !== n && o.contains(n); });
        });
        if (this.idx >= this.nodes.length) this.idx = this.nodes.length - 1;
        return this.nodes.length;
      },
      label: function () {
        if (!this.nodes.length) return 'no changes';
        if (this.idx < 0) return this.nodes.length + ' changes';
        return (this.idx + 1) + ' / ' + this.nodes.length;
      },
      go: function (delta) {
        if (!this.nodes.length) return this.label();
        this.idx = (this.idx + delta + this.nodes.length) % this.nodes.length;
        var n = this.nodes[this.idx];
        Array.prototype.forEach.call(
          document.querySelectorAll('.md-diff-current'),
          function (e) { e.classList.remove('md-diff-current'); });
        n.classList.add('md-diff-current');
        n.scrollIntoView({ block: 'center', behavior: 'smooth' });
        return this.label();
      }
    };
    window.__mdDiffNav.refresh();
  }
  return window.__mdDiffNav.__CALL__;
})()
"""


def _require_gtk():
    """Import GTK4 + WebKit 6, with an actionable message if unavailable."""
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("WebKit", "6.0")
        from gi.repository import Gtk, WebKit, Gio, Gdk, GLib, Pango
    except (ImportError, ValueError) as exc:
        sys.exit(
            f"Error: GTK4 / WebKit 6 Python bindings not available ({exc}).\n"
            "\n"
            "On Debian/Ubuntu:\n"
            "    sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0\n"
            "\n"
            "If md-diff is installed in a virtualenv, that venv must be able to\n"
            "see the system PyGObject:\n"
            "    python3 -m venv --system-site-packages .venv\n"
        )
    return Gtk, WebKit, Gio, Gdk, GLib, Pango


SANDBOX_ENV = "WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS"

SANDBOX_HELP = """\
Error: WebKit cannot start its sandbox on this system.
Reason: {reason}

WebKit renders page content in a bubblewrap sandbox, which needs unprivileged
user namespaces. Ubuntu 24.04 and later restrict these by default, so the web
process dies with "bwrap: setting up uid map: Permission denied".

Two ways forward:

  1. Run this tool without the sandbox (no root, affects nothing else):

         md-diff-gui ... --no-sandbox

  2. Re-enable unprivileged user namespaces for the whole machine:

         sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0

     Persist it in /etc/sysctl.d/60-userns.conf. This restores the sandbox
     here, but also removes a system-wide hardening measure.

Option 1 is usually the better trade: the pages rendered are generated from
local files with no network access. The caveat is that pandoc passes raw HTML
in a markdown file straight through, so a document from a branch you do not
trust could run scripts in an unsandboxed renderer.

(A per-application AppArmor profile is not a practical third option here: the
profile would attach to the Python interpreter, not to this script, and so
would grant `userns` to every Python process on the system.)
"""


def userns_restricted() -> str | None:
    """Return why unprivileged user namespaces are unavailable, or None.

    WebKit's web process cannot start without them, and it fails by dumping
    core rather than reporting anything useful, so check up front.
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


def show_diff(old: Path, new: Path, label_old: str, label_new: str) -> int:
    """Open the rendered diff in a window. Returns a process exit code."""
    Gtk, WebKit, Gio, Gdk, GLib, Pango = _require_gtk()

    title = f"{label_old} → {label_new}"
    document = build_document(diff_files(old, new), title, extra_css=NAV_CSS)

    class DiffWindow(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title=title,
                             default_width=1100, default_height=850)

            self.counter = Gtk.Label(label="…")
            self.counter.add_css_class("dim-label")

            header = Gtk.HeaderBar()
            header.set_title_widget(self.build_title(label_old, label_new))
            for icon, delta, tip in (
                ("go-up-symbolic", -1, "Previous change (p / Shift+Tab / Alt+Up)"),
                ("go-down-symbolic", 1, "Next change (n / Tab / Alt+Down)"),
            ):
                button = Gtk.Button(icon_name=icon, tooltip_text=tip)
                button.connect("clicked", lambda _b, d=delta: self.navigate(d))
                header.pack_start(button)
            header.pack_end(self.counter)
            self.set_titlebar(header)

            self.webview = WebKit.WebView()
            self.webview.connect("load-changed", self.on_load_changed)
            self.webview.load_html(document, None)
            self.set_child(self.webview)

            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self.on_key)
            # CAPTURE, not the default BUBBLE: the WebView holds focus and
            # claims arrow keys for scrolling, so Alt+Up/Down would never
            # reach a bubble-phase handler.  Keys we don't bind still return
            # False here and propagate on, leaving plain Up/Down scrolling
            # and WebKit's own shortcuts intact.
            keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            self.add_controller(keys)

        @staticmethod
        def build_title(label_old: str, label_new: str) -> "Gtk.Widget":
            """Two-line header title (GTK4 has no Adwaita WindowTitle)."""
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                          valign=Gtk.Align.CENTER)
            primary = Gtk.Label(label=label_new, ellipsize=Pango.EllipsizeMode.END)
            primary.add_css_class("title")
            secondary = Gtk.Label(label=f"was: {label_old}", ellipsize=Pango.EllipsizeMode.END)
            secondary.add_css_class("subtitle")
            secondary.add_css_class("dim-label")
            box.append(primary)
            box.append(secondary)
            return box

        # --- JavaScript bridge ---

        def run_js(self, call: str) -> None:
            # Token replace, not %-formatting: the script contains a JS
            # modulo operator.
            self.webview.evaluate_javascript(
                NAV_JS.replace("__CALL__", call), -1, None, None, None,
                self.on_js_result)

        def on_js_result(self, webview, result, *_):
            try:
                value = webview.evaluate_javascript_finish(result)
            except GLib.Error:
                return
            if value is not None and value.is_string():
                self.counter.set_label(value.to_string())

        def navigate(self, delta: int) -> None:
            self.run_js(f"go({delta})")

        def on_load_changed(self, _webview, event) -> None:
            if event == WebKit.LoadEvent.FINISHED:
                self.run_js("label()")

        # --- Input ---

        def on_key(self, _controller, keyval, _keycode, state) -> bool:
            ctrl = state & Gdk.ModifierType.CONTROL_MASK
            shift = state & Gdk.ModifierType.SHIFT_MASK
            alt = state & Gdk.ModifierType.ALT_MASK
            name = Gdk.keyval_name(keyval)

            if name in ("Escape", "q") or (ctrl and name == "w"):
                self.close()
            elif name == "n" or (name == "Tab" and not shift) or (alt and name == "Down"):
                self.navigate(1)
            elif name == "p" or name == "ISO_Left_Tab" or (alt and name == "Up"):
                self.navigate(-1)
            elif ctrl and name in ("plus", "equal", "KP_Add"):
                self.webview.set_zoom_level(self.webview.get_zoom_level() * 1.1)
            elif ctrl and name in ("minus", "KP_Subtract"):
                self.webview.set_zoom_level(self.webview.get_zoom_level() / 1.1)
            elif ctrl and name == "0":
                self.webview.set_zoom_level(1.0)
            else:
                return False
            return True

    class DiffApp(Gtk.Application):
        def __init__(self):
            # NON_UNIQUE: git difftool invokes us once per file and waits for
            # each to exit. Sharing one instance would let later invocations
            # return immediately and break that sequencing.
            super().__init__(application_id="uk.co.dnorth.md-diff",
                             flags=Gio.ApplicationFlags.NON_UNIQUE)
            self.connect("activate", lambda app: DiffWindow(app).present())

    return DiffApp().run([])


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
              f"         WebKit cannot start here, so the tool will fail when\n"
              f"         git invokes it. Re-run with --no-sandbox to bake that\n"
              f"         flag into the config, or see `md-diff-gui a.md b.md`\n"
              f"         for the alternatives.\n", file=sys.stderr)

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

    reason = userns_restricted()
    if reason and not args.no_sandbox:
        print(SANDBOX_HELP.format(reason=reason), file=sys.stderr)
        return 1
    if args.no_sandbox:
        # Must be set before WebKit spawns its web process.
        os.environ[SANDBOX_ENV] = "1"
        print("Warning: running WebKit without its content sandbox",
              file=sys.stderr)

    return show_diff(args.old, args.new, label_old, label_new)


if __name__ == "__main__":
    sys.exit(main())
