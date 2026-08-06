# Specification: Community Homepage

## Goal

Make `/` answer “What has the Evil People Network been cooking lately?” before asking visitors to create an account. The existing private-beta authentication and recipe-box workflows remain available.

## Behavior

- Anonymous visitors receive a community overview plus compact Sign In and Create Account forms.
- Sign In is the primary authentication action; Create Account is secondary.
- Authenticated, profile-ready users receive the same community overview with a prominent `+ New Recipe` action and access to their existing recipe library.
- Community content is limited to data already visible in the private-beta model: recipes shared with `shared_epn`, users, shared collections, comments on shared recipes, activity events, categories, and tags.
- Empty sections use explicit, encouraging status messages and never fabricate content.
- `Cards` is renamed to `Recipes` in navigation and page-facing labels while existing route paths and endpoint names remain compatible.
- Collections is represented in navigation as a disabled “Collections (coming soon)” entry because no browser collection route exists yet.

## Data contract

The homepage receives one community payload containing summary statistics and bounded lists for recent shared recipes, highest-rated shared recipes, newest shared recipes, active members, popular categories, and trending tags. Lists are produced with bounded aggregate SQL queries and do not perform per-item lookups.

## Non-goals

- No public visibility is introduced.
- No collection management UI or new API is added.
- No changes to authentication semantics, recipe permissions, synchronization, or live deployment.
- No invented sample content.

## Accessibility and performance

The page uses semantic headings, native links/forms/buttons, visible focus styles already present in the application, status-role empty states, and responsive grids. Community data is loaded in a single database connection with bounded queries and aggregate joins; templates do not query the database.
