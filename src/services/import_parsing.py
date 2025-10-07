"""Import parsing helpers for JSON, FHIR Bundle, and heuristic extraction.

Extracted from the monolithic app.py to allow reuse and easier testing.
"""
from __future__ import annotations
from typing import Any, List, Tuple

def _normalize_str(val):  # exported for compatibility
    if val is None:
        return None
    try:
        s = str(val).strip()
        return s if s else None
    except Exception:
        return None

def parse_fhir_bundle(bundle: dict) -> list[dict]:
    """Public wrapper around the original _parse_fhir_bundle.

    Returns a list of row dicts plus a synthetic provider candidate meta row (first) if present.
    """
    return _parse_fhir_bundle(bundle)

def heuristic_extract_rows(obj: Any, max_items_per_array: int = 500) -> Tuple[List[dict], dict]:
    return _heuristic_extract_rows(obj, max_items_per_array=max_items_per_array)

def _parse_fhir_bundle(bundle: dict) -> list[dict]:  # original logic imported verbatim (lightly trimmed comments)
    rows: list[dict] = []
    provider_candidates: list[dict] = []
    def _extract_location_id(ref: str) -> str | None:
        if not ref or not isinstance(ref, str):
            return None
        parts = ref.split('/')
        for i, p in enumerate(parts):
            if p == 'Location' and i + 1 < len(parts):
                return parts[i+1]
        tail = parts[-1]
        if tail and tail.lower() != '_history':
            return tail
        return None
    if not isinstance(bundle, dict):
        return rows
    entries = bundle.get("entry") or []
    by_full_url: dict[str, dict] = {}
    practitioner_names: dict[str, str] = {}
    practitioner_provider_candidates: list[dict] = []
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        fu = ent.get("fullUrl")
        res = ent.get("resource") if isinstance(ent.get("resource"), dict) else None
        if fu and isinstance(res, dict):
            by_full_url[fu] = res
            if res.get("resourceType") == "Practitioner":
                name_list = res.get("name") or []
                display = None
                if isinstance(name_list, list) and name_list:
                    n0 = name_list[0]
                    if isinstance(n0, dict):
                        family = _normalize_str(n0.get("family"))
                        given_list = n0.get("given") or []
                        given_first = None
                        if isinstance(given_list, list) and given_list:
                            given_first = _normalize_str(given_list[0])
                        parts = [p for p in [family, given_first] if p]
                        if parts:
                            display = ", ".join(parts)
                if display:
                    practitioner_names[fu] = display
                addr = None
                address_list = res.get("address") or []
                if isinstance(address_list, list) and address_list:
                    a0 = address_list[0]
                    if isinstance(a0, dict):
                        line0 = None
                        lines = a0.get("line") if isinstance(a0.get("line"), list) else []
                        if lines:
                            line0 = _normalize_str(lines[0])
                        city = _normalize_str(a0.get("city"))
                        state = _normalize_str(a0.get("state"))
                        postal = _normalize_str(a0.get("postalCode"))
                        parts = [p for p in [line0, city, state, postal] if p]
                        if parts:
                            addr = ", ".join(parts)
                pract_id = _normalize_str(res.get("id"))
                if display:
                    practitioner_provider_candidates.append({
                        "location_id": f"pract:{pract_id}" if pract_id else f"pract:{len(practitioner_provider_candidates)+1}",
                        "provider_name": display,
                        "provider_address": addr,
                        "full_url": fu,
                        "source": "practitioner"
                    })
    for ent in entries:
        res_loc = ent.get("resource") if isinstance(ent, dict) else None
        if not isinstance(res_loc, dict):
            continue
        if res_loc.get("resourceType") == "Location":
            loc_id = _normalize_str(res_loc.get("id"))
            name = _normalize_str(res_loc.get("name"))
            addr = res_loc.get("address") if isinstance(res_loc.get("address"), dict) else None
            address_str = None
            if addr:
                line0 = None
                lines = addr.get("line") if isinstance(addr.get("line"), list) else []
                if lines:
                    line0 = _normalize_str(lines[0])
                city = _normalize_str(addr.get("city"))
                state = _normalize_str(addr.get("state"))
                postal = _normalize_str(addr.get("postalCode"))
                parts = [p for p in [line0, city, state, postal] if p]
                if parts:
                    address_str = ", ".join(parts)
            provider_candidates.append({
                "location_id": loc_id,
                "provider_name": name,
                "provider_address": address_str,
                "full_url": ent.get("fullUrl"),
                "source": "location"
            })
    if practitioner_provider_candidates:
        provider_candidates.extend(practitioner_provider_candidates)
    for ent in entries:
        res = ent.get("resource") if isinstance(ent, dict) else None
        if not isinstance(res, dict):
            continue
        rtype = res.get("resourceType")
        if rtype == "ExplanationOfBenefit":
            eob_claim_num = None
            identifiers = res.get("identifier") or []
            for ident in identifiers:
                if not isinstance(ident, dict):
                    continue
                id_type = ident.get("type") or {}
                coding_list = id_type.get("coding") or []
                for c in coding_list:
                    if not isinstance(c, dict):
                        continue
                    if (c.get("code") or "").lower() == "uc":
                        eob_claim_num = _normalize_str(ident.get("value")) or eob_claim_num
                        break
                if eob_claim_num:
                    break
            doctor_name = None
            careteam = res.get("careteam") or []
            for ct in careteam:
                if not isinstance(ct, dict):
                    continue
                prov_ref = ct.get("provider", {}).get("reference") if isinstance(ct.get("provider"), dict) else None
                if prov_ref and isinstance(prov_ref, str):
                    for fu, pres in by_full_url.items():
                        if pres.get("resourceType") == "Practitioner" and (fu.endswith(prov_ref) or prov_ref.endswith(pres.get("id", ""))):
                            doctor_name = practitioner_names.get(fu)
                            if not doctor_name:
                                name_list = pres.get("name") or []
                                if isinstance(name_list, list) and name_list:
                                    n0 = name_list[0]
                                    if isinstance(n0, dict):
                                        family = _normalize_str(n0.get("family"))
                                        given_list = n0.get("given") or []
                                        given_first = None
                                        if isinstance(given_list, list) and given_list:
                                            given_first = _normalize_str(given_list[0])
                                        parts = [p for p in [family, given_first] if p]
                                        if parts:
                                            doctor_name = ", ".join(parts)
                            if doctor_name:
                                break
                if doctor_name:
                    break
            items = res.get("item") or []
            for it in items:
                if not isinstance(it, dict):
                    continue
                svc_date = None
                if isinstance(it.get("servicedDate"), str):
                    svc_date = it.get("servicedDate")
                elif isinstance(it.get("servicedPeriod"), dict):
                    svc_date = it.get("servicedPeriod", {}).get("start")
                cpt_code = None
                charge_description = None
                product = it.get("productOrService")
                if isinstance(product, dict):
                    coding = product.get("coding") or []
                    if coding:
                        first = coding[0]
                        if isinstance(first, dict):
                            cpt_code = _normalize_str(first.get("code"))
                            charge_description = _normalize_str(first.get("display")) or _normalize_str(first.get("text"))
                if not charge_description:
                    charge_description = _normalize_str(it.get("description")) or _normalize_str(product.get("text") if isinstance(product, dict) else None)
                base_fee_amount = None
                charged_amount = None
                owed_amount_components: list[float] = []
                insurance_discount_amount = None
                insurance_paid_amount = None
                adjudications = it.get("adjudication") or []
                for adj in adjudications:
                    if not isinstance(adj, dict):
                        continue
                    cat = adj.get("category", {}) or {}
                    coding_list = cat.get("coding") or []
                    if not isinstance(coding_list, list):
                        continue
                    code_val = None
                    for c in coding_list:
                        if isinstance(c, dict) and c.get("code"):
                            code_val = c.get("code")
                            break
                    if not code_val:
                        continue
                    code_low = code_val.lower()
                    amt_val = None
                    if isinstance(adj.get("amount"), dict):
                        amt_val = adj.get("amount", {}).get("value")
                    if code_low == "submitted":
                        base_fee_amount = base_fee_amount or amt_val
                    elif code_low == "eligible":
                        charged_amount = charged_amount or amt_val
                    elif code_low in {"deductible", "copay", "coinsurance"}:
                        if isinstance(amt_val, (int, float)):
                            owed_amount_components.append(float(amt_val))
                    elif code_low == "discount":
                        insurance_discount_amount = insurance_discount_amount or amt_val
                    elif code_low == "benefit":
                        insurance_paid_amount = insurance_paid_amount or amt_val
                owed_amount = sum(owed_amount_components) if owed_amount_components else None
                facility_ref = None
                facility = it.get("facility") or res.get("facility")
                if isinstance(facility, dict):
                    facility_ref = facility.get("reference")
                facility_location_id = None
                facility_location_name = None
                if facility_ref and isinstance(facility_ref, str):
                    extracted = _extract_location_id(facility_ref)
                    facility_location_id = extracted
                    for pc in provider_candidates:
                        fu_val = pc.get("full_url")
                        if pc.get("location_id") == extracted or (isinstance(fu_val, str) and fu_val.endswith(facility_ref)):
                            facility_location_name = pc.get("provider_name")
                            break
                if facility_location_name is None and doctor_name:
                    facility_location_name = doctor_name
                rows.append({
                    "service_date": svc_date,
                    "cpt_code": cpt_code,
                    "description": charge_description,
                    "base_fee_amount": base_fee_amount,
                    "charged_amount": charged_amount,
                    "owed_amount": owed_amount,
                    "insurance_discount_amount": insurance_discount_amount,
                    "insurance_paid_amount": insurance_paid_amount,
                    "doctor_name": doctor_name,
                    "eob_claim_num": eob_claim_num,
                    "_facility_ref": facility_ref,
                    "facility_location_id": facility_location_id,
                    "facility_location_name": facility_location_name,
                })
        elif rtype == "Claim":
            doctor_name = None
            items = res.get("item") or []
            for it in items:
                if not isinstance(it, dict):
                    continue
                svc_date = None
                if isinstance(it.get("servicedDate"), str):
                    svc_date = it.get("servicedDate")
                elif isinstance(it.get("servicedPeriod"), dict):
                    svc_date = it.get("servicedPeriod", {}).get("start")
                cpt_code = None
                description = _normalize_str(it.get("description"))
                product = it.get("productOrService")
                if isinstance(product, dict):
                    coding = product.get("coding") or []
                    if coding:
                        first = coding[0]
                        if isinstance(first, dict):
                            cpt_code = _normalize_str(first.get("code"))
                            if not description:
                                description = _normalize_str(first.get("display")) or _normalize_str(first.get("text"))
                charged_amount = None
                if isinstance(it.get("net"), dict):
                    charged_amount = it.get("net", {}).get("value")
                facility_ref = None
                facility = it.get("facility") or res.get("facility")
                if isinstance(facility, dict):
                    facility_ref = facility.get("reference")
                facility_location_id = None
                facility_location_name = None
                if facility_ref and isinstance(facility_ref, str):
                    extracted = _extract_location_id(facility_ref)
                    facility_location_id = extracted
                    for pc in provider_candidates:
                        fu_val = pc.get("full_url")
                        if pc.get("location_id") == extracted or (isinstance(fu_val, str) and fu_val.endswith(facility_ref)):
                            facility_location_name = pc.get("provider_name")
                            break
                if facility_location_name is None and doctor_name:
                    facility_location_name = doctor_name
                rows.append({
                    "service_date": svc_date,
                    "description": description,
                    "cpt_code": cpt_code,
                    "charged_amount": charged_amount,
                    "owed_amount": None,
                    "_facility_ref": facility_ref,
                    "facility_location_id": facility_location_id,
                    "facility_location_name": facility_location_name,
                })
    if provider_candidates and rows:
        loc_candidates = {pc.get("location_id"): pc for pc in provider_candidates if pc.get("source") == "location" and not (pc.get("provider_name") or "").strip()}
        if loc_candidates:
            for r in rows:
                loc_id = r.get("facility_location_id")
                doc_name = r.get("doctor_name")
                if loc_id and doc_name and loc_id in loc_candidates:
                    cand = loc_candidates[loc_id]
                    if not (cand.get("provider_name") or "").strip():
                        cand["provider_name"] = doc_name
                        cand["fallback_from_practitioner"] = True
    cleaned: list[dict] = []
    for r in rows:
        has_value = any(v not in (None, "") for k, v in r.items() if k not in {"doctor_name","eob_claim_num"})
        if not has_value:
            continue
        r["description"] = _normalize_str(r.get("description")) or _normalize_str(r.get("charge_description"))
        cleaned.append(r)
    if provider_candidates:
        cleaned.insert(0, {"_provider_candidates": provider_candidates})
    return cleaned

