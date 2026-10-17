"""Import every app module; smoke-test captcha helpers."""

from __future__ import annotations

import importlib
import pkgutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ImportAllTests(unittest.TestCase):
    def test_billcheck_and_ownerapp_import(self):
        for package in ("billcheck", "ownerapp"):
            root = ROOT / package
            for module in pkgutil.walk_packages([str(root)], prefix=f"{package}."):
                if module.ispkg:
                    continue
                importlib.import_module(module.name)

    def test_captcha_helpers(self):
        from billcheck.captcha import captcha_pause, captcha_tries_from_env

        self.assertGreaterEqual(captcha_tries_from_env(), 3)
        self.assertLessEqual(captcha_tries_from_env(10), 12)
        with mock.patch("billcheck.captcha.time.sleep") as slept:
            captcha_pause(2)
            slept.assert_called_once()


if __name__ == "__main__":
    unittest.main()
