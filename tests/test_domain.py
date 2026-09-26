import unittest
from pathlib import Path
from src.domain import load_domain

FIXTURE = Path("fixtures/domain.json")

class DomainTest(unittest.TestCase):
    def setUp(self):
        self.value = load_domain(FIXTURE)

    def test_fixture_matches_domain(self):
        self.assertEqual(self.value["domain"], "emerging-sport-officiating")
        self.assertGreaterEqual(len(self.value["constraints"]), 2)

    def test_four_sports_have_independent_profiles(self):
        sports = {sport["sport_id"]: sport for sport in self.value["sports"]}
        self.assertEqual(
            set(sports),
            {"virtual-taekwondo", "padel", "surfing", "mma"},
        )
        for sport in sports.values():
            self.assertTrue(sport["equipment"])
            self.assertTrue(sport["venue"]["dimensions"])
            self.assertTrue(sport["safety_standards"])
            self.assertTrue(sport["medical_resources"])

    def test_inspections_carry_collection_time_and_photos(self):
        for inspection in self.value["inspections"]:
            self.assertTrue(inspection["collected_at"])
            self.assertTrue(inspection["photos"])

    def test_work_orders_scoped_to_single_facility(self):
        for order in self.value["work_orders"]:
            self.assertTrue(order["facility"])
            self.assertTrue(order["scope"])
            self.assertEqual(order["assignee_role"], "场馆人员")

    def test_incidents_propagate_to_schedules(self):
        for incident in self.value["incidents"]:
            self.assertTrue(incident["affects_schedules"])
            self.assertTrue(incident["propagated"])

    def test_decisions_traceable_to_standard_evidence_signer(self):
        inspection_ids = {item["inspection_id"] for item in self.value["inspections"]}
        for decision in self.value["decisions"]:
            self.assertTrue(decision["standard_version"])
            self.assertTrue(decision["signer"])
            self.assertTrue(set(decision["evidence"]) <= inspection_ids)

    def test_affected_schedules_not_cleared_to_start(self):
        affected = {
            schedule
            for incident in self.value["incidents"]
            for schedule in incident["affects_schedules"]
        }
        for decision in self.value["decisions"]:
            if decision["schedule"] in affected:
                self.assertIn(decision["action"], {"延迟", "取消"})

    def test_expired_or_failed_certificate_blocks_go(self):
        from datetime import datetime
        now = datetime.now().astimezone()
        blocked_sports = {
            cert["sport_id"]
            for cert in self.value["certifications"]
            if cert["conclusion"] == "不通过"
            or datetime.fromisoformat(cert["valid_until"]) < now
        }
        for decision in self.value["decisions"]:
            if decision["action"] == "开赛":
                sport_prefix = decision["schedule"].split("-")[0]
                self.assertNotIn(sport_prefix, blocked_sports)

if __name__ == "__main__":
    unittest.main()
