"""
FastAPI application for Personal Health Expenses Management System.
"""

from fastapi import FastAPI, Depends, HTTPException, Request, Query, UploadFile, File, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from pathlib import Path
from io import BytesIO
from sqlalchemy import func

# EOB import in-memory store
try:
    from eob_import import create_session as eob_create_session, get_session as eob_get_session, delete_session as eob_delete_session
except ImportError:  # pragma: no cover
    from .eob_import import create_session as eob_create_session, get_session as eob_get_session, delete_session as eob_delete_session

# Handle imports whether running as module or script
try:
    from database_config import get_db
    from schemas.schemas import (
        Provider, ProviderCreate, ProviderUpdate,
        Charge, ChargeCreate, ChargeUpdate,
        Payment, PaymentCreate, PaymentUpdate,
        Premium, PremiumCreate, PremiumUpdate,
        Attachment
    )
    from crud.provider import provider_crud
    from crud.charge import charge_crud
    from crud.payment import payment_crud
    from crud.premium import premium_crud
    from crud.attachment import (
        create_attachment, list_attachments_for_charge, list_attachments_for_payment,
        get_attachment, delete_attachment
    )
except ImportError:
    # Fallback for when running as module
    from .database_config import get_db
    from .schemas.schemas import (
        Provider, ProviderCreate, ProviderUpdate,
        Charge, ChargeCreate, ChargeUpdate,
        Payment, PaymentCreate, PaymentUpdate,
        Premium, PremiumCreate, PremiumUpdate,
        Attachment
    )
    from .crud.provider import provider_crud
    from .crud.charge import charge_crud
    from .crud.payment import payment_crud
    from .crud.premium import premium_crud
    from .crud.attachment import (
        create_attachment, list_attachments_for_charge, list_attachments_for_payment,
        get_attachment, delete_attachment
    )

# Create FastAPI app
app = FastAPI(
    title="Personal Health Expenses Management System",
    description="A CRUD application for managing medical, dental, and vision expenses",
    version="1.0.0"
)

# Setup templates and static files
# Get the project root directory (parent of src)
project_root = Path(__file__).parent.parent
templates_dir = project_root / "templates"
static_dir = project_root / "static"
# Removed filesystem storage for attachments; binaries stored directly in DB now.

templates = Jinja2Templates(directory=str(templates_dir))
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Register custom Jinja2 filters
def format_currency(value):
    try:
        if value is None:
            return "$0.00"
        # Convert Decimals / Numerics to float for formatting
        number = float(value)
        return f"${number:,.2f}"
    except Exception:
        return "$0.00"

templates.env.filters["currency"] = format_currency

# Unified import helper for PaymentModel to satisfy static analysis
def _get_payment_model():  # pragma: no cover simple helper
    try:
        from models.database import Payment as _PM  # type: ignore
    except ImportError:
        from .models.database import Payment as _PM  # type: ignore
    return _PM

PaymentModel = _get_payment_model()

