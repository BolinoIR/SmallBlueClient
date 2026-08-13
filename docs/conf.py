"""Sphinx configuration for SmallBlueClient's complete documentation site."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

project = "SmallBlueClient"
author = "SmallBlueClient community"
release = "0.4.2"
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"
html_theme = "furo"
html_title = "SmallBlueClient"
html_baseurl = "https://sbc.protobuf.lol/"
html_logo = str(ROOT / "icon700.png")
html_favicon = str(ROOT / "icon700.png")
html_static_path = ["_static"]
html_extra_path = ["CNAME"]
html_theme_options = {
    "source_repository": "https://github.com/BolinoIR/SmallBlueClient/",
    "source_branch": "main",
    "source_directory": "docs/",
}
myst_enable_extensions = ["colon_fence", "deflist", "fieldlist", "linkify", "tasklist"]
