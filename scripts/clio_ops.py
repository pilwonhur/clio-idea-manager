"""Clio 운영 공용 모듈 — 무결성·지문·백업·이전 보조 (v1.3.0).

idea_check(백업·복원 시험)와 idea_relocate(데이터 이전·복구)가 함께 쓴다.
판단하지 않는다. 사실을 계산하고, 파일을 안전하게 옮기고, 검증 결과를 낸다.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import time
from datetime import datetime
from pathlib import Path

from clio_core import (
    CLIO_VERSION, DB_NAME, LOCK_NAME, ROOT_MARKER, SCHEMA_VERSION, ClioError, home,
    idea_tags, now_iso, ref_order_sql, ref_sort_key, sha256_file, sha256_text,
    write_json_atomic,
)

COUNT_TABLES = ["idea", "raw_entry", "revision", "idea_relation", "tag", "idea_tag",
                "source", "attachment", "action_log"]
# 지문 대상. search_fts는 파생 인덱스라 뺀다 (언제든 재생성 가능).
FP_TABLES = ["meta", "source", "idea", "raw_entry", "attachment", "revision",
             "idea_relation", "tag", "idea_tag", "action_log"]
IGNORABLE = {".DS_Store"}


def open_db(path, row=True) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    if row:
        conn.row_factory = sqlite3.Row
    return conn


def counts(conn) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in COUNT_TABLES}


# --------------------------------------------------------------------------
# 무결성 검사 (PRD §9.4)
# --------------------------------------------------------------------------

def run_integrity(conn, root: Path) -> dict:
    """root는 첨부 상대경로의 기준 폴더다 (운영 폴더, 백업, 시험 사본 어디든)."""
    problems = []

    def add(kind, detail):
        problems.append({"check": kind, "detail": detail})

    for r in conn.execute("SELECT id, body, body_hash FROM raw_entry WHERE redacted_at IS NULL"):
        if sha256_text(r["body"]) != r["body_hash"]:
            add("raw_body_hash", f"entry {r['id']}의 본문 해시가 일치하지 않습니다")

    for r in conn.execute(
        "SELECT i.id, i.ref, i.latest_revision_id, "
        "(SELECT id FROM revision WHERE idea_id=i.id ORDER BY rev_no DESC LIMIT 1) AS top "
        "FROM idea i WHERE i.deleted_at IS NULL"):
        if r["top"] is not None and r["latest_revision_id"] != r["top"]:
            add("latest_revision", f"{r['ref']}의 latest_revision_id가 최신 revision과 다릅니다")
        if r["top"] is None and r["latest_revision_id"] is not None:
            add("latest_revision", f"{r['ref']}가 존재하지 않는 revision을 가리킵니다")

    orphan_sql = {
        "revision": "SELECT COUNT(*) c FROM revision r LEFT JOIN idea i ON i.id=r.idea_id WHERE i.id IS NULL",
        "idea_relation_from": "SELECT COUNT(*) c FROM idea_relation r LEFT JOIN idea i ON i.id=r.from_idea_id WHERE i.id IS NULL",
        "idea_relation_to": "SELECT COUNT(*) c FROM idea_relation r LEFT JOIN idea i ON i.id=r.to_idea_id WHERE i.id IS NULL",
        "idea_tag": "SELECT COUNT(*) c FROM idea_tag it LEFT JOIN idea i ON i.id=it.idea_id WHERE i.id IS NULL",
        "raw_entry": "SELECT COUNT(*) c FROM raw_entry e WHERE e.idea_id IS NOT NULL "
                     "AND NOT EXISTS (SELECT 1 FROM idea i WHERE i.id=e.idea_id)",
        "attachment": "SELECT COUNT(*) c FROM attachment a LEFT JOIN raw_entry e ON e.id=a.raw_entry_id WHERE e.id IS NULL",
    }
    for name, sql in orphan_sql.items():
        n = conn.execute(sql).fetchone()["c"]
        if n:
            add("orphan", f"{name}에 고아 레코드 {n}건")

    refs = [r["ref"] for r in conn.execute("SELECT ref FROM idea")]
    if len(refs) != len(set(refs)):
        add("ref_duplicate", "글감 번호가 중복되었습니다")
    nums = sorted(int(x.rsplit("-", 1)[1]) for x in refs if re.fullmatch(r".+-\d+", x))
    if nums:
        missing = sorted(set(range(1, nums[-1] + 1)) - set(nums))
        if missing:
            add("ref_gap", f"번호 공백 {len(missing)}건 (tombstone 삭제 시 정상): {missing[:10]}")

    unassigned = conn.execute(
        "SELECT COUNT(*) c FROM raw_entry WHERE assign_state='unassigned' "
        "AND redacted_at IS NULL").fetchone()["c"]

    for r in conn.execute(
        "SELECT id, from_idea_id, to_idea_id FROM idea_relation "
        "WHERE symmetric=1 AND from_idea_id > to_idea_id"):
        add("symmetry", f"relation {r['id']}의 대칭 관계 정규화가 깨졌습니다")

    for r in conn.execute("SELECT * FROM attachment WHERE redacted_at IS NULL"):
        p = Path(root) / r["stored_path"]
        if not p.exists():
            add("attachment_missing", f"첨부 파일 없음: {r['stored_path']}")
        elif sha256_file(p) != r["sha256"]:
            add("attachment_hash", f"첨부 해시 불일치: {r['stored_path']}")

    pragma = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if pragma != "ok":
        add("sqlite", pragma)

    return {"healthy": not problems, "problem_count": len(problems), "problems": problems,
            "unassigned_entries": unassigned, "counts": counts(conn),
            "integrity_check": pragma, "clio_version": CLIO_VERSION}


def problem_set(integ: dict) -> list:
    return sorted((p["check"], p["detail"]) for p in integ["problems"])


# --------------------------------------------------------------------------
# 지문 — 이전 전후, 백업 전후가 같은 데이터인지 행 단위로 대조한다
# --------------------------------------------------------------------------

def fingerprint(conn) -> dict:
    tables = {}
    for t in FP_TABLES:
        h = hashlib.sha256()
        n = 0
        try:
            cur = conn.execute(f"SELECT rowid, * FROM {t} ORDER BY rowid")
        except sqlite3.OperationalError:
            tables[t] = {"rows": None, "sha256": None, "missing": True}
            continue
        for row in cur:
            h.update(json.dumps(list(row), ensure_ascii=False, default=repr).encode("utf-8"))
            h.update(b"\n")
            n += 1
        tables[t] = {"rows": n, "sha256": h.hexdigest()}
    refs = sorted((r[0] for r in conn.execute("SELECT ref FROM idea")), key=ref_sort_key)
    entry_ids = [r[0] for r in conn.execute("SELECT id FROM raw_entry ORDER BY id")]
    try:
        schema = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        schema = None
    return {
        "sha256": sha256_text(json.dumps(tables, sort_keys=True)),
        "tables": tables,
        "refs": {"count": len(refs), "first": refs[0] if refs else None,
                 "last": refs[-1] if refs else None, "sha256": sha256_text("\n".join(refs))},
        "raw_entry_ids": {"count": len(entry_ids),
                          "sha256": sha256_text(",".join(map(str, entry_ids)))},
        "schema_version": schema[0] if schema else None,
    }


def fingerprint_diff(a: dict, b: dict) -> dict:
    return {t: {"before": a["tables"][t]["rows"], "after": b["tables"][t]["rows"]}
            for t in FP_TABLES if a["tables"].get(t) != b["tables"].get(t)}


# --------------------------------------------------------------------------
# 파일 복사와 목록 — 복사한 모든 파일의 크기와 sha256을 남긴다
# --------------------------------------------------------------------------

def copy_file_verified(src: Path, dst: Path) -> dict:
    """내용을 복사하면서 해시를 계산하고, 다 쓴 뒤 다시 읽어 대조한다."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    size = 0
    with src.open("rb") as fi, dst.open("wb") as fo:
        for chunk in iter(lambda: fi.read(1 << 20), b""):
            h.update(chunk)
            fo.write(chunk)
            size += len(chunk)
        fo.flush()
        os.fsync(fo.fileno())
    shutil.copystat(src, dst)
    digest = h.hexdigest()
    if sha256_file(dst) != digest:
        raise ClioError("copy_hash_mismatch", f"복사본의 해시가 원본과 다릅니다: {dst}",
                        path=str(dst))
    return {"bytes": size, "sha256": digest}


