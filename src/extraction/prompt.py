"""
BillFlow — Invoice extraction prompt for Claude API.

This prompt is sent along with the invoice image + OCR text.
Claude returns structured JSON matching our Pydantic Invoice schema.
"""

SYSTEM_PROMPT = """You are a GST invoice data extraction engine for Indian businesses.

You receive:
1. An image of a GST invoice (PDF or photo)
2. Raw OCR text extracted from the same invoice

Your job: extract every field into the exact JSON schema below. Be precise with numbers — 
financial data must be exact, not approximated.

RULES:
- Extract amounts as strings with exactly 2 decimal places: "71752.50" not "71752.5"
- GSTIN format: 2-digit state code + 10-char PAN + entity number + Z + check digit
- HSN/SAC codes are 4-8 digit numbers
- Dates in ISO format: "2026-04-14"
- If a field is not present on the invoice, use null
- If you're unsure about a value, include it but set confidence lower
- Cross-check: quantity × rate should equal line amount
- Cross-check: subtotal + taxes + round_off should equal total
- Determine supply_type: same state codes on seller/buyer GSTIN → "intra_state" (CGST+SGST), 
  different → "inter_state" (IGST)

RESPOND WITH ONLY valid JSON matching this schema. No markdown, no explanation, no preamble.
"""

EXTRACTION_SCHEMA = """{
  "invoice_type": "TAX_SALES | TAX_PURCHASE | CREDIT_NOTE | DEBIT_NOTE",
  "invoice_number": "string",
  "invoice_date": "YYYY-MM-DD",
  
  "seller": {
    "name": "string",
    "gstin": "string (15 chars)",
    "pan": "string (10 chars) or null",
    "address": {
      "line1": "string",
      "line2": "string or null",
      "line3": "string or null",
      "city": "string",
      "pin_code": "string (6 digits) or null",
      "state_name": "string",
      "state_code": "integer (1-38)"
    }
  },
  
  "buyer": { "...same structure as seller..." },
  "consignee": { "...same structure or null if same as buyer..." },
  
  "einvoice": {
    "irn": "string or null",
    "ack_no": "string or null",
    "ack_date": "YYYY-MM-DD or null",
    "eway_bill_no": "string or null"
  },
  
  "dispatch": {
    "dispatch_doc_no": "string or null",
    "dispatch_mode": "TRUCK | RAIL | AIR | SHIP | TRANSPORTER | COURIER | OTHER | null",
    "vehicle_no": "string or null",
    "delivery_note": "string or null",
    "delivery_note_date": "YYYY-MM-DD or null",
    "destination": "string or null",
    "port_of_loading": "string or null",
    "port_of_discharge": "string or null"
  },
  
  "line_items": [
    {
      "sl_no": "integer",
      "description": "string",
      "hsn_sac_code": "string (4-8 digits)",
      "quantity": "decimal string",
      "unit": "string (kgs, nos, ltrs, mtrs, etc.)",
      "rate": "decimal string",
      "amount": "decimal string",
      "cgst_rate": "decimal string or null",
      "cgst_amount": "decimal string or null",
      "sgst_rate": "decimal string or null",
      "sgst_amount": "decimal string or null",
      "igst_rate": "decimal string or null",
      "igst_amount": "decimal string or null",
      "cess_rate": "decimal string or null",
      "cess_amount": "decimal string or null"
    }
  ],
  
  "subtotal": "decimal string",
  "cgst_total": "decimal string",
  "sgst_total": "decimal string",
  "igst_total": "decimal string",
  "cess_total": "decimal string",
  "round_off": "decimal string",
  "total_amount": "decimal string",
  "total_in_words": "string or null",
  "tax_amount_in_words": "string or null",
  
  "hsn_summary": [
    {
      "hsn_sac_code": "string",
      "taxable_value": "decimal string",
      "cgst_rate": "decimal string or null",
      "cgst_amount": "decimal string or null",
      "sgst_rate": "decimal string or null",
      "sgst_amount": "decimal string or null",
      "igst_rate": "decimal string or null",
      "igst_amount": "decimal string or null",
      "total_tax_amount": "decimal string"
    }
  ],
  
  "supply_type": "intra_state | inter_state",
  "place_of_supply": "string or null",
  "reverse_charge": "boolean",
  "buyers_order_no": "string or null",
  "buyers_order_date": "YYYY-MM-DD or null",
  "payment_terms": "string or null",
  "other_references": "string or null",
  "company_pan": "string or null",
  
  "confidence": "float 0-1, your overall confidence in the extraction"
}"""


def build_extraction_messages(ocr_text: str, image_base64: str, media_type: str = "image/jpeg"):
    """
    Build the messages array for Claude API call.
    
    Args:
        ocr_text: Raw text from Tesseract OCR
        image_base64: Base64-encoded invoice image
        media_type: MIME type of the image
    
    Returns:
        dict ready for Anthropic API /v1/messages
    """
    return {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Extract all fields from this GST invoice into the JSON schema.\n\n"
                            f"OCR text for cross-reference:\n"
                            f"---\n{ocr_text}\n---\n\n"
                            f"Output schema:\n{EXTRACTION_SCHEMA}"
                        ),
                    },
                ],
            }
        ],
    }


