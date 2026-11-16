from common import ASSAY, LOT, SHIP, Scenario


class AnalyticsPublicationTests(Scenario):
    def test_daily_report_capacity_and_local_shipment_date(self):
        self.populated()
        report = self.app.report("P1", "2026-02-01", "2026-02-02")
        self.assertEqual(report["rows"][0]["production_tons"], "40")
        self.assertEqual(report["rows"][1]["shipped_tons"], "10")
        self.assertEqual(report["summary"]["utilization"], "0.2")

    def test_quality_passing_lot(self):
        self.populated()
        result = self.app.quality_of_lot("LOT1")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["assay_id"], "A1")
        self.assertEqual(result["spec_id"], "S1")

    def test_quality_missing_assay_is_explicit(self):
        self.catalog()
        self.ingest(LOT)
        quality = self.app.quality_of_lot("LOT1")
        self.assertEqual(quality["status"], "unmeasured")
        self.assertIsNone(quality["assay_id"])
        report = self.app.report("P1", "2026-02-01", "2026-02-01")
        self.assertEqual(report["summary"]["quality_tons"]["unmeasured"], "40")

    def test_quality_failed_with_reasons(self):
        self.catalog()
        self.ingest(LOT, {**ASSAY, "sulfur": "1.5", "ash": "1.0"})
        result = self.app.quality_of_lot("LOT1")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(set(result["reasons"]), {"sulfur_above_max", "ash_above_max"})

    def test_late_assay_changes_live_not_published(self):
        self.catalog()
        self.ingest(LOT, ASSAY)
        old = self._publish("V1")
        late = {**ASSAY, "external_id": "assay-2", "assay_id": "A2",
                "sampled_at": "2026-02-01T04:00:00Z", "sulfur": "1.5"}
        self.ingest(late, batch_id="B2")
        self.assertEqual(self.app.publication.version(1)["rows"][0]["quality_counts"]["passed"], 1)
        self.assertEqual(self.app.report("P1", "2026-02-01", "2026-02-01")["rows"][0]
                         ["quality_counts"]["failed"], 1)
        comparison = self.app.compare("P1", "2026-02-01", "2026-02-01", 1)
        self.assertTrue(comparison["changes"][0]["quality_changed"])
        self.assertEqual(old["version"], 1)

    def test_candidate_stale_after_new_event(self):
        self.catalog()
        self.ingest(LOT)
        revision = self.app.status()["revision"]
        self.app.prepare({"candidate_id": "V1", "plant_id": "P1", "from_day": "2026-02-01",
                          "to_day": "2026-02-01", "expected_head": 0, "expected_revision": revision})
        self.ingest(ASSAY, batch_id="B2")
        self.expect_domain(lambda: self.app.publish("V1", {"expected_head": 0,
                                                            "expected_revision": revision}), "stale_revision")
        self.assertEqual(self.app.status()["head"], 0)

    def test_candidate_stale_after_catalog_change(self):
        self.catalog()
        revision = self.app.status()["revision"]
        self.app.prepare({"candidate_id": "V1", "plant_id": "P1", "from_day": "2026-02-01",
                          "to_day": "2026-02-01", "expected_head": 0, "expected_revision": revision})
        self.app.register_line({"line_id": "L2", "plant_id": "P1", "daily_capacity": "50",
                                "active_from": "2026-02-01"})
        self.expect_domain(lambda: self.app.publish("V1", {"expected_head": 0,
                                                            "expected_revision": revision}), "stale_revision")

    def test_preparation_checks_head_and_revision(self):
        self.catalog()
        self.expect_domain(lambda: self.app.prepare({"candidate_id": "V1", "plant_id": "P1",
                        "from_day": "2026-02-01", "to_day": "2026-02-01", "expected_head": 1,
                        "expected_revision": 3}), "stale_revision")

    def test_published_head_prevents_competing_candidate(self):
        self.catalog()
        revision = self.app.status()["revision"]
        for candidate in ("V1", "V2"):
            self.app.prepare({"candidate_id": candidate, "plant_id": "P1", "from_day": "2026-02-01",
                              "to_day": "2026-02-01", "expected_head": 0, "expected_revision": revision})
        self.app.publish("V1", {"expected_head": 0, "expected_revision": revision})
        self.expect_domain(lambda: self.app.publish("V2", {"expected_head": 0,
                                                            "expected_revision": revision}), "stale_revision")
        self.assertEqual(self.app.status()["head"], 1)

    def test_second_version_uses_new_head(self):
        self.catalog()
        self._publish("V1")
        revision = self.app.status()["revision"]
        self.app.prepare({"candidate_id": "V2", "plant_id": "P1", "from_day": "2026-02-01",
                          "to_day": "2026-02-01", "expected_head": 1, "expected_revision": revision})
        second = self.app.publish("V2", {"expected_head": 1, "expected_revision": revision})
        self.assertEqual(second["version"], 2)
        self.assertEqual(len(self.app.publication.versions("P1")), 2)

    def test_publish_twice_conflicts(self):
        self.catalog()
        self._publish("V1")
        self.expect_domain(lambda: self.app.publish("V1", {"expected_head": 0,
                                    "expected_revision": 3}), "conflict")

    def test_inventory_excludes_fully_shipped_lots(self):
        self.populated()
        more = {**SHIP, "external_id": "ship-2", "shipment_id": "SH2", "quantity": "30"}
        self.ingest(more, batch_id="B2")
        result = self.app.inventory("P1", "2026-02-02")
        self.assertEqual(result["remaining_tons"], "0")
        self.assertEqual(result["lots"], [])

    def test_reconciliation_flags_missing_assay_shipment(self):
        self.catalog()
        self.ingest(LOT, SHIP)
        result = self.app.reconciliation.plant("P1", "2026-02-01", "2026-02-01")
        self.assertEqual(result["exception_count"], 1)
        self.assertIn("shipped_without_pass", result["exceptions"][0]["issues"])

    def test_forecast_accounts_recorded_production_and_due(self):
        self.populated()
        self.app.create_order({"order_id": "O1", "plant_id": "P1", "grade": "G1",
                               "due_day": "2026-02-02", "quantity": "150", "destination": "Buyer"})
        forecast = self.app.forecast.horizon("P1", "2026-02-01", 2)
        self.assertEqual(forecast["rows"][0]["available_tons"], "60")
        self.assertEqual(forecast["rows"][1]["unfilled_due_tons"], "150")
        self.assertEqual(forecast["rows"][1]["end_backlog_tons"], "50")

    def test_incident_annotates_report_and_stales_candidate(self):
        self.catalog()
        revision = self.app.status()["revision"]
        self.app.prepare({"candidate_id": "V1", "plant_id": "P1", "from_day": "2026-02-01",
                          "to_day": "2026-02-01", "expected_head": 0, "expected_revision": revision})
        self.app.open_incident({"incident_id": "I1", "line_id": "L1", "started_at": "2026-02-01T01:00:00Z",
                                "severity": "critical", "reason": "Synthetic maintenance"})
        row = self.app.report("P1", "2026-02-01", "2026-02-01")["rows"][0]
        self.assertEqual(row["affected_lines"], ["L1"])
        self.expect_domain(lambda: self.app.publish("V1", {"expected_head": 0,
                                    "expected_revision": revision}), "stale_revision")

    def test_trace_lot_contains_quality_and_audit(self):
        self.populated()
        trace = self.app.trace.lot("LOT1")
        self.assertEqual(trace["quality"]["status"], "passed")
        self.assertEqual(len(trace["shipments"]), 1)
        self.assertEqual([row["cause"] for row in trace["audit"]], ["lot", "assay", "shipment"])

    def test_scope_mismatch_comparison_is_rejected(self):
        self.catalog()
        self._publish("V1")
        self.expect_domain(lambda: self.app.compare("P1", "2026-02-01", "2026-02-02", 1),
                           "invalid_input")

    def test_invalid_interval_is_rejected(self):
        self.catalog()
        self.expect_domain(lambda: self.app.report("P1", "2026-02-02", "2026-02-01"),
                           "invalid_input")

    def test_too_long_interval_is_rejected(self):
        self.catalog()
        self.expect_domain(lambda: self.app.report("P1", "2026-01-01", "2027-02-01"),
                           "invalid_input")

    def test_reopen_store_preserves_immutable_version(self):
        self.catalog()
        self._publish("V1")
        from carbon_trace.service import Service
        reopened = Service(self.path)
        self.assertEqual(reopened.publication.version(1)["version"], 1)
        self.assertEqual(reopened.status()["head"], 1)

    def _publish(self, candidate_id):
        revision = self.app.status()["revision"]
        self.app.prepare({"candidate_id": candidate_id, "plant_id": "P1", "from_day": "2026-02-01",
                          "to_day": "2026-02-01", "expected_head": self.app.status()["head"],
                          "expected_revision": revision})
        return self.app.publish(candidate_id, {"expected_head": self.app.status()["head"],
                                               "expected_revision": revision})
