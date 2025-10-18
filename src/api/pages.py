"""Pages router for HTML (Jinja2) views.

Contains only server-rendered pages; JSON CRUD endpoints live in other routers.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import func
from sqlalchemy.orm import Session

try:  # absolute preferred
    from crud.attachment import (
        list_attachments_for_charge,
        list_attachments_for_payment,
    )
    from crud.payment import payment_crud
    from crud.premium import premium_crud
    from crud.provider import provider_crud
    from database_config import db_config, get_db
    from services.allocation import annotate_charges_with_paid
except ImportError:  # pragma: no cover
    from ..crud.attachment import (  # type: ignore
        list_attachments_for_charge,
        list_attachments_for_payment,
    )
    from ..crud.payment import payment_crud  # type: ignore
    from ..crud.premium import premium_crud  # type: ignore
    from ..crud.provider import provider_crud  # type: ignore
    from ..database_config import db_config, get_db  # type: ignore
    from ..services.allocation import annotate_charges_with_paid  # type: ignore

import pytz

router = APIRouter(tags=["pages"])  # no prefix -> root-level paths

# Template setup (duplicated from app bootstrap; could be centralized later)
project_root = Path(__file__).resolve().parents[2]  # .../project_root
templates = Jinja2Templates(directory=str(project_root / "templates"))
templates.env.globals["ENV_MODE"] = getattr(db_config, "env_mode", "PROD")


def format_currency(value):
    try:
        if value is None:
            return "$0.00"
        return f"${float(value):,.2f}"
    except Exception:
        return "$0.00"


templates.env.filters["currency"] = format_currency


def fam_avatar(name: str) -> Markup:
    try:
        if not name:
            return Markup("")
        raw = str(name).strip()
        ch = raw[0:1].upper()
        if not ch or ch.isspace():
            ch = "?"
        slug = raw.lower()
        mod = ""
        if slug == "alex":
            mod = "fam-avatar--alex"
        elif slug == "sam":
            mod = "fam-avatar--sam"
        elif slug == "riley":
            mod = "fam-avatar--riley"
        elif slug == "jordan":
            mod = "fam-avatar--jordan"
        cls = f"fam-avatar {mod}" if mod else "fam-avatar"
        html = f'<span class="{cls}" title="{raw}">{ch}</span>'
        return Markup(html)
    except Exception:
        return Markup("")


templates.env.filters["fam_avatar"] = fam_avatar


def format_datetime_est(dt):
    """Format a naive datetime (assumed UTC) to EST timezone string."""
    if not dt:
        return "-"
    # Assume dt is naive UTC
    utc_tz = pytz.timezone("UTC")
    est_tz = pytz.timezone("US/Eastern")
    utc_dt = utc_tz.localize(dt)
    est_dt = utc_dt.astimezone(est_tz)
    return est_dt.strftime("%#m/%#d/%y %I:%M %p")


templates.env.filters["format_datetime_est"] = format_datetime_est


@router.get("/", response_class=HTMLResponse)
@router.get("/home", response_class=HTMLResponse)
async def home_page(request: Request, db: Session = Depends(get_db)):
    """Landing home dashboard page."""
    # Lightweight aggregates (optional future enhancement) – for now just render template.
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/providers", response_class=HTMLResponse)
async def providers_page(request: Request, db: Session = Depends(get_db)):
    """Providers management page with aggregates."""
    try:
        from models.database import Charge as ChargeModel
        from models.database import Payment as PaymentModel
        from models.database import Provider as ProviderModel  # type: ignore
    except ImportError:  # pragma: no cover
        from ..models.database import Provider as ProviderModel, Charge as ChargeModel, Payment as PaymentModel  # type: ignore

    from sqlalchemy import Numeric, cast

    charges_subq = (
        db.query(
            ChargeModel.provider_id.label("provider_id"),
            cast(func.sum(ChargeModel.charged_amount), Numeric(18, 2)).label(
                "total_charges"
            ),
            cast(func.sum(ChargeModel.owed_amount), Numeric(18, 2)).label("total_owed"),
            func.max(ChargeModel.service_date).label("last_charge_date"),
        )
        .group_by(ChargeModel.provider_id)
        .subquery()
    )
    payments_subq = (
        db.query(
            PaymentModel.provider_id.label("provider_id"),
            cast(func.sum(PaymentModel.payment_amount), Numeric(18, 2)).label(
                "total_payments"
            ),
            func.max(PaymentModel.payment_date).label("last_payment_date"),
        )
        .group_by(PaymentModel.provider_id)
        .subquery()
    )
    # Order so that providers are grouped by group name, but ungrouped providers
    # are interleaved alphabetically among the group names (treating their own
    # provider name as the grouping key when group is null).
    from sqlalchemy import func as _f

    grouping_key = _f.coalesce(
        _f.lower(ProviderModel.provider_group), _f.lower(ProviderModel.provider_name)
    )
    query = (
        db.query(
            ProviderModel,
            charges_subq.c.total_owed.label("total_owed"),
            payments_subq.c.total_payments.label("total_payments"),
            charges_subq.c.last_charge_date.label("last_charge_date"),
            payments_subq.c.last_payment_date.label("last_payment_date"),
        )
        .outerjoin(charges_subq, charges_subq.c.provider_id == ProviderModel.id)
        .outerjoin(payments_subq, payments_subq.c.provider_id == ProviderModel.id)
        .order_by(grouping_key, _f.lower(ProviderModel.provider_name))
    )
    providers_with_financials = []
    for (
        provider_obj,
        total_owed,
        total_payments,
        last_charge_date,
        last_payment_date,
    ) in query.all():
        try:
            total_owed_f = float(total_owed) if total_owed is not None else 0.0
        except Exception:
            total_owed_f = 0.0
        try:
            total_payments_f = (
                float(total_payments) if total_payments is not None else 0.0
            )
        except Exception:
            total_payments_f = 0.0
        net_amount = total_owed_f - total_payments_f
        providers_with_financials.append(
            {
                "id": provider_obj.id,
                "provider_name": provider_obj.provider_name,
                "provider_group": provider_obj.provider_group,
                "provider_address": provider_obj.provider_address,
                "one_way_miles": provider_obj.one_way_miles,
                "active_provider_flag": provider_obj.active_provider_flag,
                "total_owed": total_owed_f,
                "total_payments": total_payments_f,
                "net_amount": net_amount,
                "last_charge_date": last_charge_date,
                "last_payment_date": last_payment_date,
            }
        )
    return templates.TemplateResponse(
        "providers.html", {"request": request, "providers": providers_with_financials}
    )


@router.get("/providers/{provider_id}", response_class=HTMLResponse)
async def provider_detail_page(
    provider_id: int, request: Request, db: Session = Depends(get_db)
):
    """Detailed page for a single provider including summary, charges, payments."""
    try:
        from models.database import Charge as ChargeModel
        from models.database import ChargePaymentXref as XrefModel
        from models.database import Payment as PaymentModel
        from models.database import Provider as ProviderModel  # type: ignore
    except ImportError:  # pragma: no cover
        from ..models.database import Charge as ChargeModel
        from ..models.database import ChargePaymentXref as XrefModel
        from ..models.database import Payment as PaymentModel
        from ..models.database import Provider as ProviderModel  # type: ignore

    provider = db.query(ProviderModel).filter(ProviderModel.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    from sqlalchemy import Numeric, cast

    charge_totals = (
        db.query(
            func.coalesce(func.count(ChargeModel.id), 0),
            cast(func.sum(ChargeModel.charged_amount), Numeric(18, 2)),
            cast(func.sum(ChargeModel.owed_amount), Numeric(18, 2)),
            cast(func.sum(ChargeModel.insurance_paid_amount), Numeric(18, 2)),
        )
        .filter(ChargeModel.provider_id == provider_id)
        .one()
    )
    (charge_count, total_charged, total_owed, total_ins_paid) = charge_totals

    # NOTE: payment_totals will be computed from the payments list below so
    # that the financial summary matches the payments table. We intentionally
    # avoid a separate aggregation query here because payments shown include
    # payments linked to this provider's charges even when the payment's
    # provider_id is not set.
    payment_count = 0
    total_payments = 0
    total_reimbursed = 0

    charges = (
        db.query(ChargeModel)
        .filter(ChargeModel.provider_id == provider_id)
        .order_by(ChargeModel.service_date.desc())
        .all()
    )
    annotate_charges_with_paid(db, charges)

    # Add attachment counts for charges
    try:
        from models.database import Attachment as AttachmentModel  # type: ignore
    except ImportError:  # pragma: no cover
        from ..models.database import Attachment as AttachmentModel  # type: ignore

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
        except Exception:  # table may not exist yet
            att_count_map = {}
    for ch in charges:
        setattr(ch, "attachment_count", att_count_map.get(ch.id, 0))

    # Include payments that are either directly assigned to this provider
    # (Payment.provider_id == provider_id) OR payments that are linked to
    # charges belonging to this provider via the ChargePaymentXref table.
    # Historically only payments with the provider_id were shown which meant
    # payments allocated to charges for this provider but missing the
    # provider_id didn't surface on the provider detail page.
    from sqlalchemy import or_

    payment_ids_linked_to_charges = []
    if charge_ids:
        try:
            linked_rows = (
                db.query(XrefModel.payment_id)
                .filter(XrefModel.charge_id.in_(charge_ids))
                .distinct()
                .all()
            )
            payment_ids_linked_to_charges = [pid for (pid,) in linked_rows]
        except Exception:
            payment_ids_linked_to_charges = []

    payments_query = db.query(PaymentModel)
    if payment_ids_linked_to_charges:
        payments_query = payments_query.filter(
            or_(
                PaymentModel.provider_id == provider_id,
                PaymentModel.id.in_(payment_ids_linked_to_charges),
            )
        )
    else:
        payments_query = payments_query.filter(PaymentModel.provider_id == provider_id)

    payments = payments_query.order_by(PaymentModel.payment_date.desc()).all()

    # Add attachment counts for payments
    payment_ids = [p.id for p in payments]
    pay_att_counts = {}
    if payment_ids:
        try:
            rows2 = (
                db.query(AttachmentModel.payment_id, func.count(AttachmentModel.id))
                .filter(AttachmentModel.payment_id.in_(payment_ids))
                .group_by(AttachmentModel.payment_id)
                .all()
            )
            pay_att_counts = {pid: int(cnt) for pid, cnt in rows2}
        except Exception:
            pay_att_counts = {}
    for p in payments:
        setattr(p, "attachment_count", pay_att_counts.get(p.id, 0))

    # Attach linked charge ids with simple labels for each payment
    if payment_ids:
        x_rows = (
            db.query(XrefModel.payment_id, XrefModel.charge_id)
            .filter(XrefModel.payment_id.in_(payment_ids))
            .all()
        )
        mapping = {}
        all_charge_ids = {cid for _, cid in x_rows}
        charge_date_map = {}
        if all_charge_ids:
            charge_rows = (
                db.query(ChargeModel.id, ChargeModel.service_date)
                .filter(ChargeModel.id.in_(all_charge_ids))
                .all()
            )
            for cid, sdate in charge_rows:
                if sdate:
                    charge_date_map[cid] = f"{sdate.month}/{sdate.day}"
                else:
                    charge_date_map[cid] = str(cid)
        for pid, cid in x_rows:
            mapping.setdefault(pid, []).append(cid)
        for p in payments:
            ids = mapping.get(p.id, [])
            setattr(p, "linked_charge_ids", ids)
            labeled = [(cid, charge_date_map.get(cid, str(cid))) for cid in ids]
            setattr(p, "linked_charge_links", labeled)
    else:
        for p in payments:
            setattr(p, "linked_charge_ids", [])
            setattr(p, "linked_charge_links", [])

    # Recompute payment aggregates from the payments list so totals reflect the
    # exact set of payments being displayed (including linked payments).
    try:
        payment_count = int(len(payments))
        tp = 0.0
        tr = 0.0
        for p in payments:
            raw_amt = getattr(p, "payment_amount", None)
            try:
                tp += float(raw_amt) if raw_amt is not None else 0.0
            except Exception:
                pass
            raw_reim = getattr(p, "reimbursement_amount", None)
            try:
                tr += float(raw_reim) if raw_reim is not None else 0.0
            except Exception:
                pass
        total_payments = float(tp)
        total_reimbursed = float(tr)
    except Exception:
        payment_count = int(len(payments) if payments is not None else 0)
        total_payments = float(0)
        total_reimbursed = float(0)

    provider_summary = {
        "id": provider.id,
        "provider_name": provider.provider_name,
        "provider_group": provider.provider_group,
        "provider_address": provider.provider_address,
        "phone_num": provider.phone_num,
        "npi_number": provider.npi_number,
        "tax_id": provider.tax_id,
        "one_way_miles": provider.one_way_miles,
        "active_provider_flag": provider.active_provider_flag,
        "last_charge_date": provider.last_charge_date,
        "last_payment_date": provider.last_payment_date,
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
        "created_by": provider.created_by,
        "updated_by": provider.updated_by,
        "charge_count": int(charge_count or 0),
        "payment_count": int(payment_count or 0),
        "total_charged": float(total_charged or 0),
        "total_owed": float(total_owed or 0),
        "total_ins_paid": float(total_ins_paid or 0),
        "total_payments": float(total_payments or 0),
        "total_reimbursed": float(total_reimbursed or 0),
        # Ensure numeric types are compatible (SQLAlchemy may return Decimal)
        "net_outstanding": float((float(total_owed or 0) - float(total_payments or 0))),
    }
    return templates.TemplateResponse(
        "provider_detail.html",
        {
            "request": request,
            "provider": provider_summary,
            "charges": charges,
            "payments": payments,
        },
    )


@router.get("/charges", response_class=HTMLResponse)
async def charges_page(request: Request, db: Session = Depends(get_db)):
    try:
        from models.database import Charge as ChargeModel  # type: ignore
    except ImportError:  # pragma: no cover
        from ..models.database import Charge as ChargeModel  # type: ignore
    from sqlalchemy import desc
    from sqlalchemy import func as _func
    from sqlalchemy import nullslast

    charges = (
        db.query(ChargeModel)
        .order_by(nullslast(desc(ChargeModel.service_date)), desc(ChargeModel.id))
        .all()
    )
    annotate_charges_with_paid(db, charges)

    # Identify potential duplicates
    # Group by service_date, family_member, medical_type, base_fee_amount, charged_amount
    # so both the base fee and charged amount are considered when detecting duplicates
    duplicate_groups = (
        db.query(
            ChargeModel.service_date,
            ChargeModel.family_member,
            ChargeModel.medical_type,
            ChargeModel.base_fee_amount,
            ChargeModel.charged_amount,
            _func.array_agg(ChargeModel.id).label("charge_ids"),
        )
        .group_by(
            ChargeModel.service_date,
            ChargeModel.family_member,
            ChargeModel.medical_type,
            ChargeModel.base_fee_amount,
            ChargeModel.charged_amount,
        )
        .having(_func.count(ChargeModel.id) > 1)
        .all()
    )

    # Build set of potential duplicate charge IDs.
    # Note: Historically we excluded any group if any charge had 'NO DUP' in notes.
    # Per requested behavior, only exclude a duplicate group when ALL charges in
    # the group include the 'NO DUP' marker in their notes. If at least one
    # charge lacks the marker, the group remains a potential duplicate.
    potential_duplicate_ids = set()
    for group in duplicate_groups:
        group_charge_ids = group.charge_ids
        # Load charges in this group and determine NO DUP markers
        charges_in_group = (
            db.query(ChargeModel).filter(ChargeModel.id.in_(group_charge_ids)).all()
        )

        # Only exclude the group if ALL charges explicitly contain 'NO DUP' in their notes.
        # If any charge does NOT have the marker, the group should still be considered
        # potential duplicates (per the requested behavior).
        all_have_no_dup = all(
            (charge.notes is not None) and ("NO DUP" in charge.notes.upper())
            for charge in charges_in_group
        )

        if not all_have_no_dup:
            potential_duplicate_ids.update(group_charge_ids)

    # Annotate charges with duplicate status
    for charge in charges:
        setattr(charge, "is_potential_duplicate", charge.id in potential_duplicate_ids)

    providers = provider_crud.get_all(db, limit=None)
    # Attachment counts
    try:
        from models.database import Attachment as AttachmentModel  # type: ignore
    except ImportError:  # pragma: no cover
        from ..models.database import Attachment as AttachmentModel  # type: ignore
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
        except Exception:  # table may not exist yet
            att_count_map = {}
    for ch in charges:
        setattr(ch, "attachment_count", att_count_map.get(ch.id, 0))
    return templates.TemplateResponse(
        "charges.html", {"request": request, "charges": charges, "providers": providers}
    )


@router.get("/charges/import/json", response_class=HTMLResponse)
async def charges_import_json_page(request: Request, db: Session = Depends(get_db)):
    """UI for importing charges via JSON/FHIR preview workflow."""
    providers = provider_crud.get_all(db, limit=None)
    return templates.TemplateResponse(
        "import_charges_json.html", {"request": request, "providers": providers}
    )


@router.get("/charges/import/eob", response_class=HTMLResponse)
async def charges_import_eob_page(request: Request, db: Session = Depends(get_db)):
    """UI for importing charges via EOB upload and parsing workflow."""
    providers = provider_crud.get_all(db, limit=None)
    return templates.TemplateResponse(
        "import_charges_eob.html", {"request": request, "providers": providers}
    )


@router.get("/charges/add-multiple", response_class=HTMLResponse)
async def add_multiple_charges_page(request: Request, db: Session = Depends(get_db)):
    """UI for adding multiple charges in a batch."""
    providers = provider_crud.get_all(db, limit=None)
    return templates.TemplateResponse(
        "add_multiple_charges.html", {"request": request, "providers": providers}
    )


@router.get("/charges/{charge_id}", response_class=HTMLResponse)
async def charge_detail_page(
    charge_id: int, request: Request, db: Session = Depends(get_db)
):
    try:
        from models.database import Charge as ChargeModel
        from models.database import ChargePaymentXref as XrefModel
        from models.database import Payment as PaymentModel
        from models.database import Provider as ProviderModel
    except ImportError:  # pragma: no cover
        from ..models.database import Charge as ChargeModel  # type: ignore
        from ..models.database import ChargePaymentXref as XrefModel
        from ..models.database import Payment as PaymentModel
        from ..models.database import Provider as ProviderModel

    charge = db.query(ChargeModel).filter(ChargeModel.id == charge_id).first()
    if not charge:
        raise HTTPException(status_code=404, detail="Charge not found")

    provider = (
        db.query(ProviderModel).filter(ProviderModel.id == charge.provider_id).first()
        if charge.provider_id is not None
        else None
    )

    # Get payments linked to this charge (via xref) and compute allocated shares
    payments = (
        db.query(PaymentModel)
        .join(XrefModel, XrefModel.payment_id == PaymentModel.id)
        .filter(XrefModel.charge_id == charge_id)
        .order_by(PaymentModel.payment_date.desc())
        .all()
    )

    payment_ids = [p.id for p in payments]
    paid_total = 0.0
    if payment_ids:
        all_xrefs = (
            db.query(XrefModel.payment_id, XrefModel.charge_id)
            .filter(XrefModel.payment_id.in_(payment_ids))
            .all()
        )
        payment_to_charges = {}
        for pid, cid in all_xrefs:
            payment_to_charges.setdefault(pid, []).append(cid)
        for p in payments:
            raw_amt = getattr(p, "payment_amount", None)
            try:
                amt = float(raw_amt) if raw_amt is not None else 0.0
            except Exception:
                amt = 0.0
            linked = payment_to_charges.get(p.id, [])
            share = amt / len(linked) if linked else 0.0
            setattr(p, "allocated_share", share)
            paid_total += share

    for p in payments:
        if not hasattr(p, "allocated_share"):
            setattr(p, "allocated_share", 0.0)

    setattr(charge, "paid_amount_agg", float(paid_total))

    owed_val = getattr(charge, "owed_amount", None)
    try:
        owed = float(owed_val) if owed_val is not None else 0.0
    except Exception:
        owed = 0.0
    paid = float(paid_total)
    if owed > 0 and paid >= owed:
        status = "Paid"
    elif owed > 0 and 0 < paid < owed:
        status = "Partial"
    elif owed > 0 and paid == 0:
        status = "Outstanding"
    else:
        status = "N/A"
    remaining = owed - paid if owed > 0 else 0

    try:
        attachments = list_attachments_for_charge(db, charge_id)
    except Exception:
        attachments = []

    # Also include full provider list so the edit form can allow changing the
    # associated provider for a charge.
    providers = provider_crud.get_all(db, limit=None)

    return templates.TemplateResponse(
        "charge_detail.html",
        {
            "request": request,
            "charge": charge,
            "provider": provider,
            "providers": providers,
            "payments": payments,
            "status": status,
            "remaining": remaining,
            "attachments": attachments,
        },
    )


@router.get("/payments", response_class=HTMLResponse)
async def payments_page(request: Request, db: Session = Depends(get_db)):
    payments = payment_crud.get_all(db, limit=None)
    providers = provider_crud.get_all(db, limit=None)
    try:
        from models.database import ChargePaymentXref as XrefModel  # type: ignore
    except ImportError:  # pragma: no cover
        from ..models.database import ChargePaymentXref as XrefModel  # type: ignore
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
        setattr(p, "charge_link_count", counts_map.get(p.id, 0))
    try:
        from models.database import Attachment as AttachmentModel  # type: ignore
    except ImportError:  # pragma: no cover
        from ..models.database import Attachment as AttachmentModel  # type: ignore
    pay_att_counts = {}
    if payment_ids:
        from sqlalchemy import func as _func  # Ensure _func is imported

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
        setattr(p, "attachment_count", pay_att_counts.get(p.id, 0))
    return templates.TemplateResponse(
        "payments.html",
        {"request": request, "payments": payments, "providers": providers},
    )


@router.get("/payments/{payment_id}", response_class=HTMLResponse)
async def payment_detail_page(
    payment_id: int, request: Request, db: Session = Depends(get_db)
):
    try:
        from models.database import Charge as ChargeModel
        from models.database import ChargePaymentXref as XrefModel
        from models.database import Payment as PaymentModel  # type: ignore
        from models.database import Provider as ProviderModel
    except ImportError:  # pragma: no cover
        from ..models.database import Charge as ChargeModel
        from ..models.database import ChargePaymentXref as XrefModel
        from ..models.database import Payment as PaymentModel  # type: ignore
        from ..models.database import Provider as ProviderModel
    payment = db.query(PaymentModel).filter(PaymentModel.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    provider = (
        db.query(ProviderModel).filter(ProviderModel.id == payment.provider_id).first()
        if getattr(payment, "provider_id", None) is not None
        else None
    )
    xref_rows = (
        db.query(XrefModel.charge_id).filter(XrefModel.payment_id == payment_id).all()
    )
    charge_ids = [cid for (cid,) in xref_rows]
    raw_amt = getattr(payment, "payment_amount", None)
    try:
        total_amount = float(raw_amt) if raw_amt is not None else 0.0
    except Exception:
        total_amount = 0.0
    if charge_ids:
        charges = (
            db.query(ChargeModel)
            .filter(ChargeModel.id.in_(charge_ids))
            .order_by(ChargeModel.service_date.desc(), ChargeModel.id.desc())
            .all()
        )
        share_current = total_amount / len(charge_ids) if len(charge_ids) else 0.0
        for ch in charges:
            setattr(ch, "allocated_share", share_current)
    else:
        charges = []
    try:
        payment_attachments = list_attachments_for_payment(db, payment_id)
    except Exception:
        payment_attachments = []
    # Recompute full paid context for each linked charge (all payments)
    if charges:
        try:
            all_charge_xrefs = (
                db.query(XrefModel.payment_id, XrefModel.charge_id)
                .filter(XrefModel.charge_id.in_(charge_ids))
                .all()
            )
            if all_charge_xrefs:
                all_payment_ids = {pid for pid, _ in all_charge_xrefs}
                payment_amounts = {}
                if all_payment_ids:
                    for p in (
                        db.query(PaymentModel)
                        .filter(PaymentModel.id.in_(all_payment_ids))
                        .all()
                    ):
                        raw_amt2 = getattr(p, "payment_amount", None)
                        try:
                            payment_amounts[p.id] = (
                                float(raw_amt2) if raw_amt2 is not None else 0.0
                            )
                        except Exception:
                            payment_amounts[p.id] = 0.0
                payment_to_charges_map = {}
                for pid, cid in all_charge_xrefs:
                    payment_to_charges_map.setdefault(pid, []).append(cid)
                total_paid_all_map: dict[int, float] = {}
                for pid, c_list in payment_to_charges_map.items():
                    amt = payment_amounts.get(pid, 0.0)
                    if not c_list:
                        continue
                    per_share = amt / len(c_list) if len(c_list) else 0.0
                    for cid in c_list:
                        total_paid_all_map[cid] = (
                            total_paid_all_map.get(cid, 0.0) + per_share
                        )
                for ch in charges:
                    owed_val = getattr(ch, "owed_amount", None)
                    try:
                        owed_f = float(owed_val) if owed_val is not None else 0.0
                    except Exception:
                        owed_f = 0.0
                    ch_id_raw = getattr(ch, "id")
                    try:
                        ch_id_int = int(ch_id_raw) if ch_id_raw is not None else -1
                    except Exception:
                        ch_id_int = -1
                    total_paid_all = total_paid_all_map.get(ch_id_int, 0.0)
                    setattr(ch, "total_paid_all", total_paid_all)
                    setattr(
                        ch,
                        "prior_paid",
                        max(total_paid_all - getattr(ch, "allocated_share", 0.0), 0.0),
                    )
                    setattr(
                        ch, "remaining_after_all", max(owed_f - total_paid_all, 0.0)
                    )
            else:
                for ch in charges:
                    owed_val = getattr(ch, "owed_amount", None)
                    try:
                        owed_f = float(owed_val) if owed_val is not None else 0.0
                    except Exception:
                        owed_f = 0.0
                    share_val = getattr(ch, "allocated_share", 0.0) or 0.0
                    setattr(ch, "total_paid_all", share_val)
                    setattr(ch, "prior_paid", 0.0)
                    setattr(ch, "remaining_after_all", max(owed_f - share_val, 0.0))
        except Exception:
            for ch in charges:
                owed_val = getattr(ch, "owed_amount", None)
                try:
                    owed_f = float(owed_val) if owed_val is not None else 0.0
                except Exception:
                    owed_f = 0.0
                share_val = getattr(ch, "allocated_share", 0.0) or 0.0
                setattr(ch, "total_paid_all", share_val)
                setattr(ch, "prior_paid", 0.0)
                setattr(ch, "remaining_after_all", max(owed_f - share_val, 0.0))
    return templates.TemplateResponse(
        "payment_detail.html",
        {
            "request": request,
            "payment": payment,
            "provider": provider,
            "charges": charges,
            "attachments": payment_attachments,
            "providers": provider_crud.get_all(db, limit=None),
        },
    )


@router.get("/premiums", response_class=HTMLResponse)
async def premiums_page(request: Request, db: Session = Depends(get_db)):
    premiums = premium_crud.get_all(db, limit=None)
    return templates.TemplateResponse(
        "premiums.html", {"request": request, "premiums": premiums}
    )
