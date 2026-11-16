from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .catalog import Catalog
from .errors import InvalidInput, require
from .incidents import Incidents
from .quality import Quality
from .store import Store
from .values import as_number, day, decimal, identifier


class Analytics:
    def __init__(self, store: Store):
        self.store = store
        self.quality = Quality()

    def report(self, plant_id: str, from_day: str, to_day: str) -> dict:
        plant_id = identifier(plant_id, "plant_id")
        from_day = day(from_day, "from_day")
        to_day = day(to_day, "to_day")
        require(from_day <= to_day, "date interval is inverted")
        require((date.fromisoformat(to_day) - date.fromisoformat(from_day)).days <= 366,
                "date interval exceeds 366 days")
        with self.store.read() as db:
            Catalog.plant(db, plant_id)
            rows = self.calculate(db, plant_id, from_day, to_day)
            return {"plant_id": plant_id, "from_day": from_day, "to_day": to_day,
                    "source_revision": Store.revision(db), "rows": rows,
                    "summary": self.summarize(rows)}

    def calculate(self, db, plant_id: str, from_day: str, to_day: str) -> list[dict]:
        plant = Catalog.plant(db, plant_id)
        lines = db.execute("SELECT * FROM lines WHERE plant_id=? ORDER BY line_id", (plant_id,)).fetchall()
        lots = db.execute("SELECT lots.* FROM lots JOIN lines ON lots.line_id=lines.line_id "
                          "WHERE lines.plant_id=? AND lots.local_day BETWEEN ? AND ? "
                          "ORDER BY lots.local_day,lots.lot_id", (plant_id, from_day, to_day)).fetchall()
        shipped = db.execute("SELECT shipments.* FROM shipments JOIN lots ON lots.lot_id=shipments.lot_id "
                             "JOIN lines ON lines.line_id=lots.line_id WHERE lines.plant_id=? "
                             "AND shipments.local_day BETWEEN ? AND ? ORDER BY shipments.local_day,shipments.shipment_id",
                             (plant_id, from_day, to_day)).fetchall()
        lots_by_day = defaultdict(list)
        shipments_by_day = defaultdict(list)
        for lot in lots:
            lots_by_day[lot["local_day"]].append(lot)
        for shipment in shipped:
            shipments_by_day[shipment["local_day"]].append(shipment)
        current = date.fromisoformat(from_day)
        final = date.fromisoformat(to_day)
        rows = []
        while current <= final:
            label = current.isoformat()
            active_plant = Catalog.valid_on(plant["active_from"], plant["active_to"], label)
            active_lines = [line for line in lines if active_plant and Catalog.valid_on(
                line["active_from"], line["active_to"], label)]
            capacity = sum((decimal(line["daily_capacity"], "capacity") for line in active_lines), Decimal(0))
            day_lots = lots_by_day[label]
            produced = sum((decimal(lot["quantity"], "quantity") for lot in day_lots), Decimal(0))
            shipped_tons = sum((decimal(ship["quantity"], "quantity")
                                for ship in shipments_by_day[label]), Decimal(0))
            quality = self.quality.distribution(db, day_lots)
            incidents = Incidents.coverage(db, plant_id, label)
            if capacity == 0 and produced:
                raise InvalidInput("production exists without active capacity")
            rows.append({"day": label, "plant_active": active_plant,
                         "active_lines": len(active_lines), "capacity_tons": as_number(capacity),
                         "production_tons": as_number(produced),
                         "utilization": as_number(produced / capacity) if capacity else None,
                         "shipped_tons": as_number(shipped_tons),
                         "lot_count": len(day_lots), "quality_counts": quality["counts"],
                         "quality_tons": quality["quantities"],
                         "shipment_count": len(shipments_by_day[label])})
            rows[-1].update(incidents)
            current += timedelta(days=1)
        return rows

    @staticmethod
    def summarize(rows: list[dict]) -> dict:
        fields = ("capacity_tons", "production_tons", "shipped_tons")
        sums = {field: sum((decimal(row[field], field) for row in rows), Decimal(0)) for field in fields}
        counts = {key: sum(row["quality_counts"][key] for row in rows)
                  for key in ("passed", "failed", "unmeasured")}
        quality_tons = {key: sum((decimal(row["quality_tons"][key], key) for row in rows), Decimal(0))
                        for key in counts}
        return {**{key: as_number(value) for key, value in sums.items()},
                "utilization": as_number(sums["production_tons"] / sums["capacity_tons"])
                if sums["capacity_tons"] else None,
                "lot_count": sum(row["lot_count"] for row in rows),
                "shipment_count": sum(row["shipment_count"] for row in rows),
                "incident_days": sum(bool(row["incident_count"]) for row in rows),
                "critical_incident_days": sum(bool(row["critical_count"]) for row in rows),
                "quality_counts": counts,
                "quality_tons": {key: as_number(value) for key, value in quality_tons.items()}}

    def inventory(self, plant_id: str, as_of: str) -> dict:
        plant_id = identifier(plant_id, "plant_id")
        as_of = day(as_of, "as_of")
        with self.store.read() as db:
            Catalog.plant(db, plant_id)
            lots = db.execute("SELECT lots.* FROM lots JOIN lines ON lines.line_id=lots.line_id "
                              "WHERE lines.plant_id=? AND lots.local_day<=? ORDER BY lots.local_day,lots.lot_id",
                              (plant_id, as_of)).fetchall()
            entries = []
            for lot in lots:
                shipped = db.execute("SELECT quantity FROM shipments WHERE lot_id=? AND local_day<=?",
                                     (lot["lot_id"], as_of)).fetchall()
                shipped_tons = sum((decimal(row["quantity"], "quantity") for row in shipped), Decimal(0))
                quantity = decimal(lot["quantity"], "quantity")
                remaining = quantity - shipped_tons
                if remaining <= 0:
                    continue
                quality = self.quality.classify(db, lot)
                entries.append({"lot_id": lot["lot_id"], "grade": lot["grade"],
                                "produced_day": lot["local_day"], "quantity": as_number(quantity),
                                "shipped": as_number(shipped_tons), "remaining": as_number(remaining),
                                "quality_status": quality["status"], "assay_id": quality["assay_id"]})
            total = sum((decimal(row["remaining"], "remaining") for row in entries), Decimal(0))
            return {"plant_id": plant_id, "as_of": as_of, "source_revision": Store.revision(db),
                    "remaining_tons": as_number(total), "lots": entries}

    def compare(self, plant_id: str, from_day: str, to_day: str, version: int) -> dict:
        from .publication import Publication
        live = self.report(plant_id, from_day, to_day)
        frozen = Publication(self.store).version(version)
        require(frozen["plant_id"] == plant_id and frozen["from_day"] == from_day and
                frozen["to_day"] == to_day, "version scope differs from live query")
        fields = ("capacity_tons", "production_tons", "shipped_tons")
        changes = []
        for current, old in zip(live["rows"], frozen["rows"], strict=True):
            if current == old:
                continue
            changes.append({"day": current["day"],
                            "metric_deltas": {field: as_number(decimal(current[field], field) -
                                                               decimal(old[field], field)) for field in fields},
                            "quality_changed": current["quality_counts"] != old["quality_counts"] or
                            current["quality_tons"] != old["quality_tons"]})
        return {"published_version": version, "published_revision": frozen["source_revision"],
                "live_revision": live["source_revision"], "changes": changes}
