# WERP Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build WERP Phase 1 MVP — monorepo with shared library, AI service, and backend service wired to WhatsApp.

**Architecture:** Monorepo with three packages: `shared/` (Pydantic schemas + contracts), `services/ai/` (extraction, classification, query via Claude Haiku), `services/backend/` (WhatsApp webhook, pipeline, Supabase DB). Services communicate via sync HTTP/REST.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, Pydantic v2, httpx, anthropic SDK, pytesseract, pydantic-settings, pytest, testcontainers, respx

**Spec:** `docs/superpowers/specs/2026-09-18-werp-architecture-design.md`

## Global Constraints

- Python 3.12+ required on all services
- Pydantic v2 only — no v1 compatibility shims
- SQLAlchemy 2.0 async style (`async with session` — no legacy `session.execute` sync)
- All functions touching external services must be `async`
- Type hints required on every function signature
- No raw SQL strings — use SQLAlchemy ORM or parameterized text()
- Every new table needs an RLS policy in migrations
- PII fields (phone, GSTIN, business name) must be encrypted at application layer
- `ruff` must pass with zero errors before every commit
- pytest unit tests mock all external calls; integration tests use testcontainers PostgreSQL

---

## File Map

```
werp/
├── pyproject.toml                          MODIFY  add root dev tooling
├── .env.example                            CREATE
├── docker-compose.yml                      CREATE
├── Makefile                                CREATE
├── migrations/
│   └── 001_core_schema.sql                 MODIFY  add extraction_logs table
├── shared/
│   ├── pyproject.toml                      CREATE
│   └── werp_shared/
│       ├── __init__.py                     CREATE
│       ├── config.py                       CREATE  SharedSettings
│       ├── contracts.py                    CREATE  all service request/response models
│       ├── schemas/
│       │   ├── __init__.py                 CREATE
│       │   └── invoice.py                  MOVE    from src/schemas/invoice.py
│       └── validators/
│           ├── __init__.py                 CREATE
│           ├── gstin.py                    CREATE  validate_gstin()
│           └── invoice_math.py             CREATE  verify_tax_math(), verify_line_math()
├── services/
│   ├── ai/
│   │   ├── pyproject.toml                  CREATE
│   │   └── src/ai_service/
│   │       ├── __init__.py                 CREATE
│   │       ├── main.py                     CREATE  FastAPI app
│   │       ├── config.py                   CREATE  AIServiceSettings
│   │       ├── ocr.py                      CREATE  tesseract wrapper
│   │       ├── extraction.py               CREATE  Haiku extraction endpoint
│   │       ├── classify.py                 CREATE  hybrid intent classifier
│   │       └── query.py                    CREATE  hybrid NL query
│   └── backend/
│       ├── pyproject.toml                  CREATE
│       └── src/backend/
│           ├── __init__.py                 CREATE
│           ├── main.py                     CREATE  FastAPI app
│           ├── config.py                   CREATE  BackendSettings
│           ├── dependencies.py             CREATE  FastAPI DI (db, redis, ai_client)
│           ├── webhook/
│           │   ├── router.py               CREATE  GET+POST /webhook/whatsapp
│           │   ├── verifier.py             CREATE  HMAC-SHA256 verification
│           │   └── models.py               CREATE  Meta webhook payload models
│           ├── pipeline/
│           │   ├── invoice.py              CREATE  upload flow
│           │   ├── confirm.py              CREATE  confirm/edit/skip flow
│           │   └── query.py                CREATE  query menu flow
│           ├── messaging/
│           │   └── sender.py               CREATE  WhatsApp send (text/buttons/list)
│           ├── db/
│           │   ├── connection.py           CREATE  async engine + session
│           │   ├── models.py               CREATE  SQLAlchemy ORM models
│           │   └── repositories/
│           │       ├── business.py         CREATE
│           │       ├── invoice.py          CREATE
│           │       ├── inventory.py        CREATE
│           │       └── party.py            CREATE
│           └── services/
│               ├── ai_client.py            CREATE  HTTP client to AI service
│               └── storage.py             CREATE  Supabase Storage client
```

---

## Task 1: Monorepo foundation

**Files:**
- Modify: `pyproject.toml`
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `Makefile`

**Interfaces:**
- Produces: `docker compose up -d` starts postgres:5432, redis:6379, minio:9000

- [ ] **Step 1: Write root pyproject.toml**

```toml
[tool.pytest.ini_options]
addopts = "-v --tb=short"
testpaths = [
    "shared/tests",
    "services/ai/tests",
    "services/backend/tests",
]

[tool.ruff]
line-length = 100
target-version = "py312"
select = ["E", "F", "I", "UP"]

[tool.mypy]
python_version = "3.12"
strict = true
```

- [ ] **Step 2: Write docker-compose.yml**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: werp
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./migrations:/docker-entrypoint-initdb.d
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  minio:
    image: minio/minio:latest
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    command: server /data --console-address ":9001"
    volumes:
      - minio_data:/data

volumes:
  postgres_data:
  minio_data:
```

- [ ] **Step 3: Write .env.example**

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/werp
REDIS_URL=redis://localhost:6379/0
STORAGE_ENDPOINT=http://localhost:9000
STORAGE_BUCKET=invoices
STORAGE_ACCESS_KEY=minioadmin
STORAGE_SECRET_KEY=minioadmin
STORAGE_REGION=ap-south-1
ENCRYPTION_MASTER_KEY=0000000000000000000000000000000000000000000000000000000000000000
ANTHROPIC_API_KEY=sk-ant-change-me
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
WHATSAPP_VERIFY_TOKEN=change-me
WHATSAPP_ACCESS_TOKEN=change-me
WHATSAPP_PHONE_NUMBER_ID=change-me
WHATSAPP_WEBHOOK_SECRET=change-me
AI_SERVICE_URL=http://localhost:8001
AI_EXTRACT_TIMEOUT=30
AI_CLASSIFY_TIMEOUT=10
AI_QUERY_TIMEOUT=15
TEMPLATES_ENABLED=false
TRAINING_DATA_LOGGING=true
ENVIRONMENT=development
DEBUG=true
```

- [ ] **Step 4: Write Makefile**

```makefile
.PHONY: up down dev test test-integration test-ai lint

up:
	docker compose up -d

down:
	docker compose down

dev: up
	@echo "Start AI service:     cd services/ai && uvicorn src.ai_service.main:app --reload --port 8001"
	@echo "Start backend:        cd services/backend && uvicorn src.backend.main:app --reload --port 8000"

test:
	pytest shared/tests services/ai/tests/unit services/backend/tests/unit

test-integration:
	pytest shared/tests services/ai/tests services/backend/tests/unit services/backend/tests/integration

test-ai:
	pytest --run-ai services/backend/tests/ai services/ai/tests/ai

lint:
	ruff check shared services
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml docker-compose.yml .env.example Makefile
git commit -m "feat: monorepo foundation — docker-compose, env template, Makefile"
```

---

## Task 2: Shared library

**Files:**
- Create: `shared/pyproject.toml`
- Create: `shared/werp_shared/__init__.py`
- Create: `shared/werp_shared/config.py`
- Move+refactor: `shared/werp_shared/schemas/invoice.py` (from `src/schemas/invoice.py`)
- Create: `shared/werp_shared/validators/gstin.py`
- Create: `shared/werp_shared/validators/invoice_math.py`
- Create: `shared/werp_shared/contracts.py`
- Create: `shared/tests/test_validators.py`
- Create: `shared/tests/test_contracts.py`

**Interfaces:**
- Produces: `from werp_shared.schemas.invoice import Invoice, LineItem, Party, InvoiceType, InvoiceStatus, SupplyType`
- Produces: `from werp_shared.contracts import ExtractionRequest, ExtractionResult, ExtractionError, ClassifyRequest, ClassifyResult, QueryRequest, QueryResult`
- Produces: `from werp_shared.validators.gstin import validate_gstin`
- Produces: `from werp_shared.validators.invoice_math import verify_total_math`
- Produces: `from werp_shared.config import SharedSettings`

- [ ] **Step 1: Write shared/pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "werp-shared"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
]

[tool.hatch.build.targets.wheel]
packages = ["werp_shared"]
```

- [ ] **Step 2: Write shared/werp_shared/config.py**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class SharedSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    storage_endpoint: str = "http://localhost:9000"
    storage_bucket: str = "invoices"
    storage_access_key: str = "minioadmin"
    storage_secret_key: str = "minioadmin"
    storage_region: str = "ap-south-1"
    encryption_master_key: str
    environment: str = "development"
    debug: bool = False
```

- [ ] **Step 3: Move invoice schema**

Copy `src/schemas/invoice.py` to `shared/werp_shared/schemas/invoice.py` unchanged. Create `shared/werp_shared/schemas/__init__.py` empty. Create `shared/werp_shared/__init__.py` empty. Create `shared/werp_shared/validators/__init__.py` empty.

- [ ] **Step 4: Write failing validator tests**

```python
# shared/tests/test_validators.py
import pytest
from werp_shared.validators.gstin import validate_gstin
from werp_shared.validators.invoice_math import verify_total_math


def test_valid_gstin_passes():
    assert validate_gstin("24AGCPD5109M1ZN") is True


def test_invalid_gstin_wrong_length_fails():
    assert validate_gstin("24AGCPD5109M1Z") is False


def test_invalid_gstin_wrong_format_fails():
    assert validate_gstin("INVALID123") is False


def test_total_math_passes_within_tolerance():
    assert verify_total_math(
        subtotal=71752.50, cgst=6457.73, sgst=6457.73,
        igst=0, cess=0, round_off=0.04, total=84668.00
    ) is True


def test_total_math_fails_when_off_by_more_than_one():
    assert verify_total_math(
        subtotal=71752.50, cgst=6457.73, sgst=6457.73,
        igst=0, cess=0, round_off=0, total=99999.00
    ) is False
```

