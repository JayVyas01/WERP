# BillFlow — Product Architecture & Development Plan

## 1. What This Product Is

A WhatsApp-first ERP for Indian SMBs. A shopkeeper photographs an invoice, sends it on WhatsApp, and the system does the rest — extracts GST data, tracks inventory, answers natural-language questions, handles accounting, and (later) runs live auctions between trusted buyers.

No app install. No training. Just WhatsApp.

---

## 2. Product Phases

```
Phase 1 (MVP)        WhatsApp invoice ingestion → OCR/AI extraction → secure storage → basic Q&A
Phase 2              AI accounting engine, notifications, smart suggestions, dashboard
Phase 3              Live auction system for B2B sellers
```

---

## 3. System Architecture — High Level

```
┌──────────────────────────────────────────────────────────────────────┐
│                          USER LAYER                                  │
│   WhatsApp ──► Twilio/Meta Cloud API ──► Webhook Ingress             │
│   Web Dashboard (Phase 2)                                            │
│   Auction UI (Phase 3)                                               │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        API GATEWAY                                   │
│   Rate limiting · Auth (JWT + WhatsApp phone verify) · Request log   │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   ┌─────────────┐   ┌─────────────┐   ┌──────────────┐
   │  Ingestion   │   │   Query     │   │  Auction     │
   │  Service     │   │   Service   │   │  Service     │
   │              │   │             │   │  (Phase 3)   │
   │ • receive    │   │ • NL query  │   │              │
   │ • OCR/AI     │   │ • reports   │   │ • rooms      │
   │ • validate   │   │ • suggest   │   │ • bidding    │
   │ • store      │   │ • notify    │   │ • settlement │
   └──────┬───────┘   └──────┬──────┘   └──────┬───────┘
          │                  │                  │
          ▼                  ▼                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        DATA LAYER                                    │
│   PostgreSQL (structured invoice/accounting data, encrypted at rest) │
│   S3-compatible blob store (original invoice images, encrypted)      │
│   Redis (sessions, rate limits, auction state)                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 4. Phase 1 — MVP Deep Dive

### 4.1 WhatsApp Integration

**Provider:** Meta Cloud API (free tier: 1,000 conversations/month) or Twilio WhatsApp Business API.

**Flow:**
```
User sends photo ──► Meta webhook POST /api/wh/incoming
                     {from, media_url, message_id, timestamp}
                          │
                          ▼
                   Download image (signed URL, 5s TTL)
                          │
                          ▼
                   Store raw image in encrypted blob store
                          │
                          ▼
                   Kick off extraction pipeline (async job)
                          │
                          ▼
                   Reply to user: "✓ Invoice received. Processing..."
```

**Key decisions:**
- Use Meta Cloud API directly — cheaper, fewer intermediaries, official.
- Webhook signature verification on every request (HMAC-SHA256).
- Message idempotency: deduplicate on `message_id` before processing.
- Outbound messages use template messages (pre-approved by Meta) for notifications; freeform replies within the 24-hour window.

### 4.2 Invoice Extraction Pipeline

Indian GST invoices have a predictable structure. We exploit that.

**Required fields to extract:**

| Field | Example |
|---|---|
| Seller GSTIN | 29ABCDE1234F1Z5 |
| Buyer GSTIN | 27XYZAB5678C1D3 |
| Invoice number | INV-2026-0042 |
| Invoice date | 13-Sep-2026 |
| Line items[] | {description, HSN code, qty, unit, rate, taxable_value} |
| CGST / SGST / IGST amounts | per line item and totals |
| Total amount | ₹12,450.00 |
| Place of supply | Karnataka (29) |
| Reverse charge | Yes / No |

**Extraction strategy (two-pass):**

```
Pass 1: OCR
  Input:  raw image (JPEG/PNG/PDF)
  Tool:   Tesseract (self-hosted, no data leaves infra)
          OR Google Document AI (better accuracy, data processing agreement needed)
  Output: raw text + bounding boxes

Pass 2: Structured extraction via LLM
  Input:  raw OCR text + image (multimodal)
  Model:  Claude API (claude-sonnet-4-6) with a strict JSON schema prompt
  Prompt: "Extract GST invoice fields into this exact JSON schema: {...}"
  Output: validated InvoiceData JSON

Pass 3: Validation
  • GSTIN format: 2-digit state + 10-char PAN + 1 entity + 1 check (regex + checksum)
  • HSN codes: validate against master list (8-digit, published by CBIC)
  • Tax math: verify line_taxable × rate = tax_amount, sum = total
  • Flag mismatches → ask user to confirm via WhatsApp
