# Diagram Notes

Code blocks drawn with box characters are pre-processed before rendering:
some of them become HTML tables and the rest stay code blocks.  This pair
pins down which is which, and holds the shapes that got it wrong.

## File tree

A tree is drawn with the same characters as a table and is not one.  The
`│` spine marks depth rather than a column boundary, and nothing crosses
it.  One entry now runs to three lines, indented under a nested branch,
which puts a second `│` in the block — a second column, to anything
counting them:

```
src/renderer/
├── parse.py                  Markdown to blocks
├── layout/
│   ├── blocks.py             Paragraph and list geometry
│   ├── tables.py             Column widths — measured from the source
│   │                         row by row rather than from the rendered
│   │                         text, so a wide cell cannot shrink one
│   └── trees.py              Tree passthrough
├── emit.py                   HTML writer
└── cli.py                    Entry point
```

## Panel diagram

A real box-drawing table: framed, with a spanning left column and inner
separators dividing the cells on the right.  This one must convert.

```
┌────────────────┬─────────────────────────────────────┐
│                │ Stage        │ Cost                 │
│  PIPELINE      │──────────────│──────────────────────│
│                │ parse        │ one pass             │
│  [source.md]   │ layout       │ two passes           │
│                │ trees        │ passthrough          │
│                │ emit         │ one pass             │
├────────────────┼─────────────────────────────────────┤
│ NOTES          │  ▸ Widths come from the source      │
│                │  ▸ Emission is streaming            │
│                │  ▸ Trees are copied through         │
└────────────────┴─────────────────────────────────────┘
```

## Flow diagram

Box-drawn, but nothing lines up into columns — one vertical rule is not a
table, and this stays a code block:

```
source.md ──▶ parse ──▶ layout ──▶ trees ──▶ emit ──▶ out.html
                                      │
                                      └──▶ warnings.log
```

## Nested boxes

Boxes inside a box, joined by arrows.  Every `│` here is a wall or a
wire, not a column boundary, but the grid parser walks it happily — and
the table it produces holds a fraction of the words:

```
┌──────────────────────────────────────────┐
│  md-diff                                 │
│                                          │
│  ┌──────────┐  ┌──────────┐  ┌────────┐  │
│  │ renderer │  │ differ   │  │ viewer │  │
│  │ (pandoc) │  │ (lxml)   │  │ (GTK)  │  │
│  │          │  │          │  │ + nav  │  │
│  └────┬─────┘  └────┬─────┘  └───┬────┘  │
│       │ html        │ tree       │ html  │
│       ▼             ▼            ▼       │
└───────┼─────────────┼────────────┼───────┘
        └─────────────┴────────────┘
                      │
              ┌───────┴────────┐
              │  out.html (v2) │
              └────────────────┘
                      │
                 ▼ opened by ▼
                 - md-diff-view
                 - any browser
                 - the difftool
```

## Gallery

Drawn with the same characters, and none of them a table.  Not one of
these may be converted, and not one may lose a word.

A state machine, rounded:

```
        ╭──────────╮   grant    ╭──────────╮
        │  queued  │───────────▶│  active  │
        ╰────┬─────╯            ╰─────┬────╯
             │ timeout                │ release
             ▼                         ▼
        ╭──────────╮            ╭──────────╮
        │  reaped  │◀───────────│  closed  │
        ╰──────────╯   reaper   ╰──────────╯
```

A sequence, where the verticals are lifelines and no box closes at all —
the extractor is blind to this one, so the text-loss guard is what has to
catch it:

```
  client         broker          adapter
    │              │                │
    │─ /lease ────▶│                │
    │              │─ can_fit? ────▶│
    │              │◀── 6.2 GB ─────│
    │◀── grant ────│                │
    │              │─ loaded ──────▶│
    │              │                │
```

A legend, double-ruled, with a box inside a box:

```
╔════════════════════════════════════════╗
║  legend                                ║
║    ┌────────┐  a box is a component    ║
║    │ shape  │                          ║
║    └────────┘  ─────▶ is a data flow   ║
║                ◀╌╌╌╌╌ is a retry      ║
╚════════════════════════════════════════╝
```

The same thing drawn in ASCII, which the box characters never match:

```
+---------------+        +---------------+
|  fixture      | ---->  |  renderer     |
|  (v1 / v2)    |        |  (pandoc 3)   |
+---------------+        +---------------+
```

## Column widths

A first column of keys sits beside a column of prose.  The keys must not
break at their hyphens, and the table must not outgrow the page — not
even with a first cell that is a sentence rather than a key:

| ID | Behaviour |
| --- | --- |
| DG-1 | Box-drawing code blocks are converted to tables where they parse. |
| DG-2.1 | A block that parses to no rows is left as a code block. |
| DG-2.2 | A file tree is never a candidate, however many spines it has. |
| every block the pre-processor declines to convert — trees, flow diagrams, and anything parsing to no rows | reaches pandoc as the fenced block it started as, and renders as code. |