- [ ] **Step 5: Run failing tests**

```bash
cd shared && pip install -e . && pytest tests/test_validators.py -v
```
Expected: `ModuleNotFoundError` — validators don't exist yet.

- [ ] **Step 6: Write shared/werp_shared/validators/gstin.py**

```python
import re


GSTIN_PATTERN = re.compile(
    r"^\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}Z[A-Z\d]{1}$"
)


def validate_gstin(gstin: str) -> bool:
    if not gstin:
        return False
    return bool(GSTIN_PATTERN.match(gstin.strip().upper()))
```

- [ ] **Step 7: Write shared/werp_shared/validators/invoice_math.py**

```python
from decimal import Decimal


def verify_total_math(
    subtotal: float,
    cgst: float,
    sgst: float,
    igst: float,
    cess: float,
    round_off: float,
    total: float,
    tolerance: float = 1.0,
) -> bool:
    expected = subtotal + cgst + sgst + igst + cess + round_off
    return abs(expected - total) <= tolerance


def verify_line_math(quantity: float, rate: float, amount: float, tolerance: float = 1.0) -> bool:
    return abs(quantity * rate - amount) <= tolerance
```

- [ ] **Step 8: Write shared/werp_shared/contracts.py**

```python
from typing import Literal
from pydantic import BaseModel
from werp_shared.schemas.invoice import Invoice


class ExtractionRequest(BaseModel):
    message_id: str
    business_id: str
    ocr_text: str
    image_b64: str | None = None
    software_hint: str | None = None


class ExtractionResult(BaseModel):
    invoice: Invoice
    confidence: float
    method: Literal["ai", "template", "template+ai"]
    template_name: str | None = None
    flagged_fields: list[str] = []


class ExtractionError(BaseModel):
    code: Literal[
        "NOT_AN_INVOICE",
        "LOW_CONFIDENCE",
        "OCR_EMPTY",
        "AI_UNAVAILABLE",
        "VALIDATION_FAILED",
    ]
    message: str
    partial_data: dict | None = None


class ClassifyRequest(BaseModel):
    message_id: str
    message_text: str
    has_image: bool = False
    has_document: bool = False


class ClassifyResult(BaseModel):
    intent: Literal["UPLOAD", "QUERY", "COMMAND", "UNKNOWN"]
    command: str | None = None
    query_type: str | None = None
    confidence: float
    method: Literal["rules", "ai"]
    language: str | None = None


class QueryRequest(BaseModel):
    message_id: str
    business_id: str
    message_text: str
    language: str = "en"


class QueryResult(BaseModel):
    sql_template: str
    params: list
    query_type: str
    method: Literal["menu", "ai_sql"]
    response_prefix: str
    is_safe: bool
```

- [ ] **Step 9: Write failing contracts test**

```python
# shared/tests/test_contracts.py
from werp_shared.contracts import ExtractionError, ClassifyResult, QueryResult


def test_extraction_error_requires_valid_code():
    import pytest
    with pytest.raises(Exception):
        ExtractionError(code="INVALID_CODE", message="test")


def test_classify_result_defaults():
    r = ClassifyResult(intent="UPLOAD", confidence=0.99, method="rules")
    assert r.command is None
    assert r.query_type is None


def test_query_result_is_safe_required():
    r = QueryResult(
        sql_template="SELECT 1",
        params=[],
        query_type="test",
        method="menu",
        response_prefix="Here:",
        is_safe=True,
    )
    assert r.is_safe is True
```

- [ ] **Step 10: Run all tests — verify they pass**

```bash
cd shared && pytest tests/ -v
```
Expected: all PASS.

- [ ] **Step 11: Commit**

```bash
git add shared/ src/
git commit -m "feat: shared library — schemas, validators, service contracts"
```

---

## Task 3: Database migration update

**Files:**
- Modify: `migrations/001_core_schema.sql`

**Interfaces:**
- Produces: `extraction_logs` table available in PostgreSQL

- [ ] **Step 1: Append extraction_logs table to migration**

Add at the end of `migrations/001_core_schema.sql`:

```sql
-- ─── Extraction logs (training data for future model) ───────────────────────

CREATE TABLE extraction_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id          TEXT NOT NULL UNIQUE,
    business_id         UUID REFERENCES businesses(id),
    ocr_text            TEXT NOT NULL,
    image_key           TEXT NOT NULL,
    software_hint       TEXT,
    ai_response         JSONB NOT NULL,
    confidence          REAL,
    method              TEXT NOT NULL DEFAULT 'ai',
    user_confirmed      BOOLEAN DEFAULT FALSE,
    user_corrections    JSONB,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_extraction_logs_business ON extraction_logs(business_id);
CREATE INDEX idx_extraction_logs_software ON extraction_logs(software_hint);
CREATE INDEX idx_extraction_logs_confirmed ON extraction_logs(user_confirmed);

ALTER TABLE extraction_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_extraction_logs ON extraction_logs
    FOR ALL TO app_user
    USING (business_id = current_setting('app.current_business_id')::UUID);
```

- [ ] **Step 2: Restart docker compose to apply migration**

```bash
docker compose down -v && docker compose up -d
```

- [ ] **Step 3: Verify table exists**

```bash
docker compose exec postgres psql -U postgres -d werp -c "\dt extraction_logs"
```
Expected: table listed.

- [ ] **Step 4: Commit**

```bash
git add migrations/001_core_schema.sql
git commit -m "feat: add extraction_logs table for training data collection"
```

---

## Task 4: AI service scaffold

**Files:**
- Create: `services/ai/pyproject.toml`
- Create: `services/ai/src/ai_service/__init__.py`
- Create: `services/ai/src/ai_service/config.py`
- Create: `services/ai/src/ai_service/main.py`
- Create: `services/ai/tests/__init__.py`
- Create: `services/ai/tests/unit/__init__.py`
- Create: `services/ai/tests/unit/test_health.py`

**Interfaces:**
- Produces: `GET /health` returns `{"status": "ok", "service": "ai"}`

- [ ] **Step 1: Write services/ai/pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "werp-ai"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "werp-shared @ ../../../shared",
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "anthropic>=0.28",
    "pytesseract>=0.3.10",
    "Pillow>=10.0",
    "pydantic-settings>=2.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "respx>=0.21", "pytest-cov"]

[tool.hatch.build.targets.wheel]
packages = ["src/ai_service"]
```

- [ ] **Step 2: Write config.py**

```python
# services/ai/src/ai_service/config.py
from werp_shared.config import SharedSettings


class AIServiceSettings(SharedSettings):
    anthropic_api_key: str
    anthropic_model: str = "claude-haiku-4-5-20251001"
    anthropic_max_tokens: int = 2048
    templates_enabled: bool = False
    training_data_logging: bool = True
    port: int = 8001
    log_level: str = "INFO"


settings = AIServiceSettings()
```

- [ ] **Step 3: Write failing health test**

```python
# services/ai/tests/unit/test_health.py
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_health_returns_ok():
    from ai_service.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "ai"}
```

- [ ] **Step 4: Run — verify fails**

```bash
cd services/ai && pip install -e ".[dev]" && pytest tests/unit/test_health.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 5: Write main.py**

```python
# services/ai/src/ai_service/main.py
from fastapi import FastAPI

app = FastAPI(title="WERP AI Service", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "ai"}
```

- [ ] **Step 6: Run — verify passes**

```bash
pytest services/ai/tests/unit/test_health.py -v
```

- [ ] **Step 7: Commit**

```bash
git add services/ai/
git commit -m "feat: AI service scaffold with health endpoint"
```

---

## Task 5: AI service — OCR + extraction endpoint

**Files:**
- Create: `services/ai/src/ai_service/ocr.py`
- Create: `services/ai/src/ai_service/extraction.py`
- Modify: `services/ai/src/ai_service/main.py`
- Create: `services/ai/tests/unit/test_extraction.py`
- Create: `services/ai/tests/integration/test_extract_endpoint.py`

**Interfaces:**
- Consumes: `ExtractionRequest`, `ExtractionResult`, `ExtractionError` from `werp_shared.contracts`
- Consumes: `Invoice` from `werp_shared.schemas.invoice`
- Produces: `POST /extract` → `ExtractionResult | ExtractionError`

- [ ] **Step 1: Write failing unit tests**

```python
# services/ai/tests/unit/test_extraction.py
import pytest
from unittest.mock import patch, MagicMock
from ai_service.ocr import detect_software_hint


def test_detects_tally_hint():
    assert detect_software_hint("GSTIN: 24ABC\nPowered by TallyPrime") == "tally"


def test_detects_vyapar_hint():
    assert detect_software_hint("Invoice from vyapar.in") == "vyapar"


def test_returns_none_for_unknown():
    assert detect_software_hint("Some random text without markers") is None


def test_returns_none_for_empty():
    assert detect_software_hint("") is None
```

- [ ] **Step 2: Run — verify fails**

```bash
pytest services/ai/tests/unit/test_extraction.py -v
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write services/ai/src/ai_service/ocr.py**

```python
import base64
import io
import pytesseract
from PIL import Image


SOFTWARE_FINGERPRINTS: dict[str, list[str]] = {
    "tally":  ["TallyPrime", "Tally ERP", "Powered by Tally"],
    "vyapar": ["vyapar.in", "Vyapar"],
    "zoho":   ["Zoho Books", "zoho.com"],
    "busy":   ["BUSY Accounting", "busywin.com"],
}