```

**Why two-pass?** Pure LLM on raw images works but hallucinates numbers. OCR first gives us the raw text to cross-check against the LLM's structured output. Disagreements get flagged.

### 4.3 Data Model (PostgreSQL)

```sql
-- Core tables. All PII columns use pgcrypto encryption.

CREATE TABLE businesses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_hash      TEXT NOT NULL UNIQUE,       -- SHA-256 of WhatsApp number
    gstin           TEXT,                        -- encrypted
    business_name   TEXT,                        -- encrypted
    created_at      TIMESTAMPTZ DEFAULT now(),
    settings        JSONB DEFAULT '{}'
);

CREATE TABLE invoices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id     UUID REFERENCES businesses(id),
    invoice_number  TEXT NOT NULL,
    invoice_date    DATE NOT NULL,
    seller_gstin    TEXT NOT NULL,               -- encrypted
    buyer_gstin     TEXT NOT NULL,               -- encrypted
    subtotal        NUMERIC(15,2),
    cgst            NUMERIC(15,2),
    sgst            NUMERIC(15,2),
    igst            NUMERIC(15,2),
    total           NUMERIC(15,2),
    place_of_supply TEXT,
    reverse_charge  BOOLEAN DEFAULT FALSE,
    raw_image_key   TEXT NOT NULL,               -- S3 key, encrypted at rest
    extraction_conf REAL,                        -- AI confidence score 0-1
    status          TEXT DEFAULT 'pending',      -- pending | confirmed | flagged
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(business_id, invoice_number, seller_gstin)
);

CREATE TABLE line_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id      UUID REFERENCES invoices(id) ON DELETE CASCADE,
    description     TEXT,
    hsn_code        TEXT,
    quantity        NUMERIC(12,3),
    unit            TEXT,
    rate            NUMERIC(15,2),
    taxable_value   NUMERIC(15,2),
    cgst_rate       NUMERIC(5,2),
    cgst_amount     NUMERIC(15,2),
    sgst_rate       NUMERIC(5,2),
    sgst_amount     NUMERIC(15,2),
    igst_rate       NUMERIC(5,2),
    igst_amount     NUMERIC(15,2),
    line_order      INTEGER
);

CREATE TABLE inventory (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id     UUID REFERENCES businesses(id),
    item_name       TEXT NOT NULL,
    hsn_code        TEXT,
    current_qty     NUMERIC(12,3) DEFAULT 0,
    unit            TEXT,
    avg_cost        NUMERIC(15,2),
    last_updated    TIMESTAMPTZ DEFAULT now(),
    reorder_level   NUMERIC(12,3),
    UNIQUE(business_id, item_name, hsn_code)
);

-- Audit log: every data access is logged
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    business_id     UUID,
    action          TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       UUID,
    performed_by    TEXT,
    ip_address      INET,
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### 4.4 Natural Language Query Engine

User sends a WhatsApp message like:
- "How much did I spend on steel last month?"
- "Show my top 5 suppliers by value"
- "What's my IGST liability this quarter?"

**Architecture:**
```
User message
    │
    ▼
Intent classifier (Claude, fast model)
    │
    ├──► QUERY intent ──► Text-to-SQL generation ──► execute ──► format reply
    ├──► UPLOAD intent ──► redirect to ingestion pipeline
    ├──► COMMAND intent ──► handle (set reorder level, confirm invoice, etc.)
    └──► CHITCHAT ──► polite redirect
```

**Text-to-SQL safety:**
- Generated SQL runs through a read-only connection (SELECT only).
- Query is parameterized — LLM generates the query template, parameters are bound separately.
- Result set capped at 100 rows.
- User can only query their own `business_id` — enforced at the DB connection level via row-level security (RLS).

### 4.5 Notification & Suggestion Engine

Even in MVP, the system should feel alive:

| Trigger | Notification |
|---|---|
| Invoice extracted successfully | "✓ Invoice #INV-042 from ABC Traders — ₹12,450. 5 items added to inventory. Reply CONFIRM or EDIT." |
| Extraction confidence < 0.85 | "⚠ Couldn't read line 3 clearly. Is this 'MS Rod 12mm, qty 50'? Reply YES or type correction." |
| Duplicate invoice detected | "This looks like a duplicate of INV-042 from 2 days ago. Reply SKIP or KEEP BOTH." |
| Inventory item below reorder | "📦 Cement (ACC PPC) is at 15 bags — below your reorder level of 50. Want to reorder?" |
| Weekly summary (Sunday 9am) | "Last week: 12 invoices, ₹3.2L total. Top supplier: XYZ Steel. GST liability: ₹18,400." |

