from datetime import date, timedelta

from .catalog import Catalog
from .errors import Conflict, Invariant, Stale, require
from .store import Store
from .values import identifier, instant, local_day, text


class Incidents:
    SEVERITIES = {"minor", "major", "critical"}

    def __init__(self, store: Store):
        self.store = store

    def open(self, payload: dict) -> dict:
        incident_id = identifier(payload.get("incident_id"), "incident_id")
        line_id = identifier(payload.get("line_id"), "line_id")
        started_at = instant(payload.get("started_at"), "started_at")
        severity = payload.get("severity")
        require(severity in self.SEVERITIES, "invalid severity")
        reason = text(payload.get("reason"), "reason", max_length=400)
        with self.store.write() as db:
            line = Catalog.line(db, line_id)
            start_day = local_day(started_at, line["timezone"])
            Catalog.active_line(db, line_id, start_day)
            if db.execute("SELECT 1 FROM incidents WHERE incident_id=?", (incident_id,)).fetchone():
                raise Conflict("incident_id already exists")
            overlapping = db.execute("SELECT 1 FROM incidents WHERE line_id=? AND status='open'",
                                     (line_id,)).fetchone()
            if overlapping:
                raise Invariant("line already has an open incident")
            revision = Store.advance(db, "incident_open", incident_id)
            db.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (incident_id, line["plant_id"], line_id, started_at, None,
                        start_day, None, severity, reason, "open", revision, None))
            return {"incident_id": incident_id, "plant_id": line["plant_id"],
                    "start_day": start_day, "revision": revision}

    def close(self, incident_id: str, ended_at: str, expected_revision: int) -> dict:
        incident_id = identifier(incident_id, "incident_id")
        ended_at = instant(ended_at, "ended_at")
        require(type(expected_revision) is int and expected_revision >= 0, "invalid expected_revision")
        with self.store.write() as db:
            if Store.revision(db) != expected_revision:
                raise Stale("source revision changed")
            incident = Store.one(db, "SELECT * FROM incidents WHERE incident_id=?",
                                 (incident_id,), "incident")
            if incident["status"] != "open":
                raise Conflict("incident already closed")
            require(ended_at >= incident["started_at"], "incident ends before start")
            line = Catalog.line(db, incident["line_id"])
            end_day = local_day(ended_at, line["timezone"])
            revision = Store.advance(db, "incident_close", incident_id)
            db.execute("UPDATE incidents SET ended_at=?,end_day=?,status='closed',closed_revision=? "
                       "WHERE incident_id=?", (ended_at, end_day, revision, incident_id))
            return {"incident_id": incident_id, "end_day": end_day,
                    "status": "closed", "revision": revision}

    def list(self, plant_id: str, from_day: str, to_day: str) -> list[dict]:
        plant_id = identifier(plant_id, "plant_id")
        with self.store.read() as db:
            Catalog.plant(db, plant_id)
            return [dict(row) for row in db.execute(
                "SELECT * FROM incidents WHERE plant_id=? AND start_day<=? "
                "AND (end_day IS NULL OR end_day>=?) ORDER BY start_day,incident_id",
                (plant_id, to_day, from_day))]

    @staticmethod
    def coverage(db, plant_id: str, target_day: str) -> dict:
        rows = db.execute("SELECT * FROM incidents WHERE plant_id=? AND start_day<=? "
                          "AND (end_day IS NULL OR end_day>=?) ORDER BY incident_id",
                          (plant_id, target_day, target_day)).fetchall()
        return {"incident_count": len(rows),
                "affected_lines": sorted({row["line_id"] for row in rows}),
                "critical_count": sum(row["severity"] == "critical" for row in rows)}