def detect_software_hint(ocr_text: str) -> str | None:
    if not ocr_text:
        return None
    for name, markers in SOFTWARE_FINGERPRINTS.items():
        if any(m.lower() in ocr_text.lower() for m in markers):
            return name
    return None


def run_ocr(image_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(image, lang="eng")


def image_bytes_to_b64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode()
```

- [ ] **Step 4: Run unit tests — verify pass**

```bash
pytest services/ai/tests/unit/test_extraction.py -v
```

- [ ] **Step 5: Write services/ai/src/ai_service/extraction.py**

```python
import json
import logging
from anthropic import AsyncAnthropic
from fastapi import APIRouter
from werp_shared.contracts import ExtractionRequest, ExtractionResult, ExtractionError
from werp_shared.schemas.invoice import Invoice
from werp_shared.validators.invoice_math import verify_total_math
from ai_service.config import settings
from ai_service.ocr import detect_software_hint

logger = logging.getLogger(__name__)
router = APIRouter()
_client: AsyncAnthropic | None = None


def get_anthropic() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


SYSTEM_PROMPT = """You are a GST invoice data extraction engine for Indian businesses.
You receive an invoice image and OCR text. Extract every field into the exact JSON schema provided.
RULES:
- Amounts as strings with exactly 2 decimal places: "71752.50"
- GSTIN: 2-digit state + 10-char PAN + entity + Z + check (15 chars total)
- HSN/SAC codes: 4-8 digit numbers only
- Dates in ISO format: "YYYY-MM-DD"
- Null for missing fields
- supply_type: same seller/buyer state codes → "intra_state"; different → "inter_state"
- Include "confidence" float 0-1 as your overall confidence
RESPOND WITH ONLY valid JSON. No markdown, no explanation."""


@router.post("/extract")
async def extract_invoice(req: ExtractionRequest) -> ExtractionResult | ExtractionError:
    if not req.ocr_text.strip():
        return ExtractionError(code="OCR_EMPTY", message="OCR text is empty")

    software_hint = req.software_hint or detect_software_hint(req.ocr_text)

    content: list = []
    if req.image_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": req.image_b64},
        })
    content.append({
        "type": "text",
        "text": f"Extract all fields from this GST invoice.\n\nOCR text:\n---\n{req.ocr_text}\n---",
    })

    try:
        response = await get_anthropic().messages.create(
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        logger.error("Anthropic API error: %s", e)
        return ExtractionError(code="AI_UNAVAILABLE", message=str(e))

    raw_text = response.content[0].text.strip()
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError:
        return ExtractionError(code="VALIDATION_FAILED", message="AI returned non-JSON")

    confidence = float(raw.pop("confidence", 0.5))

    if confidence < 0.4:
        return ExtractionError(
            code="LOW_CONFIDENCE",
            message=f"Confidence too low: {confidence:.2f}",
            partial_data=raw,
        )

    try:
        invoice = Invoice(**raw)
    except Exception as e:
        return ExtractionError(
            code="VALIDATION_FAILED",
            message=str(e),
            partial_data=raw,
        )

    flagged: list[str] = []
    if not verify_total_math(
        float(invoice.subtotal), float(invoice.cgst_total), float(invoice.sgst_total),
        float(invoice.igst_total), float(invoice.cess_total),
        float(invoice.round_off), float(invoice.total_amount),
    ):
        flagged.append("total_amount")

    return ExtractionResult(
        invoice=invoice,
        confidence=confidence,
        method="ai",
        template_name=software_hint,
        flagged_fields=flagged,
    )
```

- [ ] **Step 6: Mount router in main.py**

```python
# services/ai/src/ai_service/main.py
from fastapi import FastAPI
from ai_service.extraction import router as extraction_router

app = FastAPI(title="WERP AI Service", version="0.1.0")
app.include_router(extraction_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "ai"}
```

- [ ] **Step 7: Write integration test (Anthropic mocked)**

```python
# services/ai/tests/integration/test_extract_endpoint.py
import pytest
import json
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch


MOCK_AI_RESPONSE = {
    "invoice_type": "TAX_SALES",
    "invoice_number": "SI0039",
    "invoice_date": "2026-04-14",
    "seller": {
        "name": "SHIV INDUSTRIES",
        "gstin": "24AGCPD5109M1ZN",
        "pan": "AGCPD5109M",
        "address": {"line1": "Survey No 309", "city": "Ahmedabad",
                    "state_name": "Gujarat", "state_code": 24},
    },
    "buyer": {
        "name": "J S ENTERPRISE",
        "gstin": "24AALFJ3424A1ZW",
        "address": {"line1": "B-20 Maruti", "city": "Ahmedabad",
                    "state_name": "Gujarat", "state_code": 24},
    },
    "line_items": [{
        "sl_no": 1, "description": "FERROUS SULPHATE", "hsn_sac_code": "28332910",
        "quantity": "5315.000", "unit": "kgs", "rate": "13.50", "amount": "71752.50",
        "cgst_rate": "9.00", "cgst_amount": "6457.73",
        "sgst_rate": "9.00", "sgst_amount": "6457.73",
    }],
    "subtotal": "71752.50", "cgst_total": "6457.73", "sgst_total": "6457.73",
    "igst_total": "0.00", "cess_total": "0.00", "round_off": "0.04",
    "total_amount": "84668.00", "supply_type": "intra_state",
    "reverse_charge": False, "confidence": 0.95,
}


@pytest.mark.asyncio
async def test_extract_returns_result_on_success():
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=json.dumps(MOCK_AI_RESPONSE))]

    with patch("ai_service.extraction.get_anthropic") as mock_get:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_message)
        mock_get.return_value = mock_client

        from ai_service.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/extract", json={
                "message_id": "msg-001",
                "business_id": "bus-001",
                "ocr_text": "GSTIN: 24AGCPD5109M1ZN\nInvoice No: SI0039",
            })

    assert resp.status_code == 200
    data = resp.json()
    assert data["confidence"] == 0.95
    assert data["method"] == "ai"
    assert data["invoice"]["invoice_number"] == "SI0039"


@pytest.mark.asyncio
async def test_extract_returns_error_on_empty_ocr():
    from ai_service.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/extract", json={
            "message_id": "msg-002",
            "business_id": "bus-001",
            "ocr_text": "   ",
        })
    assert resp.status_code == 200
    assert resp.json()["code"] == "OCR_EMPTY"
```

- [ ] **Step 8: Run all AI service tests**

```bash
pytest services/ai/tests/ -v
```
Expected: all PASS (Anthropic mocked in integration test).

- [ ] **Step 9: Commit**

```bash
git add services/ai/
git commit -m "feat: AI service extraction endpoint with Haiku + OCR hint detection"
```

---

## Task 6: AI service — classify and query endpoints

**Files:**
- Create: `services/ai/src/ai_service/classify.py`
- Create: `services/ai/src/ai_service/query.py`
- Modify: `services/ai/src/ai_service/main.py`
- Create: `services/ai/tests/unit/test_classify.py`
- Create: `services/ai/tests/unit/test_query.py`

**Interfaces:**
- Produces: `POST /classify` → `ClassifyResult`
- Produces: `POST /query` → `QueryResult`

- [ ] **Step 1: Write failing classify tests**

```python
# services/ai/tests/unit/test_classify.py
import pytest
from ai_service.classify import classify_by_rules


def test_image_attachment_is_upload():
    result = classify_by_rules("", has_image=True)
    assert result.intent == "UPLOAD"
    assert result.method == "rules"
    assert result.confidence >= 0.99


def test_confirm_keyword_is_command():
    result = classify_by_rules("CONFIRM", has_image=False)
    assert result.intent == "COMMAND"
    assert result.command == "CONFIRM"


def test_yes_keyword_is_command():
    result = classify_by_rules("yes", has_image=False)
    assert result.intent == "COMMAND"
    assert result.command == "YES"


def test_menu_number_1_is_query():
    result = classify_by_rules("1", has_image=False)
    assert result.intent == "QUERY"
    assert result.query_type == "monthly_spend"


def test_menu_number_2_is_query():
    result = classify_by_rules("2", has_image=False)
    assert result.intent == "QUERY"
    assert result.query_type == "top_suppliers"


def test_query_keyword_is_query():
    result = classify_by_rules("how much did I spend", has_image=False)
    assert result.intent == "QUERY"


def test_unknown_text_has_low_confidence():
    result = classify_by_rules("asdfghjkl random", has_image=False)
    assert result.confidence < 0.85
```

- [ ] **Step 2: Run — verify fails**

```bash
pytest services/ai/tests/unit/test_classify.py -v
```

- [ ] **Step 3: Write services/ai/src/ai_service/classify.py**

```python
import logging
from anthropic import AsyncAnthropic
from fastapi import APIRouter
from werp_shared.contracts import ClassifyRequest, ClassifyResult
from ai_service.config import settings
from ai_service.extraction import get_anthropic

logger = logging.getLogger(__name__)
router = APIRouter()

COMMAND_KEYWORDS = {"confirm", "edit", "skip", "yes", "no", "cancel", "reject"}
QUERY_TRIGGERS = {"how", "what", "show", "list", "tell", "much", "many", "top", "total"}
MENU_MAP = {
    "1": "monthly_spend",
    "2": "top_suppliers",
    "3": "gst_liability",
    "4": "inventory_status",
    "5": "invoice_history",
}


