"""
BillFlow — Pydantic schemas derived from real GST e-Invoice (SI0039, Shiv Industries)

Every field maps to an actual field visible on the uploaded invoice.
Validation rules follow GST e-invoicing spec (CBIC/NIC).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ─── Enums ───────────────────────────────────────────────────────────────────

class InvoiceType(str, Enum):
    TAX_SALES = "TAX_SALES"
    TAX_PURCHASE = "TAX_PURCHASE"
    CREDIT_NOTE = "CREDIT_NOTE"
    DEBIT_NOTE = "DEBIT_NOTE"


class InvoiceStatus(str, Enum):
    PENDING = "pending"          # extraction done, awaiting user confirm
    CONFIRMED = "confirmed"      # user verified
    FLAGGED = "flagged"          # confidence low or math mismatch
    DUPLICATE = "duplicate"      # matches existing invoice
    REJECTED = "rejected"        # user rejected extraction


class DispatchMode(str, Enum):
    TRUCK = "TRUCK"
    RAIL = "RAIL"
    AIR = "AIR"
    SHIP = "SHIP"
    TRANSPORTER = "TRANSPORTER"
    COURIER = "COURIER"
    OTHER = "OTHER"


class SupplyType(str, Enum):
    INTRA_STATE = "intra_state"   # CGST + SGST
    INTER_STATE = "inter_state"   # IGST


# ─── Reusable sub-models ─────────────────────────────────────────────────────

class Address(BaseModel):
    """Postal address as seen on the invoice."""
    line1: str = Field(..., max_length=200, description="Primary address line")
    line2: Optional[str] = Field(None, max_length=200)
    line3: Optional[str] = Field(None, max_length=200)
    city: str = Field(..., max_length=100)
    pin_code: Optional[str] = Field(None, pattern=r"^\d{6}$")
    state_name: str = Field(..., max_length=50, description="e.g. Gujarat")
    state_code: int = Field(..., ge=1, le=38, description="GST state code, e.g. 24")


class Party(BaseModel):
    """
    Seller, buyer, or consignee on the invoice.

    From the invoice:
      SHIV INDUSTRIES
      SURVEY NO. 309/1, B/H. GIDC, PHASE-IV, ...
      GSTIN/UIN: 24AGCPD5109M1ZN
      State Name: Gujarat, Code: 24
    """
    name: str = Field(..., max_length=200, description="Legal business name")
    gstin: str = Field(..., description="15-char GSTIN e.g. 24AGCPD5109M1ZN")
    pan: Optional[str] = Field(None, description="10-char PAN extracted from GSTIN or declared")
    address: Address

    @field_validator("gstin")
    @classmethod
    def validate_gstin(cls, v: str) -> str:
        v = v.strip().upper()
        pattern = r"^\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}[Z]{1}[A-Z\d]{1}$"
        if not re.match(pattern, v):
            raise ValueError(
                f"Invalid GSTIN format: {v}. "
                "Expected: 2-digit state + 10-char PAN + entity + Z + check"
            )
        return v

    @field_validator("pan")
    @classmethod
    def validate_pan(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().upper()
        if not re.match(r"^[A-Z]{5}\d{4}[A-Z]$", v):
            raise ValueError(f"Invalid PAN format: {v}")
        return v

    @property
    def derived_pan(self) -> str:
        """PAN is characters 3-12 of GSTIN."""
        return self.gstin[2:12]

    @property
    def state_code(self) -> int:
        """State code is first 2 digits of GSTIN."""
        return int(self.gstin[:2])


# ─── Line item ───────────────────────────────────────────────────────────────

class LineItem(BaseModel):
    """
    Single line item from the invoice.

    From the invoice:
      Sl. | Description of Goods      | HSN/SAC  | Quantity     | Rate  | Per | Amount
      1   | FERROUS SULPHATE ETP GRADE | 28332910 | 5,315.00 kgs | 13.50 | kgs | 71,752.50
    """
    sl_no: int = Field(..., ge=1, description="Serial number on invoice")
    description: str = Field(..., max_length=500, description="e.g. FERROUS SULPHATE ETP GRADE")
    hsn_sac_code: str = Field(..., description="HSN/SAC code, e.g. 28332910")
    quantity: Decimal = Field(..., gt=0, decimal_places=3)
    unit: str = Field(..., max_length=10, description="e.g. kgs, nos, ltrs, mtrs")
    rate: Decimal = Field(..., ge=0, decimal_places=2, description="Rate per unit")
    amount: Decimal = Field(..., ge=0, decimal_places=2, description="quantity × rate before tax")

    # Tax breakup per line item
    cgst_rate: Optional[Decimal] = Field(None, ge=0, le=28, description="CGST rate %")
    cgst_amount: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    sgst_rate: Optional[Decimal] = Field(None, ge=0, le=28, description="SGST rate %")
    sgst_amount: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    igst_rate: Optional[Decimal] = Field(None, ge=0, le=28, description="IGST rate %")
    igst_amount: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    cess_rate: Optional[Decimal] = Field(None, ge=0)
    cess_amount: Optional[Decimal] = Field(None, ge=0, decimal_places=2)

    @field_validator("hsn_sac_code")
    @classmethod
    def validate_hsn(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^\d{4,8}$", v):
            raise ValueError(f"HSN/SAC must be 4-8 digits, got: {v}")
        return v

    @model_validator(mode="after")
    def validate_line_math(self) -> "LineItem":
        """Verify quantity × rate = amount (within ₹1 tolerance for rounding)."""
        expected = self.quantity * self.rate
        if abs(expected - self.amount) > Decimal("1.00"):
            raise ValueError(
                f"Line math mismatch: {self.quantity} × {self.rate} = {expected}, "
                f"but amount is {self.amount}"
            )
        return self


# ─── HSN summary row ─────────────────────────────────────────────────────────

class HSNSummary(BaseModel):
    """
    From the HSN/SAC summary table at the bottom of the invoice.

      HSN/SAC  | Taxable Value | CGST Rate | CGST Amt | SGST Rate | SGST Amt | Total Tax
      28332910 | 71,752.50     | 9%        | 6,457.73 | 9%        | 6,457.73 | 12,915.46
    """
    hsn_sac_code: str
    taxable_value: Decimal = Field(..., ge=0, decimal_places=2)
    cgst_rate: Optional[Decimal] = Field(None, ge=0, le=28)
    cgst_amount: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    sgst_rate: Optional[Decimal] = Field(None, ge=0, le=28)
    sgst_amount: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    igst_rate: Optional[Decimal] = Field(None, ge=0, le=28)
    igst_amount: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    total_tax_amount: Decimal = Field(..., ge=0, decimal_places=2)


# ─── Dispatch details ────────────────────────────────────────────────────────

class DispatchDetails(BaseModel):
    """
    Transport/shipping details from the invoice header.

    From the invoice:
      Dispatch Doc No: SI0039
      Dispatched through: TRUCK
      Vehicle No: GJ27TD1306
    """
    dispatch_doc_no: Optional[str] = Field(None, max_length=50)
    dispatch_mode: Optional[DispatchMode] = None
    vehicle_no: Optional[str] = Field(None, max_length=20, description="e.g. GJ27TD1306")
    delivery_note: Optional[str] = Field(None, max_length=50)
    delivery_note_date: Optional[date] = None
    destination: Optional[str] = Field(None, max_length=200)
    port_of_loading: Optional[str] = Field(None, max_length=200)
    port_of_discharge: Optional[str] = Field(None, max_length=200)


# ─── E-Invoice metadata ─────────────────────────────────────────────────────

class EInvoiceMetadata(BaseModel):
    """
    NIC e-Invoice portal fields. Present on all e-invoices.

    From the invoice:
      IRN: 924e3d74cf93d514933fec3167a454cc5fb5d2032ff48b934f7-c3f0b55f11bd6
      Ack No: 162624301474406
      Ack Date: 14-04-2026
    """
    irn: Optional[str] = Field(None, max_length=80, description="Invoice Reference Number (64-char hash)")
    ack_no: Optional[str] = Field(None, max_length=30, description="NIC acknowledgement number")
    ack_date: Optional[date] = Field(None, description="NIC acknowledgement date")
    eway_bill_no: Optional[str] = Field(None, max_length=20, description="e-Way bill number")


# ─── Main Invoice schema ────────────────────────────────────────────────────

class Invoice(BaseModel):
    """
    Complete GST invoice schema. Every field maps to the uploaded Shiv Industries invoice.

    This is the canonical shape of extracted data — what the AI pipeline outputs
    and what the user confirms before it hits the database.
    """

    # ── Identity ──
    id: UUID = Field(default_factory=uuid4)
    invoice_type: InvoiceType = Field(default=InvoiceType.TAX_SALES)
    invoice_number: str = Field(..., max_length=50, description="e.g. SI0039")
    invoice_date: date = Field(..., description="e.g. 2026-04-14")

    # ── Parties ──
    seller: Party = Field(..., description="The entity issuing the invoice (Shiv Industries)")
    buyer: Party = Field(..., description="Bill-to party (J S Enterprise)")
    consignee: Optional[Party] = Field(None, description="Ship-to party, if different from buyer")

    # ── E-Invoice & transport ──
    einvoice: Optional[EInvoiceMetadata] = None
    dispatch: Optional[DispatchDetails] = None

    # ── Line items ──
    line_items: list[LineItem] = Field(..., min_length=1)

    # ── Totals ──
    subtotal: Decimal = Field(..., ge=0, decimal_places=2, description="Sum of line item amounts before tax")
    cgst_total: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    sgst_total: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    igst_total: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    cess_total: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2)
    round_off: Decimal = Field(default=Decimal("0"), description="Round-off amount (+/-)")
    total_amount: Decimal = Field(..., ge=0, decimal_places=2, description="Grand total incl. tax")
    total_in_words: Optional[str] = Field(None, max_length=500)
    tax_amount_in_words: Optional[str] = Field(None, max_length=500)

    # ── HSN summary (cross-check table) ──
    hsn_summary: Optional[list[HSNSummary]] = None

    # ── Supply classification ──
    supply_type: SupplyType = Field(
        default=SupplyType.INTRA_STATE,
        description="Intra-state (CGST+SGST) or inter-state (IGST)"
    )
    place_of_supply: Optional[str] = Field(None, max_length=50, description="State name + code")
    reverse_charge: bool = Field(default=False)

    # ── References ──
    buyers_order_no: Optional[str] = Field(None, max_length=50)
    buyers_order_date: Optional[date] = None
    payment_terms: Optional[str] = Field(None, max_length=200)
    other_references: Optional[str] = Field(None, max_length=200)
    company_pan: Optional[str] = Field(None, description="Declared PAN on invoice")

    # ── Extraction metadata (not on the invoice itself) ──
    raw_image_key: Optional[str] = Field(None, description="S3 key for the original image/PDF")
    extraction_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    status: InvoiceStatus = Field(default=InvoiceStatus.PENDING)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Validators ──

    @model_validator(mode="after")
    def validate_supply_type_vs_tax(self) -> "Invoice":
        """Intra-state must have CGST+SGST. Inter-state must have IGST."""
        if self.supply_type == SupplyType.INTRA_STATE:
            if self.igst_total > 0:
                raise ValueError("Intra-state invoice cannot have IGST")
        elif self.supply_type == SupplyType.INTER_STATE:
            if self.cgst_total > 0 or self.sgst_total > 0:
                raise ValueError("Inter-state invoice cannot have CGST/SGST")
        return self

    @model_validator(mode="after")
    def validate_total_math(self) -> "Invoice":
        """
        Verify: subtotal + all taxes + round_off = total_amount
        Tolerance: ₹1 (invoices often round to nearest rupee).
        """
        expected = (
            self.subtotal
            + self.cgst_total
            + self.sgst_total
            + self.igst_total
            + self.cess_total
            + self.round_off
        )
        if abs(expected - self.total_amount) > Decimal("1.00"):
            raise ValueError(
                f"Total mismatch: {self.subtotal} + {self.cgst_total} + {self.sgst_total} "
                f"+ {self.igst_total} + {self.cess_total} + {self.round_off} "
                f"= {expected}, but total is {self.total_amount}"
            )
        return self

    @model_validator(mode="after")
    def validate_subtotal_vs_lines(self) -> "Invoice":
        """Sum of line item amounts should equal subtotal."""
        line_sum = sum(item.amount for item in self.line_items)
        if abs(line_sum - self.subtotal) > Decimal("1.00"):
            raise ValueError(
                f"Subtotal {self.subtotal} doesn't match sum of line items {line_sum}"
            )
        return self

    @model_validator(mode="after")
    def validate_hsn_summary_cross_check(self) -> "Invoice":
        """If HSN summary table exists, its total tax must match invoice tax totals."""
        if not self.hsn_summary:
            return self
        hsn_tax_total = sum(row.total_tax_amount for row in self.hsn_summary)
        invoice_tax_total = self.cgst_total + self.sgst_total + self.igst_total + self.cess_total
        if abs(hsn_tax_total - invoice_tax_total) > Decimal("1.00"):
            raise ValueError(
                f"HSN summary tax {hsn_tax_total} doesn't match "
                f"invoice tax total {invoice_tax_total}"
            )
        return self


# ─── Extraction prompt output (what the AI returns) ──────────────────────────

class ExtractionResult(BaseModel):
    """
    Wrapper returned by the AI extraction pipeline.
    Contains the parsed invoice plus confidence metadata.
    """
    invoice: Invoice
    confidence: float = Field(..., ge=0.0, le=1.0, description="Overall extraction confidence")
    warnings: list[str] = Field(default_factory=list, description="Human-readable extraction issues")
    ocr_raw_text: Optional[str] = Field(None, description="Raw OCR text for cross-checking")


# ─── WhatsApp message schemas ────────────────────────────────────────────────

class WhatsAppIncoming(BaseModel):
    """Incoming webhook payload from Meta Cloud API (simplified)."""
    message_id: str
    from_phone: str = Field(..., description="Sender phone in E.164 format")
    timestamp: int
    message_type: str = Field(..., description="text | image | document")
    text_body: Optional[str] = None
    media_id: Optional[str] = None
    media_url: Optional[str] = None
    media_mime_type: Optional[str] = None


class WhatsAppOutgoing(BaseModel):
    """Outbound message to user via WhatsApp."""
    to_phone: str
    message_type: str = Field(default="text", description="text | template | interactive")
    body: Optional[str] = None
    template_name: Optional[str] = None
    template_params: Optional[list[str]] = None


# ─── Example: the uploaded invoice as data ───────────────────────────────────

EXAMPLE_INVOICE = Invoice(
    invoice_type=InvoiceType.TAX_SALES,
    invoice_number="SI0039",
    invoice_date=date(2026, 4, 14),
    seller=Party(
        name="SHIV INDUSTRIES",
        gstin="24AGCPD5109M1ZN",
        pan="AGCPD5109M",
        address=Address(
            line1="SURVEY NO. 309/1, B/H. GIDC, PHASE-IV",
            line2="NR. ASHAPURA ESTATE, RAMOL CHAWKDI",
            city="VATVA, AHMEDABAD",
            pin_code="382445",
            state_name="Gujarat",
            state_code=24,
        ),
    ),
    buyer=Party(
        name="J S ENTERPRISE",
        gstin="24AALFJ3424A1ZW",
        address=Address(
            line1="B-20, MARUTI INDUSTRIAL ESTATE",
            line2="B/H CHOKSI TUBE PHASE -1",
            city="GIDC VATVA AHMEDABAD",
            state_name="Gujarat",
            state_code=24,
        ),
    ),
    consignee=Party(
        name="J S ENTERPRISE",
        gstin="24AALFJ3424A1ZW",
        address=Address(
            line1="B-20, MARUTI INDUSTRIAL ESTATE",
            line2="B/H CHOKSI TUBE PHASE -1",
            city="GIDC VATVA AHMEDABAD",
            state_name="Gujarat",
            state_code=24,
        ),
    ),
    einvoice=EInvoiceMetadata(
        irn="924e3d74cf93d514933fec3167a454cc5fb5d2032ff48b934f7-c3f0b55f11bd6",
        ack_no="162624301474406",
        ack_date=date(2026, 4, 14),
        eway_bill_no="662095209582",
    ),
    dispatch=DispatchDetails(
        dispatch_doc_no="SI0039",
        dispatch_mode=DispatchMode.TRUCK,
        vehicle_no="GJ27TD1306",
        delivery_note="SI0039",
        delivery_note_date=date(2026, 4, 14),
    ),
    line_items=[
        LineItem(
            sl_no=1,
            description="FERROUS SULPHATE ETP GRADE",
            hsn_sac_code="28332910",
            quantity=Decimal("5315.00"),
            unit="kgs",
            rate=Decimal("13.50"),
            amount=Decimal("71752.50"),
            cgst_rate=Decimal("9"),
            cgst_amount=Decimal("6457.73"),
            sgst_rate=Decimal("9"),
            sgst_amount=Decimal("6457.73"),
        ),
    ],
    subtotal=Decimal("71752.50"),
    cgst_total=Decimal("6457.73"),
    sgst_total=Decimal("6457.73"),
    round_off=Decimal("0.04"),
    total_amount=Decimal("84668.00"),
    total_in_words="INR Eighty Four Thousand Six Hundred Sixty Eight Only",
    tax_amount_in_words="INR Twelve Thousand Nine Hundred Fifteen and Forty Six Paise Only",
    hsn_summary=[
        HSNSummary(
            hsn_sac_code="28332910",
            taxable_value=Decimal("71752.50"),
            cgst_rate=Decimal("9"),
            cgst_amount=Decimal("6457.73"),
            sgst_rate=Decimal("9"),
            sgst_amount=Decimal("6457.73"),
            total_tax_amount=Decimal("12915.46"),
        ),
    ],
    supply_type=SupplyType.INTRA_STATE,
    place_of_supply="Gujarat (24)",
    reverse_charge=False,
    company_pan="AGCPD5109M",
    extraction_confidence=0.97,
    status=InvoiceStatus.PENDING,
)
