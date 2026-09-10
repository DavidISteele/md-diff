# Known issues

## GtkNotebook's tab drag is broken in GTK 4.8, and is not used

Dragging one of GtkNotebook's own tabs out to a new window corrupts the strip
it leaves behind: the tab switched to is not repainted, its neighbour loses an
edge, and dropping a tab into its own content area can crash the window. It
needs four or more open documents to show reliably, and once triggered it
repeats until the window is closed.

This is GTK's, not this project's. `tests/notebook-repro.c` is a stock
`GtkNotebook` with plain labels, detachable tabs and the smallest possible
`create-window` handler -- no WebKit, nothing from here -- and it corrupts its
own strip the same way:

```
cc -O2 -Wall $(pkg-config --cflags gtk4) -o /tmp/notebook-repro \
   tests/notebook-repro.c $(pkg-config --libs gtk4)
/tmp/notebook-repro          # drag a tab out, then switch tabs
```

Debian 12 pins GTK to 4.8.3 (September 2022), where the GTK4 notebook's
drag-and-drop was newest and least settled. It is no better in 4.14.

So none of it is used. The notebook's tabs are neither detachable nor
reorderable, there is no tab group and no `create-window` handler, and
dragging a document between windows is built here instead, out of an ordinary
`GtkDragSource` on the tab and a `GtkDropTarget` on the window -- which are
not affected. The move itself is the same detach-and-append the keyboard has
always used. What is lost is reordering tabs within a strip, which was
GtkNotebook's to provide.

Two things that follow, and are worth knowing before changing them back:

- **WebKit's own drop target is removed from each view** (`release_web_view_drops`).
  It claims a drag before one can reach the window beneath, and a drop over
  the page area is then delivered seconds late and reported as having found
  no target. Nothing can be dropped into a rendered document, so the view has
  no use for it.
- **GPU compositing is off** (`WEBKIT_HARDWARE_ACCELERATION_POLICY_NEVER`).
  Reparenting a view into another window takes its compositing surface away,
  and WebKit does not paint into the new one until the page is dirtied -- a
  scroll will do it, but a document too short to scroll simply stays blank.
  These are pages of static text; the GPU context per tab bought nothing.

`MD_DIFF_DEBUG=1` reports every window, move, drag and drop with a timestamp,
plus the size, visibility and mapping of every tab label. The order and the
gap between a drag reporting itself cancelled and the drop arriving is what
distinguishes a document let go over the desktop from one that landed
somewhere -- which is not obvious from the screen, and cost a long time to
learn.

## GtkNotebook takes the keyboard when the current page changes

Whenever the notebook's current page changes it grabs the focus for itself,
and the document behind it goes deaf. The scroll keys reach the notebook,
which does nothing with them; `Left` and `Right` reach GtkNotebook's own
arrow keynav, which walks the tabs. Clicking the page hands the focus to
WebKit and the two swap over — the document scrolls, and the arrows stop
moving between tabs. It reads like WebKit and GTK disagreeing over the
keyboard, and is neither of them.

`sync_chrome` hands the focus back, being the one place every route to a new
front document already goes through — switching a tab, opening one, taking a
drop from another window, settling after a drag. A query being typed keeps
the keyboard, since switching tabs mid-search should not empty the box under
your hands. Clicking the tab that is *already* current raises no
`switch-page` to hand anything back, so a `notify::focus-widget` handler
bounces the focus off the notebook as well.

Two things worth knowing before changing it:

- **Making the notebook unfocusable is not the tidier fix.** The focus is
  then stranded inside the outgoing page's view, and clicking a tab leaves
  the keyboard pointed at the document you just left — worse than the
  problem, and quieter.
- **Nothing is taken from WebKit.** The key controller runs on the capture
  phase, so a key the window binds already outranks the page and stealing
  the rest buys no priority. What is left to WebKit is scrolling sized to the
  real viewport, horizontal scrolling inside wide tables and code blocks, and
  `Ctrl+C` over a selection that only the web process can copy.
