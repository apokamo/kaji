"""Tests for license selection and SPDX migration (#105).

Verifies Apache-2.0 license adoption, PEP 639 SPDX migration in pyproject.toml,
LICENSE file presence, and README.md license reference.
"""

import tarfile
import zipfile
from pathlib import Path

import pytest

# Repository root (two levels up from this test file)
REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Small tests — pure file content checks, no external dependencies
# ---------------------------------------------------------------------------


class TestSmallLicenseSpdx:
    """Small tests: validate file contents without external dependencies."""

    @pytest.mark.small
    def test_pyproject_license_is_spdx_string(self) -> None:
        """license field must be an SPDX string, not a table."""
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]

        pyproject = REPO_ROOT / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        license_value = data["project"]["license"]

        # Must be a plain string (SPDX expression), not a dict/table
        assert isinstance(license_value, str), (
            f"license must be an SPDX string, got {type(license_value).__name__}: {license_value}"
        )
        assert license_value == "Apache-2.0"

    @pytest.mark.small
    def test_pyproject_license_files(self) -> None:
        """license-files must include LICENSE."""
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]

        pyproject = REPO_ROOT / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        license_files = data["project"].get("license-files", [])

        assert "LICENSE" in license_files

    @pytest.mark.small
    def test_pyproject_no_license_classifier(self) -> None:
        """classifiers must not contain any License :: entries."""
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]

        pyproject = REPO_ROOT / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        classifiers = data["project"].get("classifiers", [])

        license_classifiers = [c for c in classifiers if c.startswith("License ::")]
        assert license_classifiers == [], (
            f"License classifiers should be removed (PEP 639): {license_classifiers}"
        )

    @pytest.mark.small
    def test_license_file_exists(self) -> None:
        """LICENSE file must exist at repository root."""
        license_file = REPO_ROOT / "LICENSE"
        assert license_file.exists(), "LICENSE file not found at repository root"

    @pytest.mark.small
    def test_license_file_contains_apache(self) -> None:
        """LICENSE file must contain Apache License Version 2.0 text."""
        license_file = REPO_ROOT / "LICENSE"
        content = license_file.read_text()

        assert "Apache License" in content
        assert "Version 2.0" in content

    @pytest.mark.small
    def test_readme_license_reference(self) -> None:
        """README.md must reference Apache-2.0 license."""
        readme = REPO_ROOT / "README.md"
        content = readme.read_text()

        assert "Apache-2.0" in content


# ---------------------------------------------------------------------------
# Medium tests — pip install + importlib.metadata
# ---------------------------------------------------------------------------


class TestMediumLicenseSpdx:
    """Medium tests: verify installed package metadata."""

    @pytest.mark.medium
    def test_metadata_license_expression(self) -> None:
        """Installed metadata must have License-Expression: Apache-2.0."""
        from importlib.metadata import metadata

        m = metadata("kaji")
        license_expr = m.get("License-Expression")

        assert license_expr == "Apache-2.0", (
            f"Expected License-Expression 'Apache-2.0', got '{license_expr}'"
        )

    @pytest.mark.medium
    def test_metadata_license_file(self) -> None:
        """Installed metadata must reference LICENSE file."""
        from importlib.metadata import metadata

        m = metadata("kaji")
        # License-File can appear multiple times; collect all
        license_files = m.get_all("License-File") or []

        assert any("LICENSE" in f for f in license_files), (
            f"Expected 'LICENSE' in License-File metadata, got {license_files}"
        )


# ---------------------------------------------------------------------------
# Large tests — build sdist/wheel and inspect archives
# ---------------------------------------------------------------------------


class TestLargeLicenseSpdx:
    """Large tests: build distribution and verify PEP 639 compliance."""

    @pytest.mark.large
    def test_sdist_contains_license(self, tmp_path: Path) -> None:
        """sdist must contain LICENSE and correct PKG-INFO metadata."""
        import subprocess

        result = subprocess.run(
            ["python", "-m", "build", "--sdist", "--outdir", str(tmp_path)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"build --sdist failed:\n{result.stderr}"

        # Find the tarball
        tarballs = list(tmp_path.glob("kaji-*.tar.gz"))
        assert len(tarballs) == 1, f"Expected 1 tarball, found {len(tarballs)}"

        with tarfile.open(tarballs[0], "r:gz") as tar:
            names = tar.getnames()

            # LICENSE must be in the archive
            license_entries = [n for n in names if n.endswith("/LICENSE")]
            assert license_entries, f"LICENSE not found in sdist. Files: {names}"

            # Find and read PKG-INFO
            pkg_info_entries = [n for n in names if n.endswith("/PKG-INFO")]
            assert pkg_info_entries, "PKG-INFO not found in sdist"

            pkg_info_file = tar.extractfile(pkg_info_entries[0])
            assert pkg_info_file is not None
            pkg_info = pkg_info_file.read().decode()

            assert "License-Expression: Apache-2.0" in pkg_info
            assert "License-File: LICENSE" in pkg_info

            # Metadata-Version must be >= 2.4
            for line in pkg_info.splitlines():
                if line.startswith("Metadata-Version:"):
                    version = line.split(":")[1].strip()
                    major, minor = (int(x) for x in version.split("."))
                    assert (major, minor) >= (2, 4), (
                        f"Metadata-Version must be >= 2.4, got {version}"
                    )
                    break

    @pytest.mark.large
    def test_wheel_contains_license(self, tmp_path: Path) -> None:
        """wheel must contain license in dist-info and correct METADATA."""
        import subprocess

        result = subprocess.run(
            ["python", "-m", "build", "--wheel", "--outdir", str(tmp_path)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"build --wheel failed:\n{result.stderr}"

        # Find the wheel
        wheels = list(tmp_path.glob("kaji-*.whl"))
        assert len(wheels) == 1, f"Expected 1 wheel, found {len(wheels)}"

        with zipfile.ZipFile(wheels[0]) as whl:
            names = whl.namelist()

            # License file in dist-info/licenses/ (setuptools 77.0.0+)
            license_entries = [n for n in names if "licenses/LICENSE" in n]
            assert license_entries, f"licenses/LICENSE not found in wheel. Files: {names}"

            # Find and read METADATA
            metadata_entries = [n for n in names if n.endswith("/METADATA")]
            assert metadata_entries, "METADATA not found in wheel"

            metadata_content = whl.read(metadata_entries[0]).decode()

            assert "License-Expression: Apache-2.0" in metadata_content
            # setuptools records the source path in METADATA, not the wheel-internal path
            assert "License-File: LICENSE" in metadata_content

            # Metadata-Version must be >= 2.4
            for line in metadata_content.splitlines():
                if line.startswith("Metadata-Version:"):
                    version = line.split(":")[1].strip()
                    major, minor = (int(x) for x in version.split("."))
                    assert (major, minor) >= (2, 4), (
                        f"Metadata-Version must be >= 2.4, got {version}"
                    )
                    break