def tree_files(root: Path, skip_dirs: set[str] = frozenset()) -> list[Path]:
    if not root.exists():
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        for f in sorted(filenames):
            if f in IGNORABLE:
                continue
            out.append(Path(dirpath) / f)
    return out


def copy_tree_verified(src: Path, dst: Path, skip_dirs: set[str] = frozenset()) -> dict:
    files = {}
    total = 0
    for p in tree_files(src, skip_dirs):
        rel = p.relative_to(src).as_posix()
        info = copy_file_verified(p, dst / rel)
        files[rel] = info
        total += info["bytes"]
    return {"files": len(files), "bytes": total, "manifest": files}


def hash_tree(root: Path, skip_dirs: set[str] = frozenset()) -> dict:
    return {p.relative_to(root).as_posix(): {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
            for p in tree_files(root, skip_dirs)}


def tree_bytes(root: Path, skip_dirs: set[str] = frozenset()) -> tuple[int, int]:
    files = tree_files(root, skip_dirs)
    return len(files), sum(p.stat().st_size for p in files)


# --------------------------------------------------------------------------
# 내보내기 (PRD §10) — idea_export와 백업이 함께 쓴다
# --------------------------------------------------------------------------

def collect(conn, include_deleted=False):
    out = []
    sql = "SELECT * FROM idea" + ("" if include_deleted else " WHERE deleted_at IS NULL")
    for idea in conn.execute(sql + f" ORDER BY {ref_order_sql()}"):
        entries = [dict(r) for r in conn.execute(
            "SELECT * FROM raw_entry WHERE idea_id=? ORDER BY captured_at, id", (idea["id"],))]
        revs = [dict(r) for r in conn.execute(
            "SELECT * FROM revision WHERE idea_id=? ORDER BY rev_no", (idea["id"],))]
        rels = [dict(r) for r in conn.execute(
            "SELECT r.*, f.ref AS from_ref, t.ref AS to_ref FROM idea_relation r "
            "JOIN idea f ON f.id=r.from_idea_id JOIN idea t ON t.id=r.to_idea_id "
            "WHERE r.from_idea_id=? OR r.to_idea_id=?", (idea["id"], idea["id"]))]
        atts = [dict(r) for r in conn.execute(
            "SELECT a.* FROM attachment a JOIN raw_entry e ON e.id=a.raw_entry_id "
            "WHERE e.idea_id=?", (idea["id"],))]
        out.append({"idea": dict(idea), "tags": idea_tags(conn, idea["id"]),
                    "raw_entries": entries, "revisions": revs, "relations": rels,
                    "attachments": atts})
    return out


def export_json(conn, cfg, out: Path, include_deleted=False) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"exported_at": now_iso(cfg), "schema_version": 1,
               "unassigned": [dict(r) for r in conn.execute(
                   "SELECT * FROM raw_entry WHERE idea_id IS NULL")],
               "ideas": collect(conn, include_deleted)}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(out), "ideas": len(payload["ideas"]),
            "unassigned": len(payload["unassigned"])}


