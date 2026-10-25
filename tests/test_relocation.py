#!/usr/bin/env python3
"""데이터 위치·이전·복구 테스트 (v1.3.0).

모든 테스트는 임시 홈 폴더(HOME)에서 실제 스크립트를 실행한다. 사용자 홈의 데이터,
위치 기록, registry, crontab에는 손대지 않는다 (crontab은 가짜 명령으로 바꿔 끼운다).

    python3 tests/test_clio.py      # 전체 (이 파일 포함)
    python3 tests/test_relocation.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import clio_core  # noqa: E402

CRASH_POINTS = ["after_backup", "after_staging", "after_target_placed",
                "after_source_retired", "after_pointer"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree(root: Path) -> dict:
    """폴더 아래 모든 파일(숨김 포함)의 크기·해시·수정 시각. dry-run 불변 검증용."""
    out = {}
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        for f in filenames:
            p = Path(dirpath) / f
            st = p.lstat()
            out[str(p.relative_to(root))] = (st.st_size, sha(p) if p.is_file() else None,
                                             st.st_mtime_ns, st.st_mode)
        for d in dirnames:
            p = Path(dirpath) / d
            out[str(p.relative_to(root)) + "/"] = (None, None, None, p.lstat().st_mode)
    return out


class RelocationTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="clio-rhome-")).resolve()
        self.files = Path(tempfile.mkdtemp(prefix="clio-rfiles-")).resolve()
        self.bin = self.files / "bin"
        self.bin.mkdir()
        # 가짜 crontab: $HOME/_crontab 내용을 그대로 보여준다
        cron = self.bin / "crontab"
        cron.write_text('#!/bin/sh\n[ -f "$HOME/_crontab" ] && cat "$HOME/_crontab"\nexit 0\n')
        cron.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home),
                        PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        CLIO_SKILL_DIR=str(self.files / "skill"), CLIO_LOCK_TIMEOUT="3")
        self.env.pop("CLIO_DATA_DIR", None)
        self.env.pop("CLIO_RELOCATE_CRASH_AT", None)
        self._saved = {k: os.environ.get(k) for k in ("HOME", "CLIO_DATA_DIR")}
        os.environ["HOME"] = str(self.home)
        os.environ.pop("CLIO_DATA_DIR", None)
        clio_core.invalidate_resolution()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
            if p.stdout:
                p.stdout.close()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        clio_core.invalidate_resolution()
        for d in (self.home, self.files):
            subprocess.run(["chmod", "-R", "u+rwx", str(d)], capture_output=True)
            shutil.rmtree(d, ignore_errors=True)

    # -- 경로 --------------------------------------------------------------
    @property
    def legacy(self) -> Path:
        return self.home / "ClioData"

    @property
    def target(self) -> Path:
        return self.home / "SecretaryData" / "services" / "ideas"

    @property
    def backups(self) -> Path:
        return self.home / "SecretaryBackups" / "ideas"

    @property
    def pointer(self) -> Path:
        return self.home / ".config" / "clio-idea-manager" / "data-root.json"

    @property
    def registry(self) -> Path:
        return self.home / "SecretaryData" / "registry.json"

    # -- 실행 헬퍼 ---------------------------------------------------------
    def run_tool(self, script, *args, env=None, cwd=None):
        return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                              capture_output=True, text=True, env=env or self.env, cwd=cwd)

    def sh(self, script, *args, expect_ok=True, env=None, cwd=None):
        proc = self.run_tool(script, *args, env=env, cwd=cwd)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"{script} {args} 가 JSON을 내지 않았습니다 (rc={proc.returncode}):\n"
                      f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}")
        if expect_ok:
            self.assertTrue(payload.get("ok"), f"{script} {args} 실패: "
                            f"{json.dumps(payload, ensure_ascii=False)[:3000]}")
            self.assertEqual(proc.returncode, 0)
        else:
            self.assertFalse(payload.get("ok"), f"실패해야 하는데 성공: {script} {args}")
            self.assertEqual(proc.returncode, 1)
        return payload

    def at(self, root: Path) -> dict:
        return dict(self.env, CLIO_DATA_DIR=str(root))

    def install(self, expect_ok=True, env=None):
        proc = subprocess.run(["bash", str(ROOT / "install.sh")], capture_output=True,
                              text=True, env=env or self.env, cwd=str(ROOT))
        if expect_ok:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout + proc.stderr

    def relocate(self, *extra, expect_ok=True, env=None, cwd=None):
        return self.sh("idea_relocate", "run", "--quiet-seconds", "0", *extra,
                       expect_ok=expect_ok, env=env, cwd=cwd)

    def where(self, env=None) -> dict:
        return self.sh("idea_check", "where", env=env)

    def fp(self, db: Path) -> str:
        import clio_ops
        c = sqlite3.connect(db)
        try:
            return clio_ops.fingerprint(c)["sha256"]
        finally:
            c.close()

    def counts(self, db: Path) -> dict:
        import clio_ops
        c = sqlite3.connect(db)
        try:
            return clio_ops.counts(c)
        finally:
            c.close()

    # -- 준비 --------------------------------------------------------------
    def make_legacy(self, rich=True, marker=False, prepared_target=True):
        """v1.2 이하 설치를 흉내낸다: ~/ClioData에 데이터, 위치 표시·위치 기록 없음."""
        env = self.at(self.legacy)
        if rich:
            att = self.files / "photo.txt"
            att.write_text("첨부 원본 내용", encoding="utf-8")
            e1 = self.sh("idea_store", "capture", "--body", "기억보다 망각이 중요하다",
                         "--message-id", "m1", "--attach", str(att), env=env)
            i1 = self.sh("idea_store", "new-idea", "--entry-id", e1["entry_id"],
                         "--title", "선택적 망각", "--tags", "기억,망각", env=env)
            self.sh("idea_store", "revise", "--idea-ref", i1["ref"], "--body", "정제본 1",
                    "--summary", "최초 정제", env=env)
            e2 = self.sh("idea_store", "capture", "--body", "둘째 생각", "--message-id", "m2",
                         env=env)
            i2 = self.sh("idea_store", "new-idea", "--entry-id", e2["entry_id"],
                         "--title", "둘째", env=env)
            self.sh("idea_store", "relate", "--from", i1["ref"], "--to", i2["ref"],
                    "--type", "similar", env=env)
            e3 = self.sh("idea_store", "capture", "--body", "민감한 원문", "--message-id", "m3",
                         env=env)
            self.sh("idea_store", "redact", "--entry-id", e3["entry_id"], "--reason", "시험",
                    "--confirm", env=env)
            self.sh("idea_store", "capture", "--body", "미분류로 남을 원문", "--message-id", "m4",
                    env=env)
        else:
            self.sh("idea_check", "init", env=env)
        (self.legacy / "config.json").write_text(json.dumps(
            {"backup_mirror": "~/Dropbox/ClioBackup", "ask_threshold": 0.42}), encoding="utf-8")
        (self.legacy / "skill-prev" / "scripts").mkdir(parents=True)
        (self.legacy / "skill-prev" / "scripts" / "clio_core.py").write_text(
            'CLIO_VERSION = "1.2.1"\n', encoding="utf-8")
        (self.legacy / "backups").mkdir(exist_ok=True)
        if not marker:
            (self.legacy / "clio-root.json").unlink()
        (self.legacy / ".clio.lock").unlink(missing_ok=True)
        if prepared_target:
            self.target.mkdir(parents=True, mode=0o700)
            (self.target / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        self.registry.write_text(json.dumps({
            "state": "partially_migrated",
            "operational_roots": {"ideas": str(self.legacy), "schedule": "/x/schedule"},
            "planned_roots": {"ideas": str(self.target)},
            "cutovers": {"schedule": {"state": "migrated", "relocation_id": "S1"}},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        self.registry.chmod(0o600)
        clio_core.invalidate_resolution()
        return self.legacy / "ideas.db"

    def assert_old_tool_refused(self, db: Path):
        """구버전 도구가 하던 그대로(connect → 스키마 적용) 열면 실패해야 한다."""
        c = sqlite3.connect(db)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                c.executescript(clio_core.SCHEMA_SQL)
        finally:
            c.close()

    def show_all(self, env=None) -> dict:
        lst = self.sh("idea_search", "list", "--state", "all", "--limit", "100", env=env)
        return {i["ref"]: self.sh("idea_search", "show", "--idea-ref", i["ref"], env=env)
                for i in lst["ideas"]}

    def journals_done(self) -> list:
        out = []
        for d in (self.legacy / "relocation", self.target / "relocation"):
            for f in d.glob("*.json") if d.is_dir() else []:
                j = json.loads(f.read_text(encoding="utf-8"))
                out.append((j["id"], j["kind"], j["state"]))
        return sorted(set(out))

    # =====================================================================
    # 설치 경로: 신규 · 기존 · 명시
    # =====================================================================

    def test_new_install_uses_shared_default_root(self):
        out = self.install()
        self.assertIn("새 기본 위치", out)
        self.assertTrue((self.target / "ideas.db").exists())
        self.assertFalse(self.legacy.exists(), "신규 설치가 옛 기본 위치를 만들면 안 된다")
        ptr = json.loads(self.pointer.read_text(encoding="utf-8"))
        self.assertEqual(ptr["data_dir"], str(self.target))
        self.assertEqual(ptr["reason"], "install:new")
        marker = json.loads((self.target / "clio-root.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["state"], "active")
        self.assertEqual(marker["root_id"], ptr["root_id"])
        # 환경 변수 없이 실행되는 도구(에이전트)도 같은 위치를 쓴다
        e = self.sh("idea_store", "capture", "--body", "새 설치의 첫 원문")
        self.assertEqual(self.counts(self.target / "ideas.db")["raw_entry"], 1, e)
        self.assertEqual(self.where()["source"], "pointer")

    def test_existing_install_is_kept_not_reinitialized(self):
        """구버전 설치(위치 표시 없음): 기본값이 바뀌어도 옛 DB를 계속 쓰고 빈 DB를 만들지 않는다."""
        db = self.make_legacy()
        before = self.fp(db)
        w = self.where()
        self.assertEqual((w["source"], w["data_dir"]), ("legacy", str(self.legacy)))
        out = self.install()
        self.assertIn("기존 설치 위치 유지", out)
        self.assertIn("idea_relocate plan", out)
        self.assertFalse((self.target / "ideas.db").exists(), "새 위치에 빈 DB가 생기면 안 된다")
        self.assertEqual(self.fp(db), before)
        ptr = json.loads(self.pointer.read_text(encoding="utf-8"))
        self.assertEqual((ptr["data_dir"], ptr["reason"]), (str(self.legacy), "install:legacy"))
        # 사용자 설정 유지 + 새 설정 키 추가
        cfg = json.loads((self.legacy / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["ask_threshold"], 0.42)
        self.assertEqual(cfg["backup_root"], "~/SecretaryBackups/ideas")
        # 두 번째 업데이트도 같다
        self.install()
        self.assertEqual(self.where()["data_dir"], str(self.legacy))
        self.assertFalse((self.target / "ideas.db").exists())
        self.assertEqual(self.fp(db), before)
        # 환경 변수 없이 도구를 써도 옛 DB에 쌓인다
        self.sh("idea_store", "capture", "--body", "업데이트 뒤 원문", "--message-id", "u1")
        self.assertEqual(self.counts(db)["raw_entry"], 5)

    def test_explicit_env_path_overrides_and_is_not_pinned(self):
        custom = self.files / "custom-data"
        env = self.at(custom)
        out = self.install(env=env)
        self.assertIn("CLIO_DATA_DIR", out)
        self.assertTrue((custom / "ideas.db").exists())
        self.assertFalse(self.pointer.exists(), "명시 경로는 위치 기록으로 고정하지 않는다")
        self.assertFalse((self.target / "ideas.db").exists())
        # 위치 기록과 명시 경로가 다르면 진단한다
        self.install()                              # 환경 변수 없이 → 새 기본 위치 + 기록
        w = self.where(env=env)
        self.assertEqual(w["data_dir"], str(custom))
        self.assertIn("env_overrides_pointer", [x["code"] for x in w["warnings"]])
        self.assertEqual(w["without_env"]["data_dir"], str(self.target))

    def test_ambiguous_roots_refuse_without_touching_anything(self):
        self.make_legacy(rich=False, prepared_target=False)
        self.sh("idea_check", "init", env=self.at(self.target))
        snap = tree(self.home)
        w = self.where()
        self.assertFalse(w["usable"])
        self.assertEqual(w["problem"]["code"], "root_ambiguous")
        out = self.install(expect_ok=False)
        self.assertIn("두 위치", out.replace("기존 위치", "두 위치") if "모두 DB" in out else out)
        self.assertFalse((self.files / "skill").exists(), "판정 실패 시 스킬도 설치하지 않는다")
        err = self.sh("idea_store", "capture", "--body", "어디에도 저장되면 안 됨",
                      expect_ok=False)
        self.assertEqual(err["error"]["code"], "root_ambiguous")
        self.assertEqual(tree(self.home), snap)

    def test_pointer_to_missing_root_never_creates_empty_db(self):
        self.install()
        moved = self.files / "unplugged"
        shutil.move(str(self.target), str(moved))      # 외장 디스크가 빠진 상황
        err = self.sh("idea_store", "capture", "--body", "x", expect_ok=False)
        self.assertEqual(err["error"]["code"], "root_missing")
        self.assertFalse(self.target.exists())
        self.install(expect_ok=False)
        self.assertFalse(self.target.exists())

    def test_marker_without_db_never_creates_empty_db(self):
        self.install()
        (self.target / "ideas.db").unlink()
        for f in self.target.glob("ideas.db-*"):
            f.unlink()
        err = self.sh("idea_store", "capture", "--body", "x", expect_ok=False)
        self.assertEqual(err["error"]["code"], "db_missing")
        self.assertFalse((self.target / "ideas.db").exists())

    # =====================================================================
    # dry-run
    # =====================================================================

    def test_plan_is_read_only_and_complete(self):
        self.make_legacy()
        (self.home / "_crontab").write_text(
            f"0 4 * * * CLIO_DATA_DIR={self.legacy} SECRET_TOKEN=hunter2 idea_check backup\n",
            encoding="utf-8")
        (self.home / ".zshrc").write_text(f"export CLIO_DATA_DIR={self.legacy}\n", encoding="utf-8")
        snap = tree(self.home)
        plan = self.sh("idea_relocate", "plan", "--quiet-seconds", "0")
        self.assertEqual(tree(self.home), snap, "dry-run이 파일을 바꿨다")
        self.assertTrue(plan["ready"], plan["blockers"])
        self.assertEqual(plan["source"]["root"], str(self.legacy))
        self.assertEqual(plan["target"]["root"], str(self.target))
        self.assertEqual(plan["target"]["state"], "empty")
        for key in ("writers", "interruption", "recovery", "space", "permissions", "backup",
                    "pointer", "registry", "absolute_paths", "historical_refs", "steps"):
            self.assertIn(key, plan)
        self.assertEqual(plan["source"]["counts"]["raw_entry"], 4)
        names = {e["name"] for e in plan["source"]["inventory"]["keep"]}
        self.assertTrue({"skill-prev", "backups"} <= names)
        self.assertTrue(plan["writers"]["legacy_code_copies"])
        sched = [s for s in plan["schedules"] if s["where"] == "crontab"]
        self.assertTrue(sched and sched[0]["pins_old_root"], plan["schedules"])
        files = {r["file"] for r in plan["links_to_review"]}
        self.assertIn(str(self.home / ".zshrc"), files)
        self.assertIn("schedule_pins_old_root", [w["code"] for w in plan["warnings"]])
        text = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn("hunter2", text, "비밀값을 출력하면 안 된다")
        self.assertFalse((self.legacy / ".clio.lock").exists())
        self.assertFalse(self.pointer.exists())

    def test_old_installed_tools_block_until_updated(self):
        self.make_legacy(rich=False)
        old = self.files / "skill" / "scripts"
        old.mkdir(parents=True)
        (old / "clio_core.py").write_text('CLIO_VERSION = "1.2.2"\n', encoding="utf-8")
        plan = self.sh("idea_relocate", "plan", "--quiet-seconds", "0")
        self.assertIn("old_tools_installed", [b["code"] for b in plan["blockers"]])
        self.relocate(expect_ok=False)
        self.assertTrue((self.legacy / "ideas.db").exists())
        self.assertFalse((self.target / "ideas.db").exists())

    # =====================================================================
    # 실제 이전
    # =====================================================================

    def test_full_migration_preserves_records_and_blocks_old_root(self):
        db = self.make_legacy()
        before_fp, before_counts = self.fp(db), self.counts(db)
        before_show = self.show_all(env=self.at(self.legacy))
        before_att = {p.name: sha(p) for p in (self.legacy / "attachments").rglob("*") if p.is_file()}

        out = self.relocate()
        self.assertTrue(out["relocated"])
        self.assertEqual(out["to"], str(self.target))
        v = out["verification"]
        self.assertTrue(v["fingerprint_matches_source"] and v["files_hash_match"])
        self.assertEqual(v["integrity_check"], "ok")
        self.assertEqual(v["rows"]["raw_entry"], before_counts["raw_entry"])
        self.assertTrue(out["post_verification"]["old_location_blocked"])

        # 새 위치: 내용·식별자·첨부 연결 그대로
        new_db = self.target / "ideas.db"
        self.assertEqual(self.fp(new_db), before_fp)
        self.assertEqual(self.where()["data_dir"], str(self.target))
        self.assertEqual(self.show_all(), before_show)
        after_att = {p.name: sha(p) for p in (self.target / "attachments").rglob("*") if p.is_file()}
        self.assertEqual(after_att, before_att)
        self.assertTrue(self.sh("idea_check", "integrity")["healthy"])
        self.assertEqual(json.loads((self.target / "config.json").read_text())["ask_threshold"], 0.42)

        # 새 기록 저장 (환경 변수 없이)
        att = self.files / "new.txt"
        att.write_text("새 첨부", encoding="utf-8")
        e = self.sh("idea_store", "capture", "--body", "이전 후 첫 원문", "--message-id", "n1",
                    "--attach", str(att))
        self.assertTrue((self.target / e["attachments"][0]["stored_path"]).exists())
        self.assertEqual(self.counts(new_db)["raw_entry"], before_counts["raw_entry"] + 1)

        # 옛 위치: 차단 파일 + 보존본, 지우지 않음
        self.assertEqual(clio_core.db_kind(self.legacy / "ideas.db"), "tombstone")
        kept = Path(out["kept_old_db"])
        self.assertEqual(self.fp(kept), before_fp)
        self.assertTrue((self.legacy / "attachments").exists())
        self.assertTrue((self.legacy / "skill-prev").exists())
        err = self.sh("idea_store", "capture", "--body", "옛 위치로 쓰기",
                      env=self.at(self.legacy), expect_ok=False)
        self.assertEqual(err["error"]["code"], "root_retired")
        self.assertIn(str(self.target), err["error"]["message"])
        # 구버전 도구(위치 기록을 모름)는 옛 경로를 연다 → 차단 파일에서 멈추고 아무것도 만들지 않는다
        listing = sorted(p.name for p in self.legacy.iterdir())
        self.assert_old_tool_refused(self.legacy / "ideas.db")
        self.assertEqual(sorted(p.name for p in self.legacy.iterdir()), listing)

        # registry: ideas 항목만 바뀐다
        reg = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertEqual(reg["operational_roots"]["ideas"], str(self.target))
        self.assertEqual(reg["cutovers"]["ideas"]["state"], "migrated")
        self.assertEqual(reg["operational_roots"]["schedule"], "/x/schedule")
        self.assertEqual(reg["cutovers"]["schedule"], {"state": "migrated", "relocation_id": "S1"})
        self.assertEqual(reg["state"], "partially_migrated")
        self.assertEqual(self.registry.stat().st_mode & 0o777, 0o600)

        # 백업: 새 백업 루트, 검증 목록 포함
        bk = Path(out["backup"])
        self.assertEqual(bk.parent, self.backups)
        man = json.loads((bk / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["kind"], "relocation")
        self.assertIn("ideas.db", man["files"])
        self.assertTrue(any(k.startswith("attachments/") for k in man["files"]))
        self.assertIn("config.json", man["files"])

        # 상태 보고
        st = self.sh("idea_relocate", "status")
        self.assertEqual(st["latest_relocation"]["state"], "done")
        self.assertTrue(st["old_root"]["blocked"])
        self.assertTrue(st["since_switch"]["writes_after_switch"])

    def test_isolated_copy_supports_read_write_and_backup_restore(self):
        """격리 사본에서 조회·저장·첨부 열람, 새 백업 → 복원 검증."""
        self.make_legacy()
        self.relocate()
        bk = self.sh("idea_check", "backup", "--no-mirror")
        self.assertTrue(bk["verified"])
        self.assertEqual(Path(bk["path"]).parent, self.backups)
        iso = self.files / "isolated"
        rt = self.sh("idea_check", "restore-test", "--source", bk["path"], "--restore-to", iso)
        self.assertTrue(rt["verified"] and rt["manifest_verified"] and rt["fingerprint_match"])
        self.assertTrue(rt["restored_healthy"])
        env = self.at(iso)
        refs = [i["ref"] for i in self.sh("idea_search", "list", env=env)["ideas"]]
        self.assertIn("CLIO-0001", refs)
        show = self.sh("idea_search", "show", "--idea-ref", "CLIO-0001", env=env)
        self.assertTrue((iso / show["attachments"][0]["stored_path"]).exists())
        before = self.counts(self.target / "ideas.db")
        self.sh("idea_store", "capture", "--body", "격리 사본에만 쓰는 원문", env=env)
        self.assertEqual(self.counts(iso / "ideas.db")["raw_entry"], before["raw_entry"] + 1)
        self.assertEqual(self.counts(self.target / "ideas.db"), before, "운영 정본이 바뀌면 안 된다")

    def test_empty_db_and_empty_attachments_migrate(self):
        """현재 운영과 같은 상태: 스키마만 있고 기록 0건, 첨부 폴더 비어 있음, 백업 없음."""
        self.make_legacy(rich=False)
        self.assertFalse(self.backups.exists())
        out = self.relocate()
        self.assertEqual(out["verification"]["rows"]["raw_entry"], 0)
        self.assertTrue((self.target / "attachments").is_dir())
        self.assertTrue((self.backups / Path(out["backup"]).name / "manifest.json").exists())
        self.assertTrue(self.sh("idea_check", "integrity")["healthy"])

    def test_wal_pending_records_are_not_lost(self):
        db = self.make_legacy(rich=False)
        code = f"""
