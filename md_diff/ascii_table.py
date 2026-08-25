"""Convert ASCII box-drawing diagrams in markdown code blocks to HTML tables.

Detects code blocks containing box-drawing characters (│┌┐└┘├┤┬┴┼─),
parses the grid structure, and replaces them with styled HTML tables.

Can be used as:
  - A pre-processor: python ascii_table.py input.md > output.md
  - A library: from ascii_table import convert_ascii_tables
"""

import re
import sys
from collections import Counter

# Box-drawing characters
BOX_VERT = "│"
BOX_CHARS = set("│┌┐└┘├┤┬┴┼─━║╔╗╚╝╠╣╦╩╬")
SEPARATOR_CHARS = set("─━┌┐└┘├┤┬┴┼╔╗╚╝╠╣╦╩╬ ")


# A file tree hangs "|-- name" branches off a spine of "|" that marks
# depth.  It is drawn with the same characters as a table and is not one:
# the spine is not a column boundary, and nothing crosses it.
TREE_BRANCH_RE = re.compile(r"^[\u2502 ]*[\u251c\u2514]\u2500\u2500 ")


def is_tree_diagram(lines: list[str]) -> bool:
    """Check if a block is a file tree rather than a table.

    A tree whose entries all fit one line has a single │ column and
    fails the two-column gate in ascii_to_html_table anyway.  One entry
    with a continuation line indented under a nested branch adds a
    second │ column, which is enough to get past that gate and parse to
    no rows at all -- model-hub's design.md Module Layout tree, once a
    lease.py description ran to three lines.
    """
    branches = sum(1 for line in lines if TREE_BRANCH_RE.match(line))
    content = sum(1 for line in lines if line.strip())
    return content > 0 and branches >= content * 0.5


# The glyphs a wall can be made of: verticals, and the corners and
# junctions where one starts, ends or is crossed.
WALL_CHARS = set("\u2502\u2503\u2551\u254e\u250c\u2510\u2514\u2518"
                 "\u251c\u2524\u253c\u252c\u2534"
                 "\u2554\u2557\u255a\u255d\u2560\u2563\u256c\u2566\u2569")
HORIZONTAL_FILL = set("\u2500\u2501\u2550")

# How far a hand-drawn wall may drift between one row and the next, and
# how many rows a run needs before it counts as a wall rather than a
# connector.  The workbench spec's right frame wanders from column 70 to
# 74 and its bottom section is drawn three columns short, which is what
# sets the tolerance: at 2 that section breaks off as a wall of its own
# and straightens to the wrong column, at 3 the frame comes back whole,
# and 4 changes nothing further.
WALL_WOBBLE = 3
MIN_WALL = 3


def trace_walls(rows: list[str], width: int) -> dict[tuple[int, int], int]:
    """Map each wall glyph to the column its wall belongs in.

    Walk down from every unvisited vertical, stepping to the nearest
    vertical in the next row within WALL_WOBBLE.  The run that comes back
    is one wall however much it wanders, and its modal position is where
    it was meant to sit.

    Tracing each wall is what keeps one box's edge off another's.
    Snapping to columns the whole block agrees on is simpler and wrong:
    at this tolerance it drags the "whisper" box in model-hub's
    architecture diagram onto the broker box's wall three columns away,
    and the box stops closing.
    """
    seen, runs = set(), []
    for r in range(len(rows)):
        for c in range(width):
            if rows[r][c] not in WALL_CHARS or (r, c) in seen:
                continue
            run, row, col = [(r, c)], r, c
            seen.add((r, c))
            while row + 1 < len(rows):
                near = [(abs(x - col), x)
                        for x in range(max(0, col - WALL_WOBBLE),
                                       min(width, col + WALL_WOBBLE + 1))
                        if rows[row + 1][x] in WALL_CHARS
                        and (row + 1, x) not in seen]
                if not near:
                    break
                row, col = row + 1, min(near)[1]
                run.append((row, col))
                seen.add((row, col))
            runs.append(run)

    columns = {}
    for run in runs:
        if len(run) < MIN_WALL:
            continue
        target = Counter(c for _, c in run).most_common(1)[0][0]
        for position in run:
            columns[position] = target
    return columns


def fill_for(segment: str) -> str:
    """What to pad a segment with: its own rule character, or a space."""
    body = segment.strip()
    if body and body[0] in HORIZONTAL_FILL and len(set(body)) == 1:
        return body[0]
    return " "


