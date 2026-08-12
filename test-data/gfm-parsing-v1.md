# Renderer Notes

These notes exist to pin down how the renderer treats markdown that GitHub
accepts.  Every section below is written the way people actually write it —
not the way a strict parser would prefer.

## Tight lists

The most common case is a list written directly under the line that
introduces it, with no blank line in between:
- Partial separators and spanning columns
- Bullet-list content inside cells
- Multi-line cells merged into logical rows

Pandoc's own markdown dialect folds those four bullets back into the
paragraph above.  GitHub renders them as a list, so we do too.

Ordered lists interrupt a paragraph the same way:
1. Read both files from disk
2. Convert box-drawing diagrams to tables
3. Render each side through pandoc

Nesting survives the same treatment:
- Section-level matching
  - Headings split the document
  - Unmatched sections become whole-block changes
- Block-level diffing
  - Tables are handled separately

## Typography

Smart punctuation stays on, so this paragraph should render with curly
quotes and a proper em dash: she called it "a rich diff, but local" — which
is roughly right.  Ellipses collapse too...  and ranges like 2010--2015
become en dashes.

Quoting inside a list item works as well:
- The header shows "3 / 17" for position
- Don't confuse `--no-sandbox` with disabling the renderer

## Task lists

GitHub's checkbox syntax is a gfm extension:
- [x] Section matching
- [x] Table-aware diffing
- [ ] Image diffing

## Other gfm extensions

Strikethrough marks ~~removed~~ text.  Bare URLs autolink:
https://pandoc.org/ should become a link without angle brackets.

| Stage | Input | Notes |
| --- | --- | --- |
| Pre-process | markdown | Box-drawing to HTML tables |
| Render | markdown | One pandoc call per side |
| Match | HTML | Headings split the tree |

Footnotes are supported too.[^1]

[^1]: And the footnote body renders at the end of the document.
