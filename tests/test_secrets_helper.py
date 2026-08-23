#!/usr/bin/env python3
"""Contract tests for the secrets resolution helper."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import secrets_helper


class SecretsHelperTests(unittest.TestCase):
    def test_env_var_name_is_uppercase_and_prefixed(self) -> None:
        self.assertEqual(
            secrets_helper.env_var_name("shopify", "access_token"),
            "EFFICIENS_SHOPIFY_ACCESS_TOKEN",
        )

    def test_get_secret_prefers_environment(self) -> None:
        with patch.dict(
            "os.environ", {"EFFICIENS_SHOPIFY_ACCESS_TOKEN": "env-token"}, clear=False
        ):
            self.assertEqual(
                secrets_helper.get_secret("shopify", "access_token"), "env-token"
            )

    def test_get_secret_falls_back_to_file(self) -> None:
        with TemporaryDirectory() as directory:
            secrets_dir = Path(directory)
            (secrets_dir / "shopify.json").write_text(
                json.dumps({"access_token": "file-token"}), encoding="utf-8"
            )
            with patch.object(secrets_helper, "SECRETS_DIR", secrets_dir):
                with patch.object(secrets_helper, "_keyring_get", return_value=None):
                    self.assertEqual(
                        secrets_helper.get_secret("shopify", "access_token"),
                        "file-token",
                    )

    def test_get_connector_secrets_omits_missing_fields(self) -> None:
        with TemporaryDirectory() as directory:
            secrets_dir = Path(directory)
            (secrets_dir / "shopify.json").write_text(
                json.dumps({"access_token": "file-token"}), encoding="utf-8"
            )
            with patch.object(secrets_helper, "SECRETS_DIR", secrets_dir):
                with patch.object(secrets_helper, "_keyring_get", return_value=None):
                    resolved = secrets_helper.get_connector_secrets("shopify")
            self.assertEqual(resolved, {"access_token": "file-token"})

    def test_store_secret_uses_file_when_keyring_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            secrets_dir = Path(directory)
            with patch.object(secrets_helper, "SECRETS_DIR", secrets_dir):
                with patch.object(secrets_helper, "_store_keyring", return_value=False):
                    backend = secrets_helper.store_secret(
                        "shopify", "access_token", "new-token"
                    )
            self.assertEqual(backend, "file")
            stored = json.loads(
                (secrets_dir / "shopify.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stored["access_token"], "new-token")


if __name__ == "__main__":
    unittest.main()