import sqlite3, hashlib, os
c = sqlite3.connect({str(db)!r}, isolation_level=None)
c.execute("PRAGMA wal_autocheckpoint=0")
for i in range(3):
    body = f"WAL에만 있는 원문 {{i}}"
    c.execute("INSERT INTO raw_entry(body, body_hash, captured_at, ingested_at) VALUES(?,?,?,?)",
              (body, hashlib.sha256(body.encode()).hexdigest(), "2026-10-24T10:00:00+09:00",
               "2026-10-24T10:00:00+09:00"))
os._exit(0)          # 닫지 않고 끝낸다 → 체크포인트 없이 WAL에 남는다
"""
        subprocess.run([sys.executable, "-c", code], check=True)
        wal = Path(f"{db}-wal")
        self.assertGreater(wal.stat().st_size, 0)
        naive = self.files / "naive.db"
        shutil.copy2(db, naive)                      # WAL을 빼고 본 파일만 복사하면
        self.assertEqual(self.counts(naive)["raw_entry"], 0)   # 기록이 사라진다
        out = self.relocate()
        self.assertEqual(out["verification"]["rows"]["raw_entry"], 3)
        self.assertEqual(self.counts(self.target / "ideas.db")["raw_entry"], 3)
        bk = Path(out["backup"]) / "ideas.db"
        self.assertEqual(self.counts(bk)["raw_entry"], 3)
        self.assertTrue(self.sh("idea_check", "integrity")["healthy"])

    def test_absolute_attachment_path_fixed_history_untouched(self):
        db = self.make_legacy()
        outside = self.files / "outside.bin"
        outside.write_bytes(b"outside")
        c = sqlite3.connect(db)
        with c:
            row = c.execute("SELECT id, stored_path FROM attachment").fetchone()
            c.execute("UPDATE attachment SET stored_path=? WHERE id=?",
                      (str(self.legacy / row[1]), row[0]))
            c.execute("INSERT INTO attachment(raw_entry_id, filename, sha256, stored_path, fetched_at)"
                      " VALUES(2, 'outside.bin', ?, ?, 'x')", (sha(outside), str(outside)))
            c.execute("INSERT INTO action_log(action, target, payload, created_at) VALUES"
                      "('note', 'x', ?, 'x')", (json.dumps({"path": str(self.legacy / "old.txt")}),))
        c.close()
        plan = self.sh("idea_relocate", "plan", "--quiet-seconds", "0")
        ap = plan["absolute_paths"]["attachment.stored_path"]
        self.assertEqual((ap["absolute"], ap["inside_source"], ap["outside_source"]), (2, 1, 1))
        self.assertEqual(plan["historical_refs"]["count"].get("action_log.payload"), 1)
        out = self.relocate()
        self.assertEqual(len(out["fixups"]["db"]), 1)
        c = sqlite3.connect(self.target / "ideas.db")
        paths = [r[0] for r in c.execute("SELECT stored_path FROM attachment ORDER BY id")]
        payload = c.execute("SELECT payload FROM action_log WHERE action='note'").fetchone()[0]
        c.close()
        self.assertEqual(paths[0], row[1])                      # 상대경로로 보정
        self.assertEqual(paths[1], str(outside))                # 밖은 그대로
        self.assertIn(str(self.legacy), payload)                # 역사 기록은 그대로
        self.assertTrue(self.sh("idea_check", "integrity")["healthy"])

    # =====================================================================
    # 거부 사유: 첨부 누락·해시·공간·권한·충돌·쓰기 주체
    # =====================================================================

    def assert_refused(self, code, *extra):
        snap_l, snap_t = tree(self.legacy), tree(self.target)
        err = self.relocate(*extra, expect_ok=False)
        codes = [b["code"] for b in err["error"].get("blockers", [])] + [err["error"]["code"]]
        self.assertIn(code, codes, err)
        self.assertEqual(tree(self.target), snap_t, "거부했는데 대상이 바뀌었다")
        snap_l_after = {k: v for k, v in tree(self.legacy).items()}
        self.assertEqual(snap_l_after, snap_l, "거부했는데 원본이 바뀌었다")
        self.assertFalse(self.pointer.exists())
        self.assertEqual(clio_core.db_kind(self.legacy / "ideas.db"), "sqlite")
        return err

    def test_missing_attachment_refused_then_accepted_explicitly(self):
        self.make_legacy()
        next((self.legacy / "attachments").rglob("*.txt")).unlink()
        self.assert_refused("source_problems")
        out = self.relocate("--accept-source-problems")
        self.assertEqual([p["check"] for p in out["verification"]["pre_existing_problems"]],
                         ["attachment_missing"])
        integ = self.sh("idea_check", "integrity")
        self.assertEqual([p["check"] for p in integ["problems"]], ["attachment_missing"])

    def test_bad_attachment_hash_refused(self):
        self.make_legacy()
        next((self.legacy / "attachments").rglob("*.txt")).write_text("변조됨", encoding="utf-8")
        err = self.assert_refused("source_problems")
        self.assertIn("attachment_hash", json.dumps(err, ensure_ascii=False))

    def test_insufficient_space_refused(self):
        self.make_legacy(rich=False)
        self.assert_refused("insufficient_space", "--min-free-bytes", str(10 ** 18))

    def test_permission_denied_refused(self):
        self.make_legacy(rich=False)
        services = self.target.parent
        services.chmod(0o500)
        try:
            self.assert_refused("permission_denied")
        finally:
            services.chmod(0o700)

    def test_target_conflicts_refused(self):
        self.make_legacy(rich=False)
        (self.target / "notes.txt").write_text("누군가의 파일", encoding="utf-8")
        self.assert_refused("target_conflict")
        (self.target / "notes.txt").unlink()
        self.sh("idea_check", "init", env=self.at(self.target))   # 다른 DB가 이미 있다
        (self.target / ".clio.lock").unlink()
        snap = tree(self.target)
        err = self.relocate(expect_ok=False)
        self.assertIn("target_conflict", json.dumps(err))
        self.assertEqual(tree(self.target), snap)

    def test_running_writer_blocks_relocation(self):
        db = self.make_legacy(rich=False)
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.path.insert(0, sys.argv[1]); import clio_core; "
             "clio_core.connect(); print('ready', flush=True); time.sleep(60)", str(SCRIPTS)],
            env=self.at(self.legacy), stdout=subprocess.PIPE, text=True)
        self.procs.append(holder)
        self.assertEqual(holder.stdout.readline().strip(), "ready")
        plan = self.sh("idea_relocate", "plan", "--quiet-seconds", "0")
        self.assertIn("writer_active", [b["code"] for b in plan["blockers"]])
        self.assert_refused("writer_active")
        # 잠금은 풀렸는데 잠금을 모르는 구버전 도구가 DB를 열고 있는 경우도 lsof로 잡는다
        holder.kill()
        holder.wait()
        raw = subprocess.Popen(
            [sys.executable, "-c", "import sqlite3, sys, time; c = sqlite3.connect(sys.argv[1]); "
             "c.execute('select 1 from meta').fetchall(); print('ready', flush=True); time.sleep(60)",
             str(db)], stdout=subprocess.PIPE, text=True)
        self.procs.append(raw)
        self.assertEqual(raw.stdout.readline().strip(), "ready")
        if shutil.which("lsof"):
            self.assert_refused("writer_active")
        raw.kill()
        raw.wait()
        self.relocate()

    def test_concurrent_relocation_is_refused(self):
        self.make_legacy(rich=False)
        # 다른 이전 작업이 배타 잠금을 쥔 상태
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
             "import clio_core; clio_core.lock_root(Path(sys.argv[2]), True); "
             "print('ready', flush=True); time.sleep(60)", str(SCRIPTS), str(self.legacy)],
            env=self.env, stdout=subprocess.PIPE, text=True)
        self.procs.append(holder)
        self.assertEqual(holder.stdout.readline().strip(), "ready")
        self.assert_refused("writer_active", "--lock-wait", "0.5")
        # 그 사이 일반 명령은 성공한 척하지 않고 실패를 보고한다
        err = self.sh("idea_store", "capture", "--body", "이전 중 저장 시도", expect_ok=False,
                      env=dict(self.env, CLIO_LOCK_TIMEOUT="0.5"))
        self.assertEqual(err["error"]["code"], "root_busy")
        holder.kill()
        holder.wait()

    # =====================================================================
    # 중단 후 재실행 · update 재실행
    # =====================================================================

    def test_interrupted_relocation_resumes_without_loss_or_duplication(self):
        for point in CRASH_POINTS:
            with self.subTest(crash=point):
                self.tearDown()
                self.setUp()
                db = self.make_legacy()
                before_fp, before_counts = self.fp(db), self.counts(db)
                proc = self.run_tool("idea_relocate", "run", "--quiet-seconds", "0",
                                     env=dict(self.env, CLIO_RELOCATE_CRASH_AT=point))
                self.assertEqual(proc.returncode, 70, proc.stdout + proc.stderr)
                st = self.sh("idea_relocate", "status")
                self.assertTrue(st["unfinished"], st)
                out = self.relocate()
                self.assertTrue(out.get("relocated"), out)
                self.assertEqual(self.where()["data_dir"], str(self.target))
                self.assertEqual(self.fp(self.target / "ideas.db"), before_fp)
                self.assertEqual(self.counts(self.target / "ideas.db"), before_counts)
                self.assertEqual(clio_core.db_kind(self.legacy / "ideas.db"), "tombstone")
                self.assertFalse(list(self.target.parent.glob(".ideas.staging-*")),
                                 "임시 폴더가 남았다")
                states = [s for _, k, s in self.journals_done() if k == "migrate"]
                self.assertEqual(states.count("done"), 1, self.journals_done())
                self.assertNotIn("running", states)
                self.assertTrue(self.sh("idea_check", "integrity")["healthy"])
                again = self.relocate()
                self.assertTrue(again["already_migrated"])

    def test_writes_to_source_after_interruption_are_kept(self):
        self.make_legacy()
        proc = self.run_tool("idea_relocate", "run", "--quiet-seconds", "0",
                             env=dict(self.env, CLIO_RELOCATE_CRASH_AT="after_target_placed"))
        self.assertEqual(proc.returncode, 70)
        # 아직 원본이 정본이다 (두 위치 모두 DB가 있어도 모호하다고 멈추지 않는다)
        w = self.where()
        self.assertEqual(w["data_dir"], str(self.legacy))
        self.assertIn("relocation_unfinished", [x["code"] for x in w["warnings"]])
        # 놓아 둔 사본을 직접 지정해 쓰는 것은 막는다 (양쪽이 갈라지지 않게)
        err = self.sh("idea_store", "capture", "--body", "사본에 쓰기", env=self.at(self.target),
                      expect_ok=False)
        self.assertEqual(err["error"]["code"], "relocation_unfinished")
        # 그 사이 원본에 새 기록이 쌓인다
        self.sh("idea_store", "capture", "--body", "중단 사이에 들어온 원문", "--message-id", "gap1")
        out = self.relocate()
        self.assertTrue(out["relocated"])
        bodies = [e["preview"] for e in self.sh("idea_search", "unassigned")["entries"]]
        self.assertIn("중단 사이에 들어온 원문", bodies)
        self.assertTrue(list(self.target.parent.glob(".ideas.abandoned-*")),
                        "먼저 놓아 둔 낡은 사본은 지우지 않고 옆으로 치운다")

    def test_update_rerun_after_migration_keeps_new_root(self):
        db = self.make_legacy()
        self.install()                               # v1.3 업데이트 (옛 위치 고정)
        before = self.fp(db)
        self.relocate()
        for _ in range(2):
            out = self.install()
            self.assertIn(str(self.target), out)
            self.assertEqual(self.where()["data_dir"], str(self.target))
        self.assertEqual(self.fp(self.target / "ideas.db"), before)
        self.assertEqual(clio_core.db_kind(self.legacy / "ideas.db"), "tombstone")
        self.assertFalse((self.legacy / "ideas.db-wal").exists())
        self.assertEqual(len([j for j in self.journals_done() if j[1] == "migrate"]), 1)
        cfg = json.loads((self.target / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["ask_threshold"], 0.42)
        self.assertTrue((self.target / "skill-prev" / "scripts" / "idea_store").exists())
        self.assertTrue(self.relocate()["already_migrated"])

    # =====================================================================
    # 복구
    # =====================================================================

    def test_rollback_without_new_writes_restores_original(self):
        db = self.make_legacy()
        original = self.fp(db)
        out = self.relocate()
        prev = self.sh("idea_relocate", "rollback", "--dry-run")
        self.assertEqual(prev["mode"], "no_writes")
        rb = self.sh("idea_relocate", "rollback", "--quiet-seconds", "0")
        self.assertEqual(rb["mode"], "no_writes")
        self.assertEqual(self.where()["data_dir"], str(self.legacy))
        self.assertEqual(self.fp(self.legacy / "ideas.db"), original)
        self.assertTrue(Path(out["kept_old_db"]).exists(), "보존본은 소모하지 않는다")
        # 새 위치는 은퇴 표시만 있는 빈 자리로 돌아간다 — 명시 경로로 써도 빈 DB가 생기지 않는다
        self.assertEqual([p.name for p in self.target.iterdir()], ["clio-root.json"])
        ph = json.loads((self.target / "clio-root.json").read_text(encoding="utf-8"))
        self.assertEqual((ph["state"], ph["placeholder"]), ("retired", True))
        err = self.sh("idea_store", "capture", "--body", "갈라지면 안 되는 원문",
                      env=self.at(self.target), expect_ok=False)
        self.assertEqual(err["error"]["code"], "root_retired")
        self.assertFalse((self.target / "ideas.db").exists())
        self.assertTrue(list(self.target.parent.glob(".ideas.rolledback-*")))
        reg = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertEqual(reg["operational_roots"]["ideas"], str(self.legacy))
        self.assertEqual(reg["cutovers"]["ideas"]["state"], "rolled_back")
        self.sh("idea_store", "capture", "--body", "복구 후 원문", "--message-id", "r1")
        self.assertTrue(self.sh("idea_check", "integrity")["healthy"])
        # 다시 이전할 수 있다
        self.assertTrue(self.relocate()["relocated"])
        self.assertEqual(self.counts(self.target / "ideas.db")["raw_entry"], 5)

    def test_rollback_after_new_writes_keeps_new_records(self):
        db = self.make_legacy()
        original = self.fp(db)
        out = self.relocate()
        att = self.files / "after.txt"
        att.write_text("전환 후 첨부", encoding="utf-8")
        e = self.sh("idea_store", "capture", "--body", "전환 후 새 원문", "--message-id", "w1",
                    "--attach", str(att))
        i = self.sh("idea_store", "new-idea", "--entry-id", e["entry_id"], "--title", "전환 후 글감")
        self.sh("idea_store", "undo")                 # 옛 감사 기록의 undone_at이 바뀌는 경우
        latest = self.fp(self.target / "ideas.db")
        prev = self.sh("idea_relocate", "rollback", "--dry-run")
        self.assertEqual(prev["mode"], "with_writes")
        self.assertTrue(prev["descendant"])
        self.assertGreaterEqual(prev["delta"]["new_action_count"], 3)
        rb = self.sh("idea_relocate", "rollback", "--quiet-seconds", "0")
        self.assertEqual(rb["mode"], "with_writes")
        self.assertEqual(self.where()["data_dir"], str(self.legacy))
        self.assertEqual(self.fp(self.legacy / "ideas.db"), latest, "새 기록까지 옮겨야 한다")
        # 되돌린 글감은 지운 상태로, 그 원문은 미분류로 남는다 — 전환 뒤 상태 그대로
        show = self.sh("idea_search", "show", "--idea-ref", i["ref"], "--include-deleted")
        self.assertEqual(show["title"], "전환 후 글감")
        bodies = [x["preview"] for x in self.sh("idea_search", "unassigned")["entries"]]
        self.assertIn("전환 후 새 원문", bodies)
        stored = e["attachments"][0]["stored_path"]
        self.assertTrue((self.legacy / stored).exists())
        self.assertTrue(self.sh("idea_check", "integrity")["healthy"])
        self.assertEqual(self.fp(Path(out["kept_old_db"])), original, "이전 전 원본도 그대로 보존")
        # 새 위치는 차단된다
        self.assertEqual(clio_core.db_kind(self.target / "ideas.db"), "tombstone")
        err = self.sh("idea_store", "capture", "--body", "x", env=self.at(self.target),
                      expect_ok=False)
        self.assertEqual(err["error"]["code"], "root_retired")
        man = json.loads((Path(rb["backup"]) / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["kind"], "rollback")
        self.assertEqual(man["fingerprint"]["sha256"], latest)
        # 은퇴한 위치로 다시 옮기려면 명시적으로 허용해야 한다
        self.assertIn("target_conflict", json.dumps(self.relocate(expect_ok=False)))
        again = self.relocate("--replace-retired")
        self.assertTrue(again["relocated"])
        self.assertEqual(self.fp(self.target / "ideas.db"), latest)

    def test_interrupted_rollback_resumes(self):
        for point in ("rb_after_backup", "rb_after_db_restored", "rb_after_current_retired"):
            for writes in (False, True):
                with self.subTest(crash=point, writes=writes):
                    self.tearDown()
                    self.setUp()
                    self.make_legacy()
                    self.relocate()
                    if writes:
                        self.sh("idea_store", "capture", "--body", "전환 후 원문", "--message-id", "w")
                    expect = self.fp(self.target / "ideas.db") if writes else \
                        self.fp(next(self.legacy.glob("ideas.db.relocated-*")))
                    proc = self.run_tool("idea_relocate", "rollback", "--quiet-seconds", "0",
                                         env=dict(self.env, CLIO_RELOCATE_CRASH_AT=point))
                    self.assertEqual(proc.returncode, 70, proc.stdout + proc.stderr)
                    # 멈춘 상태에서도 두 위치가 동시에 쓰이지 않고, 옛 위치 차단 파일도 그대로다
                    self.assertEqual(clio_core.db_kind(self.legacy / "ideas.db"), "tombstone", point)
                    w = self.sh("idea_check", "where")
                    live = [r for r in (self.legacy, self.target)
                            if clio_core.root_state(r)["live"]]
                    self.assertLessEqual(len(live), 1, (point, w))
                    self.assertIn("rollback_in_progress",
                                  json.dumps(self.relocate(expect_ok=False)))
                    rb = self.sh("idea_relocate", "rollback", "--quiet-seconds", "0")
                    self.assertTrue(rb["rolled_back"])
                    self.assertEqual(self.where()["data_dir"], str(self.legacy))
                    self.assertEqual(self.fp(self.legacy / "ideas.db"), expect)
                    self.assertTrue(self.sh("idea_check", "integrity")["healthy"])

    def test_code_downgrade_does_not_reopen_old_db(self):
        """코드 버전 복구(skill-prev)와 데이터 경로 복구는 별개다."""
        self.make_legacy()
        self.install()
        self.relocate()
        # 구버전 도구의 데이터 경로 규칙: CLIO_DATA_DIR 또는 ~/ClioData
        old_rule = Path(os.environ.get("CLIO_DATA_DIR", str(self.legacy))).expanduser()
        self.assertEqual(clio_core.db_kind(old_rule / "ideas.db"), "tombstone")
        self.assert_old_tool_refused(old_rule / "ideas.db")
        self.assertFalse((old_rule / "ideas.db-wal").exists())
        # 구버전으로 새 위치를 쓰려면 경로를 명시한다 (스키마 버전이 같아 열린다)
        c = sqlite3.connect(self.target / "ideas.db")
        c.executescript(clio_core.SCHEMA_SQL)
        self.assertEqual(c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                         str(clio_core.SCHEMA_VERSION))
        c.close()

    # =====================================================================
    # 백업
    # =====================================================================

    def test_backup_verification_list_and_tamper_detection(self):
        self.install()
        att = self.files / "b.txt"
        att.write_text("백업될 첨부", encoding="utf-8")
        self.sh("idea_store", "capture", "--body", "백업 대상", "--attach", str(att))
        bk = self.sh("idea_check", "backup")
        self.assertTrue(bk["verified"])
        self.assertEqual(Path(bk["path"]).parent, self.backups)
        self.assertEqual(bk["mirror"]["status"], "inactive")       # ~/Dropbox/ClioBackup 없음
        man = json.loads((Path(bk["path"]) / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((man["tool"], man["kind"]), ("clio-idea-manager", "routine"))
        rt = self.sh("idea_check", "restore-test", "--source", bk["path"])
        self.assertTrue(rt["verified"])
        f = next((Path(bk["path"]) / "attachments").rglob("*.txt"))
        f.chmod(0o644)
        f.write_text("변조", encoding="utf-8")
        rt = self.sh("idea_check", "restore-test", "--source", bk["path"])
        self.assertFalse(rt["verified"])
        self.assertFalse(rt["manifest_verified"])
        self.assertIn("attachments/", rt["file_problems"][0]["file"])

    def test_mirror_is_never_created_by_configuration_alone(self):
        self.install()
        (self.home / "Dropbox").mkdir()               # Dropbox는 있지만 미러 폴더는 없다
        bk = self.sh("idea_check", "backup")
        self.assertIsNone(bk["mirrored_to"])
        self.assertEqual(bk["mirror"]["status"], "inactive")
        self.assertFalse((self.home / "Dropbox" / "ClioBackup").exists())
        (self.home / "Dropbox" / "ClioBackup").mkdir()  # 직접 만들면 그때부터 쓴다
        bk = self.sh("idea_check", "backup")
        self.assertEqual(bk["mirror"]["status"], "mirrored")
        self.assertTrue((Path(bk["mirrored_to"]) / "manifest.json").exists())

    def test_prune_only_touches_routine_clio_backups(self):
        self.make_legacy(rich=False)
        self.relocate()                                # relocation 백업 1개
        foreign = self.backups / "20200101T000000"
        (foreign / "data").mkdir(parents=True)
        (foreign / "manifest.json").write_text(json.dumps({"system": "ideas", "purpose": "preflight"}))
        cfg_path = self.target / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["backup_retention"] = {"daily": 2, "weekly": 0, "monthly": 0}
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        for _ in range(4):
            self.sh("idea_check", "backup", "--no-mirror")
        kinds = {}
        for d in self.backups.iterdir():
            if d.is_dir():
                m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
                kinds.setdefault(m.get("kind", "foreign"), []).append(d.name)
        self.assertEqual(len(kinds["routine"]), 2)
        self.assertEqual(len(kinds["relocation"]), 1)
        self.assertTrue(foreign.exists(), "다른 도구의 사전 백업은 지우지 않는다")


    # =====================================================================
    # 검토에서 나온 경우들
    # =====================================================================

    def test_config_values_that_are_not_paths_are_untouched(self):
        """작업 폴더가 원본 안이어도 "Asia/Seoul" 같은 값을 경로로 바꾸지 않는다."""
        self.make_legacy(rich=False)
        cfg = json.loads((self.legacy / "config.json").read_text(encoding="utf-8"))
        cfg.update(timezone="Asia/Seoul", webhook="https://example.invalid/a/b",
                   notes_dir=str(self.legacy / "notes"), outside=str(self.files / "x"))
        (self.legacy / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        plan = self.sh("idea_relocate", "plan", "--quiet-seconds", "0", cwd=self.legacy)
        self.assertNotIn("example.invalid", json.dumps(plan))
        out = self.relocate(cwd=self.legacy)
        self.assertEqual([f["key"] for f in out["fixups"]["config"]], ["notes_dir"])
        new = json.loads((self.target / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(new["timezone"], "Asia/Seoul")
        self.assertEqual(new["webhook"], "https://example.invalid/a/b")
        self.assertEqual(new["notes_dir"], str(self.target / "notes"))
        self.assertEqual(new["outside"], str(self.files / "x"))
        self.sh("idea_store", "capture", "--body", "시간대가 살아 있어야 저장된다")

    def test_rollback_with_writes_does_not_resurrect_redacted_attachment(self):
        self.make_legacy()
        self.relocate()
        show = self.sh("idea_search", "show", "--idea-ref", "CLIO-0001")
        stored = show["attachments"][0]["stored_path"]
        entry = show["raw_entries"][0]["entry_id"]
        self.assertTrue((self.legacy / stored).exists(), "이전 때 옛 위치에 남겨 둔 첨부")
        self.sh("idea_store", "redact", "--entry-id", entry, "--reason", "민감정보", "--confirm")
        self.assertFalse((self.target / stored).exists())
        rb = self.sh("idea_relocate", "rollback", "--quiet-seconds", "0")
        self.assertEqual(rb["mode"], "with_writes")
        self.assertEqual(rb["removed_redacted_attachments"], [stored])
        self.assertFalse((self.legacy / stored).exists(), "영구 삭제한 첨부가 되살아나면 안 된다")
        self.assertTrue(self.sh("idea_check", "integrity")["healthy"])

    def test_abandoned_rollback_keeps_old_root_blocked(self):
        self.make_legacy()
        self.relocate()
        self.sh("idea_store", "capture", "--body", "전환 후 원문 1", "--message-id", "a1")
        proc = self.run_tool("idea_relocate", "rollback", "--quiet-seconds", "0",
                             env=dict(self.env, CLIO_RELOCATE_CRASH_AT="rb_after_db_restored"))
        self.assertEqual(proc.returncode, 70, proc.stdout + proc.stderr)
        self.assertEqual(clio_core.db_kind(self.legacy / "ideas.db"), "tombstone")
        # 멈춘 사이 새 위치가 아직 정본이다 — 기록이 더 생긴다
        self.assertEqual(self.where()["data_dir"], str(self.target))
        self.sh("idea_store", "capture", "--body", "전환 후 원문 2", "--message-id", "a2")
        latest = self.fp(self.target / "ideas.db")
        rb = self.sh("idea_relocate", "rollback", "--quiet-seconds", "0")
        self.assertTrue(rb["rolled_back"])
        self.assertEqual(self.fp(self.legacy / "ideas.db"), latest, "둘째 기록까지 옮겨야 한다")
        states = [st for _, k, st in self.journals_done() if k == "rollback"]
        self.assertEqual(sorted(states), ["abandoned", "done"])
        self.assertFalse(list(self.legacy.glob(".ideas.db.rollback-*")), "포기한 임시 DB가 남았다")

    def test_config_edit_during_interruption_is_kept(self):
        self.make_legacy(rich=False)
        proc = self.run_tool("idea_relocate", "run", "--quiet-seconds", "0",
                             env=dict(self.env, CLIO_RELOCATE_CRASH_AT="after_target_placed"))
        self.assertEqual(proc.returncode, 70)
        cfg = json.loads((self.legacy / "config.json").read_text(encoding="utf-8"))
        cfg["ask_threshold"] = 0.5                     # 멈춘 사이 정본(원본)의 설정을 고쳤다
        (self.legacy / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.relocate()
        new = json.loads((self.target / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(new["ask_threshold"], 0.5)

    def test_unreadable_legacy_db_is_not_replaced_by_empty_default(self):
        self.legacy.mkdir()
        (self.legacy / "ideas.db").write_bytes(b"\x00garbage-not-sqlite" * 10)
        snap = tree(self.home)
        w = self.sh("idea_check", "where")
        self.assertFalse(w["usable"])
        self.assertEqual(w["problem"]["code"], "db_unrecognized")
        err = self.sh("idea_store", "capture", "--body", "x", expect_ok=False)
        self.assertEqual(err["error"]["code"], "db_unrecognized")
        self.assertFalse(self.target.exists())
        self.assertEqual(tree(self.home), snap)

    def test_restore_test_leaves_backup_untouched(self):
        self.install()
        self.sh("idea_store", "capture", "--body", "백업 원문")
        bk = Path(self.sh("idea_check", "backup", "--no-mirror")["path"])
        before = tree(bk)
        for _ in range(2):
            rt = self.sh("idea_check", "restore-test", "--source", bk)
            self.assertTrue(rt["verified"], rt)
        self.assertEqual(tree(bk), before)
        # v1.2 형식 백업(WAL 표시가 남은 DB, 검증 목록 없음)도 바꾸지 않는다
        old = self.files / "old-backup"
        old.mkdir()
        src = sqlite3.connect(self.target / "ideas.db")
        dst = sqlite3.connect(old / "ideas.db")
        src.backup(dst)
        src.close()
        dst.close()
        (old / "attachments").mkdir()
        (old / "manifest.json").write_text(json.dumps({"db_sha256": sha(old / "ideas.db"),
                                                       "counts": {}}), encoding="utf-8")
        before = tree(old)
        self.sh("idea_check", "restore-test", "--source", old)
        self.assertEqual(tree(old), before)

    def test_registry_with_unexpected_format_is_left_alone(self):
        self.make_legacy(rich=False)
        self.registry.write_text(json.dumps({"operational_roots": ["not", "a", "dict"]}),
                                 encoding="utf-8")
        before = self.registry.read_bytes()
        out = self.relocate()
        self.assertTrue(out["relocated"])
        self.assertFalse(out["registry_update"]["updated"])
        self.assertEqual(out["registry_update"]["reason"], "unexpected_format")
        self.assertIn("deliver_to_chris", out["registry_update"])
        self.assertEqual(self.registry.read_bytes(), before)
        self.assertEqual(self.sh("idea_relocate", "status")["latest_relocation"]["state"], "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
