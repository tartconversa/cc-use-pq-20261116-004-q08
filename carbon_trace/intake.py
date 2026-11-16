import json

from .catalog import Catalog
from .governance import Governance
from .errors import Conflict, DomainError, Invariant, InvalidInput, Missing, require
from .store import Store
from .values import as_number, decimal, digest, identifier, instant, local_day, mapping, sequence, text


class Intake:
    def __init__(self, store: Store):
        self.store = store

    def submit(self, source: str, batch_id: str, events: list[dict]) -> dict:
        source = identifier(source, "source")
        batch_id = identifier(batch_id, "batch_id")
        events = sequence(events)
        request_hash = digest(events)
        with self.store.write() as db:
            previous = db.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if previous:
                if previous["source"] != source or previous["request_digest"] != request_hash:
                    raise Conflict("batch_id reused with different request")
                result = json.loads(previous["result_json"])
                return {**result, "replayed": True}
            policy = Governance.check_batch(db, source, len(events))
            accepted, duplicate, rejected = [], [], []
            for index, raw in enumerate(events):
                savepoint = f"event_{index}"
                db.execute(f"SAVEPOINT {savepoint}")
                try:
                    event = mapping(raw, "event")
                    external_id = identifier(event.get("external_id"), "external_id")
                    kind = identifier(event.get("kind"), "kind")
                    Governance.check_kind(policy, kind)
                    previous = db.execute("SELECT * FROM receipts WHERE source=? AND external_id=?",
                                          (source, external_id)).fetchone()
                    event_hash = digest(event)
                    if previous:
                        if previous["payload_digest"] != event_hash:
                            raise Conflict("source event identity has conflicting payload")
                        duplicate.append({"row": index, "external_id": external_id,
                                          "revision": previous["accepted_revision"]})
                    else:
                        revision = self._apply(db, source, event, kind)
                        db.execute("INSERT INTO receipts VALUES (?,?,?,?,?)",
                                   (source, external_id, event_hash, revision, kind))
                        accepted.append({"row": index, "external_id": external_id, "revision": revision})
                    db.execute(f"RELEASE SAVEPOINT {savepoint}")
                except DomainError as exc:
                    db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    db.execute(f"RELEASE SAVEPOINT {savepoint}")
                    external_id = raw.get("external_id") if isinstance(raw, dict) else None
                    db.execute("INSERT INTO rejections VALUES (?,?,?,?,?,?)",
                               (batch_id, index, source, str(external_id) if external_id is not None else None,
                                exc.code, str(exc)))
                    rejected.append({"row": index, **exc.as_dict()})
            result = {"batch_id": batch_id, "accepted": accepted, "duplicate": duplicate,
                      "rejected": rejected, "revision": Store.revision(db), "replayed": False}
            db.execute("INSERT INTO batches VALUES (?,?,?,?,?)",
                       (batch_id, source, request_hash, json.dumps(result, ensure_ascii=False), result["revision"]))
            return result

    def _apply(self, db, source: str, event: dict, kind: str) -> int:
        if kind == "lot":
            return self._lot(db, source, event)
        if kind == "assay":
            return self._assay(db, source, event)
        if kind == "shipment":
            return self._shipment(db, source, event)
        raise InvalidInput("unsupported event kind", detail={"kind": kind})

    def _lot(self, db, source: str, event: dict) -> int:
        external_id = identifier(event["external_id"], "external_id")
        lot_id = identifier(event.get("lot_id"), "lot_id")
        line_id = identifier(event.get("line_id"), "line_id")
        grade = identifier(event.get("grade"), "grade")
        produced_at = instant(event.get("produced_at"), "produced_at")
        quantity = decimal(event.get("quantity"), "quantity", positive=True)
        line = Catalog.line(db, line_id)
        event_day = local_day(produced_at, line["timezone"])
        Catalog.active_line(db, line_id, event_day)
        Catalog.specification(db, grade, event_day)
        if db.execute("SELECT 1 FROM lots WHERE lot_id=?", (lot_id,)).fetchone():
            raise Conflict("lot_id already exists")
        revision = Store.advance(db, "lot", lot_id)
        db.execute("INSERT INTO lots VALUES (?,?,?,?,?,?,?,?,?)",
                   (lot_id, line_id, grade, produced_at, event_day, as_number(quantity), source,
                    external_id, revision))
        return revision

    def _assay(self, db, source: str, event: dict) -> int:
        assay_id = identifier(event.get("assay_id"), "assay_id")
        lot_id = identifier(event.get("lot_id"), "lot_id")
        sampled_at = instant(event.get("sampled_at"), "sampled_at")
        sulfur = decimal(event.get("sulfur"), "sulfur", nonnegative=True)
        ash = decimal(event.get("ash"), "ash", nonnegative=True)
        require(sulfur <= 100 and ash <= 100, "assay percentage exceeds 100")
        lot = Store.one(db, "SELECT * FROM lots WHERE lot_id=?", (lot_id,), "lot")
        require(sampled_at >= lot["produced_at"], "sample before production")
        if db.execute("SELECT 1 FROM assays WHERE assay_id=?", (assay_id,)).fetchone():
            raise Conflict("assay_id already exists")
        revision = Store.advance(db, "assay", assay_id)
        db.execute("INSERT INTO assays VALUES (?,?,?,?,?,?,?,?)",
                   (assay_id, lot_id, sampled_at, as_number(sulfur), as_number(ash), source,
                    event["external_id"], revision))
        return revision

    def _shipment(self, db, source: str, event: dict) -> int:
        shipment_id = identifier(event.get("shipment_id"), "shipment_id")
        lot_id = identifier(event.get("lot_id"), "lot_id")
        shipped_at = instant(event.get("shipped_at"), "shipped_at")
        quantity = decimal(event.get("quantity"), "quantity", positive=True)
        destination = text(event.get("destination"), "destination")
        lot = Store.one(db, "SELECT lots.*,plants.timezone FROM lots JOIN lines ON lines.line_id=lots.line_id "
                        "JOIN plants ON plants.plant_id=lines.plant_id WHERE lots.lot_id=?", (lot_id,), "lot")
        require(shipped_at >= lot["produced_at"], "shipment before production")
        if db.execute("SELECT 1 FROM shipments WHERE shipment_id=?", (shipment_id,)).fetchone():
            raise Conflict("shipment_id already exists")
        total = sum((decimal(row["quantity"], "stored quantity") for row in
                     db.execute("SELECT quantity FROM shipments WHERE lot_id=?", (lot_id,))),
                    decimal(0, "zero"))
        if total + quantity > decimal(lot["quantity"], "lot quantity"):
            raise Invariant("shipments exceed lot quantity")
        reserved = sum((decimal(row["quantity"], "reserved quantity") for row in
                        db.execute("SELECT quantity FROM reservations WHERE lot_id=? AND status='active'",
                                   (lot_id,))), decimal(0, "zero"))
        if total + reserved + quantity > decimal(lot["quantity"], "lot quantity"):
            raise Invariant("shipment would consume reserved material")
        event_day = local_day(shipped_at, lot["timezone"])
        revision = Store.advance(db, "shipment", shipment_id)
        db.execute("INSERT INTO shipments VALUES (?,?,?,?,?,?,?,?,?)",
                   (shipment_id, lot_id, shipped_at, event_day, as_number(quantity), destination,
                    source, event["external_id"], revision))
        return revision

    def receipt(self, source: str, external_id: str) -> dict:
        with self.store.read() as db:
            return dict(Store.one(db, "SELECT * FROM receipts WHERE source=? AND external_id=?",
                                  (identifier(source, "source"), identifier(external_id, "external_id")), "receipt"))

    def batch(self, batch_id: str) -> dict:
        with self.store.read() as db:
            row = Store.one(db, "SELECT * FROM batches WHERE batch_id=?", (identifier(batch_id, "batch_id"),), "batch")
            return json.loads(row["result_json"])

    def rejections(self, batch_id: str) -> list[dict]:
        with self.store.read() as db:
            return [dict(row) for row in db.execute("SELECT * FROM rejections WHERE batch_id=? ORDER BY row_index",
                                                    (identifier(batch_id, "batch_id"),))]
