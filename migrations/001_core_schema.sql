-- BillFlow — Core schema derived from real GST e-Invoice (SI0039, Shiv Industries)
-- Every column maps to an actual field on the invoice.
-- PII fields use pgcrypto. RLS enforces tenant isolation.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─── Businesses (tenants) ───────────────────────────────────────────────────

CREATE TABLE businesses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_hash      TEXT NOT NULL UNIQUE,             -- SHA-256 of WhatsApp number
    phone_encrypted BYTEA,                            -- AES-encrypted phone for OTP
    business_name   TEXT,
    gstin           TEXT,                              -- primary GSTIN
    pan             TEXT,
    state_name      TEXT,
    state_code      SMALLINT CHECK (state_code BETWEEN 1 AND 38),
    address_json    JSONB,                             -- encrypted at app level
    settings        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_businesses_gstin ON businesses(gstin);

-- ─── Parties (sellers/buyers seen across invoices) ──────────────────────────
-- Deduplicated by GSTIN. One row per unique trading partner.

CREATE TABLE parties (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    gstin           TEXT NOT NULL,
    name            TEXT NOT NULL,
    pan             TEXT,                              -- derived from GSTIN[2:12]
    address_line1   TEXT,
    address_line2   TEXT,
    address_line3   TEXT,
    city            TEXT,
    pin_code        TEXT CHECK (pin_code ~ '^\d{6}$' OR pin_code IS NULL),
    state_name      TEXT,
    state_code      SMALLINT CHECK (state_code BETWEEN 1 AND 38),
    first_seen      TIMESTAMPTZ DEFAULT now(),
    last_seen       TIMESTAMPTZ DEFAULT now(),
    invoice_count   INTEGER DEFAULT 0,
    total_value     NUMERIC(18,2) DEFAULT 0,
    UNIQUE(business_id, gstin)
);

CREATE INDEX idx_parties_business ON parties(business_id);
CREATE INDEX idx_parties_gstin ON parties(gstin);

-- ─── Invoices ───────────────────────────────────────────────────────────────

CREATE TYPE invoice_type AS ENUM ('TAX_SALES', 'TAX_PURCHASE', 'CREDIT_NOTE', 'DEBIT_NOTE');
CREATE TYPE invoice_status AS ENUM ('pending', 'confirmed', 'flagged', 'duplicate', 'rejected');
CREATE TYPE supply_type AS ENUM ('intra_state', 'inter_state');
CREATE TYPE dispatch_mode AS ENUM ('TRUCK', 'RAIL', 'AIR', 'SHIP', 'TRANSPORTER', 'COURIER', 'OTHER');

