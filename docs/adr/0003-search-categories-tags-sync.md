# ADR-0003: Additive recipe categories and relational tags in peer sync

## Status

Accepted

## Date

2026-08-05

## Context

Recipe Box needs searchable categories and user-managed tags while remaining a small server-rendered Flask/SQLite application. Recipe cards are exchanged through the existing manual peer protocol, which uses deterministic canonical payloads, checksums, baselines, and explicit conflict resolution. Existing databases and older peers must continue to work.

A comma-separated tag field would make filtering, normalization, uniqueness, and synchronization unreliable. A new search service would add operational cost without a demonstrated scale requirement. Hard-coded category conditionals would make category values difficult to extend.

## Decision

Use additive SQLite structures:

- `recipes.category_key` stores one stable category key or an empty value for Uncategorized.
- `categories` maps stable keys to friendly labels and is seeded with the initial category set.
- `tags` stores one normalized unique name and its human-readable display name.
- `recipe_tags` provides the many-to-many relationship with composite primary key and cascading foreign keys.

Use a parameterized SQL query for recipe filtering. Search text is matched case-insensitively across recipe text, ingredients, category, tags, and creator name. Category and one-tag filters are separate predicates.

Extend the synchronization payload additively with `category` and sorted canonical `tags`. Missing fields from older peers default to empty values. Tag order is not semantically meaningful and is sorted before checksums. Category/tag validation is applied to all remote input before persistence.

## Alternatives considered

### Store tags as a comma-separated recipe column

Rejected because it cannot enforce case-insensitive uniqueness or relational filtering safely and makes synchronization order/normalization ambiguous.

### Introduce a search service or full-text subsystem

Rejected because the application is small, SQLite is already authoritative, and the requested filters can be implemented with bounded parameterized SQL without new infrastructure.

### Hard-code categories in route/template conditionals

Rejected because stable values and labels should be data-driven and extensible.

### Automatically classify existing recipes

Rejected because silently guessing categories changes user data. Existing recipes remain Uncategorized.

## Consequences

- Existing databases migrate additively and retain all existing recipe content.
- Category labels can be extended through the categories table without changing route logic.
- Tags are normalized and deduplicated centrally, with a stable relational representation.
- New peers synchronize category and tags while older peers remain readable through safe defaults.
- Checksums now detect category/tag changes, so those changes can create legitimate conflicts.
- SQLite query complexity increases modestly, but no new service or dependency is required.
- No deletion propagation is added.
