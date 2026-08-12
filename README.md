# md-diff

Rendered markdown diff — like GitHub's Rich Diff, but locally.

Takes two markdown files, renders them to HTML via pandoc, and produces a
structural diff of the rendered output. The result is a standalone HTML file
with inline additions and deletions highlighted in context.

## Installation

```
pip install .
```

Requires [pandoc](https://pandoc.org/) to be installed separately.

## Usage

### CLI

```
md-rich-diff old.md new.md [-o output.html]
```

Output defaults to `diff-<old>-vs-<new>.html`.

### GUI

Opens the same rendered diff in a window, so there's no round-trip through
a browser:

```
md-diff-gui old.md new.md
```

| Key | Action |
| --- | --- |
| `n` / `Tab` | Next change |
| `p` / `Shift+Tab` | Previous change |
| `Ctrl` `+` / `-` / `0` | Zoom in / out / reset |
| `q`, `Esc`, `Ctrl+W` | Close |

The header bar shows the current position in the change list (`3 / 17`) and
the change you jumped to is outlined.

#### WebKit sandbox

WebKit renders page content inside a bubblewrap sandbox that needs
unprivileged user namespaces. Ubuntu 24.04 and later restrict these by
default (`kernel.apparmor_restrict_unprivileged_userns=1`), and WebKit
responds by dumping core. The GUI checks for this at startup and explains the
options rather than crashing:

```
md-diff-gui old.md new.md --no-sandbox
```

`--no-sandbox` is scoped to this tool and needs no root. The pages rendered
come from local files with no network access, so the exposure is small — but
pandoc passes raw HTML in a markdown file straight through, so a document
from a branch you don't trust could run scripts in an unsandboxed renderer.
The alternative is restoring user namespaces machine-wide with
`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`, which keeps
the sandbox but drops a system-wide hardening measure.

A per-application AppArmor profile is not a practical third option: it would
attach to the Python interpreter rather than to this script, granting
`userns` to every Python process on the system.

### git difftool

```
md-diff-gui --install-difftool
```

This registers a difftool named `md-diff` and sets it as the default, so
`git difftool <ref>` shows a rendered diff for markdown files. Non-markdown
files are passed through to `meld` — pick another with
`--install-difftool --fallback <tool>`, restrict the config to the current
repo with `--local`, or write the config yourself. Add `--no-sandbox` to bake
that flag into the generated command if your system restricts user
namespaces (the installer warns when it detects this):

```
git config --global difftool.md-diff.cmd \
  'md-diff-gui "$LOCAL" "$REMOTE" --label-old "$BASE (old)" --label-new "$BASE (new)" --fallback meld'
git config --global diff.tool md-diff
```

The `$BASE` labels make the header bar show the repo-relative path rather
than git's temporary checkout filenames.

### ASCII tables

The ASCII table converter is also available standalone:

```
ascii-table input.md [-o output.md]
```

### As a library

```python
from md_diff import render_markdown, diff_sections, convert_ascii_tables
```

## How it works

1. **ASCII table pre-processing** — `ascii_table` detects code blocks
   containing Unicode box-drawing characters (│┌┐└┘├┤┬┴┼─ etc.), parses
   their grid structure, and converts them to HTML `<table>` elements before
   pandoc sees them.  This prevents pandoc from treating diagrams as plain
   code blocks.

2. **Markdown → HTML** — Both files are rendered through pandoc.

3. **Section-level matching** — The HTML is split by headings and sections
   are matched between the old and new documents using `SequenceMatcher`.
   Unmatched sections appear as whole-block additions or deletions.

4. **Block-level diffing** — Within matched sections, content is split into
   blocks (tables vs. everything else).  Non-table blocks are diffed with
   `lxml.html.diff.htmldiff`.

5. **Table-aware diffing** — Tables are diffed row-by-row and cell-by-cell,
   producing per-cell inline diffs with inserted/deleted/changed row
   styling.

## ASCII table features

Handles complex box-drawing diagrams including:
- Partial separators and spanning (rowspan) columns
- Bullet-list content inside cells (▸, ●, - markers)
- Multi-line cells merged into logical rows
- Sections with varying column counts (colspan)

## Dependencies

- Python 3.10+
- [pandoc](https://pandoc.org/) (external)
- [lxml](https://lxml.de/) (installed automatically via pip)

The GUI additionally needs GTK4 and WebKit, via the system PyGObject:

```
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0
```

PyGObject is not pip-installable in practice, so a virtualenv must be
created with access to system packages:

```
python3 -m venv --system-site-packages .venv
```

The CLI has no such constraint — a plain venv is fine if you don't want the
GUI.