CREATE TABLE invoices (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id         UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,

    -- Identity (from invoice header)
    invoice_type        invoice_type NOT NULL DEFAULT 'TAX_SALES',
    invoice_number      TEXT NOT NULL,                    -- SI0039
    invoice_date        DATE NOT NULL,                    -- 14-Apr-26

    -- Parties (FK to parties table for deduplication)
    seller_party_id     UUID REFERENCES parties(id),
    buyer_party_id      UUID REFERENCES parties(id),
    consignee_party_id  UUID REFERENCES parties(id),      -- ship-to, often same as buyer

    -- Seller/buyer GSTIN stored directly for fast queries without join
    seller_gstin        TEXT NOT NULL,                     -- 24AGCPD5109M1ZN
    buyer_gstin         TEXT NOT NULL,                     -- 24AALFJ3424A1ZW

    -- E-Invoice fields (NIC portal)
    irn                 TEXT,                              -- 64-char Invoice Reference Number
    ack_no              TEXT,                              -- 162624301474406
    ack_date            DATE,                              -- 14-04-2026
    eway_bill_no        TEXT,                              -- 662095209582

    -- Dispatch/transport
    dispatch_doc_no     TEXT,                              -- SI0039
    dispatch_mode       dispatch_mode,                     -- TRUCK
    vehicle_no          TEXT,                              -- GJ27TD1306
    delivery_note       TEXT,
    delivery_note_date  DATE,
    destination         TEXT,
    port_of_loading     TEXT,
    port_of_discharge   TEXT,

    -- Supply classification
    supply_type         supply_type NOT NULL DEFAULT 'intra_state',
    place_of_supply     TEXT,                              -- Gujarat (24)
    reverse_charge      BOOLEAN DEFAULT FALSE,

    -- Totals (all amounts in INR)
    subtotal            NUMERIC(15,2) NOT NULL,            -- 71,752.50
    cgst_total          NUMERIC(15,2) DEFAULT 0,           -- 6,457.73
    sgst_total          NUMERIC(15,2) DEFAULT 0,           -- 6,457.73
    igst_total          NUMERIC(15,2) DEFAULT 0,
    cess_total          NUMERIC(15,2) DEFAULT 0,
    round_off           NUMERIC(8,2) DEFAULT 0,            -- 0.04
    total_amount        NUMERIC(15,2) NOT NULL,            -- 84,668.00
    total_in_words      TEXT,
    tax_amount_in_words TEXT,

    -- References
    buyers_order_no     TEXT,
    buyers_order_date   DATE,
    payment_terms       TEXT,
    other_references    TEXT,
    company_pan         TEXT,                              -- AGCPD5109M

    -- Raw storage
    raw_image_key       TEXT NOT NULL,                     -- S3 key for original PDF/image
    ocr_raw_text        TEXT,                              -- raw OCR output for audit

    -- Extraction metadata
    extraction_confidence REAL CHECK (extraction_confidence BETWEEN 0 AND 1),
    status              invoice_status NOT NULL DEFAULT 'pending',
    status_reason       TEXT,                              -- why flagged/rejected

    -- Timestamps
    created_at          TIMESTAMPTZ DEFAULT now(),
    confirmed_at        TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ DEFAULT now(),

    -- Constraints
    UNIQUE(business_id, invoice_number, seller_gstin),
    CONSTRAINT chk_supply_tax CHECK (
        (supply_type = 'intra_state' AND igst_total = 0)
        OR
        (supply_type = 'inter_state' AND cgst_total = 0 AND sgst_total = 0)
    ),
    CONSTRAINT chk_total CHECK (
        ABS(subtotal + cgst_total + sgst_total + igst_total + cess_total + round_off - total_amount) <= 1
    )
);

CREATE INDEX idx_invoices_business ON invoices(business_id);
CREATE INDEX idx_invoices_date ON invoices(business_id, invoice_date DESC);
CREATE INDEX idx_invoices_seller ON invoices(seller_gstin);
CREATE INDEX idx_invoices_buyer ON invoices(buyer_gstin);
CREATE INDEX idx_invoices_status ON invoices(business_id, status);
CREATE INDEX idx_invoices_irn ON invoices(irn) WHERE irn IS NOT NULL;

-- ─── Line items ─────────────────────────────────────────────────────────────

CREATE TABLE line_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id      UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,

    sl_no           SMALLINT NOT NULL,                    -- 1
    description     TEXT NOT NULL,                        -- FERROUS SULPHATE ETP GRADE
    hsn_sac_code    TEXT NOT NULL CHECK (hsn_sac_code ~ '^\d{4,8}$'),  -- 28332910
    quantity        NUMERIC(12,3) NOT NULL,               -- 5315.000
    unit            TEXT NOT NULL,                        -- kgs
    rate            NUMERIC(15,2) NOT NULL,               -- 13.50
    amount          NUMERIC(15,2) NOT NULL,               -- 71,752.50

    -- Tax per line item
    cgst_rate       NUMERIC(5,2),                         -- 9.00
    cgst_amount     NUMERIC(15,2),                        -- 6,457.73
    sgst_rate       NUMERIC(5,2),
    sgst_amount     NUMERIC(15,2),
    igst_rate       NUMERIC(5,2),
    igst_amount     NUMERIC(15,2),
    cess_rate       NUMERIC(5,2),
    cess_amount     NUMERIC(15,2),

    CONSTRAINT chk_line_math CHECK (ABS(quantity * rate - amount) <= 1)
);