---

## 5. Phase 2 — AI Accounting & Dashboard

### 5.1 Accounting Engine

Map every invoice to double-entry bookkeeping automatically:

```
Purchase invoice received:
    DR  Purchase A/C (by HSN category)     ₹10,000
    DR  Input CGST A/C                      ₹900
    DR  Input SGST A/C                      ₹900
    CR  Accounts Payable / Supplier A/C    ₹11,800

Sale invoice uploaded:
    DR  Accounts Receivable / Customer A/C ₹11,800
    CR  Sales A/C                          ₹10,000
    CR  Output CGST A/C                     ₹900
    CR  Output SGST A/C                     ₹900
```

**AI layer on top:**
- Auto-categorize expenses by HSN → Chart of Accounts mapping.
- Flag unusual transactions (amount 10× higher than average for that supplier).
- GST return preparation: auto-fill GSTR-1 and GSTR-3B data.
- Cash flow forecasting based on invoice patterns.
- Reconciliation suggestions when amounts don't match.

### 5.2 Web Dashboard

A lightweight React dashboard for users who want more than WhatsApp:
- Invoice list with search and filters
- Inventory overview with reorder alerts
- GST summary (input vs output, liability)
- P&L and cash flow charts
- Export to Tally-compatible format

Auth: phone number OTP (same number as WhatsApp).

---

## 6. Phase 3 — Live Auction System

### 6.1 Concept

A seller wants to sell 500 tons of TMT bars. They invite 8 known buyers to a 30-minute auction.

**Not a public marketplace.** This is private, invite-only, time-boxed.

### 6.2 Auction Flow

```
Seller creates auction (via WhatsApp or dashboard):
    • Product: TMT Bars Fe500D, 500 MT
    • Base price: ₹48,000/MT
    • Duration: 30 minutes
    • Invited buyers: [phone1, phone2, ..., phone8]
          │
          ▼
    System sends WhatsApp invites with auction link
          │
          ▼
    Auction goes live at scheduled time
          │
          ▼
    Buyers bid via WhatsApp ("BID 49500") or web UI
    Real-time updates pushed to all participants
          │
          ▼
    Timer expires → highest bid wins
    Both parties notified, invoice auto-generated
```

### 6.3 Technical Design

```
WebSocket server (for real-time bids on web UI)
    +
WhatsApp message handler (for bid-by-text)
    │
    ▼
Auction State Machine (Redis)
    States: CREATED → INVITED → LIVE → CLOSING → SETTLED → CANCELLED
    │
    ▼
Bid validation:
    • Is bidder invited?
    • Is auction still live?
    • Is bid > current highest?
    • Rate limit: max 1 bid per 5 seconds per user
    │
    ▼
Broadcast new bid to all participants (masked: "New bid: ₹49,500")
    │
    ▼
On close: atomic settlement, generate invoice, notify both parties
```

**Anti-gaming:**
- Last-second extension: if a bid arrives in the final 60 seconds, extend by 60 seconds (max 3 extensions).
- Bid history is immutable and auditable.
- Seller cannot bid on their own auction.
- All participants see the same price; bidder identity is hidden until settlement.

---

## 7. Security Architecture

### 7.1 Data Protection

| Layer | Measure |
|---|---|
| Transit | TLS 1.3 everywhere, certificate pinning on mobile |
| Storage | AES-256 encryption at rest (S3 SSE-KMS, PostgreSQL pgcrypto) |
| PII fields | Application-level encryption with per-tenant keys |
| Images | Stored encrypted, presigned URLs with 60s expiry for access |
| Phone numbers | Stored as SHA-256 hash; original only in encrypted PII vault |
| Backups | Encrypted, stored in separate region, 30-day retention |

### 7.2 Access Control

- Row-Level Security (RLS) in PostgreSQL: every query scoped to `business_id`.
- API auth: JWT with phone-OTP verification, 15-min access token + 7-day refresh.
- WhatsApp identity: verified by Meta — phone number is the identity.
- Admin access: separate credentials, MFA required, all actions audit-logged.
- No shared tenancy in queries: even Text-to-SQL runs in tenant-scoped read-only connections.

### 7.3 Compliance

- GST data retention: 8 years (as per CGST Act, Section 36).
- Data residency: all storage in Indian AWS/GCP region (ap-south-1).
- Audit log: immutable append-only table, no DELETE permissions.
- DPDP Act readiness: consent collection, data access/deletion APIs, breach notification flow.

