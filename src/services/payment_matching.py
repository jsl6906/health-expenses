"""AI-assisted review of payment <-> charge links for a single provider.

Flow:
 1. ``load_provider_snapshot`` gathers the provider's charges, payments and
    current ChargePaymentXref links as plain dicts.
 2. ``find_amount_candidates`` precomputes exact dollar-amount matches
    (single charges or small same-period groups) so the LLM does not have to
    do subset-sum arithmetic itself.
 3. ``run_link_review`` asks Gemini for proposed re-mappings and
    ``build_recommendations`` validates them and diffs against current links.
 4. ``apply_link_changes`` commits user-confirmed changes.
"""
from __future__ import annotations

import json
from datetime import date
from itertools import combinations
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..llm_config import get_gemini_api_key, get_gemini_model_name
from ..models.database import Charge, ChargePaymentXref, Payment

try:  # pragma: no cover - optional at import time
    import google.generativeai as genai  # type: ignore
except Exception:  # pragma: no cover
    genai = None  # type: ignore

REVIEW_USER = "ai-review"
CENT_TOLERANCE = 1  # cents


class LinkReviewUnavailable(RuntimeError):
    """Gemini is not configured."""


class LinkReviewError(RuntimeError):
    """The model call failed or returned unusable output."""


def _cents(value: Any) -> int:
    return int(round(float(value or 0) * 100))


def _money(cents: int) -> float:
    return round(cents / 100.0, 2)


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def load_provider_snapshot(db: Session, provider_id: int) -> Dict[str, Any]:
    charges = (
        db.query(Charge)
        .options(joinedload(Charge.family_member))
        .filter(Charge.provider_id == provider_id)
        .order_by(Charge.service_date, Charge.id)
        .all()
    )
    charge_ids = [c.id for c in charges]

    payment_filter = Payment.provider_id == provider_id
    if charge_ids:
        linked = db.query(ChargePaymentXref.payment_id).filter(
            ChargePaymentXref.charge_id.in_(charge_ids)
        )
        payment_filter = or_(payment_filter, Payment.id.in_(linked))
    payments = (
        db.query(Payment)
        .filter(payment_filter)
        .order_by(Payment.payment_date, Payment.id)
        .all()
    )
    payment_ids = [p.id for p in payments]

    xrefs = (
        db.query(ChargePaymentXref)
        .filter(ChargePaymentXref.payment_id.in_(payment_ids))
        .all()
        if payment_ids
        else []
    )
    local_charge_ids = set(charge_ids)

    return {
        "provider_id": provider_id,
        "charges": [
            {
                "id": c.id,
                "service_date": _iso(c.service_date),
                "member": c.family_member.first_name if c.family_member else None,
                "description": (c.charge_description or "")[:120],
                "cpt_code": c.cpt_code,
                "charged": _money(_cents(c.charged_amount)),
                "owed": _money(_cents(c.owed_amount)),
                "insurance_paid": _money(_cents(c.insurance_paid_amount)),
                "eob_claim_num": c.eob_claim_num,
                "claim_submitted_date": _iso(c.claim_submitted_date),
                "self_pay": bool(c.not_covered_by_insurance),
            }
            for c in charges
        ],
        "payments": [
            {
                "id": p.id,
                "payment_date": _iso(p.payment_date),
                "amount": _money(_cents(p.payment_amount)),
                "reimbursement": _money(_cents(p.reimbursement_amount)),
                "expense_type": p.expense_type,
                "notes": (p.notes or "")[:200] or None,
            }
            for p in payments
        ],
        "links": [
            {
                "payment_id": x.payment_id,
                "charge_id": x.charge_id,
                "amount": _money(_cents(x.allocated_amount)),
                "other_provider": x.charge_id not in local_charge_ids,
            }
            for x in xrefs
        ],
    }


