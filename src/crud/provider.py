"""
CRUD operations for Provider model.
"""

from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

# Handle imports whether running as module or script
try:
    from crud.base import BaseCRUD
    from models.database import Charge, Payment, Provider, ProviderAlias
except ImportError:
    from ..models.database import Charge, Payment, Provider, ProviderAlias
    from .base import BaseCRUD


def clean_text(value: Optional[str]) -> Optional[str]:
    """Collapse all whitespace (including newlines) to single spaces; blank -> None."""
    if value is None:
        return None
    collapsed = " ".join(str(value).split())
    return collapsed or None


def norm_key(value: Optional[str]) -> str:
    return (clean_text(value) or "").lower()


class ProviderMatcher:
    """Resolve an imported provider name/address to an existing provider id.

    Exact provider name+address wins, then an alias scoped to that address,
    then an alias with no address.
    """

    def __init__(self, db: Session):
        self._by_key: dict[tuple[str, str], int] = {}
        self._by_name: dict[str, int] = {}
        self._aliases: dict[str, list[tuple[Optional[str], int]]] = {}
        for pid, name, addr in db.query(
            Provider.id, Provider.provider_name, Provider.provider_address
        ):
            self.add(pid, name, addr)
        for pid, name, addr in db.query(
            ProviderAlias.provider_id,
            ProviderAlias.alias_name,
            ProviderAlias.alias_address,
        ):
            self._aliases.setdefault(norm_key(name), []).append(
                (norm_key(addr) if addr else None, pid)
            )

    def add(self, provider_id: int, name: Optional[str], address: Optional[str] = None):
        key = (norm_key(name), norm_key(address))
        self._by_key.setdefault(key, provider_id)
        if key[0]:
            self._by_name.setdefault(key[0], provider_id)

    def _alias(self, name: str, address: Optional[str]) -> Optional[int]:
        candidates = self._aliases.get(name, [])
        for alias_addr, pid in candidates:
            if alias_addr is not None and alias_addr == address:
                return pid
        for alias_addr, pid in candidates:
            if alias_addr is None:
                return pid
        return None

    def match(self, name: Optional[str], address: Optional[str] = None) -> Optional[int]:
        n, a = norm_key(name), norm_key(address)
        if not n and not a:
            return None
        return self._by_key.get((n, a)) or (self._alias(n, a) if n else None)

    def match_name(self, name: Optional[str]) -> Optional[int]:
        """Match on name only (any address), consulting aliases."""
        n = norm_key(name)
        if not n:
            return None
        if n in self._by_name:
            return self._by_name[n]
        candidates = self._aliases.get(n)
        return candidates[0][1] if candidates else None


def find_alias(
    db: Session,
    name: Optional[str],
    address: Optional[str] = None,
    any_address: bool = False,
) -> Optional[ProviderAlias]:
    n = norm_key(name)
    if not n:
        return None
    addr = clean_text(address)
    query = db.query(ProviderAlias).filter(
        func.lower(func.trim(ProviderAlias.alias_name)) == n
    )
    if any_address:
        return query.first()
    if addr:
        query = query.filter(
            func.lower(func.trim(ProviderAlias.alias_address)) == addr.lower()
        )
    else:
        query = query.filter(ProviderAlias.alias_address.is_(None))
    return query.first()


def add_alias(
    db: Session,
    provider_id: int,
    name: Optional[str],
    address: Optional[str] = None,
    user: Optional[str] = None,
) -> Optional[ProviderAlias]:
    """Point alias (name, address) at provider_id, creating or re-pointing it as needed.

    Returns None when the alias would just duplicate the provider's own name/address.
    """
    alias_name = clean_text(name)
    alias_address = clean_text(address)
    if not alias_name:
        return None
    provider = db.get(Provider, provider_id)
    if provider is None:
        raise ValueError(f"Provider {provider_id} not found")
    if norm_key(provider.provider_name) == alias_name.lower() and (  # type: ignore[arg-type]
        alias_address is None
        or norm_key(provider.provider_address) == alias_address.lower()  # type: ignore[arg-type]
    ):
        return None
    existing = find_alias(db, alias_name, alias_address)
    if existing is not None:
        existing.provider_id = provider_id  # type: ignore[assignment]
        return existing
    alias = ProviderAlias(
        provider_id=provider_id,
        alias_name=alias_name,
        alias_address=alias_address,
        created_by=user,
    )
    db.add(alias)
    db.flush()
    return alias