def export_md(conn, cfg, out: Path, include_deleted=False) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    items = collect(conn, include_deleted)
    for it in items:
        idea, tags = it["idea"], it["tags"]
        lines = [f"# {idea['ref']} — {idea['title']}", "",
                 f"- 보관 상태: {idea['storage_state']}",
                 f"- 발전 단계: {idea['dev_stage']}",
                 f"- 생성일: {idea['created_at']}",
                 f"- 최종 업데이트: {idea['updated_at']}",
                 f"- 키워드: {', '.join(tags) or '-'}"]
        parents = [r["to_ref"] for r in it["relations"]
                   if r["rel_type"] == "derived_from" and r["from_ref"] == idea["ref"]]
        derived = [r["from_ref"] for r in it["relations"]
                   if r["rel_type"] == "derived_from" and r["to_ref"] == idea["ref"]]
        others = [f"{r['to_ref'] if r['from_ref'] == idea['ref'] else r['from_ref']} ({r['rel_type']})"
                  for r in it["relations"] if r["rel_type"] != "derived_from"]
        lines += [f"- 부모 글감: {', '.join(parents) or '없음'}",
                  f"- 파생 글감: {', '.join(derived) or '없음'}",
                  f"- 관련 글감: {', '.join(others) or '없음'}", "",
                  "## 원문 (교수님 작성 · 무수정)", ""]
        for e in it["raw_entries"]:
            lines.append(f"### {e['captured_at']}  ·  entry {e['id']}  ·  {e['entry_type']}")
            lines.append("")
            lines.append("*(영구 삭제됨)*" if e["redacted_at"] else e["body"])
            lines.append("")
        lines += ["## 정제본 (Clio 작성)", ""]
        if it["revisions"]:
            for r in it["revisions"]:
                mark = " ← 최신" if r["id"] == idea["latest_revision_id"] else ""
                lines += [f"### rev.{r['rev_no']} · {r['created_at']}{mark}", "",
                          f"> 변경: {r['change_summary']}", "", r["body"], ""]
        else:
            lines += ["아직 없습니다.", ""]
        if it["attachments"]:
            lines += ["## 첨부", ""]
            lines += [f"- `{a['stored_path']}` ({a['filename']})" for a in it["attachments"]]
        path = out / f"{idea['ref']}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        written.append(path.name)

    unassigned = [dict(r) for r in conn.execute(
        "SELECT * FROM raw_entry WHERE idea_id IS NULL ORDER BY captured_at")]
    idx = ["# Clio 글감 목록", "", f"생성: {now_iso(cfg)}", ""]
    for it in items:
        i = it["idea"]
        idx.append(f"- [{i['ref']}]({i['ref']}.md) — {i['title']} "
                   f"({i['storage_state']}/{i['dev_stage']}, 업데이트 {i['updated_at'][:10]})")
    if unassigned:
        idx += ["", "## 미분류 원문", ""]
        idx += [f"- entry {u['id']} · {u['captured_at']} · "
                f"{' '.join(u['body'].split())[:60]}" for u in unassigned]
    (out / "index.md").write_text("\n".join(idx), encoding="utf-8")
    return {"path": str(out), "files": len(written) + 1, "ideas": len(items)}


