#!/usr/bin/env python3
"""Render markdown files and open them in the md-diff window.

Usage: md-view [file.md ... | -] [--toc] [--new-window] [-o output.html]

Markdown can also arrive on stdin -- `pandoc -t gfm x.docx | md-view` --
either by naming `-` or just by piping, since a file argument is what the
desktop launcher lacks and stdin is what it has nothing on.

Several files named at once open as tabs in one window, in the order given.
Documents collect in one md-view: an invocation joins the instance already
running and returns, rather than opening a window of its own and waiting.
The diff never does -- see gui.VIEW_APP_ID for why the two stay apart.

The same renderer and the same window as the diff, minus the diff: a
document reads the same viewed as it does in `md-diff-gui`.  Pandoc builds
the whole document here rather than a fragment, because its template is
what supplies --toc and the embedded resources -- the window loads the
HTML as a string, with no base URI for relative images to resolve against.
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from md_diff.ascii_table import convert_ascii_tables
from md_diff.gui import (NAV_CSS, VIEW_APP_ID, show_document,
                          spawn_document)
from md_diff.rich_diff import CSS

# Rules that only apply to a document pandoc built from its own template.
# The diff builds its own document (see build_document) and needs none of
# them -- it has no table of contents, and its highlight colours assume a
# light background.
VIEW_CSS = """
/* Pandoc's template styles tables `display: block`, which drops table
   layout and shrink-wraps every cell to its content. */
table {
    display: table;
}

/* Anchored section links from --toc. */
#TOC {
    background: #f6f8fa;
    border: 1px solid #d1d9e0;
    border-radius: 6px;
    padding: 0.75em 1.5em;
    margin-bottom: 2em;
}

