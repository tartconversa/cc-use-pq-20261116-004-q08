from .catalog import Catalog
from .errors import require
from .quality import Quality
from .store import Store
from .values import identifier


class Trace:
    def __init__(self, store: Store):
        self.store = store
        self.quality = Quality()

    def lot(self, lot_id: str) -> dict:
        lot_id = identifier(lot_id, "lot_id")
        with self.store.read() as db:
            lot = Store.one(db, "SELECT * FROM lots WHERE lot_id=?", (lot_id,), "lot")
            line = Catalog.line(db, lot["line_id"])
            assays = [dict(row) for row in db.execute(
                "SELECT * FROM assays WHERE lot_id=? ORDER BY sampled_at,accepted_revision,assay_id",
                (lot_id,))]
            shipments = [dict(row) for row in db.execute(
                "SELECT * FROM shipments WHERE lot_id=? ORDER BY shipped_at,shipment_id", (lot_id,))]
            reservations = [dict(row) for row in db.execute(
                "SELECT * FROM reservations WHERE lot_id=? ORDER BY created_revision,reservation_id", (lot_id,))]
            revisions = sorted({lot["accepted_revision"]} |
                               {row["accepted_revision"] for row in assays + shipments} |
                               {row["created_revision"] for row in reservations} |
                               {row["released_revision"] for row in reservations if row["released_revision"]})
            audit = [dict(row) for row in db.execute(
                "SELECT * FROM audit WHERE revision IN (%s) ORDER BY revision" %
                ",".join("?" for _ in revisions), tuple(revisions))] if revisions else []
            return {"lot": dict(lot), "line": dict(line),
                    "quality": self.quality.classify(db, lot), "assays": assays,
                    "shipments": shipments, "reservations": reservations,
                    "audit": audit, "source_revision": Store.revision(db)}

    def revision(self, revision: int) -> dict:
        require(type(revision) is int and revision > 0, "invalid revision")
        with self.store.read() as db:
            audit = Store.one(db, "SELECT * FROM audit WHERE revision=?", (revision,), "revision")
            kind = audit["cause"]
            ref = audit["ref"]
            table = {"plant": ("plants", "plant_id"), "line": ("lines", "line_id"),
                     "specification": ("specifications", "spec_id"), "lot": ("lots", "lot_id"),
                     "assay": ("assays", "assay_id"), "shipment": ("shipments", "shipment_id"),
                     "order": ("orders", "order_id"), "reservation": ("reservations", "reservation_id"),
                     "source_policy": ("source_policies", "source")}.get(kind)
            entity = None
            if table:
                entity_row = db.execute(f"SELECT * FROM {table[0]} WHERE {table[1]}=?", (ref,)).fetchone()
                entity = dict(entity_row) if entity_row else None
            return {"audit": dict(audit), "entity": entity,
                    "current_revision": Store.revision(db)}

    def source_event(self, source: str, external_id: str) -> dict:
        source = identifier(source, "source")
        external_id = identifier(external_id, "external_id")
        with self.store.read() as db:
            receipt = Store.one(db, "SELECT * FROM receipts WHERE source=? AND external_id=?",
                                (source, external_id), "source event")
            kind = receipt["event_kind"]
            table = {"lot": ("lots", "lot_id"), "assay": ("assays", "assay_id"),
                     "shipment": ("shipments", "shipment_id")}.get(kind)
            entity = None
            if table:
                entity_row = db.execute(f"SELECT * FROM {table[0]} WHERE source=? AND external_id=?",
                                        (source, external_id)).fetchone()
                entity = dict(entity_row) if entity_row else None
            audit = db.execute("SELECT * FROM audit WHERE revision=?",
                               (receipt["accepted_revision"],)).fetchone()
            return {"receipt": dict(receipt), "entity": entity,
                    "audit": dict(audit) if audit else None}

    def timeline(self, from_revision: int, to_revision: int) -> list[dict]:
        require(type(from_revision) is int and from_revision > 0, "invalid from_revision")
        require(type(to_revision) is int and to_revision >= from_revision, "invalid to_revision")
        require(to_revision - from_revision <= 1000, "revision interval exceeds 1000")
        with self.store.read() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM audit WHERE revision BETWEEN ? AND ? ORDER BY revision",
                (from_revision, to_revision))]
