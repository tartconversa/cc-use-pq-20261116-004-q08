import json

from .errors import Conflict, Invariant, require
from .store import Store
from .values import identifier


class Governance:
    ALLOWED = {"lot", "assay", "shipment"}

    def __init__(self, store: Store):
        self.store = store

    def configure(self, source: str, enabled: bool, kinds: list[str], max_batch_size: int,
                  expected_revision: int) -> dict:
        source = identifier(source, "source")
        require(type(enabled) is bool, "enabled must be boolean")
        require(isinstance(kinds, list) and bool(kinds) and len(kinds) <= len(self.ALLOWED),
                "kinds must be nonempty")
        require(all(isinstance(kind, str) and kind in self.ALLOWED for kind in kinds) and
                len(set(kinds)) == len(kinds), "invalid or repeated kind")
        require(type(max_batch_size) is int and 1 <= max_batch_size <= 500,
                "max_batch_size must be 1-500")
        require(type(expected_revision) is int and expected_revision >= 0, "invalid expected_revision")
        with self.store.write() as db:
            if Store.revision(db) != expected_revision:
                raise Conflict("source policy revision changed")
            revision = Store.advance(db, "source_policy", source)
            db.execute("INSERT INTO source_policies VALUES (?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET "
                       "enabled=excluded.enabled,allowed_kinds=excluded.allowed_kinds,"
                       "max_batch_size=excluded.max_batch_size,updated_revision=excluded.updated_revision",
                       (source, int(enabled), json.dumps(sorted(kinds)), max_batch_size, revision))
            return {"source": source, "enabled": enabled, "kinds": sorted(kinds),
                    "max_batch_size": max_batch_size, "revision": revision}

    @staticmethod
    def policy(db, source: str):
        row = db.execute("SELECT * FROM source_policies WHERE source=?", (source,)).fetchone()
        if row is None:
            return {"source": source, "enabled": True, "kinds": sorted(Governance.ALLOWED),
                    "max_batch_size": 500, "updated_revision": None, "implicit": True}
        return {"source": source, "enabled": bool(row["enabled"]),
                "kinds": json.loads(row["allowed_kinds"]),
                "max_batch_size": row["max_batch_size"],
                "updated_revision": row["updated_revision"], "implicit": False}

    def get(self, source: str) -> dict:
        with self.store.read() as db:
            return self.policy(db, identifier(source, "source"))

    @staticmethod
    def check_batch(db, source: str, size: int) -> dict:
        policy = Governance.policy(db, source)
        if not policy["enabled"]:
            raise Invariant("source is disabled")
        if size > policy["max_batch_size"]:
            raise Invariant("batch exceeds source limit")
        return policy

    @staticmethod
    def check_kind(policy: dict, kind: str):
        if kind not in policy["kinds"]:
            raise Invariant("source is not permitted to submit this event kind")
