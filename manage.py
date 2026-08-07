"""Administrative command-line tools for EPN Recipe Box."""

from __future__ import annotations

import argparse
import getpass
import sys

import app as recipe_app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python manage.py", description="EPN Recipe Box administrative tools")
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser("reset-password", help="reset an existing user's password")
    reset.add_argument("email", help="email address of the account to update")
    return parser


def reset_password(email: str) -> int:
    new_password = getpass.getpass("New password: ")
    confirmation = getpass.getpass("Confirm new password: ")
    if new_password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2

    try:
        recipe_app.init_db()
        updated = recipe_app.reset_account_password(email, new_password)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    if not updated:
        print("No user found for that email.", file=sys.stderr)
        return 1
    print(f"Password reset for {email.strip().lower()}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "reset-password":
        return reset_password(args.email)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
