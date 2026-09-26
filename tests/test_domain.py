import copy
import unittest
from datetime import datetime
from pathlib import Path

from src.domain import (
    decision_trace,
    gap_report,
    load_domain,
    official_view,
    session_readiness,
    staff_work_orders,
    validate,
)

FIXTURE = Path("fixtures/domain.json")
AS_OF = datetime.fromisoformat("2026-09-26T09:20:00+08:00")


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.data = load_domain(FIXTURE)

    def assertInvalid(self, data, needle):
        errors = validate(data)
        self.assertTrue(
            any(needle in e for e in errors),
            f"期望校验错误包含 {needle!r}，实际错误：{errors}",
        )

    def mutate(self, table, key, key_value, **changes):
        data = copy.deepcopy(self.data)
        for row in data[table]:
            if row[key] == key_value:
                row.update(changes)
        return data

    def test_fixture_is_valid_and_complete(self):
        self.assertEqual(validate(self.data), [])
        self.assertEqual(self.data["domain"], "emerging-sport-officiating")
        self.assertGreaterEqual(self.data["version"], 2)

    def test_each_discipline_has_own_standard_and_checklist(self):
        # 四个项目各有独立的生效标准与验收清单，不存在通用表
        by_disc = {"virtual_taekwondo": set(), "padel": set(), "surfing": set(), "mma": set()}
        for cl in self.data["checklists"]:
            by_disc[cl["discipline"]].add(cl["id"])
        for disc, items in by_disc.items():
            self.assertTrue(items, f"{disc} 缺少独立验收清单")
        effective = set()
        for std in self.data["standards"]:
            if std["effective_to"] is None:
                effective.add(std["discipline"])
        self.assertEqual(effective, set(by_disc))

    def test_equipment_models_and_calibration_records_present(self):
        models = {e["model"] for e in self.data["equipment"]}
        self.assertIn("Varis XR-Pro v2", models)
        self.assertIn("Varis Tracker G3", models)
        drift = [
            i for i in self.data["inspections"]
            if i["item_id"] == "cl-vt-trk"
        ]
        self.assertTrue(any(i["verdict"] == "fail" for i in drift))
        # 漂移检查都有带采集时间的照片与读数
        for ins in drift:
            self.assertIn("mm", {r["unit"] for r in ins["readings"]})
            self.assertTrue(ins["photos"])
            self.assertTrue(ins["photos"][0]["captured_at"])

    def test_inspection_and_photo_timestamps_required(self):
        data = self.mutate("inspections", "id", "ins-pa-c1-s")
        data["inspections"] = [
            i for i in data["inspections"] if i["id"] != "ins-pa-c1-s"
        ]
        re_added = next(i for i in self.data["inspections"] if i["id"] == "ins-pa-c1-s")
        re_added = copy.deepcopy(re_added)
        re_added["photos"][0]["captured_at"] = "2026-09-26T10:00:00+08:00"
        data["inspections"].append(re_added)
        self.assertInvalid(data, "采集时间与检查时间不一致")

    def test_verdict_must_match_readings(self):
        data = self.mutate("inspections", "id", "ins-hmd1", verdict="fail")
        self.assertInvalid(data, "ins-hmd1")

    # ---- 工单作用域隔离 ----

    def test_work_order_scope_limited_to_one_facility(self):
        # 南面玻璃的换件复检若挂到其它面板，构成越界传播
        data = self.mutate("work_orders", "id", "wo-glass-s", status="closed")
        wo = next(w for w in data["work_orders"] if w["id"] == "wo-glass-s")
        wo["resolution"] = {"inspection_id": "ins-pa-c1-n", "permanent": True}
        self.assertInvalid(data, "不属于其作用设施")

    def test_temporary_repair_cannot_release(self):
        data = self.mutate("work_orders", "id", "wo-trk02", status="closed",
                           temporary_repair=True)
        wo = next(w for w in data["work_orders"] if w["id"] == "wo-trk02")
        wo["resolution"] = {"inspection_id": "ins-trk01", "permanent": False}
        self.assertInvalid(data, "临时修复")

    def test_staff_assignee_scope(self):
        # v-vta 的技师不能处理 v-pad 的工单
        data = self.mutate("work_orders", "id", "wo-glass-s", assignee="st-vt")
        self.assertInvalid(data, "场馆作用域")

    def test_staff_sees_only_own_orders(self):
        self.assertEqual(
            {w["id"] for w in staff_work_orders(self.data, "st-pa")},
            {"wo-glass-s", "wo-loan"},
        )
        self.assertEqual(
            {w["id"] for w in staff_work_orders(self.data, "st-vt")},
            {"wo-trk02"},
        )
        with self.assertRaises(PermissionError):
            staff_work_orders(self.data, "td-pa")

    # ---- 证书效力 ----

    def test_superseded_certificate_cannot_release(self):
        data = self.mutate("decisions", "id", "dec-pa-02", certificate_id="cert-vt-1")
        self.assertInvalid(data, "dec-pa-02")

    def test_suspended_certificate_blocks_court_one_only(self):
        # 一号场证书暂停不影响二号场；一号场无可用证书
        c1 = session_readiness(self.data, "sess-pa-01", AS_OF)
        c2 = session_readiness(self.data, "sess-pa-02", AS_OF)
        self.assertFalse(any(c["usable"] for c in c1["certificates"]))
        self.assertTrue(any(c["usable"] for c in c2["certificates"]))
        self.assertTrue(c2["ready"])

    def test_certificate_scope_must_cover_session_facility(self):
        # cert-pa-2 只覆盖二号场，不能用于一号场的开赛
        cert = next(c for c in self.data["certificates"] if c["id"] == "cert-pa-1")
        self.assertEqual(cert["status"], "suspended")
        c1 = session_readiness(self.data, "sess-pa-01", AS_OF)
        self.assertFalse(c1["ready"])

    # ---- 事件传播 ----

    def test_glass_damage_propagates_to_all_court_one_sessions(self):
        evt = next(e for e in self.data["events"] if e["id"] == "evt-glass")
        self.assertIn("sess-pa-01", evt["affected_sessions"])
        self.assertIn("sess-pa-03", evt["affected_sessions"])
        # 同馆另一块场地不受影响
        self.assertNotIn("sess-pa-02", evt["affected_sessions"])

    def test_missing_propagation_is_invalid(self):
        data = self.mutate("events", "id", "evt-glass")
        evt = next(e for e in data["events"] if e["id"] == "evt-glass")
        evt["affected_sessions"].remove("sess-pa-03")
        self.assertInvalid(data, "未传播到受影响赛程 sess-pa-03")

    def test_cross_venue_loan_propagates_to_both_venues(self):
        evt = next(e for e in self.data["events"] if e["id"] == "evt-loan")
        self.assertEqual(set(evt["affected_sessions"]),
                         {"sess-pa-demo", "sess-vt-02"})

    def test_sensor_drift_blocks_affected_bouts(self):
        evt = next(e for e in self.data["events"] if e["id"] == "evt-drift")
        self.assertEqual(set(evt["affected_sessions"]),
                         {"sess-vt-01", "sess-vt-02"})

    def test_mma_medic_absence_blocks_mma_sessions(self):
        evt = next(e for e in self.data["events"] if e["id"] == "evt-med")
        self.assertEqual(set(evt["affected_sessions"]),
                         {"sess-mma-01", "sess-mma-02"})
        for sid in ("sess-mma-01", "sess-mma-02"):
            row = session_readiness(self.data, sid, AS_OF)
            self.assertIn("evt-med", row["blocking_events"])

    def test_sea_state_change_short_cadence_gap(self):
        # 涌浪周期最近复测为 07:10，60 分钟周期，09:20 判定为过期
        row = session_readiness(self.data, "sess-surf-01", AS_OF)
        stale = {g["target_ref"]: g for g in row["gaps"] if g["checklist_id"] == "cl-surf-period"}
        self.assertEqual(stale["@discipline"]["status"], "stale")
        fails = {g["checklist_id"] for g in row["gaps"] if g["status"] == "fail"}
        self.assertIn("cl-surf-wave", fails)
        self.assertIn("cl-surf-wind", fails)

    def test_closed_event_without_permanent_repair_invalid(self):
        data = self.mutate("events", "id", "evt-mat-hist")
        evt = next(e for e in data["events"] if e["id"] == "evt-mat-hist")
        evt["resolution"]["permanent"] = False
        self.assertInvalid(data, "临时修复")

    # ---- 决定与证据 ----

    def test_go_decision_requires_clear_session(self):
        # 给仍有开放事件的场次发开赛决定必须失败
        data = self.mutate("decisions", "id", "dec-vt-01", outcome="go")
        self.assertInvalid(data, "dec-vt-01")

    def test_decision_standard_must_be_effective_at_signing(self):
        data = self.mutate("decisions", "id", "dec-vt-01", standard_id="std-vt-r2")
        self.assertInvalid(data, "尚未生效")

    def test_decision_evidence_must_precede_signing(self):
        data = self.mutate("decisions", "id", "dec-pa-02",
                           evidence_inspection_ids=["ins-loan-pad"])
        self.assertInvalid(data, "晚于签署时间")

    def test_only_technical_delegate_may_sign(self):
        data = self.mutate("decisions", "id", "dec-pa-02", signed_by="st-pa")
        self.assertInvalid(data, "技术官员")

    def test_decision_trace_reconstructs_context(self):
        trace = decision_trace(self.data, "dec-pa-02")
        self.assertEqual(trace["outcome"], "go")
        self.assertEqual(trace["standard"]["id"], "std-pa-r2")
        self.assertTrue(trace["certificate_was_usable"])
        self.assertEqual(trace["signed_by"]["id"], "td-pa")
        self.assertEqual(len(trace["evidence"]), 8)
        delayed = decision_trace(self.data, "dec-surf-01")
        self.assertEqual(delayed["outcome"], "delayed")
        self.assertIn("evt-sea", [e["id"] for e in delayed["events"]])
        self.assertIn("12:00复测窗口", delayed["alternative"])

    # ---- 技术官员视图 ----

    def test_official_view_scoped_by_venue(self):
        mine = official_view(self.data, "td-pa")
        self.assertEqual(
            {s["session_id"] for s in mine["sessions"]},
            {"sess-pa-01", "sess-pa-02", "sess-pa-03", "sess-pa-demo"},
        )
        head = official_view(self.data, "td01")
        self.assertEqual(len(head["sessions"]), len(self.data["schedule"]))
        with self.assertRaises(PermissionError):
            official_view(self.data, "st-pa")

    def test_gap_report_carries_alternatives(self):
        row = next(r for r in gap_report(self.data) if r["session_id"] == "sess-pa-01")
        glass_gap = next(g for g in row["gaps"] if g["target_ref"] == "fac-pa-court1#pnl-s")
        self.assertTrue(glass_gap["alternatives"])
        self.assertTrue(any("court2" in a or "fac-pa-court2" in a for a in glass_gap["alternatives"]))

    def test_overall_report_counts(self):
        report = gap_report(self.data)
        ready = {r["session_id"] for r in report if r["ready"]}
        self.assertEqual(ready, {"sess-pa-02"})


if __name__ == "__main__":
    unittest.main()
