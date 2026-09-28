import base64
import tempfile
import unittest
from pathlib import Path

from app import BusinessError, ProvenanceStore


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProvenanceStore(Path(self.tmp.name) / "test.db")
        self.store.seed()

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_provenance_and_return_review_flow(self):
        source = self.store.add_source("staff", "馆藏购藏档案", "archive", "ACC-1999-7")
        obj = self.store.create_object("staff", "M-1999-7", "青铜器", "礼器", "市博物馆", "1999年入藏，来源待持续核验。")
        event = self.store.add_event("staff", obj["id"], "acquisition", "1999-07-01", "", "本市", "从私人藏家购入", source["id"], "public")
        evidence = self.store.upload_evidence("staff", obj["id"], "purchase.pdf", base64.b64encode(b"purchase record").decode(), "internal", event["id"])
        self.assertEqual(len(evidence["sha256"]), 64)
        updated = self.store.update_object("staff", obj["id"], {"public_summary": "已完成首轮来源整理。"})
        self.assertEqual(updated["version"], 3)
        claim = self.store.create_claim("claimant1", obj["id"], "王氏家族", "返还藏品")
        self.store.transition_claim("reviewer1", claim["id"], "under_review", "材料齐全，进入调查。")
        self.store.transition_claim("reviewer1", claim["id"], "negotiating", "双方开始协商返还安排。")
        self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "签署返还协议。")
        public_view = self.store.get_object("public", obj["id"])
        self.assertNotIn("current_holder", public_view)
        self.assertEqual(len(public_view["events"]), 1)
        self.assertEqual(public_view["claims"][0]["status"], "resolved_return")
        claimant_view = self.store.get_object("claimant1", obj["id"])
        self.assertEqual(len(claimant_view["claims"]), 1)
        self.assertGreaterEqual(len(self.store.object_history("reviewer1", obj["id"])), 6)

    def test_visibility_and_claim_stage_invariants(self):
        obj = self.store.create_object("staff", "M-2001-2", "手稿", "纸质", "资料室", "公开简介。")
        claim = self.store.create_claim("claimant1", obj["id"], "捐赠人后代", "归还手稿")
        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "直接结束。")
        self.assertEqual(ctx.exception.code, "invalid_transition")
        self.assertNotIn("claimant_id", self.store.get_object("public", obj["id"])["claims"][0])
        with self.assertRaises(BusinessError) as ctx:
            self.store.add_event("public", obj["id"], "note", "2020-01-01", "", "馆内", "未授权事件", None, "public")
        self.assertEqual(ctx.exception.status, 403)

    def test_provenance_gate_can_be_completed_and_locks_returned_object(self):
        source = self.store.add_source("staff", "家族移交记录", "archive", "FAM-1974-3")
        obj = self.store.create_object("staff", "M-1974-3", "信函", "纸质", "市档案馆", "来源链待补。")
        event = self.store.add_event("staff", obj["id"], "transfer", "1974-05-01", "", "本市", "家族移交至馆方", None, "public")
        claim = self.store.create_claim("claimant1", obj["id"], "李氏后人", "返还原物")
        self.store.transition_claim("reviewer1", claim["id"], "under_review", "受理主张并核验来源。")

        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "negotiating", "拟进入协商。")
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.code, "provenance_check_failed")
        missing_types = {item["type"] for item in ctx.exception.details["missing_items"]}
        self.assertEqual(missing_types, {"source_reference", "evidence"})
        self.assertEqual(ctx.exception.details["missing_items"][0]["events"][0]["event_id"], event["id"])

        self.store.attach_event_source("staff", obj["id"], event["id"], source["id"])
        self.store.upload_evidence(
            "staff", obj["id"], "family-letter.pdf",
            base64.b64encode(b"family transfer record").decode(),
            "internal", event["id"],
        )
        self.store.transition_claim("reviewer1", claim["id"], "negotiating", "来源链补齐，进入协商。")
        self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "核验通过，确认返还。")

        internal_view = self.store.get_object("reviewer1", obj["id"])
        self.assertTrue(internal_view["provenance_check"]["passed"])
        self.assertEqual(internal_view["provenance_check"]["evidence_count"], 1)
        public_view = self.store.get_object("public", obj["id"])
        claimant_view = self.store.get_object("claimant1", obj["id"])
        self.assertTrue(public_view["provenance_verified"])
        self.assertTrue(claimant_view["provenance_verified"])
        self.assertNotIn("provenance_check", public_view)
        self.assertNotIn("provenance_check", claimant_view)
        self.assertNotIn("current_holder", claimant_view)
        self.assertNotIn("unlinked_evidence", claimant_view)
        self.assertNotIn("reviews", claimant_view["claims"][0])
        self.assertNotIn("source", claimant_view["events"][0])

        locked_calls = [
            lambda: self.store.update_object("staff", obj["id"], {"public_summary": "尝试修改"}),
            lambda: self.store.add_event("staff", obj["id"], "note", "2026-01-01", "", "馆内", "返还后新增流转", source["id"], "internal"),
            lambda: self.store.attach_event_source("staff", obj["id"], event["id"], source["id"]),
            lambda: self.store.upload_evidence("staff", obj["id"], "after.pdf", base64.b64encode(b"x").decode(), "internal"),
            lambda: self.store.create_claim("claimant1", obj["id"], "其他后人", "再次主张"),
        ]
        for call in locked_calls:
            with self.assertRaises(BusinessError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "return_locked")

        history = self.store.object_history("reviewer1", obj["id"])
        final_snapshot = self.store.history_detail("reviewer1", obj["id"], history[-1]["version"])
        self.assertEqual(final_snapshot["snapshot"]["claims"][0]["status"], "resolved_return")
        self.assertEqual(len(final_snapshot["snapshot"]["evidence"]), 1)


if __name__ == "__main__":
    unittest.main()
