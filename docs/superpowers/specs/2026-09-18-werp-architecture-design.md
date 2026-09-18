# WERP — Architecture Design
**Date:** 2026-09-18  
**Status:** Approved  
**Scope:** Phase 1 MVP — AI service, Backend service, WhatsApp integration

---

## 1. What We're Building

WERP is a WhatsApp-first GST invoice management system for Indian SMBs. A shopkeeper photographs a GST invoice, sends it on WhatsApp, and WERP extracts the data, tracks inventory, and answers business questions — no app install, no training required.

**Phase 1 delivers:**
- Receive invoice photos via WhatsApp
- Extract GST fields (OCR + Claude Haiku)
- User confirms or edits via WhatsApp buttons
- Inventory auto-updates on confirmation
- Basic query menu (spend, suppliers, GST liability)

---

## 2. Architecture Overview

```
WhatsApp (Meta Cloud API)
        │
        ▼
┌───────────────────┐    HTTP/REST    ┌─────────────────┐
│  Backend Service  │◄───────────────►│   AI Service    │
│  (port 8000)      │                 │   (port 8001)   │
│                   │                 │                 │
│  • Webhook        │                 │  • Extraction   │
│  • Pipeline       │                 │  • Classify     │
│  • DB layer       │                 │  • NL Query     │
│  • Messaging      │                 │                 │
└────────┬──────────┘                 └─────────────────┘
         │
         ▼
┌───────────────────────────────────┐
│  Supabase                         │
│  PostgreSQL (RLS + pgcrypto)      │
│  Storage (invoice images)         │
└───────────────────────────────────┘
         +
┌───────────────────────────────────┐
│  Redis (idempotency + sessions)   │
└───────────────────────────────────┘
```

**Key decisions:**
- **Monorepo** — shared Pydantic schemas, one test run, easy refactoring
- **Sync HTTP/REST** between services for MVP, upgrade to async queue when scale demands
- **Supabase** for PostgreSQL + file storage (replaces AWS RDS + S3)
- **Railway** for hosting both services
- **Vercel** for frontend dashboard (Phase 2 only)

---

## 3. Repository Structure

```
werp/
├── shared/                     # Local pip package, imported by both services
│   └── werp_shared/
│       ├── schemas/            # Invoice, LineItem, Party Pydantic models
│       ├── contracts.py        # Service request/response models
│       ├── validators/         # GSTIN, HSN, tax math validation
│       └── config.py           # Shared BaseSettings
│
├── services/
│   ├── ai/                     # AI Service (port 8001)
│   │   ├── src/ai_service/
│   │   │   ├── main.py
│   │   │   ├── extraction.py   # OCR + Haiku extraction
│   │   │   ├── classify.py     # Intent classification
│   │   │   └── query.py        # NL query → SQL
│   │   └── tests/
│   │
│   └── backend/                # Backend Service (port 8000)
│       ├── src/backend/
│       │   ├── main.py
│       │   ├── webhook/        # WhatsApp webhook handler
│       │   ├── pipeline/       # Invoice, confirm, query flows
│       │   ├── messaging/      # WhatsApp reply sender
│       │   ├── db/             # SQLAlchemy + repositories
│       │   └── services/       # AI client, Storage, OCR
│       └── tests/
│
├── migrations/
│   └── 001_core_schema.sql
├── docker-compose.yml
├── .env.example
└── pyproject.toml
```

`shared/` is installed as `werp-shared @ ../shared` in each service's `pyproject.toml`. Schemas defined once, used everywhere.

---

## 4. AI Service

### Endpoints
| Endpoint | Purpose | Default path |
|---|---|---|
| `POST /extract` | Invoice extraction | Template → Haiku fallback |
| `POST /classify` | Intent classification | Rules → Haiku fallback |
| `POST /query` | NL query → SQL | Menu match → Haiku fallback |
| `GET /health` | Health check | — |

### Extraction pipeline
**Phase 1 (now):** Every invoice goes straight to Claude Haiku. Templates not active yet.

