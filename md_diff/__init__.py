"""md-diff: Rendered markdown diff — like GitHub's Rich Diff, locally."""

import importlib

_EXPORTS = {
    "convert_ascii_tables": "md_diff.ascii_table",
    "diff_sections": "md_diff.rich_diff",
    "render_markdown": "md_diff.rich_diff",
    "render_document": "md_diff.view",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    """Resolve the public API on first use.

    Importing the submodules here eagerly would put them in sys.modules
    before `python -m md_diff.<module>` gets to run one as __main__, which
    makes runpy warn and execute that module a second time.  The launchers
    in scripts/ run the package that way.
    """
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(__all__)