def find_amount_candidates(
    snapshot: Dict[str, Any],
    max_group: int = 3,
    window_days: int = 365,
    slack_days: int = 7,
    max_pool: int = 40,
    per_payment: int = 5,
) -> Dict[int, List[Dict[str, Any]]]:
    """Return exact owed-amount matches per payment, closest service dates first.

    A payment may match a single charge or a group of up to ``max_group``
    charges whose owed amounts sum exactly to the payment amount. Only charges
    with service dates from ``window_days`` before to ``slack_days`` after the
    payment date are considered.
    """
    charges = [c for c in snapshot["charges"] if _cents(c["owed"]) > 0]
    out: Dict[int, List[Dict[str, Any]]] = {}
    for p in snapshot["payments"]:
        target = abs(_cents(p["amount"]))
        if target == 0:
            continue
        pdate = date.fromisoformat(p["payment_date"]) if p["payment_date"] else None

        pool = []
        for c in charges:
            if pdate and c["service_date"]:
                gap = (pdate - date.fromisoformat(c["service_date"])).days
                if gap < -slack_days or gap > window_days:
                    continue
            else:
                gap = window_days
            pool.append((abs(gap), gap, c))
        pool.sort(key=lambda t: t[0])
        pool = pool[:max_pool]

        matches = []
        for size in range(1, max_group + 1):
            for combo in combinations(pool, size):
                if sum(_cents(c["owed"]) for _, _, c in combo) == target:
                    matches.append(
                        {
                            "charge_ids": sorted(c["id"] for _, _, c in combo),
                            "days_after_service": max(g for _, g, _ in combo),
                            "_score": sum(a for a, _, _ in combo) / size + 15 * (size - 1),
                        }
                    )
        if matches:
            matches.sort(key=lambda m: m["_score"])
            for m in matches:
                m.pop("_score")
            out[p["id"]] = matches[:per_payment]
    return out


PROMPT_INSTRUCTIONS = """You are reconciling a household's medical payments against the charges billed by ONE healthcare provider.

Domain rules:
- Each charge's "owed" is the patient responsibility after insurance. Payments made by the household should be linked to the charge(s) they paid.
- A payment's allocations must sum EXACTLY to the payment amount. Negative payments are refunds from the provider and should be linked (with negative amounts) to the charge(s) that were overpaid.
- Payments usually occur on or after the service date, commonly 0-120 days later (after the insurance EOB). Payments made the same day as a visit (copays) are common.
- One payment often covers several line items from the same visit (same service_date and member) or a statement covering several visits.
- Charges with owed = 0 should normally not have payments linked.
- "candidate_exact_matches" lists precomputed charge groups whose owed amounts sum exactly to each payment amount. Prefer them, but weigh timing and avoid using the same charge for two payments unless the charge is only partially covered by each.
- Existing links may be correct; only recommend changes you are reasonably confident improve the mapping. Links marked other_provider=true are to another provider's charges; do not reference those charges.

Tasks:
1. Review the existing links and identify problems (wrong charge, over/under-allocation, charges paid twice, unlinked payments, unpaid charges that appear to have a matching payment).
2. Describe the matching methods/patterns that best fit this provider (e.g. "pays exact owed per visit ~30 days after service", "monthly statement payments covering multiple visits").
3. Propose corrected links ONLY for payments whose links should change. For each, give the FULL desired set of allocations for that payment (it replaces the payment's current links to this provider's charges). An empty allocations list means unlink the payment entirely.

Return ONLY JSON matching this shape:
{
  "summary": "2-4 sentence overview of the state of the links",
  "matching_methods": ["short description of each pattern observed"],
  "proposals": [
    {"payment_id": 123, "allocations": [{"charge_id": 456, "amount": 25.00}], "confidence": "high|medium|low", "rationale": "short reason referencing dates and amounts"}
  ],
  "issues": [
    {"description": "problem that needs human attention and cannot be fixed by relinking", "charge_ids": [], "payment_ids": []}
  ]
}
"""


def build_prompt(snapshot: Dict[str, Any], candidates: Dict[int, List[Dict[str, Any]]]) -> str:
    data = {
        "charges": snapshot["charges"],
        "payments": snapshot["payments"],
        "current_links": snapshot["links"],
        "candidate_exact_matches": {str(k): v for k, v in candidates.items()},
    }
    return PROMPT_INSTRUCTIONS + "\nDATA:\n" + json.dumps(data, separators=(",", ":"))