```
Image arrives
     │
     ▼
Tesseract OCR (always, free)
     │
     ▼
Detect software marker (log for future templates)
     │
     ▼
Claude Haiku extraction (always in Phase 1)
     │
     ▼
Pydantic validation + tax math check
     │
     ▼
Return ExtractionResult + confidence score
```

**Phase 2 (after ~500 confirmed invoices):** Activate template extractor via `TEMPLATES_ENABLED=true`. Templates run first; Haiku only for low-confidence or unknown formats.

**Why this order:** Production data tells you which templates to build first and validates them before activation. No wasted effort.

**Training data logging:** Every confirmed extraction saved to `extraction_logs` table. Powers future fine-tuning of a small open-source model (Qwen2.5-1.5B) to eventually replace Haiku calls.

### Intent classification (hybrid)
```
Rule-based first → confidence ≥ 0.85? → return (free)
                 → confidence < 0.85? → Claude Haiku (handles Hindi/Gujarati/Hinglish/Gujlish)
```

Rules cover: image attachments → UPLOAD, known keywords (CONFIRM/YES/NO) → COMMAND, menu numbers (1-5) → QUERY. Haiku handles mixed languages and ambiguous phrasing.

### NL Query (hybrid)
```
Menu match → known query type? → run pre-written SQL (free)
           → freetext?         → Claude Haiku generates parameterized SQL
```

Pre-written queries: monthly spend, top suppliers, GST liability, inventory status, invoice history. SQL safety: SELECT-only, `business_id` required, dangerous keywords blocked, results capped at 100 rows.

---

## 5. Backend Service

### Endpoints
| Endpoint | Purpose |
|---|---|
| `GET /webhook/whatsapp` | Meta webhook verification |
| `POST /webhook/whatsapp` | Incoming WhatsApp messages |
| `GET /health` | Health check (checks DB + Redis + AI service) |

### Invoice upload flow
```
1. Verify HMAC-SHA256 signature (reject if invalid)
2. Check message_id in Redis (skip if already processed)
3. Look up or create business from phone_hash
4. Reply immediately: "✓ Invoice received. Processing..."
5. Download image from Meta CDN
6. Upload to Supabase Storage
7. Run Tesseract OCR
8. POST /extract → AI service
9. Check for duplicate invoice
10. Save to DB (status=pending)
11. Send confirmation with CONFIRM / EDIT / SKIP buttons
```

Step 4 happens before extraction — WhatsApp requires a response within 5 seconds or it retries.

### Confirmation flow
```
User replies CONFIRM
→ Update invoice status → confirmed
→ Upsert inventory (line items → inventory table)
→ Upsert party record (deduplicate seller by GSTIN)
→ Write audit_log
→ Reply: "✓ Saved. Inventory updated."
```

### Query flow
```
User sends message
→ POST /classify → AI service → QUERY intent
→ Run menu SQL (or AI-generated SQL for freetext)
→ Format + send WhatsApp reply
```

### Security
- HMAC-SHA256 signature verification on every webhook request
- Idempotency: `message_id` checked in Redis (24hr TTL) before any processing
- RLS: every DB query scoped to `business_id` via PostgreSQL row-level security
- Phone numbers stored as SHA-256 hashes, never in plaintext

---

## 6. Service Communication

### Contracts (shared Pydantic models)

```python
# Extraction
ExtractionRequest  → { message_id, business_id, ocr_text, image_b64?, software_hint? }
ExtractionResult   → { invoice, confidence, method, template_name?, flagged_fields[] }
ExtractionError    → { code, message, partial_data? }

# Classification
ClassifyRequest    → { message_id, message_text, has_image, has_document }
ClassifyResult     → { intent, command?, query_type?, confidence, method, language? }

# Query
QueryRequest       → { message_id, business_id, message_text, language }
QueryResult        → { sql_template, params[], query_type, method, response_prefix, is_safe }
```

### Timeouts (configurable via env vars)
| Endpoint | Timeout | Env var |
|---|---|---|
| `/extract` | 30s | `AI_EXTRACT_TIMEOUT` |
| `/classify` | 10s | `AI_CLASSIFY_TIMEOUT` |
| `/query` | 15s | `AI_QUERY_TIMEOUT` |

