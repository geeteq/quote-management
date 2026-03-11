# PLAN.md — Quote Management System v2

**Date:** 2026-03-10
**Status:** Data model complete, migrations applied. Use this file to plan every feature before touching code.

---

## Current State

### What's done
- Full normalized schema: `tenants → projects → (quotes + base_configs) → (line_items + base_config_components) → (learned_components + defined_components)`
- All M01–M12 migrations applied; `learned_components`, `defined_components`, `base_config_components` live
- Admin: tenant CRUD, project CRUD, quote list (delete/archive), learned components view, entered components view
- Frontend: hierarchy nav, quote cards (expiry, archive, compare, PO comments), upload modal, base config modal (create/delete)
- API: all core CRUD endpoints operational

### Known gaps (P0)
- No authentication / session management
- No CSRF protection
- No rate limiting on upload

---

## Feature Backlog (Prioritized)

### P1 — Core Business Value

#### F1: Base Config → Quote Matching
**What:** Show which quotes "satisfy" a base config's component list — i.e., the quote contains every component type in the config.

**Data model:** No schema change needed. All data exists.

**API contract:**
```
GET /api/configs/<config_id>/match?quotes=<comma-separated-quote-ids>
Response: {
  config_id: int,
  matches: [
    {
      quote_id: int,
      quote_ref: str,
      matched_components: [ {component_type, part_number, found_in_quote: bool} ],
      coverage_pct: float  // 0-100
    }
  ]
}
```

**Alternate — attach to project view:**
```
GET /api/projects/<project_id>/config-match
Response: { configs: [ { ...config, quotes: [ { ...quote, coverage_pct } ] } ] }
```

**UI:** On each config card in the project view, show a "Coverage" badge per quote card (green = 100%, yellow = partial, red = 0%).

**Logic:**
1. For each config, get its `defined_components` component_types.
2. For each quote in same project, get distinct `category` values from `line_items`.
3. Compare: coverage = matched_types / total_types * 100.
4. Surface in `renderProjectView()`.

**Dependencies:** None beyond existing data.

---

#### F2: Quote Reassignment (Edit Metadata Post-Upload)
**What:** Allow user to change a quote's tenant, project, ICA, and PO comments after upload. Also allow un-archiving.

**API contract:**
```
PATCH /api/quotes/<id>
Body: { tenant_id, project_id, ica, po_comments }
Response: { success: true }
```

**UI:** On quote card, add an "Edit" button (pencil icon) that opens a modal with:
- Tenant dropdown (loads from `/api/admin/tenants`)
- Project dropdown (cascading, loads from `/api/tenants/<id>/projects`)
- ICA text field
- PO Comments textarea (max 255)
- Save / Cancel

**Validation:** project must belong to selected tenant.

**Dependencies:** None.

---

#### F3: Config Edit (Add/Remove Components)
**What:** After a config is created, allow editing: rename it, add components, remove specific component rows.

**API contract:**
```
PATCH /api/configs/<config_id>
Body: { config_name?, add_components: [...], remove_component_ids: [...] }
Response: { success: true }
```

**UI:** On config card, add "Edit" button. Reuses the existing Add Config modal with pre-populated fields.

**Logic:**
- Add: insert into `defined_components`, link via `base_config_components`
- Remove: delete from `base_config_components` (NOT from `defined_components` — they may be reused)
- Rename: UPDATE `base_configs`

**Dependencies:** F2 modal pattern is a good template.

---

### P2 — Admin Completeness

#### F4: Vendor & Manufacturer Admin
**What:** CRUD pages for `vendors` and `manufacturers` tables. Currently both are seed-only.

**API contracts:**
```
GET    /api/vendors                    → list
POST   /api/vendors                    → create {name, code}
PATCH  /api/vendors/<id>               → update
DELETE /api/vendors/<id>               → delete (guard: check for FK references in quotes)

GET    /api/manufacturers              → list (already exists)
POST   /api/manufacturers              → create {name}
PATCH  /api/manufacturers/<id>         → update
DELETE /api/manufacturers/<id>         → delete (guard: check defined_components)
```

**UI:** Two new sidebar items under "Components" in admin dashboard:
- Vendors — simple table with inline edit + add row
- Manufacturers — same pattern

**Dependencies:** None.

---

#### F5: Quote Status Toggle (Restore Archived)
**What:** Admin can un-archive a quote (restore to 'active').

**API contract:**
```
POST /api/admin/quotes/<id>/restore
Response: { success: true }
```

**UI:** In admin quotes list, add "Restore" button in the Status column for archived rows.

**Dependencies:** None.

---

#### F6: Project Archive & Restore
**What:** Archive a project from admin (moves it out of nav, hides its quotes from frontend). Mirror of tenant archive.

