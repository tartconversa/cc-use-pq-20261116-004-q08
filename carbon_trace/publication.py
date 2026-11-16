import json
from datetime import datetime, timezone

from .analytics import Analytics
from .catalog import Catalog
from .errors import Conflict, Stale, require
from .store import Store
from .values import day, digest, identifier


class Publication:
    def __init__(self, store: Store):
        self.store = store

    def prepare(self, candidate_id: str, plant_id: str, from_day: str, to_day: str,
                expected_head: int, expected_revision: int) -> dict:
        candidate_id = identifier(candidate_id, "candidate_id")
        plant_id = identifier(plant_id, "plant_id")
        from_day = day(from_day, "from_day")
        to_day = day(to_day, "to_day")
        require(type(expected_head) is int and expected_head >= 0, "invalid expected_head")
        require(type(expected_revision) is int and expected_revision >= 0, "invalid expected_revision")
        with self.store.write() as db:
            if self.store.head(db) != expected_head or self.store.revision(db) != expected_revision:
                raise Stale("head or source revision changed before preparation")
            Catalog.plant(db, plant_id)
            if db.execute("SELECT 1 FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone():
                raise Conflict("candidate_id already exists")
            rows = Analytics(self.store).calculate(db, plant_id, from_day, to_day)
            rows_json = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            rows_digest = digest(rows)
            db.execute("INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?)",
                       (candidate_id, plant_id, from_day, to_day, expected_revision,
                        expected_head, rows_json, rows_digest, "prepared"))
            return {"candidate_id": candidate_id, "plant_id": plant_id,
                    "source_revision": expected_revision, "expected_head": expected_head,
                    "rows_digest": rows_digest, "rows": rows}

    def publish(self, candidate_id: str, expected_head: int, expected_revision: int) -> dict:
        candidate_id = identifier(candidate_id, "candidate_id")
        require(type(expected_head) is int and expected_head >= 0, "invalid expected_head")
        require(type(expected_revision) is int and expected_revision >= 0, "invalid expected_revision")
        with self.store.write() as db:
            candidate = Store.one(db, "SELECT * FROM candidates WHERE candidate_id=?",
                                  (candidate_id,), "candidate")
            if candidate["status"] != "prepared":
                raise Conflict("candidate already published")
            if (candidate["expected_head"] != expected_head or candidate["base_revision"] != expected_revision
                    or self.store.head(db) != expected_head or self.store.revision(db) != expected_revision):
                raise Stale("candidate no longer matches head and source revision")
            rows = json.loads(candidate["rows_json"])
            if digest(rows) != candidate["rows_digest"]:
                raise Conflict("candidate row digest mismatch")
            version = expected_head + 1
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            db.execute("INSERT INTO publications VALUES (?,?,?,?,?,?,?,?)",
                       (version, candidate_id, candidate["plant_id"], candidate["from_day"],
                        candidate["to_day"], expected_revision, candidate["rows_digest"], timestamp))
            db.executemany("INSERT INTO publication_rows VALUES (?,?,?)",
                           [(version, row["day"], json.dumps(row, ensure_ascii=False, sort_keys=True))
                            for row in rows])
            db.execute("UPDATE candidates SET status='published' WHERE candidate_id=?", (candidate_id,))
            db.execute("UPDATE state SET head=? WHERE singleton=1", (version,))
            return {"version": version, "candidate_id": candidate_id,
                    "source_revision": expected_revision, "rows_digest": candidate["rows_digest"]}

    def candidate(self, candidate_id: str) -> dict:
        with self.store.read() as db:
            row = Store.one(db, "SELECT * FROM candidates WHERE candidate_id=?",
                            (identifier(candidate_id, "candidate_id"),), "candidate")
            return {**dict(row), "rows": json.loads(row["rows_json"])}

    def version(self, version: int) -> dict:
        require(type(version) is int and version > 0, "invalid version")
        with self.store.read() as db:
            row = Store.one(db, "SELECT * FROM publications WHERE version=?", (version,), "publication")
            rows = [json.loads(item["data_json"]) for item in db.execute(
                "SELECT * FROM publication_rows WHERE version=? ORDER BY day", (version,))]
            if digest(rows) != row["rows_digest"]:
                raise Conflict("stored publication digest mismatch")
            return {**dict(row), "rows": rows, "summary": Analytics.summarize(rows)}

    def versions(self, plant_id: str) -> list[dict]:
        plant_id = identifier(plant_id, "plant_id")
        with self.store.read() as db:
            Catalog.plant(db, plant_id)
            return [dict(row) for row in db.execute(
                "SELECT * FROM publications WHERE plant_id=? ORDER BY version", (plant_id,))]
