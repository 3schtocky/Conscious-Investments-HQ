"""Tenants: one private office per customer, each in its own folder under data/tenants/<id>/.

A request, task or thread runs "as" a tenant through a context variable (`use`). The few paths
that used to be global (the research folder, roster.yaml, avatars) ask `current()`. Once a
Registry exists the office is multi-tenant, and asking for one of those paths with no tenant set
raises instead of falling back to the shared folders, so a missed spot fails loudly rather than
leaking another customer's files. Without a Registry (the Captain's own office, demos) nothing
changes. Shared read-only data (SEC/Yahoo cache, prompts, departments) stays shared: it is public.
"""

from __future__ import annotations

import contextlib
import re
import shutil
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from hq.config import CONFIG_DIR, DATA_DIR

TENANTS_DIR = DATA_DIR / "tenants"
TENANT_ID = re.compile(r"^[a-z0-9]{8,32}$")   # also what keeps a tenant id from being a path

_current: ContextVar[Tenant | None] = ContextVar("tenant", default=None)
_multi = False


class TenantError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tenant:
    id: str
    root: Path

    @property
    def db(self) -> Path: return self.root / "office.db"
    @property
    def quant(self) -> Path: return self.root / "Quant"
    @property
    def memory(self) -> Path: return self.root / "memory"
    @property
    def outbox(self) -> Path: return self.root / "outbox"
    @property
    def coverage(self) -> Path: return self.root / "coverage"
    @property
    def avatars(self) -> Path: return self.root / "avatars"
    @property
    def roster(self) -> Path: return self.root / "roster.yaml"


def current() -> Tenant | None:
    t = _current.get()
    if t is None and _multi:
        raise TenantError("No tenant is set for this work; refusing to use shared folders.")
    return t


@contextlib.contextmanager
def use(tenant: Tenant | None):
    token = _current.set(tenant)
    try:
        yield tenant
    finally:
        _current.reset(token)


class Registry:
    """Creates tenants on disk and keeps one running Office per tenant."""

    def __init__(self, base: Path | None = None, *, office_factory=None, on_load=None):
        global _multi
        self.base = Path(base or TENANTS_DIR)
        self.base.mkdir(parents=True, exist_ok=True)
        self._offices: dict[str, object] = {}
        self._factory = office_factory   # (Tenant) -> Office; tests pass scripted ones
        self.on_load = on_load          # (Tenant, Office) -> None: start reminders, rounds
        _multi = True

    def tenant(self, tenant_id: str) -> Tenant:
        if not TENANT_ID.fullmatch(tenant_id or ""):
            raise TenantError("Not a valid tenant id.")
        return Tenant(tenant_id, self.base / tenant_id)

    def exists(self, tenant_id: str) -> bool:
        return TENANT_ID.fullmatch(tenant_id or "") is not None and (self.base / tenant_id).is_dir()

    def create(self, tenant_id: str) -> Tenant:
        """Build the folder aside and rename it into place, so a failure never leaves half a tenant."""
        t = self.tenant(tenant_id)
        if t.root.exists():
            raise TenantError("That tenant already exists.")
        tmp = self.base / f".new-{tenant_id}"
        shutil.rmtree(tmp, ignore_errors=True)
        draft = Tenant(tenant_id, tmp)
        try:
            for d in (draft.quant, draft.memory, draft.outbox, draft.coverage, draft.avatars):
                d.mkdir(parents=True)
            shutil.copyfile(CONFIG_DIR / "roster.yaml", draft.roster)   # their own cast to rename and restyle
            from hq import roster_edit
            with use(draft):   # the template is the Captain's cast: the customer is not "Stott"
                roster_edit.update_member("captain", {"nickname": "Captain"})
            tmp.rename(t.root)   # fails if another sign-up got there first
        except OSError as e:
            raise TenantError("That tenant already exists.") from e
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return t

    def office(self, tenant_id: str):
        """The tenant's running Office, built on first use. Must exist already."""
        if tenant_id not in self._offices:
            if not self.exists(tenant_id):
                raise TenantError("Unknown tenant.")
            t = self.tenant(tenant_id)
            with use(t):   # the office, and the tasks it starts, inherit the tenant
                if self._factory:
                    office = self._factory(t)
                else:
                    from hq.engine.runtime import Office
                    office = Office(db_path=t.db, quant_dir=t.quant, memory_dir=t.memory,
                                    outbox_dir=t.outbox)
                db = office.store._db.execute("PRAGMA database_list").fetchone()[2]
                for d in (db, office.quant_dir, office.memory_dir, office.outbox_dir):
                    if not Path(d).resolve().is_relative_to(t.root.resolve()):
                        raise TenantError(f"Office folder {d} is outside the tenant's own folder.")
                office.tenant = t
                if self.on_load:
                    self.on_load(t, office)
            self._offices[tenant_id] = office
        return self._offices[tenant_id]

    def loaded(self) -> list:
        return list(self._offices.values())


def reset_for_tests() -> None:
    global _multi
    _multi = False
