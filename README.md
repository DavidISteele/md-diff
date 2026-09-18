# md-diff

This project started out as a rendered markdown diff tool — like GitHub's
Rich Diff, but locally.

It takes two markdown files, renders them to HTML via pandoc, and produces a
structural diff of the rendered output. The result is a standalone HTML file
with inline additions and deletions highlighted in context.

Structured ascii tables and diagrams are parsed and tidied up, and where
possible, rendered using box drawing characters for a cleaner ouput.

It now also has a markdown viewer, with dockable tabs for multiple docs.

## Installation

Everything comes from distribution packages. On Debian or Ubuntu:

```
sudo apt install pandoc python3-lxml                                 # renderer and diff
sudo apt install build-essential libgtk-4-dev libwebkitgtk-6.0-dev   # the window
make -C viewer                                                       # Build the gui (optional)
```

lxml is the only third-party import and the rest of the package is the
standard library, so the system `python3` is enough — there is nothing here
worth building a virtualenv for. Run it out of the checkout:

```
PYTHONPATH=/path/to/md-diff python3 -m md_diff.rich_diff old.md new.md
```

`scripts/md-diff.sh` and `scripts/md-view.sh` wrap that; symlink whichever
you use into `~/bin`.

`pip install .` is the alternative, and all it adds is the four command names
on `PATH`. Debian 12 and Ubuntu 24.04 mark the system python as externally
managed (PEP 668), so it has to go into a virtualenv; a plain one is fine, as
there is no PyGObject to reach around and `--system-site-packages` is not
required. The usage below names the installed commands — each is also a
module, so either form works:

| Installed | Out of the checkout |
| --- | --- |
| `md-rich-diff` | `python3 -m md_diff.rich_diff` |
| `md-diff-gui` | `python3 -m md_diff.gui` |
| `md-view` | `python3 -m md_diff.view` |
| `ascii-table` | `python3 -m md_diff.ascii_table` |

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

