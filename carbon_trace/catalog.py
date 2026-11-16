from .errors import Conflict, Invariant, Missing, require
from .store import Store
from .values import as_number, day, decimal, identifier, text, zone_name


class Catalog:
    def __init__(self, store: Store):
        self.store = store

    def add_plant(self, payload: dict) -> dict:
        plant_id = identifier(payload.get("plant_id"), "plant_id")
        name = text(payload.get("name"), "name")
        zone = zone_name(payload.get("timezone"))
        first = day(payload.get("active_from"), "active_from")
        last = payload.get("active_to")
        if last is not None:
            last = day(last, "active_to")
            require(last >= first, "active_to precedes active_from")
        with self.store.write() as db:
            if db.execute("SELECT 1 FROM plants WHERE plant_id=?", (plant_id,)).fetchone():
                raise Conflict("plant_id already exists")
            revision = self.store.advance(db, "plant", plant_id)
            db.execute("INSERT INTO plants VALUES (?,?,?,?,?,?)",
                       (plant_id, name, zone, first, last, revision))
            return {"plant_id": plant_id, "revision": revision}

    def add_line(self, payload: dict) -> dict:
        line_id = identifier(payload.get("line_id"), "line_id")
        plant_id = identifier(payload.get("plant_id"), "plant_id")
        capacity = as_number(decimal(payload.get("daily_capacity"), "daily_capacity", positive=True))
        first = day(payload.get("active_from"), "active_from")
        last = payload.get("active_to")
        if last is not None:
            last = day(last, "active_to")
            require(last >= first, "active_to precedes active_from")
        with self.store.write() as db:
            plant = self.plant(db, plant_id)
            require(first >= plant["active_from"] and
                    (plant["active_to"] is None or first <= plant["active_to"]),
                    "line start lies outside plant lifecycle")
            if last is not None:
                require(plant["active_to"] is None or last <= plant["active_to"],
                        "line end lies outside plant lifecycle")
            if db.execute("SELECT 1 FROM lines WHERE line_id=?", (line_id,)).fetchone():
                raise Conflict("line_id already exists")
            revision = self.store.advance(db, "line", line_id)
            db.execute("INSERT INTO lines VALUES (?,?,?,?,?,?)",
                       (line_id, plant_id, capacity, first, last, revision))
            return {"line_id": line_id, "revision": revision}

    def add_specification(self, payload: dict) -> dict:
        spec_id = identifier(payload.get("spec_id"), "spec_id")
        grade = identifier(payload.get("grade"), "grade")
        first = day(payload.get("valid_from"), "valid_from")
        last = payload.get("valid_to")
        if last is not None:
            last = day(last, "valid_to")
            require(last >= first, "valid_to precedes valid_from")
        low = decimal(payload.get("sulfur_min"), "sulfur_min", nonnegative=True)
        high = decimal(payload.get("sulfur_max"), "sulfur_max", nonnegative=True)
        ash = decimal(payload.get("ash_max"), "ash_max", nonnegative=True)
        require(low <= high, "sulfur interval is inverted")
        with self.store.write() as db:
            if db.execute("SELECT 1 FROM specifications WHERE spec_id=?", (spec_id,)).fetchone():
                raise Conflict("spec_id already exists")
            intervals = db.execute("SELECT valid_from,valid_to FROM specifications WHERE grade=?",
                                   (grade,)).fetchall()
            for old in intervals:
                if (old["valid_to"] is None or first <= old["valid_to"]) and (last is None or old["valid_from"] <= last):
                    raise Conflict("grade specification periods overlap")
            revision = self.store.advance(db, "specification", spec_id)
            db.execute("INSERT INTO specifications VALUES (?,?,?,?,?,?,?,?)",
                       (spec_id, grade, first, last, as_number(low), as_number(high), as_number(ash), revision))
            return {"spec_id": spec_id, "revision": revision}

    @staticmethod
    def plant(db, plant_id: str):
        return Store.one(db, "SELECT * FROM plants WHERE plant_id=?", (plant_id,), "plant")

    @staticmethod
    def line(db, line_id: str):
        return Store.one(db, "SELECT l.*,p.timezone,p.active_from AS plant_from,p.active_to AS plant_to "
                         "FROM lines l JOIN plants p ON p.plant_id=l.plant_id WHERE l.line_id=?",
                         (line_id,), "line")

    @staticmethod
    def valid_on(first: str, last: str | None, target: str) -> bool:
        return first <= target and (last is None or target <= last)

    @classmethod
    def active_line(cls, db, line_id: str, local_day: str):
        line = cls.line(db, line_id)
        if not cls.valid_on(line["active_from"], line["active_to"], local_day):
            raise Invariant("line inactive on event day")
        if not cls.valid_on(line["plant_from"], line["plant_to"], local_day):
            raise Invariant("plant inactive on event day")
        return line

    @staticmethod
    def specification(db, grade: str, local_day: str):
        rows = db.execute("SELECT * FROM specifications WHERE grade=? AND valid_from<=? "
                          "AND (valid_to IS NULL OR valid_to>=?) ORDER BY valid_from DESC",
                          (grade, local_day, local_day)).fetchall()
        if not rows:
            raise Missing("no specification for grade and day")
        if len(rows) != 1:
            raise Invariant("ambiguous grade specification")
        return rows[0]

    def list_plants(self) -> list[dict]:
        with self.store.read() as db:
            return [dict(row) for row in db.execute("SELECT * FROM plants ORDER BY plant_id")]

    def list_lines(self, plant_id: str) -> list[dict]:
        with self.store.read() as db:
            self.plant(db, identifier(plant_id, "plant_id"))
            return [dict(row) for row in db.execute("SELECT * FROM lines WHERE plant_id=? ORDER BY line_id", (plant_id,))]

    def list_specifications(self, grade: str) -> list[dict]:
        with self.store.read() as db:
            return [dict(row) for row in db.execute("SELECT * FROM specifications WHERE grade=? ORDER BY valid_from",
                                                    (identifier(grade, "grade"),))]
