"""Release build hooks for generated SBC schema artifacts."""
from __future__ import annotations

import os
import runpy
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as BuildPy


ROOT = Path(__file__).parent


class build_py(BuildPy):
    """Refresh the embedded schema catalog from a BBB checkout during builds."""

    def run(self) -> None:
        generate = runpy.run_path(str(ROOT / "tools" / "generate_schema_catalog.py"))["generate"]

        source = Path(os.environ.get(
            "SBC_BBB_SCHEMA",
            ROOT / "bigbluebutton-3.0.32" / "bbb-graphql-server" / "bbb-graphql-schema.md",
        ))
        version = os.environ.get("SBC_BBB_VERSION", "3.0")
        # ``build_py`` does not clear stale files from an earlier package layout.
        # Clear only the package output so a rebuilt wheel never carries the
        # retired flat ``sbc/*.py`` modules alongside the grouped layout.
        package_build = Path(self.build_lib) / "sbc"
        if package_build.exists():
            shutil.rmtree(package_build)
        super().run()
        # Release builders provide BBB's schema source (normally through
        # ``SBC_BBB_SCHEMA``) and receive a frozen full catalog. A public
        # Python-only source checkout deliberately omits the very large BBB
        # source tree; its bundled runtime fallback remains importable and a
        # user can opt into a full generated catalog with SBC_BBB_SCHEMA.
        if source.is_file():
            suffix = version.replace(".", "_")
            generate(source, Path(self.build_lib) / "sbc" / f"_schema_generated_{suffix}.py", version)
            generate(source, Path(self.build_lib) / "sbc" / "_schema_generated.py", version)
        generate_api = runpy.run_path(str(ROOT / "tools" / "generate_api_reference.py"))["generate"]
        generate_api(
            ROOT / "sbc" / "operations" / "__init__.py",
            Path(self.build_lib) / "sbc" / "API.md",
            Path(self.build_lib) / "sbc" / "operations" / "__init__.pyi",
        )


setup(cmdclass={"build_py": build_py})