@media (prefers-color-scheme: dark) {
    body { color: #e6edf3; background: #0d1117; }
    h1, h2, h3, h4, h5, h6 { border-bottom-color: #3d444d; }
    th, td { border-color: #3d444d; }
    th { background: #161b22; }
    tr:nth-child(even) td { background: #161b2266; }
    code, pre { background: #161b22; }
    hr { border-top-color: #3d444d; }
    a { color: #4493f8; }
    #TOC { background: #161b22; border-color: #3d444d; }
    /* The window's scrollbar map (gui.NAV_CSS), which is otherwise a light
       strip down the edge of a dark page. */
    .md-diff-map { background: #161b22; border-left-color: #3d444d; }
}
"""


def embed_flag() -> str:
    """Pick the flag that inlines images and other resources.

    --embed-resources arrived in pandoc 2.19, renaming --self-contained,
    which is still accepted (deprecated) by later versions.  Debian
    bookworm ships 2.17, so ask the installed pandoc which one it knows
    rather than assuming.
    """
    try:
        version = subprocess.run(["pandoc", "--version"], capture_output=True,
                                 text=True, check=True).stdout.split()[1]
        release = tuple(int(part) for part in version.split(".")[:2])
    except (subprocess.CalledProcessError, OSError, IndexError, ValueError):
        return "--self-contained"
    return "--embed-resources" if release >= (2, 19) else "--self-contained"


def render_source(markdown: str, title: str, resource_dir: Path,
                  toc: bool = False, toc_depth: int = 3,
                  extra_css: str = "") -> str:
    """Render markdown text to a standalone HTML document.

    Input is parsed the same way as for a diff -- see render_markdown for
    why gfm+smart, and why ASCII diagrams are pre-processed into tables.
    The stylesheet goes in through --include-in-header, which lands it
    after the template's own rules, so it wins where the two disagree.

    The text is passed in rather than read from a path because a document
    can arrive on stdin, where there is no file to name; `resource_dir` is
    then whatever relative links should resolve against.
    """
    markdown = convert_ascii_tables(markdown)
    with tempfile.TemporaryDirectory() as tmp:
        header = Path(tmp) / "style.html"
        header.write_text(f"<style>{CSS}{VIEW_CSS}{extra_css}</style>")
        command = [
            "pandoc",
            "--from=gfm+smart",
            "--to=html5",
            "--standalone",
            embed_flag(),
            f"--include-in-header={header}",
            # Markdown reaches pandoc on stdin, so relative links to
            # images and other resources resolve against the cwd unless
            # told better.
            f"--resource-path={resource_dir}",
            f"--metadata=title:{title}",
        ]
        if toc:
            command += ["--toc", f"--toc-depth={toc_depth}"]
        result = subprocess.run(command, input=markdown, capture_output=True,
                                text=True, check=True)
    return result.stdout


def render_document(path: Path, toc: bool = False, toc_depth: int = 3,
                    extra_css: str = "") -> str:
    """Render a markdown file to a standalone HTML document."""
    return render_source(path.read_text(), path.name, path.parent,
                         toc=toc, toc_depth=toc_depth, extra_css=extra_css)


def piped_input() -> bool:
    """True when stdin is carrying a document rather than nothing.

    Not `isatty()`: the desktop entry launches md-view with no argument
    and no terminal, and stdin there is /dev/null or closed -- reading it
    would swallow the file chooser and report an empty document.  A pipe
    or a redirected file is somebody meaning to send markdown; a character
    device or a closed descriptor is not.
    """
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (AttributeError, OSError, ValueError):
        return False
    return stat.S_ISFIFO(mode) or stat.S_ISREG(mode)


def session_running(app_id: str) -> bool:
    """True when an md-view already owns the session name on the bus.

    Asked of the bus rather than guessed from a process listing: the name is
    what a joining invocation actually looks for, and it is taken slightly
    after the process starts.
    """
    if shutil.which("gdbus") is None:
        return False
    try:
        result = subprocess.run(
            ["gdbus", "call", "--session",
             "--dest", "org.freedesktop.DBus",
             "--object-path", "/org/freedesktop/DBus",
             "--method", "org.freedesktop.DBus.NameHasOwner", app_id],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "true" in result.stdout


def await_session(app_id: str, timeout: float = 20.0,
                  holder: subprocess.Popen | None = None) -> bool:
    """Wait for the session to exist, so later documents join it in order.

    Documents are sent one at a time and appended as they arrive, so the
    order they open in is the order they were sent -- but only once there is
    something to send them to.  Without gdbus to ask, a pause is the best
    that can be done; getting it wrong costs the tab order, nothing more.
    """
    if shutil.which("gdbus") is None:
        time.sleep(1.5)
        return True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session_running(app_id):
            return True
        # Gone without taking the name: it failed to start, or a session
        # appeared while it was starting and it joined that instead.  Either
        # way there is nothing further to wait for.
        if holder is not None and holder.poll() is not None:
            return session_running(app_id)
        time.sleep(0.1)
    return False


def open_documents(documents: list[tuple[str, str, str]], session: str | None,
                   no_sandbox: bool) -> int:
    """Open rendered documents in the viewer, in the order given.

    Without a session each document is its own window and its own process,
    so they are all started and then waited for.  With one, they are sent
    one at a time so the tabs come out in the order they were named -- and
    the first has to have taken the session name before the second is sent,
    or the two race to become it.
    """
    if session is None:
        started = [spawn_document(document, title, title, where,
                                  navigation=False, no_sandbox=no_sandbox)
                   for document, title, where in documents]
        if any(process is None for process in started):
            return 1
        return max((process.wait() for process in started), default=0)

    # Nothing to order, so nothing to wait for: hand it over and let the
    # invocation stand or fall on its own, exactly as one file always has.
    if len(documents) == 1:
        document, title, where = documents[0]
        return show_document(document, title, title, where, navigation=False,
                             no_sandbox=no_sandbox, session=session)

    holder = None
    status = 0
    for index, (document, title, where) in enumerate(documents):
        # The invocation that becomes the session runs until its window
        # closes; waiting for it here would stop the rest being sent.
        if index == 0 and not session_running(session):
            holder = spawn_document(document, title, title, where,
                                    navigation=False, no_sandbox=no_sandbox,
                                    session=session)
            if holder is None:
                return 1
            if not await_session(session, holder=holder):
                print("md-view: the viewer did not start", file=sys.stderr)
                return 1
            continue

        status = show_document(document, title, title, where,
                               navigation=False, no_sandbox=no_sandbox,
                               session=session)
        if status != 0:
            return status

    # Whichever invocation is holding the window is the one to wait for.
    return holder.wait() if holder is not None else status


def choose_file() -> Path | None:
    """Ask the desktop for a file, for a launcher that passed none.

    The desktop entry is in the application menu, where it is launched with
    no argument at all; a usage message on stderr would be invisible there.
    """
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return None

    for command in (
        ["kdialog", "--getopenfilename", ".",
         "Markdown (*.md *.markdown *.mdown *.mkd)"],
        ["zenity", "--file-selection", "--title=Open markdown",
         "--file-filter=Markdown | *.md *.markdown *.mdown *.mkd"],
    ):
        exe = shutil.which(command[0])
        if exe is None:
            continue
        result = subprocess.run([exe, *command[1:]], capture_output=True,
                                text=True)
        chosen = result.stdout.strip()
        return Path(chosen) if result.returncode == 0 and chosen else None
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path, nargs="*",
                        help="Markdown files to render, or - for stdin "
                             "(several open as tabs in one window; without "
                             "any, piped input is read, and failing that a "
                             "file chooser opens)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Write the HTML to a file instead of opening it")
    parser.add_argument("--toc", action="store_true",
                        help="Include a table of contents")
    parser.add_argument("--new-window", action="store_true",
                        help="Open a window of its own instead of joining "
                             "the md-view already running")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="Run WebKit without its content sandbox, for "
                             "systems that block unprivileged user namespaces")
    args = parser.parse_args()

    files = list(args.file)

    # `-` asks for stdin outright; no argument at all takes it only when
    # something is actually piped in, leaving the chooser for the launcher.
    from_stdin = ([str(path) for path in files] == ["-"] if files
                  else piped_input())

    if not files and not from_stdin:
        chosen = choose_file()
        if chosen is None:
            print("md-view: no file given", file=sys.stderr)
            return 1
        files = [chosen]

    if not from_stdin:
        missing = [path for path in files if not path.exists()]
        for path in missing:
            print(f"Error: {path} not found", file=sys.stderr)
        if missing:
            return 1

    # One output file cannot hold several documents, and quietly rendering
    # only the first would be the wrong kind of helpful.
    if args.output is not None and len(files) > 1:
        print("md-view: --output takes a single file", file=sys.stderr)
        return 1

    if shutil.which("pandoc") is None:
        print("Error: pandoc not found on PATH (required to render markdown)",
              file=sys.stderr)
        return 1

    if from_stdin:
        markdown = sys.stdin.read()
        if not markdown.strip():
            print("md-view: nothing on stdin", file=sys.stderr)
            return 1
        # A piped document has no file to resolve relative links against,
        # so they get the directory the shell that wrote the pipe was in.
        # That is also what the window shows under the heading, as it does
        # the containing directory for a file.
        sources = [(markdown, "stdin", Path.cwd())]
    else:
        sources = [(path.read_text(), path.name, path.resolve().parent)
                   for path in files]

    if args.output is not None:
        markdown, title, where = sources[0]
        args.output.write_text(
            render_source(markdown, title, where, toc=args.toc))
        print(f"Written to {args.output}")
        return 0

    documents = [(render_source(markdown, title, where, toc=args.toc,
                                extra_css=NAV_CSS),
                  title, str(where))
                 for markdown, title, where in sources]

    # Documents being read accumulate in one md-view; diffs never do.
    return open_documents(documents,
                          None if args.new_window else VIEW_APP_ID,
                          args.no_sandbox)


if __name__ == "__main__":
    sys.exit(main())