def _heuristic_extract_rows(obj, max_items_per_array: int = 500):  # original logic with minimal changes
    from collections import deque
    candidates_meta = []
    extracted: list[dict] = []
    visited_ids = set()
    date_keys = {"service_date", "serviceDate", "dateOfService", "dos", "date", "startDate"}
    desc_keys = {"description", "procedureDescription", "serviceDescription", "lineDescription", "chargeDescription"}
    cpt_keys = {"cpt", "cptCode", "cpt_code", "procedureCode", "code"}
    charge_keys = {"charged_amount", "billed_amount", "billedAmount", "totalCharge", "chargeAmount", "allowedAmount", "amount", "net", "netAmount"}
    owed_keys = {"owed_amount", "patientResponsibility", "patientAmount", "patientOwed", "patientPay", "patientBalance"}
    def score_dict(d: dict) -> int:
        s = 0
        lower_keys = {k.lower(): k for k in d.keys()}
        if any(k in lower_keys for k in (k.lower() for k in date_keys)):
            s += 1
        if any(k in lower_keys for k in (k.lower() for k in desc_keys)):
            s += 1
        if any(k in lower_keys for k in (k.lower() for k in cpt_keys)):
            s += 1
        if any(k in lower_keys for k in (k.lower() for k in charge_keys)):
            s += 1
        if any(k in lower_keys for k in (k.lower() for k in owed_keys)):
            s += 1
        return s
    dq = deque([(obj, 0, None)])
    max_depth = 10
    while dq:
        cur, depth, parent_key = dq.popleft()
        if id(cur) in visited_ids:
            continue
        visited_ids.add(id(cur))
        if depth > max_depth:
            continue
        if isinstance(cur, dict):
            for k, v in cur.items():
                dq.append((v, depth + 1, k))
        elif isinstance(cur, list):
            dict_items = [it for it in cur if isinstance(it, dict)]
            if not dict_items:
                for it in cur:
                    dq.append((it, depth + 1, parent_key))
                continue
            sample = dict_items[: min(len(dict_items), 5)]
            scores = [score_dict(d) for d in sample]
            avg_score = sum(scores) / len(scores) if scores else 0
            if avg_score >= 2:
                take = dict_items[: max_items_per_array]
                for d in take:
                    def first_key(dct, keys_set):
                        for kk in dct.keys():
                            lower_name = kk.lower()
                            for target in keys_set:
                                if lower_name == target.lower():
                                    return kk
                        return None
                    date_key = first_key(d, date_keys)
                    desc_key = first_key(d, desc_keys)
                    cpt_key = first_key(d, cpt_keys)
                    charged_key = first_key(d, charge_keys)
                    owed_key = first_key(d, owed_keys)
                    row = {
                        "service_date": d.get(date_key) if date_key else None,
                        "description": d.get(desc_key) if desc_key else None,
                        "cpt_code": d.get(cpt_key) if cpt_key else None,
                        "charged_amount": d.get(charged_key) if charged_key else None,
                        "owed_amount": d.get(owed_key) if owed_key else None,
                    }
                    if any(v not in (None, "") for v in row.values()):
                        extracted.append(row)
                candidates_meta.append({
                    "parent_key": parent_key,
                    "depth": depth,
                    "array_length": len(cur),
                    "sample_scores": scores,
                    "avg_score": avg_score,
                })
            else:
                for it in dict_items:
                    dq.append((it, depth + 1, parent_key))
    diagnostics = {"candidates": candidates_meta, "extracted_count": len(extracted)}
    return extracted, diagnostics

__all__ = [
    "_normalize_str",
    "parse_fhir_bundle",
    "heuristic_extract_rows",
]