def call_gemini_json(prompt: str) -> Dict[str, Any]:
    key = get_gemini_api_key(False)
    if not key or genai is None:
        raise LinkReviewUnavailable("GEMINI_API_KEY is not configured")
    try:
        genai.configure(api_key=key)  # type: ignore[attr-defined]
        model = genai.GenerativeModel(  # type: ignore[attr-defined]
            get_gemini_model_name(),
            generation_config={"response_mime_type": "application/json", "temperature": 0.1},
        )
        resp = model.generate_content(prompt, request_options={"timeout": 300})
        raw = (getattr(resp, "text", "") or "").strip()
    except Exception as ex:
        raise LinkReviewError(f"Gemini request failed: {ex}") from ex
    try:
        parsed = json.loads(raw.strip("`").removeprefix("json").strip())
    except json.JSONDecodeError as ex:
        raise LinkReviewError("Gemini returned invalid JSON") from ex
    if not isinstance(parsed, dict):
        raise LinkReviewError("Gemini returned an unexpected response shape")
    return parsed


def _rebalance(alloc_ids: List[int], owed_by_id: Dict[int, int], target: int) -> Dict[int, int]:
    """Fill each charge up to its owed amount in order; remainder goes to the last charge."""
    result: Dict[int, int] = {}
    remaining = abs(target)
    sign = 1 if target >= 0 else -1
    for i, cid in enumerate(alloc_ids):
        take = remaining if i == len(alloc_ids) - 1 else min(remaining, max(owed_by_id.get(cid, 0), 0))
        result[cid] = sign * take
        remaining -= take
    return result


def build_recommendations(snapshot: Dict[str, Any], ai: Dict[str, Any]) -> Dict[str, Any]:
    charges = {c["id"]: c for c in snapshot["charges"]}
    payments = {p["id"]: p for p in snapshot["payments"]}

    current: Dict[int, Dict[int, int]] = {}
    foreign_cents: Dict[int, int] = {}
    for link in snapshot["links"]:
        if link["other_provider"]:
            foreign_cents[link["payment_id"]] = foreign_cents.get(link["payment_id"], 0) + _cents(link["amount"])
        else:
            current.setdefault(link["payment_id"], {})[link["charge_id"]] = _cents(link["amount"])

    def charge_view(cid: int, cents: int) -> Dict[str, Any]:
        c = charges[cid]
        return {
            "charge_id": cid,
            "amount": _money(cents),
            "service_date": c["service_date"],
            "member": c["member"],
            "description": c["description"],
            "owed": c["owed"],
        }

    recommendations = []
    seen: set[int] = set()
    for prop in ai.get("proposals") or []:
        if not isinstance(prop, dict):
            continue
        try:
            pid = int(prop.get("payment_id"))
        except (TypeError, ValueError):
            continue
        if pid not in payments or pid in seen:
            continue
        seen.add(pid)
        warnings: List[str] = []

        proposed: Dict[int, int] = {}
        for a in prop.get("allocations") or []:
            try:
                cid = int(a.get("charge_id"))
                cents = _cents(a.get("amount"))
            except (TypeError, ValueError, AttributeError):
                warnings.append("Dropped a malformed allocation")
                continue
            if cid not in charges:
                warnings.append(f"Dropped unknown charge #{cid}")
                continue
            proposed[cid] = proposed.get(cid, 0) + cents

        target = _cents(payments[pid]["amount"]) - foreign_cents.get(pid, 0)
        if proposed and abs(sum(proposed.values()) - target) > CENT_TOLERANCE:
            warnings.append("Allocation amounts did not sum to the payment; rebalanced by owed amount")
            proposed = _rebalance(list(proposed), {cid: _cents(charges[cid]["owed"]) for cid in proposed}, target)

        existing = current.get(pid, {})
        unchanged = set(existing) == set(proposed) and all(
            abs(existing[cid] - proposed[cid]) <= CENT_TOLERANCE for cid in proposed
        )
        if unchanged:
            continue

        confidence = str(prop.get("confidence") or "low").lower()
        recommendations.append(
            {
                "payment": payments[pid],
                "current": [charge_view(cid, amt) for cid, amt in existing.items()],
                "proposed": [charge_view(cid, amt) for cid, amt in proposed.items()],
                "confidence": confidence if confidence in {"high", "medium", "low"} else "low",
                "rationale": str(prop.get("rationale") or "")[:600],
                "warnings": warnings,
                "other_provider_links": foreign_cents.get(pid, 0) != 0,
            }
        )

    order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda r: (order[r["confidence"]], r["payment"]["payment_date"] or ""))

    issues = []
    for issue in ai.get("issues") or []:
        if isinstance(issue, dict) and issue.get("description"):
            issues.append(
                {
                    "description": str(issue["description"])[:600],
                    "charge_ids": [i for i in issue.get("charge_ids") or [] if i in charges],
                    "payment_ids": [i for i in issue.get("payment_ids") or [] if i in payments],
                }
            )

    return {
        "summary": str(ai.get("summary") or ""),
        "matching_methods": [str(m) for m in ai.get("matching_methods") or [] if m][:10],
        "recommendations": recommendations,
        "issues": issues,
        "stats": {
            "charges": len(charges),
            "payments": len(payments),
            "links": len(snapshot["links"]),
            "unlinked_payments": sum(1 for pid in payments if pid not in current and pid not in foreign_cents),
        },
    }


