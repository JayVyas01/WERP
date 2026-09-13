# WERP — WhatsApp ERP for Indian SMBs

A WhatsApp-first invoice management and ERP system built for Indian small and medium businesses. Send a photo of your GST invoice on WhatsApp — WERP extracts the data, tracks inventory, handles accounting, and answers your business questions in natural language.

No app to install. No training needed. Just WhatsApp.

## How it works

```
You photograph an invoice
        ↓
Send it on WhatsApp
        ↓
WERP extracts every GST field (OCR + AI)
        ↓
You confirm or correct via chat
        ↓
Data flows into inventory, accounting, GST returns
        ↓
Ask questions: "How much did I spend on steel last quarter?"
```

## Product phases

**Phase 1 — Invoice ingestion (MVP)**
WhatsApp upload → OCR + Claude AI extraction → GST validation → secure storage → natural language queries → notifications and suggestions.

**Phase 2 — AI accounting and dashboard**
Double-entry bookkeeping auto-generated from invoices. Web dashboard with GST return prep (GSTR-1, GSTR-3B), P&L, cash flow forecasting, and Tally export.

**Phase 3 — Live auctions**
Private, invite-only, time-boxed auctions between trusted trading partners. Sellers list products, invite specific buyers, run a 30-minute auction via WhatsApp or web UI.

## Project structure

```
werp/
├── src/
│   ├── schemas/
│   │   └── invoice.py            # Pydantic models (Invoice, Party, LineItem, etc.)
│   ├── extraction/
│   │   └── prompt.py             # Claude AI extraction prompt and validation
│   ├── ingestion/
│   │   ├── webhook.py            # WhatsApp webhook handler
│   │   ├── ocr.py                # Tesseract OCR wrapper
│   │   └── pipeline.py           # Extraction orchestration
│   ├── query/
│   │   ├── engine.py             # Natural language → SQL
│   │   └── intent.py             # Intent classification
│   ├── accounting/               # Phase 2
│   │   ├── journal.py            # Double-entry auto-generation
│   │   └── gst_returns.py        # GSTR-1 / GSTR-3B data prep
│   ├── auction/                  # Phase 3
│   │   ├── state_machine.py      # Auction lifecycle
│   │   └── bidding.py            # Bid validation and broadcast
│   ├── notifications/
│   │   └── engine.py             # WhatsApp notifications and suggestions
│   ├── db/
│   │   ├── connection.py         # PostgreSQL connection with RLS
│   │   └── models.py             # SQLAlchemy models (mirrors Pydantic schemas)
│   └── config.py                 # Environment config
├── migrations/
│   └── 001_core_schema.sql       # PostgreSQL schema with RLS, CHECK constraints
├── tests/
│   ├── test_schemas.py           # Pydantic validation tests
│   ├── test_extraction.py        # AI extraction accuracy tests
│   ├── test_ingestion.py         # Webhook and pipeline tests
│   └── fixtures/
│       └── sample_invoices/      # Real invoice samples for testing
├── docs/
│   └── architecture.md           # Full system architecture document
├── pyproject.toml
├── README.md
└── .env.example
```

## Quick start

### Prerequisites

- Python 3.12+
- PostgreSQL 16+
- Redis 7+
- Tesseract OCR (`apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin`)

### Setup

```bash
# Clone
git clone https://github.com/<your-org>/werp.git
cd werp

# Install dependencies
pip install -e ".[dev]"

# Copy env and fill in your keys
cp .env.example .env

# Run database migrations
psql $DATABASE_URL -f migrations/001_core_schema.sql

# Start the server
uvicorn src.main:app --reload --port 8000
```

### Environment variables

```env
# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/werp

# Redis
REDIS_URL=redis://localhost:6379/0

# WhatsApp (Meta Cloud API)
WHATSAPP_VERIFY_TOKEN=your-verify-token
WHATSAPP_ACCESS_TOKEN=your-access-token
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
WHATSAPP_WEBHOOK_SECRET=your-webhook-secret

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Storage (S3-compatible)
S3_BUCKET=werp-invoices
S3_REGION=ap-south-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

# Encryption
ENCRYPTION_MASTER_KEY=...  # 32-byte hex key for PII encryption
```

## Data model

The schema is derived from a real GST e-invoice. Every field maps to an actual invoice field.

**Core tables:**
- `businesses` — tenants, identified by WhatsApp phone number (hashed)
- `parties` — deduplicated sellers/buyers by GSTIN, with running totals
- `invoices` — full invoice header: e-invoice IRN, dispatch, supply type, all tax totals
- `line_items` — per-item breakdown: HSN code, quantity, rate, per-line tax
- `hsn_summary` — footer cross-check table matching CBIC format
- `inventory` — auto-updated stock levels with weighted average cost
- `audit_log` — immutable append-only log of every data operation

**Security layers:**
- Row-Level Security (RLS) scopes every query to the authenticated tenant
- PII fields encrypted with pgcrypto (AES-256)
- Phone numbers stored as SHA-256 hashes
- All storage in AWS ap-south-1 (India region)
- CHECK constraints validate invoice math at the database level

## Extraction pipeline

Invoice processing uses a two-pass strategy for accuracy:

1. **OCR** — Tesseract extracts raw text (no data leaves infrastructure)
2. **AI extraction** — Claude receives both the image and OCR text, outputs structured JSON matching the Pydantic schema
3. **Validation** — Three independent layers check the output:
   - AI self-consistency (image vs OCR cross-reference)
   - Pydantic `model_validator` checks (math, GSTIN format, supply type vs tax)
   - PostgreSQL `CHECK` constraints (final wall before data is persisted)

Extractions with confidence below 0.85 are flagged for user confirmation via WhatsApp.

## GST compliance

WERP handles Indian GST invoicing standards:
- GSTIN validation (format + state code checksum)
- HSN/SAC code validation against CBIC master list
- Intra-state (CGST + SGST) vs inter-state (IGST) classification
- e-Invoice IRN and acknowledgement tracking
- e-Way bill number storage
- GSTR-1 and GSTR-3B data preparation (Phase 2)
- 8-year data retention per CGST Act Section 36

## Tech stack

| Component | Technology |
|---|---|
| Language | Python 3.12, TypeScript (dashboard) |
| API framework | FastAPI |
| Database | PostgreSQL 16 (RLS, pgcrypto) |
| Cache / Queue | Redis + BullMQ |
| Object storage | AWS S3 (ap-south-1) |
| OCR | Tesseract (self-hosted) |
| AI | Claude API (Anthropic) |
| WhatsApp | Meta Cloud API |
| Dashboard | React + Vite (Phase 2) |
| Realtime | Socket.io (Phase 3) |
| Infrastructure | Docker, AWS ECS |

## Running tests

```bash
# All tests
pytest

# With coverage
pytest --cov=src --cov-report=term-missing

# Only schema validation tests
pytest tests/test_schemas.py -v

# Only extraction tests (requires ANTHROPIC_API_KEY)
pytest tests/test_extraction.py -v --run-ai
```

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feat/inventory-alerts`)
3. Write tests for new functionality
4. Ensure `pytest` and `ruff check` pass
5. Submit a pull request

### Code standards

- Type hints on all function signatures
- Docstrings on all public functions
- `ruff` for linting and formatting
- No raw SQL strings in application code — use parameterized queries
- All PII must go through the encryption layer, never stored in plaintext
- Every new table needs an RLS policy

## License

Proprietary. All rights reserved.