def merge_providers(
    db: Session,
    source_id: int,
    target_id: int,
    user: Optional[str] = None,
    backfill_doctor_name: Optional[str] = None,
) -> dict:
    """Move charges, payments and aliases from source to target, record the source
    name as an alias of target, then delete source. Caller commits.
    """
    if source_id == target_id:
        raise ValueError("Cannot merge a provider into itself")
    source = db.get(Provider, source_id)
    target = db.get(Provider, target_id)
    if source is None:
        raise LookupError(f"Source provider {source_id} not found")
    if target is None:
        raise LookupError(f"Target provider {target_id} not found")

    doctors_backfilled = 0
    if backfill_doctor_name:
        doctors_backfilled = (
            db.query(Charge)
            .filter(
                Charge.provider_id == source_id,
                func.coalesce(func.trim(Charge.doctor_name), "") == "",
            )
            .update(
                {Charge.doctor_name: backfill_doctor_name}, synchronize_session=False
            )
        )
    charges_moved = (
        db.query(Charge)
        .filter(Charge.provider_id == source_id)
        .update({Charge.provider_id: target_id}, synchronize_session=False)
    )
    payments_moved = (
        db.query(Payment)
        .filter(Payment.provider_id == source_id)
        .update({Payment.provider_id: target_id}, synchronize_session=False)
    )
    db.query(ProviderAlias).filter(ProviderAlias.provider_id == source_id).update(
        {ProviderAlias.provider_id: target_id}, synchronize_session=False
    )
    source_name = source.provider_name
    db.delete(source)
    db.flush()
    add_alias(db, target_id, source_name, None, user=user)  # type: ignore[arg-type]
    return {
        "source_name": source_name,
        "target_name": target.provider_name,
        "charges_moved": charges_moved,
        "payments_moved": payments_moved,
        "doctors_backfilled": doctors_backfilled,
    }


class ProviderCRUD(BaseCRUD[Provider]):
    """CRUD operations for Provider model."""

    def __init__(self):
        super().__init__(Provider)

    def get_all_with_derived_fields(
        self, db: Session, skip: int = 0, limit: Optional[int] = None
    ) -> List[Provider]:
        """Get all providers with derived fields computed."""
        query = db.query(
            Provider,
            Provider.last_charge_date.label("last_charge_date"),
            Provider.last_payment_date.label("last_payment_date"),
        ).options(selectinload(Provider.charges), selectinload(Provider.payments))

        if skip:
            query = query.offset(skip)
        if limit:
            query = query.limit(limit)

        results = query.all()

        # Convert to Provider objects with derived fields set
        providers = []
        for result in results:
            provider = result[0]
            # The hybrid properties will be calculated automatically
            providers.append(provider)

        return providers

    def get_by_name(self, db: Session, provider_name: str) -> Optional[Provider]:
        """Get provider by name."""
        return (
            db.query(Provider).filter(Provider.provider_name == provider_name).first()
        )

    def get_active_providers(self, db: Session) -> List[Provider]:
        """Get all active providers with derived fields."""
        return (
            db.query(Provider)
            .filter(Provider.active_provider_flag == True)
            .options(selectinload(Provider.charges), selectinload(Provider.payments))
            .all()
        )

    def search_by_group(self, db: Session, group_name: str) -> List[Provider]:
        """Search providers by group."""
        return (
            db.query(Provider)
            .filter(Provider.provider_group.ilike(f"%{group_name}%"))
            .all()
        )


# Create instance
provider_crud = ProviderCRUD()
