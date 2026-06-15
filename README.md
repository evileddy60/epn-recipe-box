# EPN Recipe Box

EPN Recipe Box is a small Flask web app for creating, sharing, rating, and commenting on recipes. It is designed mobile-first and styled like a box of handwritten recipe cards.

## Features

- Email and password signup with securely hashed passwords
- Profile setup with nickname, about text, and avatar upload
- Create, edit, and share recipe cards
- Rate recipes from 1 to 5 stars
- Comment on recipes
- Keep a food stock inventory
- Generate recipe ideas from stocked ingredients
- Suggest recipes that need one extra ingredient
- Local SQLite database storage

## Project Files

- `app.py` - the Flask application, routes, templates, styling, and SQLite setup
- `requirements.txt` - Python dependencies for local/Pi deployment
- `static/recipe-box.png` - header image used by the app
- `deploy/epn-recipe-box.service` - sample Raspberry Pi systemd service
- `.env.example` - sample environment settings
- `data/recipe_box.db` - local SQLite database created when the app runs
- `data/uploads/` - local avatar uploads

`data/`, `.env`, `.venv`, SQLite databases, and uploaded avatars are ignored by Git so personal/local user data is not pushed to GitHub.

## Requirements

- Python 3
- Flask
- Gunicorn for production/Pi hosting

Install dependencies:

```powershell
py -3 -m pip install -r requirements.txt
```

## Run Locally On Windows

From this project folder:

```powershell
cd "C:\Users\USERNAME\Documents\epn-recipe-box"
py -3 app.py
```

Open the app:

```text
http://127.0.0.1:5000/
```

The first visit redirects to `/signup`.

## Run On A Raspberry Pi 4

Install system packages:

```bash
sudo apt update
sudo apt install python3 python3-venv git
```

Clone the repo:

```bash
cd /home/pi
git clone https://github.com/evileddy60/epn-recipe-box.git
cd epn-recipe-box
```

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create the data folder:

```bash
mkdir -p data/uploads
```

Create an environment file:

```bash
sudo cp .env.example /etc/epn-recipe-box.env
sudo nano /etc/epn-recipe-box.env
```

Set a long random `SECRET_KEY`. You can generate one with:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Run manually with Gunicorn:

```bash
source .venv/bin/activate
gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
```

Open from another device on your network or Tailscale:

```text
http://PI_IP_ADDRESS:5000/
```

## Start On Boot With systemd

Copy the service file:

```bash
sudo cp deploy/epn-recipe-box.service /etc/systemd/system/epn-recipe-box.service
sudo systemctl daemon-reload
sudo systemctl enable epn-recipe-box
sudo systemctl start epn-recipe-box
```

Check status and logs:

```bash
sudo systemctl status epn-recipe-box
journalctl -u epn-recipe-box -f
```

Restart after pulling updates:

```bash
cd /home/pi/epn-recipe-box
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart epn-recipe-box
```

## Tailscale Private Access

Install and connect Tailscale on the Pi and on each invited device. Once connected, users can visit:

```text
http://PI_TAILSCALE_IP:5000/
```

With MagicDNS enabled, this may also work:

```text
http://raspberrypi:5000/
```

This keeps the app private to your Tailscale network instead of exposing it publicly.

## How To Use

1. Create an account with your email and password.
2. Set up your profile nickname, about text, and optional avatar.
3. Add ingredients to your food stock inventory.
4. Review generated recipe ideas from stock and one-extra-ingredient suggestions.
5. Save generated ideas as recipe cards when you like them.
6. Create recipe cards with ingredients and steps.
7. Open recipe cards to rate, comment, copy the share link, or edit your own recipes.

## Database And Uploads

The app automatically creates `data/recipe_box.db` when it starts. The database includes tables for:

- users
- recipes
- inventory items
- ratings
- comments

Avatar files are stored in `data/uploads/`.

To reset the app locally, stop the server and delete `data/recipe_box.db` and, if desired, `data/uploads/`. The next run will create a fresh empty database.

## Backup

Stop the app before backing up to avoid copying the database mid-write:

```bash
sudo systemctl stop epn-recipe-box
mkdir -p ~/epn-recipe-box-backups
cp data/recipe_box.db ~/epn-recipe-box-backups/recipe_box-$(date +%Y-%m-%d).db
tar -czf ~/epn-recipe-box-backups/uploads-$(date +%Y-%m-%d).tar.gz data/uploads
sudo systemctl start epn-recipe-box
```

Restore a backup:

```bash
sudo systemctl stop epn-recipe-box
cp ~/epn-recipe-box-backups/recipe_box-YYYY-MM-DD.db data/recipe_box.db
tar -xzf ~/epn-recipe-box-backups/uploads-YYYY-MM-DD.tar.gz
sudo systemctl start epn-recipe-box
```

## GitHub

Repository:

```text
https://github.com/evileddy60/epn-recipe-box
```
