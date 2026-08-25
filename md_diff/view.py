#!/usr/bin/env python3
"""Render a single markdown file and open it in the md-diff window.

Usage: md-view file.md [--toc] [-o output.html]

The same renderer and the same window as the diff, minus the diff: a
document reads the same viewed as it does in `md-diff-gui`.  Pandoc builds
the whole document here rather than a fragment, because its template is
what supplies --toc and the embedded resources -- the window loads the
HTML as a string, with no base URI for relative images to resolve against.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from md_diff.ascii_table import convert_ascii_tables
from md_diff.gui import NAV_CSS, show_document
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


def render_document(path: Path, toc: bool = False, toc_depth: int = 3,
                    extra_css: str = "") -> str:
    """Render a markdown file to a standalone HTML document.

    Input is parsed the same way as for a diff -- see render_markdown for
    why gfm+smart, and why ASCII diagrams are pre-processed into tables.
    The stylesheet goes in through --include-in-header, which lands it
    after the template's own rules, so it wins where the two disagree.
    """
    markdown = convert_ascii_tables(path.read_text())
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
            # Markdown arrives on stdin, so relative links to images and
            # other resources resolve against the cwd unless told better.
            f"--resource-path={path.parent}",
            f"--metadata=title:{path.name}",
        ]
        if toc:
            command += ["--toc", f"--toc-depth={toc_depth}"]
        result = subprocess.run(command, input=markdown, capture_output=True,
                                text=True, check=True)
    return result.stdout


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
    parser.add_argument("file", type=Path, nargs="?",
                        help="Markdown file to render "
                             "(without one, a file chooser opens)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Write the HTML to a file instead of opening it")
    parser.add_argument("--toc", action="store_true",
                        help="Include a table of contents")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="Run WebKit without its content sandbox, for "
                             "systems that block unprivileged user namespaces")
    args = parser.parse_args()

    if args.file is None:
        args.file = choose_file()
        if args.file is None:
            print("md-view: no file given", file=sys.stderr)
            return 1

    if not args.file.exists():
        print(f"Error: {args.file} not found", file=sys.stderr)
        return 1

    if shutil.which("pandoc") is None:
        print("Error: pandoc not found on PATH (required to render markdown)",
              file=sys.stderr)
        return 1

    if args.output is not None:
        args.output.write_text(render_document(args.file, toc=args.toc))
        print(f"Written to {args.output}")
        return 0

    document = render_document(args.file, toc=args.toc, extra_css=NAV_CSS)
    return show_document(document, args.file.name, args.file.name,
                         str(args.file.resolve().parent), navigation=False,
                         no_sandbox=args.no_sandbox)


if __name__ == "__main__":
    sys.exit(main())
