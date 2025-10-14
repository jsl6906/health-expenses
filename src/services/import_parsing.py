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


def heuristic_extract_rows(
    obj: Any, max_items_per_array: int = 500
) -> Tuple[List[dict], dict]:
    return _heuristic_extract_rows(obj, max_items_per_array=max_items_per_array)


def _parse_fhir_bundle(
    bundle: dict,
) -> list[dict]:  # restructured provider extraction logic
    rows: list[dict] = []
    provider_candidates: list[dict] = []

    def _extract_location_id(ref: str) -> str | None:
        if not ref or not isinstance(ref, str):
            return None
        parts = ref.split("/")
        for i, p in enumerate(parts):
            if p == "Location" and i + 1 < len(parts):
                return parts[i + 1]
        tail = parts[-1]
        if tail and tail.lower() != "_history":
            return tail
        return None

    def _construct_address(resource: dict) -> str | None:
        """Extract and construct address from a resource."""
        address_list = resource.get("address") or []
        if isinstance(address_list, list) and address_list:
            a0 = address_list[0]
            if isinstance(a0, dict):
                line_parts = []
                lines = a0.get("line") if isinstance(a0.get("line"), list) else []
                if lines:
                    # Concatenate all line entries if it's an array
                    for line in lines:
                        line_str = _normalize_str(line)
                        if line_str:
                            line_parts.append(line_str)
                line0 = " ".join(line_parts) if line_parts else None
                city = _normalize_str(a0.get("city"))
                state = _normalize_str(a0.get("state"))
                postal = _normalize_str(a0.get("postalCode"))
                parts = [p for p in [line0, city, state, postal] if p]
                if parts:
                    return ", ".join(parts)
        return None

    def _extract_name(resource: dict) -> str | None:
        """Extract name from a practitioner or organization resource."""
        if resource.get("resourceType") == "Practitioner":
            name_list = resource.get("name") or []
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
                        return ", ".join(parts)
        elif resource.get("resourceType") == "Organization":
            return _normalize_str(resource.get("name"))
        elif resource.get("resourceType") == "Location":
            return _normalize_str(resource.get("name"))
        return None

    if not isinstance(bundle, dict):
        return rows

    entries = bundle.get("entry") or []
    by_full_url: dict[str, dict] = {}

    # Build lookup table for all resources
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        fu = ent.get("fullUrl")
        res = ent.get("resource") if isinstance(ent.get("resource"), dict) else None
        if fu and isinstance(res, dict):
            by_full_url[fu] = res

    # Step 1: Filter to ExplanationOfBenefit entries and gather provider links
    eob_provider_debug = []
    provider_name_address_pairs = []

    print("=== COMPREHENSIVE EOB ANALYSIS ===")

    for ent in entries:
        res = ent.get("resource") if isinstance(ent, dict) else None
        if (
            not isinstance(res, dict)
            or res.get("resourceType") != "ExplanationOfBenefit"
        ):
            continue

        eob_id = res.get("id")
        provider_refs = []

        print(f"\n--- EOB {eob_id} ---")

        # Gather facility references first (priority)
        facility = res.get("facility")
        if isinstance(facility, dict):
            facility_ref = facility.get("reference")
            if facility_ref:
                provider_refs.append(facility_ref)
                print(f"EOB-level facility ref: {facility_ref}")
        else:
            print("No EOB-level facility reference")

        # Also check item-level facility references
        items = res.get("item") or []
        print(f"Items: {len(items)}")
        for i, it in enumerate(items):
            if isinstance(it, dict):
                item_facility = it.get("facility")
                if isinstance(item_facility, dict):
                    item_facility_ref = item_facility.get("reference")
                    if item_facility_ref and item_facility_ref not in provider_refs:
                        provider_refs.append(item_facility_ref)
                        print(f"  Item[{i}] facility ref: {item_facility_ref}")

        # Then add careTeam provider references
        careteam = res.get("careTeam") or []
        print(f"CareTeam entries: {len(careteam)}")
        for i, ct in enumerate(careteam):
            if isinstance(ct, dict):
                prov_ref = (
                    ct.get("provider", {}).get("reference")
                    if isinstance(ct.get("provider"), dict)
                    else None
                )
                if prov_ref:
                    provider_refs.append(prov_ref)
                    print(f"  CareTeam[{i}] provider ref: {prov_ref}")
                else:
                    print(f"  CareTeam[{i}] no provider reference")
                    # Debug: show what's actually in the careTeam entry
                    print(f"    Raw careTeam entry: {ct}")
            else:
                print(f"  CareTeam[{i}] not a dict: {type(ct)}")

        print(f"Total unique provider refs for EOB {eob_id}: {provider_refs}")

        # Step 2: Pull in associated entries based on reference values
        linked_providers = []
        for ref in provider_refs:
            if not ref:
                continue
            print(f"  Looking for reference: {ref}")
            # Find matching resource by reference
            matched_resource = None
            for fu, resource in by_full_url.items():
                resource_id = resource.get("id", "")
                resource_type = resource.get("resourceType", "")

                # Try multiple matching strategies
                if (
                    fu.endswith(ref)
                    or ref.endswith(resource_id)
                    or ref == f"{resource_type}/{resource_id}"
                ):
                    matched_resource = resource
                    print(f"    MATCHED: {fu} -> {resource_type} {resource_id}")
                    break

            if matched_resource:
                extracted_name = _extract_name(matched_resource)
                extracted_address = _construct_address(matched_resource)

                print(f"    Resource type: {matched_resource.get('resourceType')}")
                print(f"    Extracted name: {extracted_name}")
                print(f"    Extracted address: {extracted_address}")

                # Debug address extraction in detail
                if matched_resource.get("resourceType") in [
                    "Practitioner",
                    "Organization",
                    "Location",
                ]:
                    address_list = matched_resource.get("address") or []
                    print(f"    Raw address data: {address_list}")

                linked_providers.append(
                    {
                        "reference": ref,
                        "resource": matched_resource,
                        "name": extracted_name,
                        "address": extracted_address,
                        "resource_type": matched_resource.get("resourceType"),
                        "resource_id": matched_resource.get("id"),
                    }
                )
            else:
                print(f"    NO MATCH FOUND for {ref}")
                # Show what resources are available for debugging
                print(
                    f"    Available resources: {[(fu.split('/')[-1], res.get('resourceType'), res.get('id')) for fu, res in list(by_full_url.items())[:5]]}"
                )

        # Debug info for this EOB
        debug_entry = {
            "eob_id": eob_id,
            "provider_refs": provider_refs,
            "linked_providers": linked_providers,
        }
        eob_provider_debug.append(debug_entry)

        # Step 3: Derive name and address from first available in the array, plus doctor_name from careTeam
        derived_name = None
        derived_address = None
        doctor_name = None

        print(f"  Deriving name/address from {len(linked_providers)} linked providers:")

        # Extract doctor_name from careTeam providers only (ignore facility)
        careteam = res.get("careTeam") or []
        for ct in careteam:
            if isinstance(ct, dict):
                prov_ref = (
                    ct.get("provider", {}).get("reference")
                    if isinstance(ct.get("provider"), dict)
                    else None
                )
                if prov_ref:
                    # Find the linked provider for this careTeam reference
                    for provider in linked_providers:
                        if provider["reference"] == prov_ref and provider["name"]:
                            doctor_name = provider["name"]
                            print(f"    -> Doctor name from careTeam: {doctor_name}")
                            break
                if doctor_name:
                    break

        # Derive facility name and address from first available in priority order (facility first, then careTeam)
        for j, provider in enumerate(linked_providers):
            print(
                f"    Provider[{j}]: name='{provider['name']}', address='{provider['address']}', ref='{provider['reference']}'"
            )
            if not derived_name and provider["name"]:
                derived_name = provider["name"]
                print(f"      -> Using this name: {derived_name}")
            if not derived_address and provider["address"]:
                derived_address = provider["address"]
                print(f"      -> Using this address: {derived_address}")
            if derived_name and derived_address:
                break

        print(
            f"  FINAL: name='{derived_name}', address='{derived_address}', doctor='{doctor_name}'"
        )

        # If no name or address found, use first provider reference as fallback name
        if not derived_name and not derived_address and provider_refs:
            # Use the first provider reference as the name
            first_ref = provider_refs[0]
            derived_name = first_ref
            print(f"  -> Using first provider ref as fallback name: {derived_name}")

        if derived_name or derived_address:
            provider_name_address_pairs.append(
                {
                    "provider_name": derived_name,
                    "provider_address": derived_address,
                    "doctor_name": doctor_name,
                    "eob_id": eob_id,
                    "source_refs": provider_refs,
                }
            )
            print("  -> Added to provider pairs")
        else:
            print(
                "  -> NOT ADDED (no name or address, and no provider refs for fallback)"
            )

    print("=== PROVIDER PAIRS SUMMARY ===")
    print(f"Total EOBs processed: {len(eob_provider_debug)}")
    print(f"Provider pairs created: {len(provider_name_address_pairs)}")
    for pair in provider_name_address_pairs:
        print(
            f"  {pair['eob_id']}: '{pair['provider_name']}' @ '{pair['provider_address']}' (doctor: '{pair['doctor_name']}')"
        )

    # Step 4: Filter to unique name/address combinations and create lookup by EOB
    seen_combinations = set()
    unique_providers = []
    eob_to_provider_map = {}  # Map EOB ID to derived provider info

    print("=== UNIQUE PROVIDER FILTERING ===")
    for pair in provider_name_address_pairs:
        name = pair["provider_name"] or ""
        address = pair["provider_address"] or ""
        combination_key = (name.strip().lower(), address.strip().lower())

        print(f"Processing: '{name}' @ '{address}' -> key: {combination_key}")

        # Store EOB mapping regardless of uniqueness (for charge processing)
        eob_to_provider_map[pair["eob_id"]] = {
            "provider_name": name,
            "provider_address": address,
            "doctor_name": pair["doctor_name"],
        }

        if combination_key not in seen_combinations and (name or address):
            seen_combinations.add(combination_key)
            unique_providers.append(
                {
                    "location_id": f"derived:{len(unique_providers)+1}",
                    "provider_name": name,
                    "provider_address": address,
                    "source": "derived_from_eob",
                    "source_eobs": [
                        p["eob_id"]
                        for p in provider_name_address_pairs
                        if (p["provider_name"] or "").strip().lower()
                        == name.strip().lower()
                        and (p["provider_address"] or "").strip().lower()
                        == address.strip().lower()
                    ],
                }
            )
            print(f"  -> ADDED as unique provider #{len(unique_providers)}")
        else:
            print("  -> SKIPPED (duplicate or empty name/address)")

    print(f"\nFINAL UNIQUE PROVIDERS: {len(unique_providers)}")
    for up in unique_providers:
        print(
            f"  {up['location_id']}: '{up['provider_name']}' @ '{up['provider_address']}' (EOBs: {up['source_eobs']})"
        )

    provider_candidates = unique_providers

    # Output debug info to console (will be visible in backend logs)
    print("=== EOB Provider Debug Info ===")
    for debug_entry in eob_provider_debug:
        print(f"EOB {debug_entry['eob_id']}:")
        print(f"  Provider refs: {debug_entry['provider_refs']}")
        print(
            f"  Linked providers: {[p['name'] for p in debug_entry['linked_providers']]}"
        )
        print("---")

    # Now process ExplanationOfBenefit entries for charge data
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
                        eob_claim_num = (
                            _normalize_str(ident.get("value")) or eob_claim_num
                        )
                        break
                if eob_claim_num:
                    break

            doctor_name = None

            # Get derived provider info for this EOB from our preprocessing
            eob_id = res.get("id")
            derived_info = eob_to_provider_map.get(eob_id, {})
            doctor_name = derived_info.get("doctor_name")

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
                            charge_description = _normalize_str(
                                first.get("display")
                            ) or _normalize_str(first.get("text"))
                if not charge_description:
                    charge_description = _normalize_str(
                        it.get("description")
                    ) or _normalize_str(
                        product.get("text") if isinstance(product, dict) else None
                    )
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
                owed_amount = (
                    sum(owed_amount_components) if owed_amount_components else None
                )

                # Use the derived provider info from our preprocessing
                facility_location_id = None
                facility_location_name = None

                # Get derived provider info for this EOB
                if derived_info:
                    facility_location_name = derived_info.get("provider_name")
                    # Find the matching unique provider for location_id
                    for provider in unique_providers:
                        if eob_id in provider.get("source_eobs", []):
                            facility_location_id = provider.get("location_id")
                            break

                # Final fallback: use doctor name if no facility name
                if not facility_location_name and doctor_name:
                    facility_location_name = doctor_name

                rows.append(
                    {
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
                        "_facility_ref": None,  # No longer using individual facility refs
                        "facility_location_id": facility_location_id,
                        "facility_location_name": facility_location_name,
                    }
                )
        elif rtype == "Claim":
            # Handle Claim resources (simplified version)
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
                                description = _normalize_str(
                                    first.get("display")
                                ) or _normalize_str(first.get("text"))
                charged_amount = None
                if isinstance(it.get("net"), dict):
                    charged_amount = it.get("net", {}).get("value")

                rows.append(
                    {
                        "service_date": svc_date,
                        "description": description,
                        "cpt_code": cpt_code,
                        "charged_amount": charged_amount,
                        "owed_amount": None,
                        "facility_location_id": None,
                        "facility_location_name": None,
                    }
                )

    cleaned: list[dict] = []
    for r in rows:
        has_value = any(
            v not in (None, "")
            for k, v in r.items()
            if k not in {"doctor_name", "eob_claim_num"}
        )
        if not has_value:
            continue
        r["description"] = _normalize_str(r.get("description")) or _normalize_str(
            r.get("charge_description")
        )
        cleaned.append(r)

    if provider_candidates:
        cleaned.insert(0, {"_provider_candidates": provider_candidates})

    return cleaned


def _heuristic_extract_rows(
    obj, max_items_per_array: int = 500
):  # original logic with minimal changes
    from collections import deque

    candidates_meta = []
    extracted: list[dict] = []
    visited_ids = set()
    date_keys = {
        "service_date",
        "serviceDate",
        "dateOfService",
        "dos",
        "date",
        "startDate",
    }
    desc_keys = {
        "description",
        "procedureDescription",
        "serviceDescription",
        "lineDescription",
        "chargeDescription",
    }
    cpt_keys = {"cpt", "cptCode", "cpt_code", "procedureCode", "code"}
    charge_keys = {
        "charged_amount",
        "billed_amount",
        "billedAmount",
        "totalCharge",
        "chargeAmount",
        "allowedAmount",
        "amount",
        "net",
        "netAmount",
    }
    owed_keys = {
        "owed_amount",
        "patientResponsibility",
        "patientAmount",
        "patientOwed",
        "patientPay",
        "patientBalance",
    }

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
                take = dict_items[:max_items_per_array]
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
                candidates_meta.append(
                    {
                        "parent_key": parent_key,
                        "depth": depth,
                        "array_length": len(cur),
                        "sample_scores": scores,
                        "avg_score": avg_score,
                    }
                )
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