def run_link_review(db: Session, provider_id: int) -> Dict[str, Any]:
    snapshot = load_provider_snapshot(db, provider_id)
    if not snapshot["payments"] or not snapshot["charges"]:
        return {
            "summary": "This provider needs both charges and payments before links can be reviewed.",
            "matching_methods": [],
            "recommendations": [],
            "issues": [],
            "stats": {"charges": len(snapshot["charges"]), "payments": len(snapshot["payments"]), "links": 0, "unlinked_payments": 0},
        }
    candidates = find_amount_candidates(snapshot)
    ai = call_gemini_json(build_prompt(snapshot, candidates))
    result = build_recommendations(snapshot, ai)
    result["model"] = get_gemini_model_name()
    return result


def apply_link_changes(db: Session, provider_id: int, changes: List[Dict[str, Any]]) -> Dict[str, int]:
    """Replace each payment's links to this provider's charges with the given allocations.

    Links from the payment to other providers' charges are left untouched.
    Raises ValueError if a payment/charge doesn't belong to the provider or the
    allocations don't sum to the payment amount.
    """
    snapshot = load_provider_snapshot(db, provider_id)
    charge_ids = {c["id"] for c in snapshot["charges"]}
    payments = {p["id"]: p for p in snapshot["payments"]}
    foreign_cents: Dict[int, int] = {}
    for link in snapshot["links"]:
        if link["other_provider"]:
            foreign_cents[link["payment_id"]] = foreign_cents.get(link["payment_id"], 0) + _cents(link["amount"])

    seen: set[int] = set()
    for change in changes:
        pid = change["payment_id"]
        if pid not in payments:
            raise ValueError(f"Payment #{pid} does not belong to this provider")
        if pid in seen:
            raise ValueError(f"Payment #{pid} appears more than once")
        seen.add(pid)
        allocs = change["allocations"]
        bad = [a["charge_id"] for a in allocs if a["charge_id"] not in charge_ids]
        if bad:
            raise ValueError(f"Charge(s) {bad} do not belong to this provider")
        if len({a["charge_id"] for a in allocs}) != len(allocs):
            raise ValueError(f"Payment #{pid} lists a charge more than once")
        target = _cents(payments[pid]["amount"]) - foreign_cents.get(pid, 0)
        if allocs and abs(sum(_cents(a["amount"]) for a in allocs) - target) > CENT_TOLERANCE:
            raise ValueError(f"Allocations for payment #{pid} do not sum to the payment amount")

    counts = {"payments_updated": 0, "links_added": 0, "links_updated": 0, "links_removed": 0}
    for change in changes:
        pid = change["payment_id"]
        new = {a["charge_id"]: _money(_cents(a["amount"])) for a in change["allocations"]}
        existing = {
            x.charge_id: x
            for x in db.query(ChargePaymentXref)
            .filter(ChargePaymentXref.payment_id == pid, ChargePaymentXref.charge_id.in_(charge_ids))
            .all()
        }
        for cid, xref in existing.items():
            if cid not in new:
                db.delete(xref)
                counts["links_removed"] += 1
            elif _cents(xref.allocated_amount) != _cents(new[cid]):
                xref.allocated_amount = new[cid]
                xref.updated_by = REVIEW_USER
                counts["links_updated"] += 1
        for cid, amt in new.items():
            if cid not in existing:
                db.add(
                    ChargePaymentXref(
                        payment_id=pid,
                        charge_id=cid,
                        allocated_amount=amt,
                        created_by=REVIEW_USER,
                        updated_by=REVIEW_USER,
                    )
                )
                counts["links_added"] += 1
        counts["payments_updated"] += 1
    db.commit()
    return counts
