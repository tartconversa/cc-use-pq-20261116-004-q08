from copy import deepcopy

from common import ASSAY, LINE, LOT, PLANT, SHIP, SPEC, Scenario


class CatalogIntakeTests(Scenario):
    def test_catalog_records_and_revision(self):
        self.catalog()
        self.assertEqual(self.app.status()["revision"], 3)
        self.assertEqual(self.app.catalog.list_plants()[0]["plant_id"], "P1")
        self.assertEqual(self.app.catalog.list_lines("P1")[0]["line_id"], "L1")
        self.assertEqual(self.app.catalog.list_specifications("G1")[0]["spec_id"], "S1")

    def test_duplicate_plant_and_line_do_not_change_revision(self):
        self.catalog()
        self.expect_domain(lambda: self.app.register_plant(PLANT), "conflict")
        self.expect_domain(lambda: self.app.register_line(LINE), "conflict")
        self.assertEqual(self.app.status()["revision"], 3)

    def test_specification_overlap_rejected(self):
        self.catalog()
        next_spec = {**SPEC, "spec_id": "S2", "valid_from": "2026-02-01"}
        self.expect_domain(lambda: self.app.register_specification(next_spec), "conflict")
        self.assertEqual(self.app.catalog.list_specifications("G1")[0]["spec_id"], "S1")

    def test_nonoverlap_specifications(self):
        self.app.register_plant(PLANT)
        self.app.register_line(LINE)
        self.app.register_specification({**SPEC, "valid_to": "2026-01-31"})
        self.app.register_specification({**SPEC, "spec_id": "S2", "valid_from": "2026-02-01"})
        self.assertEqual(len(self.app.catalog.list_specifications("G1")), 2)
        self.assertEqual(len(self.ingest(LOT)["accepted"]), 1)
        self.assertEqual(self.app.quality_of_lot("LOT1")["spec_id"], "S2")

    def test_source_event_deduplication(self):
        self.catalog()
        first = self.ingest(LOT)
        second = self.ingest(LOT, batch_id="B2")
        self.assertEqual(first["accepted"][0]["revision"], 4)
        self.assertEqual(second["duplicate"][0]["revision"], 4)
        self.assertEqual(self.app.status()["revision"], 4)

    def test_batch_replay_identical_result(self):
        self.catalog()
        first = self.ingest(LOT)
        replay = self.ingest(LOT)
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["accepted"], replay["accepted"])
        self.assertEqual(self.app.status()["revision"], 4)

    def test_batch_conflict_preserves_first_request(self):
        self.catalog()
        self.ingest(LOT)
        changed = {**LOT, "quantity": "41"}
        self.expect_domain(lambda: self.ingest(changed), "conflict")
        self.assertEqual(self.app.intake.batch("B1")["accepted"][0]["external_id"], "lot-1")

    def test_source_identity_conflict_is_row_rejection(self):
        self.catalog()
        self.ingest(LOT)
        changed = {**LOT, "quantity": "41"}
        result = self.ingest(changed, batch_id="B2")
        self.assert_rejected(result, "conflict")
        self.assertEqual(self.app.status()["revision"], 4)
        self.assertEqual(self.app.intake.rejections("B2")[0]["error_code"], "conflict")

    def test_row_savepoint_keeps_valid_rows(self):
        self.catalog()
        invalid = {**LOT, "external_id": "bad", "lot_id": "LOTBAD", "quantity": "-1"}
        result = self.ingest(invalid, LOT, ASSAY)
        self.assertEqual([x["external_id"] for x in result["accepted"]], ["lot-1", "assay-1"])
        self.assertEqual(result["rejected"][0]["row"], 0)
        self.assertEqual(self.app.status()["revision"], 5)

    def test_missing_parent_lot_rejection(self):
        self.catalog()
        result = self.ingest(ASSAY)
        self.assert_rejected(result, "not_found")
        self.assertEqual(self.app.status()["revision"], 3)

    def test_local_day_crosses_utc_date(self):
        self.catalog()
        event = {**LOT, "produced_at": "2026-01-31T18:00:00Z"}
        self.assertEqual(len(self.ingest(event)["accepted"]), 1)
        self.assertEqual(self.app.report("P1", "2026-02-01", "2026-02-01")["rows"][0]["lot_count"], 1)

    def test_lifecycle_rejects_early_production(self):
        self.catalog()
        event = {**LOT, "produced_at": "2025-12-31T10:00:00Z"}
        self.assert_rejected(self.ingest(event), "business_invariant")
        self.assertEqual(self.app.status()["lots"], 0)

    def test_missing_specification_rejects_lot(self):
        self.app.register_plant(PLANT)
        self.app.register_line(LINE)
        self.assert_rejected(self.ingest(LOT), "not_found")
        self.assertEqual(self.app.status()["revision"], 2)

    def test_shipment_before_production_rejected(self):
        self.catalog()
        self.ingest(LOT)
        earlier = {**SHIP, "shipped_at": "2026-01-31T01:00:00Z"}
        self.assert_rejected(self.ingest(earlier, batch_id="B2"), "invalid_input")

    def test_shipment_over_lot_quantity_rejected(self):
        self.catalog()
        self.ingest(LOT)
        excess = {**SHIP, "quantity": "41"}
        self.assert_rejected(self.ingest(excess, batch_id="B2"), "business_invariant")

    def test_second_shipment_cannot_overdraw(self):
        self.populated()
        excess = {**SHIP, "shipment_id": "SH2", "external_id": "ship-2", "quantity": "31"}
        self.assert_rejected(self.ingest(excess, batch_id="B2"), "business_invariant")
        self.assertEqual(self.app.inventory("P1", "2026-02-02")["remaining_tons"], "30")

    def test_assay_must_follow_production(self):
        self.catalog()
        self.ingest(LOT)
        earlier = {**ASSAY, "sampled_at": "2026-01-31T00:00:00Z"}
        self.assert_rejected(self.ingest(earlier, batch_id="B2"), "invalid_input")

    def test_assay_percentages_bounded(self):
        self.catalog()
        self.ingest(LOT)
        high = {**ASSAY, "sulfur": "101"}
        self.assert_rejected(self.ingest(high, batch_id="B2"), "invalid_input")

    def test_source_policy_blocks_kind(self):
        self.catalog()
        self.app.configure_source("factory", {"enabled": True, "kinds": ["assay"],
                                              "max_batch_size": 2, "expected_revision": 3})
        self.assert_rejected(self.ingest(LOT), "business_invariant")
        self.assertEqual(self.app.status()["lots"], 0)

    def test_source_policy_blocks_large_batch(self):
        self.catalog()
        self.app.configure_source("factory", {"enabled": True, "kinds": ["lot"],
                                              "max_batch_size": 1, "expected_revision": 3})
        self.expect_domain(lambda: self.ingest(LOT, LOT), "business_invariant")
        self.assertEqual(self.app.status()["revision"], 4)

    def test_disabled_source_does_not_replay_new_batch(self):
        self.catalog()
        self.ingest(LOT)
        self.app.configure_source("factory", {"enabled": False, "kinds": ["lot"],
                                              "max_batch_size": 1, "expected_revision": 4})
        self.assertTrue(self.ingest(LOT)["replayed"])
        self.expect_domain(lambda: self.ingest(ASSAY, batch_id="B2"), "business_invariant")

    def test_source_reconciliation_counts_rejections(self):
        self.catalog()
        self.ingest(LOT)
        self.ingest({**ASSAY, "lot_id": "unknown"}, batch_id="B2")
        result = self.app.reconciliation.source("factory")
        self.assertEqual(result["accepted_by_kind"][0]["event_kind"], "lot")
        self.assertEqual(result["rejected_by_code"][0]["total"], 1)

    def test_audit_revisions_follow_valid_events_only(self):
        self.catalog()
        self.ingest({**LOT, "quantity": "-1"}, LOT)
        timeline = self.app.trace.timeline(1, 4)
        self.assertEqual([item["revision"] for item in timeline], [1, 2, 3, 4])
        self.assertEqual(timeline[-1]["cause"], "lot")

    def test_receipt_and_trace_resolve_same_event(self):
        self.catalog()
        self.ingest(LOT)
        receipt = self.app.intake.receipt("factory", "lot-1")
        trace = self.app.trace.source_event("factory", "lot-1")
        self.assertEqual(receipt["accepted_revision"], trace["audit"]["revision"])
        self.assertEqual(trace["entity"]["lot_id"], "LOT1")
