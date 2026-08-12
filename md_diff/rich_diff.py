#!/usr/bin/env python3
"""Rendered markdown diff — like GitHub's Rich Diff, locally.

Usage: md-rich-diff.py old.md new.md [-o output.html]

Renders both markdown files to HTML via pandoc, then diffs the
rendered output structurally using lxml's DOM-aware htmldiff.
"""

import argparse
import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

from lxml.html.diff import htmldiff

from md_diff.ascii_table import convert_ascii_tables

CSS = """
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica,
                 Arial, sans-serif;
    max-width: 960px;
    margin: 2em auto;
    padding: 0 1em;
    line-height: 1.6;
    color: #1f2328;
    background: #fff;
}

h1, h2, h3, h4, h5, h6 {
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    border-bottom: 1px solid #d1d9e0;
    padding-bottom: 0.3em;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
}

th, td {
    border: 1px solid #d1d9e0;
    padding: 6px 12px;
    text-align: left;
}

th {
    background: #f6f8fa;
    font-weight: 600;
}

code {
    background: #f6f8fa;
    padding: 0.2em 0.4em;
    border-radius: 3px;
    font-size: 0.9em;
}

pre {
    background: #f6f8fa;
    padding: 1em;
    border-radius: 6px;
    overflow-x: auto;
}

pre code {
    background: none;
    padding: 0;
}

hr {
    border: none;
    border-top: 1px solid #d1d9e0;
    margin: 2em 0;
}

/* Diff styling */
ins {
    background: #d1fae5;
    text-decoration: none;
    padding: 1px 2px;
    border-radius: 2px;
}

del {
    background: #fee2e2;
    text-decoration: line-through;
    padding: 1px 2px;
    border-radius: 2px;
}

/* Make whole inserted/deleted blocks more visible */
ins > * { background: #d1fae5; }
del > * { background: #fee2e2; }

/* Legend */
.diff-legend {
    background: #f6f8fa;
    border: 1px solid #d1d9e0;
    border-radius: 6px;
    padding: 0.75em 1em;
    margin-bottom: 2em;
    font-size: 0.9em;
    color: #656d76;
}

.diff-legend ins, .diff-legend del {
    padding: 2px 6px;
    margin: 0 4px;
}

/* Whole sections added or removed */
.diff-added-section {
    background: #d1fae5;
    border-left: 4px solid #10b981;
    padding: 0.5em 1em;
    margin: 1em 0;
    border-radius: 4px;
}

.diff-removed-section {
    background: #fee2e2;
    border-left: 4px solid #ef4444;
    padding: 0.5em 1em;
    margin: 1em 0;
    border-radius: 4px;
    text-decoration: line-through;
    opacity: 0.7;
}

/* Block-level additions/removals within a section */
.diff-added-block {
    background: #d1fae5;
    border-left: 3px solid #10b981;
    padding: 0.25em 0.75em;
    margin: 0.5em 0;
    border-radius: 3px;
}

.diff-removed-block {
    background: #fee2e2;
    border-left: 3px solid #ef4444;
    padding: 0.25em 0.75em;
    margin: 0.5em 0;
    border-radius: 3px;
    text-decoration: line-through;
    opacity: 0.7;
}

/* Table row diffs */
tr.diff-row-deleted td {
    background: #fee2e2;
    text-decoration: line-through;
    opacity: 0.7;
}

tr.diff-row-inserted td {
    background: #d1fae5;
}

tr.diff-row-changed td {
    background: #fef9c3;
}

/* Converted ASCII diagram tables */
table.ascii-converted {
    border: 2px solid #8b949e;
    font-size: 0.85em;
}

table.ascii-converted th,
table.ascii-converted td {
    vertical-align: top;
    padding: 8px 12px;
    line-height: 1.4;
}

table.ascii-converted ul {
    margin: 0.3em 0;
    padding-left: 1.4em;
}

table.ascii-converted li {
    margin-bottom: 0.2em;
}
"""


def render_markdown(path: Path) -> str:
    """Render a markdown file to HTML via pandoc.

    Pre-processes ASCII box-drawing diagrams into HTML tables first,
    so pandoc passes them through as raw HTML rather than code blocks.

    Input is parsed as GitHub-flavoured markdown so the rendering matches
    what GitHub shows.  Pandoc's own markdown dialect differs in ways that
    surprise: notably it requires a blank line before a bullet list, so a
    list written directly under its introducing paragraph is swallowed into
    that paragraph rather than rendered as a list.  `smart` is added back on
    top of gfm — it is on by default in pandoc's markdown but not in gfm —
    so quotes and dashes stay typographic.
    """
    with open(path) as f:
        markdown = f.read()
    markdown = convert_ascii_tables(markdown)
    result = subprocess.run(
        ["pandoc", "-f", "gfm+smart", "-t", "html", "--no-highlight"],
        input=markdown, capture_output=True, text=True, check=True,
    )
    return result.stdout


