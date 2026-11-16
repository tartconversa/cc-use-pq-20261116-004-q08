from decimal import Decimal

from .catalog import Catalog
from .errors import Conflict, Invariant, Stale, require
from .quality import Quality
from .store import Store
from .values import as_number, day, decimal, identifier, text


class Orders:
    def __init__(self, store: Store):
        self.store = store
        self.quality = Quality()

    def create(self, payload: dict) -> dict:
        order_id = identifier(payload.get("order_id"), "order_id")
        plant_id = identifier(payload.get("plant_id"), "plant_id")
        grade = identifier(payload.get("grade"), "grade")
        due_day = day(payload.get("due_day"), "due_day")
        quantity = decimal(payload.get("quantity"), "quantity", positive=True)
        destination = text(payload.get("destination"), "destination")
        with self.store.write() as db:
            plant = Catalog.plant(db, plant_id)
            require(due_day >= plant["active_from"] and
                    (plant["active_to"] is None or due_day <= plant["active_to"]),
                    "order due outside plant lifecycle")
            Catalog.specification(db, grade, due_day)
            if db.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone():
                raise Conflict("order_id already exists")
            revision = Store.advance(db, "order", order_id)
            db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
                       (order_id, plant_id, grade, due_day, as_number(quantity), destination,
                        "open", revision, None))
            return {"order_id": order_id, "revision": revision}

    def cancel(self, order_id: str, expected_revision: int) -> dict:
        order_id = identifier(order_id, "order_id")
        require(type(expected_revision) is int, "expected_revision must be integer")
        with self.store.write() as db:
            if Store.revision(db) != expected_revision:
                raise Stale("source revision changed")
            order = Store.one(db, "SELECT * FROM orders WHERE order_id=?", (order_id,), "order")
            if order["status"] != "open":
                raise Conflict("order already cancelled")
            revision = Store.advance(db, "cancel_order", order_id)
            db.execute("UPDATE orders SET status='cancelled',cancelled_revision=? WHERE order_id=?",
                       (revision, order_id))
            db.execute("UPDATE reservations SET status='released',released_revision=? "
                       "WHERE order_id=? AND status='active'", (revision, order_id))
            return {"order_id": order_id, "revision": revision, "status": "cancelled"}

    def reserve(self, payload: dict) -> dict:
        reservation_id = identifier(payload.get("reservation_id"), "reservation_id")
        order_id = identifier(payload.get("order_id"), "order_id")
        lot_id = identifier(payload.get("lot_id"), "lot_id")
        quantity = decimal(payload.get("quantity"), "quantity", positive=True)
        expected_revision = payload.get("expected_revision")
        require(type(expected_revision) is int and expected_revision >= 0, "invalid expected_revision")
        with self.store.write() as db:
            if Store.revision(db) != expected_revision:
                raise Stale("source revision changed before reservation")
            order = Store.one(db, "SELECT * FROM orders WHERE order_id=?", (order_id,), "order")
            lot = Store.one(db, "SELECT lots.*,lines.plant_id FROM lots JOIN lines "
                            "ON lines.line_id=lots.line_id WHERE lot_id=?", (lot_id,), "lot")
            if order["status"] != "open":
                raise Invariant("order is not open")
            if order["plant_id"] != lot["plant_id"] or order["grade"] != lot["grade"]:
                raise Invariant("lot and order scope differ")
            if lot["local_day"] > order["due_day"]:
                raise Invariant("lot produced after order due day")
            quality = self.quality.classify(db, lot)
            if quality["status"] != "passed":
                raise Invariant("lot must pass quality before reservation")
            if db.execute("SELECT 1 FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone():
                raise Conflict("reservation_id already exists")
            order_used = self._sum(db, "SELECT quantity FROM reservations WHERE order_id=? AND status='active'", order_id)
            lot_used = self._sum(db, "SELECT quantity FROM reservations WHERE lot_id=? AND status='active'", lot_id)
            shipped = self._sum(db, "SELECT quantity FROM shipments WHERE lot_id=?", lot_id)
            if order_used + quantity > decimal(order["quantity"], "order quantity"):
                raise Invariant("reservations exceed order quantity")
            if lot_used + shipped + quantity > decimal(lot["quantity"], "lot quantity"):
                raise Invariant("reservations and shipments exceed lot quantity")
            revision = Store.advance(db, "reservation", reservation_id)
            db.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?,?)",
                       (reservation_id, order_id, lot_id, as_number(quantity), "active", revision, None))
            return {"reservation_id": reservation_id, "revision": revision, "quantity": as_number(quantity)}

    def release(self, reservation_id: str, expected_revision: int) -> dict:
        reservation_id = identifier(reservation_id, "reservation_id")
        require(type(expected_revision) is int, "expected_revision must be integer")
        with self.store.write() as db:
            if Store.revision(db) != expected_revision:
                raise Stale("source revision changed before release")
            row = Store.one(db, "SELECT * FROM reservations WHERE reservation_id=?",
                            (reservation_id,), "reservation")
            if row["status"] != "active":
                raise Conflict("reservation already released")
            revision = Store.advance(db, "release", reservation_id)
            db.execute("UPDATE reservations SET status='released',released_revision=? WHERE reservation_id=?",
                       (revision, reservation_id))
            return {"reservation_id": reservation_id, "revision": revision, "status": "released"}

    @staticmethod
    def _sum(db, sql: str, ref: str) -> Decimal:
        return sum((decimal(row["quantity"], "quantity") for row in db.execute(sql, (ref,))), Decimal(0))

    def detail(self, order_id: str) -> dict:
        with self.store.read() as db:
            order = Store.one(db, "SELECT * FROM orders WHERE order_id=?", (identifier(order_id, "order_id"),), "order")
            reservations = [dict(row) for row in db.execute(
                "SELECT * FROM reservations WHERE order_id=? ORDER BY created_revision", (order_id,))]
            active = sum((decimal(row["quantity"], "quantity") for row in reservations
                          if row["status"] == "active"), Decimal(0))
            return {**dict(order), "reserved_tons": as_number(active), "reservations": reservations}

    def allocation(self, plant_id: str, as_of: str) -> dict:
        plant_id = identifier(plant_id, "plant_id")
        as_of = day(as_of, "as_of")
        with self.store.read() as db:
            Catalog.plant(db, plant_id)
            orders = db.execute("SELECT * FROM orders WHERE plant_id=? AND due_day<=? ORDER BY due_day,order_id",
                                (plant_id, as_of)).fetchall()
            result = []
            for order in orders:
                reserved = self._sum(db, "SELECT quantity FROM reservations WHERE order_id=? AND status='active'",
                                     order["order_id"])
                quantity = decimal(order["quantity"], "quantity")
                result.append({"order_id": order["order_id"], "due_day": order["due_day"],
                               "grade": order["grade"], "status": order["status"],
                               "requested_tons": as_number(quantity), "reserved_tons": as_number(reserved),
                               "unfilled_tons": as_number(quantity - reserved) if order["status"] == "open" else "0"})
            return {"plant_id": plant_id, "as_of": as_of, "source_revision": Store.revision(db),
                    "orders": result}