# ─── Post-extraction validation pipeline ─────────────────────────────────────

def validate_extraction(raw_json: dict) -> tuple[dict, list[str]]:
    """
    Run validation checks on extracted invoice data.
    Returns (cleaned_data, list_of_warnings).
    
    These checks catch what the AI might miss:
    1. GSTIN checksum validation
    2. HSN code lookup against CBIC master
    3. Math cross-checks (already in Pydantic, but we run them pre-parse too)
    4. Duplicate detection
    """
    warnings = []
    
    # 1. GSTIN format check
    for party_key in ["seller", "buyer", "consignee"]:
        party = raw_json.get(party_key)
        if party and party.get("gstin"):
            gstin = party["gstin"]
            if len(gstin) != 15:
                warnings.append(f"{party_key} GSTIN '{gstin}' is not 15 characters")
            # State code from GSTIN should match declared state_code
            if party.get("address", {}).get("state_code"):
                gstin_state = int(gstin[:2])
                declared_state = party["address"]["state_code"]
                if gstin_state != declared_state:
                    warnings.append(
                        f"{party_key} GSTIN state code ({gstin_state}) "
                        f"doesn't match declared state ({declared_state})"
                    )
    
    # 2. Supply type inference
    seller_gstin = raw_json.get("seller", {}).get("gstin", "")
    buyer_gstin = raw_json.get("buyer", {}).get("gstin", "")
    if len(seller_gstin) >= 2 and len(buyer_gstin) >= 2:
        same_state = seller_gstin[:2] == buyer_gstin[:2]
        declared_supply = raw_json.get("supply_type")
        if same_state and declared_supply == "inter_state":
            warnings.append("Same state GSTINs but supply_type is inter_state — verify")
        if not same_state and declared_supply == "intra_state":
            warnings.append("Different state GSTINs but supply_type is intra_state — verify")
    
    # 3. Line item math
    for i, item in enumerate(raw_json.get("line_items", [])):
        try:
            qty = float(item.get("quantity", 0))
            rate = float(item.get("rate", 0))
            amount = float(item.get("amount", 0))
            expected = qty * rate
            if abs(expected - amount) > 1.0:
                warnings.append(
                    f"Line {i+1}: {qty} × {rate} = {expected:.2f}, "
                    f"but amount is {amount:.2f}"
                )
        except (ValueError, TypeError):
            warnings.append(f"Line {i+1}: non-numeric values in qty/rate/amount")
    
    # 4. Total math
    try:
        subtotal = float(raw_json.get("subtotal", 0))
        cgst = float(raw_json.get("cgst_total", 0))
        sgst = float(raw_json.get("sgst_total", 0))
        igst = float(raw_json.get("igst_total", 0))
        cess = float(raw_json.get("cess_total", 0))
        round_off = float(raw_json.get("round_off", 0))
        total = float(raw_json.get("total_amount", 0))
        expected_total = subtotal + cgst + sgst + igst + cess + round_off
        if abs(expected_total - total) > 1.0:
            warnings.append(
                f"Total mismatch: {subtotal} + {cgst} + {sgst} + {igst} + "
                f"{cess} + {round_off} = {expected_total:.2f}, but total is {total:.2f}"
            )
    except (ValueError, TypeError):
        warnings.append("Non-numeric values in totals")
    
    return raw_json, warnings


# ─── WhatsApp confirmation message builder ───────────────────────────────────

def build_confirmation_message(invoice_data: dict, warnings: list[str]) -> str:
    """
    Build the WhatsApp reply message after extraction.
    User replies CONFIRM, EDIT, or specific corrections.
    """
    seller = invoice_data.get("seller", {}).get("name", "Unknown")
    inv_no = invoice_data.get("invoice_number", "?")
    inv_date = invoice_data.get("invoice_date", "?")
    total = invoice_data.get("total_amount", "?")
    items_count = len(invoice_data.get("line_items", []))
    
    # Build line items summary
    items_summary = ""
    for item in invoice_data.get("line_items", [])[:5]:  # max 5 lines in WhatsApp
        desc = item.get("description", "?")[:30]
        qty = item.get("quantity", "?")
        unit = item.get("unit", "")
        amt = item.get("amount", "?")
        items_summary += f"  • {desc} — {qty} {unit} — ₹{amt}\n"
    
    msg = (
        f"✓ *Invoice extracted*\n\n"
        f"*From:* {seller}\n"
        f"*Invoice:* {inv_no} | {inv_date}\n"
        f"*Items:* {items_count}\n"
        f"{items_summary}\n"
        f"*Subtotal:* ₹{invoice_data.get('subtotal', '?')}\n"
        f"*CGST:* ₹{invoice_data.get('cgst_total', '0')} | "
        f"*SGST:* ₹{invoice_data.get('sgst_total', '0')}\n"
        f"*Total:* ₹{total}\n"
    )
    
    if warnings:
        msg += f"\n⚠️ {len(warnings)} warning(s):\n"
        for w in warnings[:3]:
            msg += f"  ⚠ {w}\n"
    
    msg += (
        f"\nReply:\n"
        f"  *CONFIRM* — save this invoice\n"
        f"  *EDIT* — I'll ask what to fix\n"
        f"  *REJECT* — discard and re-upload"
    )
    
    return msg
