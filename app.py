import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from flask import Flask, flash, jsonify, redirect, render_template_string, request, send_from_directory, session, url_for
from jinja2 import DictLoader
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from sync import MAX_SYNC_BODY_BYTES, canonical_recipe, classify_merge, make_keep_both_copy, recipe_checksum, token_hash, token_matches, validate_recipe_payload


APP_TITLE = "EPN Recipe Box"
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("EPN_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_FILE = DATA_DIR / "recipe_box.db"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

STATIC_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["SYNC_TIMEOUT_SECONDS"] = float(os.environ.get("SYNC_TIMEOUT_SECONDS", "5"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def now_iso_legacy() -> str:
    return datetime.now().isoformat(timespec="seconds")


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def split_ingredients(value: str) -> list[str]:
    chunks = re.split(r"[\n,]+", value)
    return [chunk.strip().lower() for chunk in chunks if chunk.strip()]


def normalized_ingredient_words(value: str) -> list[str]:
    value = value.lower()
    value = re.sub(r"\b(cups?|tbsp|tablespoons?|tsp|teaspoons?|oz|ounces?|lbs?|pounds?|grams?|g|kg|ml|l)\b", "", value)
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    value = re.sub(r"\b\d+([./]\d+)?\b", "", value)
    words = []
    for word in value.split():
        if len(word) <= 2:
            continue
        if word.endswith("ies") and len(word) > 4:
            word = f"{word[:-3]}y"
        elif word.endswith("es") and len(word) > 4:
            word = word[:-2]
        elif word.endswith("s") and len(word) > 3:
            word = word[:-1]
        words.append(word)
    return words


def ingredient_key(value: str) -> str:
    words = normalized_ingredient_words(value)
    return " ".join(words[-2:]) if words else value.strip().lower()


def ingredient_matches(needed: str, stocked: str) -> bool:
    needed_words = normalized_ingredient_words(needed)
    stocked_words = normalized_ingredient_words(stocked)
    if not needed_words or not stocked_words:
        return needed.strip().lower() == stocked.strip().lower()
    needed_set = set(needed_words)
    stocked_set = set(stocked_words)
    return needed_set.issubset(stocked_set) or bool(needed_set & stocked_set)


def stock_has(needed: str, inventory: list[str]) -> bool:
    return any(ingredient_matches(needed, stocked) for stocked in inventory)


def allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _backup_database_before_migration() -> None:
    if not DB_FILE.exists():
        return
    backup = DB_FILE.with_name(f"{DB_FILE.name}.pre-sync-{utc_stamp()}.bak")
    shutil.copy2(DB_FILE, backup)


def sync_token() -> str:
    configured = os.environ.get("SYNC_TOKEN", "").strip()
    if configured:
        return configured
    token_file = DATA_DIR / ".sync-token"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    token_file.write_text(token + "\n", encoding="utf-8")
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    return token


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                nickname TEXT NOT NULL DEFAULT '',
                bio TEXT NOT NULL DEFAULT '',
                avatar TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory_items (
                user_id TEXT NOT NULL,
                item TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (user_id, item),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS recipes (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                prep_time TEXT NOT NULL,
                servings TEXT NOT NULL,
                ingredients_json TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ratings (
                recipe_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                score INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (recipe_id, user_id),
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS comments (
                id TEXT PRIMARY KEY,
                recipe_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS installations (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_peers (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, token TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, last_sync_at TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_baselines (
                peer_id TEXT NOT NULL, recipe_id TEXT NOT NULL, checksum TEXT NOT NULL,
                remote_updated_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (peer_id, recipe_id), FOREIGN KEY (peer_id) REFERENCES sync_peers(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sync_conflicts (
                id TEXT PRIMARY KEY, peer_id TEXT NOT NULL, recipe_id TEXT NOT NULL,
                local_json TEXT NOT NULL, remote_json TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, resolved_at TEXT,
                FOREIGN KEY (peer_id) REFERENCES sync_peers(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sync_history (
                id TEXT PRIMARY KEY, peer_id TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL, summary_json TEXT NOT NULL,
                FOREIGN KEY (peer_id) REFERENCES sync_peers(id) ON DELETE CASCADE
            );
            """
        )
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        recipe_columns = {row["name"] for row in conn.execute("PRAGMA table_info(recipes)")}
        required_alters = []
        if "sync_source_installation_id" not in recipe_columns:
            required_alters.append("ALTER TABLE recipes ADD COLUMN sync_source_installation_id TEXT NOT NULL DEFAULT ''")
        if "email" not in user_columns:
            required_alters.append("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        if "password_hash" not in user_columns:
            required_alters.append("ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
        if "nickname" not in user_columns:
            required_alters.append("ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
        if required_alters:
            _backup_database_before_migration()
            for statement in required_alters:
                conn.execute(statement)
        if "email" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        if "password_hash" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
        if "nickname" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
            if "name" in user_columns:
                conn.execute("UPDATE users SET nickname = name WHERE nickname = ''")
        installation = conn.execute("SELECT id FROM installations LIMIT 1").fetchone()
        if not installation:
            conn.execute("INSERT INTO installations (id, token_hash, created_at) VALUES (?, ?, ?)", (f"box-{uuid.uuid4().hex}", token_hash(sync_token()), now_iso()))
        else:
            conn.execute("UPDATE installations SET token_hash = ?", (token_hash(sync_token()),))


def row_to_user(row: sqlite3.Row, inventory: list[str] | None = None) -> dict:
    nickname = row["nickname"] or row["email"]
    return {
        "id": row["id"],
        "email": row["email"],
        "password_hash": row["password_hash"],
        "nickname": row["nickname"],
        "name": nickname,
        "bio": row["bio"],
        "avatar": row["avatar"],
        "inventory": inventory or [],
        "created_at": row["created_at"],
    }


def row_to_recipe(row: sqlite3.Row, ratings: list[dict] | None = None, comments: list[dict] | None = None) -> dict:
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "title": row["title"],
        "summary": row["summary"],
        "prep_time": row["prep_time"],
        "servings": row["servings"],
        "ingredients": json.loads(row["ingredients_json"]),
        "steps": json.loads(row["steps_json"]),
        "ratings": ratings or [],
        "comments": comments or [],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "sync_source_installation_id": row["sync_source_installation_id"] if "sync_source_installation_id" in row.keys() else "",
    }


def load_data() -> dict:
    init_db()
    with db_connect() as conn:
        inventory_by_user = {}
        for row in conn.execute("SELECT user_id, item FROM inventory_items ORDER BY position, item"):
            inventory_by_user.setdefault(row["user_id"], []).append(row["item"])

        users = [
            row_to_user(row, inventory_by_user.get(row["id"], []))
            for row in conn.execute("SELECT * FROM users ORDER BY created_at")
        ]

        ratings_by_recipe = {}
        for row in conn.execute("SELECT recipe_id, user_id, score FROM ratings"):
            ratings_by_recipe.setdefault(row["recipe_id"], []).append(
                {"user_id": row["user_id"], "score": row["score"]}
            )

        comments_by_recipe = {}
        for row in conn.execute("SELECT id, recipe_id, user_id, body, created_at FROM comments ORDER BY created_at"):
            comments_by_recipe.setdefault(row["recipe_id"], []).append(
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "body": row["body"],
                    "created_at": row["created_at"],
                }
            )

        recipes = [
            row_to_recipe(row, ratings_by_recipe.get(row["id"], []), comments_by_recipe.get(row["id"], []))
            for row in conn.execute("SELECT * FROM recipes ORDER BY created_at DESC")
        ]

    return {"users": users, "recipes": recipes}


def create_account(email: str, password: str) -> str:
    user_id = f"u-{uuid.uuid4().hex[:10]}"
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO users (id, email, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, email.lower(), generate_password_hash(password), now_iso()),
        )
    return user_id


def authenticate_user(email: str, password: str) -> str | None:
    init_db()
    with db_connect() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE lower(email) = lower(?)",
            (email.strip(),),
        ).fetchone()
    if row and check_password_hash(row["password_hash"], password):
        return row["id"]
    return None


def update_profile(user_id: str, nickname: str, bio: str, avatar: str) -> None:
    with db_connect() as conn:
        if avatar:
            conn.execute(
                "UPDATE users SET nickname = ?, bio = ?, avatar = ? WHERE id = ?",
                (nickname, bio, avatar, user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET nickname = ?, bio = ? WHERE id = ?",
                (nickname, bio, user_id),
            )


def update_inventory(user_id: str, items: list[str]) -> None:
    unique_items = list(dict.fromkeys(items))
    with db_connect() as conn:
        conn.execute("DELETE FROM inventory_items WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO inventory_items (user_id, item, position) VALUES (?, ?, ?)",
            [(user_id, item, position) for position, item in enumerate(unique_items)],
        )


def create_recipe(owner_id: str, form) -> str:
    recipe_id = f"r-{uuid.uuid4().hex[:10]}"
    timestamp = now_iso()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO recipes (
                id, owner_id, title, summary, prep_time, servings,
                ingredients_json, steps_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recipe_id,
                owner_id,
                form["title"].strip(),
                form["summary"].strip(),
                form["prep_time"].strip(),
                form["servings"].strip(),
                json.dumps(split_ingredients(form["ingredients"])),
                json.dumps(split_lines(form["steps"])),
                timestamp,
                timestamp,
            ),
        )
    return recipe_id


def update_recipe(recipe_id: str, form) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE recipes
            SET title = ?, summary = ?, prep_time = ?, servings = ?,
                ingredients_json = ?, steps_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                form["title"].strip(),
                form["summary"].strip(),
                form["prep_time"].strip(),
                form["servings"].strip(),
                json.dumps(split_ingredients(form["ingredients"])),
                json.dumps(split_lines(form["steps"])),
                now_iso(),
                recipe_id,
            ),
        )


def save_rating(recipe_id: str, user_id: str, score: int) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO ratings (recipe_id, user_id, score, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(recipe_id, user_id)
            DO UPDATE SET score = excluded.score, created_at = excluded.created_at
            """,
            (recipe_id, user_id, score, now_iso()),
        )


def create_comment(recipe_id: str, user_id: str, body: str) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO comments (id, recipe_id, user_id, body, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (f"c-{uuid.uuid4().hex[:10]}", recipe_id, user_id, body, now_iso()),
        )


def current_user(data: dict) -> dict | None:
    user_id = session.get("user_id")
    user = next((user for user in data["users"] if user["id"] == user_id), None)
    if user_id and not user:
        session.pop("user_id", None)
    return user


def profile_ready(user: dict | None) -> bool:
    return bool(user and user.get("nickname"))


def recipe_owner(data: dict, recipe: dict) -> dict:
    return next((user for user in data["users"] if user["id"] == recipe["owner_id"]), {"name": "Unknown cook", "avatar": ""})


def average_rating(recipe: dict) -> float:
    ratings = recipe.get("ratings", [])
    if not ratings:
        return 0
    return round(sum(item["score"] for item in ratings) / len(ratings), 1)


def suggestion_score(recipe: dict, inventory: list[str]) -> tuple[int, int, list[str], list[str]]:
    recipe_keys = {ingredient_key(item) for item in recipe["ingredients"]}
    matched = sorted(item for item in recipe["ingredients"] if stock_has(item, inventory))
    missing = sorted(item for item in recipe["ingredients"] if not stock_has(item, inventory))
    return len(matched), len(recipe_keys), matched, missing

RECIPE_IDEA_PATTERNS = [
    {
        "id": "fried-rice",
        "title": "Stock Pot Fried Rice",
        "core": ["rice", "egg", "garlic", "soy sauce"],
        "optional": ["peas", "carrot", "green onion", "onion", "spinach", "chicken"],
        "prep_time": "20 min",
        "servings": "2",
        "summary": "A fast skillet rice bowl built from pantry staples and whatever vegetables are on hand.",
        "steps": [
            "Warm a large skillet with a little oil over medium-high heat.",
            "Cook garlic and any chopped vegetables until fragrant and just tender.",
            "Add rice and stir until hot, then move it to one side of the pan.",
            "Scramble the egg in the open space, fold everything together, and season with soy sauce.",
        ],
    },
    {
        "id": "pantry-pasta",
        "title": "Pantry Pasta Card",
        "core": ["pasta", "garlic", "olive oil"],
        "optional": ["tomatoes", "basil", "spinach", "parmesan", "mushrooms", "chicken"],
        "prep_time": "25 min",
        "servings": "2",
        "summary": "A simple pasta that turns stock ingredients into a glossy weeknight dinner.",
        "steps": [
            "Boil pasta in salted water until tender, saving a little cooking water.",
            "Warm olive oil with garlic in a skillet until fragrant.",
            "Add any vegetables or protein from your stock and cook until ready.",
            "Toss in pasta with a splash of cooking water until lightly sauced.",
        ],
    },
    {
        "id": "egg-scramble",
        "title": "Recipe Box Scramble",
        "core": ["egg", "cheese"],
        "optional": ["spinach", "tomatoes", "onion", "mushrooms", "peppers", "toast"],
        "prep_time": "15 min",
        "servings": "1",
        "summary": "A quick, flexible scramble for using up small amounts of vegetables and cheese.",
        "steps": [
            "Whisk eggs with a pinch of salt and pepper.",
            "Cook any chopped vegetables in a nonstick pan until softened.",
            "Add eggs and stir gently until softly set.",
            "Fold in cheese at the end and serve right away.",
        ],
    },
    {
        "id": "hearty-soup",
        "title": "Stock Drawer Soup",
        "core": ["broth", "onion", "carrot"],
        "optional": ["potatoes", "rice", "pasta", "chicken", "beans", "spinach", "garlic"],
        "prep_time": "35 min",
        "servings": "4",
        "summary": "A cozy soup formula that turns stock vegetables, grains, and protein into dinner.",
        "steps": [
            "Cook onion and carrot with a little oil until softened.",
            "Add broth and any sturdy vegetables, grains, beans, or protein from your stock.",
            "Simmer until everything is tender.",
            "Season to taste and finish with any tender greens near the end.",
        ],
    },
    {
        "id": "taco-bowl",
        "title": "Build-A-Bowl Supper",
        "core": ["rice", "beans", "salsa"],
        "optional": ["cheese", "lettuce", "tomatoes", "corn", "chicken", "avocado", "onion"],
        "prep_time": "20 min",
        "servings": "2",
        "summary": "A hearty bowl assembled from grains, beans, and bright toppings.",
        "steps": [
            "Warm rice and beans separately or together in a skillet.",
            "Stir in salsa and any cooked protein from your stock.",
            "Spoon into bowls and add any fresh toppings you have.",
            "Finish with cheese or herbs if available.",
        ],
    },
    {
        "id": "sheet-pan",
        "title": "One Pan Roast Card",
        "core": ["potatoes", "onion", "olive oil"],
        "optional": ["carrot", "chicken", "sausage", "broccoli", "peppers", "garlic"],
        "prep_time": "45 min",
        "servings": "3",
        "summary": "A hands-off roast that works with sturdy vegetables and a simple oil seasoning.",
        "steps": [
            "Heat the oven to 425 F and cut ingredients into bite-size pieces.",
            "Toss everything with olive oil, salt, pepper, and any seasonings you like.",
            "Spread on a sheet pan with space between pieces.",
            "Roast until browned and tender, turning once halfway through.",
        ],
    },    {
        "id": "pantry-pancakes",
        "title": "One-Ingredient-Away Pancakes",
        "core": ["flour", "egg", "baking soda", "milk"],
        "optional": ["butter", "sugar", "salt"],
        "prep_time": "20 min",
        "servings": "2",
        "summary": "A simple breakfast card from baking staples. Add milk and your pantry is nearly there.",
        "steps": [
            "Whisk flour, baking soda, a little sugar, and a pinch of salt.",
            "Beat in egg and milk until just combined.",
            "Cook small pancakes in butter over medium heat.",
            "Flip when bubbles form and serve warm.",
        ],
    },
    {
        "id": "loaded-potatoes",
        "title": "Loaded Potato Cards",
        "core": ["potatoes", "butter", "cheese"],
        "optional": ["bacon", "salt", "eggs", "garlic"],
        "prep_time": "35 min",
        "servings": "2",
        "summary": "Crispy or fluffy potatoes finished with butter, cheese, and savory toppings.",
        "steps": [
            "Cook potatoes until tender by baking, boiling, or microwaving.",
            "Split or smash them and add butter and salt.",
            "Top with cheese and bacon, then warm until melted.",
            "Add a fried egg if you want it to eat like a meal.",
        ],
    },
    {
        "id": "pork-chop-plate",
        "title": "Pork Chop Supper Card",
        "core": ["pork chops", "potatoes", "butter", "salt"],
        "optional": ["garlic", "flour", "bacon"],
        "prep_time": "35 min",
        "servings": "2",
        "summary": "A straightforward dinner plate with seared pork chops and buttery potatoes.",
        "steps": [
            "Season pork chops with salt and any spices you like.",
            "Cook potatoes until tender, then finish with butter.",
            "Sear pork chops in a hot pan until browned and cooked through.",
            "Rest the pork briefly before serving with the potatoes.",
        ],
    },
    {
        "id": "bacon-pasta",
        "title": "Bacon Pasta Skillet",
        "core": ["pasta", "bacon", "egg", "cheese"],
        "optional": ["butter", "garlic", "salt"],
        "prep_time": "25 min",
        "servings": "2",
        "summary": "A creamy-style pasta using bacon, egg, and cheese from your stock.",
        "steps": [
            "Boil pasta until tender, saving some pasta water.",
            "Cook bacon until crisp and keep a little of the rendered fat.",
            "Whisk egg with cheese in a bowl.",
            "Toss hot pasta with bacon, then remove from heat and stir in the egg mixture with pasta water until glossy.",
        ],
    },
    {
        "id": "naan-pizza",
        "title": "Naan Pizza Card",
        "core": ["naan bread", "cheese", "tomato sauce"],
        "optional": ["bacon", "garlic", "pork chops"],
        "prep_time": "18 min",
        "servings": "1",
        "summary": "A fast personal pizza idea. Add tomato sauce and your naan and cheese become dinner.",
        "steps": [
            "Heat the oven to 425 F.",
            "Spread tomato sauce over naan bread.",
            "Top with cheese and any cooked toppings from your stock.",
            "Bake until the edges are crisp and the cheese is melted.",
        ],
    },
]


def inventory_key_set(inventory: list[str]) -> set[str]:
    return {ingredient_key(item) for item in inventory}


def pattern_matches(pattern: dict, inventory: list[str]) -> dict:
    matched_core = [item for item in pattern["core"] if stock_has(item, inventory)]
    missing_core = [item for item in pattern["core"] if not stock_has(item, inventory)]
    matched_optional = [item for item in pattern["optional"] if stock_has(item, inventory)]
    ingredients = matched_core + matched_optional
    return {
        "matched_core": matched_core,
        "missing_core": missing_core,
        "matched_optional": matched_optional,
        "ingredients": ingredients,
    }


def build_recipe_idea(pattern: dict, matches: dict, missing: list[str], kind: str) -> dict:
    ingredients = matches["ingredients"] + missing
    return {
        "id": pattern["id"],
        "kind": kind,
        "title": pattern["title"],
        "summary": pattern["summary"],
        "prep_time": pattern["prep_time"],
        "servings": pattern["servings"],
        "ingredients": ingredients,
        "steps": pattern["steps"],
        "matched": matches["ingredients"],
        "missing": missing,
        "match_count": len(matches["ingredients"]),
        "ingredient_count": len(ingredients),
    }


def generated_recipe_ideas(inventory: list[str]) -> tuple[list[dict], list[dict]]:
    make_now = []
    add_one = []
    if not inventory:
        return make_now, add_one

    for pattern in RECIPE_IDEA_PATTERNS:
        matches = pattern_matches(pattern, inventory)
        if not matches["missing_core"] and matches["ingredients"]:
            make_now.append(build_recipe_idea(pattern, matches, [], "now"))
        elif len(matches["missing_core"]) == 1 and matches["matched_core"]:
            add_one.append(build_recipe_idea(pattern, matches, matches["missing_core"], "one"))

    make_now.sort(key=lambda idea: (idea["match_count"], -idea["ingredient_count"]), reverse=True)
    add_one.sort(key=lambda idea: (idea["match_count"], -idea["ingredient_count"]), reverse=True)
    return make_now[:4], add_one[:4]


def save_generated_recipe(owner_id: str, idea: dict) -> str:
    payload = {
        "title": idea["title"],
        "summary": idea["summary"],
        "prep_time": idea["prep_time"],
        "servings": idea["servings"],
        "ingredients": "\n".join(idea["ingredients"]),
        "steps": "\n".join(idea["steps"]),
    }
    return create_recipe(owner_id, payload)

def decorate_recipe(data: dict, recipe: dict, inventory: list[str] | None = None) -> dict:
    match_count, ingredient_count, matched, missing = suggestion_score(recipe, inventory or [])
    decorated = dict(recipe)
    decorated["owner"] = recipe_owner(data, recipe)
    decorated["average_rating"] = average_rating(recipe)
    decorated["rating_count"] = len(recipe.get("ratings", []))
    decorated["match_count"] = match_count
    decorated["ingredient_count"] = ingredient_count
    decorated["matched"] = matched
    decorated["missing"] = missing
    decorated["share_url"] = url_for("recipe_detail", recipe_id=recipe["id"], _external=True)
    return decorated


def save_avatar(upload) -> str:
    if not upload or not upload.filename or not allowed_image(upload.filename):
        return ""
    filename = secure_filename(upload.filename)
    suffix = filename.rsplit(".", 1)[1].lower()
    stored_name = f"avatar-{uuid.uuid4().hex[:10]}.{suffix}"
    upload.save(UPLOAD_DIR / stored_name)
    return f"uploads/{stored_name}"


TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #2d241d;
      --muted: #74665a;
      --paper: #fff8e8;
      --paper-deep: #f5dfb9;
      --line: #e5c991;
      --accent: #b13f2c;
      --accent-dark: #7e2d24;
      --green: #416f42;
      --blue: #355c7d;
      --box: #9b5a2e;
      --shadow: 0 16px 38px rgba(70, 39, 17, .18);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(111, 74, 38, .05) 1px, transparent 1px),
        linear-gradient(#f8ead0, #f4dcb7 48%, #ead0a5);
      background-size: 18px 18px, auto;
    }
    a { color: inherit; }
    .shell {
      width: min(100%, 980px);
      margin: 0 auto;
      padding: 14px 14px 92px;
    }
    .hero {
      min-height: 250px;
      border: 1px solid rgba(77, 46, 22, .18);
      border-radius: 8px;
      background-image: linear-gradient(180deg, rgba(34, 19, 8, .08), rgba(34, 19, 8, .5)), url("{{ url_for('static', filename='recipe-box.png') }}");
      background-size: cover;
      background-position: center;
      color: white;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      padding: 18px;
      box-shadow: var(--shadow);
    }
    .hero h1 {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      font-size: clamp(2rem, 12vw, 4.5rem);
      line-height: .9;
      letter-spacing: 0;
      text-shadow: 0 2px 16px rgba(0,0,0,.45);
    }
    .hero p {
      max-width: 620px;
      margin: 8px 0 0;
      font-size: 1rem;
      text-shadow: 0 2px 12px rgba(0,0,0,.55);
    }
    .topbar {
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      margin: 14px 0;
    }
    .profile-chip, .nav-chip {
      border: 1px solid rgba(91, 55, 27, .2);
      background: rgba(255, 248, 232, .88);
      border-radius: 999px;
      padding: 8px 12px;
      text-decoration: none;
      color: var(--ink);
      white-space: nowrap;
      box-shadow: 0 8px 18px rgba(70,39,17,.08);
    }
    .profile-chip {
      display: flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      min-width: max-content;
    }
    .avatar {
      width: 34px;
      height: 34px;
      border-radius: 50%;
      border: 2px solid #fff3d8;
      object-fit: cover;
      background: #c46c45;
      display: grid;
      place-items: center;
      color: white;
      font-weight: 800;
      flex: 0 0 auto;
    }
    .nav {
      display: none;
      gap: 8px;
      min-width: max-content;
    }
    .flash {
      margin: 12px 0;
      padding: 12px 14px;
      border-radius: 8px;
      background: #ecf5df;
      border: 1px solid #bad19c;
      color: #2f4d24;
    }
    .flash.error {
      background: #fff0ec;
      border-color: #e3a493;
      color: #722d22;
    }
    .workspace {
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
    }
    .recipe-box {
      position: relative;
      padding: 16px 12px 18px;
      border: 1px solid rgba(74, 38, 13, .22);
      border-radius: 8px;
      background:
        linear-gradient(90deg, rgba(255,255,255,.14), transparent 18%, rgba(0,0,0,.08) 100%),
        linear-gradient(#b47442, #8d4f2a);
      box-shadow: inset 0 0 0 3px rgba(255,255,255,.12), var(--shadow);
    }
    .box-label {
      color: #fff1ce;
      font-family: Georgia, "Times New Roman", serif;
      font-size: 1.3rem;
      margin: 0 0 12px;
    }
    .cards {
      display: grid;
      gap: 12px;
    }
    .recipe-card, .panel {
      position: relative;
      border: 1px solid #dfbd7a;
      border-radius: 8px;
      background:
        repeating-linear-gradient(0deg, transparent 0 30px, rgba(207, 165, 85, .34) 31px 32px),
        linear-gradient(96deg, rgba(177,63,44,.14) 0 3px, transparent 3px),
        var(--paper);
      box-shadow: 0 10px 24px rgba(73, 42, 15, .14);
    }
    .recipe-card { padding: 18px 16px 14px; }
    .recipe-card::before {
      content: "";
      position: absolute;
      top: -1px;
      right: 18px;
      width: 92px;
      height: 20px;
      border-radius: 0 0 7px 7px;
      background: var(--paper-deep);
      border: 1px solid #d1ac66;
      border-top: 0;
    }
    .recipe-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }
    h2, h3 {
      font-family: Georgia, "Times New Roman", serif;
      letter-spacing: 0;
    }
    h2 { margin: 0 0 8px; font-size: 1.45rem; }
    h3 { margin: 0 0 8px; font-size: 1.2rem; }
    .meta, .small {
      color: var(--muted);
      font-size: .9rem;
    }
    .summary { margin: 9px 0 12px; }
    .tags {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      margin: 10px 0;
    }
    .tag {
      border-radius: 999px;
      padding: 5px 8px;
      background: rgba(65,111,66,.12);
      color: #315434;
      border: 1px solid rgba(65,111,66,.22);
      font-size: .8rem;
    }
    .tag.missing {
      background: rgba(177,63,44,.09);
      color: var(--accent-dark);
      border-color: rgba(177,63,44,.2);
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    button, .button {
      appearance: none;
      border: 1px solid rgba(91, 55, 27, .25);
      border-radius: 8px;
      background: var(--accent);
      color: white;
      padding: 10px 12px;
      font: inherit;
      font-weight: 750;
      text-decoration: none;
      cursor: pointer;
      min-height: 42px;
    }
    .button.secondary, button.secondary {
      background: #fff5df;
      color: var(--ink);
    }
    .button.green, button.green { background: var(--green); }
    .panel {
      padding: 16px;
    }
    .panel + .panel { margin-top: 14px; }
    form { display: grid; gap: 10px; }
    label { display: grid; gap: 6px; font-weight: 760; color: #4b392a; }
    input, textarea, select {
      width: 100%;
      border: 1px solid #d4b274;
      border-radius: 8px;
      background: rgba(255,255,255,.68);
      color: var(--ink);
      padding: 11px 12px;
      font: inherit;
      min-height: 42px;
    }
    textarea { min-height: 96px; resize: vertical; }
    .two { display: grid; grid-template-columns: 1fr; gap: 10px; }
    .rating-row {
      display: flex;
      gap: 6px;
      align-items: center;
      flex-wrap: wrap;
    }
    .star-button {
      width: 40px;
      height: 40px;
      padding: 0;
      display: grid;
      place-items: center;
      background: #fff5df;
      color: #8a5b18;
    }
    .comment {
      border-top: 1px dashed #cfa85f;
      padding-top: 10px;
      margin-top: 10px;
    }
    .empty {
      padding: 18px;
      border: 1px dashed rgba(91,55,27,.32);
      border-radius: 8px;
      background: rgba(255,248,232,.5);
      color: var(--muted);
    }
    .bottom-nav {
      position: fixed;
      left: 50%;
      bottom: 12px;
      transform: translateX(-50%);
      width: min(calc(100% - 22px), 520px);
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 6px;
      padding: 7px;
      border-radius: 18px;
      background: rgba(61, 36, 18, .88);
      backdrop-filter: blur(14px);
      box-shadow: 0 16px 36px rgba(40,21,8,.32);
      z-index: 10;
    }
    .bottom-nav a {
      color: #fff7e6;
      text-decoration: none;
      text-align: center;
      padding: 8px 5px;
      border-radius: 13px;
      font-size: .8rem;
      font-weight: 760;
    }
    .bottom-nav a.active {
      background: #fff4d8;
      color: var(--accent-dark);
    }
    .eyebrow { margin: 0 0 4px; color: var(--accent-dark); text-transform: uppercase; letter-spacing: .12em; font-size: .76rem; font-weight: 800; }
    .sync-grid { display: grid; gap: 14px; }
    .peer-card, .status-note, .preview-row, .conflict-row { padding: 12px; border: 1px solid #dfbd7a; border-radius: 8px; background: rgba(255,248,232,.72); }
    .peer-card + .peer-card, .preview-row + .preview-row { margin-top: 10px; }
    .status-note { color: #315434; background: #ecf5df; border-color: #bad19c; }
    .status-badge { display: inline-flex; align-items: center; min-height: 30px; padding: 4px 9px; border-radius: 999px; font-size: .78rem; font-weight: 800; }
    .status-badge.ok { color: #315434; background: #dcebd1; }
    .status-badge.muted { color: var(--muted); background: #eee1c9; }
    .preview-row strong { text-transform: capitalize; color: var(--accent-dark); }
    .conflict-row { border-color: #e3a493; background: #fff0ec; }
    button:focus-visible, a:focus-visible, input:focus-visible, textarea:focus-visible { outline: 3px solid var(--blue); outline-offset: 3px; }
    button:active, .button:active { transform: scale(.96); }
    @media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; } }
    @media (min-width: 740px) {
      .sync-grid { grid-template-columns: minmax(0, 1.35fr) minmax(270px, .8fr); align-items: start; }
      .hero { min-height: 330px; padding: 30px; }
      .workspace { grid-template-columns: minmax(0, 1.5fr) minmax(270px, .8fr); align-items: start; }
      .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .two { grid-template-columns: repeat(2, 1fr); }
      .nav { display: flex; }
      .bottom-nav { display: none; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <h1>EPN Recipe Box</h1>
      <p>Create, share, rate, and remix recipes from the food you already have.</p>
    </section>

    <div class="topbar">
      <a class="profile-chip" href="{{ url_for('profile_setup') if user else url_for('signup') }}">
        {% if user and user.avatar %}
          <img class="avatar" src="{{ url_for('uploaded_file', filename=user.avatar.replace('uploads/', '')) }}" alt="">
        {% else %}
          <span class="avatar">{{ (user.name[:1] if user else "R") }}</span>
        {% endif %}
        <span>{{ user.name if user and user.nickname else "Sign up" }}</span>
      </a>
      <nav class="nav" aria-label="Primary">
        <a class="nav-chip" href="{{ url_for('index') }}">Cards</a>
        <a class="nav-chip" href="{{ url_for('new_recipe') }}">New recipe</a>
        <a class="nav-chip" href="{{ url_for('inventory') }}">Inventory</a>
        <a class="nav-chip" href="{{ url_for('sync_dashboard') }}">Sync</a>
        {% if user %}
          <form method="post" action="{{ url_for('logout') }}" style="margin:0;">
            <button class="secondary" type="submit">Logout</button>
          </form>
        {% endif %}
      </nav>
    </div>

    {% with messages = get_flashed_messages(with_categories=true) %}
      {% for category, message in messages %}
        <div class="flash {% if category == 'error' %}error{% endif %}">{{ message }}</div>
      {% endfor %}
    {% endwith %}

    {% block content %}{% endblock %}
  </main>

  <nav class="bottom-nav" aria-label="Mobile primary">
    <a class="{% if active == 'cards' %}active{% endif %}" href="{{ url_for('index') }}">Cards</a>
    <a class="{% if active == 'new' %}active{% endif %}" href="{{ url_for('new_recipe') }}">Create</a>
    <a class="{% if active == 'inventory' %}active{% endif %}" href="{{ url_for('inventory') }}">Stock</a>
    <a class="{% if active == 'sync' %}active{% endif %}" href="{{ url_for('sync_dashboard') }}">Sync</a>
    <a class="{% if active == 'profile' %}active{% endif %}" href="{{ url_for('profile_setup') if user else url_for('signup') }}">{{ "Profile" if user and user.nickname else "Sign up" }}</a>
  </nav>
</body>
</html>
"""


INDEX_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="workspace">
  <div class="recipe-box">
    <p class="box-label">Shared cards</p>
    <div class="cards">
      {% for recipe in recipes %}
        <article class="recipe-card">
          <div class="recipe-head">
            <div>
              <h2>{{ recipe.title }}</h2>
              <div class="meta">by {{ recipe.owner.name }} &middot; {{ recipe.prep_time }} &middot; {{ recipe.servings }} servings</div>
            </div>
            <div class="meta">&#9733; {{ recipe.average_rating or "New" }}{% if recipe.rating_count %} ({{ recipe.rating_count }}){% endif %}</div>
          </div>
          <p class="summary">{{ recipe.summary }}</p>
          <div class="tags">
            {% for item in recipe.ingredients[:5] %}
              <span class="tag">{{ item }}</span>
            {% endfor %}
          </div>
          {% if recipe.match_count %}
            <div class="small">{{ recipe.match_count }} of {{ recipe.ingredient_count }} ingredients in stock</div>
          {% endif %}
          <div class="actions">
            <a class="button" href="{{ url_for('recipe_detail', recipe_id=recipe.id) }}">Open card</a>
            {% if user and recipe.owner_id == user.id %}
              <a class="button secondary" href="{{ url_for('edit_recipe', recipe_id=recipe.id) }}">Edit</a>
            {% endif %}
          </div>
        </article>
      {% else %}
        <div class="empty">{% if user %}No recipes yet. Add the first card to the box.{% else %}Sign up to start building your recipe box.{% endif %}</div>
      {% endfor %}
    </div>
  </div>

  <aside>
    <section class="panel">
      <h3>Your food stock</h3>
      {% if user.inventory %}
        <div class="tags">
          {% for item in user.inventory %}
            <span class="tag">{{ item }}</span>
          {% endfor %}
        </div>
      {% else %}
        <p class="small">Add ingredients to unlock recipe suggestions.</p>
      {% endif %}
      <div class="actions">
        <a class="button green" href="{{ url_for('inventory') }}">Update stock</a>
      </div>
    </section>

    <section class="panel">
      <h3>Best matches</h3>
      {% for recipe in suggestions %}
        <p><strong>{{ recipe.title }}</strong><br><span class="small">{{ recipe.match_count }} of {{ recipe.ingredient_count }} matched</span></p>
      {% else %}
        <p class="small">Add stock items and recipes will sort themselves into place.</p>
      {% endfor %}
    </section>
  </aside>
</section>
{% endblock %}
"""


RECIPE_FORM_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="panel">
  <h2>{{ "Edit recipe" if recipe else "Create a recipe card" }}</h2>
  <form method="post">
    <label>Recipe name
      <input name="title" value="{{ recipe.title if recipe else '' }}" required>
    </label>
    <label>Short note
      <textarea name="summary" required>{{ recipe.summary if recipe else '' }}</textarea>
    </label>
    <div class="two">
      <label>Prep time
        <input name="prep_time" value="{{ recipe.prep_time if recipe else '' }}" placeholder="35 min" required>
      </label>
      <label>Servings
        <input name="servings" value="{{ recipe.servings if recipe else '' }}" placeholder="4" required>
      </label>
    </div>
    <label>Ingredients
      <textarea name="ingredients" placeholder="One per line or comma separated" required>{{ recipe.ingredients|join('\\n') if recipe else '' }}</textarea>
    </label>
    <label>Steps
      <textarea name="steps" placeholder="One step per line" required>{{ recipe.steps|join('\\n') if recipe else '' }}</textarea>
    </label>
    <div class="actions">
      <button type="submit">{{ "Save changes" if recipe else "Add to box" }}</button>
      <a class="button secondary" href="{{ url_for('index') }}">Cancel</a>
    </div>
  </form>
</section>
{% endblock %}
"""


DETAIL_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="workspace">
  <article class="recipe-card">
    <div class="recipe-head">
      <div>
        <h2>{{ recipe.title }}</h2>
        <div class="meta">by {{ recipe.owner.name }} &middot; {{ recipe.prep_time }} &middot; {{ recipe.servings }} servings</div>
      </div>
      <div class="meta">&#9733; {{ recipe.average_rating or "New" }}</div>
    </div>
    <p class="summary">{{ recipe.summary }}</p>

    <h3>Ingredients</h3>
    <div class="tags">
      {% for item in recipe.ingredients %}
        <span class="tag {% if item in recipe.missing %}missing{% endif %}">{{ item }}</span>
      {% endfor %}
    </div>

    <h3>Steps</h3>
    <ol>
      {% for step in recipe.steps %}
        <li>{{ step }}</li>
      {% endfor %}
    </ol>

    <div class="actions">
      {% if user and recipe.owner_id == user.id %}
        <a class="button secondary" href="{{ url_for('edit_recipe', recipe_id=recipe.id) }}">Edit recipe</a>
      {% endif %}
      <button class="secondary" type="button" onclick="navigator.clipboard && navigator.clipboard.writeText('{{ recipe.share_url }}')">Copy share link</button>
    </div>
    <p class="small">{{ recipe.share_url }}</p>
  </article>

  <aside>
    <section class="panel">
      <h3>Rate this recipe</h3>
      <form method="post" action="{{ url_for('rate_recipe', recipe_id=recipe.id) }}">
        <div class="rating-row">
          {% for score in range(1, 6) %}
            <button class="star-button" name="score" value="{{ score }}" title="{{ score }} stars">&#9733;</button>
          {% endfor %}
        </div>
      </form>
    </section>

    <section class="panel">
      <h3>Comments</h3>
      {% for comment in recipe.comments %}
        <div class="comment">
          <strong>{{ comment.user.name }}</strong>
          <p>{{ comment.body }}</p>
          <div class="small">{{ comment.created_at[:10] }}</div>
        </div>
      {% else %}
        <p class="small">No comments yet.</p>
      {% endfor %}
      <form method="post" action="{{ url_for('comment_recipe', recipe_id=recipe.id) }}">
        <label>Add a comment
          <textarea name="body" required></textarea>
        </label>
        <button type="submit">Post comment</button>
      </form>
    </section>
  </aside>
</section>
{% endblock %}
"""


SIGNUP_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="workspace">
  <div class="panel">
    <h2>Sign up</h2>
    <form method="post">
      <input type="hidden" name="mode" value="signup">
      <label>Email
        <input type="email" name="email" autocomplete="email" required>
      </label>
      <label>Password
        <input type="password" name="password" autocomplete="new-password" minlength="8" required>
      </label>
      <button type="submit">Create account</button>
    </form>
  </div>
  <div class="panel">
    <h2>Sign in</h2>
    <form method="post">
      <input type="hidden" name="mode" value="login">
      <label>Email
        <input type="email" name="email" autocomplete="email" required>
      </label>
      <label>Password
        <input type="password" name="password" autocomplete="current-password" required>
      </label>
      <button class="secondary" type="submit">Sign in</button>
    </form>
  </div>
</section>
{% endblock %}
"""


PROFILE_SETUP_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="workspace">
  <div class="panel">
    <h2>Set up profile</h2>
    <form method="post" enctype="multipart/form-data">
      <label>Profile nickname
        <input name="nickname" value="{{ user.nickname if user else '' }}" autocomplete="nickname" required>
      </label>
      <label>About
        <textarea name="bio" placeholder="Favorite cuisines, dietary notes, cooking style">{{ user.bio if user else '' }}</textarea>
      </label>
      <label>Avatar
        <input type="file" name="avatar" accept="image/*">
      </label>
      <button type="submit">Save profile</button>
    </form>
  </div>
  <div class="panel">
    <h2>Account</h2>
    <p class="small">{{ user.email }}</p>
    <form method="post" action="{{ url_for('logout') }}">
      <button class="secondary" type="submit">Logout</button>
    </form>
  </div>
</section>
{% endblock %}
"""


INVENTORY_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="workspace">
  <div class="panel">
    <h2>Food stock inventory</h2>
    <form method="post">
      <label>Ingredients you have
        <textarea name="inventory" placeholder="eggs, rice, spinach">{{ user.inventory|join('\\n') }}</textarea>
      </label>
      <button class="green" type="submit">Update stock</button>
    </form>
  </div>
  <div class="recipe-box">
    <p class="box-label">Generated from stock</p>
    <div class="cards">
      {% for idea in generated_now %}
        <article class="recipe-card">
          <h2>{{ idea.title }}</h2>
          <div class="meta">Uses {{ idea.match_count }} stocked ingredients &middot; {{ idea.prep_time }}</div>
          <p class="summary">{{ idea.summary }}</p>
          <div class="tags">
            {% for item in idea.matched %}
              <span class="tag">{{ item }}</span>
            {% endfor %}
          </div>
          <form method="post" action="{{ url_for('save_generated') }}">
            <input type="hidden" name="idea_id" value="{{ idea.id }}">
            <input type="hidden" name="kind" value="{{ idea.kind }}">
            <button type="submit">Save recipe</button>
          </form>
        </article>
      {% else %}
        <div class="empty">Add more stock items to generate recipes you can make now.</div>
      {% endfor %}
    </div>

    <p class="box-label">Add one ingredient</p>
    <div class="cards">
      {% for idea in generated_one %}
        <article class="recipe-card">
          <h2>{{ idea.title }}</h2>
          <div class="meta">Add {{ idea.missing[0] }} &middot; {{ idea.prep_time }}</div>
          <p class="summary">{{ idea.summary }}</p>
          <div class="tags">
            {% for item in idea.matched %}
              <span class="tag">{{ item }}</span>
            {% endfor %}
            {% for item in idea.missing %}
              <span class="tag missing">{{ item }}</span>
            {% endfor %}
          </div>
          <form method="post" action="{{ url_for('save_generated') }}">
            <input type="hidden" name="idea_id" value="{{ idea.id }}">
            <input type="hidden" name="kind" value="{{ idea.kind }}">
            <button class="secondary" type="submit">Save idea</button>
          </form>
        </article>
      {% else %}
        <div class="empty">No one-ingredient-away ideas yet.</div>
      {% endfor %}
    </div>

    <p class="box-label">Saved recipe matches</p>
    <div class="cards">
      {% for recipe in suggestions %}
        <article class="recipe-card">
          <h2>{{ recipe.title }}</h2>
          <div class="meta">{{ recipe.match_count }} of {{ recipe.ingredient_count }} ingredients ready</div>
          <div class="tags">
            {% for item in recipe.matched %}
              <span class="tag">{{ item }}</span>
            {% endfor %}
            {% for item in recipe.missing[:4] %}
              <span class="tag missing">{{ item }}</span>
            {% endfor %}
          </div>
          <a class="button" href="{{ url_for('recipe_detail', recipe_id=recipe.id) }}">Cook this</a>
        </article>
      {% else %}
        <div class="empty">Saved recipes will appear here after you add cards to the box.</div>
      {% endfor %}
    </div>
  </div>
</section>
{% endblock %}
"""


app.jinja_loader = DictLoader({
    "base": TEMPLATE,
    "index": INDEX_TEMPLATE,
    "recipe_form": RECIPE_FORM_TEMPLATE,
    "detail": DETAIL_TEMPLATE,
    "signup": SIGNUP_TEMPLATE,
    "profile_setup": PROFILE_SETUP_TEMPLATE,
    "inventory": INVENTORY_TEMPLATE,
})


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/")
def index():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    inventory = user.get("inventory", []) if user else []
    recipes = [decorate_recipe(data, recipe, inventory) for recipe in data["recipes"]]
    suggestions = sorted(recipes, key=lambda item: (item["match_count"], item["average_rating"]), reverse=True)[:3]
    return render_template_string(
        INDEX_TEMPLATE,
        title=APP_TITLE,
        user=user,
        recipes=recipes,
        suggestions=suggestions,
        active="cards",
    )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    data = load_data()
    user = current_user(data)
    if profile_ready(user):
        return redirect(url_for("index"))
    if user:
        return redirect(url_for("profile_setup"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        mode = request.form.get("mode", "signup")
        if mode == "login":
            user_id = authenticate_user(email, password)
            if not user_id:
                flash("Email or password did not match.", "error")
                return redirect(url_for("signup"))
            session["user_id"] = user_id
            data = load_data()
            user = current_user(data)
            return redirect(url_for("index") if profile_ready(user) else url_for("profile_setup"))
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect(url_for("signup"))
        try:
            user_id = create_account(email, password)
        except sqlite3.IntegrityError:
            flash("An account already exists for that email. Sign in instead.", "error")
            return redirect(url_for("signup"))
        session["user_id"] = user_id
        flash("Account created. Set up your profile next.")
        return redirect(url_for("profile_setup"))
    return render_template_string(SIGNUP_TEMPLATE, title=APP_TITLE, user=user, active="profile")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("signup"))


@app.route("/profiles", methods=["GET", "POST"])
@app.route("/profile/setup", methods=["GET", "POST"])
def profile_setup():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        if not nickname:
            flash("Choose a profile nickname.", "error")
            return redirect(url_for("profile_setup"))
        avatar = save_avatar(request.files.get("avatar"))
        update_profile(user["id"], nickname, request.form.get("bio", "").strip(), avatar)
        flash("Profile saved.")
        return redirect(url_for("index"))
    return render_template_string(PROFILE_SETUP_TEMPLATE, title=APP_TITLE, user=user, active="profile")


@app.route("/recipes/new", methods=["GET", "POST"])
def new_recipe():
    data = load_data()
    user = current_user(data)
    if not user:
        flash("Create a profile before adding recipes.", "error")
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    if request.method == "POST":
        recipe_id = create_recipe(user["id"], request.form)
        flash("Recipe card added to the box.")
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    return render_template_string(RECIPE_FORM_TEMPLATE, title=APP_TITLE, user=user, recipe=None, active="new")


@app.route("/recipes/<recipe_id>")
def recipe_detail(recipe_id):
    data = load_data()
    user = current_user(data)
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    if not recipe:
        flash("Recipe not found.", "error")
        return redirect(url_for("index"))
    decorated = decorate_recipe(data, recipe, user.get("inventory", []) if user else [])
    comments = []
    for comment in decorated.get("comments", []):
        enriched = dict(comment)
        enriched["user"] = next((profile for profile in data["users"] if profile["id"] == comment["user_id"]), {"name": "Guest"})
        comments.append(enriched)
    decorated["comments"] = comments
    return render_template_string(DETAIL_TEMPLATE, title=APP_TITLE, user=user, recipe=decorated, active="cards")


@app.route("/recipes/<recipe_id>/edit", methods=["GET", "POST"])
def edit_recipe(recipe_id):
    data = load_data()
    user = current_user(data)
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    if not recipe:
        flash("Recipe not found.", "error")
        return redirect(url_for("index"))
    if not user or recipe["owner_id"] != user["id"]:
        flash("Only the recipe owner can edit this card.", "error")
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    if request.method == "POST":
        update_recipe(recipe_id, request.form)
        flash("Recipe card updated.")
        return redirect(url_for("recipe_detail", recipe_id=recipe_id))
    return render_template_string(RECIPE_FORM_TEMPLATE, title=APP_TITLE, user=user, recipe=recipe, active="cards")


@app.route("/recipes/<recipe_id>/rate", methods=["POST"])
def rate_recipe(recipe_id):
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    if recipe:
        score = max(1, min(5, int(request.form["score"])))
        save_rating(recipe_id, user["id"], score)
        flash("Rating saved.")
    return redirect(url_for("recipe_detail", recipe_id=recipe_id))


@app.route("/recipes/<recipe_id>/comment", methods=["POST"])
def comment_recipe(recipe_id):
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    recipe = next((item for item in data["recipes"] if item["id"] == recipe_id), None)
    body = request.form.get("body", "").strip()
    if recipe and body:
        create_comment(recipe_id, user["id"], body)
        flash("Comment added.")
    return redirect(url_for("recipe_detail", recipe_id=recipe_id))


@app.route("/inventory", methods=["GET", "POST"])
def inventory():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))
    if request.method == "POST":
        update_inventory(user["id"], split_ingredients(request.form.get("inventory", "")))
        flash("Food stock updated.")
        return redirect(url_for("inventory"))
    inventory_items = user.get("inventory", [])
    generated_now, generated_one = generated_recipe_ideas(inventory_items)
    suggestions = [
        decorate_recipe(data, recipe, inventory_items)
        for recipe in data["recipes"]
    ]
    suggestions = sorted(suggestions, key=lambda item: (item["match_count"], item["average_rating"]), reverse=True)
    return render_template_string(
        INVENTORY_TEMPLATE,
        title=APP_TITLE,
        user=user,
        suggestions=suggestions,
        generated_now=generated_now,
        generated_one=generated_one,
        active="inventory",
    )


@app.route("/inventory/generated/save", methods=["POST"])
def save_generated():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    if not profile_ready(user):
        return redirect(url_for("profile_setup"))

    generated_now, generated_one = generated_recipe_ideas(user.get("inventory", []))
    candidates = generated_now + generated_one
    idea_id = request.form.get("idea_id", "")
    kind = request.form.get("kind", "")
    idea = next((item for item in candidates if item["id"] == idea_id and item["kind"] == kind), None)
    if not idea:
        flash("That generated recipe is no longer available. Update your stock and try again.", "error")
        return redirect(url_for("inventory"))

    recipe_id = save_generated_recipe(user["id"], idea)
    flash("Generated recipe saved to your recipe box.")
    return redirect(url_for("recipe_detail", recipe_id=recipe_id))
SYNC_TEMPLATE = """
{% extends "base" %}
{% block content %}
<section class="sync-dashboard" aria-labelledby="sync-title">
  <div class="panel">
    <p class="eyebrow">Connected kitchens</p>
    <h2 id="sync-title">Recipe Box sync</h2>
    <p class="summary">Connect another Recipe Box on your LAN or private network. Sync is manual, previewed, and never deletes local cards.</p>
    <div class="status-note" role="note">Your sync token stays outside the interface. Share it only with a trusted Recipe Box operator.</div>
  </div>
  <div class="panel">
    <h3>Add a trusted peer</h3>
    <form method="post" action="{{ url_for('add_sync_peer') }}">
      <label>Peer name<input name="name" required maxlength="80" placeholder="Kitchen Pi"></label>
      <label>Peer URL<input name="url" type="url" required maxlength="500" placeholder="http://kitchen-pi:5000"></label>
      <label>Shared token<input name="token" type="password" required maxlength="500" autocomplete="new-password"></label>
      <button type="submit">Add peer</button>
    </form>
  </div>
  <div class="sync-grid">
    <div>
      <h3>Connected Recipe Boxes</h3>
      {% for peer in peers %}
      <article class="peer-card">
        <div class="recipe-head"><div><h3>{{ peer.name }}</h3><p class="small">{{ peer.url }}</p></div><span class="status-badge {{ 'ok' if peer.enabled else 'muted' }}">{{ 'Enabled' if peer.enabled else 'Disabled' }}</span></div>
        <p class="small">Last sync: {{ peer.last_sync_at or 'Not yet synced' }}</p>
        <div class="actions">
          <form method="post" action="{{ url_for('preview_sync_peer', peer_id=peer.id) }}"><button class="secondary" type="submit">Preview changes</button></form>
          <form method="post" action="{{ url_for('run_sync_peer', peer_id=peer.id) }}"><button class="green" type="submit">Sync now</button></form>
          <form method="post" action="{{ url_for('toggle_sync_peer', peer_id=peer.id) }}"><button class="secondary" type="submit">{{ 'Disable' if peer.enabled else 'Enable' }}</button></form>
          <form method="post" action="{{ url_for('delete_sync_peer', peer_id=peer.id) }}"><button class="secondary" type="submit">Remove</button></form>
        </div>
      </article>
      {% else %}<div class="empty">No peers yet. Add another trusted Recipe Box to begin.</div>{% endfor %}
    </div>
    <aside>
      <section class="panel"><h3>Recent synchronization</h3>{% for item in history %}<p class="small"><strong>{{ item.status }}</strong> · {{ item.started_at }}<br>{{ item.summary_label }}</p>{% else %}<p class="small">Sync results will appear here.</p>{% endfor %}</section>
      {% if preview %}<section class="panel" aria-labelledby="preview-title"><h3 id="preview-title">Preview: {{ preview.peer_name }}</h3><p class="small">New {{ preview.counts.new }} · Updated {{ preview.counts.update }} · Unchanged {{ preview.counts.unchanged }} · Conflicts {{ preview.counts.conflict }} · Failures {{ preview.counts.failure }}</p>{% for item in preview.items %}<div class="preview-row"><strong>{{ item.status }}</strong> · {{ item.recipe.title }}</div>{% endfor %}</section>{% endif %}
      {% if conflicts %}<section class="panel" aria-labelledby="conflict-title"><h3 id="conflict-title">Conflicts needing a decision</h3>{% for conflict in conflicts %}<div class="conflict-row"><strong>{{ conflict.recipe_id }}</strong><div class="actions"><form method="post" action="{{ url_for('resolve_sync_conflict', conflict_id=conflict.id) }}"><input type="hidden" name="resolution" value="keep_local"><button class="secondary" type="submit">Keep local</button></form><form method="post" action="{{ url_for('resolve_sync_conflict', conflict_id=conflict.id) }}"><input type="hidden" name="resolution" value="use_remote"><button type="submit">Use remote</button></form><form method="post" action="{{ url_for('resolve_sync_conflict', conflict_id=conflict.id) }}"><input type="hidden" name="resolution" value="keep_both"><button class="green" type="submit">Keep both</button></form></div></div>{% endfor %}</section>{% endif %}
    </aside>
  </div>
</section>
{% endblock %}
"""


def sync_installation_id() -> str:
    init_db()
    with db_connect() as conn:
        return conn.execute("SELECT id FROM installations LIMIT 1").fetchone()["id"]


def sync_json_error(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def local_session_user():
    data = load_data()
    return current_user(data)


def require_local_json_auth():
    if not local_session_user():
        return sync_json_error("AUTHENTICATION_REQUIRED", "Sign in before using synchronization.", 401)
    return None


def require_peer_auth():
    authorization = request.headers.get("Authorization", "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    init_db()
    with db_connect() as conn:
        row = conn.execute("SELECT token_hash FROM installations LIMIT 1").fetchone()
    if not row or not token_matches(token, row["token_hash"]):
        return sync_json_error("AUTHENTICATION_FAILED", "A valid sync token is required.", 401)
    return None


def sync_recipe_payload(row: sqlite3.Row | dict) -> dict:
    recipe = {
        "id": row["id"], "title": row["title"], "summary": row["summary"],
        "prep_time": row["prep_time"], "servings": row["servings"],
        "ingredients": json.loads(row["ingredients_json"]), "steps": json.loads(row["steps_json"]),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    recipe = validate_recipe_payload(recipe)
    recipe["checksum"] = recipe_checksum(recipe)
    return recipe


def valid_peer_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username and not parsed.password and not parsed.fragment


def get_sync_peer(peer_id: str):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM sync_peers WHERE id = ?", (peer_id,)).fetchone()


def fetch_peer_manifest(peer: sqlite3.Row) -> dict:
    since = f"?{urlencode({'since': peer['last_sync_at']})}" if peer["last_sync_at"] else ""
    endpoint = urljoin(peer["url"].rstrip("/") + "/", "api/sync/manifest") + since
    if not valid_peer_url(endpoint):
        raise ValueError("Peer URL must be an HTTP or HTTPS URL without credentials.")
    request_obj = Request(endpoint, headers={"Authorization": f"Bearer {peer['token']}", "Accept": "application/json"})
    try:
        with urlopen(request_obj, timeout=app.config["SYNC_TIMEOUT_SECONDS"]) as response:
            body = response.read(MAX_SYNC_BODY_BYTES + 1)
    except HTTPError as exc:
        raise RuntimeError(f"Peer returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Peer did not respond before the timeout.") from exc
    if len(body) > MAX_SYNC_BODY_BYTES:
        raise RuntimeError("Peer response exceeded the synchronization size limit.")
    try:
        manifest = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Peer returned malformed JSON.") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("recipes"), list) or len(manifest["recipes"]) > 500:
        raise RuntimeError("Peer returned an invalid recipe manifest.")
    manifest["recipes"] = [validate_recipe_payload(item) for item in manifest["recipes"]]
    return manifest


def preview_peer_changes(peer_id: str) -> dict:
    peer = get_sync_peer(peer_id)
    if not peer or not peer["enabled"]:
        raise ValueError("That peer is missing or disabled.")
    manifest = fetch_peer_manifest(peer)
    items = []
    with db_connect() as conn:
        for remote in manifest["recipes"]:
            row = conn.execute("SELECT * FROM recipes WHERE id = ?", (remote["id"],)).fetchone()
            local = sync_recipe_payload(row) if row else None
            baseline = conn.execute("SELECT checksum FROM sync_baselines WHERE peer_id = ? AND recipe_id = ?", (peer_id, remote["id"])).fetchone()
            status = classify_merge(local, remote, baseline["checksum"] if baseline else None)
            items.append({"status": status, "recipe": remote, "local": local})
    counts = {name: sum(item["status"] == name for item in items) for name in ("new", "update", "unchanged", "conflict")}
    counts["failure"] = 0
    return {"peer_id": peer_id, "peer_name": peer["name"], "source_installation_id": manifest.get("installation_id", "unknown"), "items": items, "counts": counts}


@app.route("/api/sync/manifest")
def sync_manifest():
    auth_error = require_peer_auth()
    if auth_error:
        return auth_error
    if request.content_length and request.content_length > MAX_SYNC_BODY_BYTES:
        return sync_json_error("PAYLOAD_TOO_LARGE", "The request is too large.", 413)
    since = request.args.get("since", "").strip()
    init_db()
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM recipes ORDER BY updated_at DESC LIMIT 500").fetchall()
        recipes = []
        for row in rows:
            if since and row["updated_at"] <= since:
                continue
            recipes.append(sync_recipe_payload(row))
    return jsonify({"installation_id": sync_installation_id(), "recipes": recipes})


@app.route("/api/sync/recipes/<recipe_id>")
def sync_recipe(recipe_id):
    auth_error = require_peer_auth()
    if auth_error:
        return auth_error
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    if not row:
        return sync_json_error("NOT_FOUND", "Recipe not found.", 404)
    return jsonify(sync_recipe_payload(row))


@app.route("/sync")
def sync_dashboard():
    data = load_data()
    user = current_user(data)
    if not user:
        return redirect(url_for("signup"))
    with db_connect() as conn:
        peers = [dict(row) for row in conn.execute("SELECT id, name, url, enabled, last_sync_at FROM sync_peers ORDER BY name")]
        history = []
        for row in conn.execute("SELECT status, started_at, summary_json FROM sync_history ORDER BY started_at DESC LIMIT 6"):
            summary = json.loads(row["summary_json"])
            history.append({**dict(row), "summary_label": f"{summary.get('new', 0)} new · {summary.get('update', 0)} updated · {summary.get('unchanged', 0)} unchanged · {summary.get('conflict', 0)} conflicts · {summary.get('imported', 0)} imported"})
        conflicts = [dict(row) for row in conn.execute("SELECT id, recipe_id FROM sync_conflicts WHERE status = 'open' ORDER BY created_at")]
    return render_template_string(SYNC_TEMPLATE, title=APP_TITLE, user=user, peers=peers, history=history, conflicts=conflicts, preview=session.pop("sync_preview", None), active="sync")


@app.route("/sync/peers", methods=["POST"])
def add_sync_peer():
    data = load_data(); user = current_user(data)
    if not user: return redirect(url_for("signup"))
    name, url, token = request.form.get("name", "").strip(), request.form.get("url", "").strip().rstrip("/"), request.form.get("token", "").strip()
    if not name or len(name) > 80 or not valid_peer_url(url) or not token or len(token) > 500:
        flash("Enter a peer name, a valid HTTP(S) URL, and its shared token.", "error")
        return redirect(url_for("sync_dashboard"))
    with db_connect() as conn:
        conn.execute("INSERT INTO sync_peers (id, name, url, token, created_at) VALUES (?, ?, ?, ?, ?)", (f"peer-{uuid.uuid4().hex}", name, url, token, now_iso()))
    flash("Trusted peer added.")
    return redirect(url_for("sync_dashboard"))


@app.route("/sync/peers/<peer_id>/toggle", methods=["POST"])
def toggle_sync_peer(peer_id):
    if not local_session_user(): return redirect(url_for("signup"))
    with db_connect() as conn: conn.execute("UPDATE sync_peers SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id = ?", (peer_id,))
    return redirect(url_for("sync_dashboard"))


@app.route("/sync/peers/<peer_id>/delete", methods=["POST"])
def delete_sync_peer(peer_id):
    if not local_session_user(): return redirect(url_for("signup"))
    with db_connect() as conn: conn.execute("DELETE FROM sync_peers WHERE id = ?", (peer_id,))
    flash("Peer removed.")
    return redirect(url_for("sync_dashboard"))


@app.route("/api/sync/preview", methods=["POST"])
def api_sync_preview():
    auth_error = require_local_json_auth()
    if auth_error: return auth_error
    data = request.get_json(silent=True) or {}
    try: return jsonify(preview_peer_changes(str(data["peer_id"])))
    except KeyError: return sync_json_error("VALIDATION_ERROR", "peer_id is required.", 422)
    except (ValueError, RuntimeError) as exc: return sync_json_error("SYNC_FAILED", str(exc), 502)


@app.route("/sync/peers/<peer_id>/preview", methods=["POST"])
def preview_sync_peer(peer_id):
    if not local_session_user(): return redirect(url_for("signup"))
    try: session["sync_preview"] = preview_peer_changes(peer_id); flash("Preview ready. Review the counts before syncing.")
    except (ValueError, RuntimeError) as exc: flash(str(exc), "error")
    return redirect(url_for("sync_dashboard"))


@app.route("/api/sync/run", methods=["POST"])
def api_sync_run():
    auth_error = require_local_json_auth()
    if auth_error: return auth_error
    data = request.get_json(silent=True) or {}
    try: return jsonify(run_peer_sync(str(data["peer_id"])))
    except KeyError: return sync_json_error("VALIDATION_ERROR", "peer_id is required.", 422)
    except (ValueError, RuntimeError) as exc: return sync_json_error("SYNC_FAILED", str(exc), 502)


def run_peer_sync(peer_id: str) -> dict:
    preview = preview_peer_changes(peer_id)
    peer = get_sync_peer(peer_id)
    started = now_iso(); history_id = f"sync-{uuid.uuid4().hex}"
    imported = 0; conflicts = 0
    try:
        with db_connect() as conn:
            owner = conn.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
            if not owner: raise ValueError("Create a local account before importing recipes.")
            for item in preview["items"]:
                remote = item["recipe"]; status = item["status"]
                if status == "new":
                    conn.execute("INSERT INTO recipes (id, owner_id, title, summary, prep_time, servings, ingredients_json, steps_json, created_at, updated_at, sync_source_installation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (remote["id"], owner["id"], remote["title"], remote["summary"], remote["prep_time"], remote["servings"], json.dumps(remote["ingredients"]), json.dumps(remote["steps"]), remote["created_at"], remote["updated_at"], preview["source_installation_id"])); imported += 1
                elif status == "update":
                    conn.execute("UPDATE recipes SET title=?, summary=?, prep_time=?, servings=?, ingredients_json=?, steps_json=?, updated_at=?, sync_source_installation_id=? WHERE id=?", (remote["title"], remote["summary"], remote["prep_time"], remote["servings"], json.dumps(remote["ingredients"]), json.dumps(remote["steps"]), remote["updated_at"], preview["source_installation_id"], remote["id"])); imported += 1
                elif status == "conflict":
                    existing_conflict = conn.execute("SELECT id FROM sync_conflicts WHERE peer_id = ? AND recipe_id = ? AND status = 'open'", (peer_id, remote["id"])).fetchone()
                    if not existing_conflict:
                        conn.execute("INSERT INTO sync_conflicts (id, peer_id, recipe_id, local_json, remote_json, status, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)", (f"conflict-{uuid.uuid4().hex}", peer_id, remote["id"], json.dumps(item["local"]), json.dumps(remote), now_iso()))
                    conflicts += 1
                conn.execute("INSERT INTO sync_baselines (peer_id, recipe_id, checksum, remote_updated_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(peer_id, recipe_id) DO UPDATE SET checksum=excluded.checksum, remote_updated_at=excluded.remote_updated_at, updated_at=excluded.updated_at", (peer_id, remote["id"], recipe_checksum(remote), remote["updated_at"], now_iso()))
            summary = dict(preview["counts"]); summary["imported"] = imported
            conn.execute("UPDATE sync_peers SET last_sync_at = ? WHERE id = ?", (now_iso(), peer_id))
            conn.execute("INSERT INTO sync_history (id, peer_id, started_at, finished_at, status, summary_json) VALUES (?, ?, ?, ?, ?, ?)", (history_id, peer_id, started, now_iso(), "conflict" if conflicts else "success", json.dumps(summary, sort_keys=True)))
        return {"status": "conflict" if conflicts else "success", "summary": summary}
    except Exception:
        with db_connect() as conn: conn.execute("INSERT INTO sync_history (id, peer_id, started_at, finished_at, status, summary_json) VALUES (?, ?, ?, ?, 'failure', ?)", (history_id, peer_id, started, now_iso(), json.dumps({"error": "sync failed"})))
        raise


@app.route("/sync/peers/<peer_id>/run", methods=["POST"])
def run_sync_peer(peer_id):
    if not local_session_user(): return redirect(url_for("signup"))
    try:
        result = run_peer_sync(peer_id); flash(f"Sync complete: {result['summary']['imported']} imported, {result['summary']['conflict']} conflicts.", "error" if result["status"] == "conflict" else "")
    except (ValueError, RuntimeError) as exc: flash(str(exc), "error")
    return redirect(url_for("sync_dashboard"))


@app.route("/api/sync/conflicts/<conflict_id>/resolve", methods=["POST"])
def api_resolve_sync_conflict(conflict_id):
    auth_error = require_local_json_auth()
    if auth_error: return auth_error
    try: return jsonify(resolve_sync_conflict_action(conflict_id, (request.get_json(silent=True) or {}).get("resolution", "")))
    except ValueError as exc: return sync_json_error("VALIDATION_ERROR", str(exc), 422)


def resolve_sync_conflict_action(conflict_id: str, resolution: str) -> dict:
    if resolution not in {"keep_local", "use_remote", "keep_both"}: raise ValueError("resolution must be keep_local, use_remote, or keep_both")
    with db_connect() as conn:
        conflict = conn.execute("SELECT * FROM sync_conflicts WHERE id = ? AND status = 'open'", (conflict_id,)).fetchone()
        if not conflict: raise ValueError("Conflict not found or already resolved.")
        local, remote = json.loads(conflict["local_json"]), json.loads(conflict["remote_json"])
        if resolution == "use_remote":
            conn.execute("UPDATE recipes SET title=?, summary=?, prep_time=?, servings=?, ingredients_json=?, steps_json=?, updated_at=? WHERE id=?", (remote["title"], remote["summary"], remote["prep_time"], remote["servings"], json.dumps(remote["ingredients"]), json.dumps(remote["steps"]), remote["updated_at"], remote["id"]))
        elif resolution == "keep_both":
            copy = make_keep_both_copy(remote)
            owner = conn.execute("SELECT owner_id FROM recipes WHERE id = ?", (local["id"],)).fetchone()["owner_id"]
            conn.execute("INSERT INTO recipes (id, owner_id, title, summary, prep_time, servings, ingredients_json, steps_json, created_at, updated_at, sync_source_installation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'remote')", (copy["id"], owner, copy["title"], copy["summary"], copy["prep_time"], copy["servings"], json.dumps(copy["ingredients"]), json.dumps(copy["steps"]), copy["created_at"], copy["updated_at"]))
        conn.execute("UPDATE sync_conflicts SET status = 'resolved', resolved_at = ? WHERE id = ?", (now_iso(), conflict_id))
    return {"status": "resolved", "resolution": resolution}


@app.route("/sync/conflicts/<conflict_id>/resolve", methods=["POST"])
def resolve_sync_conflict(conflict_id):
    if not local_session_user(): return redirect(url_for("signup"))
    try: resolve_sync_conflict_action(conflict_id, request.form.get("resolution", "")); flash("Conflict resolved.")
    except ValueError as exc: flash(str(exc), "error")
    return redirect(url_for("sync_dashboard"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
