import tempfile
import unittest
from pathlib import Path

from carbon_trace.errors import DomainError
from carbon_trace.service import Service


PLANT = {"plant_id": "P1", "name": "Synthetic Plant", "timezone": "Asia/Shanghai",
         "active_from": "2026-01-01"}
LINE = {"line_id": "L1", "plant_id": "P1", "daily_capacity": "100",
        "active_from": "2026-01-01"}
SPEC = {"spec_id": "S1", "grade": "G1", "valid_from": "2026-01-01",
        "sulfur_min": "0", "sulfur_max": "1.2", "ash_max": "0.8"}
LOT = {"kind": "lot", "external_id": "lot-1", "lot_id": "LOT1", "line_id": "L1",
       "grade": "G1", "produced_at": "2026-02-01T02:00:00Z", "quantity": "40"}
ASSAY = {"kind": "assay", "external_id": "assay-1", "assay_id": "A1", "lot_id": "LOT1",
         "sampled_at": "2026-02-01T03:00:00Z", "sulfur": "0.7", "ash": "0.4"}
SHIP = {"kind": "shipment", "external_id": "ship-1", "shipment_id": "SH1", "lot_id": "LOT1",
        "shipped_at": "2026-02-02T04:00:00Z", "quantity": "10", "destination": "Buyer"}
ORDER = {"order_id": "O1", "plant_id": "P1", "grade": "G1",
         "due_day": "2026-02-05", "quantity": "30", "destination": "Buyer"}


class Scenario(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "app.sqlite3")
        self.app = Service(self.path)

    def catalog(self):
        self.app.register_plant(dict(PLANT))
        self.app.register_line(dict(LINE))
        self.app.register_specification(dict(SPEC))

    def ingest(self, *events, source="factory", batch_id="B1"):
        return self.app.ingest({"source": source, "batch_id": batch_id,
                                "events": [dict(event) for event in events]})

    def populated(self):
        self.catalog()
        result = self.ingest(LOT, ASSAY, SHIP)
        self.assertEqual(len(result["accepted"]), 3)

    def assert_rejected(self, result, code=None):
        self.assertEqual(result["accepted"], [])
        self.assertEqual(len(result["rejected"]), 1)
        if code:
            self.assertEqual(result["rejected"][0]["error"], code)

    def expect_domain(self, operation, code=None):
        with self.assertRaises(DomainError) as raised:
            operation()
        if code:
            self.assertEqual(raised.exception.code, code)
        return raised.exception

