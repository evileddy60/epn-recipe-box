import json
import tempfile
import unittest
from pathlib import Path


class BetaDownloadRouteTests(unittest.TestCase):
    def setUp(self):
        import app as recipe_app
        import recipe_box.config as config

        self.recipe_app = recipe_app
        self.config = config
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.previous = config.PUBLIC_BETA_DIR
        config.PUBLIC_BETA_DIR = root
        (root / "latest.apk").write_bytes(b"approved apk")
        (root / "latest.sha256").write_text("0" * 64 + "  latest.apk\n")
        (root / "INSTALL.md").write_text("Install with Tailscale")
        (root / "CHANGELOG.md").write_text("# Changelog\n\n## 0.5.1\n\n- Real release note\n")
        (root / "release.json").write_text(json.dumps({
            "application": "EPN Recipe Box", "channel": "private-beta", "version_name": "0.5.1", "version_code": 7,
            "package_name": "ca.evilpeoplenetwork.recipebox", "apk": "latest.apk", "sha256": "0" * 64,
            "release_date": "2026-08-07T00:00:00+00:00", "minimum_android_api": 26, "minimum_android_version": "8.0",
            "server": "https://epn-hermes-worker-01.tail510dca.ts.net", "release_notes": "CHANGELOG.md",
        }))
        self.recipe_app.app.config.update(TESTING=True, ENFORCE_CSRF=False)
        self.client = recipe_app.app.test_client()

    def tearDown(self):
        self.config.PUBLIC_BETA_DIR = self.previous
        self.tempdir.cleanup()

    def test_beta_page_and_allowlisted_artifacts(self):
        response = self.client.get("/beta")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Download Android Beta", response.data)
        self.assertIn(b"0.5.1", response.data)
        apk = self.client.get("/beta/latest.apk")
        self.assertEqual(apk.status_code, 200)
        self.assertEqual(apk.mimetype, "application/vnd.android.package-archive")
        self.assertIn("EPN-Recipe-Box-Private-Beta-0.5.1.apk", apk.headers["Content-Disposition"])
        self.assertEqual(self.client.get("/beta/release.json").mimetype, "application/json")
        self.assertEqual(self.client.get("/beta/CHANGELOG.md").status_code, 200)

    def test_traversal_and_unapproved_files_are_rejected(self):
        self.assertEqual(self.client.get("/beta/../release.json").status_code, 404)
        self.assertEqual(self.client.get("/beta/.env").status_code, 404)
        self.assertEqual(self.client.get("/beta/releases/0.5.1/app.apk").status_code, 404)


if __name__ == "__main__":
    unittest.main()
