from __future__ import annotations

import uuid
from flask import url_for
from werkzeug.utils import secure_filename

from . import config as _config
from .config import *
from .db import *

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
    upload.save(_config.UPLOAD_DIR / stored_name)
    return f"uploads/{stored_name}"

