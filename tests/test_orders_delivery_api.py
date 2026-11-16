from fastapi.testclient import TestClient

from carbon_trace.api import create_app
from common import ASSAY, LOT, ORDER, SHIP, Scenario


class OrdersDeliveryApiTests(Scenario):
    def test_order_and_reservation_happy_path(self):
        self.catalog()
        self.ingest(LOT, ASSAY)
        self.app.create_order(ORDER)
        revision = self.app.status()["revision"]
        result = self.app.reserve({"reservation_id": "R1", "order_id": "O1", "lot_id": "LOT1",
                                   "quantity": "20", "expected_revision": revision})
        self.assertEqual(result["quantity"], "20")
        self.assertEqual(self.app.orders.detail("O1")["reserved_tons"], "20")

    def test_unmeasured_lot_cannot_be_reserved(self):
        self.catalog()
        self.ingest(LOT)
        self.app.create_order(ORDER)
        self.expect_domain(lambda: self.app.reserve({"reservation_id": "R1", "order_id": "O1",
                          "lot_id": "LOT1", "quantity": "10",
                          "expected_revision": self.app.status()["revision"]}), "business_invariant")

    def test_failed_lot_cannot_be_reserved(self):
        self.catalog()
        self.ingest(LOT, {**ASSAY, "sulfur": "1.9"})
        self.app.create_order(ORDER)
        self.expect_domain(lambda: self.app.reserve({"reservation_id": "R1", "order_id": "O1",
                          "lot_id": "LOT1", "quantity": "10",
                          "expected_revision": self.app.status()["revision"]}), "business_invariant")

    def test_stale_reservation_does_not_consume_material(self):
        self.catalog()
        self.ingest(LOT, ASSAY)
        self.app.create_order(ORDER)
        self.expect_domain(lambda: self.app.reserve({"reservation_id": "R1", "order_id": "O1",
                          "lot_id": "LOT1", "quantity": "10", "expected_revision": 0}), "stale_revision")
        self.assertEqual(self.app.orders.detail("O1")["reservations"], [])

    def test_order_reservation_limit(self):
        self.catalog()
        self.ingest(LOT, ASSAY)
        self.app.create_order(ORDER)
        self.expect_domain(lambda: self.app.reserve({"reservation_id": "R1", "order_id": "O1",
                          "lot_id": "LOT1", "quantity": "31",
                          "expected_revision": self.app.status()["revision"]}), "business_invariant")

    def test_lot_reservation_limit_across_orders(self):
        self.catalog()
        self.ingest(LOT, ASSAY)
        self.app.create_order(ORDER)
        self.app.create_order({**ORDER, "order_id": "O2"})
        self.app.reserve({"reservation_id": "R1", "order_id": "O1", "lot_id": "LOT1",
                          "quantity": "25", "expected_revision": self.app.status()["revision"]})
        self.expect_domain(lambda: self.app.reserve({"reservation_id": "R2", "order_id": "O2",
                          "lot_id": "LOT1", "quantity": "20",
                          "expected_revision": self.app.status()["revision"]}), "business_invariant")

    def test_shipment_cannot_take_reserved_material(self):
        self.catalog()
        self.ingest(LOT, ASSAY)
        self.app.create_order(ORDER)
        self.app.reserve({"reservation_id": "R1", "order_id": "O1", "lot_id": "LOT1",
                          "quantity": "30", "expected_revision": self.app.status()["revision"]})
        self.assert_rejected(self.ingest({**SHIP, "quantity": "11"}, batch_id="B2"), "business_invariant")
        self.assertEqual(len(self.ingest({**SHIP, "quantity": "10"}, batch_id="B3")["accepted"]), 1)

    def test_release_makes_stock_available(self):
        self.catalog()
        self.ingest(LOT, ASSAY)
        self.app.create_order(ORDER)
        self.app.reserve({"reservation_id": "R1", "order_id": "O1", "lot_id": "LOT1",
                          "quantity": "30", "expected_revision": self.app.status()["revision"]})
        self.app.release("R1", {"expected_revision": self.app.status()["revision"]})
        self.assertEqual(self.app.orders.detail("O1")["reserved_tons"], "0")
        self.assertEqual(len(self.ingest({**SHIP, "quantity": "40"}, batch_id="B2")["accepted"]), 1)

    def test_cancel_order_releases_reservations(self):
        self.catalog()
        self.ingest(LOT, ASSAY)
        self.app.create_order(ORDER)
        self.app.reserve({"reservation_id": "R1", "order_id": "O1", "lot_id": "LOT1",
                          "quantity": "20", "expected_revision": self.app.status()["revision"]})
        self.app.cancel_order("O1", {"expected_revision": self.app.status()["revision"]})
        detail = self.app.orders.detail("O1")
        self.assertEqual(detail["status"], "cancelled")
        self.assertEqual(detail["reserved_tons"], "0")
        self.assertEqual(detail["reservations"][0]["status"], "released")

    def test_allocation_report_tracks_unfilled(self):
        self.catalog()
        self.ingest(LOT, ASSAY)
        self.app.create_order(ORDER)
        self.app.reserve({"reservation_id": "R1", "order_id": "O1", "lot_id": "LOT1",
                          "quantity": "20", "expected_revision": self.app.status()["revision"]})
        row = self.app.orders.allocation("P1", "2026-02-05")["orders"][0]
        self.assertEqual(row["unfilled_tons"], "10")

    def test_delivery_commitment_and_receipt(self):
        self.populated()
        self.app.create_order(ORDER)
        commitment = self.app.delivery.commit({"commitment_id": "C1", "shipment_id": "SH1",
                                               "order_id": "O1", "expected_revision": self.app.status()["revision"]})
        self.assertEqual(commitment["quantity"], "10")
        self.app.delivery.check({"check_id": "D1", "shipment_id": "SH1",
                                 "checked_at": "2026-02-03T01:00:00Z", "received_quantity": "9",
                                 "condition": "accepted", "note": "Synthetic received"})
        self.assertEqual(self.app.delivery.order("O1")["accepted_received_tons"], "9")
        self.assertEqual(self.app.delivery.shipment("SH1")["latest_condition"], "accepted")

    def test_delivery_check_before_shipment_rejected(self):
        self.populated()
        self.expect_domain(lambda: self.app.delivery.check({"check_id": "D1", "shipment_id": "SH1",
                           "checked_at": "2026-02-01T00:00:00Z", "received_quantity": "10",
                           "condition": "accepted", "note": "Early"}), "business_invariant")

    def test_delivery_check_cannot_exceed_shipment(self):
        self.populated()
        self.expect_domain(lambda: self.app.delivery.check({"check_id": "D1", "shipment_id": "SH1",
                           "checked_at": "2026-02-03T00:00:00Z", "received_quantity": "11",
                           "condition": "accepted", "note": "Excess"}), "business_invariant")

    def test_api_cross_component_workflow(self):
        client = TestClient(create_app(self.path))
        self.assertEqual(client.post("/plants", json={"plant_id": "P1", "name": "Plant",
                        "timezone": "Asia/Shanghai", "active_from": "2026-01-01"}).status_code, 201)
        self.assertEqual(client.post("/lines", json={"line_id": "L1", "plant_id": "P1",
                        "daily_capacity": "100", "active_from": "2026-01-01"}).status_code, 201)
        self.assertEqual(client.post("/specifications", json={"spec_id": "S1", "grade": "G1",
                        "valid_from": "2026-01-01", "sulfur_min": "0", "sulfur_max": "1.2",
                        "ash_max": "0.8"}).status_code, 201)
        batch = client.post("/batches", json={"source": "factory", "batch_id": "B1",
                              "events": [LOT, ASSAY]})
        self.assertEqual(batch.status_code, 200)
        self.assertEqual(len(batch.json()["accepted"]), 2)
        report = client.get("/plants/P1/report", params={"from_day": "2026-02-01",
                         "to_day": "2026-02-01"})
        self.assertEqual(report.json()["summary"]["quality_counts"]["passed"], 1)
        status = client.get("/status").json()
        candidate = client.post("/candidates", json={"candidate_id": "V1", "plant_id": "P1",
                         "from_day": "2026-02-01", "to_day": "2026-02-01",
                         "expected_head": status["head"], "expected_revision": status["revision"]})
        self.assertEqual(candidate.status_code, 201)
        published = client.post("/candidates/V1/publish", json={"expected_head": 0,
                                "expected_revision": status["revision"]})
        self.assertEqual(published.status_code, 201)
        self.assertEqual(client.get("/versions/1").json()["rows"][0]["lot_count"], 1)

    def test_api_domain_error_status_and_code(self):
        client = TestClient(create_app(self.path))
        result = client.get("/lots/unknown/quality")
        self.assertEqual(result.status_code, 404)
        self.assertEqual(result.json()["error"], "not_found")

    def test_api_batch_rejections_visible(self):
        self.catalog()
        client = TestClient(create_app(self.path))
        result = client.post("/batches", json={"source": "factory", "batch_id": "B1",
                               "events": [{**LOT, "quantity": "-1"}]})
        self.assertEqual(result.json()["rejected"][0]["error"], "invalid_input")
        self.assertEqual(client.get("/batches/B1/rejections").json()[0]["row_index"], 0)

    def test_api_stale_reservation_status(self):
        self.catalog()
        self.ingest(LOT, ASSAY)
        self.app.create_order(ORDER)
        client = TestClient(create_app(self.path))
        response = client.post("/reservations", json={"reservation_id": "R1", "order_id": "O1",
                               "lot_id": "LOT1", "quantity": "10", "expected_revision": 0})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "stale_revision")
