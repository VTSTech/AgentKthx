# Codebase Intelligence Brief: {PROJECT_NAME}

> Generated: {TIMESTAMP} | Auditor: {AGENT_NAME} | Commit: {GIT_HASH}

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | One sentence — what does this project do? |
| **Tech Stack** | Language, framework, runtime, key libraries |
| **Entry Point** | Main file or handler that starts execution |
| **Build/Run** | How to build and run the project (dev + prod) |
| **Test Command** | How to run tests |

---

## Architecture Map

Directory → role. Only include directories that matter. One line each.

```
src/           → Application source code
src/routes/    → API route definitions
src/models/    → Data models and schemas
src/middleware/ → Request processing pipeline
tests/         → Test suites
config/        → Configuration files
```

### Skip List

Directories and files that are safe to ignore entirely:

- `node_modules/`, `vendor/`, `dist/`, `build/`, `.git/`
- Generated code, compiled output
- Static assets unless relevant to functionality

---

## Critical Files Index

The most important files in the codebase. These are the files a developer will
need to touch for almost any meaningful change. Include file path, purpose,
and why it matters.

| File | Purpose | Why It Matters |
|------|---------|----------------|
| `src/server.ts` | Express server setup | All routes mount here; middleware chain defined here |
| `src/db/client.ts` | Database connection | Single source of truth for DB access; connection pooling |
| `prisma/schema.prisma` | Data model definitions | Changing anything in the DB requires updating this first |

**Rule of thumb**: If a file appears in 3+ dependency chains, it belongs here.

---

## Request / Execution Lifecycle

How data flows through the system. Trace the path from input to output.
For non-server projects, describe the main execution flow instead.

```
1. Request arrives → middleware.ts (auth, logging, CORS)
2. Route handler → routes/api/users.ts
3. Validation → validators/userValidator.ts
4. Business logic → services/userService.ts
5. Data access → repositories/userRepository.ts
6. Database → Prisma ORM → PostgreSQL
7. Response → formatted and returned
```

---

## Dependency Graph

Key relationships between modules. What depends on what? Where are the
coupling points? This helps the next agent understand blast radius of changes.

```
routes/ → services/ → repositories/ → prisma/
middleware/auth.ts → services/sessionService.ts
config/ ← referenced by everything
```

---

## Patterns & Conventions

What patterns does this codebase use? Document the expected way of doing things
so the next agent writes code that fits in.

| Aspect | Pattern |
|--------|---------|
| Error handling | Custom error classes thrown in services, caught by global handler |
| State management | React Context + useReducer for client state |
| API responses | `{ success: boolean, data: T, error?: string }` envelope |
| Naming | camelCase for variables, PascalCase for types, kebab-case for files |
| File organization | Co-locate tests with source files |

---

## Known Landmines

Things that will trip up an unwary developer. These are the non-obvious pitfalls
discovered during the audit.

- **`config/env.ts`** — loads from `.env` at import time; must be imported before
  anything that needs env vars, but importing it in test files breaks CI
- **`src/utils/cache.ts`** — has a memory leak: entries are pushed but never evicted;
  will OOM on long-running processes
- **Prisma middleware** — soft-deletes are implemented via a Prisma extension, not
  standard middleware; standard `delete()` calls will actually delete
- **Circular dependency** — `auth.ts` ↔ `userService.ts` — works because of hoisting
  but will break if either is refactored to use top-level `const`

---

## Active Decisions

Architectural or design choices that were made (and why). These are decisions a
future developer should understand before proposing alternatives.

| Decision | Choice | Rationale |
|----------|--------|-----------|
| ORM | Prisma over TypeORM | Type-safe queries, better DX, team experience |
| Auth | JWT (not sessions) | Stateless, horizontal scaling, SPA client |
| State | Local state over Redux | App is small enough that Redux adds complexity without benefit |

---

## What's Missing / Incomplete

Unfinished work, TODO comments, known gaps. Honest assessment of what's not done.

- Auth refresh token rotation is planned but not implemented
- No rate limiting on public endpoints
- Test coverage is ~40%, concentrated on services, no integration tests
- `docs/` directory is empty — no API documentation

---

## Quick Start for Developer

The minimum a new agent/developer needs to do to be productive:

1. Read the Critical Files Index above — start with files marked "Why It Matters"
2. Understand the Request Lifecycle — this is how the system works
3. Check Known Landmines — don't step on these
4. Follow Patterns & Conventions — write code that fits
5. If changing a critical file, check the Dependency Graph for blast radius

Do NOT start by reading every file. Use this brief as your map and read only
what you need for your specific task.
