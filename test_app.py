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

    def test_reviewer_cannot_advance_when_provenance_has_gaps(self):
        source = self.store.add_source("staff", "捐赠档案", "archive", "DON-2002-3")
        obj = self.store.create_object("staff", "M-2002-3", "瓷瓶", "瓷器", "市博物馆", "来源链待核验。")
        event = self.store.add_event("staff", obj["id"], "transfer", "2002-05-01", "", "本市", "家族捐赠", None, "internal")
        claim = self.store.create_claim("claimant1", obj["id"], "李氏家族", "返还瓷瓶")
        self.store.transition_claim("reviewer1", claim["id"], "under_review", "进入审查。")

        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "negotiating", "申请进入协商。")
        self.assertEqual(ctx.exception.code, "provenance_incomplete")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.details["missing_transfers"][0]["id"], event["id"])
        self.assertTrue(ctx.exception.details["missing_evidence"])

        self.store.attach_event_source("staff", obj["id"], event["id"], source["id"])
        evidence = self.store.upload_evidence(
            "staff", obj["id"], "donation.pdf", base64.b64encode(b"donation record").decode(), "internal", event["id"]
        )
        self.assertEqual(len(evidence["sha256"]), 64)
        result = self.store.transition_claim("reviewer1", claim["id"], "negotiating", "补齐来源和证据，开始协商。")
        self.assertEqual(result["status"], "negotiating")
        self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "核验通过，确认返还。")

    def test_return_locks_history_provenance_and_claims(self):
        source = self.store.add_source("staff", "调拨档案", "archive", "TR-2003-9")
        obj = self.store.create_object("staff", "M-2003-9", "漆器", "木器", "市博物馆", "来源完整。")
        event = self.store.add_event("staff", obj["id"], "transfer", "2003-09-01", "", "本市", "调拨入藏", source["id"], "internal")
        self.store.upload_evidence("staff", obj["id"], "transfer.pdf", base64.b64encode(b"transfer").decode(), "internal", event["id"])
        claim = self.store.create_claim("claimant1", obj["id"], "赵氏家族", "返还漆器")
        self.store.transition_claim("reviewer1", claim["id"], "under_review", "进入审查。")
        self.store.transition_claim("reviewer1", claim["id"], "negotiating", "进入协商。")
        self.store.transition_claim("reviewer1", claim["id"], "resolved_return", "完成返还。")

        for call, user_id, args in [
            (self.store.update_object, "staff", (obj["id"], {"public_summary": "试图修改"})),
            (self.store.add_event, "staff", (obj["id"], "note", "2024-01-01", "", "馆内", "试图新增流转", None, "internal")),
            (self.store.attach_event_source, "staff", (obj["id"], event["id"], source["id"])),
            (self.store.upload_evidence, "staff", (obj["id"], "new.pdf", base64.b64encode(b"new").decode(), "internal", event["id"])),
            (self.store.create_claim, "claimant1", (obj["id"], "新主张人", "再次主张")),
        ]:
            with self.assertRaises(BusinessError) as ctx:
                call(user_id, *args)
            self.assertEqual(ctx.exception.code, "return_locked")

        with self.assertRaises(BusinessError) as ctx:
            self.store.transition_claim("reviewer1", claim["id"], "rejected", "试图重新处理。")
        self.assertEqual(ctx.exception.code, "invalid_transition")

    def test_external_roles_see_only_verification_result(self):
        source = self.store.add_source("staff", "旧藏档案", "archive", "OLD-2004-1")
        obj = self.store.create_object("staff", "M-2004-1", "画卷", "书画", "资料室", "公开说明。")
        event = self.store.add_event("staff", obj["id"], "transfer", "2004-03-01", "", "本市", "旧藏流转", source["id"], "public")
        self.store.upload_evidence("staff", obj["id"], "provenance.pdf", base64.b64encode(b"internal evidence").decode(), "internal", event["id"])
        claim = self.store.create_claim("claimant1", obj["id"], "孙氏家族", "返还画卷")

        internal = self.store.get_object("reviewer1", obj["id"])
        self.assertTrue(internal["provenance_verification"]["passed"])
        self.assertIn("missing_transfers", internal["provenance_verification"])

        claimant = self.store.get_object("claimant1", obj["id"])
        self.assertEqual(set(claimant["provenance_verification"]), {"passed"})
        self.assertNotIn("source", claimant["events"][0])
        self.assertNotIn("evidence", claimant["events"][0])
        self.assertNotIn("reviews", claimant["claims"][0])
        self.assertNotIn("claimant_id", claimant["claims"][0])

        public = self.store.get_object("public", obj["id"])
        self.assertEqual(set(public["provenance_verification"]), {"passed"})
        self.assertEqual(public["claims"][0]["id"], claim["id"])
        self.assertNotIn("claimant_id", public["claims"][0])
        self.assertNotIn("current_holder", public)


if __name__ == "__main__":
    unittest.main()