# --------------------------------------------------------------------------
# 백업 (PRD §9.3) — <backup_root>/<시각>/ 에 DB·첨부·설정·내보내기·검증 목록
# --------------------------------------------------------------------------

def snapshot(src_conn, dest: Path, standalone: bool = False) -> None:
    """실행 중인 DB 파일을 복사하지 않고 SQLite 백업 API로 스냅샷을 뜬다.

    백업 API는 WAL에만 있고 아직 본 파일에 반영되지 않은 기록까지 포함한다.
    standalone=True(백업용)면 저널 모드를 DELETE로 바꿔 파일 하나로 완결되게 한다.
    WAL 표시가 남은 백업은 읽기 전용으로 열기만 해도 옆에 -wal/-shm이 생겨 검증 목록과 어긋난다.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(dest)
    try:
        src_conn.backup(target)
        if standalone:
            target.execute("PRAGMA journal_mode=DELETE").fetchall()
    finally:
        target.close()


def _unique_dir(parent: Path, stamp: str) -> Path:
    cand = parent / stamp
    n = 2
    while cand.exists():
        cand = parent / f"{stamp}-{n}"
        n += 1
    return cand


def redaction_manifest(conn, backup_root: Path, ts: str) -> int:
    """영구 삭제 사실을 백업과 함께 보존한다 (PRD §9.2). 복구 시 재적용한다."""
    items = []
    for r in conn.execute("SELECT id, redacted_at, body_hash FROM raw_entry "
                          "WHERE redacted_at IS NOT NULL"):
        items.append({"kind": "raw_entry", "entry_id": r["id"],
                      "redacted_at": r["redacted_at"], "body_hash": r["body_hash"]})
    for r in conn.execute("SELECT ref, deleted_at FROM idea WHERE deleted_at IS NOT NULL"):
        items.append({"kind": "idea", "ref": r["ref"], "deleted_at": r["deleted_at"]})
    (backup_root / "redaction_manifest.json").write_text(
        json.dumps({"updated_at": ts, "entries": items}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return len(items)


def create_backup(conn, root: Path, backup_root: Path, cfg: dict, kind: str = "routine",
                  relocation_id: str | None = None, root_id: str | None = None) -> dict:
    """백업을 만들고 곧바로 검증한다. 검증에 실패하면 verified=False로 보고한다.

    kind: routine(예약·수동, 보존 정책 적용) | relocation | rollback (보존 정책 미적용)
    """
    ts = now_iso(cfg)
    stamp = datetime.fromisoformat(ts).strftime("%Y%m%dT%H%M%S")
    backup_root.mkdir(parents=True, exist_ok=True)
    dest = _unique_dir(backup_root, stamp)
    dest.mkdir(mode=0o700)

    snapshot(conn, dest / DB_NAME, standalone=True)
    att = copy_tree_verified(root / "attachments", dest / "attachments", skip_dirs={".incoming"})
    if (root / "config.json").is_file():
        copy_file_verified(root / "config.json", dest / "config.json")
    snap = open_db(dest / DB_NAME)
    try:
        export_json(snap, cfg, dest / "clio-export.json")
        export_md(snap, cfg, dest / "markdown")
        fp = fingerprint(snap)
        integ = run_integrity(snap, dest)
        n_red = redaction_manifest(snap, backup_root, ts)
        cnt = counts(snap)
    finally:
        snap.close()
    files = hash_tree(dest)
    manifest = {
        "tool": "clio-idea-manager", "format": 2, "kind": kind, "created_at": ts,
        "clio_version": CLIO_VERSION, "schema_version": SCHEMA_VERSION,
        "source": {"root": str(root), "root_id": root_id}, "relocation_id": relocation_id,
        "counts": cnt, "attachments": att["files"], "fingerprint": fp,
        "integrity": {"healthy": integ["healthy"], "problems": integ["problems"]},
        "db_sha256": files[DB_NAME]["sha256"], "files": files,
    }
    write_json_atomic(dest / "manifest.json", manifest, mode=0o600)
    verify = verify_backup(dest)
    return {"path": str(dest), "kind": kind, "counts": cnt, "attachments": att["files"],
            "attachment_bytes": att["bytes"], "redaction_entries": n_red,
            "fingerprint": fp["sha256"], "files": len(files),
            "source_healthy": integ["healthy"], "verified": verify["verified"],
            "verification": verify}


def verify_backup(dest: Path) -> dict:
    """검증 목록(manifest.files)의 크기·해시를 다시 계산해 대조한다."""
    mpath = dest / "manifest.json"
    manifest = None
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        return {"verified": False, "format": None, "files_checked": 0,
                "problems": [{"file": "manifest.json", "problem": "unreadable"}]}
    if not manifest or "files" not in manifest:
        return {"verified": None, "format": (manifest or {}).get("format", 1),
                "files_checked": 0, "problems": [],
                "note": "구형(v1.2 이하) 백업이라 파일 검증 목록이 없습니다."}
    problems = []
    actual = hash_tree(dest)
    actual.pop("manifest.json", None)
    for rel, info in manifest["files"].items():
        got = actual.get(rel)
        if got is None:
            problems.append({"file": rel, "problem": "missing"})
        elif got["sha256"] != info["sha256"] or got["bytes"] != info["bytes"]:
            problems.append({"file": rel, "problem": "hash_mismatch"})
    for rel in sorted(set(actual) - set(manifest["files"])):
        problems.append({"file": rel, "problem": "unexpected"})
    return {"verified": not problems, "format": manifest.get("format"),
            "files_checked": len(manifest["files"]), "problems": problems}


def is_prunable(d: Path) -> bool:
    """Clio가 만든 정기 백업만 지운다. 다른 도구·사전 백업·이전용 백업은 건드리지 않는다."""
    try:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if m.get("tool") == "clio-idea-manager":
        return m.get("kind", "routine") == "routine"
    # v1.2 이하 백업: tool 표시가 없다
    return "db_sha256" in m and "counts" in m and (d / DB_NAME).is_file()


def prune(backup_root: Path, cfg) -> list[str]:
    """일 7 / 주 4 / 월 12 rolling 보존. 대상은 is_prunable()인 폴더뿐이다."""
    dirs = sorted([d for d in backup_root.iterdir() if d.is_dir() and d.name[:1].isdigit()
                   and is_prunable(d)], reverse=True)
    keep, seen_w, seen_m = set(), set(), set()
    ret = cfg["backup_retention"]
    for i, d in enumerate(dirs):
        try:
            when = datetime.strptime(d.name[:15], "%Y%m%dT%H%M%S")
        except ValueError:
            keep.add(d)
            continue
        if i < ret["daily"]:
            keep.add(d)
        wk = when.strftime("%G-W%V")
        if wk not in seen_w and len(seen_w) < ret["weekly"]:
            seen_w.add(wk)
            keep.add(d)
        mo = when.strftime("%Y-%m")
        if mo not in seen_m and len(seen_m) < ret["monthly"]:
            seen_m.add(mo)
            keep.add(d)
    removed = []
    for d in dirs:
        if d not in keep:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    return removed


def mirror_backup(dest: Path, backup_root: Path, mirror: str | None) -> dict:
    """외부 미러. 미러 폴더가 이미 있을 때만 복사한다 — 설정만으로 새로 켜지 않는다."""
    if not mirror:
        return {"status": "not_configured", "mirrored_to": None}
    m = Path(mirror).expanduser()
    if not m.is_dir():
        return {"status": "inactive", "mirrored_to": None, "configured": str(m),
                "message": f"미러 폴더가 없어 복사하지 않았습니다: {m}. "
                           "설정이 있다고 미러 백업이 된 것은 아닙니다. 쓰려면 폴더를 직접 만드세요."}
    try:
        out = _unique_dir(m, dest.name)
        copy_tree_verified(dest, out)
        if (backup_root / "redaction_manifest.json").exists():
            shutil.copy2(backup_root / "redaction_manifest.json", m / "redaction_manifest.json")
        return {"status": "mirrored", "mirrored_to": str(out)}
    except (OSError, ClioError) as e:
        return {"status": "failed", "mirrored_to": None, "configured": str(m),
                "message": str(e)}


def restore_copy(backup_dir: Path, db: Path, out: Path) -> dict:
    """백업을 out 폴더에 복원한다 (DB는 백업 API, 첨부는 해시 검증 복사)."""
    out.mkdir(parents=True, exist_ok=True)
    # 백업 폴더의 DB는 열지 않고 파일로 복사한다. 열면(읽기 전용이라도) WAL 표시가 있는
    # 구형 백업 옆에 -wal/-shm이 생겨 백업 자체가 바뀐다.
    copy_file_verified(db, out / DB_NAME)
    if Path(f"{db}-wal").is_file():
        copy_file_verified(Path(f"{db}-wal"), out / f"{DB_NAME}-wal")
    att = copy_tree_verified(backup_dir / "attachments", out / "attachments")
    if (backup_dir / "config.json").is_file():
        copy_file_verified(backup_dir / "config.json", out / "config.json")
    return {"attachments": att["files"]}


# --------------------------------------------------------------------------
# 쓰기 주체·예약 작업·참조 조사 (읽기 전용)
# --------------------------------------------------------------------------

def open_handles(paths: list[Path]) -> dict:
    """lsof로 DB 파일을 열고 있는 다른 프로세스를 찾는다. 내 프로세스는 뺀다."""
    exe = shutil.which("lsof")
    existing = [str(p) for p in paths if p.exists()]
    if not exe:
        return {"method": "unavailable", "pids": [], "processes": {},
                "note": "lsof가 없어 열린 파일을 확인하지 못했습니다."}
    if not existing:
        return {"method": "lsof", "pids": [], "processes": {}}
    try:
        proc = subprocess.run([exe, "-F", "pc", "--", *existing], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"method": "failed", "pids": [], "processes": {}, "note": str(e)}
    procs, pid = {}, None
    for line in proc.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("c") and pid is not None:
            procs[pid] = line[1:]
    procs.pop(os.getpid(), None)
    return {"method": "lsof", "pids": sorted(procs), "processes": {str(k): v for k, v in procs.items()}}


def last_write_age(root: Path) -> float | None:
    """DB·WAL의 마지막 수정 후 지난 초. -shm은 읽기만 해도 바뀌므로 보지 않는다."""
    times = [p.stat().st_mtime for p in (root / DB_NAME, root / f"{DB_NAME}-wal") if p.exists()]
    return round(time.time() - max(times), 1) if times else None


def lock_holders(root: Path) -> dict:
    """잠금 파일을 만들지 않고, 지금 Clio 명령이 잠금을 쥐고 있는지만 본다."""
    path = root / LOCK_NAME
    if not path.exists():
        return {"lock_file": False, "busy": False}
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return {"lock_file": True, "busy": False}
    except BlockingIOError:
        return {"lock_file": True, "busy": True}
    finally:
        os.close(fd)


def skill_version(skill_dir: Path) -> str | None:
    f = skill_dir / "scripts" / "clio_core.py"
    try:
        m = re.search(r'^CLIO_VERSION\s*=\s*"([^"]+)"', f.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return m.group(1) if m else "1.1.0 이전"


def _needles(root: Path) -> list[str]:
    out = {str(root)}
    try:
        rel = root.relative_to(home()).as_posix()
        out |= {f"~/{rel}", f"$HOME/{rel}", f"${{HOME}}/{rel}"}
    except ValueError:
        pass
    return sorted(out, key=len, reverse=True)


def _crontab() -> list[str] | None:
    exe = shutil.which("crontab")
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, "-l"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.splitlines() if proc.returncode == 0 else []


SCAN_FILES = [".zshrc", ".zprofile", ".zshenv", ".bashrc", ".bash_profile", ".profile",
              ".openclaw/openclaw.json"]
SCHEDULE_HINT = re.compile(r"idea_(check|store|export|search|relocate)|clio-idea-manager|CLIO_DATA_DIR")


def scan_references(old_root: Path, extra: list[str] | None = None) -> dict:
    """옛 경로를 적어 둔 사용자 파일·예약 작업을 찾는다. 내용은 출력하지 않는다 (비밀값 보호).

    파일 경로와 줄 번호만 보고한다. 자동으로 고치지 않는다.
    """
    needles = _needles(old_root)
    h = home()
    cands = [h / f for f in SCAN_FILES]
    cands += sorted((h / "Library" / "LaunchAgents").glob("*.plist"))
    cands += sorted((h / ".openclaw").glob("workspace-clio/*.md"))
    cands += sorted((h / ".openclaw" / "cron").glob("*.json*"))
    for e in extra or []:
        p = Path(e).expanduser()
        if p.is_dir():
            cands += sorted(x for x in p.rglob("*") if x.is_file()
                            and x.suffix in (".md", ".json", ".txt", ".plist", ".sh", ".yaml", ".yml"))[:2000]
        else:
            cands.append(p)
    refs, schedules = [], []
    for f in cands:
        if not f.is_file():
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        hit = [i + 1 for i, l in enumerate(lines) if any(n in l for n in needles)]
        if hit:
            refs.append({"where": "file", "file": str(f), "lines": hit[:50], "hits": len(hit)})
        if f.suffix == ".plist" or "cron" in f.parts:
            sched = [i + 1 for i, l in enumerate(lines) if SCHEDULE_HINT.search(l)]
            if sched:
                schedules.append({"where": "launchd" if f.suffix == ".plist" else "openclaw_cron",
                                  "file": str(f), "lines": sched[:50],
                                  "pins_old_root": bool(hit)})
    cron = _crontab()
    if cron is None:
        cron_status = "unavailable"
    else:
        cron_status = "checked"
        hit = [i + 1 for i, l in enumerate(cron) if any(n in l for n in needles)]
        sched = [i + 1 for i, l in enumerate(cron)
                 if SCHEDULE_HINT.search(l) and not l.lstrip().startswith("#")]
        if hit:
            refs.append({"where": "crontab", "file": "crontab -l", "lines": hit, "hits": len(hit)})
        if sched:
            schedules.append({"where": "crontab", "file": "crontab -l", "lines": sched,
                              "pins_old_root": bool(set(hit) & set(sched))})
    return {"needles": needles, "links_to_review": refs, "schedules": schedules,
            "crontab": cron_status}


# --------------------------------------------------------------------------
# 공간·권한
# --------------------------------------------------------------------------

def nearest_existing(p: Path) -> Path:
    p = Path(p)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def free_bytes(p: Path) -> int:
    return shutil.disk_usage(nearest_existing(p)).free


def same_device(a: Path, b: Path) -> bool:
    return nearest_existing(a).stat().st_dev == nearest_existing(b).stat().st_dev


def writable(p: Path) -> bool:
    base = nearest_existing(p)
    return os.access(base, os.W_OK | os.X_OK)


# --------------------------------------------------------------------------
# 공동 registry — 해당 항목만, 잠금을 잡고 원자적으로 갱신한다
# --------------------------------------------------------------------------

def registry_path() -> Path:
    return home() / "SecretaryData" / "registry.json"


def read_registry_entry(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"exists": False}
    except (OSError, json.JSONDecodeError) as e:
        return {"exists": True, "readable": False, "error": str(e)}
    def section(name):
        v = data.get(name) if isinstance(data, dict) else None
        return v if isinstance(v, dict) else {}
    ok = isinstance(data, dict) and all(
        isinstance(data.get(k, {}), dict) for k in ("operational_roots", "planned_roots", "cutovers"))
    return {"exists": True, "readable": True, "format_ok": ok,
            "operational_root": section("operational_roots").get("ideas"),
            "planned_root": section("planned_roots").get("ideas"),
            "cutover": section("cutovers").get("ideas")}


def update_registry(path: Path, operational_root: str, cutover: dict,
                    timeout: float = 10.0) -> dict:
    """operational_roots.ideas와 cutovers.ideas만 바꾼다. 다른 시스템 항목은 그대로 둔다.

    registry 옆의 .registry.json.lock에 배타 잠금(flock)을 잡고, 잠금 안에서 다시 읽어
    고친 뒤 임시 파일 + rename으로 교체한다. 실패해도 이전 자체는 되돌리지 않고,
    Chris에게 전달할 갱신 내용을 그대로 돌려준다.
    """
    request = {"operational_roots.ideas": operational_root, "cutovers.ideas": cutover}
    if not path.exists():
        return {"updated": False, "reason": "registry_missing", "path": str(path),
                "deliver_to_chris": request}
    lock = path.parent / f".{path.name}.lock"
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        return {"updated": False, "reason": f"lock_unavailable: {e}", "path": str(path),
                "deliver_to_chris": request}
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return {"updated": False, "reason": "lock_timeout", "path": str(path),
                            "deliver_to_chris": request}
                time.sleep(0.1)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return {"updated": False, "reason": f"unreadable: {e}", "path": str(path),
                    "deliver_to_chris": request}
        if not (isinstance(data, dict)
                and isinstance(data.get("operational_roots", {}), dict)
                and isinstance(data.get("cutovers", {}), dict)):
            return {"updated": False, "reason": "unexpected_format", "path": str(path),
                    "deliver_to_chris": request}
        before = {"operational_root": (data.get("operational_roots") or {}).get("ideas"),
                  "cutover": (data.get("cutovers") or {}).get("ideas")}
        data.setdefault("operational_roots", {})["ideas"] = operational_root
        data.setdefault("cutovers", {})["ideas"] = cutover
        mode = stat.S_IMODE(path.stat().st_mode)
        try:
            write_json_atomic(path, data, mode=mode)
        except OSError as e:
            return {"updated": False, "reason": f"write_failed: {e}", "path": str(path),
                    "deliver_to_chris": request}
        return {"updated": True, "path": str(path), "before": before,
                "after": {"operational_root": operational_root, "cutover": cutover}}
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