def classify_by_rules(text: str, has_image: bool = False, has_document: bool = False) -> ClassifyResult:
    if has_image or has_document:
        return ClassifyResult(intent="UPLOAD", confidence=0.99, method="rules")

    t = text.strip().lower()

    if t in COMMAND_KEYWORDS:
        return ClassifyResult(intent="COMMAND", command=t.upper(), confidence=0.99, method="rules")

    if t in MENU_MAP:
        return ClassifyResult(intent="QUERY", query_type=MENU_MAP[t], confidence=0.99, method="rules")

    words = set(t.split())
    if words & QUERY_TRIGGERS:
        return ClassifyResult(intent="QUERY", query_type="freetext", confidence=0.75, method="rules")

    return ClassifyResult(intent="UNKNOWN", confidence=0.4, method="rules")


CLASSIFY_PROMPT = """Classify this WhatsApp message for a GST invoice management app.
The user may write in English, Hindi, Gujarati, Hinglish, or Gujlish.

Intents:
- UPLOAD: user is sending an invoice or bill
- QUERY: user is asking about their business data
- COMMAND: user is confirming/editing/skipping a prompt (confirm/yes/no/edit/skip)
- UNKNOWN: none of the above

Examples:
"Invoice confirm kar do" → COMMAND, command: CONFIRM
"Mujhe last month ka kharcha batao" → QUERY, query_type: freetext
"Invoice bhej raha hoon" → UPLOAD
"હા સાચું છે" → COMMAND, command: YES
"ક્યા ભાવ છે?" → QUERY, query_type: freetext
"1" → QUERY, query_type: monthly_spend

Message: {message}
Has image: {has_image}

Reply with ONLY JSON: {{"intent": "...", "command": null, "query_type": null, "confidence": 0.0, "language": "en"}}"""


