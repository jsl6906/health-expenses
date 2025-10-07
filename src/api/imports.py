"""Router for JSON / FHIR Bundle charge import and EOB stub endpoints."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Query
from sqlalchemy.orm import Session
import json as _json
from pydantic import BaseModel

from database_config import get_db
from crud.charge import charge_crud
from crud.provider import provider_crud
from crud.attachment import create_attachment
from services.import_parsing import parse_fhir_bundle, heuristic_extract_rows
from services.import_session import (
    JSONImportRow, JSONImportPreview, JSONImportCommitRequest, JSONImportCommitResult,
    json_import_sessions
)

router = APIRouter(tags=["imports"], prefix="/api")


@router.post("/charges/import-json", response_model=JSONImportPreview)
async def import_charges_from_json_preview(
    request: Request,
    provider_id: int | None = Query(None),
    family_member: str | None = Query(None),
    medical_type: str | None = Query(None),
    db: Session = Depends(get_db)
):
    ctype = request.headers.get("content-type", "").lower()
    raw_json_obj = None
    source_mode = None
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        def _get_text(field_name: str) -> str | None:
            v = form.get(field_name)
            from fastapi import UploadFile as _UF
            if v is None or isinstance(v, _UF):
                return None
            s = str(v).strip()
            return s or None
        txt_provider = _get_text("provider_id")
        if txt_provider:
            try:
                provider_id = int(txt_provider)
            except Exception:
                pass
        txt_fam = _get_text("family_member")
        if txt_fam:
            family_member = txt_fam
        txt_med = _get_text("medical_type")
        if txt_med:
            medical_type = txt_med
        upload_candidate = form.get("file")
        upload: UploadFile | None = upload_candidate if isinstance(upload_candidate, UploadFile) else None
        if upload is not None:
            content_bytes = await upload.read()
            if not content_bytes:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            try:
                text = content_bytes.decode("utf-8-sig")
            except Exception:
                try:
                    text = content_bytes.decode("latin-1")
                except Exception as ex:
                    raise HTTPException(status_code=400, detail=f"Unable to decode file bytes: {ex}")
            stripped = text.lstrip()
            if not stripped:
                raise HTTPException(status_code=400, detail="Uploaded file only contains whitespace")
            try:
                raw_json_obj = _json.loads(stripped)
                source_mode = "multipart_file"
            except Exception as ex:
                raise HTTPException(status_code=400, detail=f"Uploaded file is not valid JSON: {ex}")
        else:
            inline_json_text = _get_text("file") or _get_text("json")
            if not inline_json_text:
                raise HTTPException(status_code=400, detail="No JSON file or inline JSON provided")
            try:
                raw_json_obj = _json.loads(inline_json_text.lstrip())
                source_mode = "multipart_inline"
            except Exception as ex:
                raise HTTPException(status_code=400, detail=f"Inline JSON text not valid: {ex}")
    else:
        body_bytes = await request.body()
        if not body_bytes:
            raise HTTPException(status_code=400, detail="Empty JSON body")
        try:
            raw_json_obj = _json.loads(body_bytes.decode("utf-8"))
            source_mode = "json_body"
        except Exception as ex:
            raise HTTPException(status_code=400, detail=f"Request body is not valid JSON: {ex}")
    rows: list[dict] = []
    parse_mode = None
    # debug flag currently unused; reserved for future diagnostic return
    if isinstance(raw_json_obj, dict) and raw_json_obj.get("resourceType") == "Bundle":
        rows = parse_fhir_bundle(raw_json_obj)
        parse_mode = "fhir_bundle"
    else:
        candidate_rows = None
        if isinstance(raw_json_obj, dict) and isinstance(raw_json_obj.get("charges"), list):
            candidate_rows = raw_json_obj.get("charges")
        elif isinstance(raw_json_obj, list):
            candidate_rows = raw_json_obj
        if candidate_rows is not None:
            for r in candidate_rows:
                if not isinstance(r, dict):
                    continue
                svc = r.get("service_date") or r.get("date") or r.get("serviceDate")
                desc = r.get("description") or r.get("charge_description") or r.get("chargeDescription")
                cpt = r.get("cpt_code") or r.get("cpt")
                charged = r.get("charged_amount") or r.get("amount") or r.get("charge_amount")
                owed = r.get("owed_amount") or r.get("patient_amount") or r.get("patientOwed")
                rows.append({
                    "service_date": svc,
                    "description": desc,
                    "cpt_code": cpt,
                    "charged_amount": charged,
                    "owed_amount": owed,
                })
            parse_mode = "generic_rows"
        else:
            if isinstance(raw_json_obj, dict) or isinstance(raw_json_obj, list):
                # attempt heuristic
                heur_rows, heur_diag = heuristic_extract_rows(raw_json_obj)
                if heur_rows:
                    rows = heur_rows
                    parse_mode = "heuristic"
            else:
                raise HTTPException(status_code=400, detail="JSON not recognized. Expect FHIR Bundle, charges array, or raw list.")
    total_parsed = len(rows)
    try:
        from models.database import Charge as ChargeModel  # type: ignore
    except ImportError:
        from .models.database import Charge as ChargeModel  # type: ignore
    existing = db.query(ChargeModel).all()
    existing_keys = set()
    for ch in existing:
        _svc = getattr(ch, 'service_date', None)
        key = (
            _svc.isoformat() if _svc else None,
            getattr(ch, 'family_member', None),
            getattr(ch, 'medical_type', None),
            getattr(ch, 'provider_id', None),
            (getattr(ch, 'cpt_code', None) or None),
            float(getattr(ch, 'charged_amount', 0) or 0),
            (getattr(ch, 'charge_description', None) or None),
        )
        existing_keys.add(key)
    preview_rows: list[JSONImportRow] = []
    provider_candidates: list[dict] = []
    if rows and isinstance(rows[0], dict) and rows[0].get("_provider_candidates"):
        provider_candidates = rows[0].get("_provider_candidates") or []
        rows = rows[1:]
    try:
        from models.database import Provider as ProviderModel  # type: ignore
    except ImportError:
        from .models.database import Provider as ProviderModel  # type: ignore
    existing_providers = db.query(ProviderModel).all()
    existing_provider_keys = {((getattr(p, 'provider_name', None) or '').strip().upper()) for p in existing_providers}
    provider_preview: list[dict] = []
    provider_dup_count = 0
    for pc in provider_candidates:
        key = (pc.get('provider_name') or '').strip().upper()
        is_dup = key in existing_provider_keys
        if is_dup:
            provider_dup_count += 1
        provider_preview.append({**pc, "duplicate": is_dup})
    duplicate_count = 0
    fam_val = family_member or None
    med_val = medical_type or "Medical"
    prov_val = provider_id
    for idx, r in enumerate(rows):
        svc_date = r.get("service_date")
        desc = r.get("description")
        cpt_code = r.get("cpt_code")
        charged_amount = r.get("charged_amount")
        owed_amount = r.get("owed_amount")
        base_fee_amount = r.get("base_fee_amount")
        insurance_discount_amount = r.get("insurance_discount_amount")
        insurance_paid_amount = r.get("insurance_paid_amount")
        doctor_name = r.get("doctor_name")
        eob_claim_num = r.get("eob_claim_num")
        key = (svc_date, fam_val, med_val, prov_val, cpt_code, float(charged_amount or 0), desc)
        is_dup = key in existing_keys
        if is_dup:
            duplicate_count += 1
        preview_rows.append(JSONImportRow(
            index=idx,
            service_date=svc_date,
            description=desc,
            cpt_code=cpt_code,
            charged_amount=charged_amount,
            owed_amount=owed_amount,
            base_fee_amount=base_fee_amount,
            insurance_discount_amount=insurance_discount_amount,
            insurance_paid_amount=insurance_paid_amount,
            doctor_name=doctor_name,
            eob_claim_num=eob_claim_num,
            facility_location_id=r.get("facility_location_id"),
            facility_location_name=r.get("facility_location_name"),
            duplicate=is_dup,
        ))
    session_id = json_import_sessions.create({
        "rows": preview_rows,
        "provider_id": provider_id,
        "family_member": family_member,
        "medical_type": medical_type,
        "parse_mode": parse_mode,
        "source_mode": source_mode,
        "providers": provider_preview,
        "existing_keys_snapshot": existing_keys,
    })
    return JSONImportPreview(
        session_id=session_id,
        total_parsed=total_parsed,
        duplicate_count=duplicate_count,
        rows=preview_rows,
        parse_mode=parse_mode,
        source_mode=source_mode,
        providers=provider_preview or None,
        provider_duplicate_count=provider_dup_count if provider_preview else None,
    )


@router.post("/charges/import-json/{session_id}/commit", response_model=JSONImportCommitResult)
def commit_json_import(session_id: str, payload: JSONImportCommitRequest, db: Session = Depends(get_db)):
    sess = json_import_sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    rows: list[JSONImportRow] = sess["rows"]
    provider_preview: list[dict] = sess.get("providers") or []
    try:
        from models.database import Charge as ChargeModel  # type: ignore
    except ImportError:
        from .models.database import Charge as ChargeModel  # type: ignore
    existing = db.query(ChargeModel).all()
    existing_keys = set()
    for ch in existing:
        _svc = getattr(ch, 'service_date', None)
        key = (
            _svc.isoformat() if _svc else None,
            getattr(ch, 'family_member', None),
            getattr(ch, 'medical_type', None),
            getattr(ch, 'provider_id', None),
            (getattr(ch, 'cpt_code', None) or None),
            float(getattr(ch, 'charged_amount', 0) or 0),
            (getattr(ch, 'charge_description', None) or None),
        )
        existing_keys.add(key)
    inserted_provider_ids_by_location: dict[str, int] = {}
    if payload.selected_provider_location_ids:
        try:
            from models.database import Provider as ProviderModel  # type: ignore
        except ImportError:
            from .models.database import Provider as ProviderModel  # type: ignore
        existing_providers = db.query(ProviderModel).all()
        provider_key_map: dict[tuple[str,str], int] = {}
        for p in existing_providers:
            key = ((getattr(p, 'provider_name', '') or '').strip().lower(), (getattr(p, 'provider_address', '') or '').strip().lower())
            raw_id = getattr(p, 'id', None)
            if isinstance(raw_id, int):
                provider_key_map[key] = raw_id
        for prov in provider_preview:
            loc_id = prov.get("location_id")
            if not loc_id or loc_id not in payload.selected_provider_location_ids:
                continue
            key = ((prov.get('provider_name') or '').strip().lower(), (prov.get('provider_address') or '').strip().lower())
            existing_id = provider_key_map.get(key)
            if existing_id is not None and isinstance(existing_id, int):
                inserted_provider_ids_by_location[loc_id] = existing_id
                continue
            raw_name = (prov.get("provider_name") or "").strip()
            raw_addr = (prov.get("provider_address") or "").strip()
            fallback_name = None
            if not raw_name:
                if raw_addr:
                    fallback_name = raw_addr.split(',')[0][:80]
                elif loc_id:
                    fallback_name = f"Provider {loc_id}"
                else:
                    fallback_name = "Unnamed Provider"
            final_name = raw_name or fallback_name
            if not final_name:
                continue
            prov_in = {
                "provider_name": final_name,
                "provider_address": raw_addr or None,
                "provider_group": None,
                "one_way_miles": None,
                "active_provider_flag": True,
            }
            try:
                created_p = provider_crud.create(db, prov_in)  # type: ignore[arg-type]
                db.flush()
                pid = getattr(created_p, 'id', None)
                if pid:
                    inserted_provider_ids_by_location[loc_id] = int(pid)
                    provider_key_map[key] = int(pid)
            except Exception:
                continue
    fam_val = payload.family_member or sess.get("family_member") or None
    med_val = payload.medical_type or sess.get("medical_type") or "Medical"
    prov_val = payload.provider_id or sess.get("provider_id")
    selected = set(payload.selected_indices)
    inserted_ids: list[int] = []
    skipped_duplicates = 0
    for r in rows:
        if r.index not in selected:
            continue
        key = (r.service_date, fam_val, med_val, prov_val, r.cpt_code, float(r.charged_amount or 0), r.description)
        if key in existing_keys:
            skipped_duplicates += 1
            continue
        provider_override = prov_val
        facility_ref = getattr(r, '_facility_ref', None)
        if facility_ref and isinstance(facility_ref, str) and '/' in facility_ref:
            loc_part = facility_ref.split('/')[-1]
            pid = inserted_provider_ids_by_location.get(loc_part)
            if pid:
                provider_override = pid
        charge_in = {
            "service_date": r.service_date,
            "family_member": fam_val,
            "medical_type": med_val,
            "provider_id": provider_override,
            "cpt_code": r.cpt_code,
            "charge_description": r.description,
            "base_fee_amount": r.base_fee_amount,
            "charged_amount": r.charged_amount,
            "owed_amount": r.owed_amount,
            "insurance_discount_amount": r.insurance_discount_amount,
            "insurance_paid_amount": r.insurance_paid_amount,
            "doctor_name": r.doctor_name,
            "eob_claim_num": r.eob_claim_num,
        }
        try:
            created = charge_crud.create(db, charge_in)
            db.flush()
            raw_id = getattr(created, 'id', None)
            if raw_id is not None:
                inserted_ids.append(int(raw_id))
                existing_keys.add(key)
        except Exception:
            continue
    db.commit()
    return JSONImportCommitResult(
        inserted_count=len(inserted_ids),
        inserted_ids=inserted_ids,
        skipped_duplicates=skipped_duplicates,
        session_id=session_id,
    )


# --- EOB Stub endpoints (reuse original session object design) ---
class EOBUploadResponse(BaseModel):
    session_id: str
    row_count: int
    filename: str
try:
    from eob_import import create_session as eob_create_session, get_session as eob_get_session, delete_session as eob_delete_session
except ImportError:  # pragma: no cover
    from ..eob_import import create_session as eob_create_session, get_session as eob_get_session, delete_session as eob_delete_session

@router.post("/eob/upload", response_model=EOBUploadResponse)
async def eob_upload(file: UploadFile = File(...)):
    content = await file.read()
    session = eob_create_session(file.filename or "eob_upload", content, file.content_type)
    data = session.serialize()
    return EOBUploadResponse(session_id=session.id, row_count=data["row_count"], filename=data["filename"])

@router.get("/eob/session/{session_id}")
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

@router.post("/eob/session/{session_id}/import", response_model=EOBImportResult)
def eob_import(session_id: str, payload: EOBImportRequest, db: Session = Depends(get_db)):
    session = eob_get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if payload.provider_id is None:
        raise HTTPException(status_code=400, detail="provider_id is required for EOB import")
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
        amt = (overrides_map.get(r.index, {}).get("charged_amount") if r.index in overrides_map else r.charged_amount) or 0
        if amt > highest_amount:
            highest_amount = amt
            highest_row = r.index
    for r in rows:
        try:
            ov = overrides_map.get(r.index, {})
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
            if highest_row is not None and r.index != highest_row:
                charge_in["notes"] = "EOB file attached to highest charge in this batch"
            created = charge_crud.create(db, charge_in)
            raw_id = getattr(created, "id", None)
            if raw_id is not None:
                created_ids.append(int(raw_id))
        except Exception:
            continue
    db.commit()
    if highest_row is not None and session.file_bytes and created_ids:
        try:
            highest_created_id = None
            idx_tracker = 0
            for r in rows:
                if getattr(r, 'index', None) in sel_set:
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
    eob_delete_session(session_id)
    return EOBImportResult(imported_count=len(created_ids), created_charge_ids=created_ids)