def diff_files(old: Path, new: Path) -> str:
    """Render both files and return the diff body HTML."""
    return diff_sections(render_markdown(old), render_markdown(new))


def build_document(diff_html: str, title: str, extra_css: str = "") -> str:
    """Wrap diff body HTML in a standalone HTML document."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{CSS}{extra_css}</style>
</head>
<body>
<div class="diff-legend">
    <strong>{title}</strong>
    &nbsp;&mdash;&nbsp;
    <del>removed</del>
    <ins>added</ins>
</div>
{diff_html}
</body>
</html>
"""


def strip_tags(html: str) -> str:
    """Extract plain text from HTML."""
    return re.sub(r'<[^>]+>', '', html).strip()


def split_sections(html: str) -> list[tuple[str, str]]:
    """Split HTML into sections by heading tags.

    Returns list of (heading_html, body_html) tuples.
    The first entry may have an empty heading if content precedes
    the first heading.
    """
    parts = re.split(r'(?=<h[1-6][ >])', html, flags=re.IGNORECASE)
    sections = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r'(<h[1-6][^>]*>.*?</h[1-6]>)(.*)', part,
                     flags=re.DOTALL | re.IGNORECASE)
        if m:
            sections.append((m.group(1), m.group(2).strip()))
        else:
            sections.append(("", part))
    return sections


def heading_text(heading_html: str) -> str:
    """Extract plain text from a heading tag for matching."""
    return strip_tags(heading_html).lower()


def section_key(heading_html: str, body_html: str) -> str:
    """Create a key for matching sections between old and new."""
    return heading_text(heading_html)


# --- Table-aware diffing ---

def extract_rows(table_html: str) -> list[str]:
    """Extract individual <tr>...</tr> blocks from a table."""
    return re.findall(r'<tr[^>]*>.*?</tr>', table_html, flags=re.DOTALL)


def extract_cells(row_html: str) -> list[str]:
    """Extract cell contents from a <tr>."""
    return re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row_html, flags=re.DOTALL)


def row_text(row_html: str) -> str:
    """Plain text of a row for matching."""
    return strip_tags(row_html)


def diff_cells(old_row: str, new_row: str) -> str:
    """Diff two table rows cell-by-cell, returning a <tr> with inline diffs."""
    old_cells = re.findall(r'(<t[dh])([^>]*>)(.*?)(</t[dh]>)', old_row, flags=re.DOTALL)
    new_cells = re.findall(r'(<t[dh])([^>]*>)(.*?)(</t[dh]>)', new_row, flags=re.DOTALL)

    result_cells = []
    max_len = max(len(old_cells), len(new_cells))

    for i in range(max_len):
        if i >= len(old_cells):
            # Extra cell in new
            tag, attr, content, close = new_cells[i]
            result_cells.append(f'{tag}{attr}<ins>{content}</ins>{close}')
        elif i >= len(new_cells):
            # Cell removed
            tag, attr, content, close = old_cells[i]
            result_cells.append(f'{tag}{attr}<del>{content}</del>{close}')
        else:
            o_tag, o_attr, o_content, o_close = old_cells[i]
            n_tag, n_attr, n_content, n_close = new_cells[i]
            if strip_tags(o_content) == strip_tags(n_content):
                result_cells.append(f'{n_tag}{n_attr}{n_content}{n_close}')
            else:
                diff = htmldiff(o_content, n_content)
                result_cells.append(f'{n_tag}{n_attr}{diff}{n_close}')

    return '<tr class="diff-row-changed">' + '\n'.join(result_cells) + '</tr>'


