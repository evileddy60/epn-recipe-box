# EPN Recipe Box

EPN Recipe Box is a small Flask web app for creating, sharing, rating, and commenting on recipes. It is designed mobile-first and styled like a box of handwritten recipe cards.

## Features

- Email and password signup with securely hashed passwords
- Profile setup with nickname, about text, and avatar upload
- Create, edit, and share recipe cards
- Rate recipes from 1 to 5 stars
- Comment on recipes
- Keep a food stock inventory
- Suggest recipes based on ingredients you already have
- Local SQLite database storage

## Project Files

- `app.py` - the Flask application, routes, templates, styling, and SQLite setup
- `static/recipe-box.png` - header image used by the app
- `recipe_box.db` - local SQLite database created when the app runs
- `static/uploads/` - local avatar uploads

`recipe_box.db` and `static/uploads/` are ignored by Git so personal/local user data is not pushed to GitHub.

## Requirements

- Python 3
- Flask

Install Flask if needed:

```powershell
py -3 -m pip install flask
```

## Run Locally

From this project folder:

```powershell
cd "C:\Users\evile\Documents\epn-recipe-box"
py -3 app.py
```

Open the app:

```text
http://127.0.0.1:5000/
```

The first visit redirects to `/signup`.

## How To Use

1. Create an account with your email and password.
2. Set up your profile nickname, about text, and optional avatar.
3. Add ingredients to your food stock inventory.
4. Create recipe cards with ingredients and steps.
5. Open recipe cards to rate, comment, copy the share link, or edit your own recipes.
6. Use the inventory page to see recipe suggestions based on what you have available.

## Database Notes

The app automatically creates `recipe_box.db` when it starts. The database includes tables for:

- users
- recipes
- inventory items
- ratings
- comments

To reset the app locally, stop the server and delete `recipe_box.db`. The next run will create a fresh empty database.

## GitHub

Repository:

```text
https://github.com/evileddy60/epn-recipe-box
```
