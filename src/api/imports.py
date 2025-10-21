"""Router for JSON / FHIR Bundle charge import and EOB stub endpoints."""
from __future__ import annotations

import json as _json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..crud.attachment import create_attachment
from ..crud.charge import charge_crud
from ..crud.provider import provider_crud
from ..database_config import get_db
from ..schemas.schemas import AttachmentCreate
from ..services.import_parsing import heuristic_extract_rows, parse_fhir_bundle
from ..services.import_session import (
    JSONChargeAnalyzeResponse,
    JSONImportCommitRequest,
    JSONImportCommitResult,
    JSONImportPreview,
    JSONImportRow,
    JSONProviderAnalyzeResponse,
    json_import_sessions,
)

router = APIRouter(tags=["imports"], prefix="/api")

logger = logging.getLogger(__name__)


# NOTE: Legacy single-step preview endpoint (kept for backward compatibility)
@router.post("/charges/import-json", response_model=JSONImportPreview)
async def import_charges_from_json_preview(
    request: Request,
    provider_id: int | None = Query(None),
    family_member: str | None = Query(None),
    medical_type: str | None = Query(None),
    db: Session = Depends(get_db),
):
    ctype = request.headers.get("content-type", "").lower()
    raw_json_obj = None
    source_mode = None
    original_filename = None
    original_json_text = None

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
            txt_fam = _get_text("family_member_id") or _get_text("family_member")
            if txt_fam:
                # try to parse as id first
                try:
                    family_member_id = int(txt_fam)
                except Exception:
                    family_member_id = None
                if family_member_id:
                    try:
                        from models.database import FamilyMember as FamilyMemberModel
                    except Exception:
                        from .models.database import FamilyMember as FamilyMemberModel
                    fm = db.get(FamilyMemberModel, family_member_id)
                    if fm:
                        family_member = (
                            fm.emoji
                            if getattr(fm, "emoji", None)
                            else (
                                getattr(fm, "first_name", "")
                                + (
                                    (" " + getattr(fm, "last_name", ""))
                                    if getattr(fm, "last_name", None)
                                    else ""
                                )
                            )
                        )
                else:
                    family_member = txt_fam
        txt_med = _get_text("medical_type")
        if txt_med:
            medical_type = txt_med
        upload_candidate = form.get("file")
        upload: UploadFile | None = (
            upload_candidate if isinstance(upload_candidate, UploadFile) else None
        )
        if upload is not None:
            content_bytes = await upload.read()
            if not content_bytes:

                def extract_relevant_fhir_entries(json_obj, charge_row):
                    """Extract only the specific EOB and related entries for this charge.

                    Uses a simple scoring mechanism to choose the best-matching
                    ExplanationOfBenefit when multiple EOB resources exist in the
                    Bundle. Preference order: exact claim number match, then
                    (service date + CPT) matches, then single-field matches.
                    """

                    relevant_entries = []

                    if (
                        isinstance(json_obj, dict)
                        and json_obj.get("resourceType") == "Bundle"
                    ):
                        entries = json_obj.get("entry", [])

                        target_claim_num = getattr(charge_row, "eob_claim_num", None)
                        target_service_date = getattr(charge_row, "service_date", None)
                        target_cpt = getattr(charge_row, "cpt_code", None)

                        # Score each EOB and pick best candidate
                        best_eob = None
                        best_score = 0

                        for entry in entries:
                            resource = entry.get("resource", {})
                            if resource.get("resourceType") != "ExplanationOfBenefit":
                                continue

                            score = 0
                            claim_match = False

                            # Claim number match (high weight)
                            if target_claim_num:
                                for identifier in resource.get("identifier", []):
                                    if identifier.get("value") == target_claim_num:
                                        score += 100
                                        claim_match = True
                                        break

                            # Inspect items for date / CPT matches and also check item/detail identifiers
                            matching_items = []
                            for item in resource.get("item", []):
                                # Date can be servicedDate or servicedPeriod.start
                                item_date = item.get("servicedDate")
                                if not item_date:
                                    sp = item.get("servicedPeriod") or {}
                                    item_date = sp.get("start")

                                item_cpt = None
                                product_service = item.get("productOrService", {})
                                for coding in product_service.get("coding", []):
                                    code = coding.get("code")
                                    if code:
                                        item_cpt = code
                                        break

                                matched = False

                                # Check for identifier matches at item level
                                if target_claim_num:
                                    for ident in (
                                        item.get("identifier", [])
                                        if isinstance(item.get("identifier"), list)
                                        else []
                                    ):
                                        if (
                                            isinstance(ident, dict)
                                            and ident.get("value") == target_claim_num
                                        ):
                                            score += 100
                                            matched = True
                                            break

                                    # Check detail-level identifiers
                                    if not matched:
                                        for detail in item.get("detail", []) or []:
                                            for ident in (
                                                detail.get("identifier", [])
                                                if isinstance(
                                                    detail.get("identifier"), list
                                                )
                                                else []
                                            ):
                                                if (
                                                    isinstance(ident, dict)
                                                    and ident.get("value")
                                                    == target_claim_num
                                                ):
                                                    score += 100
                                                    matched = True
                                                    break
                                            if matched:
                                                break

                                if not matched:
                                    if (
                                        target_service_date
                                        and item_date
                                        and item_date == target_service_date
                                    ):
                                        score += 10
                                        matched = True
                                    if (
                                        target_cpt
                                        and item_cpt
                                        and item_cpt == target_cpt
                                    ):
                                        score += 5
                                        matched = True

                                if matched:
                                    matching_items.append(item)

                            # If we found any score, consider this EOB
                            if score > 0 and score >= best_score:
                                best_score = score
                                # Store the resource plus the matching items (for later pruning)
                                # Attach matching_items on a temp key so we can prune later
                                candidate = dict(resource)
                                if matching_items:
                                    candidate["_matched_items"] = matching_items
                                elif claim_match:
                                    # Claim matched but no specific items matched by date/CPT - keep full EOB
                                    candidate["_matched_items"] = None
                                else:
                                    candidate["_matched_items"] = []
                                best_eob = candidate

                        if best_eob:
                            # Prune the EOB resource to only include matching items when available
                            target_eob_id = best_eob.get("id")
                            stored_matched = best_eob.get("_matched_items", [])
                            if stored_matched is None:
                                # None means claim matched but no specific items identified - keep full resource
                                pruned_resource = dict(best_eob)
                                # remove temp key if present
                                pruned_resource.pop("_matched_items", None)
                            else:
                                pruned_resource = dict(best_eob)
                                pruned_resource.pop("_matched_items", None)
                                # Replace items with only the matched subset (or empty list)
                                pruned_resource["item"] = stored_matched or []

                            relevant_entries.append(
                                {
                                    "resourceType": "ExplanationOfBenefit",
                                    "id": target_eob_id,
                                    "resource": pruned_resource,
                                }
                            )

                            # Collect related organization/practitioner ids from the chosen (original) EOB
                            related_org_ids = set()
                            related_practitioner_ids = set()

                            provider_ref = best_eob.get("provider", {}).get(
                                "reference", ""
                            )
                            if provider_ref.startswith("Organization/"):
                                related_org_ids.add(provider_ref.split("/")[1])
                            facility_ref = best_eob.get("facility", {}).get(
                                "reference", ""
                            )
                            if facility_ref.startswith("Organization/"):
                                related_org_ids.add(facility_ref.split("/")[1])

                            for care_team in best_eob.get("careTeam", []):
                                provider_ref = care_team.get("provider", {}).get(
                                    "reference", ""
                                )
                                if provider_ref.startswith("Practitioner/"):
                                    related_practitioner_ids.add(
                                        provider_ref.split("/")[1]
                                    )
                                elif provider_ref.startswith("Organization/"):
                                    related_org_ids.add(provider_ref.split("/")[1])

                            # Now add related Organization / Practitioner resources from the bundle
                            for entry in entries:
                                resource = entry.get("resource", {})
                                resource_type = resource.get("resourceType")
                                resource_id = resource.get("id")
                                if not resource_id:
                                    continue
                                if (
                                    resource_type == "Organization"
                                    and resource_id in related_org_ids
                                ):
                                    relevant_entries.append(
                                        {
                                            "resourceType": "Organization",
                                            "id": resource_id,
                                            "resource": resource,
                                        }
                                    )
                                elif (
                                    resource_type
                                    in ["Practitioner", "PractitionerRole"]
                                    and resource_id in related_practitioner_ids
                                ):
                                    relevant_entries.append(
                                        {
                                            "resourceType": resource_type,
                                            "id": resource_id,
                                            "resource": resource,
                                        }
                                    )
                    else:
                        # For non-FHIR JSON, include provider-related info (keep existing logic)
                        if isinstance(json_obj, dict):
                            if json_obj.get("providers"):
                                relevant_entries.append(
                                    {
                                        "type": "providers",
                                        "data": json_obj.get("providers"),
                                    }
                                )
                            if json_obj.get("organizations"):
                                relevant_entries.append(
                                    {
                                        "type": "organizations",
                                        "data": json_obj.get("organizations"),
                                    }
                                )
                            if json_obj.get("facilities"):
                                relevant_entries.append(
                                    {
                                        "type": "facilities",
                                        "data": json_obj.get("facilities"),
                                    }
                                )

                    return relevant_entries

        desc = r.get("description")
        cpt_code = r.get("cpt_code")
        charged_amount = r.get("charged_amount")
        owed_amount = r.get("owed_amount")
        base_fee_amount = r.get("base_fee_amount")
        insurance_discount_amount = r.get("insurance_discount_amount")
        insurance_paid_amount = r.get("insurance_paid_amount")
        doctor_name = r.get("doctor_name")
        eob_claim_num = r.get("eob_claim_num")
        key = (
            svc_date,
            fam_val,
            med_val,
            prov_val,
            cpt_code,
            float(charged_amount or 0),
            desc,
        )
        is_dup = key in existing_keys
        if is_dup:
            duplicate_count += 1
        preview_rows.append(
            JSONImportRow(
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
            )
        )
    session_id = json_import_sessions.create(
        {
            "rows": preview_rows,
            "provider_id": provider_id,
            "family_member": family_member,
            "medical_type": medical_type,
            "parse_mode": parse_mode,
            "source_mode": source_mode,
            "providers": provider_preview,
            "existing_keys_snapshot": existing_keys,
            "original_filename": original_filename,
            "original_json_text": original_json_text,
            "original_json_obj": raw_json_obj,
        }
    )
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