def normalise_grid(code: str) -> str:
    """Straighten a ragged diagram so every wall sits on one column.

    Hand-drawn tables do not line up: in the workbench spec no two lines
    are the same length and each column drifts by a character or three.
    The parser tolerates that as it reads -- find_column_clusters chains
    positions within 2 of each other, find_actual_vert re-searches within
    1 of the cluster -- which finds cells but leaves the grid itself
    ragged, so nothing needing exact geometry can be built on it.

    Repair it once instead, by adjusting the padding rather than moving
    the glyph: pad to push a wall right, trim spare spaces to pull it
    left, and give up on the row rather than overwrite text.  Moving the
    glyph needs the target to be empty, which one row of a wall is enough
    to deny -- and then the whole wall stays crooked.
    """
    rows = code.strip("\n").split("\n")
    width = max((len(row) for row in rows), default=0)
    rows = [row.ljust(width) for row in rows]
    columns = trace_walls(rows, width)

    straightened = []
    for r, row in enumerate(rows):
        walls = sorted((c, columns[(r, c)])
                       for c in range(width) if (r, c) in columns)
        if not walls:
            straightened.append(row)
            continue
        parts, read, cursor = [], 0, 0
        for position, target in walls:
            segment = row[read:position]
            want = target - cursor
            if want > len(segment):
                segment += fill_for(segment) * (want - len(segment))
            elif want < len(segment):
                spare = len(segment) - want
                if not segment[len(segment) - spare:].strip():
                    segment = segment[:len(segment) - spare]
            parts.append(segment)
            parts.append(row[position])
            cursor = sum(len(part) for part in parts)
            read = position + 1
        parts.append(row[read:])
        straightened.append("".join(parts))

    width = max((len(row) for row in straightened), default=0)
    return "\n".join(row.ljust(width) for row in straightened)


def is_box_drawing_block(code: str) -> bool:
    """Check if a code block contains box-drawing art."""
    lines = code.strip().split("\n")
    if len(lines) < 3:
        return False
    if is_tree_diagram(lines):
        return False
    box_lines = sum(1 for line in lines if any(c in BOX_CHARS for c in line))
    return box_lines >= len(lines) * 0.6


def is_separator_row(line: str) -> bool:
    """Check if a line is a horizontal separator (mostly ─ and junctions)."""
    stripped = line.strip()
    if not stripped:
        return False
    return all(c in SEPARATOR_CHARS for c in stripped)


def find_column_clusters(lines: list[str]) -> list[list[int]]:
    """Find column boundary clusters by analyzing │ positions.

    Returns clusters of nearby positions, sorted by position.
    """
    position_counts = Counter()
    content_lines = [l for l in lines if not is_separator_row(l)]

    for line in content_lines:
        for i, c in enumerate(line):
            if c == BOX_VERT:
                position_counts[i] += 1

    if not position_counts:
        return []

    # Cluster positions within 2 chars of each other
    sorted_positions = sorted(position_counts.keys())
    clusters = []
    current_cluster = [sorted_positions[0]]

    for pos in sorted_positions[1:]:
        if pos - current_cluster[-1] <= 2:
            current_cluster.append(pos)
        else:
            clusters.append(current_cluster)
            current_cluster = [pos]
    clusters.append(current_cluster)

    return clusters


def find_actual_vert(line: str, cluster: list[int]) -> int | None:
    """Find the actual │ position in this line for a given column cluster.

    Returns the position of │ in the line that falls within the cluster
    range, or None if no │ found there.
    """
    lo = min(cluster) - 1
    hi = max(cluster) + 1
    for pos in range(max(0, lo), min(len(line), hi + 1)):
        if line[pos] == BOX_VERT:
            return pos
    return None


def extract_row_cells(line: str, clusters: list[list[int]]) -> list[str]:
    """Extract cell contents from a content row using column clusters.

    Finds the actual │ in this row for each cluster, then extracts
    text between adjacent │ positions.
    """
    # Find actual │ positions for each cluster
    actual_positions = []
    for cluster in clusters:
        pos = find_actual_vert(line, cluster)
        if pos is not None:
            actual_positions.append(pos)

    if len(actual_positions) < 2:
        return []

    cells = []
    for i in range(len(actual_positions) - 1):
        start = actual_positions[i] + 1
        end = actual_positions[i + 1]
        if start >= len(line):
            text = ""
        else:
            text = line[start:min(end, len(line))].rstrip()
        cells.append(text)

    return cells


