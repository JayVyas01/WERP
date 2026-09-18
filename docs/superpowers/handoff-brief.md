# WERP Phase 1 — Handoff Brief for New Session

## Your job

Execute the WERP Phase 1 implementation plan using the `superpowers:subagent-driven-development` skill. Follow the plan exactly — do not improve, refactor, or add features beyond what is written. Commit after every task.

Read these two files before starting:
- **Plan:** `docs/superpowers/plans/2026-09-18-werp-phase1-implementation.md`
- **Spec:** `docs/superpowers/specs/2026-09-18-werp-architecture-design.md`

---

## What this project is

WERP is a WhatsApp-first GST invoice management system for Indian small businesses. A shopkeeper photographs a GST invoice, sends it on WhatsApp, and WERP extracts the data, tracks inventory, and answers business questions — no app install required.

**Phase 1 scope (what you are building):**
- Shared Python library (`shared/`) with Pydantic schemas and service contracts
- AI service (`services/ai/`) — invoice extraction, intent classification, NL query
- Backend service (`services/backend/`) — WhatsApp webhook, database, pipelines

---

## What already exists in the repo

```
src/schemas/invoice.py       — rich Pydantic Invoice schema (Task 2 moves this to shared/)
src/extraction/prompt.py     — Claude extraction prompt and validation helpers
migrations/001_core_schema.sql — PostgreSQL schema with RLS + pgcrypto
docs/superpowers/specs/      — architecture design document
```

Do not delete `src/` until Task 2 explicitly moves it. After Task 2 is committed, `src/` is no longer needed.

---

## Key decisions — do not change these

These were made deliberately. If something looks wrong, check the spec before changing it.

| Decision | What | Why |
|---|---|---|
| AI model | `claude-haiku-4-5-20251001` | Cost — ~₹0.40/invoice vs ₹8,000/month on Sonnet |
| Service communication | Sync HTTP/REST | MVP simplicity — async queue added at scale |
| Templates | `TEMPLATES_ENABLED=false` in Phase 1 | Not enough data yet — collect first, build later |
| Training logs | Always write to `extraction_logs` | Needed for future model fine-tuning |
| Storage | S3-compatible (MinIO locally, Supabase in prod) | Same boto3 code, only `.env` changes |
| DB | PostgreSQL with RLS | Every query scoped to `business_id` — security requirement |
| OCR | Tesseract always runs first | Free, data stays on-premise, feeds template detection |
| Intent classification | Rules first, Haiku fallback | 85-90% of messages handled free |
| NL queries | Menu-driven first, Haiku SQL fallback | Free for standard queries, AI for freetext |
| Timeouts | Configurable via env vars (Option B) | Tune in prod without code changes |

---

## Tech stack

- Python 3.12, FastAPI, SQLAlchemy 2.0 async, Pydantic v2
- `anthropic` SDK, `pytesseract`, `Pillow`, `httpx`, `pydantic-settings`
- `redis[asyncio]`, `asyncpg`, `boto3`
- Tests: `pytest`, `pytest-asyncio`, `respx`, `testcontainers[postgres]`

---

## Environment setup

```bash
# Copy env template
cp .env.example .env

# Start local infrastructure (PostgreSQL + Redis + MinIO)
docker compose up -d

# Install shared library
cd shared && pip install -e .

# Install AI service
cd services/ai && pip install -e ".[dev]"

# Install backend service
cd services/backend && pip install -e ".[dev]"
```

System package required for OCR:
```bash
# macOS
brew install tesseract

# Ubuntu/Debian
apt install tesseract-ocr tesseract-ocr-eng
```

---

## How to run the services

```bash
# AI service (port 8001)
cd services/ai && uvicorn src.ai_service.main:app --reload --port 8001

# Backend service (port 8000)
cd services/backend && uvicorn src.backend.main:app --reload --port 8000
```

---

## Test commands

```bash
make test               # unit tests only (fast, no external deps)
make test-integration   # + real PostgreSQL via testcontainers
make test-ai            # + real Claude API (costs money, needs ANTHROPIC_API_KEY)
```

---

## Rules for execution

1. **Follow the plan exactly.** The code in the plan is what to write — do not improve it.
2. **TDD strictly.** Write the failing test first, run it to confirm it fails, then implement.
3. **Commit after every task.** The plan has a commit step — do not skip it.
4. **Environment errors ≠ code errors.** If a test fails because Tesseract is not installed, fix the environment. Do not change the code to work around missing system packages.
5. **Do not upgrade dependencies.** Use the exact versions in `pyproject.toml`.
6. **Do not add comments** unless the plan explicitly includes them.
7. **If a task's test passes before you write the implementation**, the test is wrong — fix the test first.
8. **`ANTHROPIC_API_KEY` is needed for tasks 5-6** (AI service endpoints). Integration tests mock the API — only `tests/ai/` tests need real keys.

---

## What to do if something breaks

- **Test fails unexpectedly:** Read the error carefully. Check if it's an import issue (wrong path), environment issue (missing package), or logic issue (wrong code). Fix the root cause.
- **Import errors on `werp_shared`:** Run `pip install -e shared/` from the repo root.
- **Database errors in integration tests:** testcontainers starts its own PostgreSQL — no manual setup needed. If it hangs, check Docker is running.
- **AI service returns 422:** The request body doesn't match the Pydantic model — check field names match `ExtractionRequest` in `shared/werp_shared/contracts.py`.

---

## Git branch

Work on the `dev` branch. Do not merge to `main`.

```bash
git checkout dev
```

---

## When you are done

After Task 11 passes (smoke test — both services healthy, all tests green), report:
1. Which tasks completed successfully
2. Any tasks that had to deviate from the plan (what and why)
3. Final `pytest` output summary
4. Any issues left open for the next session