@router.post("/classify")
async def classify_intent(req: ClassifyRequest) -> ClassifyResult:
    rule_result = classify_by_rules(req.message_text, req.has_image, req.has_document)

    if rule_result.confidence >= 0.85:
        return rule_result

    # Fall back to Haiku for ambiguous/multilingual messages
    import json
    prompt = CLASSIFY_PROMPT.format(message=req.message_text, has_image=req.has_image)
    try:
        response = await get_anthropic().messages.create(
            model=settings.anthropic_model,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(response.content[0].text.strip())
        return ClassifyResult(
            intent=data.get("intent", "UNKNOWN"),
            command=data.get("command"),
            query_type=data.get("query_type"),
            confidence=float(data.get("confidence", 0.7)),
            method="ai",
            language=data.get("language"),
        )
    except Exception as e:
        logger.error("Classify AI error: %s", e)
        return ClassifyResult(intent="UNKNOWN", confidence=0.3, method="ai")
```

- [ ] **Step 4: Run classify tests — verify pass**

```bash
pytest services/ai/tests/unit/test_classify.py -v
```

- [ ] **Step 5: Write failing query tests**

```python
# services/ai/tests/unit/test_query.py
from ai_service.query import match_menu_query, validate_sql_safety


def test_monthly_spend_matches_menu():
    result = match_menu_query("monthly spend")
    assert result is not None
    assert result.query_type == "monthly_spend"
    assert result.method == "menu"


def test_top_suppliers_matches_menu():
    result = match_menu_query("top suppliers")
    assert result is not None
    assert result.query_type == "top_suppliers"


def test_freetext_returns_none():
    result = match_menu_query("something completely random that never matches")
    assert result is None


def test_safe_select_passes():
    assert validate_sql_safety("SELECT * FROM invoices WHERE business_id = $1") is True


def test_drop_table_fails():
    assert validate_sql_safety("DROP TABLE invoices") is False


def test_delete_fails():
    assert validate_sql_safety("DELETE FROM invoices") is False


def test_no_business_id_fails():
    assert validate_sql_safety("SELECT * FROM invoices") is False
```

- [ ] **Step 6: Write services/ai/src/ai_service/query.py**

```python
import json
import logging
from fastapi import APIRouter
from werp_shared.contracts import QueryRequest, QueryResult
from ai_service.config import settings
from ai_service.extraction import get_anthropic

logger = logging.getLogger(__name__)
router = APIRouter()

FORBIDDEN_KEYWORDS = {"DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE", "ALTER", "CREATE"}

MENU_QUERIES: dict[str, dict] = {
    "monthly_spend": {
        "sql": "SELECT SUM(total_amount) AS total FROM invoices WHERE business_id = $1 AND invoice_date >= date_trunc('month', CURRENT_DATE)",
        "params_hint": ["business_id"],
        "prefix": "Here is your spend this month:",
        "triggers": ["monthly spend", "month kharcha", "is mahine"],
    },
    "top_suppliers": {
        "sql": "SELECT seller_gstin, COUNT(*) AS invoices, SUM(total_amount) AS total FROM invoices WHERE business_id = $1 GROUP BY seller_gstin ORDER BY total DESC LIMIT 5",
        "params_hint": ["business_id"],
        "prefix": "Here are your top 5 suppliers:",
        "triggers": ["top supplier", "best supplier", "sabse bada supplier"],
    },
    "gst_liability": {
        "sql": "SELECT SUM(cgst_total + sgst_total + igst_total) AS liability FROM invoices WHERE business_id = $1 AND invoice_date >= date_trunc('quarter', CURRENT_DATE)",
        "params_hint": ["business_id"],
        "prefix": "Your GST liability this quarter:",
        "triggers": ["gst liability", "gst kitna", "tax liability"],
    },
    "inventory_status": {
        "sql": "SELECT item_name, current_qty, unit, reorder_level FROM inventory WHERE business_id = $1 ORDER BY item_name",
        "params_hint": ["business_id"],
        "prefix": "Here is your current inventory:",
        "triggers": ["inventory", "stock", "stock level", "maal"],
    },
    "invoice_history": {
        "sql": "SELECT invoice_number, invoice_date, total_amount, status FROM invoices WHERE business_id = $1 ORDER BY invoice_date DESC LIMIT 20",
        "params_hint": ["business_id"],
        "prefix": "Your recent invoices:",
        "triggers": ["invoice history", "recent invoice", "invoice list"],
    },
}


def match_menu_query(text: str) -> QueryResult | None:
    t = text.strip().lower()
    for query_type, meta in MENU_QUERIES.items():
        if any(trigger in t for trigger in meta["triggers"]):
            return QueryResult(
                sql_template=meta["sql"],
                params=[],
                query_type=query_type,
                method="menu",
                response_prefix=meta["prefix"],
                is_safe=True,
            )
    return None


def validate_sql_safety(sql: str) -> bool:
    upper = sql.upper().strip()
    if not upper.startswith("SELECT"):
        return False
    if any(kw in upper for kw in FORBIDDEN_KEYWORDS):
        return False
    if "BUSINESS_ID" not in upper:
        return False
    return True


SQL_PROMPT = """Generate a PostgreSQL SELECT query for an Indian GST invoice database.
The user is asking: {message}

Tables available:
- invoices(id, business_id, invoice_number, invoice_date, seller_gstin, buyer_gstin, subtotal, cgst_total, sgst_total, igst_total, total_amount, status)
- line_items(id, invoice_id, description, hsn_sac_code, quantity, unit, rate, amount)
- inventory(id, business_id, item_name, hsn_sac_code, current_qty, unit, avg_cost)
- parties(id, business_id, gstin, name, invoice_count, total_value)

Rules:
- Always filter by business_id = $1 (first parameter, always required)
- Return parameterized SQL with $1, $2 etc
- SELECT only, max 100 rows, no subqueries modifying data
- Reply ONLY JSON: {{"sql": "...", "params": ["business_id"], "type": "custom", "prefix": "Here are your results:"}}"""


@router.post("/query")
async def handle_query(req: QueryRequest) -> QueryResult:
    menu_result = match_menu_query(req.message_text)
    if menu_result:
        menu_result.params = [req.business_id]
        return menu_result

    # Fall back to AI SQL generation
    prompt = SQL_PROMPT.format(message=req.message_text)
    try:
        response = await get_anthropic().messages.create(
            model=settings.anthropic_model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(response.content[0].text.strip())
        sql = data.get("sql", "")
        if not validate_sql_safety(sql):
            return QueryResult(
                sql_template="SELECT 'Query not supported' AS message",
                params=[req.business_id],
                query_type="unsupported",
                method="ai_sql",
                response_prefix="Sorry, I can't run that query safely.",
                is_safe=False,
            )
        return QueryResult(
            sql_template=sql,
            params=[req.business_id] + data.get("params", [])[1:],
            query_type=data.get("type", "custom"),
            method="ai_sql",
            response_prefix=data.get("prefix", "Here are your results:"),
            is_safe=True,
        )
    except Exception as e:
        logger.error("Query AI error: %s", e)
        return QueryResult(
            sql_template="SELECT 'Service unavailable' AS message",
            params=[req.business_id],
            query_type="error",
            method="ai_sql",
            response_prefix="Query service is temporarily unavailable.",
            is_safe=False,
        )
```

- [ ] **Step 7: Mount both routers in main.py**

```python
# services/ai/src/ai_service/main.py
from fastapi import FastAPI
from ai_service.extraction import router as extraction_router
from ai_service.classify import router as classify_router
from ai_service.query import router as query_router

app = FastAPI(title="WERP AI Service", version="0.1.0")
app.include_router(extraction_router)
app.include_router(classify_router)
app.include_router(query_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "ai"}
```

- [ ] **Step 8: Run all AI service tests**

```bash
pytest services/ai/tests/unit/ -v
```
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add services/ai/
git commit -m "feat: AI service classify and query endpoints with hybrid rules+Haiku"
```

---

## Task 7: Backend service scaffold + DB layer

**Files:**
- Create: `services/backend/pyproject.toml`
- Create: `services/backend/src/backend/main.py`
- Create: `services/backend/src/backend/config.py`
- Create: `services/backend/src/backend/dependencies.py`
- Create: `services/backend/src/backend/db/connection.py`
- Create: `services/backend/src/backend/db/models.py`
- Create: `services/backend/tests/unit/test_health.py`
- Create: `services/backend/tests/integration/test_db.py`

**Interfaces:**
- Produces: `GET /health` → `{"status": "ok", "service": "backend", "checks": {...}}`
- Produces: `get_db_session()` FastAPI dependency → `AsyncSession`

- [ ] **Step 1: Write services/backend/pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "werp-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "werp-shared @ ../../../shared",
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "redis[asyncio]>=5.0",
    "httpx>=0.27",
    "boto3>=1.34",
    "pytesseract>=0.3.10",
    "Pillow>=10.0",
    "pydantic-settings>=2.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "respx>=0.21",
       "testcontainers[postgres]>=4.0", "pytest-cov"]

[tool.hatch.build.targets.wheel]
packages = ["src/backend"]
```

- [ ] **Step 2: Write config.py**

```python
# services/backend/src/backend/config.py
from werp_shared.config import SharedSettings


class BackendSettings(SharedSettings):
    whatsapp_verify_token: str = "test-token"
    whatsapp_access_token: str = "test-access"
    whatsapp_phone_number_id: str = "test-phone-id"
    whatsapp_webhook_secret: str = "test-secret"
    ai_service_url: str = "http://localhost:8001"
    ai_extract_timeout: float = 30.0
    ai_classify_timeout: float = 10.0
    ai_query_timeout: float = 15.0
    port: int = 8000
    log_level: str = "INFO"


settings = BackendSettings()
```

- [ ] **Step 3: Write db/connection.py**

```python
# services/backend/src/backend/db/connection.py
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from backend.config import settings

engine = create_async_engine(settings.database_url, echo=settings.debug)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```

- [ ] **Step 4: Write db/models.py**

```python
# services/backend/src/backend/db/models.py
import uuid
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import (
    String, Text, Boolean, Numeric, Integer, SmallInteger,
    Date, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, REAL
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, BYTEA, INET
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    phone_encrypted: Mapped[bytes | None] = mapped_column(BYTEA)
    business_name: Mapped[str | None] = mapped_column(Text)
    gstin: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict] = mapped_column(JSONB, default={})
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("business_id", "invoice_number", "seller_gstin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    invoice_type: Mapped[str] = mapped_column(String(20), default="TAX_SALES")
    invoice_number: Mapped[str] = mapped_column(Text, nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    seller_gstin: Mapped[str] = mapped_column(Text, nullable=False)
    buyer_gstin: Mapped[str] = mapped_column(Text, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    cgst_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    sgst_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    igst_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    raw_image_key: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_confidence: Mapped[float | None] = mapped_column(REAL)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    supply_type: Mapped[str] = mapped_column(String(20), default="intra_state")
    reverse_charge: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    line_items: Mapped[list["LineItem"]] = relationship("LineItem", back_populates="invoice", cascade="all, delete-orphan")


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    sl_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hsn_sac_code: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    cgst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    cgst_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    sgst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    sgst_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    igst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    igst_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="line_items")


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (UniqueConstraint("business_id", "item_name", "hsn_sac_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    item_name: Mapped[str] = mapped_column(Text, nullable=False)
    hsn_sac_code: Mapped[str | None] = mapped_column(Text)
    current_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)
    unit: Mapped[str | None] = mapped_column(Text)
    avg_cost: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Party(Base):
    __tablename__ = "parties"
    __table_args__ = (UniqueConstraint("business_id", "gstin"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    gstin: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    invoice_count: Mapped[int] = mapped_column(Integer, default=0)
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
```

- [ ] **Step 5: Write dependencies.py**

```python
# services/backend/src/backend/dependencies.py
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from backend.db.connection import get_db_session
from backend.config import settings

_redis: Redis | None = None


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis
```

- [ ] **Step 6: Write main.py with health endpoint**

```python
# services/backend/src/backend/main.py
from fastapi import FastAPI, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.connection import get_db_session
from backend.dependencies import get_redis

app = FastAPI(title="WERP Backend", version="0.1.0")


@app.get("/health")
async def health(
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    try:
        await db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "service": "backend",
        "checks": {"database": db_status},
    }
```

- [ ] **Step 7: Write unit health test**

```python
# services/backend/tests/unit/test_health.py
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_health_returns_ok_with_healthy_db():
    from backend.main import app

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with patch("backend.main.get_db_session", return_value=mock_session):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["service"] == "backend"
```

- [ ] **Step 8: Install and run**

```bash
cd services/backend && pip install -e ".[dev]" && pytest tests/unit/test_health.py -v
```

- [ ] **Step 9: Commit**

```bash
git add services/backend/
git commit -m "feat: backend service scaffold with DB models and health endpoint"
```

---

## Task 8: Backend repositories

**Files:**
- Create: `services/backend/src/backend/db/repositories/business.py`
- Create: `services/backend/src/backend/db/repositories/invoice.py`
- Create: `services/backend/src/backend/db/repositories/inventory.py`
- Create: `services/backend/src/backend/db/repositories/party.py`
- Create: `services/backend/tests/integration/test_repositories.py`

**Interfaces:**
- Produces: `BusinessRepository(session).get_or_create_by_phone(phone_hash) -> Business`
- Produces: `InvoiceRepository(session).create(data) -> Invoice`
- Produces: `InvoiceRepository(session).find_duplicate(business_id, invoice_number, seller_gstin) -> Invoice | None`
- Produces: `InvoiceRepository(session).confirm(invoice_id) -> Invoice`
- Produces: `InvoiceRepository(session).get_pending(business_id) -> Invoice | None`
- Produces: `InventoryRepository(session).upsert_from_line_items(business_id, line_items) -> None`
- Produces: `PartyRepository(session).upsert(business_id, gstin, name, invoice_value) -> Party`

- [ ] **Step 1: Write integration test (testcontainers PostgreSQL)**

```python
# services/backend/tests/integration/test_repositories.py
import pytest
import uuid
from testcontainers.postgres import PostgresContainer
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from backend.db.models import Base, Business, Invoice
from backend.db.repositories.business import BusinessRepository
from backend.db.repositories.invoice import InvoiceRepository
from backend.db.repositories.inventory import InventoryRepository
from decimal import Decimal


@pytest.fixture(scope="module")
async def db_session():
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql://", "postgresql+asyncpg://")
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_or_create_business(db_session: AsyncSession):
    repo = BusinessRepository(db_session)
    phone_hash = "abc123hash"
    biz = await repo.get_or_create_by_phone(phone_hash)
    assert biz.id is not None
    assert biz.phone_hash == phone_hash

    biz2 = await repo.get_or_create_by_phone(phone_hash)
    assert biz2.id == biz.id  # same record returned


@pytest.mark.asyncio
async def test_create_and_confirm_invoice(db_session: AsyncSession):
    biz_repo = BusinessRepository(db_session)
    biz = await biz_repo.get_or_create_by_phone("phone-for-invoice-test")

    inv_repo = InvoiceRepository(db_session)
    invoice = await inv_repo.create({
        "business_id": biz.id,
        "invoice_number": "SI0039",
        "invoice_date": "2026-04-14",
        "seller_gstin": "24AGCPD5109M1ZN",
        "buyer_gstin": "24AALFJ3424A1ZW",
        "subtotal": Decimal("71752.50"),
        "total_amount": Decimal("84668.00"),
        "raw_image_key": "invoices/test.jpg",
        "supply_type": "intra_state",
    })
    assert invoice.status == "pending"

    confirmed = await inv_repo.confirm(invoice.id)
    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_at is not None


@pytest.mark.asyncio
async def test_find_duplicate(db_session: AsyncSession):
    biz_repo = BusinessRepository(db_session)
    biz = await biz_repo.get_or_create_by_phone("phone-for-dup-test")

    inv_repo = InvoiceRepository(db_session)
    await inv_repo.create({
        "business_id": biz.id,
        "invoice_number": "DUP001",
        "invoice_date": "2026-04-14",
        "seller_gstin": "24AGCPD5109M1ZN",
        "buyer_gstin": "24AALFJ3424A1ZW",
        "subtotal": Decimal("1000.00"),
        "total_amount": Decimal("1180.00"),
        "raw_image_key": "invoices/dup.jpg",
        "supply_type": "intra_state",
    })

    dup = await inv_repo.find_duplicate(biz.id, "DUP001", "24AGCPD5109M1ZN")
    assert dup is not None

    no_dup = await inv_repo.find_duplicate(biz.id, "NOTEXIST", "24AGCPD5109M1ZN")
    assert no_dup is None
```

- [ ] **Step 2: Run — verify fails**

```bash
pytest services/backend/tests/integration/test_repositories.py -v
```

- [ ] **Step 3: Write business repository**

```python
# services/backend/src/backend/db/repositories/business.py
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.db.models import Business


class BusinessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_by_phone(self, phone_hash: str) -> Business:
        result = await self.session.execute(
            select(Business).where(Business.phone_hash == phone_hash)
        )
        business = result.scalar_one_or_none()
        if business is None:
            business = Business(phone_hash=phone_hash)
            self.session.add(business)
            await self.session.commit()
            await self.session.refresh(business)
        return business

    async def get_by_id(self, business_id: uuid.UUID) -> Business | None:
        result = await self.session.execute(
            select(Business).where(Business.id == business_id)
        )
        return result.scalar_one_or_none()
```

- [ ] **Step 4: Write invoice repository**

```python
# services/backend/src/backend/db/repositories/invoice.py
import uuid
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from backend.db.models import Invoice, LineItem


class InvoiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: dict) -> Invoice:
        invoice = Invoice(**data)
        self.session.add(invoice)
        await self.session.commit()
        await self.session.refresh(invoice)
        return invoice

    async def find_duplicate(
        self, business_id: uuid.UUID, invoice_number: str, seller_gstin: str
    ) -> Invoice | None:
        result = await self.session.execute(
            select(Invoice).where(
                Invoice.business_id == business_id,
                Invoice.invoice_number == invoice_number,
                Invoice.seller_gstin == seller_gstin,
            )
        )
        return result.scalar_one_or_none()

    async def confirm(self, invoice_id: uuid.UUID) -> Invoice:
        result = await self.session.execute(
            select(Invoice).where(Invoice.id == invoice_id).options(selectinload(Invoice.line_items))
        )
        invoice = result.scalar_one()
        invoice.status = "confirmed"
        invoice.confirmed_at = datetime.utcnow()
        await self.session.commit()
        await self.session.refresh(invoice)
        return invoice

    async def get_pending(self, business_id: uuid.UUID) -> Invoice | None:
        result = await self.session.execute(
            select(Invoice)
            .where(Invoice.business_id == business_id, Invoice.status == "pending")
            .options(selectinload(Invoice.line_items))
            .order_by(Invoice.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
```

- [ ] **Step 5: Write inventory repository**

```python
# services/backend/src/backend/db/repositories/inventory.py
import uuid
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from backend.db.models import Inventory, LineItem


class InventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_from_line_items(
        self, business_id: uuid.UUID, line_items: list[LineItem]
    ) -> None:
        for item in line_items:
            stmt = (
                insert(Inventory)
                .values(
                    business_id=business_id,
                    item_name=item.description,
                    hsn_sac_code=item.hsn_sac_code,
                    current_qty=item.quantity,
                    unit=item.unit,
                    avg_cost=item.rate,
                )
                .on_conflict_do_update(
                    index_elements=["business_id", "item_name", "hsn_sac_code"],
                    set_=dict(
                        current_qty=Inventory.current_qty + item.quantity,
                        avg_cost=item.rate,
                    ),
                )
            )
            await self.session.execute(stmt)
        await self.session.commit()
```

- [ ] **Step 6: Write party repository**

```python
# services/backend/src/backend/db/repositories/party.py
import uuid
from decimal import Decimal
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from backend.db.models import Party


class PartyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self, business_id: uuid.UUID, gstin: str, name: str, invoice_value: Decimal
    ) -> None:
        stmt = (
            insert(Party)
            .values(business_id=business_id, gstin=gstin, name=name,
                    invoice_count=1, total_value=invoice_value, last_seen=datetime.utcnow())
            .on_conflict_do_update(
                index_elements=["business_id", "gstin"],
                set_=dict(
                    invoice_count=Party.invoice_count + 1,
                    total_value=Party.total_value + invoice_value,
                    last_seen=datetime.utcnow(),
                ),
            )
        )
        await self.session.execute(stmt)
        await self.session.commit()
```

- [ ] **Step 7: Run integration tests**

```bash
pytest services/backend/tests/integration/test_repositories.py -v
```
Expected: all PASS (testcontainers starts a real PostgreSQL).

- [ ] **Step 8: Commit**

```bash
git add services/backend/src/backend/db/
git commit -m "feat: backend DB repositories — business, invoice, inventory, party"
```

---

## Task 9: Backend external clients + webhook security

**Files:**
- Create: `services/backend/src/backend/services/ai_client.py`
- Create: `services/backend/src/backend/services/storage.py`
- Create: `services/backend/src/backend/webhook/verifier.py`
- Create: `services/backend/src/backend/webhook/models.py`
- Create: `services/backend/tests/unit/test_verifier.py`
- Create: `services/backend/tests/unit/test_ai_client.py`

**Interfaces:**
- Produces: `AIServiceClient.extract(req) -> ExtractionResult | ExtractionError`
- Produces: `AIServiceClient.classify(req) -> ClassifyResult`
- Produces: `AIServiceClient.query(req) -> QueryResult`
- Produces: `verify_whatsapp_signature(payload, signature, secret) -> bool`
- Produces: `StorageClient.upload(key, data) -> str`
- Produces: `StorageClient.download_url(key) -> str`

- [ ] **Step 1: Write failing verifier tests**

```python
# services/backend/tests/unit/test_verifier.py
import hmac
import hashlib
from backend.webhook.verifier import verify_whatsapp_signature


def _make_signature(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_passes():
    payload = b'{"test": "data"}'
    secret = "my-secret"
    sig = _make_signature(payload, secret)
    assert verify_whatsapp_signature(payload, sig, secret) is True


def test_tampered_payload_fails():
    secret = "my-secret"
    sig = _make_signature(b'{"test": "data"}', secret)
    assert verify_whatsapp_signature(b'{"test": "tampered"}', sig, secret) is False


def test_wrong_secret_fails():
    payload = b'{"test": "data"}'
    sig = _make_signature(payload, "real-secret")
    assert verify_whatsapp_signature(payload, sig, "wrong-secret") is False


def test_missing_sha256_prefix_fails():
    payload = b'{"test": "data"}'
    assert verify_whatsapp_signature(payload, "invalidsignature", "secret") is False
```

- [ ] **Step 2: Write verifier**

```python
# services/backend/src/backend/webhook/verifier.py
import hashlib
import hmac


def verify_whatsapp_signature(payload: bytes, signature: str, secret: str) -> bool:
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

- [ ] **Step 3: Run verifier tests**

```bash
pytest services/backend/tests/unit/test_verifier.py -v
```

- [ ] **Step 4: Write webhook models**

```python
# services/backend/src/backend/webhook/models.py
from pydantic import BaseModel


class WhatsAppMessage(BaseModel):
    id: str
    from_: str
    timestamp: str
    type: str
    text: dict | None = None
    image: dict | None = None
    document: dict | None = None
    interactive: dict | None = None

    @property
    def text_body(self) -> str | None:
        if self.text:
            return self.text.get("body")
        if self.interactive:
            reply = self.interactive.get("button_reply") or self.interactive.get("list_reply")
            if reply:
                return reply.get("title")
        return None

    @property
    def has_image(self) -> bool:
        return self.type == "image"

    @property
    def media_id(self) -> str | None:
        if self.image:
            return self.image.get("id")
        if self.document:
            return self.document.get("id")
        return None


class WhatsAppWebhookPayload(BaseModel):
    object: str
    entry: list[dict]

    def extract_messages(self) -> list[tuple[str, WhatsAppMessage]]:
        results = []
        for entry in self.entry:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                phone_number_id = value.get("metadata", {}).get("phone_number_id")
                for msg_data in value.get("messages", []):
                    msg = WhatsAppMessage(
                        id=msg_data["id"],
                        from_=msg_data["from"],
                        timestamp=msg_data["timestamp"],
                        type=msg_data["type"],
                        text=msg_data.get("text"),
                        image=msg_data.get("image"),
                        document=msg_data.get("document"),
                        interactive=msg_data.get("interactive"),
                    )
                    results.append((msg_data["from"], msg))
        return results
```

- [ ] **Step 5: Write AI client**

```python
# services/backend/src/backend/services/ai_client.py
import logging
import httpx
from werp_shared.contracts import (
    ExtractionRequest, ExtractionResult, ExtractionError,
    ClassifyRequest, ClassifyResult,
    QueryRequest, QueryResult,
)
from backend.config import settings

logger = logging.getLogger(__name__)


class AIServiceClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=settings.ai_service_url)
        self._timeouts = {
            "extract":  httpx.Timeout(connect=5.0, read=settings.ai_extract_timeout),
            "classify": httpx.Timeout(connect=5.0, read=settings.ai_classify_timeout),
            "query":    httpx.Timeout(connect=5.0, read=settings.ai_query_timeout),
        }

    async def extract(self, req: ExtractionRequest) -> ExtractionResult | ExtractionError:
        for attempt in range(2):
            try:
                resp = await self._client.post(
                    "/extract", json=req.model_dump(),
                    timeout=self._timeouts["extract"],
                )
                resp.raise_for_status()
                data = resp.json()
                if "code" in data:
                    return ExtractionError(**data)
                return ExtractionResult(**data)
            except httpx.TimeoutException:
                if attempt == 0:
                    continue
                return ExtractionError(code="AI_UNAVAILABLE", message="Extraction timed out")
            except Exception as e:
                return ExtractionError(code="AI_UNAVAILABLE", message=str(e))
        return ExtractionError(code="AI_UNAVAILABLE", message="Max retries exceeded")

    async def classify(self, req: ClassifyRequest) -> ClassifyResult:
        try:
            resp = await self._client.post(
                "/classify", json=req.model_dump(),
                timeout=self._timeouts["classify"],
            )
            resp.raise_for_status()
            return ClassifyResult(**resp.json())
        except Exception:
            return ClassifyResult(intent="UNKNOWN", confidence=0.0, method="rules")

    async def query(self, req: QueryRequest) -> QueryResult:
        resp = await self._client.post(
            "/query", json=req.model_dump(),
            timeout=self._timeouts["query"],
        )
        resp.raise_for_status()
        return QueryResult(**resp.json())

    async def aclose(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 6: Write AI client tests**

```python
# services/backend/tests/unit/test_ai_client.py
import pytest
import respx
import httpx
from backend.services.ai_client import AIServiceClient
from werp_shared.contracts import ExtractionRequest


@pytest.mark.asyncio
@respx.mock
async def test_extract_returns_error_on_timeout():
    respx.post("http://localhost:8001/extract").mock(side_effect=httpx.TimeoutException("timeout"))
    client = AIServiceClient()
    result = await client.extract(ExtractionRequest(
        message_id="m1", business_id="b1", ocr_text="test"
    ))
    assert result.code == "AI_UNAVAILABLE"
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_extract_returns_error_response_as_extraction_error():
    respx.post("http://localhost:8001/extract").mock(
        return_value=httpx.Response(200, json={"code": "NOT_AN_INVOICE", "message": "not invoice"})
    )
    client = AIServiceClient()
    result = await client.extract(ExtractionRequest(
        message_id="m2", business_id="b1", ocr_text="test"
    ))
    assert result.code == "NOT_AN_INVOICE"
    await client.aclose()
```

- [ ] **Step 7: Write storage client**

```python
# services/backend/src/backend/services/storage.py
import boto3
from botocore.config import Config
from backend.config import settings


class StorageClient:
    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.storage_endpoint,
            aws_access_key_id=settings.storage_access_key,
            aws_secret_access_key=settings.storage_secret_key,
            region_name=settings.storage_region,
            config=Config(signature_version="s3v4"),
        )

    def upload(self, key: str, data: bytes, content_type: str = "image/jpeg") -> str:
        self._client.put_object(
            Bucket=settings.storage_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return key

    def presigned_url(self, key: str, expires_in: int = 60) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.storage_bucket, "Key": key},
            ExpiresIn=expires_in,
        )
```

- [ ] **Step 8: Run all unit tests**

```bash
pytest services/backend/tests/unit/ -v
```

- [ ] **Step 9: Commit**

```bash
git add services/backend/src/backend/services/ services/backend/src/backend/webhook/
git commit -m "feat: AI client, storage client, webhook verifier and models"
```

---

## Task 10: Backend messaging + pipelines + webhook router

**Files:**
- Create: `services/backend/src/backend/messaging/sender.py`
- Create: `services/backend/src/backend/pipeline/invoice.py`
- Create: `services/backend/src/backend/pipeline/confirm.py`
- Create: `services/backend/src/backend/pipeline/query.py`
- Create: `services/backend/src/backend/webhook/router.py`
- Modify: `services/backend/src/backend/main.py`
- Create: `services/backend/tests/unit/test_pipeline.py`
- Create: `services/backend/tests/integration/test_webhook.py`

**Interfaces:**
- Consumes: all repositories, AI client, storage client, sender
- Produces: `POST /webhook/whatsapp` processes messages end-to-end
- Produces: `GET /webhook/whatsapp` handles Meta verification

- [ ] **Step 1: Write WhatsApp sender**

```python
# services/backend/src/backend/messaging/sender.py
import logging
import httpx
from backend.config import settings

logger = logging.getLogger(__name__)
WHATSAPP_API_URL = "https://graph.facebook.com/v19.0"


class WhatsAppSender:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
            timeout=10.0,
        )

    async def send_text(self, to: str, text: str) -> None:
        await self._client.post(
            f"{WHATSAPP_API_URL}/{settings.whatsapp_phone_number_id}/messages",
            json={"messaging_product": "whatsapp", "to": to,
                  "type": "text", "text": {"body": text}},
        )

    async def send_buttons(self, to: str, body: str, buttons: list[str]) -> None:
        rows = [{"type": "reply", "reply": {"id": b.lower(), "title": b}} for b in buttons[:3]]
        await self._client.post(
            f"{WHATSAPP_API_URL}/{settings.whatsapp_phone_number_id}/messages",
            json={
                "messaging_product": "whatsapp", "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button", "body": {"text": body},
                    "action": {"buttons": rows},
                },
            },
        )

    async def send_list_menu(self, to: str, header: str, items: list[dict]) -> None:
        rows = [{"id": str(i["id"]), "title": i["title"]} for i in items]
        await self._client.post(
            f"{WHATSAPP_API_URL}/{settings.whatsapp_phone_number_id}/messages",
            json={
                "messaging_product": "whatsapp", "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "list", "header": {"type": "text", "text": header},
                    "body": {"text": "Choose an option:"},
                    "action": {"button": "View options", "sections": [{"rows": rows}]},
                },
            },
        )
```

- [ ] **Step 2: Write invoice pipeline**

```python
# services/backend/src/backend/pipeline/invoice.py
import hashlib
import logging
import uuid
from backend.services.ai_client import AIServiceClient
from backend.services.storage import StorageClient
from backend.db.repositories.business import BusinessRepository
from backend.db.repositories.invoice import InvoiceRepository
from backend.messaging.sender import WhatsAppSender
from werp_shared.contracts import ExtractionRequest, ExtractionError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

QUERY_MENU = (
    "📊 What would you like to see?\n"
    "1. Monthly spend\n2. Top suppliers\n3. GST liability\n4. Inventory status\n5. Invoice history\n\n"
    "Reply with a number or ask freely."
)


def _format_confirmation(invoice) -> str:
    items = invoice.line_items[:5]
    lines = "\n".join(
        f"  • {li.description[:25]} — {li.quantity} {li.unit} @ ₹{li.rate}"
        for li in items
    )
    return (
        f"✅ *Invoice extracted*\n\n"
        f"*From:* {invoice.seller_gstin}\n"
        f"*Invoice:* #{invoice.invoice_number}\n"
        f"*Total:* ₹{invoice.total_amount}\n\n"
        f"{lines}\n\n"
        f"Confirm to save and update inventory."
    )


async def handle_invoice_upload(
    from_phone: str,
    message_id: str,
    media_bytes: bytes,
    session: AsyncSession,
    ai: AIServiceClient,
    storage: StorageClient,
    sender: WhatsAppSender,
) -> None:
    await sender.send_text(from_phone, "✓ Invoice received. Processing...")

    phone_hash = hashlib.sha256(from_phone.encode()).hexdigest()
    biz_repo = BusinessRepository(session)
    business = await biz_repo.get_or_create_by_phone(phone_hash)

    image_key = f"invoices/{business.id}/{message_id}.jpg"
    storage.upload(image_key, media_bytes)

    from ai_service.ocr import run_ocr, image_bytes_to_b64
    ocr_text = run_ocr(media_bytes)
    image_b64 = image_bytes_to_b64(media_bytes)

    req = ExtractionRequest(
        message_id=message_id,
        business_id=str(business.id),
        ocr_text=ocr_text,
        image_b64=image_b64,
    )
    result = await ai.extract(req)

    if isinstance(result, ExtractionError):
        messages = {
            "NOT_AN_INVOICE": "❌ This doesn't look like a GST invoice. Please send a clear invoice photo.",
            "LOW_CONFIDENCE": "⚠️ Couldn't read this invoice clearly. Try a brighter, flatter photo.",
            "OCR_EMPTY": "⚠️ Image appears blank or too dark. Please resend.",
            "AI_UNAVAILABLE": "⚠️ Processing slow right now. Please try again in a moment.",
            "VALIDATION_FAILED": "⚠️ Some fields look unusual. Please check and resend.",
        }
        await sender.send_text(from_phone, messages.get(result.code, "❌ Could not process invoice."))
        return

    inv_repo = InvoiceRepository(session)
    inv = result.invoice

    duplicate = await inv_repo.find_duplicate(business.id, inv.invoice_number, inv.seller.gstin)
    if duplicate:
        await sender.send_buttons(
            from_phone,
            f"⚠️ This looks like a duplicate of #{inv.invoice_number} already saved.\nSkip or keep both?",
            ["SKIP", "KEEP BOTH"],
        )
        return

    await inv_repo.create({
        "business_id": business.id,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date,
        "seller_gstin": inv.seller.gstin,
        "buyer_gstin": inv.buyer.gstin,
        "subtotal": inv.subtotal,
        "cgst_total": inv.cgst_total,
        "sgst_total": inv.sgst_total,
        "igst_total": inv.igst_total,
        "total_amount": inv.total_amount,
        "raw_image_key": image_key,
        "extraction_confidence": result.confidence,
        "supply_type": inv.supply_type.value,
        "reverse_charge": inv.reverse_charge,
    })

    await sender.send_buttons(
        from_phone,
        _format_confirmation(inv),
        ["CONFIRM", "EDIT", "SKIP"],
    )
```

- [ ] **Step 3: Write confirm pipeline**

```python
# services/backend/src/backend/pipeline/confirm.py
import hashlib
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.repositories.business import BusinessRepository
from backend.db.repositories.invoice import InvoiceRepository
from backend.db.repositories.inventory import InventoryRepository
from backend.db.repositories.party import PartyRepository
from backend.messaging.sender import WhatsAppSender

logger = logging.getLogger(__name__)

QUERY_MENU = (
    "📊 What would you like to see?\n"
    "1. Monthly spend\n2. Top suppliers\n3. GST liability\n"
    "4. Inventory status\n5. Invoice history"
)


async def handle_confirm(
    from_phone: str,
    command: str,
    session: AsyncSession,
    sender: WhatsAppSender,
) -> None:
    phone_hash = hashlib.sha256(from_phone.encode()).hexdigest()
    biz_repo = BusinessRepository(session)
    business = await biz_repo.get_or_create_by_phone(phone_hash)

    inv_repo = InvoiceRepository(session)
    pending = await inv_repo.get_pending(business.id)

    if pending is None:
        await sender.send_text(from_phone, "No pending invoice to confirm. Send an invoice photo to get started.")
        return

    if command == "SKIP":
        pending.status = "rejected"
        await session.commit()
        await sender.send_text(from_phone, "Invoice discarded. Send a new invoice photo anytime.")
        return

    if command in ("CONFIRM", "YES"):
        confirmed = await inv_repo.confirm(pending.id)

        inv_repo2 = InventoryRepository(session)
        await inv_repo2.upsert_from_line_items(business.id, confirmed.line_items)

        party_repo = PartyRepository(session)
        await party_repo.upsert(business.id, confirmed.seller_gstin, confirmed.seller_gstin, confirmed.total_amount)

        items_updated = len(confirmed.line_items)
        await sender.send_text(
            from_phone,
            f"✅ Invoice #{confirmed.invoice_number} saved.\n"
            f"Inventory updated: {items_updated} item(s).\n\n"
            + QUERY_MENU
        )
        return

    if command == "EDIT":
        await sender.send_text(from_phone, "What would you like to correct? Reply with the field name and correct value (e.g. 'total 84668.00').")
```

- [ ] **Step 4: Write query pipeline**

```python
# services/backend/src/backend/pipeline/query.py
import hashlib
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from backend.db.repositories.business import BusinessRepository
from backend.services.ai_client import AIServiceClient
from backend.messaging.sender import WhatsAppSender
from werp_shared.contracts import QueryRequest

logger = logging.getLogger(__name__)


async def handle_query(
    from_phone: str,
    message_text: str,
    session: AsyncSession,
    ai: AIServiceClient,
    sender: WhatsAppSender,
) -> None:
    phone_hash = hashlib.sha256(from_phone.encode()).hexdigest()
    biz_repo = BusinessRepository(session)
    business = await biz_repo.get_or_create_by_phone(phone_hash)

    query_result = await ai.query(QueryRequest(
        message_id=f"q-{from_phone}",
        business_id=str(business.id),
        message_text=message_text,
    ))

    if not query_result.is_safe:
        await sender.send_text(from_phone, "Sorry, I can't run that query safely. Try one of the menu options.")
        return

    try:
        params = [str(business.id)] + query_result.params[1:]
        rows = await session.execute(text(query_result.sql_template), params)
        results = rows.fetchmany(20)
        if not results:
            await sender.send_text(from_phone, f"{query_result.response_prefix}\n\nNo data found.")
            return
        lines = "\n".join(str(dict(row._mapping)) for row in results[:10])
        await sender.send_text(from_phone, f"{query_result.response_prefix}\n\n{lines}")
    except Exception as e:
        logger.error("Query execution failed: %s", e)
        await sender.send_text(from_phone, "Could not run that query. Try a simpler question.")
```

- [ ] **Step 5: Write webhook router**

```python
# services/backend/src/backend/webhook/router.py
import logging
from fastapi import APIRouter, Request, HTTPException, Query, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from backend.webhook.verifier import verify_whatsapp_signature
from backend.webhook.models import WhatsAppWebhookPayload
from backend.services.ai_client import AIServiceClient
from backend.services.storage import StorageClient
from backend.messaging.sender import WhatsAppSender
from backend.pipeline.invoice import handle_invoice_upload
from backend.pipeline.confirm import handle_confirm
from backend.pipeline.query import handle_query
from backend.dependencies import get_redis
from backend.db.connection import get_db_session
from backend.config import settings
from werp_shared.contracts import ClassifyRequest
import httpx

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook")

_ai = AIServiceClient()
_storage = StorageClient()
_sender = WhatsAppSender()


@router.get("/whatsapp")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
) -> int:
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def receive_message(
    request: Request,
    redis: Redis = Depends(get_redis),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256", "")

    if not verify_whatsapp_signature(body, signature, settings.whatsapp_webhook_secret):
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = WhatsAppWebhookPayload.model_validate_json(body)

    for from_phone, message in payload.extract_messages():
        msg_key = f"processed:{message.id}"
        already = await redis.set(msg_key, "1", nx=True, ex=86400)
        if already is None:
            continue  # already processed

        if message.has_image and message.media_id:
            media_bytes = await _download_media(message.media_id)
            if media_bytes:
                await handle_invoice_upload(
                    from_phone, message.id, media_bytes, session, _ai, _storage, _sender
                )
            continue

        text = message.text_body or ""
        classify_result = await _ai.classify(ClassifyRequest(
            message_id=message.id,
            message_text=text,
            has_image=message.has_image,
        ))

        if classify_result.intent == "COMMAND" and classify_result.command:
            await handle_confirm(from_phone, classify_result.command, session, _sender)
        elif classify_result.intent == "QUERY":
            await handle_query(from_phone, text, session, _ai, _sender)
        else:
            await _sender.send_text(
                from_phone,
                "Send me an invoice photo to get started, or type a number to query your data:\n"
                "1. Monthly spend  2. Top suppliers  3. GST liability  4. Inventory  5. Invoices"
            )

    return {"status": "ok"}


async def _download_media(media_id: str) -> bytes | None:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://graph.facebook.com/v19.0/{media_id}",
            headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
        )
        if resp.status_code != 200:
            return None
        url = resp.json().get("url")
        if not url:
            return None
        media_resp = await client.get(
            url, headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"}
        )
        return media_resp.content if media_resp.status_code == 200 else None
```

- [ ] **Step 6: Mount webhook router in main.py**

```python
# services/backend/src/backend/main.py
from fastapi import FastAPI, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.connection import get_db_session
from backend.webhook.router import router as webhook_router

app = FastAPI(title="WERP Backend", version="0.1.0")
app.include_router(webhook_router)


@app.get("/health")
async def health(db: AsyncSession = Depends(get_db_session)) -> dict:
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "service": "backend",
        "checks": {"database": db_status},
    }
