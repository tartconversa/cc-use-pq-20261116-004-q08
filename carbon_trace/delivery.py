from decimal import Decimal

from .errors import Conflict, Invariant, Stale, require
from .store import Store
from .values import as_number, decimal, identifier, instant, text


class Delivery:
    CONDITIONS = {"accepted", "damaged", "rejected"}

    def __init__(self, store: Store):
        self.store = store

    def commit(self, payload: dict) -> dict:
        commitment_id = identifier(payload.get("commitment_id"), "commitment_id")
        shipment_id = identifier(payload.get("shipment_id"), "shipment_id")
        order_id = identifier(payload.get("order_id"), "order_id")
        expected_revision = payload.get("expected_revision")
        require(type(expected_revision) is int and expected_revision >= 0, "invalid expected_revision")
        with self.store.write() as db:
            if Store.revision(db) != expected_revision:
                raise Stale("source revision changed")
            shipment = Store.one(db, "SELECT shipments.*,lines.plant_id,lots.grade FROM shipments "
                                 "JOIN lots ON lots.lot_id=shipments.lot_id "
                                 "JOIN lines ON lines.line_id=lots.line_id WHERE shipment_id=?",
                                 (shipment_id,), "shipment")
            order = Store.one(db, "SELECT * FROM orders WHERE order_id=?", (order_id,), "order")
            if order["status"] != "open":
                raise Invariant("order is not open")
            if order["plant_id"] != shipment["plant_id"] or order["grade"] != shipment["grade"]:
                raise Invariant("shipment and order scope differ")
            if shipment["local_day"] > order["due_day"]:
                raise Invariant("shipment occurs after order due day")
            if db.execute("SELECT 1 FROM commitments WHERE shipment_id=? OR commitment_id=?",
                          (shipment_id, commitment_id)).fetchone():
                raise Conflict("shipment or commitment already matched")
            claimed = sum((decimal(row["quantity"], "quantity") for row in db.execute(
                "SELECT quantity FROM commitments WHERE order_id=?", (order_id,))), Decimal(0))
            quantity = decimal(shipment["quantity"], "quantity")
            if claimed + quantity > decimal(order["quantity"], "order quantity"):
                raise Invariant("shipments exceed order demand")
            revision = Store.advance(db, "delivery_commitment", commitment_id)
            db.execute("INSERT INTO commitments VALUES (?,?,?,?,?)",
                       (commitment_id, order_id, shipment_id, as_number(quantity), revision))
            return {"commitment_id": commitment_id, "quantity": as_number(quantity),
                    "revision": revision}

    def check(self, payload: dict) -> dict:
        check_id = identifier(payload.get("check_id"), "check_id")
        shipment_id = identifier(payload.get("shipment_id"), "shipment_id")
        checked_at = instant(payload.get("checked_at"), "checked_at")
        received = decimal(payload.get("received_quantity"), "received_quantity", nonnegative=True)
        condition = payload.get("condition")
        require(condition in self.CONDITIONS, "invalid delivery condition")
        note = text(payload.get("note"), "note", max_length=400)
        with self.store.write() as db:
            shipment = Store.one(db, "SELECT * FROM shipments WHERE shipment_id=?", (shipment_id,), "shipment")
            if checked_at < shipment["shipped_at"]:
                raise Invariant("delivery check precedes shipment")
            if received > decimal(shipment["quantity"], "shipment quantity"):
                raise Invariant("received quantity exceeds shipped quantity")
            if db.execute("SELECT 1 FROM delivery_checks WHERE check_id=?", (check_id,)).fetchone():
                raise Conflict("check_id already exists")
            revision = Store.advance(db, "delivery_check", check_id)
            db.execute("INSERT INTO delivery_checks VALUES (?,?,?,?,?,?,?)",
                       (check_id, shipment_id, checked_at, as_number(received), condition, note, revision))
            return {"check_id": check_id, "revision": revision}

    def shipment(self, shipment_id: str) -> dict:
        shipment_id = identifier(shipment_id, "shipment_id")
        with self.store.read() as db:
            shipment = Store.one(db, "SELECT * FROM shipments WHERE shipment_id=?", (shipment_id,), "shipment")
            checks = [dict(row) for row in db.execute(
                "SELECT * FROM delivery_checks WHERE shipment_id=? ORDER BY checked_at,accepted_revision",
                (shipment_id,))]
            commitment = db.execute("SELECT * FROM commitments WHERE shipment_id=?", (shipment_id,)).fetchone()
            return {"shipment": dict(shipment), "checks": checks,
                    "commitment": dict(commitment) if commitment else None,
                    "latest_condition": checks[-1]["condition"] if checks else "pending",
                    "source_revision": Store.revision(db)}

    def order(self, order_id: str) -> dict:
        order_id = identifier(order_id, "order_id")
        with self.store.read() as db:
            order = Store.one(db, "SELECT * FROM orders WHERE order_id=?", (order_id,), "order")
            commitments = [dict(row) for row in db.execute(
                "SELECT * FROM commitments WHERE order_id=? ORDER BY accepted_revision",
                (order_id,))]
            received = Decimal(0)
            for row in commitments:
                check = db.execute("SELECT * FROM delivery_checks WHERE shipment_id=? "
                                   "ORDER BY checked_at DESC,accepted_revision DESC LIMIT 1",
                                   (row["shipment_id"],)).fetchone()
                if check and check["condition"] == "accepted":
                    received += decimal(check["received_quantity"], "received quantity")
            return {"order_id": order_id, "ordered_tons": order["quantity"],
                    "committed_tons": as_number(sum((decimal(row["quantity"], "quantity")
                                                    for row in commitments), Decimal(0))),
                    "accepted_received_tons": as_number(received),
                    "unreceived_tons": as_number(decimal(order["quantity"], "quantity") - received),
                    "commitments": commitments, "source_revision": Store.revision(db)}