# ---------------- New multi-step workflow endpoints -----------------
@router.post(
    "/charges/analyze-json/providers", response_model=JSONProviderAnalyzeResponse
)
async def analyze_json_providers(request: Request, db: Session = Depends(get_db)):
    """Stage 1: Accept an upload/pasted JSON and return provider candidates.

    Stores raw parsed rows + provider candidates in the session. Does NOT do duplicate
    check for charges yet (requires family/medical inputs in stage 2).
    """
    ctype = request.headers.get("content-type", "").lower()
    raw_json_obj = None
    source_mode = None
    original_filename = None
    original_json_text = None

    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        upload_candidate = form.get("file")
        upload: UploadFile | None = (
            upload_candidate if isinstance(upload_candidate, UploadFile) else None
        )
        if upload is None:
            raise HTTPException(
                status_code=400, detail="JSON file is required (multipart)"
            )
        content_bytes = await upload.read()
        if not content_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        try:
            text = content_bytes.decode("utf-8-sig")
        except Exception:
            try:
                text = content_bytes.decode("latin-1")
            except Exception as ex:  # pragma: no cover - rare path
                raise HTTPException(
                    status_code=400, detail=f"Unable to decode file bytes: {ex}"
                )

        # Store original filename and JSON text
        original_filename = upload.filename or "uploaded_file.json"
        original_json_text = text.lstrip()

        try:
            raw_json_obj = _json.loads(original_json_text)
            source_mode = "multipart_file"
        except Exception as ex:
            raise HTTPException(
                status_code=400, detail=f"Uploaded file is not valid JSON: {ex}"
            )
    else:
        body_bytes = await request.body()
        if not body_bytes:
            raise HTTPException(status_code=400, detail="Empty JSON body")
        try:
            original_json_text = body_bytes.decode("utf-8")
            raw_json_obj = _json.loads(original_json_text)
            source_mode = "json_body"

            # Try to create a more meaningful filename from JSON content
            if isinstance(raw_json_obj, dict):
                if raw_json_obj.get("resourceType") == "Bundle":
                    bundle_id = raw_json_obj.get("id", "unknown")
                    original_filename = f"fhir_bundle_{bundle_id}.json"
                elif raw_json_obj.get("id"):
                    original_filename = f"json_import_{raw_json_obj.get('id')}.json"
                else:
                    original_filename = "pasted_json.json"
            else:
                original_filename = "pasted_json.json"

        except Exception as ex:
            raise HTTPException(
                status_code=400, detail=f"Request body is not valid JSON: {ex}"
            )

    # Parse just like legacy but keep raw rows separate
    rows: list[dict] = []
    parse_mode = None
    if isinstance(raw_json_obj, dict) and raw_json_obj.get("resourceType") == "Bundle":
        rows = parse_fhir_bundle(raw_json_obj)
        parse_mode = "fhir_bundle"
    else:
        candidate_rows = None
        if isinstance(raw_json_obj, dict) and isinstance(
            raw_json_obj.get("charges"), list
        ):
            candidate_rows = raw_json_obj.get("charges")
        elif isinstance(raw_json_obj, list):
            candidate_rows = raw_json_obj
        if candidate_rows is not None:
            for r in candidate_rows:
                if not isinstance(r, dict):
                    continue
                svc = r.get("service_date") or r.get("date") or r.get("serviceDate")
                desc = (
                    r.get("description")
                    or r.get("charge_description")
                    or r.get("chargeDescription")
                )
                cpt = r.get("cpt_code") or r.get("cpt")
                charged = (
                    r.get("charged_amount") or r.get("amount") or r.get("charge_amount")
                )
                owed = (
                    r.get("owed_amount")
                    or r.get("patient_amount")
                    or r.get("patientOwed")
                )
                rows.append(
                    {
                        "service_date": svc,
                        "description": desc,
                        "cpt_code": cpt,
                        "charged_amount": charged,
                        "owed_amount": owed,
                    }
                )
            parse_mode = "generic_rows"
        else:
            if isinstance(raw_json_obj, (dict, list)):
                heur_rows, heur_diag = heuristic_extract_rows(raw_json_obj)
                if heur_rows:
                    rows = heur_rows
                    parse_mode = "heuristic"
            else:
                raise HTTPException(
                    status_code=400,
                    detail="JSON not recognized. Expect FHIR Bundle or charges array.",
                )

    provider_candidates: list[dict] = []
    if rows and isinstance(rows[0], dict) and rows[0].get("_provider_candidates"):
        provider_candidates = rows[0].get("_provider_candidates") or []
        rows = rows[1:]
    try:
        from models.database import Provider as ProviderModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import Provider as ProviderModel  # type: ignore
    existing_providers = db.query(ProviderModel).all()
    existing_provider_keys = set()
    for p in existing_providers:
        key = (
            (getattr(p, "provider_name", "") or "").strip().lower(),
            (getattr(p, "provider_address", "") or "").strip().lower(),
        )
        existing_provider_keys.add(key)

    provider_preview: list[dict] = []
    existing_count = 0
    for pc in provider_candidates:
        key = (
            (pc.get("provider_name") or "").strip().lower(),
            (pc.get("provider_address") or "").strip().lower(),
        )
        is_dup = key in existing_provider_keys
        if is_dup:
            existing_count += 1
        provider_preview.append({**pc, "duplicate": is_dup})
    session_id = json_import_sessions.create(
        {
            "raw_rows": rows,
            "providers": provider_preview,
            "parse_mode": parse_mode,
            "source_mode": source_mode,
            "providers_added": False,
            "original_filename": original_filename,
            "original_json_text": original_json_text,
            "original_json_obj": raw_json_obj,
        }
    )
    return JSONProviderAnalyzeResponse(
        session_id=session_id,
        total_candidates=len(provider_preview),
        existing_count=existing_count,
        new_count=len(provider_preview) - existing_count,
        providers=provider_preview,
        parse_mode=parse_mode,
        source_mode=source_mode,
    )