The window is `viewer/md-diff-view`, a small C program linking GTK4 and
WebKit. It reads a rendered document on stdin and adds the chrome; pandoc,
the diff and the stylesheet all stay in Python. It is a compiled binary
rather than PyGObject so that an AppArmor profile has an executable to
attach to — see [WebKit sandbox](#webkit-sandbox). `md-diff-gui` finds it in
`viewer/`, on `PATH`, or wherever `MD_DIFF_VIEWER` points.

| Key | Action |
| --- | --- |
| `PageUp` / `PageDown`, `Space` / `Shift+Space` | Scroll a screen |
| `Up` / `Down`, `Home` / `End` | Scroll a line, or to the ends |
| `n` / `Tab` / `Alt+Down` | Next change |
| `p` / `Shift+Tab` / `Alt+Up` | Previous change |
| `Ctrl+F`, `Ctrl+S` | Find |
| `F9` | Show / hide the outline |
| `Ctrl` `+` / `-` / `0` | Zoom in / out / reset |
| `Ctrl+PageUp` / `Ctrl+PageDown` | Previous / next document (md-view) |
| `Alt+1` … `Alt+9` | Nth document (md-view) |
| `Ctrl+Shift+B` | Show / hide the tab bar (md-view) |
| `Ctrl+Shift+D` | Move this document to a window of its own (md-view) |
| `q`, `Esc`, `Ctrl+W` | Close the document, and the window with the last one |

Scrolling is WebKit's own, and reaches the document from the moment the
window opens — the tab strip never holds the keyboard, so there is no click
needed first and no point at which the same key means two things. See
[GtkNotebook takes the keyboard](#gtknotebook-takes-the-keyboard-when-the-current-page-changes).

The header bar shows the current position in the change list (`3 / 17`) and
the change you jumped to is outlined.

Down the right-hand edge is a meld-style overview of the whole document: one
marker per change — green for added, red for removed, amber for a changed
table row — placed where that change falls in the file, with the change you
jumped to ringed in blue. The scrollbar thumb is drawn on top of it and stays
visible rather than fading out; click or drag the strip to scroll.

#### Find

`Ctrl+F` or `Ctrl+S` drops a search box out from under the title bar. Typing
in it highlights every occurrence of the string in the document — the search
is case-insensitive — and counts them beside the box (`3 / 17`). `Down` and
`Up`, `Return` and `Shift+Return`, or the two arrow buttons step through the
matches, wrapping at the ends; the one you are on is picked out from the rest
and scrolled to. `Esc` puts the box away and drops the highlighting with it —
a second `Esc` closes the window as usual.

While the box has the keyboard the document's own single-key bindings stand
down, so `n`, `p` and `q` are letters to type rather than commands.

A match is found only where it lies inside one run of text: a phrase spanning
an inline change in a diff — `the word`, where `word` is an insertion — is
not matched, because finding it would mean rewriting the markup the diff
exists to show.

#### Untrusted documents

This tool renders markdown out of git branches, so a document it is pointed
at is written by whoever wrote the branch. Pandoc passes raw HTML in a
markdown file straight through, which means a document can arrive carrying
`<script>`. The viewer takes that as given:

- **Page script does not run.** `enable-javascript-markup` is off, which
  kills `<script>` and inline handlers while leaving the host's own
  `evaluate_javascript` working — that is what drives the change stepper, the
  search and the overview map, so the features survive the mitigation. A
  query typed into the search box reaches that script as a quoted string
  literal, never as code.
- **The renderer has no network.** It runs in an ephemeral session with the
  proxy pointed at a dead address. Rendering a local file needs no network,
  and a document that asks for one is either tracking who opened it or
  carrying something out.
- **The document cannot navigate the window.** Links and popups are refused
  after the initial load. A link into the document itself is not that: an
  anchor whose target is the page already open scrolls to it, which is what
  makes a table of contents work, and the whole URI up to the `#` has to
  match for that exception to apply.

#### WebKit sandbox

WebKit renders page content inside a bubblewrap sandbox that needs
unprivileged user namespaces. Ubuntu 24.04 and later restrict these to
executables an AppArmor profile grants them to
(`kernel.apparmor_restrict_unprivileged_userns=1`), and WebKit responds to
being refused by dumping core. The viewer settles the question at startup by
running WebKit's own sandbox helper — `bwrap --unshare-user` — and explains
the options rather than crashing. Asking bwrap, rather than reimplementing
what it does, is what makes the answer match reality: bwrap inherits the
viewer's AppArmor label, so the probe accounts for the profile, which the
global sysctl cannot. `md-diff-view --check-sandbox` reports the same thing
on demand, along with the label in force.

The recommended fix is the profile shipped in `packaging/apparmor/`, which
grants the permission to that one binary:

```
sudo cp packaging/apparmor/md-diff-view /etc/apparmor.d/md-diff-view
sudo apparmor_parser -r /etc/apparmor.d/md-diff-view
```

Like Ubuntu's own profiles for Firefox and Brave it imposes no confinement
of its own; it exists to name the binary and grant `userns`. Whoever can
write to the attachment path gets the grant, so on a shared machine install
the binary somewhere only root can write.

Failing that, `--no-sandbox` runs without the sandbox. It needs no root and
is scoped to this tool, but it removes the containment that matters when a
document reaches a bug in the renderer rather than merely running script:
inside the sandbox that costs an attacker a process in an empty cell,
without it they have your user account. The third option,
`sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`, restores
the sandbox everywhere at the cost of a system-wide hardening measure.

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

The generated command names `md-diff-gui`, so the config is only good where
that is on `PATH` — installed, in other words. Working out of the checkout,
write it against the wrapper instead, which takes git's three arguments as
they come:

```
git config --global difftool.md-diff.cmd '~/bin/md-diff.sh "$LOCAL" "$REMOTE" "$BASE"'
git config --global diff.tool md-diff
```

### Viewing a markdown file

```
md-view [file.md ... | -] [--toc] [--new-window] [-o output.html]
```

`-o` converts the markdown to standalone HTML and stops.

Otherwise, the file opens in the GUI window, with `Ctrl+F` / `Ctrl+S`,
`Ctrl` `+` / `-` / `0` and `q` / `Esc` / `Ctrl+W` as above. The stepping
chrome is absent — the search box and the overview strip stay, the
latter as a scrollbar that doesn't fade. `--no-sandbox` applies here too.

#### The outline

`F9`, or the button at the left of the header bar, opens a pane down the
side listing the document's headings, indented by depth. Click one and the
document scrolls to it; the keyboard goes back to the page, so the next
`Page Down` still belongs to the text.

The list is read from the headings in the rendered document, so it needs
nothing asked for at the command line — `--toc` puts pandoc's own table of
contents *in* the document, which is what you want in a file written with
`-o`, while the pane is the window's and costs the document nothing. Those
anchor links work too, now that a link into the page is allowed to scroll
it — see [Untrusted documents](#untrusted-documents).

Like the search box, the pane belongs to the window rather than to a
document: opened once, it re-reads whatever tab you move to. The depths are
relative, so a file whose sections all sit under one `#` title reads as a
flat list rather than one indented off the left edge.

Multiple files can be passed in together, and they are rendered using tabs,
in order.

```
md-view chapter-*.md
```

Invoking md-view a second time will add a new tab to the existing window and
immediately return. If this behaviour is not desired, `--new-window` will open
the document in it's own window, and block.

Tabs are undockable - just drag the tab out the the desktop to give it it's
own window, or use `Ctrl-Shift-D`.
You can also drag the tab onto a different md-view window.
To re-dock a document that has no tab, use the button in the title bar to show
the tab-strip (or `Ctrl-Shift-B`), and it's tab will re-appear.

These are also application actions, for a keybinding of your own or a
script:

```
gapplication action org.user.local.md-view toggle-tabs
gapplication action org.user.local.md-view detach-tab
gapplication action org.user.local.md-view toggle-toc
```

A document keeps its place across the move — the scroll position, the search
you had open and its match count all survive being dragged from one window to
another, because the page itself is carried across rather than reloaded.

Diffs never join it. md-diff registers a different GTK application id, so the
two can't adopt each other's windows — and git difftool depends on that: it
runs the tool once per file and waits for each to exit, which an invocation
that attached to a running window would break.

Pandoc builds the document here rather than a fragment, which is what supplies
`--toc` and the embedded resources: the window loads the HTML as a string, so
relative images have no base URI to resolve against and must be inlined. Both
paths share one stylesheet, so a document reads the same viewed as it does
diffed — ASCII diagrams included.

#### Piping in markdown

Generated Markdown can be piped in via stdin, which puts the window at
the end of a pipeline:

```
pandoc -t gfm report.docx | md-view
git show HEAD:README.md | md-view
md-view < notes.md
```

Naming `-` as the file asks for stdin outright; with no argument at all,
md-view reads stdin when something is piped or redirected into it, and falls
back to the file chooser otherwise. That distinction is by descriptor, not
`isatty()` — the [desktop entry](#desktop-entry) launches md-view with no
argument and no terminal, and stdin there is `/dev/null`, which must still
open the chooser rather than render an empty document.

A piped document has no file to resolve relative links against, so they get
the working directory instead, and the header bar reads `stdin` over that
directory where a file would name itself over its own.

### Desktop entry

```
packaging/install-desktop.sh
```

Installs `Markdown Viewer` into the application menu and registers it as a
handler for `text/markdown`, so a `.md` file opens in the window from the
file manager. Everything lands under `$XDG_DATA_HOME` — no root, nothing
system-wide. The entry, its icon and the window's GTK application id all
share the name `org.user.local.md-diff-view`, which is what lets the shell
pair the running window with the launcher rather than showing it unnamed;
`StartupWMClass` covers X11, where the match is by `WM_CLASS` instead.

`Exec` is rewritten to an absolute path at install time, since a desktop
session's `PATH` is not the shell's and rarely includes `~/bin`. Launched
from the menu with no file, `md-view` opens a file chooser (`kdialog` or
`zenity`, whichever is present) rather than exiting invisibly.

Registering a handler can change which application opens markdown by
default when nothing was set explicitly. Pin whichever you want:

```
xdg-mime default org.user.local.md-diff-view.desktop text/markdown
xdg-mime query default text/markdown
```

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
- [pandoc](https://pandoc.org/) — `pandoc`
- [lxml](https://lxml.de/) — `python3-lxml`, and the only third-party import

Nothing above is needed for the window itself: the Python package never
imports GTK. Building `viewer/md-diff-view` needs a C compiler and the GTK4
and WebKit development packages, which bring the runtime libraries with them:

- `build-essential`
- `libgtk-4-dev`
- `libwebkitgtk-6.0-dev`

The split runs the other way too. `md-rich-diff`, `ascii-table` and
`md-view --output` only write HTML, so a machine that never opens the window
can skip the GTK packages and the `make` entirely.