def find_sections(lines: list[str]) -> list[list[str]]:
    """Split lines into sections separated by full horizontal rules.

    Only lines that are ENTIRELY separator chars count as dividers.
    Lines with mixed content and ─ (like inner table separators) are
    kept as content but filtered later.
    """
    sections = []
    current = []
    for line in lines:
        if is_separator_row(line):
            if current:
                sections.append(current)
                current = []
        else:
            current.append(line)
    if current:
        sections.append(current)
    return sections


def is_cell_separator(cell: str) -> bool:
    """Check if a single cell contains separator characters.

    Empty cells are NOT separators — they indicate the column
    continues through this row (no divider).
    """
    c = cell.strip()
    return bool(c) and all(ch in SEPARATOR_CHARS for ch in c)


def is_any_separator(line: str, clusters: list[list[int]]) -> bool:
    """Check if a line acts as a separator in at least some columns.

    Returns True if any column has separator content (───),
    regardless of whether other columns have real content.
    """
    cells = extract_row_cells(line, clusters)
    if not cells:
        return False
    return any(is_cell_separator(c) for c in cells)


def is_inner_separator(line: str, clusters: list[list[int]]) -> bool:
    """Check if a line is a pure separator (no real content in any column)."""
    cells = extract_row_cells(line, clusters)
    if not cells:
        return False
    has_sep = any(is_cell_separator(c) for c in cells)
    has_content = any(c.strip() and not is_cell_separator(c) for c in cells)
    return has_sep and not has_content


def get_separator_columns(line: str, clusters: list[list[int]]) -> list[bool]:
    """For a line, return which columns have separator content.

    Returns a list of booleans, one per cell. True = this column
    has separator chars (or is empty), False = has real content.
    """
    cells = extract_row_cells(line, clusters)
    return [is_cell_separator(c) for c in cells]


def is_partial_separator(line: str, clusters: list[list[int]]) -> bool:
    """Check if a line has separators in some columns but content in others."""
    cells = extract_row_cells(line, clusters)
    if not cells:
        return False
    has_sep = any(c.strip() and all(ch in SEPARATOR_CHARS for ch in c.strip()) for c in cells)
    has_content = any(c.strip() and not all(ch in SEPARATOR_CHARS for ch in c.strip()) for c in cells)
    return has_sep and has_content


LIST_MARKER_RE = re.compile(r'^(\s*)([▸▹►▻●○•·\-\*])\s+(.*)')


def format_cell_lines(lines: list[str]) -> str:
    """Format a list of lines for a cell.

    Detects bullet markers and groups continuation lines (indented
    to the text position after the marker) into the same list item.
    Non-bullet lines become plain text paragraphs.
    """
    if not lines:
        return ""

    # Parse each line: is it a bullet, a continuation, or plain text?
    items = []  # list of (type, content) where type is 'bullet', 'cont', 'text'
    bullet_text_col = None  # column where bullet text starts

    for line in lines:
        m = LIST_MARKER_RE.match(line)
        if m:
            indent, marker, text = m.groups()
            bullet_text_col = len(indent) + len(marker) + 1  # position after "▸ "
            items.append(('bullet', text))
        elif bullet_text_col is not None and len(line) > 0:
            # Check if this line is indented to align with bullet text
            stripped = line.lstrip()
            leading = len(line) - len(stripped)
            if stripped and leading >= bullet_text_col - 1:
                # Continuation of the current bullet item
                items.append(('cont', stripped))
            elif not stripped:
                # Empty line — skip
                continue
            else:
                # Less indented — plain text, reset bullet context
                items.append(('text', stripped))
                bullet_text_col = None
        else:
            stripped = line.strip()
            if stripped:
                items.append(('text', stripped))

    if not items:
        return ""

    # Check if we have any bullets at all
    has_bullets = any(t == 'bullet' for t, _ in items)
    if not has_bullets:
        return "<br>".join(c for _, c in items)

    # Build HTML: plain text as <p>-ish content, bullets as <ul><li>
    parts = []
    in_list = False
    current_li_parts = []

    def flush_li():
        if current_li_parts:
            parts.append("<li>" + "<br>".join(current_li_parts) + "</li>")
            current_li_parts.clear()

    for typ, content in items:
        if typ == 'bullet':
            if not in_list:
                in_list = True
                parts.append("<ul>")
            else:
                flush_li()
            current_li_parts.append(content)
        elif typ == 'cont':
            current_li_parts.append(content)
        else:  # 'text'
            if in_list:
                flush_li()
                parts.append("</ul>")
                in_list = False
            parts.append(content)

    if in_list:
        flush_li()
        parts.append("</ul>")

    return "".join(parts)