@router.post("/charges/analyze-json/charges", response_model=JSONChargeAnalyzeResponse)
def analyze_json_charges(
    session_id: str = Query(
        ..., description="Session id returned from provider analyze"
    ),
    family_member_id: int
    | None = Query(None, description="Family member id to use for preview"),
    medical_type: str = Query(...),
    provider_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """Stage 2: With family member & medical type, build charge candidate rows + duplicate flags."""
    sess = json_import_sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    raw_rows: list[dict] = sess.get("raw_rows") or []
    parse_mode = sess.get("parse_mode")
    source_mode = sess.get("source_mode")
    try:
        from models.database import Charge as ChargeModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import Charge as ChargeModel  # type: ignore
    existing = db.query(ChargeModel).all()
    existing_keys = set()
    for ch in existing:
        _svc = getattr(ch, "service_date", None)
        key = (
            _svc.isoformat() if _svc else None,
            getattr(ch, "family_member", None),
            getattr(ch, "medical_type", None),
            getattr(ch, "provider_id", None),
            (getattr(ch, "cpt_code", None) or None),
            float(getattr(ch, "charged_amount", 0) or 0),
            (getattr(ch, "charge_description", None) or None),
            (getattr(ch, "eob_claim_num", None) or None),
        )
        existing_keys.add(key)

    # Get provider mapping from session for correct provider ID assignment
    inserted_provider_ids_by_location = (
        sess.get("inserted_provider_ids_by_location") or {}
    )

    # Also build a mapping of all existing providers by facility location for charges
    # that might reference providers not added in this session
    try:
        from models.database import Provider as ProviderModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import Provider as ProviderModel  # type: ignore

    existing_providers = db.query(ProviderModel).all()
    existing_provider_key_map: dict[tuple[str, str], int] = {}
    for p in existing_providers:
        key = (
            (getattr(p, "provider_name", "") or "").strip().lower(),
            (getattr(p, "provider_address", "") or "").strip().lower(),
        )
        pid = getattr(p, "id", None)
        if isinstance(pid, int):
            existing_provider_key_map[key] = pid

    # Debug: log session contents and available provider mappings
    logger.debug("=== CHARGE ANALYSIS SESSION DEBUG ===")
    logger.debug(f"Session ID: {session_id}")
    logger.debug(f"Full session contents: {sess}")
    logger.debug(f"Session keys: {list(sess.keys()) if sess else 'None'}")
    logger.debug(
        f"inserted_provider_ids_by_location: {inserted_provider_ids_by_location}"
    )
    logger.debug(f"providers_added flag: {sess.get('providers_added', 'NOT_SET')}")
    logger.debug(f"raw_rows count: {len(sess.get('raw_rows', []))}")
    logger.debug("=======================================")

    preview_rows: list[JSONImportRow] = []
    duplicate_count = 0
    # Resolve family_member label for preview display if id provided
    fam_val = None
    if family_member_id:
        try:
            from models.database import FamilyMember as FamilyMemberModel
        except Exception:
            from .models.database import FamilyMember as FamilyMemberModel
        fm = db.get(FamilyMemberModel, family_member_id)
        if fm:
            fam_val = (
                fm.emoji
                if getattr(fm, "emoji", None)
                else (
                    getattr(fm, "first_name", "")
                    + (
                        (" " + getattr(fm, "last_name", ""))
                        if getattr(fm, "last_name", None)
                        else ""
                    )
                )
            )
    else:
        fam_val = None
    med_val = medical_type or "Medical"
    prov_val = provider_id
    for idx, r in enumerate(raw_rows):
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

        # Determine provider ID from facility location
        final_provider_id = prov_val  # Start with override if provided
        facility_location_id = r.get("facility_location_id")

        # Debug: log facility location lookup
        logger.debug(
            f"Row {idx}: facility_location_id={facility_location_id}, prov_override={prov_val}"
        )
        logger.debug(
            f"  -> Available session mappings: {list(inserted_provider_ids_by_location.keys())}"
        )

        if not final_provider_id and facility_location_id:
            # Look up provider ID from inserted providers first
            final_provider_id = inserted_provider_ids_by_location.get(
                facility_location_id
            )
            logger.debug(
                f"  -> Session lookup result: {final_provider_id} for {facility_location_id}"
            )

            # If not found in session, try to match against all existing providers
            # by looking for a provider with matching name from the raw row
            if not final_provider_id:
                facility_name = r.get("facility_location_name") or ""

                # Try to find existing provider by name (exact match first)
                if facility_name:
                    for (
                        existing_name,
                        existing_addr,
                    ), provider_id in existing_provider_key_map.items():
                        if existing_name == facility_name.strip().lower():
                            final_provider_id = provider_id
                            logger.debug(
                                f"  -> Name match found: {final_provider_id} for '{facility_name}'"
                            )
                            break

                if not final_provider_id:
                    logger.debug(
                        f"  -> No existing provider match found for '{facility_name}'"
                    )
        else:
            logger.debug(f"  -> Using override provider_id: {final_provider_id}")

        logger.debug(f"  -> Final provider_id: {final_provider_id}")

        key = (
            svc_date,
            fam_val,
            med_val,
            final_provider_id,
            cpt_code,
            float(charged_amount or 0),
            desc,
            eob_claim_num,
        )
        is_dup = key in existing_keys
        if is_dup:
            duplicate_count += 1

        # Debug: log the provider ID right before creating the object
        logger.debug(
            f"  -> Creating JSONImportRow with resolved_provider_id={final_provider_id}"
        )

        new_row = JSONImportRow(
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
            resolved_provider_id=final_provider_id,
        )

        # Debug: verify the provider ID was set correctly
        logger.debug(
            f"  -> Created object has resolved_provider_id={new_row.resolved_provider_id}"
        )

        preview_rows.append(new_row)

    # Debug: verify resolved_provider_id is set before session update
    if preview_rows:
        logger.debug(
            f"Before session update - first row resolved_provider_id: {preview_rows[0].resolved_provider_id}"
        )

    json_import_sessions.update(
        session_id,
        {
            "rows": preview_rows,
            "family_member_id": family_member_id,
            "family_member": fam_val,
            "medical_type": medical_type,
            "provider_id": provider_id,
        },
    )

    # Debug: verify resolved_provider_id after session update
    updated_sess = json_import_sessions.get(session_id)
    if updated_sess and updated_sess.get("rows"):
        first_updated_row = updated_sess["rows"][0]
        logger.debug(
            f"After session update - first row type: {type(first_updated_row)}"
        )
        if hasattr(first_updated_row, "resolved_provider_id"):
            logger.debug(
                f"After session update - first row resolved_provider_id: {first_updated_row.resolved_provider_id}"
            )
        elif isinstance(first_updated_row, dict):
            logger.debug(
                f"After session update - first row resolved_provider_id: {first_updated_row.get('resolved_provider_id', 'NOT_FOUND')}"
            )

    return JSONChargeAnalyzeResponse(
        session_id=session_id,
        total_parsed=len(preview_rows),
        duplicate_count=duplicate_count,
        rows=preview_rows,
        family_member=family_member,
        medical_type=medical_type,
        parse_mode=parse_mode,
        source_mode=source_mode,
    )


class ProviderAddRequest(BaseModel):
    session_id: str
    selected_provider_location_ids: list[str]


class ProviderAddResult(BaseModel):
    session_id: str
    inserted_count: int
    skipped_existing: int
    provider_ids_by_location: dict[str, int]


@router.post("/charges/analyze-json/providers/add", response_model=ProviderAddResult)
def add_analyzed_providers(payload: ProviderAddRequest, db: Session = Depends(get_db)):
    sess = json_import_sessions.get(payload.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    provider_preview: list[dict] = sess.get("providers") or []
    try:
        from models.database import Provider as ProviderModel  # type: ignore
    except ImportError:  # pragma: no cover
        from .models.database import Provider as ProviderModel  # type: ignore
    existing_providers = db.query(ProviderModel).all()
    provider_key_map: dict[tuple[str, str], int] = {}
    for p in existing_providers:
        key = (
            (getattr(p, "provider_name", "") or "").strip().lower(),
            (getattr(p, "provider_address", "") or "").strip().lower(),
        )
        pid = getattr(p, "id", None)
        if isinstance(pid, int):
            provider_key_map[key] = pid
    inserted_provider_ids_by_location: dict[str, int] = {}
    inserted = 0
    skipped_existing = 0
    for pc in provider_preview:
        loc_id = pc.get("location_id")
        if not loc_id or loc_id not in payload.selected_provider_location_ids:
            continue
        key = (
            (pc.get("provider_name") or "").strip().lower(),
            (pc.get("provider_address") or "").strip().lower(),
        )
        existing_id = provider_key_map.get(key)
        if existing_id is not None:
            inserted_provider_ids_by_location[loc_id] = existing_id
            skipped_existing += 1
            continue
        raw_name = (pc.get("provider_name") or "").strip()
        raw_addr = (pc.get("provider_address") or "").strip()
        if not raw_name:
            if raw_addr:
                raw_name = raw_addr.split(",")[0][:80]
            elif loc_id:
                raw_name = f"Provider {loc_id}"
            else:
                raw_name = "Unnamed Provider"
        prov_in = {
            "provider_name": raw_name,
            "provider_address": raw_addr or None,
            "provider_group": None,
            "one_way_miles": None,
            "active_provider_flag": True,
        }
        try:
            created_p = provider_crud.create(db, prov_in)  # type: ignore[arg-type]
            db.flush()
            pid = getattr(created_p, "id", None)
            if pid:
                inserted_provider_ids_by_location[loc_id] = int(pid)
                provider_key_map[key] = int(pid)
                inserted += 1
        except Exception:  # pragma: no cover
            continue
    db.commit()

    # Debug: log what we're storing in the session
    logger.debug(
        f"Storing in session - inserted_provider_ids_by_location: {inserted_provider_ids_by_location}"
    )

    json_import_sessions.update(
        payload.session_id,
        {
            "providers_added": True,
            "inserted_provider_ids_by_location": inserted_provider_ids_by_location,
        },
    )

    # Debug: verify session update worked
    updated_sess = json_import_sessions.get(payload.session_id)
    if updated_sess:
        logger.debug(f"Session after update - keys: {list(updated_sess.keys())}")
        logger.debug(
            f"Session after update: providers_added={updated_sess.get('providers_added')}"
        )
        logger.debug(
            f"Session mapping after update: {updated_sess.get('inserted_provider_ids_by_location')}"
        )
    else:
        logger.debug("ERROR: Session not found after update!")

    return ProviderAddResult(
        session_id=payload.session_id,
        inserted_count=inserted,
        skipped_existing=skipped_existing,
        provider_ids_by_location=inserted_provider_ids_by_location,
    )


@router.post(
    "/charges/import-json/{session_id}/commit", response_model=JSONImportCommitResult
)
def commit_json_import(
    session_id: str, payload: JSONImportCommitRequest, db: Session = Depends(get_db)
):
    sess = json_import_sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    rows: list[JSONImportRow] = sess["rows"]

    # Debug: Check what type of objects we're getting from session
    logger.debug("=== COMMIT SESSION DEBUG ===")
    logger.debug(f"Session ID: {session_id}")
    logger.debug(f"Row count: {len(rows)}")
    if rows:
        first_row = rows[0]
        logger.debug(f"First row type: {type(first_row)}")
        logger.debug(
            f"First row keys: {list(first_row.keys()) if isinstance(first_row, dict) else 'Not a dict'}"
        )
        if isinstance(first_row, dict):
            logger.debug(
                f"First row resolved_provider_id: {first_row.get('resolved_provider_id', 'NOT_FOUND')}"
            )
        else:
            logger.debug(
                f"First row resolved_provider_id: {getattr(first_row, 'resolved_provider_id', 'NOT_FOUND')}"
            )
    logger.debug("===============================")

    provider_preview: list[dict] = sess.get("providers") or []
    try:
        from models.database import Charge as ChargeModel  # type: ignore
    except ImportError:
        from .models.database import Charge as ChargeModel  # type: ignore
    existing = db.query(ChargeModel).all()
    existing_keys = set()
    for ch in existing:
        _svc = getattr(ch, "service_date", None)
        key = (
            _svc.isoformat() if _svc else None,
            getattr(ch, "family_member", None),
            getattr(ch, "medical_type", None),
            getattr(ch, "provider_id", None),
            (getattr(ch, "cpt_code", None) or None),
            float(getattr(ch, "charged_amount", 0) or 0),
            (getattr(ch, "charge_description", None) or None),
            (getattr(ch, "eob_claim_num", None) or None),
        )
        existing_keys.add(key)
    inserted_provider_ids_by_location: dict[str, int] = {}
    if payload.selected_provider_location_ids:
        try:
            from models.database import Provider as ProviderModel  # type: ignore
        except ImportError:
            from .models.database import Provider as ProviderModel  # type: ignore
        existing_providers = db.query(ProviderModel).all()
        provider_key_map: dict[tuple[str, str], int] = {}
        for p in existing_providers:
            key = (
                (getattr(p, "provider_name", "") or "").strip().lower(),
                (getattr(p, "provider_address", "") or "").strip().lower(),
            )
            raw_id = getattr(p, "id", None)
            if isinstance(raw_id, int):
                provider_key_map[key] = raw_id
        for prov in provider_preview:
            loc_id = prov.get("location_id")
            if not loc_id or loc_id not in payload.selected_provider_location_ids:
                continue
            key = (
                (prov.get("provider_name") or "").strip().lower(),
                (prov.get("provider_address") or "").strip().lower(),
            )
            existing_id = provider_key_map.get(key)
            if existing_id is not None and isinstance(existing_id, int):
                inserted_provider_ids_by_location[loc_id] = existing_id
                continue
            raw_name = (prov.get("provider_name") or "").strip()
            raw_addr = (prov.get("provider_address") or "").strip()
            fallback_name = None
            if not raw_name:
                if raw_addr:
                    fallback_name = raw_addr.split(",")[0][:80]
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
                pid = getattr(created_p, "id", None)
                if pid:
                    inserted_provider_ids_by_location[loc_id] = int(pid)
                    provider_key_map[key] = int(pid)
            except Exception:
                continue
    # Determine family_member_id to store (prefer payload, then session)
    fam_id_val = payload.family_member_id or sess.get("family_member_id") or None
    # Also keep a human-readable family_member label for notes/preview stored in session
    fam_label = payload.family_member or sess.get("family_member") or None
    med_val = payload.medical_type or sess.get("medical_type") or "Medical"
    prov_val = payload.provider_id or sess.get("provider_id")
    selected = set(payload.selected_indices)
    inserted_ids: list[int] = []
    skipped_duplicates = 0
    for r in rows:
        # Convert to object if it's a dict (session serialization issue)
        if isinstance(r, dict):
            r = JSONImportRow(**r)

        if r.index not in selected:
            continue

        # Use the resolved provider ID from the analysis step
        final_provider_id = r.resolved_provider_id or prov_val

        # Debug: trace provider ID assignment
        logger.debug(
            f"Row {r.index}: resolved_provider_id={r.resolved_provider_id}, prov_val={prov_val}, final={final_provider_id}"
        )
        logger.debug(f"  facility_location_id: {r.facility_location_id}")

        key = (
            r.service_date,
            fam_val,
            med_val,
            final_provider_id,
            r.cpt_code,
            float(r.charged_amount or 0),
            r.description,
            r.eob_claim_num,
        )
        is_duplicate = key in existing_keys

        # Debug: log duplicate detection
        if is_duplicate:
            logger.debug(f"DUPLICATE DETECTED: {key}")

        if is_duplicate:
            skipped_duplicates += 1
            continue

        # Get session data for attachment creation
        original_filename = sess.get("original_filename", "unknown_file.json")
        original_json_obj = sess.get("original_json_obj", {})

        # Create notes with import information
        import_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        notes_text = f"Imported from JSON id: {original_json_obj.get('id', 'N/A')}, on {import_date}"

        charge_in = {
            "service_date": r.service_date,
            "family_member_id": fam_id_val,
            "medical_type": med_val,
            "provider_id": final_provider_id,
            "cpt_code": r.cpt_code,
            "charge_description": r.description,
            "base_fee_amount": r.base_fee_amount,
            "charged_amount": r.charged_amount,
            "owed_amount": r.owed_amount,
            "insurance_discount_amount": r.insurance_discount_amount,
            "insurance_paid_amount": r.insurance_paid_amount,
            "doctor_name": r.doctor_name,
            "eob_claim_num": r.eob_claim_num,
            # Apply diagnosis code: prefer session-level header value, then parsed row, then any top-level payload override
            "diagnosis_code": (
                sess.get("diagnosis_code")
                or getattr(r, "diagnosis_code", None)
                or getattr(payload, "diagnosis_code", None)
            ),
            "notes": notes_text,
        }

        # Debug: log what we're trying to create
        logger.debug(f"Creating charge with provider_id: {final_provider_id}")
        try:
            created = charge_crud.create(db, charge_in)
            db.flush()
            raw_id = getattr(created, "id", None)
            if raw_id is not None:
                charge_id = int(raw_id)
                inserted_ids.append(charge_id)
                existing_keys.add(key)

                # Create attachment with JSON details for this charge
                try:
                    # Get provider information if available
                    provider_info = {}
                    if final_provider_id:
                        try:
                            from models.database import (
                                Provider as ProviderModel,  # type: ignore
                            )
                        except ImportError:
                            from .models.database import (
                                Provider as ProviderModel,  # type: ignore
                            )
                        provider = db.get(ProviderModel, final_provider_id)
                        if provider:
                            provider_info = {
                                "provider_id": final_provider_id,
                                "provider_name": getattr(
                                    provider, "provider_name", None
                                ),
                                "provider_address": getattr(
                                    provider, "provider_address", None
                                ),
                                "provider_group": getattr(
                                    provider, "provider_group", None
                                ),
                            }

                    # Create simplified JSON attachment data containing only relevant entries
                    def extract_relevant_fhir_entries(json_obj, charge_row):
                        """Extract only the specific EOB and related entries for this charge"""
                        relevant_entries = []

                        if (
                            isinstance(json_obj, dict)
                            and json_obj.get("resourceType") == "Bundle"
                        ):
                            entries = json_obj.get("entry", [])
                            # Find the specific EOB entry for this charge
                            target_eob_id = None
                            target_claim_num = charge_row.eob_claim_num
                            target_service_date = charge_row.service_date
                            target_cpt = charge_row.cpt_code

                            # First pass: find the matching EOB
                            for entry in entries:
                                resource = entry.get("resource", {})
                                if (
                                    resource.get("resourceType")
                                    == "ExplanationOfBenefit"
                                ):
                                    # Check if this EOB matches our charge
                                    eob_matches = False
                                    matched_items = []

                                    # 1) Check top-level EOB identifiers
                                    if target_claim_num:
                                        for identifier in resource.get(
                                            "identifier", []
                                        ):
                                            if (
                                                identifier.get("value")
                                                == target_claim_num
                                            ):
                                                eob_matches = True
                                                break

                                    # 2) Check item-level identifiers and detail-level identifiers
                                    if not eob_matches and (target_claim_num):
                                        for item in resource.get("item", []):
                                            # Check item-level identifier(s)
                                            item_identifiers = (
                                                item.get("identifier") or []
                                            )
                                            item_identifier_match = False
                                            for ident in item_identifiers:
                                                if (
                                                    isinstance(ident, dict)
                                                    and ident.get("value")
                                                    == target_claim_num
                                                ):
                                                    item_identifier_match = True
                                                    break

                                            # Check detail-level identifiers (if present)
                                            matched_details = []
                                            for detail in item.get("detail", []) or []:
                                                for ident in (
                                                    detail.get("identifier", [])
                                                    if isinstance(
                                                        detail.get("identifier"), list
                                                    )
                                                    else []
                                                ):
                                                    if (
                                                        isinstance(ident, dict)
                                                        and ident.get("value")
                                                        == target_claim_num
                                                    ):
                                                        matched_details.append(detail)
                                                        break

                                            if item_identifier_match or matched_details:
                                                # Build a pruned item that includes only matched details when present
                                                pruned_item = dict(item)
                                                if matched_details:
                                                    pruned_item = dict(item)
                                                    pruned_item[
                                                        "detail"
                                                    ] = matched_details
                                                matched_items.append(pruned_item)
                                                eob_matches = True

                                    # 3) Fallback: inspect service date and CPT code in items
                                    if not eob_matches and (
                                        target_service_date or target_cpt
                                    ):
                                        for item in resource.get("item", []):
                                            item_date = item.get("servicedDate")
                                            if not item_date:
                                                sp = item.get("servicedPeriod") or {}
                                                item_date = sp.get("start")

                                            item_cpt = None
                                            product_service = item.get(
                                                "productOrService", {}
                                            )
                                            for coding in product_service.get(
                                                "coding", []
                                            ):
                                                code = coding.get("code")
                                                if code:
                                                    item_cpt = code
                                                    break

                                            if (
                                                target_service_date
                                                and item_date
                                                and item_date == target_service_date
                                            ) or (
                                                target_cpt
                                                and item_cpt
                                                and item_cpt == target_cpt
                                            ):
                                                matched_items.append(item)
                                                eob_matches = True
                                                break

                                    if eob_matches:
                                        target_eob_id = resource.get("id")
                                        # If we found matched_items (possibly pruned details), create a pruned resource
                                        if matched_items:
                                            pruned_resource = dict(resource)
                                            # Replace items with only matched subset; preserve other top-level fields
                                            pruned_resource["item"] = matched_items
                                            relevant_entries.append(
                                                {
                                                    "resourceType": "ExplanationOfBenefit",
                                                    "id": target_eob_id,
                                                    "resource": pruned_resource,
                                                }
                                            )
                                        else:
                                            relevant_entries.append(
                                                {
                                                    "resourceType": "ExplanationOfBenefit",
                                                    "id": target_eob_id,
                                                    "resource": resource,
                                                }
                                            )
                                        break

                            # Second pass: find related Organization and CareTeam entries
                            # Only include if we found a matching EOB
                            if target_eob_id:
                                related_org_ids = set()
                                related_practitioner_ids = set()

                                # Extract references from the EOB
                                target_eob = None
                                for entry in relevant_entries:
                                    if entry.get("id") == target_eob_id:
                                        target_eob = entry.get("resource", {})
                                        break

                                if target_eob:
                                    # Extract organization references
                                    provider_ref = target_eob.get("provider", {}).get(
                                        "reference", ""
                                    )
                                    if provider_ref.startswith("Organization/"):
                                        related_org_ids.add(provider_ref.split("/")[1])

                                    # Extract facility reference
                                    facility_ref = target_eob.get("facility", {}).get(
                                        "reference", ""
                                    )
                                    if facility_ref.startswith("Organization/"):
                                        related_org_ids.add(facility_ref.split("/")[1])

                                    # Extract care team references
                                    for care_team in target_eob.get("careTeam", []):
                                        provider_ref = care_team.get(
                                            "provider", {}
                                        ).get("reference", "")
                                        if provider_ref.startswith("Practitioner/"):
                                            related_practitioner_ids.add(
                                                provider_ref.split("/")[1]
                                            )
                                        elif provider_ref.startswith("Organization/"):
                                            related_org_ids.add(
                                                provider_ref.split("/")[1]
                                            )

                                # Now find the related resources
                                for entry in entries:
                                    resource = entry.get("resource", {})
                                    resource_type = resource.get("resourceType")
                                    resource_id = resource.get("id")

                                    if (
                                        resource_type == "Organization"
                                        and resource_id in related_org_ids
                                    ):
                                        relevant_entries.append(
                                            {
                                                "resourceType": "Organization",
                                                "id": resource_id,
                                                "resource": resource,
                                            }
                                        )
                                    elif (
                                        resource_type
                                        in ["Practitioner", "PractitionerRole"]
                                        and resource_id in related_practitioner_ids
                                    ):
                                        relevant_entries.append(
                                            {
                                                "resourceType": resource_type,
                                                "id": resource_id,
                                                "resource": resource,
                                            }
                                        )
                        else:
                            # For non-FHIR JSON, include provider-related info (keep existing logic)
                            if isinstance(json_obj, dict):
                                if json_obj.get("providers"):
                                    relevant_entries.append(
                                        {
                                            "type": "providers",
                                            "data": json_obj.get("providers"),
                                        }
                                    )
                                if json_obj.get("organizations"):
                                    relevant_entries.append(
                                        {
                                            "type": "organizations",
                                            "data": json_obj.get("organizations"),
                                        }
                                    )
                                if json_obj.get("facilities"):
                                    relevant_entries.append(
                                        {
                                            "type": "facilities",
                                            "data": json_obj.get("facilities"),
                                        }
                                    )

                        return relevant_entries

                    # Extract relevant entries from original JSON for this specific charge
                    relevant_json_entries = extract_relevant_fhir_entries(
                        original_json_obj, r
                    )

                    logger.debug(
                        f"Found {len(relevant_json_entries)} relevant entries for charge {charge_id}"
                    )
                    for entry in relevant_json_entries:
                        logger.debug(
                            f"  - {entry.get('resourceType', 'Unknown')} (ID: {entry.get('id', 'N/A')})"
                        )

                    # Create simplified attachment data. Keep the main EOB number from the
                    # parsed row (`r.eob_claim_num`) — do not override it with identifiers
                    # from individual related entries (those are included in
                    # `relevant_source_entries` if needed for inspection).
                    attachment_data = {
                        "charge_reference": {
                            "charge_id": charge_id,
                            "service_date": r.service_date,
                            "description": r.description,
                            "cpt_code": r.cpt_code,
                            "eob_claim_num": r.eob_claim_num,
                        },
                        "derived_provider_info": provider_info,
                        "import_metadata": {
                            "original_filename": original_filename,
                            "import_date": import_date,
                            "family_member": fam_val,
                            "medical_type": med_val,
                            "parse_mode": sess.get("parse_mode"),
                        },
                        "relevant_source_entries": relevant_json_entries,
                    }

                    # Create attachment
                    attachment_json = _json.dumps(
                        attachment_data, indent=2, default=str
                    )
                    attachment_bytes = attachment_json.encode("utf-8")

                    attachment_create = AttachmentCreate(
                        charge_id=charge_id,
                        notes=f"Import source data for charge from {original_filename} - {len(relevant_json_entries)} related entries found",
                    )

                    # Create attachment with a descriptive filename
                    attachment_filename = (
                        f"import_source_{charge_id}_{original_filename}"
                    )

                    create_attachment(
                        db=db,
                        data=attachment_create,
                        original_filename=attachment_filename,
                        content_type="application/json",
                        file_size=len(attachment_bytes),
                        content_bytes=attachment_bytes,
                    )

                    logger.debug(f"Created attachment for charge {charge_id}")
                except Exception as e:
                    logger.debug(
                        f"Failed to create attachment for charge {charge_id}: {e}"
                    )
                    # Don't fail the import if attachment creation fails
                    pass

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
    from eob_import import create_session as eob_create_session
    from eob_import import delete_session as eob_delete_session
    from eob_import import get_session as eob_get_session
except ImportError:  # pragma: no cover
    from ..eob_import import create_session as eob_create_session
    from ..eob_import import delete_session as eob_delete_session
    from ..eob_import import get_session as eob_get_session


@router.post("/eob/upload", response_model=EOBUploadResponse)
async def eob_upload(request: Request, file: UploadFile = File(...)):
    # Accept optional multipart fields and persist them to the session
    form = await request.form()
    content = await file.read()
    session = eob_create_session(
        file.filename or "eob_upload", content, file.content_type
    )

    # Persist optional top-level values if provided
    def _get_text(field_name: str) -> str | None:
        v = form.get(field_name)
        from fastapi import UploadFile as _UF

        if v is None or isinstance(v, _UF):
            return None
        s = str(v).strip()
        return s or None

    fm = _get_text("family_member")
    if fm:
        try:
            session.family_member = fm
        except Exception:
            pass
    mt = _get_text("medical_type")
    if mt:
        try:
            session.medical_type = mt
        except Exception:
            pass
    dn = _get_text("doctor_name")
    if dn:
        try:
            session.doctor_name = dn
        except Exception:
            pass
    diag = _get_text("diagnosis_code")
    if diag:
        try:
            session.diagnosis_code = diag
        except Exception:
            pass
    en = _get_text("eob_number") or _get_text("eob_number")
    if en:
        try:
            session.eob_number = en
        except Exception:
            pass
    prov_txt = _get_text("provider_id")
    if prov_txt:
        try:
            session.provider_id = int(prov_txt)
        except Exception:
            # ignore non-int provider ids
            pass

    data = session.serialize()
    return EOBUploadResponse(
        session_id=session.id, row_count=data["row_count"], filename=data["filename"]
    )


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
    medical_type: str | None = None
    diagnosis_code: str | None = None


class EOBImportResult(BaseModel):
    imported_count: int
    created_charge_ids: list[int]


@router.post("/eob/session/{session_id}/import", response_model=EOBImportResult)
def eob_import(
    session_id: str, payload: EOBImportRequest, db: Session = Depends(get_db)
):
    session = eob_get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    # provider_id is optional now: if not provided, try to infer per-row or from session mappings
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
    # Attempt to infer batch-level values when top-level inputs are not provided
    batch_family = None
    batch_provider_override = getattr(payload, "provider_id", None)
    batch_eob_num = None
    batch_doctor = None

    # Prefer explicit session-level family if present
    try:
        sess_family = (
            session.get("family_member") if isinstance(session, dict) else None
        )
        if sess_family:
            batch_family = sess_family
    except Exception:
        batch_family = None

    # Attempt to resolve batch-level family_member string to an id (if available)
    batch_family_id = None
    try:
        from models.database import FamilyMember as FamilyMemberModel  # type: ignore
    except Exception:
        from .models.database import FamilyMember as FamilyMemberModel
    try:
        if batch_family:
            # Try parse as int id first
            try:
                parsed = int(batch_family)
                fam_obj = db.get(FamilyMemberModel, parsed)
                if fam_obj:
                    batch_family_id = getattr(fam_obj, "id", None)
            except Exception:
                # Fallback: match by the label generation used in templates (emoji or 'First Last')
                fams = db.query(FamilyMemberModel).all()
                for fm in fams:
                    label = (
                        fm.emoji
                        if getattr(fm, "emoji", None)
                        else (
                            getattr(fm, "first_name", "")
                            + (
                                " " + getattr(fm, "last_name", "")
                                if getattr(fm, "last_name", None)
                                else ""
                            )
                        )
                    )
                    if label == batch_family:
                        batch_family_id = getattr(fm, "id", None)
                        break
    except Exception:
        batch_family_id = None

    # Inspect overrides for any provided eob or doctor or provider-like name
    for ov in overrides_map.values():
        if not batch_eob_num:
            batch_eob_num = ov.get("eob_claim_num") or ov.get("eob_number")
        if not batch_doctor:
            batch_doctor = ov.get("doctor_name")
        if not batch_provider_override:
            # try facility_location_name or provider_name keys
            prov_name = ov.get("provider_name") or ov.get("facility_location_name")
            if prov_name:
                try:
                    from models.database import (
                        Provider as ProviderModel,  # type: ignore
                    )
                except Exception:
                    from .models.database import (
                        Provider as ProviderModel,  # type: ignore
                    )
                found = (
                    db.query(ProviderModel)
                    .filter(ProviderModel.provider_name.ilike(prov_name))
                    .first()
                )
                if found:
                    batch_provider_override = getattr(found, "id", None)
        if batch_eob_num and batch_doctor and batch_provider_override:
            break

    # If still missing eob/doctor, try parsed rows in session
    if not batch_eob_num:
        for pr in session.rows:
            val = getattr(pr, "eob_claim_num", None)
            if val:
                batch_eob_num = val
                break
    if not batch_doctor:
        for pr in session.rows:
            val = getattr(pr, "doctor_name", None)
            if val:
                batch_doctor = val
                break
    created_ids: list[int] = []
    highest_row = None
    highest_amount = -1.0
    for r in rows:
        amt = (
            overrides_map.get(r.index, {}).get("charged_amount")
            if r.index in overrides_map
            else r.charged_amount
        ) or 0
        if amt > highest_amount:
            highest_amount = amt
            highest_row = r.index
    for r in rows:
        try:
            ov = overrides_map.get(r.index, {})
            ov_service_date = (
                ov.get("service_date")
                if ov.get("service_date")
                else (r.service_date.isoformat() if r.service_date else None)
            )
            # Resolve family member for this row: allow id or label
            fam_field = ov.get("family_member", r.family_member or batch_family)
            family_member_id_for_row = None
            if fam_field is not None:
                try:
                    # If a numeric id was provided
                    parsed_id = int(fam_field)
                    fam_obj = db.get(FamilyMemberModel, parsed_id)
                    if fam_obj:
                        family_member_id_for_row = getattr(fam_obj, "id", None)
                except Exception:
                    # Match by label (emoji or 'First Last') using same logic as UI
                    try:
                        fams = db.query(FamilyMemberModel).all()
                        for fm in fams:
                            label = (
                                fm.emoji
                                if getattr(fm, "emoji", None)
                                else (
                                    getattr(fm, "first_name", "")
                                    + (
                                        " " + getattr(fm, "last_name", "")
                                        if getattr(fm, "last_name", None)
                                        else ""
                                    )
                                )
                            )
                            if label == fam_field:
                                family_member_id_for_row = getattr(fm, "id", None)
                                break
                    except Exception:
                        family_member_id_for_row = None

            # Fallback to batch-level resolved id
            if family_member_id_for_row is None:
                family_member_id_for_row = batch_family_id

            # Determine medical_type: prefer per-row override, then explicit
            # payload value, then session-level value, then fallback to 'Medical'.
            row_medical_type = (
                ov.get("medical_type")
                or getattr(payload, "medical_type", None)
                or (
                    session.get("medical_type")
                    if isinstance(session, dict)
                    else getattr(session, "medical_type", None)
                )
                or "Medical"
            )

            charge_in = {
                "service_date": ov_service_date,
                "family_member_id": family_member_id_for_row,
                "medical_type": row_medical_type,
                "provider_id": payload.provider_id or batch_provider_override,
                "cpt_code": ov.get("cpt_code", r.cpt_code),
                "charge_description": ov.get("description", r.description),
                "charged_amount": ov.get("charged_amount", r.charged_amount),
                "owed_amount": ov.get("owed_amount", r.owed_amount),
                "insurance_paid_amount": ov.get(
                    "insurance_paid_amount", getattr(r, "insurance_paid_amount", None)
                ),
                "insurance_discount_amount": ov.get(
                    "insurance_discount_amount",
                    getattr(r, "insurance_discount_amount", None),
                ),
                "base_fee_amount": ov.get(
                    "base_fee_amount", getattr(r, "base_fee_amount", None)
                ),
                "reimbursement_amount": ov.get(
                    "reimbursement_amount", getattr(r, "reimbursement_amount", None)
                ),
                # Prefer explicit override value (from UI top-level doctor field applied into overrides),
                # otherwise fall back to any doctor_name parsed on the row
                "doctor_name": ov.get("doctor_name", getattr(r, "doctor_name", None))
                or batch_doctor,
                # Prefer override eob_claim_num (from UI) then parsed row value then batch fallback
                "eob_claim_num": ov.get(
                    "eob_claim_num", getattr(r, "eob_claim_num", None)
                )
                or batch_eob_num,
                # diagnosis_code: prefer explicit per-row override, then session-level
                # header value, then parsed row, then payload-level override.
                "diagnosis_code": (
                    ov.get("diagnosis_code", None)
                    or (
                        session.get("diagnosis_code")
                        if isinstance(session, dict)
                        else getattr(session, "diagnosis_code", None)
                    )
                    or getattr(r, "diagnosis_code", None)
                    or getattr(payload, "diagnosis_code", None)
                ),
                "notes": None,
            }
            # No per-row placeholder notes here; we'll attach the EOB to the highest
            # charge after creating all rows and then update other charges with
            # an exact reference to the charge that received the attachment.
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
                if getattr(r, "index", None) in sel_set:
                    if r.index == highest_row:
                        highest_created_id = created_ids[idx_tracker]
                        break
                    idx_tracker += 1
            if highest_created_id is not None:
                from schemas.schemas import (
                    AttachmentCreate as _AttachmentCreate,  # type: ignore
                )

                # Make the attachment notes and filename include the original
                # uploaded filename when available so users can identify the
                # source easily in the UI.
                orig_name = None
                try:
                    orig_name = (
                        session.get("filename")
                        if isinstance(session, dict)
                        else getattr(session, "filename", None)
                    )
                except Exception:
                    orig_name = None

                notes_for_attachment = (
                    f"Original EOB document: {orig_name}"
                    if orig_name
                    else "Original EOB document"
                )

                att_in = _AttachmentCreate(
                    charge_id=highest_created_id,
                    payment_id=None,
                    notes=notes_for_attachment,
                )

                attachment_filename = (
                    f"original_eob_{highest_created_id}_{orig_name}"
                    if orig_name
                    else f"original_eob_{highest_created_id}"
                )

                create_attachment(
                    db,
                    data=att_in,
                    original_filename=attachment_filename,
                    content_type=session.content_type or "application/octet-stream",
                    file_size=len(session.file_bytes),
                    content_bytes=session.file_bytes,
                )
                db.commit()
                # Update notes on the other created charges to point to the charge
                # that received the attachment so users can discover where the
                # file is stored. Use a clear, human-friendly message.
                try:
                    note_text = f"Created via bulk upload, attachment on charge #{highest_created_id}"
                    for cid in created_ids:
                        if cid == highest_created_id:
                            continue
                        try:
                            # Safely append or set notes on the charge
                            from models.database import (
                                Charge as ChargeModel,  # type: ignore
                            )
                        except ImportError:
                            from .models.database import (
                                Charge as ChargeModel,  # type: ignore
                            )
                        ch_obj = (
                            db.query(ChargeModel).filter(ChargeModel.id == cid).first()
                        )
                        if not ch_obj:
                            continue
                        existing = (getattr(ch_obj, "notes", None) or "").strip()
                        if existing:
                            new_notes = f"{existing}; {note_text}"
                        else:
                            new_notes = note_text
                        setattr(ch_obj, "notes", new_notes)
                    db.commit()
                except Exception:
                    # Don't let note-updating failures block the import outcome
                    pass
        except Exception:
            pass
    eob_delete_session(session_id)
    return EOBImportResult(
        imported_count=len(created_ids), created_charge_ids=created_ids
    )
