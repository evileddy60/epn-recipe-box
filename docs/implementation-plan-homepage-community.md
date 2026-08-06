# Implementation plan: Homepage community improvements

## Milestone 1 — Contracts and data read model

1. Add homepage rendering tests for anonymous and authenticated views, statistics, empty states, community sections, navigation labels, and authentication priority.
2. Add a read-only `community_home_data` database helper. Keep all lists bounded and use aggregate SQL for ratings, comments, categories, tags, and activity.
3. Preserve the existing `/recipes`-style behavior behind `/` for authenticated users where practical; do not alter route URLs or database schema.

## Milestone 2 — Server-rendered interface

1. Render `/` for anonymous visitors instead of redirecting to `/signup`.
2. Pass the community payload to the homepage and keep the existing authenticated recipe library available below the community summary.
3. Update the shared navigation to use `Recipes`, `Activity`, `Inventory`, `Sync`, and a disabled Collections placeholder.
4. Make `+ New Recipe` the authenticated primary action and keep Sign In visually primary in the anonymous auth panel.

## Milestone 3 — Presentation and accessibility

1. Add community summary/list markup using the existing cookbook palette and card language.
2. Add responsive styles for statistics and community sections without introducing a JavaScript framework.
3. Use semantic headings, status empty states, link labels, and existing visible focus treatment.

## Milestone 4 — Verification

1. Run focused and full built-in test suites, compilation, Ruff format/check, Bandit, pip-audit, and `git diff --check`.
2. Start an isolated temporary Gunicorn instance with a temporary database/data directory.
3. Exercise anonymous homepage, sign-in, sign-up, authenticated homepage/navigation, desktop and narrow browser layouts, keyboard focus, console/network/CSP/asset checks.
4. Stop the temporary process and confirm the repository is clean except for the intended commit, with no deployment or push.

## Rollback

The change is additive and does not migrate the database. Rollback is `git revert` of the local commit (or deletion of the branch). Existing route paths and tables remain intact.

## Expected files

- `recipe_box/db.py`
- `recipe_box/routes.py`
- `recipe_box/templates/base.html`
- `recipe_box/templates/index.html`
- `recipe_box/templates/signup.html`
- `static/style.css`
- `tests/test_application.py` or a focused homepage test module
- `docs/SPEC-homepage-community.md`
- `docs/implementation-plan-homepage-community.md`
- `docs/adr/0006-community-homepage.md`