def merge_row_group(rows: list[list[str]]) -> list[str]:
    """Merge a group of raw rows into a single logical row.

    Preserves raw lines with leading whitespace for indentation
    detection in format_cell_lines.
    """
    if not rows:
        return []
    num_cols = len(rows[0])
    merged = [[] for _ in range(num_cols)]
    for row in rows:
        for i in range(min(num_cols, len(row))):
            cell = row[i]
            # Skip cells that are purely separator characters
            if is_cell_separator(cell):
                continue
            if cell.rstrip():
                merged[i].append(cell)
    return [format_cell_lines(lines) for lines in merged]


def detect_section_clusters(section_lines: list[str],
                            all_clusters: list[list[int]]) -> list[list[int]]:
    """Determine which column clusters are used in this section."""
    hits = Counter()
    for line in section_lines:
        for ci, cluster in enumerate(all_clusters):
            if find_actual_vert(line, cluster) is not None:
                hits[ci] += 1

    threshold = len(section_lines) * 0.4
    return [all_clusters[ci] for ci in sorted(hits)
            if hits[ci] >= threshold]


# Glyphs that draw the diagram rather than say anything: box rules, the
# arrows joining them, and the bullet markers format_cell_lines turns into
# list items.  What is left is the text a reader would lose.
DECORATION = BOX_CHARS | set("\u25bc\u25b2\u25c0\u25b6\u2192\u2190\u2193\u2191"
                             "\u25b8\u25b9\u25ba\u25bb\u25cf\u25cb\u2022\u00b7")
WORD_RE = re.compile(r"[^\s]+")


def text_tokens(text: str) -> Counter:
    """The words in a block, ignoring everything that only draws it."""
    plain = "".join(" " if c in DECORATION else c for c in text)
    return Counter(w for w in WORD_RE.findall(plain))


def preserves_text(code: str, table_html: str) -> bool:
    """Check that converting kept every word the block started with.

    The parser reads a grid: verticals are column boundaries and the text
    between them is a cell.  A diagram of nested boxes and arrows has
    verticals that mean nothing of the sort, and the same code walks it
    happily -- emitting a table that looks reasonable and quietly holds a
    third of the words.  model-hub's design.md had two: the architecture
    diagram kept 27 of 70 words, the /lease sequence 12 of 35.  Every
    real grid table in the corpus keeps all of them, so any loss at all
    means the block was not a grid, and it belongs in a code block where
    it renders as drawn.
    """
    plain = re.sub(r"<[^>]+>", " ", table_html)
    plain = (plain.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&#39;", "'")
             .replace("&amp;", "&"))
    return not (text_tokens(code) - text_tokens(plain))