**API contract:**
```
POST /api/admin/projects/<id>/archive
POST /api/admin/projects/<id>/restore
```

**UI:** Archive button on project row in admin projects list. Separate "Archived Projects" view (like archived tenants).

**Dependencies:** None.

---

### P3 — UX Improvements

#### F7: Global Search
**What:** Single search box that queries across tenants, projects, quotes (by quote_id, vendor, ICA, description, PO comments).

**API contract:**
```
GET /api/search?q=<term>&limit=20
Response: {
  tenants: [ {id, name} ],
  projects: [ {id, name, tenant_name} ],
  quotes: [ {id, quote_id, vendor, description, tenant_name, project_name} ]
}
```

**UI:** Search box in the top nav bar of `index_new.html`. Results appear in a dropdown panel. Clicking a result navigates to the relevant tenant/project/quote.

**Dependencies:** None.

---

#### F8: CSV Export
**What:** Export quote line items to CSV for import into procurement systems.

**API contract:**
```
GET /api/quotes/<id>/export/csv
GET /api/projects/<id>/quotes/export/csv
Response: text/csv attachment
```

**UI:** "Export CSV" button on quote card header and project section toolbar.

**Dependencies:** None (stdlib `csv` module).

---

### P4 — Security (P0 gaps)

#### F9: Authentication
**What:** Simple session-based login gate. Single shared password (env var) is acceptable for v2.

**Implementation:**
- `SECRET_KEY` env var for Flask session signing
- `AUTH_PASSWORD` env var (bcrypt-hashed)
- `@login_required` decorator on all non-static routes
- `GET/POST /login`, `POST /logout`

**UI:** Minimal login form; redirect to `?next=` after login.

**Dependencies:** `flask-login` or roll with Flask sessions directly.

---

#### F10: CSRF Protection
**What:** CSRF token on all state-mutating requests.

**Implementation:**
- `flask-wtf` CSRFProtect or manual double-submit cookie
- Token injected via `<meta name="csrf-token">` in base template
- JS reads it and adds `X-CSRFToken` header to all `fetch()` POST/PATCH/DELETE calls

**Dependencies:** F9 (session must exist first).

---

## Dependency Order

```
F4 (vendors/manufacturers) — standalone, no deps
F5 (quote restore)         — standalone
F6 (project archive)       — standalone
F2 (quote edit)            — standalone; F4 useful for vendor dropdown
F3 (config edit)           — standalone; reuses F2 modal pattern
F1 (config matching)       — requires F3 to be useful (configs need components)
F7 (global search)         — standalone
F8 (CSV export)            — standalone
F9 (auth)                  — do before prod deploy
F10 (CSRF)                 — requires F9
```

---

## Pre-Build Checklist (Use Before Every Feature)

Before writing any code for a feature:

1. **Data model:** Does the schema support it? If not, write the migration block first (M13+).
2. **API contract:** Define endpoint, method, request body, and response shape in this file.
3. **UI wireframe:** Write the DOM structure as a comment or ASCII art. Name every CSS class.
4. **New CSS classes:** List them. Check `style.css` for conflicts before adding.
5. **JS functions:** Name every new function. Check `index_new.html` for naming conflicts.
6. **Error states:** Define loading, empty, and error HTML for every fetch.
7. **Open questions:** List unknowns here before starting. Resolve them first.

---

## Open Questions

- **F1 matching logic:** Should matching be by `component_type` (coarse) or by `part_number` (exact)? Coarse is more useful since vendors use different part numbers.
- **F2 tenant cascade:** When reassigning a quote to a different tenant, should it auto-clear the project (since the old project won't belong to the new tenant)?
- **F9 scope:** Single shared password, or per-user accounts? Per-user requires a `users` table and M13 migration.
- **Export format:** Does the procurement team need a specific CSV column order/naming convention?

---

## Migration Log

| ID  | Description                              | Status   |
|-----|------------------------------------------|----------|
| M01 | projects.status CHECK add 'archived'     | Applied  |
| M02 | tenants.status CHECK add 'archived'      | Applied  |
| M03 | quotes.po_comments column                | Applied  |
| M04 | manufacturers table + seed               | Applied  |
| M05 | vendors table + seed                     | Applied  |
| M06 | quotes.vendor_id FK + populate           | Applied  |
| M07 | components RENAME TO learned_components  | Applied  |
| M08 | learned_components.manufacturer_id FK    | Applied  |
| M09 | base_configs table                       | Applied  |
| M10 | defined_components table                 | Applied  |
| M11 | base_config_components junction          | Applied  |
| M12 | DROP entered_components                  | Applied  |
| M13 | (next migration goes here)               | Pending  |