---

## 8. Tech Stack

| Component | Technology | Rationale |
|---|---|---|
| Runtime | Node.js (TypeScript) | Fast async I/O, good for webhook-heavy workloads |
| Framework | Fastify | Faster than Express, built-in schema validation |
| Database | PostgreSQL 16 | RLS, pgcrypto, JSONB, rock-solid for financial data |
| Cache/Queue | Redis + BullMQ | Job queue for async extraction, auction state |
| Object Storage | AWS S3 (ap-south-1) | Encrypted invoice images |
| OCR | Tesseract (self-hosted) | No data leaves infra; upgrade path to Document AI |
| AI | Claude API (Anthropic) | Extraction, NL queries, accounting categorization |
| WhatsApp | Meta Cloud API | Official, free tier, direct integration |
| Dashboard | React + Vite | Phase 2, lightweight SPA |
| Realtime | Socket.io | Phase 3 auction bids |
| Infra | Docker + AWS ECS | Simple container orchestration |
| CI/CD | GitHub Actions | Standard, free for private repos |

---

## 9. Development Roadmap

### Phase 1 — MVP (Weeks 1–8)

```
Week 1-2:  Project setup, DB schema, WhatsApp webhook integration
           Receive image, store, reply "received"

Week 3-4:  OCR + AI extraction pipeline
           Tesseract → Claude structured extraction → validation
           Confidence scoring, user confirmation flow

Week 5-6:  Inventory tracking (auto-update from invoices)
           Natural language query engine (basic: spend, top suppliers, GST)
           Notification system (confirmations, duplicates, weekly summary)

Week 7-8:  Security hardening (encryption, RLS, audit log)
           Testing with real GST invoices (collect 50+ samples)
           Beta launch with 5-10 pilot users
```

### Phase 2 — Accounting & Dashboard (Weeks 9–16)

```
Week 9-10:  Double-entry accounting engine
            Auto journal entries from invoices

Week 11-12: Web dashboard (auth, invoice list, inventory view)
            GST return data preparation (GSTR-1, GSTR-3B)

Week 13-14: AI accounting features
            Anomaly detection, cash flow forecasting
            Tally export

Week 15-16: Testing, iteration with pilot users
            Public beta launch
```

### Phase 3 — Auction (Weeks 17–24)

```
Week 17-18: Auction data model, state machine
            Create/invite flow via WhatsApp

Week 19-20: Real-time bidding (WhatsApp + WebSocket)
            Anti-gaming rules, bid validation

Week 21-22: Settlement flow, auto-invoice generation
            Auction history and analytics

Week 23-24: Load testing, security audit
            Launch to select sellers
```

---

## 10. Cost Estimate (MVP, Monthly)

| Item | Cost |
|---|---|
| AWS ECS (2 containers) | ~₹5,000 |
| PostgreSQL (RDS db.t3.micro) | ~₹3,000 |
| S3 storage (10 GB) | ~₹100 |
| Redis (ElastiCache t3.micro) | ~₹2,500 |
| Claude API (1,000 extractions) | ~₹8,000 |
| Meta Cloud API (1,000 conversations) | Free |
| Domain + SSL | ~₹500 |
| **Total** | **~₹19,100/mo (~$230)** |

Scales linearly. At 10,000 invoices/month, roughly ₹80,000/mo.

---

## 11. Key Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| OCR accuracy on handwritten invoices | Wrong data | Confidence scoring + user confirmation; reject below 0.6 |
| WhatsApp API policy changes | Lose primary channel | Abstract messaging layer; Telegram/SMS as fallback |
| GST format changes | Broken extraction | Versioned extraction prompts; monitor CBIC updates |
| Data breach | Legal, trust | Encryption at every layer, minimal PII storage, audit logs |
| LLM hallucination on numbers | Wrong financials | Two-pass verify (OCR vs LLM), math validation, human confirm |
| Auction disputes | Trust | Immutable bid log, clear T&C, optional escrow integration |

---

## 12. What Success Looks Like

**MVP (Month 2):** 10 businesses uploading invoices daily via WhatsApp, >90% extraction accuracy on printed GST invoices, users asking 2+ natural language queries per week.

**Phase 2 (Month 5):** 100 businesses, GST return prep saving 4+ hours/month per user, dashboard DAU > 30%.

**Phase 3 (Month 8):** 5 auctions/week, average 4 bidders per auction, zero disputed settlements.