```

- [ ] **Step 7: Write webhook unit test**

```python
# services/backend/tests/unit/test_pipeline.py
import pytest
import hmac
import hashlib
import json
from httpx import AsyncClient, ASGITransport
from backend.main import app
from backend.config import settings


def _sign(payload: bytes) -> str:
    digest = hmac.new(settings.whatsapp_webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/webhook/whatsapp",
            content=b'{"object":"whatsapp_business_account","entry":[]}',
            headers={"x-hub-signature-256": "sha256=invalidsig"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_webhook_verification_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/webhook/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": settings.whatsapp_verify_token,
                "hub.challenge": "12345",
            },
        )
    assert resp.status_code == 200
    assert resp.text == "12345"
```

- [ ] **Step 8: Run all backend tests**

```bash
pytest services/backend/tests/unit/ -v
```
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add services/backend/
git commit -m "feat: backend messaging, invoice/confirm/query pipelines, webhook router"
```

---

## Task 11: Smoke test — run both services together

**Files:** No new files — verifies everything works end-to-end locally.

- [ ] **Step 1: Start infrastructure**

```bash
docker compose up -d
```

- [ ] **Step 2: Start AI service**

```bash
cd services/ai && uvicorn src.ai_service.main:app --port 8001
```

- [ ] **Step 3: Start backend**

