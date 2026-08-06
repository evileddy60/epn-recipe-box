# ADR 0006: Community-focused homepage

- Status: Accepted
- Date: 2026-08-06

## Context

The current homepage is a private recipe-card library and redirects anonymous visitors to account creation. The application now has shared recipes, collections, comments, tags, and activity data, so the first screen should communicate the community value while preserving the private-beta authentication model.

## Decision

Render a community dashboard at `/` for both anonymous visitors and profile-ready members. Build its view model with bounded aggregate SQL in the existing SQLite data layer. Show only recipes with `visibility = 'shared_epn'`; keep authentication forms on the page for anonymous visitors, with Sign In primary and Create Account secondary. Rename the navigation label `Cards` to `Recipes` without changing compatible URL paths or Flask endpoint names. Add a disabled Collections navigation item until a browser collection route exists.

The authenticated homepage retains the existing recipe library/filter area after the community overview and emphasizes `+ New Recipe` using the existing accent action style.

## Alternatives considered

### Keep redirecting anonymous visitors to signup

Rejected: it makes account creation the product's first message and hides community content from visitors.

### Add a client-side dashboard/API

Rejected: this app is server-rendered and does not need JavaScript or an additional request layer for bounded homepage summaries.

### Show all recipes publicly

Rejected: private-beta policy explicitly permits owner-only or `shared_epn` visibility, not public visibility.

### Add full collection management now

Rejected: it expands scope beyond the homepage. A disabled placeholder makes the intended information architecture visible without claiming unsupported functionality.

## Consequences

- Anonymous requests now perform bounded read queries instead of redirecting.
- Community statistics and sections accurately show empty states on a new installation.
- The homepage has more server-rendered content, but avoids N+1 queries by using one read connection and aggregate SQL.
- Existing route URLs, forms, database schema, authentication, and synchronization behavior remain unchanged.
- Collections remains a documented navigation placeholder until its browser workflow is implemented.