def diff_table(old_table: str, new_table: str) -> str:
    """Diff two HTML tables row-by-row."""
    old_rows = extract_rows(old_table)
    new_rows = extract_rows(new_table)

    old_texts = [row_text(r) for r in old_rows]
    new_texts = [row_text(r) for r in new_rows]

    matcher = SequenceMatcher(None, old_texts, new_texts)
    result_rows = []

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for ni in range(j1, j2):
                result_rows.append(new_rows[ni])
        elif op == "replace":
            # Try to pair up rows for cell-level diff
            old_chunk = list(range(i1, i2))
            new_chunk = list(range(j1, j2))
            pairs = min(len(old_chunk), len(new_chunk))
            for k in range(pairs):
                result_rows.append(diff_cells(old_rows[old_chunk[k]], new_rows[new_chunk[k]]))
            # Leftover old rows = deleted
            for k in range(pairs, len(old_chunk)):
                result_rows.append(
                    f'<tr class="diff-row-deleted">{_mark_row_cells(old_rows[old_chunk[k]], "del")}</tr>')
            # Leftover new rows = inserted
            for k in range(pairs, len(new_chunk)):
                result_rows.append(
                    f'<tr class="diff-row-inserted">{_mark_row_cells(new_rows[new_chunk[k]], "ins")}</tr>')
        elif op == "delete":
            for oi in range(i1, i2):
                result_rows.append(
                    f'<tr class="diff-row-deleted">{_mark_row_cells(old_rows[oi], "del")}</tr>')
        elif op == "insert":
            for ni in range(j1, j2):
                result_rows.append(
                    f'<tr class="diff-row-inserted">{_mark_row_cells(new_rows[ni], "ins")}</tr>')

    # Reconstruct table with colgroup from new if present
    colgroup = ""
    cg = re.search(r'<colgroup>.*?</colgroup>', new_table, flags=re.DOTALL)
    if cg:
        colgroup = cg.group()

    thead = ""
    th = re.search(r'<thead>.*?</thead>', new_table, flags=re.DOTALL)
    if th:
        thead = th.group()
        # Remove thead rows from result_rows if they duplicate
        if result_rows and 'header' in result_rows[0]:
            result_rows = result_rows[1:]

    return f'<table>\n{colgroup}\n{thead}\n<tbody>\n' + '\n'.join(result_rows) + '\n</tbody>\n</table>'


def _mark_row_cells(row_html: str, tag: str) -> str:
    """Wrap each cell's content in <ins> or <del>."""
    def wrap(m):
        return f'{m.group(1)}<{tag}>{m.group(2)}</{tag}>{m.group(3)}'
    return re.sub(r'(<t[dh][^>]*>)(.*?)(</t[dh]>)', wrap, row_html, flags=re.DOTALL)


# --- Block-level splitting within a section ---

def split_blocks(html: str) -> list[str]:
    """Split section body HTML into block-level elements.

    Separates tables from non-table content so tables can be
    diffed with the table-aware algorithm.
    """
    blocks = []
    pos = 0
    for m in re.finditer(r'<table[^>]*>.*?</table>', html, flags=re.DOTALL):
        before = html[pos:m.start()].strip()
        if before:
            blocks.append(before)
        blocks.append(m.group())
        pos = m.end()
    after = html[pos:].strip()
    if after:
        blocks.append(after)
    return blocks


def is_table(block: str) -> bool:
    return block.strip().startswith('<table')


