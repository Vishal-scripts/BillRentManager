"""The handover ZIP stays free of secrets and the real tenant list."""

from __future__ import annotations

import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_github_source_zip import build_zip as build_github_zip  # noqa: E402
from build_github_source_zip import source_files  # noqa: E402
from build_handover_zip import build_zip, handover_paths  # noqa: E402


class HandoverZipTests(unittest.TestCase):
    def test_zip_has_the_app_and_not_secrets_or_real_tenants(self):
        dest = ROOT / "dist" / "_handover_test.zip"
        try:
            build_zip(dest)
            with zipfile.ZipFile(dest) as archive:
                names = set(archive.namelist())
            self.assertIn("narela-rent/ownerapp/Start-App.bat", names)
            self.assertIn("narela-rent/ownerapp/Setup.bat", names)
            self.assertIn("narela-rent/data/sample_tenants.csv", names)
            self.assertIn("narela-rent/docs/TEST_GUIDE.md", names)
            self.assertIn("narela-rent/docs/OWNER_GUIDE.md", names)
            self.assertIn("narela-rent/billcheck/ndpl.py", names)
            self.assertNotIn("narela-rent/data/tenants.csv", names)
            self.assertFalse(any(name.endswith(".env") and not name.endswith(".env.example") for name in names))
            self.assertFalse(any(name.endswith(".db") for name in names))
            blob = "\n".join(names)
            self.assertNotIn("tenants.csv", blob.replace("sample_tenants.csv", ""))
        finally:
            dest.unlink(missing_ok=True)

    def test_handover_paths_skip_env_logs_and_the_real_list(self):
        rels = {path.relative_to(ROOT).as_posix() for path in handover_paths()}
        self.assertIn("ownerapp/.env.example", rels)
        self.assertNotIn("ownerapp/.env", rels)
        self.assertNotIn("data/tenants.csv", rels)
        self.assertFalse(any("billcheck/logs/" in rel or "billcheck/debug/" in rel for rel in rels))


class GithubSourceZipTests(unittest.TestCase):
    def test_archive_is_source_only_and_under_20_mb(self):
        dest = ROOT / "dist" / "_github_source_test.zip"
        try:
            build_github_zip(dest)
            self.assertLess(dest.stat().st_size, 20 * 1024 * 1024)
            with zipfile.ZipFile(dest) as archive:
                names = set(archive.namelist())
            self.assertIn("BillRentManager/README.md", names)
            self.assertIn("BillRentManager/LICENSE", names)
            self.assertIn("BillRentManager/requirements.txt", names)
            self.assertIn("BillRentManager/.gitignore", names)
            self.assertIn("BillRentManager/users.env.example", names)
            self.assertIn("BillRentManager/ownerapp/app.py", names)
            self.assertIn("BillRentManager/billcheck/ndpl.py", names)
            self.assertIn("BillRentManager/scripts/build_windows_portable.py", names)
            self.assertIn("BillRentManager/tests/test_owner_app.py", names)
            self.assertIn("BillRentManager/docs/OWNER_GUIDE.md", names)
            self.assertIn("BillRentManager/docs/screenshots/README.md", names)
            self.assertIn("BillRentManager/data/sample_tenants.csv", names)
            self.assertNotIn("BillRentManager/data/tenants.csv", names)
            self.assertNotIn("BillRentManager/users.env", names)
            self.assertFalse(any(name.endswith(".db") or name.endswith(".exe") for name in names))
            self.assertFalse(
                any(
                    part == ".git"
                    for name in names
                    for part in name.replace("\\", "/").split("/")
                )
            )
            self.assertFalse(any("__pycache__" in name or "/.venv/" in name or name.endswith(".pyc") for name in names))
            self.assertFalse(any("/logs/" in name or name.endswith(".jsonl") for name in names))
            self.assertFalse(any("tesseract.exe" in name or "/python/python.exe" in name for name in names))
            rels = {path.relative_to(ROOT).as_posix() for path in source_files()}
            self.assertNotIn("data/tenants.csv", rels)
            self.assertNotIn("users.env", rels)
        finally:
            dest.unlink(missing_ok=True)