def ascii_to_html_table(code: str) -> str:
    """Convert an ASCII box-drawing diagram to an HTML table."""
    lines = normalise_grid(code).split("\n")
    all_clusters = find_column_clusters(lines)

    if len(all_clusters) < 2:
        return None

    # Determine the maximum column count across all sections
    max_col_count = len(all_clusters) - 1  # cells = boundaries - 1

    sections = find_sections(lines)
    html_parts = ['<table class="ascii-converted">']

    for sec_idx, section_lines in enumerate(sections):
        sec_clusters = detect_section_clusters(section_lines, all_clusters)
        if len(sec_clusters) < 2:
            continue

        # Identify separator lines and which columns they split
        num_cells = len(sec_clusters) - 1
        separator_indices = []
        for li, line in enumerate(section_lines):
            if is_any_separator(line, sec_clusters):
                sep_cols = get_separator_columns(line, sec_clusters)
                separator_indices.append((li, sep_cols))

        # Determine which columns are split by separators
        # A column is "spanning" if it's never separated
        col_is_split = [False] * num_cells
        for _, sep_cols in separator_indices:
            for ci in range(min(num_cells, len(sep_cols))):
                if sep_cols[ci]:
                    col_is_split[ci] = True

        # Split section into row groups at separator lines.
        # For partial separators, keep the non-separator cell content
        # in the current group (it belongs to the spanning column).
        sep_line_map = {li: cols for li, cols in separator_indices}
        row_groups = []
        current_group = []
        for li, line in enumerate(section_lines):
            if li in sep_line_map:
                # This line has separators — extract any real content
                # from non-separator columns into the current group
                cells = extract_row_cells(line, sec_clusters)
                if cells:
                    sep_cols = sep_line_map[li]
                    # Zero out separator columns, keep content columns
                    cleaned = [
                        "" if (ci < len(sep_cols) and sep_cols[ci]) else c
                        for ci, c in enumerate(cells)
                    ]
                    if any(c.rstrip() for c in cleaned):
                        current_group.append(cleaned)
                # Split the row group for the separated columns
                if current_group:
                    row_groups.append(current_group)
                    current_group = []
                continue
            cells = extract_row_cells(line, sec_clusters)
            if cells:
                current_group.append(cells)
        if current_group:
            row_groups.append(current_group)

        if not row_groups:
            continue

        logical_rows = [merge_row_group(g) for g in row_groups]
        sec_col_count = len(logical_rows[0]) if logical_rows else 0
        num_row_groups = len(logical_rows)

        # For spanning columns, merge all groups into one cell
        if any(not s for s in col_is_split) and num_row_groups > 1:
            spanning_cells = {}
            for ci in range(sec_col_count):
                if not col_is_split[ci]:
                    # Merge all row group content for this column
                    all_lines = []
                    for g in row_groups:
                        for row in g:
                            if ci < len(row) and row[ci].rstrip():
                                all_lines.append(row[ci])
                    spanning_cells[ci] = format_cell_lines(all_lines)

        html_parts.append("<tbody>")
        for ri, row in enumerate(logical_rows):
            html_parts.append("  <tr>")
            for ci, cell in enumerate(row):
                # Skip spanning columns after the first row
                if not col_is_split[ci] and num_row_groups > 1 and ri > 0:
                    continue

                content = spanning_cells[ci] if (not col_is_split[ci] and num_row_groups > 1) else cell

                # Escape HTML but preserve our formatting tags
                cell_esc = (content.replace("&", "&amp;")
                            .replace("<", "&lt;").replace(">", "&gt;")
                            .replace("&lt;br&gt;", "<br>")
                            .replace("&lt;ul&gt;", "<ul>")
                            .replace("&lt;/ul&gt;", "</ul>")
                            .replace("&lt;li&gt;", "<li>")
                            .replace("&lt;/li&gt;", "</li>"))

                attrs = ""
                # Rowspan for spanning columns
                if not col_is_split[ci] and num_row_groups > 1 and ri == 0:
                    attrs += f' rowspan="{num_row_groups}"'
                # Colspan for sections with fewer columns
                if ci == len(row) - 1 and sec_col_count < max_col_count:
                    extra_span = max_col_count - sec_col_count + 1
                    attrs += f' colspan="{extra_span}"'

                html_parts.append(f"    <td{attrs}>{cell_esc}</td>")
            html_parts.append("  </tr>")
        html_parts.append("</tbody>")

    # Box-drawn, but no section parsed into rows.  The empty <table>
    # built so far is a truthy string, and convert_ascii_tables falls
    # back to the original block only on None -- returning it replaces
    # the diagram with an empty box.  Leave the block alone instead.
    if "  <tr>" not in html_parts:
        return None

    html_parts.append("</table>")
    table_html = "\n".join(html_parts)

    if not preserves_text(code, table_html):
        return None

    return table_html


def convert_ascii_tables(markdown: str) -> str:
    """Find code blocks with ASCII tables and replace with HTML."""
    return re.sub(
        r'```[^\n]*\n(.*?)```',
        lambda m: ascii_to_html_table(m.group(1)) or m.group(0)
        if is_box_drawing_block(m.group(1)) else m.group(0),
        markdown,
        flags=re.DOTALL,
    )


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Input markdown file")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    args = parser.parse_args()

    with open(args.input) as f:
        content = f.read()

    result = convert_ascii_tables(content)

    if args.output:
        with open(args.output, "w") as f:
            f.write(result)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