CREATE INDEX idx_line_items_invoice ON line_items(invoice_id);
CREATE INDEX idx_line_items_hsn ON line_items(hsn_sac_code);

-- ─── HSN summary (cross-check table from invoice footer) ────────────────────

CREATE TABLE hsn_summary (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id      UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,

    hsn_sac_code    TEXT NOT NULL,                        -- 28332910
    taxable_value   NUMERIC(15,2) NOT NULL,               -- 71,752.50
    cgst_rate       NUMERIC(5,2),
    cgst_amount     NUMERIC(15,2),
    sgst_rate       NUMERIC(5,2),
    sgst_amount     NUMERIC(15,2),
    igst_rate       NUMERIC(5,2),
    igst_amount     NUMERIC(15,2),
    total_tax_amount NUMERIC(15,2) NOT NULL               -- 12,915.46
);

CREATE INDEX idx_hsn_summary_invoice ON hsn_summary(invoice_id);

-- ─── Inventory (auto-updated from confirmed invoices) ───────────────────────

CREATE TABLE inventory (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id     UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    item_name       TEXT NOT NULL,                        -- normalized description
    hsn_sac_code    TEXT,
    current_qty     NUMERIC(12,3) DEFAULT 0,
    unit            TEXT,
    avg_cost        NUMERIC(15,2),                        -- weighted average cost
    last_purchase_rate NUMERIC(15,2),
    last_purchase_date DATE,
    last_supplier_gstin TEXT,
    reorder_level   NUMERIC(12,3),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(business_id, item_name, hsn_sac_code)
);

CREATE INDEX idx_inventory_business ON inventory(business_id);
CREATE INDEX idx_inventory_hsn ON inventory(hsn_sac_code);

-- ─── Audit log (append-only, no DELETE/UPDATE ever) ─────────────────────────

CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    business_id     UUID,
    actor           TEXT NOT NULL,                        -- 'user', 'system', 'ai_extraction'
    action          TEXT NOT NULL,                        -- 'invoice.created', 'invoice.confirmed'
    entity_type     TEXT,                                 -- 'invoice', 'inventory', 'party'
    entity_id       UUID,
    detail          JSONB,                                -- changed fields, old/new values
    ip_address      INET,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_audit_business ON audit_log(business_id, created_at DESC);

-- Prevent any modification to audit log
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;

-- ─── Row-Level Security ─────────────────────────────────────────────────────
-- Every query is scoped to the authenticated business_id via RLS.

ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE line_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE hsn_summary ENABLE ROW LEVEL SECURITY;
ALTER TABLE parties ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

-- App role used by the API server
CREATE ROLE app_user;

CREATE POLICY tenant_invoices ON invoices
    FOR ALL TO app_user
    USING (business_id = current_setting('app.current_business_id')::UUID);

CREATE POLICY tenant_line_items ON line_items
    FOR ALL TO app_user
    USING (invoice_id IN (
        SELECT id FROM invoices
        WHERE business_id = current_setting('app.current_business_id')::UUID
    ));

CREATE POLICY tenant_hsn_summary ON hsn_summary
    FOR ALL TO app_user
    USING (invoice_id IN (
        SELECT id FROM invoices
        WHERE business_id = current_setting('app.current_business_id')::UUID
    ));

CREATE POLICY tenant_parties ON parties
    FOR ALL TO app_user
    USING (business_id = current_setting('app.current_business_id')::UUID);

CREATE POLICY tenant_inventory ON inventory
    FOR ALL TO app_user
    USING (business_id = current_setting('app.current_business_id')::UUID);

CREATE POLICY tenant_audit ON audit_log
    FOR SELECT TO app_user
    USING (business_id = current_setting('app.current_business_id')::UUID);

-- Audit log: app_user can INSERT but never UPDATE/DELETE
CREATE POLICY audit_insert ON audit_log
    FOR INSERT TO app_user
    WITH CHECK (TRUE);