### Error handling
`AI_UNAVAILABLE` errors retry once with a 2s pause. Business errors (`NOT_AN_INVOICE`, `LOW_CONFIDENCE`) are not retried — each maps to a specific WhatsApp reply to the user.

---

## 7. Data Layer

**Supabase** provides PostgreSQL 16 + file storage. Schema already defined in `migrations/001_core_schema.sql`.

Key tables: `businesses`, `invoices`, `line_items`, `hsn_summary`, `parties`, `inventory`, `audit_log`, `extraction_logs` (new — for training data).

**RLS:** Every table scoped to `business_id` via `app.current_business_id` session variable. App sets this on every connection before querying.

**Repositories pattern:** Business logic never writes raw SQL. Each table has a repository class with typed methods (`InvoiceRepository.confirm()`, `InventoryRepository.upsert_from_line_items()`).

---

## 8. Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12 |
| Framework | FastAPI + Uvicorn |
| Database | Supabase (PostgreSQL 16 + pgcrypto + RLS) |
| File storage | Supabase Storage (S3-compatible) |
| Cache | Redis (Upstash free tier in prod) |
| OCR | Tesseract (self-hosted) |
| AI | Claude Haiku (Anthropic API) |
| WhatsApp | Meta Cloud API |
| ORM | SQLAlchemy 2.0 (async) |
| Validation | Pydantic v2 |
| HTTP client | httpx (async) |
| Hosting | Railway (backend + AI service) |
| Local dev | Docker Compose (PostgreSQL + Redis + MinIO) |

---

## 9. Testing Strategy

Three layers, run independently:

```
pytest                     # unit tests only (fast, no external deps, always run)
pytest --integration       # + real PostgreSQL via testcontainers
pytest --run-ai            # + real Claude API (costs money, run before releases)
```

| Layer | What's tested | External deps |
|---|---|---|
| Unit | Logic, regex, validators, formatters, error handling | None (all mocked) |
| Integration | DB writes, RLS enforcement, webhook endpoint, full pipeline | Real PostgreSQL (testcontainers) |
| AI | Extraction accuracy on real invoices, Hindi/Gujarati classification | Real Claude API |

**Key test files:**
- `test_signature_verifier.py` — HMAC verification rejects tampered requests
- `test_idempotency.py` — duplicate WhatsApp messages processed only once
- `test_template_*.py` — regex patterns extract correct fields
- `test_sql_safety.py` — generated SQL never contains DROP/DELETE/etc
- `test_invoice_pipeline.py` — full upload → save → confirm flow (integration)
- `test_rls_enforcement.py` — business A cannot read business B's data (integration)

---

## 10. Local Development

```bash
# Start infrastructure
docker compose up -d

# Run AI service
cd services/ai && uvicorn src.ai_service.main:app --reload --port 8001

# Run backend
cd services/backend && uvicorn src.backend.main:app --reload --port 8000
```

Local uses Docker PostgreSQL + Redis + MinIO. Production uses Supabase + Upstash + Supabase Storage. Same boto3/psycopg2 code — only `.env` changes.

---

## 11. Cost Profile (Phase 1 MVP)

| Item | Monthly cost |
|---|---|
| Supabase (free tier) | ₹0 |
| Upstash Redis (free tier) | ₹0 |
| Railway (2 services) | ~₹400 |
| Claude Haiku (1,000 invoices) | ~₹400 |
| Meta Cloud API (1,000 conversations) | ₹0 |
| **Total** | **~₹800/month** |

Rises to ~₹2,000/month at 5,000 invoices. Templates (Phase 2) reduce Haiku cost by 60-70%.

---

## 12. What's Not in Phase 1

- Web dashboard (Phase 2)
- Double-entry accounting (Phase 2)
- Live auctions (Phase 3)
- Template extractor active (Phase 2, after 500 confirmed invoices)
- Fine-tuned local model (Phase 3, after 1,000 confirmed invoices)
- Async job queue (when sync HTTP becomes a bottleneck at scale)