```bash
cd services/backend && uvicorn src.backend.main:app --port 8000
```

- [ ] **Step 4: Verify health endpoints**

```bash
curl http://localhost:8001/health
# Expected: {"status":"ok","service":"ai"}

curl http://localhost:8000/health
# Expected: {"status":"ok","service":"backend","checks":{"database":"ok"}}
```

- [ ] **Step 5: Run full test suite**

```bash
make test
```
Expected: all unit tests PASS.

- [ ] **Step 6: Run integration tests**

```bash
make test-integration
```
Expected: all integration tests PASS (testcontainers starts PostgreSQL automatically).

- [ ] **Step 7: Final commit**

```bash
git add .
git commit -m "feat: Phase 1 MVP complete — AI service, backend service, WhatsApp integration"
```

---

## Self-review

### Spec coverage check

| Spec section | Covered by task |
|---|---|
| Monorepo structure | Task 1 |
| Shared schemas (move from src/) | Task 2 |
| Shared validators (GSTIN, math) | Task 2 |
| Shared contracts (all 6 models) | Task 2 |
| extraction_logs table | Task 3 |
| AI service scaffold | Task 4 |
| OCR wrapper + hint detection | Task 5 |
| Extraction endpoint (Haiku) | Task 5 |
| TEMPLATES_ENABLED flag | Task 5 (config, dormant) |
| Classify endpoint (rules+Haiku) | Task 6 |
| Query endpoint (menu+Haiku SQL) | Task 6 |
| Backend scaffold | Task 7 |
| DB models (Business, Invoice, LineItem, Inventory, Party) | Task 7 |
| Repositories (all 4) | Task 8 |
| AI HTTP client + timeouts | Task 9 |
| Storage client (S3-compatible) | Task 9 |
| HMAC-SHA256 webhook verifier | Task 9 |
| Meta webhook payload parser | Task 9 |
| WhatsApp sender (text/buttons/list) | Task 10 |
| Invoice upload pipeline | Task 10 |
| Confirm/edit/skip pipeline | Task 10 |
| Query pipeline | Task 10 |
| Webhook router (GET+POST) | Task 10 |
| Idempotency (Redis message_id) | Task 10 |
| Duplicate invoice detection | Task 10 |
| Layered tests (unit/integration/ai) | All tasks |
| docker-compose.yml | Task 1 |
| .env.example | Task 1 |
| Option B timeouts (env vars) | Task 9 |

### No placeholders — confirmed.
### Type consistency — all function signatures use exact types defined in tasks where first introduced.
