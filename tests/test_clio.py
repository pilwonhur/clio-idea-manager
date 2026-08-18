#!/usr/bin/env python3
"""PRD.md §13 테스트 및 성공 기준 — TC1 ~ TC20.

각 테스트는 격리된 CLIO_DATA_DIR에서 실제 스크립트를 실행한다.
    python3 tests/test_clio.py
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import clio_core  # noqa: E402


class ClioTest(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="clio-test-"))
        self.mirror = Path(tempfile.mkdtemp(prefix="clio-mirror-"))
        (self.data / "config.json").write_text(
            json.dumps({"backup_mirror": str(self.mirror)}), encoding="utf-8")
        self.env = dict(os.environ, CLIO_DATA_DIR=str(self.data))

    def tearDown(self):
        shutil.rmtree(self.data, ignore_errors=True)
        shutil.rmtree(self.mirror, ignore_errors=True)

    # -- 헬퍼 ------------------------------------------------------------
    def sh(self, script, *args, expect_ok=True, env=None):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / script), *[str(a) for a in args]],
            capture_output=True, text=True, env=env or self.env)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"{script} {args} 가 JSON을 내지 않았습니다:\n"
                      f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}")
        if expect_ok:
            self.assertTrue(payload.get("ok"), f"실패: {payload}")
            self.assertEqual(proc.returncode, 0)
        else:
            self.assertFalse(payload.get("ok"), f"실패해야 하는데 성공: {payload}")
            self.assertEqual(proc.returncode, 1)
        return payload

    def db(self):
        conn = sqlite3.connect(self.data / "ideas.db")
        conn.row_factory = sqlite3.Row
        return conn

    def capture(self, body, **kw):
        args = ["capture", "--body", body]
        for k, v in kw.items():
            args += [f"--{k.replace('_', '-')}", str(v)]
        return self.sh("idea_store", *args)

    def make_idea(self, body, title, **kw):
        e = self.capture(body, **kw)
        return self.sh("idea_store", "new-idea", "--entry-id", e["entry_id"],
                        "--title", title), e

    # -- TC1 --------------------------------------------------------------
    def test_TC01_duplicate_message_stored_once(self):
        a = self.capture("같은 생각", message_id="m1")
        b = self.capture("같은 생각", message_id="m1")
        self.assertFalse(a["duplicate"])
        self.assertTrue(b["duplicate"])
        self.assertEqual(a["entry_id"], b["entry_id"])
        self.assertEqual(self.db().execute("SELECT COUNT(*) c FROM raw_entry").fetchone()["c"], 1)

    # -- TC2 --------------------------------------------------------------
    def test_TC02_no_partial_save_on_failure(self):
        """트랜잭션 도중 예외가 나면 전부 되돌아간다."""
        os.environ["CLIO_DATA_DIR"] = str(self.data)
        conn = clio_core.connect()
        before = conn.execute("SELECT COUNT(*) c FROM idea").fetchone()["c"]
        with self.assertRaises(RuntimeError):
            with clio_core.tx(conn):
                conn.execute(
                    "INSERT INTO idea(ref,title,created_at,updated_at) VALUES('X-1','t','a','b')")
                raise RuntimeError("중단 시뮬레이션")
        after = conn.execute("SELECT COUNT(*) c FROM idea").fetchone()["c"]
        self.assertEqual(before, after)

    def test_TC02b_failed_assign_leaves_entry_intact(self):
        e = self.capture("보존되어야 할 원문", message_id="m2")
        self.sh("idea_store", "assign", "--entry-id", e["entry_id"],
                 "--idea-ref", "CLIO-9999", expect_ok=False)
        row = self.db().execute("SELECT * FROM raw_entry WHERE id=?", (e["entry_id"],)).fetchone()
        self.assertEqual(row["assign_state"], "unassigned")
        self.assertEqual(row["body"], "보존되어야 할 원문")

    # -- TC3 --------------------------------------------------------------
    def test_TC03_adding_entry_does_not_change_history(self):
        idea, e1 = self.make_idea("첫 번째 원문", "기억과 망각", message_id="m3")
        self.sh("idea_store", "revise", "--idea-ref", idea["ref"],
                 "--body", "정제본 1", "--summary", "최초 정제")
        before = self.sh("idea_search", "show", "--idea-ref", idea["ref"])
        e2 = self.capture("두 번째 원문", message_id="m4")
        self.sh("idea_store", "assign", "--entry-id", e2["entry_id"], "--idea-ref", idea["ref"])
        after = self.sh("idea_search", "show", "--idea-ref", idea["ref"])
        self.assertEqual(before["raw_entries"][0], after["raw_entries"][0])
        self.assertEqual(before["latest_revision"], after["latest_revision"])
        self.assertEqual(len(after["raw_entries"]), 2)

    # -- TC4 --------------------------------------------------------------
    def test_TC04_merge_keeps_parents(self):
        a, _ = self.make_idea("첫째 생각", "A", message_id="p1")
        b, _ = self.make_idea("둘째 생각", "B", message_id="p2")
        c, _ = self.make_idea("셋째 생각", "C", message_id="p3")
        m = self.sh("idea_store", "merge", "--parents",
                     f"{a['ref']},{b['ref']},{c['ref']}", "--title", "합성 글감")
        for ref in (a["ref"], b["ref"], c["ref"]):
            self.sh("idea_search", "show", "--idea-ref", ref)
        rel = self.sh("idea_search", "relations", "--idea-ref", m["ref"])
        self.assertEqual(len(rel["relations"]["parents"]), 3)
        self.assertNotIn(m["ref"], (a["ref"], b["ref"], c["ref"]))

    # -- TC5 --------------------------------------------------------------
    def test_TC05_reassign_leaves_audit_trail(self):
        a, e = self.make_idea("잘못 연결될 원문", "원래 글감", message_id="r1")
        b, _ = self.make_idea("다른 주제", "옮길 글감", message_id="r2")
        self.sh("idea_store", "reassign", "--entry-id", e["entry_id"], "--to-idea-ref", b["ref"])
        row = self.db().execute("SELECT idea_id FROM raw_entry WHERE id=?",
                                (e["entry_id"],)).fetchone()
        target = self.db().execute("SELECT id FROM idea WHERE ref=?", (b["ref"],)).fetchone()
        self.assertEqual(row["idea_id"], target["id"])
        log = self.db().execute(
            "SELECT * FROM action_log WHERE action='reassign'").fetchone()
        self.assertIsNotNone(log)
        self.assertEqual(json.loads(log["payload"])["to"], b["ref"])

    # -- TC6 --------------------------------------------------------------
    def test_TC06_archived_hidden_from_list_but_searchable(self):
        idea, _ = self.make_idea("보관될 글감의 원문 기억 실험", "보관 대상", message_id="a1")
        self.sh("idea_store", "set-state", "--idea-ref", idea["ref"], "--storage", "archived")
        listed = self.sh("idea_search", "list")
        self.assertNotIn(idea["ref"], [i["ref"] for i in listed["ideas"]])
        found = self.sh("idea_search", "query", "--q", "보관될 글감 기억")
        self.assertIn(idea["ref"], [r["ref"] for r in found["results"]])

    # -- TC7 --------------------------------------------------------------
    def test_TC07_backup_restore_counts_match(self):
        idea, _ = self.make_idea("백업 대상 원문", "백업 시험", message_id="b1")
        self.sh("idea_store", "revise", "--idea-ref", idea["ref"],
                 "--body", "정제본", "--summary", "요약")
        other, _ = self.make_idea("둘째 원문", "둘째", message_id="b2")
        self.sh("idea_store", "relate", "--from", idea["ref"], "--to", other["ref"],
                 "--type", "similar")
        bk = self.sh("idea_check", "backup")
        res = self.sh("idea_check", "restore-test", "--source", bk["path"])
        self.assertTrue(res["match"], res["diff"])
        self.assertTrue(res["restored_healthy"])
        for t in ("raw_entry", "revision", "idea_relation"):
            self.assertEqual(bk["counts"][t], res["restored_counts"][t])

    # -- TC8 --------------------------------------------------------------
    def test_TC08_korean_partial_search(self):
        self.make_idea("AI 비서는 모든 것을 기억하는 것보다 무엇을 잊어야 하는지가 더 중요하다",
                       "기록보다 중요한 망각", message_id="k1")
        for i, (body, title) in enumerate([
                ("연구실 장비 예산을 다시 계산해야 한다", "예산 계획"),
                ("학생 논문 지도는 질문의 순서가 중요하다", "지도 방법"),
                ("로봇 제어에서 지연은 안정성을 해친다", "제어 지연")]):
            self.make_idea(body, title, message_id=f"k{i + 2}")
        for query in ["망각", "기억", "AI 비서의 기억", "무엇을 잊어야 하는지"]:
            res = self.sh("idea_search", "query", "--q", query)
            refs = [r["ref"] for r in res["results"][:5]]
            self.assertIn("CLIO-0001", refs, f"질의 '{query}' 실패: {refs}")

    # -- TC9 --------------------------------------------------------------
    def test_TC09_storage_failure_is_not_reported_as_success(self):
        blocked = dict(os.environ, CLIO_DATA_DIR="/dev/null/clio")
        out = self.sh("idea_store", "capture", "--body", "저장될 수 없는 원문",
                       expect_ok=False, env=blocked)
        self.assertIn(out["error"]["code"], ("storage_unavailable", "db_error"))
        self.assertNotIn("entry_id", out)

    def test_install_then_update_keeps_data_and_user_config(self):
        """업데이트는 지우고 다시 깔지 않는다 — 데이터·설정은 남고 낡은 파일만 사라진다."""
        skill = Path(tempfile.mkdtemp(prefix="clio-skill-"))
        data = Path(tempfile.mkdtemp(prefix="clio-instdata-"))
        self.addCleanup(shutil.rmtree, skill, ignore_errors=True)
        self.addCleanup(shutil.rmtree, str(skill) + ".prev", ignore_errors=True)
        self.addCleanup(shutil.rmtree, data, ignore_errors=True)

        env = dict(os.environ, CLIO_SKILL_DIR=str(skill), CLIO_DATA_DIR=str(data))

        def install():
            out = subprocess.run(["bash", str(ROOT / "install.sh")],
                                 capture_output=True, text=True, env=env, cwd=str(ROOT))
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            return out.stdout

        first = install()
        self.assertIn("설치 완료", first)
        self.assertTrue((skill / "scripts" / "idea_store").exists())
        self.assertTrue((data / "ideas.db").exists())

        # 사용자가 쌓은 데이터와 손댄 설정
        entry = self.sh("idea_store", "capture", "--body", "업데이트를 견뎌야 하는 원문",
                         "--message-id", "keep1", env=env)
        cfg_path = data / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["ask_threshold"] = 0.42
        del cfg["mention_threshold"]          # 새 설정 키가 추가되는 상황을 흉내낸다
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

        stale = skill / "scripts" / "지워져야_하는_파일.py"
        stale.write_text("# 이전 버전의 잔재\n", encoding="utf-8")

        second = install()
        self.assertIn("업데이트 완료", second)

        # 데이터는 그대로
        self.assertTrue((data / "ideas.db").exists())
        rows = self.sh("idea_search", "unassigned", env=env)["entries"]
        self.assertIn(entry["entry_id"], [r["entry_id"] for r in rows])

        # 사용자가 고친 값은 유지, 빠진 키만 기본값으로 채움
        merged = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(merged["ask_threshold"], 0.42)
        self.assertEqual(merged["mention_threshold"],
                         clio_core.DEFAULT_CONFIG["mention_threshold"])

        # 낡은 파일은 사라지고, 되돌릴 수 있게 직전 스킬이 남는다
        self.assertFalse(stale.exists())
        self.assertTrue(Path(str(skill) + ".prev", "scripts", "idea_store").exists())

    def test_version_is_reported_and_matches_changelog(self):
        """버전은 CLIO_VERSION이 단일 원본이고, CHANGELOG 맨 위 항목과 일치해야 한다."""
        import re
        version = clio_core.CLIO_VERSION
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")

        for tool in ("idea_store", "idea_search", "idea_export", "idea_check"):
            out = subprocess.run([sys.executable, str(SCRIPTS / tool), "--version"],
                                 capture_output=True, text=True, env=self.env)
            self.assertEqual(out.returncode, 0, tool)
            self.assertIn(version, out.stdout + out.stderr, tool)

        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        latest = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M)
        self.assertIsNotNone(latest, "CHANGELOG.md에 버전 항목이 없습니다")
        self.assertEqual(latest.group(1), version,
                         "CLIO_VERSION과 CHANGELOG 최신 항목이 어긋납니다")

    def test_detail_output_shows_capture_time_to_the_second(self):
        """상세 출력은 입력 시각을 초 단위까지 보여주고, 기록 시각과 다르면 함께 낸다."""
        idea, e = self.make_idea("아침에 떠오른 문장", "시각 표시",
                                  message_id="ts1", captured_at="2026-08-17T09:12:04+09:00")
        text = self.sh("idea_search", "show", "--idea-ref", idea["ref"],
                        "--format", "text")["text"]
        self.assertIn("2026-08-17 09:12:04", text)
        self.assertIn("(기록 ", text)          # captured_at != ingested_at

        detail = self.sh("idea_search", "show", "--idea-ref", idea["ref"])
        entry = detail["raw_entries"][0]
        self.assertEqual(entry["captured_at"], "2026-08-17T09:12:04+09:00")
        self.assertNotEqual(entry["captured_at"][:19], entry["ingested_at"][:19])

        # 브리지가 원 시각을 주지 않으면 괄호 없이 한 시각만 나온다
        e2 = self.capture("바로 지금 보낸 문장", message_id="ts2")
        self.sh("idea_store", "assign", "--entry-id", e2["entry_id"],
                 "--idea-ref", idea["ref"])
        text2 = self.sh("idea_search", "show", "--idea-ref", idea["ref"],
                         "--format", "text")["text"]
        self.assertEqual(text2.count("(기록 "), 1)

    # -- TC10 -------------------------------------------------------------
    def test_TC10_output_separates_user_text_from_clio(self):
        idea, _ = self.make_idea("교수님의 원문 그대로", "구분 시험", message_id="s1")
        self.sh("idea_store", "revise", "--idea-ref", idea["ref"],
                 "--body", "Clio가 다듬은 정제본", "--summary", "정리")
        text = self.sh("idea_search", "show", "--idea-ref", idea["ref"],
                        "--format", "text")["text"]
        self.assertIn("← 교수님 원문, 무수정", text)
        self.assertIn("← Clio 작성", text)
        self.assertLess(text.index("교수님의 원문 그대로"), text.index("Clio가 다듬은 정제본"))

    # -- TC11 -------------------------------------------------------------
    def test_TC11_unanswered_stays_unassigned(self):
        e = self.capture("분류되지 않을 원문", message_id="u1")
        self.assertEqual(e["assign_state"], "unassigned")
        un = self.sh("idea_search", "unassigned")
        self.assertIn(e["entry_id"], [x["entry_id"] for x in un["entries"]])

    # -- TC12 -------------------------------------------------------------
    def test_TC12_redaction_and_no_ref_reuse(self):
        idea, e = self.make_idea("민감한 내용이 섞인 원문", "삭제 대상", message_id="d1")
        self.sh("idea_store", "redact", "--entry-id", e["entry_id"],
                 "--reason", "개인정보", expect_ok=False)          # --confirm 없이는 거부
        self.sh("idea_store", "redact", "--entry-id", e["entry_id"],
                 "--reason", "개인정보", "--confirm")
        row = self.db().execute("SELECT * FROM raw_entry WHERE id=?", (e["entry_id"],)).fetchone()
        self.assertEqual(row["body"], "")
        self.assertIsNotNone(row["redacted_at"])
        self.sh("idea_store", "delete-idea", "--idea-ref", idea["ref"],
                 "--reason", "정리", "--confirm")
        nxt, _ = self.make_idea("다음 글감", "다음", message_id="d2")
        self.assertNotEqual(nxt["ref"], idea["ref"])
        self.assertEqual(nxt["ref"], "CLIO-0002")

    # -- TC13 / TC14 / TC16 : 저장 지시어 (§6.1) --------------------------
    def test_TC13_no_trigger_no_write(self):
        self.capture("초기 글감", message_id="t0")     # DB 생성
        before = self.db().execute("SELECT COUNT(*) c FROM raw_entry").fetchone()["c"]
        out = self.sh("idea_store", "capture", "--raw-message",
                       "이번 주 논문 심사 일정 정리해줘", expect_ok=False)
        self.assertEqual(out["error"]["code"], "no_trigger")
        after = self.db().execute("SELECT COUNT(*) c FROM raw_entry").fetchone()["c"]
        self.assertEqual(before, after)

    def test_TC14_multiline_body_preserved_without_token(self):
        msg = "글감: 첫 줄\n\n  들여쓴 셋째 줄\n마지막 줄"
        out = self.sh("idea_store", "capture", "--raw-message", msg, "--message-id", "t1")
        body = self.db().execute("SELECT body FROM raw_entry WHERE id=?",
                                 (out["entry_id"],)).fetchone()["body"]
        self.assertEqual(body, "첫 줄\n\n  들여쓴 셋째 줄\n마지막 줄")
        self.assertNotIn("글감:", body)

    def test_TC16_mid_sentence_trigger_not_recognized(self):
        out = self.sh("idea_store", "capture", "--raw-message",
                       "이 논문은 글감: 이라는 표현을 쓴다", expect_ok=False)
        self.assertEqual(out["error"]["code"], "no_trigger")

    def test_TC14b_trigger_variants(self):
        cases = {"글감: 본문": ("new", None),
                 "아이디어: 본문": ("new", None),
                 "ㄱ: 본문": ("new", None),
                 "글감 CLIO-0007: 본문": ("target", "CLIO-0007"),
                 "글감+: 본문": ("supplement", None)}
        for msg, (kind, ref) in cases.items():
            out = self.sh("idea_store", "parse", "--message", msg)
            self.assertTrue(out["trigger"], msg)
            self.assertEqual(out["kind"], kind, msg)
            self.assertEqual(out["idea_ref"], ref, msg)

    # -- TC15 -------------------------------------------------------------
    def test_TC15_failure_response_has_no_idea_ref(self):
        out = self.sh("idea_store", "capture", "--raw-message", "글감:   ", expect_ok=False)
        self.assertEqual(out["error"]["code"], "empty_body")
        self.assertNotIn("CLIO-", json.dumps(out, ensure_ascii=False))

    # -- TC17 -------------------------------------------------------------
    def test_TC17_attachment_copied_and_hashed(self):
        src = self.data / "sample.txt"
        src.write_text("첨부 내용", encoding="utf-8")
        out = self.sh("idea_store", "capture", "--body", "첨부가 있는 원문",
                       "--message-id", "at1", "--attach", str(src))
        self.assertEqual(len(out["attachments"]), 1)
        stored = self.data / out["attachments"][0]["stored_path"]
        self.assertTrue(stored.exists())
        self.assertEqual(clio_core.sha256_file(stored), out["attachments"][0]["sha256"])
        self.assertTrue(self.sh("idea_check", "integrity")["healthy"])

    # -- TC18 -------------------------------------------------------------
    def test_TC18_adding_entry_creates_no_revision(self):
        idea, _ = self.make_idea("첫 원문", "정제본 없음", message_id="v1")
        e2 = self.capture("둘째 원문", message_id="v2")
        self.sh("idea_store", "assign", "--entry-id", e2["entry_id"], "--idea-ref", idea["ref"])
        n = self.db().execute("SELECT COUNT(*) c FROM revision").fetchone()["c"]
        self.assertEqual(n, 0)
        show = self.sh("idea_search", "show", "--idea-ref", idea["ref"])
        self.assertIsNone(show["latest_revision"])

    # -- TC19 -------------------------------------------------------------
    def test_TC19_same_day_duplicate_without_message_id(self):
        a = self.capture("메시지 아이디 없는 원문")
        b = self.capture("메시지 아이디 없는 원문")
        self.assertFalse(a["duplicate"])
        self.assertTrue(b["duplicate"])
        self.assertEqual(b["duplicate_by"], "body_hash_same_day")
        self.assertEqual(a["entry_id"], b["entry_id"])
        c = self.sh("idea_store", "capture", "--body", "메시지 아이디 없는 원문", "--force")
        self.assertFalse(c["duplicate"])
        self.assertNotEqual(c["entry_id"], a["entry_id"])

    # -- TC20 -------------------------------------------------------------
    def test_TC20_live_db_not_inside_dropbox_mirror(self):
        self.make_idea("백업 위치 시험", "위치", message_id="loc1")
        bk = self.sh("idea_check", "backup")
        self.assertIsNotNone(bk["mirrored_to"])
        live = (self.data / "ideas.db").resolve()
        self.assertFalse(str(live).startswith(str(self.mirror.resolve())),
                         "원본 DB가 백업 미러 안에 있습니다")
        self.assertTrue((Path(bk["mirrored_to"]) / "ideas.db").exists())

    # -- undo (§6.5) ------------------------------------------------------
    def test_undo_last_assign(self):
        idea, _ = self.make_idea("첫 원문", "취소 시험", message_id="z1")
        e = self.capture("잘못 붙일 원문", message_id="z2")
        self.sh("idea_store", "assign", "--entry-id", e["entry_id"], "--idea-ref", idea["ref"])
        self.sh("idea_store", "undo")
        row = self.db().execute("SELECT * FROM raw_entry WHERE id=?", (e["entry_id"],)).fetchone()
        self.assertEqual(row["assign_state"], "unassigned")
        self.assertIsNone(row["idea_id"])

    def test_undo_revise_restores_pointer(self):
        idea, _ = self.make_idea("원문", "정제 취소", message_id="z3")
        self.sh("idea_store", "revise", "--idea-ref", idea["ref"],
                 "--body", "rev1", "--summary", "s1")
        self.sh("idea_store", "revise", "--idea-ref", idea["ref"],
                 "--body", "rev2", "--summary", "s2")
        self.sh("idea_store", "undo")
        show = self.sh("idea_search", "show", "--idea-ref", idea["ref"])
        self.assertEqual(show["latest_revision"]["rev_no"], 1)
        self.assertEqual(len(show["revisions"]), 2)   # 이력은 불변

    def test_symmetric_relation_normalized(self):
        a, _ = self.make_idea("가", "A", message_id="y1")
        b, _ = self.make_idea("나", "B", message_id="y2")
        self.sh("idea_store", "relate", "--from", b["ref"], "--to", a["ref"], "--type", "similar")
        r = self.db().execute("SELECT * FROM idea_relation").fetchone()
        self.assertLess(r["from_idea_id"], r["to_idea_id"])
        self.assertEqual(r["symmetric"], 1)
        self.assertTrue(self.sh("idea_check", "integrity")["healthy"])

    def test_ref_overflow_past_9999_keeps_numeric_order(self):
        """9999를 넘겨 자릿수가 늘어도 채번과 정렬이 번호순을 지킨다 (PRD §5.2)."""
        a, _ = self.make_idea("첫째", "A", message_id="o1")     # CLIO-0001
        b, _ = self.make_idea("둘째", "B", message_id="o2")     # CLIO-0002
        conn = self.db()
        with conn:                                              # 9999 직전 상태를 만든다
            for old, new in ((a["ref"], "CLIO-2000"), (b["ref"], "CLIO-9999")):
                conn.execute("UPDATE idea SET ref=? WHERE ref=?", (new, old))
                conn.execute("UPDATE search_fts SET ref=? WHERE ref=?", (new, old))
        conn.close()

        c, _ = self.make_idea("셋째", "C", message_id="o3")
        self.assertEqual(c["ref"], "CLIO-10000")                # 자릿수 확장, 잘리지 않는다

        refs = [i["ref"] for i in
                self.sh("idea_search", "list", "--order", "ref")["ideas"]]
        self.assertEqual(refs, ["CLIO-2000", "CLIO-9999", "CLIO-10000"])

        out = Path(tempfile.mkdtemp(prefix="clio-export-"))
        self.addCleanup(shutil.rmtree, out, True)
        self.sh("idea_export", "md", "--out", str(out))
        idx = (out / "index.md").read_text(encoding="utf-8")
        self.assertLess(idx.index("CLIO-9999"), idx.index("CLIO-10000"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