# --------------------------- Attachment Endpoints ---------------------------
@app.post("/api/attachments/upload", response_model=Attachment)
async def upload_attachment(
    charge_id: int | None = Form(None),
    payment_id: int | None = Form(None),
    notes: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Upload a file attachment for either a charge OR a payment (exactly one) storing binary in DB."""
    if (charge_id is None and payment_id is None) or (charge_id is not None and payment_id is not None):
        raise HTTPException(status_code=400, detail="Provide exactly one of charge_id or payment_id")

    original_name = file.filename or "uploaded_file"
    content_type = file.content_type or "application/octet-stream"
    # Read entire file into memory (acceptable for expected small medical receipts). Could stream if needed.
    try:
        content_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read uploaded file: {e}")
    file_size = len(content_bytes)
    from schemas.schemas import AttachmentCreate as _AttachmentCreate  # type: ignore
    att_in = _AttachmentCreate(charge_id=charge_id, payment_id=payment_id, notes=notes)
    try:
        att = create_attachment(
            db,
            data=att_in,
            original_filename=original_name,
            content_type=content_type,
            file_size=file_size,
            content_bytes=content_bytes,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return att


@app.get("/api/charges/{charge_id}/attachments", response_model=list[Attachment])
def list_charge_attachments(charge_id: int, db: Session = Depends(get_db)):
    return list_attachments_for_charge(db, charge_id)


@app.get("/api/payments/{payment_id}/attachments", response_model=list[Attachment])
def list_payment_attachments(payment_id: int, db: Session = Depends(get_db)):
    return list_attachments_for_payment(db, payment_id)


@app.get("/api/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int, db: Session = Depends(get_db)):
    att = get_attachment(db, attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    media_type = getattr(att, 'content_type', None) or "application/octet-stream"
    download_name = getattr(att, 'original_filename', None) or f"attachment_{attachment_id}"
    data = getattr(att, 'content', None)
    if data is None:
        raise HTTPException(status_code=500, detail="Attachment content missing")
    return StreamingResponse(BytesIO(data), media_type=str(media_type), headers={
        "Content-Disposition": f"attachment; filename=\"{download_name}\""
    })


@app.delete("/api/attachments/{attachment_id}")
def remove_attachment(attachment_id: int, db: Session = Depends(get_db)):
    att = get_attachment(db, attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    delete_attachment(db, attachment_id)
    return {"message": "Attachment deleted"}

@app.get("/api/attachments/list")
def list_attachments_endpoint(
    charge_id: int | None = Query(None),
    payment_id: int | None = Query(None),
    db: Session = Depends(get_db)
):
    """Return attachments for either a charge or a payment (JSON).
    Exactly one of charge_id or payment_id must be provided.
    """
    if bool(charge_id) == bool(payment_id):  # both set or both None
        raise HTTPException(status_code=400, detail="Provide exactly one of charge_id or payment_id")
    try:
        if charge_id is not None:
            items = list_attachments_for_charge(db, charge_id)
        else:
            items = list_attachments_for_payment(db, payment_id)  # type: ignore[arg-type]
        return {"attachments": [
            {
                "id": a.id,
                "original_filename": a.original_filename,
                "file_size": a.file_size,
                "notes": a.notes,
                "charge_id": a.charge_id,
                "payment_id": a.payment_id,
            }
            for a in items
        ]}
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Error listing attachments: {ex}")



# Root endpoint
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Home page."""
    return templates.TemplateResponse("index.html", {"request": request})


# Provider endpoints
@app.post("/api/providers/", response_model=Provider)
def create_provider(provider: ProviderCreate, db: Session = Depends(get_db)):
    """Create a new provider (enforces case-insensitive uniqueness on provider_name)."""
    existing = db.query(provider_crud.model).filter(
        provider_crud.model.provider_name.ilike(provider.provider_name.strip())
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Provider name already exists")
    return provider_crud.create(db, provider.model_dump())


@app.get("/api/providers/exists")
def provider_exists(name: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    """Lightweight existence check for provider name (case-insensitive)."""
    normalized = name.strip()
    if not normalized:
        return {"exists": False}
    exists = db.query(provider_crud.model.id).filter(
        provider_crud.model.provider_name.ilike(normalized)
    ).first() is not None
    return {"exists": exists}


@app.get("/api/providers/", response_model=List[Provider])
def read_providers(skip: int = 0, limit: Optional[int] = None, db: Session = Depends(get_db)):
    """Get all providers."""
    return provider_crud.get_all(db, skip=skip, limit=limit)


@app.get("/api/providers/{provider_id}", response_model=Provider)
def read_provider(provider_id: int, db: Session = Depends(get_db)):
    """Get a provider by ID."""
    provider = provider_crud.get(db, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider


@app.put("/api/providers/{provider_id}", response_model=Provider)
def update_provider(provider_id: int, provider: ProviderUpdate, db: Session = Depends(get_db)):
    """Update a provider."""
    db_provider = provider_crud.get(db, provider_id)
    if db_provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider_crud.update(db, db_provider, provider.model_dump(exclude_unset=True))


@app.delete("/api/providers/{provider_id}")
def delete_provider(provider_id: int, db: Session = Depends(get_db)):
    """Delete a provider and cascade delete related charges, payments, attachments, and xrefs.
    This is an application-level cascade to keep schema simpler.
    """
    try:
        from models.database import Provider as PModel, Charge as CModel, Payment as PayModel, Attachment as AttModel, ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:
        from .models.database import Provider as PModel, Charge as CModel, Payment as PayModel, Attachment as AttModel, ChargePaymentXref as XrefModel  # type: ignore
    provider = db.query(PModel).filter(PModel.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    # Collect related charges & payments
    charge_ids = [cid for (cid,) in db.query(CModel.id).filter(CModel.provider_id == provider_id).all()]
    payment_ids = [pid for (pid,) in db.query(PayModel.id).filter(PayModel.provider_id == provider_id).all()]
    # Delete attachments tied to those charges/payments
    if charge_ids:
        try:
            db.query(AttModel).filter(AttModel.charge_id.in_(charge_ids)).delete(synchronize_session=False)
        except Exception:
            pass
    if payment_ids:
        try:
            db.query(AttModel).filter(AttModel.payment_id.in_(payment_ids)).delete(synchronize_session=False)
        except Exception:
            pass
    # Delete xrefs referencing those charges or payments
    if charge_ids or payment_ids:
        try:
            q = db.query(XrefModel)
            if charge_ids:
                q = q.filter(XrefModel.charge_id.in_(charge_ids))
            if payment_ids:
                q = q.union(db.query(XrefModel).filter(XrefModel.payment_id.in_(payment_ids)))  # simple union approach
            # Simpler: delete all xrefs where either side is in sets
            db.query(XrefModel).filter(
                (XrefModel.charge_id.in_(charge_ids)) | (XrefModel.payment_id.in_(payment_ids))
            ).delete(synchronize_session=False)
        except Exception:
            pass
    # Delete charges then payments
    if charge_ids:
        try:
            db.query(CModel).filter(CModel.id.in_(charge_ids)).delete(synchronize_session=False)
        except Exception:
            pass
    if payment_ids:
        try:
            db.query(PayModel).filter(PayModel.id.in_(payment_ids)).delete(synchronize_session=False)
        except Exception:
            pass
    # Finally delete provider
    db.delete(provider)
    db.commit()
    return {"message": "Provider and related entities deleted"}


@app.get("/api/providers/active/", response_model=List[Provider])
def read_active_providers(db: Session = Depends(get_db)):
    """Get all active providers."""
    return provider_crud.get_active_providers(db)


@app.get("/api/provider-groups")
def list_provider_groups(q: Optional[str] = Query(None, description="Optional case-insensitive substring filter"), db: Session = Depends(get_db)):
    """Return distinct provider_group values (excluding NULL/empty)."""
    Model = provider_crud.model
    # Build base query selecting the raw column, then apply DISTINCT at the ORM level.
    query = db.query(Model.provider_group).filter(
        Model.provider_group.isnot(None),
        func.length(func.trim(Model.provider_group)) > 0
    )
    if q:
        like_pattern = f"%{q.strip()}%"
        query = query.filter(Model.provider_group.ilike(like_pattern))
    # Apply DISTINCT without ORDER BY to satisfy PostgreSQL; then sort in Python case-insensitively.
    raw_groups = [row[0] for row in query.distinct().all()]
    # Normalize whitespace (trim) and drop accidental duplicates after trimming.
    normalized = []
    seen = set()
    for g in raw_groups:
        if g is None:
            continue
        trimmed = g.strip()
        low = trimmed.lower()
        if low not in seen:
            seen.add(low)
            normalized.append(trimmed)
    normalized.sort(key=lambda s: s.lower())
    return {"groups": normalized}


@app.get("/api/cpt-codes")
def list_cpt_codes(q: Optional[str] = Query(None, description="Optional case-insensitive substring filter"), db: Session = Depends(get_db)):
    """Return distinct CPT codes from charges for autocomplete (excluding NULL/empty).

    Query params:
    - q: optional substring filter (case-insensitive)
    """
    try:
        from models.database import Charge as ChargeModel  # type: ignore
    except ImportError:
        from .models.database import Charge as ChargeModel  # type: ignore

    query = db.query(ChargeModel.cpt_code).filter(
        ChargeModel.cpt_code.isnot(None),
        func.length(func.trim(ChargeModel.cpt_code)) > 0
    )
    if q:
        like_pattern = f"%{q.strip()}%"
        query = query.filter(ChargeModel.cpt_code.ilike(like_pattern))

    raw_codes = [row[0] for row in query.distinct().all()]
    # Normalize (trim + uppercase for uniqueness while preserving original format decision: keep original case returned)
    normalized = []
    seen = set()
    for c in raw_codes:
        if c is None:
            continue
        trimmed = c.strip()
        key = trimmed.lower()
        if key not in seen:
            seen.add(key)
            normalized.append(trimmed)
    normalized.sort(key=lambda s: s.lower())
    return {"cpt_codes": normalized}


# --------------------------- Charge Import Placeholder Pages ---------------------------
@app.get("/charges/import/eob", response_class=HTMLResponse)
def import_charges_from_eob_page(request: Request):
    """Placeholder page for future EOB-based bulk charge import."""
    return templates.TemplateResponse("import_charges_eob.html", {"request": request})


@app.get("/charges/import/invoice", response_class=HTMLResponse)
def import_charges_from_invoice_page(request: Request):
    """Placeholder page for future invoice-based bulk charge import."""
    return templates.TemplateResponse("import_charges_invoice.html", {"request": request})


# --------------------------- EOB Import API (MVP Stub) ---------------------------
class EOBUploadResponse(BaseModel):
    session_id: str
    row_count: int
    filename: str


@app.post("/api/eob/upload", response_model=EOBUploadResponse)
async def eob_upload(file: UploadFile = File(...)):
    """Accept an EOB file and create a parsing session (stub parser)."""
    content = await file.read()
    session = eob_create_session(file.filename or "eob_upload", content, file.content_type)
    data = session.serialize()
    return EOBUploadResponse(session_id=session.id, row_count=data["row_count"], filename=data["filename"])


@app.get("/api/eob/session/{session_id}")
def eob_get(session_id: str):
    session = eob_get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.serialize()


class EOBImportRequest(BaseModel):
    selected_indices: list[int]
    provider_id: int | None = None
    overrides: list[dict] | None = None

class EOBImportResult(BaseModel):
    imported_count: int
    created_charge_ids: list[int]


@app.post("/api/eob/session/{session_id}/import", response_model=EOBImportResult)
def eob_import(session_id: str, payload: EOBImportRequest, db: Session = Depends(get_db)):
    session = eob_get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if payload.provider_id is None:
        raise HTTPException(status_code=400, detail="provider_id is required for EOB import")
    # Map indices to rows
    sel_set = set(payload.selected_indices)
    rows = [r for r in session.rows if r.index in sel_set]
    overrides_map = {}
    if payload.overrides:
        for ov in payload.overrides:
            try:
                idx_val = ov.get("index")
                if idx_val is None:
                    continue
                overrides_map[int(idx_val)] = ov
            except Exception:
                continue
    created_ids: list[int] = []
    highest_row = None
    highest_amount = -1.0
    for r in rows:
        amt = (overrides_map.get(r.index, {}).get("charged_amount")
               if r.index in overrides_map else r.charged_amount) or 0
        if amt > highest_amount:
            highest_amount = amt
            highest_row = r.index
    for r in rows:
        try:
            ov = overrides_map.get(r.index, {})
            # Allow override of service_date (ISO date) & cpt_code
            ov_service_date = ov.get("service_date") if ov.get("service_date") else (r.service_date.isoformat() if r.service_date else None)
            charge_in = {
                "service_date": ov_service_date,
                "family_member": ov.get("family_member", r.family_member),
                "medical_type": "Medical",
                "provider_id": payload.provider_id,
                "cpt_code": ov.get("cpt_code", r.cpt_code),
                "charge_description": ov.get("description", r.description),
                "charged_amount": ov.get("charged_amount", r.charged_amount),
                "owed_amount": ov.get("owed_amount", r.owed_amount),
                "insurance_paid_amount": ov.get("insurance_paid_amount", getattr(r, "insurance_paid_amount", None)),
                "insurance_discount_amount": ov.get("insurance_discount_amount", getattr(r, "insurance_discount_amount", None)),
                "base_fee_amount": ov.get("base_fee_amount", getattr(r, "base_fee_amount", None)),
                "reimbursement_amount": ov.get("reimbursement_amount", getattr(r, "reimbursement_amount", None)),
                "doctor_name": None,
                "eob_claim_num": None,
                "notes": None,
            }
            # Add notes referencing where the EOB attachment will live if not the highest charge
            if highest_row is not None and r.index != highest_row:
                charge_in["notes"] = "EOB file attached to highest charge in this batch"
            created = charge_crud.create(db, charge_in)
            try:
                raw_id = getattr(created, "id", None)
                if raw_id is None:
                    continue
                created_ids.append(int(raw_id))
            except Exception:
                continue
        except Exception:  # pragma: no cover simple robustness
            continue
    db.commit()
    # Attach original EOB file to highest charge if available
    if highest_row is not None and session.file_bytes and created_ids:
        try:
            # Find which created charge corresponds to highest_row (order of rows iteration preserved)
            # We re-loop rows and mapping of created_ids in same order
            highest_created_id = None
            idx_tracker = 0
            for r in rows:
                if getattr(r, 'index', None) in sel_set:
                    # created_ids appended in order of rows loop
                    if r.index == highest_row:
                        highest_created_id = created_ids[idx_tracker]
                        break
                    idx_tracker += 1
            if highest_created_id is not None:
                from schemas.schemas import AttachmentCreate as _AttachmentCreate  # type: ignore
                att_in = _AttachmentCreate(charge_id=highest_created_id, payment_id=None, notes="Original EOB document")
                create_attachment(
                    db,
                    data=att_in,
                    original_filename=session.filename,
                    content_type=session.content_type or "application/octet-stream",
                    file_size=len(session.file_bytes),
                    content_bytes=session.file_bytes,
                )
                db.commit()
        except Exception:
            pass
    # One-shot: remove session after import to reduce memory footprint
    eob_delete_session(session_id)
    return EOBImportResult(imported_count=len(created_ids), created_charge_ids=created_ids)


@app.get("/api/providers/{provider_id}/open-charges")
def list_open_charges(provider_id: int, db: Session = Depends(get_db)):
    """Return charges for a provider that are not fully paid (remaining > 0 after allocated shares)."""
    try:
        from models.database import Charge as ChargeModel, ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:
        from .models.database import Charge as ChargeModel, ChargePaymentXref as XrefModel  # type: ignore

    charges = db.query(ChargeModel).filter(ChargeModel.provider_id == provider_id).all()
    charge_ids = [c.id for c in charges]
    allocated_map = {cid: 0.0 for cid in charge_ids}
    if charge_ids:
        xrefs = db.query(XrefModel.charge_id, XrefModel.payment_id).filter(XrefModel.charge_id.in_(charge_ids)).all()
        if xrefs:
            payment_ids = {pid for _, pid in xrefs}
            payments_lookup = {}
            for p in db.query(PaymentModel).filter(PaymentModel.id.in_(payment_ids)).all():
                raw_amt = getattr(p, 'payment_amount', None)
                try:
                    payments_lookup[p.id] = float(raw_amt) if raw_amt is not None else 0.0
                except Exception:
                    payments_lookup[p.id] = 0.0
            payment_to_charges = {}
            for cid, pid in xrefs:
                payment_to_charges.setdefault(pid, []).append(cid)
            for pid, c_list in payment_to_charges.items():
                amount = payments_lookup.get(pid, 0.0)
                if not c_list:
                    continue
                share = amount / len(c_list) if len(c_list) > 0 else 0.0
                for cid in c_list:
                    allocated_map[cid] = allocated_map.get(cid, 0.0) + share
    open_result = []
    for c in charges:
        owed_raw = getattr(c, 'owed_amount', None)
        try:
            owed = float(owed_raw) if owed_raw is not None else 0.0
        except Exception:
            owed = 0.0
        paid = allocated_map.get(c.id, 0.0)
        remaining = max(owed - paid, 0.0)
        if remaining > 0.0001:  # treat tiny remainders as zero
            open_result.append({
                "id": c.id,
                "service_date": c.service_date.isoformat() if getattr(c, 'service_date', None) is not None else None,
                "description": c.charge_description,
                "remaining": remaining,
                "owed": owed,
                "paid_allocated": paid,
            })
    # Sort by service_date desc then remaining desc
    open_result.sort(key=lambda r: (r['service_date'] or '', -r['remaining']), reverse=True)
    return {"open_charges": open_result}


# --------------------------- Apply Payment to Charge ---------------------------
class ApplyPaymentPayload(BaseModel):
    """Payload for linking or creating a payment and applying it to a charge.

    Provide either payment_id (link existing) OR creation fields to make a new payment.
    Required creation fields: payment_amount, provider_id, expense_type.
    """
    payment_id: Optional[int] = None
    # New payment fields
    payment_date: Optional[str] = None
    payment_amount: Optional[float] = None
    expense_type: Optional[str] = None
    provider_id: Optional[int] = None
    has_receipt_indicator: Optional[str] = None
    reimbursement_amount: Optional[float] = None
    notes: Optional[str] = None


@app.post("/api/charges/{charge_id}/apply-payment")
def apply_payment_to_charge(charge_id: int, payload: ApplyPaymentPayload, db: Session = Depends(get_db)):
    """Apply a payment to a charge (link existing or create new then link)."""
    try:
        from models.database import Charge as ChargeModel, ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:
        from .models.database import Charge as ChargeModel, ChargePaymentXref as XrefModel  # type: ignore

    charge_obj = db.query(ChargeModel).filter(ChargeModel.id == charge_id).first()
    if not charge_obj:
        raise HTTPException(status_code=404, detail="Charge not found")

    creating_new = payload.payment_id is None
    if creating_new:
        missing = []
        if payload.payment_amount is None:
            missing.append("payment_amount")
        if payload.provider_id is None:
            missing.append("provider_id")
        if not payload.expense_type:
            missing.append("expense_type")
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing required fields for new payment: {', '.join(missing)}")
        payment_data = {
            "payment_date": payload.payment_date,
            "payment_amount": payload.payment_amount,
            "expense_type": payload.expense_type,
            "provider_id": payload.provider_id,
            "has_receipt_indicator": payload.has_receipt_indicator,
            "reimbursement_amount": payload.reimbursement_amount,
            "notes": payload.notes,
        }
        payment_obj = payment_crud.create(db, payment_data)
    else:
        payment_obj = db.query(PaymentModel).filter(PaymentModel.id == payload.payment_id).first()
        if not payment_obj:
            raise HTTPException(status_code=404, detail="Payment not found")

    # Ensure xref exists
    existing = db.query(XrefModel).filter(
        XrefModel.charge_id == charge_id,
        XrefModel.payment_id == payment_obj.id
    ).first()
    if not existing:
        db.add(XrefModel(charge_id=charge_id, payment_id=payment_obj.id))
        db.commit()
    else:
        db.commit()

    from sqlalchemy import func as _func
    paid_total = db.query(_func.coalesce(_func.sum(PaymentModel.payment_amount), 0)).join(
        XrefModel, XrefModel.payment_id == PaymentModel.id
    ).filter(XrefModel.charge_id == charge_id).scalar() or 0

    return {
        "charge_id": charge_id,
        "payment_id": payment_obj.id,
        "paid_total": float(paid_total),
        "created_new": creating_new,
        "linked": True
    }


class PaymentWithChargesPayload(BaseModel):
    payment_date: Optional[str] = None
    payment_amount: float
    expense_type: str
    provider_id: int
    has_receipt_indicator: Optional[str] = None
    reimbursement_amount: Optional[float] = None
    notes: Optional[str] = None
    charge_ids: List[int] = []


@app.post("/api/payments/with-charges")
def create_payment_with_charges(payload: PaymentWithChargesPayload, db: Session = Depends(get_db)):
    """Create a payment and link it to selected charges (xref rows)."""
    try:
        from models.database import Charge as ChargeModel, ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:
        from .models.database import Charge as ChargeModel, ChargePaymentXref as XrefModel  # type: ignore

    payment_data = {
        "payment_date": payload.payment_date,
        "payment_amount": payload.payment_amount,
        "expense_type": payload.expense_type,
        "provider_id": payload.provider_id,
        "has_receipt_indicator": payload.has_receipt_indicator,
        "reimbursement_amount": payload.reimbursement_amount,
        "notes": payload.notes,
    }
    payment_obj = payment_crud.create(db, payment_data)

    # Validate and create xrefs
    if payload.charge_ids:
        existing_charges = {c.id for c in db.query(ChargeModel.id).filter(ChargeModel.id.in_(payload.charge_ids)).all()}
        for cid in payload.charge_ids:
            if cid in existing_charges:
                x = XrefModel(charge_id=cid, payment_id=payment_obj.id)
                db.add(x)
        db.commit()
    else:
        db.commit()

    return {
        "payment_id": payment_obj.id,
        "linked_charge_ids": payload.charge_ids,
        "charge_link_count": len(payload.charge_ids),
    }


# Charge endpoints
@app.post("/api/charges/", response_model=Charge)
def create_charge(charge: ChargeCreate, db: Session = Depends(get_db)):
    """Create a new charge."""
    charge_data = charge.model_dump()
    # Convert enum values to strings for database storage
    if charge_data.get('family_member'):
        charge_data['family_member'] = charge_data['family_member'].value if hasattr(charge_data['family_member'], 'value') else charge_data['family_member']
    if charge_data.get('medical_type'):
        charge_data['medical_type'] = charge_data['medical_type'].value if hasattr(charge_data['medical_type'], 'value') else charge_data['medical_type']
    # Normalize CPT code: treat empty or placeholder '???' as None
    cpt = charge_data.get('cpt_code')
    if cpt is not None:
        cpt_norm = cpt.strip()
        if not cpt_norm or cpt_norm.lower() in {'???', 'na', 'n/a'}:
            charge_data['cpt_code'] = None
        else:
            charge_data['cpt_code'] = cpt_norm
    return charge_crud.create(db, charge_data)


@app.get("/api/charges/", response_model=List[Charge])
def read_charges(skip: int = 0, limit: Optional[int] = None, db: Session = Depends(get_db)):
    """Get all charges."""
    return charge_crud.get_all(db, skip=skip, limit=limit)


@app.get("/api/charges/{charge_id}", response_model=Charge)
def read_charge(charge_id: int, db: Session = Depends(get_db)):
    """Get a charge by ID."""
    charge = charge_crud.get(db, charge_id)
    if charge is None:
        raise HTTPException(status_code=404, detail="Charge not found")
    return charge


@app.put("/api/charges/{charge_id}", response_model=Charge)
def update_charge(charge_id: int, charge: ChargeUpdate, db: Session = Depends(get_db)):
    """Update a charge."""
    db_charge = charge_crud.get(db, charge_id)
    if db_charge is None:
        raise HTTPException(status_code=404, detail="Charge not found")
    charge_data = charge.model_dump(exclude_unset=True)
    # Convert enum values to strings for database storage
    if charge_data.get('family_member'):
        charge_data['family_member'] = charge_data['family_member'].value if hasattr(charge_data['family_member'], 'value') else charge_data['family_member']
    if charge_data.get('medical_type'):
        charge_data['medical_type'] = charge_data['medical_type'].value if hasattr(charge_data['medical_type'], 'value') else charge_data['medical_type']
    if 'cpt_code' in charge_data:
        cpt = charge_data.get('cpt_code')
        if cpt is None:
            pass
        else:
            cpt_norm = cpt.strip()
            if not cpt_norm or cpt_norm.lower() in {'???', 'na', 'n/a'}:
                charge_data['cpt_code'] = None
            else:
                charge_data['cpt_code'] = cpt_norm
    return charge_crud.update(db, db_charge, charge_data)


@app.delete("/api/charges/{charge_id}")
def delete_charge(charge_id: int, db: Session = Depends(get_db)):
    """Delete a charge and any associated attachments and payment xrefs."""
    try:
        from models.database import Attachment as _AttachmentModel, ChargePaymentXref as _Xref  # type: ignore
    except ImportError:
        from .models.database import Attachment as _AttachmentModel, ChargePaymentXref as _Xref  # type: ignore
    # Ensure charge exists first
    ch_obj = charge_crud.get(db, charge_id)
    if ch_obj is None:
        raise HTTPException(status_code=404, detail="Charge not found")
    # Delete attachments linked to this charge
    try:
        db.query(_AttachmentModel).filter(_AttachmentModel.charge_id == charge_id).delete(synchronize_session=False)
    except Exception:
        pass
    # Delete xref links to payments (so allocation recalculation won't break)
    try:
        db.query(_Xref).filter(_Xref.charge_id == charge_id).delete(synchronize_session=False)
    except Exception:
        pass
    # Delete the charge itself
    db.delete(ch_obj)
    db.commit()
    return {"message": "Charge and related attachments deleted"}


@app.get("/api/charges/family/{family_member}", response_model=List[Charge])
def read_charges_by_family_member(family_member: str, db: Session = Depends(get_db)):
    """Get charges by family member."""
    return charge_crud.get_by_family_member(db, family_member)


@app.get("/api/charges/unpaid/", response_model=List[Charge])
def read_unpaid_charges(db: Session = Depends(get_db)):
    """Get charges with outstanding amount owed."""
    return charge_crud.get_unpaid_charges(db)


# Payment endpoints
@app.post("/api/payments/", response_model=Payment)
def create_payment(payment: PaymentCreate, db: Session = Depends(get_db)):
    """Create a new payment."""
    payment_data = payment.model_dump()
    # Convert enum values to strings for database storage
    if payment_data.get('expense_type'):
        payment_data['expense_type'] = payment_data['expense_type'].value if hasattr(payment_data['expense_type'], 'value') else payment_data['expense_type']
    return payment_crud.create(db, payment_data)


@app.get("/api/payments/", response_model=List[Payment])
def read_payments(skip: int = 0, limit: Optional[int] = None, db: Session = Depends(get_db)):
    """Get all payments."""
    return payment_crud.get_all(db, skip=skip, limit=limit)


@app.get("/api/payments/{payment_id}", response_model=Payment)
def read_payment(payment_id: int, db: Session = Depends(get_db)):
    """Get a payment by ID."""
    payment = payment_crud.get(db, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


@app.put("/api/payments/{payment_id}", response_model=Payment)
def update_payment(payment_id: int, payment: PaymentUpdate, db: Session = Depends(get_db)):
    """Update a payment."""
    db_payment = payment_crud.get(db, payment_id)
    if db_payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    payment_data = payment.model_dump(exclude_unset=True)
    # Convert enum values to strings for database storage
    if payment_data.get('expense_type'):
        payment_data['expense_type'] = payment_data['expense_type'].value if hasattr(payment_data['expense_type'], 'value') else payment_data['expense_type']
    return payment_crud.update(db, db_payment, payment_data)


@app.delete("/api/payments/{payment_id}")
def delete_payment(payment_id: int, db: Session = Depends(get_db)):
    """Delete a payment and any associated attachments and xrefs."""
    try:
        from models.database import Attachment as _AttachmentModel, ChargePaymentXref as _Xref  # type: ignore
    except ImportError:
        from .models.database import Attachment as _AttachmentModel, ChargePaymentXref as _Xref  # type: ignore
    pay_obj = payment_crud.get(db, payment_id)
    if pay_obj is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    # Delete attachments for this payment
    try:
        db.query(_AttachmentModel).filter(_AttachmentModel.payment_id == payment_id).delete(synchronize_session=False)
    except Exception:
        pass
    # Delete xref links (payments to charges)
    try:
        db.query(_Xref).filter(_Xref.payment_id == payment_id).delete(synchronize_session=False)
    except Exception:
        pass
    db.delete(pay_obj)
    db.commit()
    return {"message": "Payment and related attachments deleted"}


# Premium endpoints
@app.post("/api/premiums/", response_model=Premium)
def create_premium(premium: PremiumCreate, db: Session = Depends(get_db)):
    """Create a new premium."""
    # Enforce uniqueness on year (prevent duplicate records for same year)
    existing = premium_crud.get_by_year(db, premium.year)
    if existing:
        raise HTTPException(status_code=400, detail=f"Premium already exists for year {premium.year}")
    return premium_crud.create(db, premium.model_dump())


@app.get("/api/premiums/", response_model=List[Premium])
def read_premiums(skip: int = 0, limit: Optional[int] = None, db: Session = Depends(get_db)):
    """Get all premiums."""
    return premium_crud.get_all(db, skip=skip, limit=limit)


@app.get("/api/premiums/{premium_id}", response_model=Premium)
def read_premium(premium_id: int, db: Session = Depends(get_db)):
    """Get a premium by ID."""
    premium = premium_crud.get(db, premium_id)
    if premium is None:
        raise HTTPException(status_code=404, detail="Premium not found")
    return premium


@app.get("/api/premiums/year/{year}", response_model=Premium)
def read_premium_by_year(year: int, db: Session = Depends(get_db)):
    """Get premium by year."""
    premium = premium_crud.get_by_year(db, year)
    if premium is None:
        raise HTTPException(status_code=404, detail="Premium not found for this year")
    return premium


@app.put("/api/premiums/{premium_id}", response_model=Premium)
def update_premium(premium_id: int, premium: PremiumUpdate, db: Session = Depends(get_db)):
    """Update a premium."""
    db_premium = premium_crud.get(db, premium_id)
    if db_premium is None:
        raise HTTPException(status_code=404, detail="Premium not found")
    return premium_crud.update(db, db_premium, premium.model_dump(exclude_unset=True))


@app.delete("/api/premiums/{premium_id}")
def delete_premium(premium_id: int, db: Session = Depends(get_db)):
    """Delete a premium."""
    success = premium_crud.delete(db, premium_id)
    if not success:
        raise HTTPException(status_code=404, detail="Premium not found")
    return {"message": "Premium deleted successfully"}


# Web interface endpoints
@app.get("/providers", response_class=HTMLResponse)
async def providers_page(request: Request, db: Session = Depends(get_db)):
    """Providers management page with financial aggregates (charges, payments, net)."""
    from sqlalchemy import func
    try:
        from models.database import Provider as ProviderModel, Charge as ChargeModel, Payment as PaymentModel  # type: ignore
    except ImportError:
        from .models.database import Provider as ProviderModel, Charge as ChargeModel, Payment as PaymentModel  # type: ignore

    # Aggregate in two subqueries to avoid row multiplication when both charges & payments exist (fan-out issue)
    charges_subq = (
        db.query(
            ChargeModel.provider_id.label("provider_id"),
            func.coalesce(func.sum(ChargeModel.charged_amount), 0).label("total_charges"),
            func.coalesce(func.sum(ChargeModel.owed_amount), 0).label("total_owed")
        )
        .group_by(ChargeModel.provider_id)
        .subquery()
    )
    payments_subq = (
        db.query(
            PaymentModel.provider_id.label("provider_id"),
            func.coalesce(func.sum(PaymentModel.payment_amount), 0).label("total_payments")
        )
        .group_by(PaymentModel.provider_id)
        .subquery()
    )

    query = (
        db.query(
            ProviderModel,
            func.coalesce(charges_subq.c.total_owed, 0).label("total_owed"),
            func.coalesce(payments_subq.c.total_payments, 0).label("total_payments")
        )
        .outerjoin(charges_subq, charges_subq.c.provider_id == ProviderModel.id)
        .outerjoin(payments_subq, payments_subq.c.provider_id == ProviderModel.id)
        .order_by(ProviderModel.provider_name)
    )

    providers_with_financials = []
    for provider_obj, total_owed, total_payments in query.all():
        total_owed_f = float(total_owed or 0)
        total_payments_f = float(total_payments or 0)
        net_amount = total_owed_f - total_payments_f  # net now based on owed vs payments
        providers_with_financials.append({
            "id": provider_obj.id,
            "provider_name": provider_obj.provider_name,
            "provider_group": provider_obj.provider_group,
            "provider_address": provider_obj.provider_address,
            "one_way_miles": provider_obj.one_way_miles,
            "active_provider_flag": provider_obj.active_provider_flag,
            "total_owed": total_owed_f,
            "total_payments": total_payments_f,
            "net_amount": net_amount,
        })

    return templates.TemplateResponse("providers.html", {"request": request, "providers": providers_with_financials})


@app.get("/providers/{provider_id}", response_class=HTMLResponse)
async def provider_detail_page(provider_id: int, request: Request, db: Session = Depends(get_db)):
    """Detailed page for a single provider including summary, charges, payments, and optional map."""
    try:
        from models.database import Provider as ProviderModel, Charge as ChargeModel, Payment as PaymentModel  # type: ignore
    except ImportError:
        from .models.database import Provider as ProviderModel, Charge as ChargeModel, Payment as PaymentModel  # type: ignore

    provider = db.query(ProviderModel).filter(ProviderModel.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    # Charges aggregation
    charge_totals = db.query(
        func.coalesce(func.count(ChargeModel.id), 0),
        func.coalesce(func.sum(ChargeModel.charged_amount), 0),
        func.coalesce(func.sum(ChargeModel.owed_amount), 0),
        func.coalesce(func.sum(ChargeModel.insurance_paid_amount), 0)
    ).filter(ChargeModel.provider_id == provider_id).one()
    (charge_count, total_charged, total_owed, total_ins_paid) = charge_totals

    # Payments aggregation
    payment_totals = db.query(
        func.coalesce(func.count(PaymentModel.id), 0),
        func.coalesce(func.sum(PaymentModel.payment_amount), 0),
        func.coalesce(func.sum(PaymentModel.reimbursement_amount), 0)
    ).filter(PaymentModel.provider_id == provider_id).one()
    (payment_count, total_payments, total_reimbursed) = payment_totals

    charges = db.query(ChargeModel).filter(ChargeModel.provider_id == provider_id).order_by(ChargeModel.service_date.desc()).all()
    # Aggregate paid amounts per charge with even allocation when a payment spans multiple charges
    try:
        from models.database import ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:
        from .models.database import ChargePaymentXref as XrefModel  # type: ignore
    charge_ids = [c.id for c in charges]
    if charge_ids:
        # Fetch all xrefs for these charges
        xrefs = db.query(XrefModel.charge_id, XrefModel.payment_id).filter(XrefModel.charge_id.in_(charge_ids)).all()
        if xrefs:
            payment_ids = {pid for _, pid in xrefs}
            payments_lookup = {}
            for p in db.query(PaymentModel).filter(PaymentModel.id.in_(payment_ids)).all():
                raw_amt = getattr(p, 'payment_amount', None)
                try:
                    payments_lookup[p.id] = float(raw_amt) if raw_amt is not None else 0.0
                except Exception:
                    payments_lookup[p.id] = 0.0
            payment_to_charges = {}
            for cid, pid in xrefs:
                payment_to_charges.setdefault(pid, []).append(cid)
            allocated = {}
            for pid, cid_list in payment_to_charges.items():
                amt = payments_lookup.get(pid, 0.0)
                if not cid_list:
                    continue
                share = amt / len(cid_list) if len(cid_list) > 0 else 0.0
                for cid in cid_list:
                    allocated[cid] = allocated.get(cid, 0.0) + share
            for c in charges:
                setattr(c, 'paid_amount_agg', allocated.get(c.id, 0.0))
        else:
            for c in charges:
                setattr(c, 'paid_amount_agg', 0.0)
    else:
        for c in charges:
            setattr(c, 'paid_amount_agg', 0.0)
    payments = db.query(PaymentModel).filter(PaymentModel.provider_id == provider_id).order_by(PaymentModel.payment_date.desc()).all()
    # Attach linked charge ids per payment
    try:
        from models.database import ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:
        from .models.database import ChargePaymentXref as XrefModel  # type: ignore
    payment_ids = [p.id for p in payments]
    if payment_ids:
        x_rows = (
            db.query(XrefModel.payment_id, XrefModel.charge_id)
            .filter(XrefModel.payment_id.in_(payment_ids))
            .all()
        )
        mapping = {}
        # Collect all charge ids to fetch dates for labeling
        all_charge_ids = {cid for _, cid in x_rows}
        charge_date_map = {}
        if all_charge_ids:
            charge_rows = db.query(ChargeModel.id, ChargeModel.service_date).filter(ChargeModel.id.in_(all_charge_ids)).all()
            for cid, sdate in charge_rows:
                if sdate:
                    # Format m/d (no leading zeros) using Python formatting
                    charge_date_map[cid] = f"{sdate.month}/{sdate.day}"
                else:
                    charge_date_map[cid] = str(cid)
        for pid, cid in x_rows:
            mapping.setdefault(pid, []).append(cid)
        for p in payments:
            ids = mapping.get(p.id, [])
            setattr(p, 'linked_charge_ids', ids)
            # Also attach labeled tuples for template convenience
            labeled = [(cid, charge_date_map.get(cid, str(cid))) for cid in ids]
            setattr(p, 'linked_charge_links', labeled)
    else:
        for p in payments:
            setattr(p, 'linked_charge_ids', [])
            setattr(p, 'linked_charge_links', [])

    provider_summary = {
        "id": provider.id,
        "provider_name": provider.provider_name,
        "provider_group": provider.provider_group,
        "provider_address": provider.provider_address,
        "one_way_miles": provider.one_way_miles,
        "active_provider_flag": provider.active_provider_flag,
        "charge_count": int(charge_count or 0),
        "payment_count": int(payment_count or 0),
        "total_charged": float(total_charged or 0),
        "total_owed": float(total_owed or 0),
        "total_ins_paid": float(total_ins_paid or 0),
        "total_payments": float(total_payments or 0),
        "total_reimbursed": float(total_reimbursed or 0),
    # Outstanding now defined as total owed (patient responsibility) minus payments made
    "net_outstanding": float((total_owed or 0) - (total_payments or 0)),
    }

    return templates.TemplateResponse(
        "provider_detail.html",
        {
            "request": request,
            "provider": provider_summary,
            "charges": charges,
            "payments": payments,
        }
    )


@app.get("/charges", response_class=HTMLResponse)
async def charges_page(request: Request, db: Session = Depends(get_db)):
    """Charges management page."""
    # Fetch charges ordered by most recent service_date first (nulls last), then id desc
    try:
        from models.database import Charge as _ChargeOrderModel  # type: ignore
    except ImportError:
        from .models.database import Charge as _ChargeOrderModel  # type: ignore
    from sqlalchemy import desc, nullslast
    charges = (
        db.query(_ChargeOrderModel)
        .order_by(nullslast(desc(_ChargeOrderModel.service_date)), desc(_ChargeOrderModel.id))
        .all()
    )
    providers = provider_crud.get_all(db, limit=None)
    try:
        from models.database import ChargePaymentXref as XrefModel, Payment as PaymentModel  # type: ignore
    except ImportError:
        from .models.database import ChargePaymentXref as XrefModel, Payment as PaymentModel  # type: ignore
    # Fetch all xrefs and payment amounts, then allocate payment amount equally among its linked charges
    xrefs = db.query(XrefModel.charge_id, XrefModel.payment_id).all()
    if xrefs:
        payment_ids = {pid for _, pid in xrefs}
        payments_map = {}
        for p in db.query(PaymentModel).filter(PaymentModel.id.in_(payment_ids)).all():
            raw_amt = getattr(p, 'payment_amount', None)
            try:
                payments_map[p.id] = float(raw_amt) if raw_amt is not None else 0.0
            except Exception:
                payments_map[p.id] = 0.0
        # Build mapping payment -> list of charge_ids
        payment_to_charges = {}
        for cid, pid in xrefs:
            payment_to_charges.setdefault(pid, []).append(cid)
        allocated = {}
        for pid, cids in payment_to_charges.items():
            amount = payments_map.get(pid, 0.0)
            if not cids:
                continue
            share = amount / len(cids) if len(cids) > 0 else 0
            for cid in cids:
                allocated[cid] = allocated.get(cid, 0.0) + share
        paid_sums = allocated
    else:
        paid_sums = {}
    for ch in charges:
        setattr(ch, 'paid_amount_agg', paid_sums.get(ch.id, 0.0))
    # Attachment counts for charges
    try:
        from models.database import Attachment as AttachmentModel  # type: ignore
    except ImportError:
        from .models.database import Attachment as AttachmentModel  # type: ignore
    charge_ids = [c.id for c in charges]
    att_count_map = {}
    if charge_ids:
        from sqlalchemy import func as _func
        try:
            rows = (
                db.query(AttachmentModel.charge_id, _func.count(AttachmentModel.id))
                .filter(AttachmentModel.charge_id.in_(charge_ids))
                .group_by(AttachmentModel.charge_id)
                .all()
            )
            att_count_map = {cid: int(cnt) for cid, cnt in rows}
        except Exception:  # Table might not exist yet
            att_count_map = {}
    for ch in charges:
        setattr(ch, 'attachment_count', att_count_map.get(ch.id, 0))
    return templates.TemplateResponse("charges.html", {"request": request, "charges": charges, "providers": providers})


@app.get("/charges/{charge_id}", response_class=HTMLResponse)
async def charge_detail_page(charge_id: int, request: Request, db: Session = Depends(get_db)):
    """Charge detail page showing all fields and applied payments with ability to add payment."""
    try:
        from models.database import Charge as ChargeModel, Payment as PaymentModel, Provider as ProviderModel, ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:
        from .models.database import Charge as ChargeModel, Payment as PaymentModel, Provider as ProviderModel, ChargePaymentXref as XrefModel  # type: ignore

    charge = db.query(ChargeModel).filter(ChargeModel.id == charge_id).first()
    if not charge:
        raise HTTPException(status_code=404, detail="Charge not found")

    provider = db.query(ProviderModel).filter(ProviderModel.id == charge.provider_id).first() if charge.provider_id is not None else None

    payments = db.query(PaymentModel).join(XrefModel, XrefModel.payment_id == PaymentModel.id)\
        .filter(XrefModel.charge_id == charge_id)\
        .order_by(PaymentModel.payment_date.desc()).all()
    payment_ids = [p.id for p in payments]
    paid_total = 0.0
    if payment_ids:
        all_xrefs = db.query(XrefModel.payment_id, XrefModel.charge_id).filter(XrefModel.payment_id.in_(payment_ids)).all()
        payment_to_charges = {}
        for pid, cid in all_xrefs:
            payment_to_charges.setdefault(pid, []).append(cid)
        for p in payments:
            raw_amt = getattr(p, 'payment_amount', None)
            try:
                amt = float(raw_amt) if raw_amt is not None else 0.0
            except Exception:
                amt = 0.0
            linked = payment_to_charges.get(p.id, [])
            share = amt / len(linked) if linked else 0.0
            setattr(p, 'allocated_share', share)
            paid_total += share
    for p in payments:
        if not hasattr(p, 'allocated_share'):
            setattr(p, 'allocated_share', 0.0)
    setattr(charge, 'paid_amount_agg', float(paid_total))
    # Safely coerce numeric columns (which may be Decimal or None)
    owed_val = getattr(charge, 'owed_amount', None)
    try:
        owed = float(owed_val) if owed_val is not None else 0.0
    except Exception:
        owed = 0.0
    paid = float(paid_total)
    if owed > 0 and paid >= owed:
        status = 'Paid'
    elif owed > 0 and 0 < paid < owed:
        status = 'Partial'
    elif owed > 0 and paid == 0:
        status = 'Outstanding'
    else:
        status = 'N/A'
    remaining = owed - paid if owed > 0 else 0

    # Fetch attachments for this charge
    try:
        attachments = list_attachments_for_charge(db, charge_id)
    except Exception:
        attachments = []

    return templates.TemplateResponse(
        "charge_detail.html",
        {
            "request": request,
            "charge": charge,
            "provider": provider,
            "payments": payments,
            "status": status,
            "remaining": remaining,
            "attachments": attachments,
        }
    )


@app.get("/payments", response_class=HTMLResponse)
async def payments_page(request: Request, db: Session = Depends(get_db)):
    """Payments management page."""
    payments = payment_crud.get_all(db, limit=None)
    providers = provider_crud.get_all(db, limit=None)
    # Attach count of associated charges via xref
    try:
        from models.database import ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:
        from .models.database import ChargePaymentXref as XrefModel  # type: ignore
    from sqlalchemy import func as _func
    payment_ids = [p.id for p in payments]
    counts_map = {}
    if payment_ids:
        rows = (
            db.query(XrefModel.payment_id, _func.count(XrefModel.charge_id))
            .filter(XrefModel.payment_id.in_(payment_ids))
            .group_by(XrefModel.payment_id)
            .all()
        )
        counts_map = {r[0]: int(r[1]) for r in rows}
    for p in payments:
        setattr(p, 'charge_link_count', counts_map.get(p.id, 0))
    # Attachment counts for payments
    try:
        from models.database import Attachment as AttachmentModel  # type: ignore
    except ImportError:
        from .models.database import Attachment as AttachmentModel  # type: ignore
    payment_ids = [p.id for p in payments]
    pay_att_counts = {}
    if payment_ids:
        try:
            rows2 = (
                db.query(AttachmentModel.payment_id, _func.count(AttachmentModel.id))
                .filter(AttachmentModel.payment_id.in_(payment_ids))
                .group_by(AttachmentModel.payment_id)
                .all()
            )
            pay_att_counts = {pid: int(cnt) for pid, cnt in rows2}
        except Exception:
            pay_att_counts = {}
    for p in payments:
        setattr(p, 'attachment_count', pay_att_counts.get(p.id, 0))
    return templates.TemplateResponse("payments.html", {"request": request, "payments": payments, "providers": providers})


@app.get("/payments/{payment_id}", response_class=HTMLResponse)
async def payment_detail_page(payment_id: int, request: Request, db: Session = Depends(get_db)):
    """Payment detail page: show payment info, allocated charges, and attachments with upload/delete."""
    try:
        from models.database import Payment as PaymentModel, Charge as ChargeModel, ChargePaymentXref as XrefModel, Provider as ProviderModel  # type: ignore
    except ImportError:
        from .models.database import Payment as PaymentModel, Charge as ChargeModel, ChargePaymentXref as XrefModel, Provider as ProviderModel  # type: ignore

    payment = db.query(PaymentModel).filter(PaymentModel.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    # Provider (may be null if legacy or detached)
    provider = None
    if getattr(payment, 'provider_id', None) is not None:
        provider = db.query(ProviderModel).filter(ProviderModel.id == payment.provider_id).first()

    # Determine linked charges and compute even share allocation
    xref_rows = db.query(XrefModel.charge_id).filter(XrefModel.payment_id == payment_id).all()
    charge_ids = [cid for (cid,) in xref_rows]
    total_amount = 0.0
    raw_amt = getattr(payment, 'payment_amount', None)
    try:
        total_amount = float(raw_amt) if raw_amt is not None else 0.0
    except Exception:
        total_amount = 0.0
    if charge_ids:
        # Preserve a stable order (service_date desc, id desc similar to charges list)
        charges = (
            db.query(ChargeModel)
            .filter(ChargeModel.id.in_(charge_ids))
            .order_by(ChargeModel.service_date.desc(), ChargeModel.id.desc())
            .all()
        )
        # Even share for THIS payment only
        share_current = total_amount / len(charge_ids) if len(charge_ids) else 0.0
        for ch in charges:
            setattr(ch, 'allocated_share', share_current)
    else:
        charges = []
        share_current = 0.0

    # Attachments for this payment
    try:
        payment_attachments = list_attachments_for_payment(db, payment_id)
    except Exception:
        payment_attachments = []

    # Recompute full paid context for each linked charge: include ALL payments allocated to that charge via even-share logic.
    if charges:
        try:
            # Fetch all xrefs for the set of charges (may include many payments including current)
            all_charge_xrefs = db.query(XrefModel.payment_id, XrefModel.charge_id).filter(XrefModel.charge_id.in_(charge_ids)).all()
            if all_charge_xrefs:
                all_payment_ids = {pid for pid, _ in all_charge_xrefs}
                # Pull all payments amounts once
                payment_amounts = {}
                if all_payment_ids:
                    for p in db.query(PaymentModel).filter(PaymentModel.id.in_(all_payment_ids)).all():
                        raw_amt2 = getattr(p, 'payment_amount', None)
                        try:
                            payment_amounts[p.id] = float(raw_amt2) if raw_amt2 is not None else 0.0
                        except Exception:
                            payment_amounts[p.id] = 0.0
                payment_to_charges_map = {}
                for pid, cid in all_charge_xrefs:
                    payment_to_charges_map.setdefault(pid, []).append(cid)
                # Aggregate total paid per charge considering each payment's even split among its linked charges
                total_paid_all_map: dict[int, float] = {}
                for pid, c_list in payment_to_charges_map.items():
                    amt = payment_amounts.get(pid, 0.0)
                    if not c_list:
                        continue
                    per_share = amt / len(c_list) if len(c_list) else 0.0
                    for cid in c_list:
                        total_paid_all_map[cid] = total_paid_all_map.get(cid, 0.0) + per_share
                # Attach aggregates to charge objects
                for ch in charges:
                    owed_val = getattr(ch, 'owed_amount', None)
                    try:
                        owed_f = float(owed_val) if owed_val is not None else 0.0
                    except Exception:
                        owed_f = 0.0
                    # Ensure we use plain int id for dict lookup (SQLAlchemy attribute may be InstrumentedAttribute during type checking)
                    try:
                        ch_id_int_raw = getattr(ch, 'id')
                        ch_id_int = int(ch_id_int_raw) if ch_id_int_raw is not None else -1
                    except Exception:
                        ch_id_int = -1
                    total_paid_all = total_paid_all_map.get(int(ch_id_int), 0.0)
                    # allocated_share already represents this payment's share for the charge
                    setattr(ch, 'total_paid_all', total_paid_all)
                    setattr(ch, 'prior_paid', max(total_paid_all - getattr(ch, 'allocated_share', 0.0), 0.0))
                    setattr(ch, 'remaining_after_all', max(owed_f - total_paid_all, 0.0))
            else:
                for ch in charges:
                    owed_val = getattr(ch, 'owed_amount', None)
                    try:
                        owed_f = float(owed_val) if owed_val is not None else 0.0
                    except Exception:
                        owed_f = 0.0
                    setattr(ch, 'total_paid_all', getattr(ch, 'allocated_share', 0.0))  # only this payment
                    setattr(ch, 'prior_paid', 0.0)
                    setattr(ch, 'remaining_after_all', max(owed_f - getattr(ch, 'total_paid_all', 0.0), 0.0))
        except Exception:
            # On any failure, fallback to previous simple remaining logic
            for ch in charges:
                owed_val = getattr(ch, 'owed_amount', None)
                try:
                    owed_f = float(owed_val) if owed_val is not None else 0.0
                except Exception:
                    owed_f = 0.0
                share_val = getattr(ch, 'allocated_share', 0.0) or 0.0
                setattr(ch, 'total_paid_all', share_val)
                setattr(ch, 'prior_paid', 0.0)
                setattr(ch, 'remaining_after_all', max(owed_f - share_val, 0.0))

    return templates.TemplateResponse(
        "payment_detail.html",
        {
            "request": request,
            "payment": payment,
            "provider": provider,
            "charges": charges,
            "attachments": payment_attachments,
            "providers": provider_crud.get_all(db, limit=None),
        }
    )


@app.get("/premiums", response_class=HTMLResponse)
async def premiums_page(request: Request, db: Session = Depends(get_db)):
    """Premiums management page."""
    premiums = premium_crud.get_all(db, limit=None)
    return templates.TemplateResponse("premiums.html", {"request": request, "premiums": premiums})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)