def diff_body(old_body: str, new_body: str) -> str:
    """Diff section bodies, handling tables specially."""
    old_blocks = split_blocks(old_body)
    new_blocks = split_blocks(new_body)

    # Classify blocks for matching
    def block_key(b):
        if is_table(b):
            return "TABLE:" + strip_tags(b)[:100]
        return strip_tags(b)[:100]

    old_keys = [block_key(b) for b in old_blocks]
    new_keys = [block_key(b) for b in new_blocks]

    matcher = SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    result = []

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for oi, ni in zip(range(i1, i2), range(j1, j2)):
                ob, nb = old_blocks[oi], new_blocks[ni]
                if ob == nb:
                    result.append(nb)
                elif is_table(ob) and is_table(nb):
                    result.append(diff_table(ob, nb))
                else:
                    result.append(htmldiff(ob, nb))

        elif op == "replace":
            # Try to match tables with tables, non-tables with non-tables
            old_chunk = old_blocks[i1:i2]
            new_chunk = new_blocks[j1:j2]

            old_tables = [(i, b) for i, b in enumerate(old_chunk) if is_table(b)]
            new_tables = [(i, b) for i, b in enumerate(new_chunk) if is_table(b)]

            if len(old_tables) == 1 and len(new_tables) == 1:
                # Pair the tables, diff non-table content separately
                ot_idx, ot = old_tables[0]
                nt_idx, nt = new_tables[0]

                # Content before tables
                old_before = ''.join(old_chunk[:ot_idx])
                new_before = ''.join(new_chunk[:nt_idx])
                if old_before or new_before:
                    if old_before and new_before:
                        result.append(htmldiff(old_before, new_before))
                    elif new_before:
                        result.append(f'<div class="diff-added-block">{new_before}</div>')
                    else:
                        result.append(f'<div class="diff-removed-block">{old_before}</div>')

                result.append(diff_table(ot, nt))

                # Content after tables
                old_after = ''.join(old_chunk[ot_idx+1:])
                new_after = ''.join(new_chunk[nt_idx+1:])
                if old_after or new_after:
                    if old_after and new_after:
                        result.append(htmldiff(old_after, new_after))
                    elif new_after:
                        result.append(f'<div class="diff-added-block">{new_after}</div>')
                    else:
                        result.append(f'<div class="diff-removed-block">{old_after}</div>')
            else:
                # Pair up blocks positionally and diff them inline
                pairs = min(len(old_chunk), len(new_chunk))
                for k in range(pairs):
                    ob, nb = old_chunk[k], new_chunk[k]
                    if is_table(ob) and is_table(nb):
                        result.append(diff_table(ob, nb))
                    else:
                        result.append(htmldiff(ob, nb))
                # Leftover old blocks = deleted
                for b in old_chunk[pairs:]:
                    result.append(f'<div class="diff-removed-block">{b}</div>')
                # Leftover new blocks = inserted
                for b in new_chunk[pairs:]:
                    result.append(f'<div class="diff-added-block">{b}</div>')

        elif op == "delete":
            for oi in range(i1, i2):
                result.append(f'<div class="diff-removed-block">{old_blocks[oi]}</div>')

        elif op == "insert":
            for ni in range(j1, j2):
                result.append(f'<div class="diff-added-block">{new_blocks[ni]}</div>')

    return '\n'.join(result)


# --- Top-level section diffing ---

def diff_sections(old_html: str, new_html: str) -> str:
    """Diff two HTML documents section-by-section.

    Splits both documents by headings, matches sections, then diffs
    body content block-by-block (with table-aware diffing).
    Unmatched sections are shown as pure additions or deletions.
    """
    old_secs = split_sections(old_html)
    new_secs = split_sections(new_html)

    old_keys = [section_key(h, b) for h, b in old_secs]
    new_keys = [section_key(h, b) for h, b in new_secs]

    matcher = SequenceMatcher(None, old_keys, new_keys)
    result_parts = []

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for oi, ni in zip(range(i1, i2), range(j1, j2)):
                old_h, old_b = old_secs[oi]
                new_h, new_b = new_secs[ni]
                # Diff the heading if it changed
                if old_h != new_h:
                    result_parts.append(htmldiff(old_h, new_h))
                else:
                    result_parts.append(new_h)
                # Diff the body
                if old_b == new_b:
                    result_parts.append(new_b)
                else:
                    result_parts.append(diff_body(old_b, new_b))

        elif op == "replace":
            for oi in range(i1, i2):
                h, b = old_secs[oi]
                result_parts.append(f'<div class="diff-removed-section">{h}\n{b}</div>')
            for ni in range(j1, j2):
                h, b = new_secs[ni]
                result_parts.append(f'<div class="diff-added-section">{h}\n{b}</div>')

        elif op == "delete":
            for oi in range(i1, i2):
                h, b = old_secs[oi]
                result_parts.append(f'<div class="diff-removed-section">{h}\n{b}</div>')

        elif op == "insert":
            for ni in range(j1, j2):
                h, b = new_secs[ni]
                result_parts.append(f'<div class="diff-added-section">{h}\n{b}</div>')

    return "\n".join(result_parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", type=Path, help="Original markdown file")
    parser.add_argument("new", type=Path, help="Updated markdown file")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output HTML file (default: diff-<old>-<new>.html)")
    args = parser.parse_args()

    for p in (args.old, args.new):
        if not p.exists():
            print(f"Error: {p} not found", file=sys.stderr)
            sys.exit(1)

    diff_html = diff_files(args.old, args.new)

    if args.output is None:
        args.output = Path(f"diff-{args.old.stem}-vs-{args.new.stem}.html")

    title = f"Diff: {args.old.name} → {args.new.name}"

    with open(args.output, "w") as f:
        f.write(build_document(diff_html, title))

    print(f"Written to {args.output}")


if __name__ == "__main__":
    main()
