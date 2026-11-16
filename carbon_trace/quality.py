from decimal import Decimal

from .catalog import Catalog
from .store import Store
from .values import decimal


class Quality:
    def latest_assay(self, db, lot_id: str, cutoff: str | None = None):
        query = "SELECT * FROM assays WHERE lot_id=?"
        params: tuple = (lot_id,)
        if cutoff is not None:
            query += " AND sampled_at<=?"
            params += (cutoff,)
        query += " ORDER BY sampled_at DESC,accepted_revision DESC,assay_id DESC LIMIT 1"
        return db.execute(query, params).fetchone()

    def classify(self, db, lot, *, cutoff: str | None = None) -> dict:
        spec = Catalog.specification(db, lot["grade"], lot["local_day"])
        assay = self.latest_assay(db, lot["lot_id"], cutoff)
        base = {"lot_id": lot["lot_id"], "grade": lot["grade"],
                "spec_id": spec["spec_id"], "spec_revision": spec["created_revision"],
                "assay_id": None, "assay_revision": None, "status": "unmeasured",
                "sulfur": None, "ash": None}
        if assay is None:
            return base
        sulfur = decimal(assay["sulfur"], "sulfur")
        ash = decimal(assay["ash"], "ash")
        low = decimal(spec["sulfur_min"], "sulfur_min")
        high = decimal(spec["sulfur_max"], "sulfur_max")
        ash_max = decimal(spec["ash_max"], "ash_max")
        reasons = []
        if sulfur < low:
            reasons.append("sulfur_below_min")
        if sulfur > high:
            reasons.append("sulfur_above_max")
        if ash > ash_max:
            reasons.append("ash_above_max")
        return {**base, "assay_id": assay["assay_id"],
                "assay_revision": assay["accepted_revision"],
                "status": "failed" if reasons else "passed", "reasons": reasons,
                "sulfur": assay["sulfur"], "ash": assay["ash"]}

    def distribution(self, db, lots: list) -> dict:
        counts = {"passed": 0, "failed": 0, "unmeasured": 0}
        quantities = {key: Decimal(0) for key in counts}
        classifications = {}
        for lot in lots:
            result = self.classify(db, lot)
            classifications[lot["lot_id"]] = result
            counts[result["status"]] += 1
            quantities[result["status"]] += decimal(lot["quantity"], "quantity")
        return {"counts": counts, "quantities": {key: str(value) for key, value in quantities.items()},
                "lots": classifications}

    def lot(self, store: Store, lot_id: str) -> dict:
        with store.read() as db:
            lot = Store.one(db, "SELECT * FROM lots WHERE lot_id=?", (lot_id,), "lot")
            return self.classify(db, lot)

