import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


class RecipeFeatureTests(unittest.TestCase):
    def setUp(self):
        import app as recipe_app

        self.recipe_app = recipe_app
        self.tempdir = __import__("tempfile").TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.original = {name: getattr(recipe_app, name) for name in ("DATA_DIR", "UPLOAD_DIR", "DB_FILE", "STATIC_DIR")}
        recipe_app.DATA_DIR = root
        recipe_app.UPLOAD_DIR = root / "uploads"
        recipe_app.RECIPE_IMAGE_DIR = root / "recipe-images"
        recipe_app.DB_FILE = root / "recipe_box.db"
        recipe_app.UPLOAD_DIR.mkdir()
        recipe_app.RECIPE_IMAGE_DIR.mkdir()
        self.previous = {name: os.environ.get(name) for name in ("SECRET_KEY", "EPN_ENV", "EPN_HTTPS", "SYNC_TOKEN")}
        os.environ.update(
            {
                "SECRET_KEY": "recipe-feature-test-secret-that-is-long-enough-123",
                "EPN_ENV": "development",
                "EPN_HTTPS": "0",
                "SYNC_TOKEN": "recipe-feature-sync-token",
            }
        )
        recipe_app.init_db()
        recipe_app.app.config.update(TESTING=True, ENFORCE_CSRF=True)
        self.client = recipe_app.app.test_client()
        self._signup("feature@example.com", "correct-horse", "Feature User")

    def tearDown(self):
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for name, value in self.original.items():
            setattr(self.recipe_app, name, value)
        self.tempdir.cleanup()

    def _csrf(self):
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def _signup(self, email, password, nickname):
        self.client.get("/signup")
        token = self._csrf()
        self.client.post("/signup", data={"mode": "signup", "email": email, "password": password, "csrf_token": token})
        token = self._csrf()
        return self.client.post("/profile/setup", data={"nickname": nickname, "bio": "", "csrf_token": token})

    @staticmethod
    def _image(color="red", fmt="PNG"):
        image = io.BytesIO()
        Image.new("RGB", (40, 30), color).save(image, format=fmt)
        image.seek(0)
        return image

    def _recipe(self, title="Image Recipe", image=None):
        data = {
            "title": title,
            "summary": "A recipe with an image",
            "prep_time": "20 min",
            "servings": "2",
            "category": "dinner",
            "tags": "quick, image",
            "ingredients": "pasta\nsauce",
            "steps": "Boil pasta.\nAdd sauce.",
            "csrf_token": self._csrf(),
        }
        if image is not None:
            data["image"] = (image, "recipe.png")
        response = self.client.post("/recipes/new", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        return response.location.rsplit("/", 1)[-1]

    def test_recipe_image_migration_and_existing_rows_default_empty(self):
        with self.recipe_app.db_connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(recipes)")}
            self.assertTrue(
                {"image_filename", "image_media_type", "image_width", "image_height", "image_size", "image_sha256", "archived_at"}
                <= columns
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0], 0)

    def test_recipe_image_upload_display_replacement_and_invalid_rejection(self):
        recipe_id = self._recipe(image=self._image())
        with self.recipe_app.db_connect() as conn:
            row = conn.execute(
                "SELECT image_filename, image_media_type, image_width, image_height FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
        self.assertEqual(row["image_media_type"], "image/jpeg")
        self.assertEqual((row["image_width"], row["image_height"]), (40, 30))
        first = row["image_filename"]
        self.assertTrue((self.recipe_app.RECIPE_IMAGE_DIR / first).exists())
        detail = self.client.get(f"/recipes/{recipe_id}")
        self.assertIn(b"recipe-image", detail.data)
        response = self.client.post(
            f"/recipes/{recipe_id}/edit",
            data={
                "title": "Image Recipe",
                "summary": "A recipe with an image",
                "prep_time": "20 min",
                "servings": "2",
                "category": "dinner",
                "tags": "quick",
                "ingredients": "pasta",
                "steps": "Boil",
                "csrf_token": self._csrf(),
                "image": (io.BytesIO(b"not image"), "x.jpg"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 422)
        response = self.client.post(
            f"/recipes/{recipe_id}/edit",
            data={
                "title": "Image Recipe",
                "summary": "A recipe with an image",
                "prep_time": "20 min",
                "servings": "2",
                "category": "dinner",
                "tags": "quick",
                "ingredients": "pasta",
                "steps": "Boil",
                "csrf_token": self._csrf(),
                "image": (self._image("blue"), "../../replacement.png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        with self.recipe_app.db_connect() as conn:
            replacement = conn.execute("SELECT image_filename FROM recipes WHERE id = ?", (recipe_id,)).fetchone()[0]
        self.assertNotEqual(first, replacement)
        self.assertFalse((self.recipe_app.RECIPE_IMAGE_DIR / first).exists())
        self.assertTrue((self.recipe_app.RECIPE_IMAGE_DIR / replacement).exists())
        self.assertNotIn("..", replacement)

    def test_favorite_archive_restore_and_filters_are_user_local(self):
        recipe_id = self._recipe()
        response = self.client.post(f"/recipes/{recipe_id}/favorite", data={"csrf_token": self._csrf()})
        self.assertEqual(response.status_code, 302)
        self.client.post(f"/recipes/{recipe_id}/favorite", data={"csrf_token": self._csrf()})
        with self.recipe_app.db_connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0], 1)
        response = self.client.get("/?favorites=1")
        self.assertIn(b"Image Recipe", response.data)
        response = self.client.post(f"/recipes/{recipe_id}/archive", data={"csrf_token": self._csrf()})
        self.assertEqual(response.status_code, 302)
        with self.recipe_app.db_connect() as conn:
            self.assertEqual(conn.execute("SELECT is_archived FROM recipe_user_state WHERE recipe_id = ?", (recipe_id,)).fetchone()[0], 1)
        archived_view = self.client.get("/?archived=1")
        self.assertEqual(archived_view.status_code, 200)
        detail = self.client.get(f"/recipes/{recipe_id}")
        self.assertEqual(detail.status_code, 200)
        archived_export = self.client.get(f"/recipes/{recipe_id}/export")
        self.assertEqual(archived_export.status_code, 200)
        self.assertEqual(archived_export.get_json()["recipes"][0]["id"], recipe_id)
        response = self.client.post(f"/recipes/{recipe_id}/restore", data={"csrf_token": self._csrf()})
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"Image Recipe", self.client.get("/").data)

    def test_export_import_preview_and_duplicate_conflict(self):
        recipe_id = self._recipe()
        response = self.client.get(f"/recipes/{recipe_id}/export")
        self.assertEqual(response.status_code, 200)
        exported = response.get_json()
        self.assertEqual(exported["format"], "epn-recipe-box.recipe-exchange")
        self.assertEqual(exported["version"], 1)
        self.assertNotIn("password_hash", json.dumps(exported))
        self.assertNotIn("recipe-images", json.dumps(exported))
        preview = self.client.post(
            "/recipes/import/preview",
            data={"csrf_token": self._csrf(), "exchange": (io.BytesIO(json.dumps(exported).encode()), "recipe.json")},
            content_type="multipart/form-data",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b"unchanged", preview.data)
        changed = json.loads(json.dumps(exported))
        changed["recipes"][0]["title"] = "Conflicting Imported Title"
        preview = self.client.post(
            "/recipes/import/preview",
            data={"csrf_token": self._csrf(), "exchange": (io.BytesIO(json.dumps(changed).encode()), "recipe.json")},
            content_type="multipart/form-data",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b"conflicts", preview.data)

    def test_recipe_image_size_and_dimension_limits(self):
        oversized = io.BytesIO(b"x" * (4 * 1024 * 1024 + 1))
        response = self.client.post(
            "/recipes/new",
            data={
                "title": "Too big",
                "summary": "x",
                "prep_time": "1",
                "servings": "1",
                "ingredients": "x",
                "steps": "x",
                "csrf_token": self._csrf(),
                "image": (oversized, "x.png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 422)
        huge = io.BytesIO()
        Image.new("RGB", (5000, 1), "red").save(huge, format="PNG")
        huge.seek(0)
        response = self.client.post(
            "/recipes/new",
            data={
                "title": "Too wide",
                "summary": "x",
                "prep_time": "1",
                "servings": "1",
                "ingredients": "x",
                "steps": "x",
                "csrf_token": self._csrf(),
                "image": (huge, "x.png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 422)

    def test_sync_payload_excludes_local_favorites_archive_and_image_assets(self):
        recipe_id = self._recipe()
        with self.recipe_app.db_connect() as conn:
            owner_id = conn.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()[0]
            conn.execute("INSERT INTO favorites (user_id, recipe_id, created_at) VALUES (?, ?, ?)", (owner_id, recipe_id, "now"))
            conn.execute(
                "UPDATE recipes SET archived_at = ?, image_filename = ?, image_media_type = ?, image_size = ? WHERE id = ?",
                ("now", "private.jpg", "image/jpeg", 12, recipe_id),
            )
            row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        payload = self.recipe_app.sync_recipe_payload(row)
        self.assertEqual(payload["category"], "dinner")
        self.assertEqual([tag["normalized_name"] for tag in payload["tags"]], ["image", "quick"])
        self.assertNotIn("favorite", payload)
        self.assertNotIn("archived_at", payload)
        self.assertNotIn("image_filename", payload)
        self.assertNotIn("image_size", payload)
        self.assertNotIn("private.jpg", json.dumps(payload))

        response = self.client.get("/recipes/export")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([item["id"] for item in payload["recipes"]], [recipe_id])
        imported = json.loads(json.dumps(payload))
        imported["recipes"][0]["id"] = "r-portable-copy"
        response = self.client.post(
            "/recipes/import/apply", data={"csrf_token": self._csrf(), "exchange_json": json.dumps(imported), "conflict_action": "reject"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"New: 1", response.data)
        with self.recipe_app.db_connect() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM recipes WHERE id = 'r-portable-copy'").fetchone())

        payload = {
            "format": "epn-recipe-box.recipe-exchange",
            "version": 99,
            "source_installation_id": "x",
            "exported_at": "now",
            "recipes": [],
        }
        response = self.client.post(
            "/recipes/import/preview",
            data={"csrf_token": self._csrf(), "exchange": (io.BytesIO(json.dumps(payload).encode()), "bad.json")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn(b"version", response.data.lower())


class ExchangeContractTests(unittest.TestCase):
    def test_exchange_rejects_remote_image_reference_and_preserves_deterministic_shape(self):
        from recipe_box.exchange import validate_exchange_payload

        payload = {
            "format": "epn-recipe-box.recipe-exchange",
            "version": 1,
            "source_installation_id": "box-a",
            "exported_at": "2026-01-01T00:00:00+00:00",
            "recipes": [
                {
                    "id": "r-1",
                    "title": "A",
                    "summary": "B",
                    "prep_time": "1",
                    "servings": "1",
                    "ingredients": ["x"],
                    "steps": ["y"],
                    "category": "dinner",
                    "tags": [],
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "image": {"available": True, "url": "https://example.invalid/a.jpg"},
                }
            ],
        }
        with self.assertRaises(ValueError):
            validate_exchange_payload(payload)


if __name__ == "__main__":
    unittest.main()
