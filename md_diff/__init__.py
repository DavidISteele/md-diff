"""md-diff: Rendered markdown diff — like GitHub's Rich Diff, locally."""

from md_diff.ascii_table import convert_ascii_tables
from md_diff.rich_diff import diff_sections, render_markdown
from md_diff.view import render_document

__all__ = ["convert_ascii_tables", "diff_sections", "render_markdown",
           "render_document"]
