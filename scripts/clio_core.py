"""Clio 아이디어 관리 시스템 — 공용 코어.

PRD.md v1.2 §4.1 "결정론적 저장 계층"의 공용 모듈.
Python 3.11+ 표준 라이브러리만 사용한다 (PRD D3).

이 모듈은 판단하지 않는다. 스키마 제약, 트랜잭션, 중복 판정, 무결성만 책임진다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# 설정 (PRD §11)
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "backup_mirror": "~/Dropbox/ClioBackup",
    "timezone": "Asia/Seoul",
    "ref_prefix": "CLIO",
    "ref_digits": 4,
    # D1 — 자동 연결은 초기 비활성. 근거 없이 낮추지 않는다.
    "auto_link_threshold": 1.01,
    "auto_link_margin": 0.15,
    "ask_threshold": 0.35,
    # 이 값 미만의 후보는 "연결 후보"로도 언급하지 않는다 (출력 소음 방지).
    "mention_threshold": 0.15,
    "candidate_limit": 3,
    "default_list_limit": 20,
    "search_weights": {
        "title": 0.30,
        "tag": 0.25,
        "substring": 0.20,
        "fts": 0.15,
        "recency": 0.05,
        "relation": 0.05,
    },
    "state_factor": {"active": 1.0, "inbox": 1.0, "archived": 0.7},
    "recency_halflife_days": 90,
    "backup_retention": {"daily": 7, "weekly": 4, "monthly": 12},
}

SYMMETRIC_TYPES = {"similar", "related"}
REL_TYPES = ["similar", "related", "expands", "contradicts", "derived_from"]
ENTRY_TYPES = ["idea", "keyword", "supplement", "quote", "question"]
STORAGE_STATES = ["inbox", "active", "archived"]
DEV_STAGES = ["fragment", "developed", "outlined", "drafted", "published"]


def data_dir() -> Path:
    return Path(os.environ.get("CLIO_DATA_DIR", "~/ClioData")).expanduser()


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = data_dir() / "config.json"
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


class ClioError(Exception):
    """도구 계층의 모든 실패. 종료 코드 1과 함께 JSON으로 보고된다."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


# --------------------------------------------------------------------------
# 시각 (PRD §11 — ISO8601, 초 단위, Asia/Seoul 오프셋 포함)
# --------------------------------------------------------------------------

def now_iso(cfg: dict) -> str:
    return datetime.now(ZoneInfo(cfg["timezone"])).replace(microsecond=0).isoformat()


def day_of(ts: str) -> str:
    return ts[:10]


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


# --------------------------------------------------------------------------
# 스키마 (PRD §5.2)
# --------------------------------------------------------------------------

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source (
  id          INTEGER PRIMARY KEY,
  platform    TEXT NOT NULL,
  guild_id    TEXT,
  channel_id  TEXT,
  thread_id   TEXT,
  message_id  TEXT,
  author_id   TEXT,
  reply_to_id TEXT,
  edited      INTEGER NOT NULL DEFAULT 0,
  raw_payload TEXT,
  UNIQUE (platform, message_id)
);
-- D7: message_id는 NULL 허용. SQLite는 NULL을 서로 다른 값으로 보므로 위 UNIQUE는
-- ID가 있을 때만 작동한다. 없을 때의 중복 판정은 find_duplicate()가 담당한다.

CREATE TABLE IF NOT EXISTS idea (
  id                 INTEGER PRIMARY KEY,
  ref                TEXT UNIQUE NOT NULL,
  title              TEXT NOT NULL,
  storage_state      TEXT NOT NULL DEFAULT 'inbox'
                     CHECK (storage_state IN ('inbox','active','archived')),
  dev_stage          TEXT NOT NULL DEFAULT 'fragment'
                     CHECK (dev_stage IN ('fragment','developed','outlined','drafted','published')),
  latest_revision_id INTEGER,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  deleted_at         TEXT
);

CREATE TABLE IF NOT EXISTS raw_entry (
  id           INTEGER PRIMARY KEY,
  idea_id      INTEGER REFERENCES idea(id),
  assign_state TEXT NOT NULL DEFAULT 'unassigned'
               CHECK (assign_state IN ('unassigned','assigned')),
  body         TEXT NOT NULL,
  body_hash    TEXT NOT NULL,
  entry_type   TEXT NOT NULL DEFAULT 'idea'
               CHECK (entry_type IN ('idea','keyword','supplement','quote','question')),
  captured_at  TEXT NOT NULL,
  ingested_at  TEXT NOT NULL,
  source_id    INTEGER REFERENCES source(id),
  redacted_at  TEXT,
  UNIQUE (body_hash, source_id)
);
CREATE INDEX IF NOT EXISTS ix_raw_idea ON raw_entry(idea_id);
CREATE INDEX IF NOT EXISTS ix_raw_hash ON raw_entry(body_hash);

CREATE TABLE IF NOT EXISTS attachment (
  id           INTEGER PRIMARY KEY,
  raw_entry_id INTEGER NOT NULL REFERENCES raw_entry(id),
  filename     TEXT NOT NULL,
  mime_type    TEXT,
  byte_size    INTEGER,
  sha256       TEXT NOT NULL,
  stored_path  TEXT NOT NULL,
  source_url   TEXT,
  fetched_at   TEXT NOT NULL,
  redacted_at  TEXT,
  UNIQUE (raw_entry_id, sha256)
);

CREATE TABLE IF NOT EXISTS revision (
  id             INTEGER PRIMARY KEY,
  idea_id        INTEGER NOT NULL REFERENCES idea(id),
  rev_no         INTEGER NOT NULL,
  body           TEXT NOT NULL,
  change_summary TEXT NOT NULL,
  author         TEXT NOT NULL DEFAULT 'clio' CHECK (author IN ('clio','user')),
  created_at     TEXT NOT NULL,
  UNIQUE (idea_id, rev_no)
);

CREATE TABLE IF NOT EXISTS idea_relation (
  id                INTEGER PRIMARY KEY,
  from_idea_id      INTEGER NOT NULL REFERENCES idea(id),
  to_idea_id        INTEGER NOT NULL REFERENCES idea(id),
  rel_type          TEXT NOT NULL
                    CHECK (rel_type IN ('similar','related','expands','contradicts','derived_from')),
  symmetric         INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  created_by        TEXT NOT NULL CHECK (created_by IN ('user','clio')),
  rationale         TEXT,
  confidence        REAL,
  confirmed_by_user INTEGER NOT NULL DEFAULT 0,
  CHECK (from_idea_id <> to_idea_id),
  UNIQUE (from_idea_id, to_idea_id, rel_type)
);

CREATE TABLE IF NOT EXISTS tag (
  id       INTEGER PRIMARY KEY,
  name     TEXT UNIQUE NOT NULL,
  alias_of INTEGER REFERENCES tag(id)
);

CREATE TABLE IF NOT EXISTS idea_tag (
  idea_id INTEGER NOT NULL REFERENCES idea(id),
  tag_id  INTEGER NOT NULL REFERENCES tag(id),
  PRIMARY KEY (idea_id, tag_id)
);

CREATE TABLE IF NOT EXISTS action_log (
  id         INTEGER PRIMARY KEY,
  action     TEXT NOT NULL,
  target     TEXT NOT NULL,
  payload    TEXT NOT NULL,
  undone_at  TEXT,
  created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
  ref, title, tags, raw_body, revision_body,
  tokenize='unicode61 remove_diacritics 2'
);
"""


def connect(create: bool = True) -> sqlite3.Connection:
    d = data_dir()
    db = d / "ideas.db"
    if not db.exists() and not create:
        raise ClioError("db_missing", f"데이터베이스가 없습니다: {db}")
    d.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('schema_version',?) "
        "ON CONFLICT(key) DO NOTHING",
        (str(SCHEMA_VERSION),),
    )
    return conn


class tx:
    """단일 트랜잭션. 예외가 나면 전부 되돌린다 (부분 저장 금지, TC2)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False


# --------------------------------------------------------------------------
# 공용 헬퍼
# --------------------------------------------------------------------------

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ref_sort_key(ref: str) -> tuple:
    """번호순 정렬 키. 자릿수가 늘어난 뒤에도(CLIO-9999 → CLIO-10000) 순서를 지킨다."""
    m = re.fullmatch(r"(.*?)-(\d+)", ref or "")
    if m:
        return (m.group(1), int(m.group(2)), "")
    return (ref or "", -1, ref or "")


def ref_order_sql(col: str = "ref") -> str:
    """ref 번호순 ORDER BY 절. TEXT 사전순은 CLIO-10000을 CLIO-2000 앞에 둔다."""
    return f"substr({col}, 1, instr({col}, '-')), length({col}), {col}"


def next_ref(conn: sqlite3.Connection, cfg: dict) -> str:
    """번호는 재사용하지 않는다 (PRD §8 운영 원칙).

    tombstone 행이 남아 있으므로 MAX 기반 채번으로 충분하다.
    """
    prefix = cfg["ref_prefix"]
    rows = conn.execute("SELECT ref FROM idea WHERE ref LIKE ?", (prefix + "-%",))
    top = 0
    for (ref,) in rows:
        m = re.fullmatch(re.escape(prefix) + r"-(\d+)", ref)
        if m:
            top = max(top, int(m.group(1)))
    return f"{prefix}-{top + 1:0{cfg['ref_digits']}d}"


def get_idea(conn: sqlite3.Connection, ref: str, allow_deleted: bool = False) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM idea WHERE ref = ?", (ref,)).fetchone()
    if row is None:
        raise ClioError("idea_not_found", f"글감을 찾을 수 없습니다: {ref}")
    if row["deleted_at"] and not allow_deleted:
        raise ClioError("idea_deleted", f"삭제된 글감입니다: {ref}")
    return row


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM raw_entry WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise ClioError("entry_not_found", f"원문 기록을 찾을 수 없습니다: {entry_id}")
    return row


def touch_idea(conn: sqlite3.Connection, idea_id: int, ts: str) -> None:
    conn.execute("UPDATE idea SET updated_at = ? WHERE id = ?", (ts, idea_id))


def log_action(conn, action: str, target: str, payload: dict, ts: str) -> int:
    cur = conn.execute(
        "INSERT INTO action_log(action,target,payload,created_at) VALUES(?,?,?,?)",
        (action, target, json.dumps(payload, ensure_ascii=False), ts),
    )
    return cur.lastrowid


# --------------------------------------------------------------------------
# 태그
# --------------------------------------------------------------------------

def resolve_tag(conn, name: str, create: bool = True) -> int | None:
    name = name.strip()
    if not name:
        return None
    row = conn.execute("SELECT id, alias_of FROM tag WHERE name = ?", (name,)).fetchone()
    if row:
        return row["alias_of"] or row["id"]
    if not create:
        return None
    return conn.execute("INSERT INTO tag(name) VALUES(?)", (name,)).lastrowid


def idea_tags(conn, idea_id: int) -> list[str]:
    return [
        r["name"]
        for r in conn.execute(
            "SELECT t.name FROM idea_tag it JOIN tag t ON t.id = it.tag_id "
            "WHERE it.idea_id = ? ORDER BY t.name",
            (idea_id,),
        )
    ]


# --------------------------------------------------------------------------
# 검색 인덱스 (PRD §7.1)
# --------------------------------------------------------------------------

def reindex(conn, idea_id: int) -> None:
    idea = conn.execute("SELECT * FROM idea WHERE id = ?", (idea_id,)).fetchone()
    if idea is None:
        conn.execute("DELETE FROM search_fts WHERE rowid = ?", (idea_id,))
        return
    raws = " ".join(
        r["body"]
        for r in conn.execute(
            "SELECT body FROM raw_entry WHERE idea_id = ? AND redacted_at IS NULL",
            (idea_id,),
        )
    )
    rev = ""
    if idea["latest_revision_id"]:
        row = conn.execute(
            "SELECT body FROM revision WHERE id = ?", (idea["latest_revision_id"],)
        ).fetchone()
        rev = row["body"] if row else ""
    tags = " ".join(idea_tags(conn, idea_id))
    conn.execute("DELETE FROM search_fts WHERE rowid = ?", (idea_id,))
    conn.execute(
        "INSERT INTO search_fts(rowid, ref, title, tags, raw_body, revision_body) "
        "VALUES(?,?,?,?,?,?)",
        (idea_id, idea["ref"], idea["title"], tags, raws, rev),
    )


_PUNCT = re.compile(r"[^\w가-힣]+", re.UNICODE)
_HANGUL = re.compile(r"[가-힣]")

# 어절 끝에 붙는 조사·어미. 긴 것부터 검사한다.
_PARTICLES = sorted([
    "에서는", "에게서", "이라는", "라는", "으로는", "에서", "에게", "으로", "부터",
    "까지", "처럼", "보다", "마다", "조차", "이다", "한다", "하는", "하고", "했다",
    "이란", "라도", "이나", "든지", "지만", "면서", "이며", "으며", "에는", "에도",
    "은", "는", "이", "가", "을", "를", "에", "의", "와", "과", "도", "만", "로",
    "며", "고", "다", "인", "된", "될", "한", "할",
], key=len, reverse=True)


def tokenize(text: str) -> list[str]:
    """질의 토큰. PRD §7.1의 '2자 이상 어절' 기준."""
    text = unicodedata.normalize("NFC", text or "")
    parts = [p for p in _PUNCT.split(text) if p]
    return [p for p in parts if len(p) >= 2]


def stems(token: str) -> set[str]:
    """한국어 조사·어미 변화를 흡수한 어간 후보.

    형태소 분석기 없이(D3: 표준 라이브러리만) 다루기 위한 근사다.
    '기억은', '기억하는', '기억을'이 모두 '기억'을 공유하도록 만든다.
    2자 이상만 남기므로 과도한 단축으로 인한 오매칭은 제한된다.
    """
    out = {token}
    if _HANGUL.search(token):
        for suf in _PARTICLES:
            if len(token) > len(suf) + 1 and token.endswith(suf):
                out.add(token[: -len(suf)])
                break
        if len(token) >= 3:
            out.add(token[:-1])
            out.add(token[:2])
    return {s for s in out if len(s) >= 2}


def hits(token: str, haystack: str) -> bool:
    """어간 후보 중 하나라도 포함되면 일치로 본다."""
    return any(s in haystack for s in stems(token))


def fts_query(tokens: list[str]) -> str:
    """한국어는 조사가 붙으므로 가장 짧은 어간으로 prefix 매칭한다."""
    terms = []
    for t in tokens:
        stem = min(stems(t), key=len).replace('"', "")
        if stem:
            terms.append(f'"{stem}"*')
    return " OR ".join(dict.fromkeys(terms))


def score_candidates(conn, cfg: dict, text: str, limit: int = 20,
                     exclude_idea_id: int | None = None,
                     include_archived: bool = True) -> list[dict]:
    """PRD §7.1의 가중합으로 유사 글감 후보를 매긴다.

        score = Σ(가중 × 신호) × 상태계수

    반환 항목에는 판단 근거(§7.2)를 함께 담는다.
    """
    tokens = tokenize(text)
    if not tokens:
        return []
    w = cfg["search_weights"]
    factor = cfg["state_factor"]
    now = parse_iso(now_iso(cfg))
    half = max(1, int(cfg["recency_halflife_days"]))

    # FTS 후보 수집 (bm25는 음수이며 절댓값이 클수록 관련성이 높다)
    fts_raw: dict[int, float] = {}
    try:
        q = fts_query(tokens)
        if q:
            for r in conn.execute(
                "SELECT rowid AS rid, bm25(search_fts) AS b FROM search_fts "
                "WHERE search_fts MATCH ? LIMIT 200",
                (q,),
            ):
                fts_raw[r["rid"]] = -float(r["b"])
    except sqlite3.OperationalError:
        fts_raw = {}
    fts_max = max(fts_raw.values()) if fts_raw else 0.0

    sql = "SELECT * FROM idea WHERE deleted_at IS NULL"
    if not include_archived:
        sql += " AND storage_state <> 'archived'"
    ideas = list(conn.execute(sql))

    matched_ids: set[int] = set()
    prelim: list[dict] = []
    for idea in ideas:
        if exclude_idea_id and idea["id"] == exclude_idea_id:
            continue
        title = unicodedata.normalize("NFC", idea["title"])
        tags = [unicodedata.normalize("NFC", t) for t in idea_tags(conn, idea["id"])]
        bodies = " ".join(
            r["body"]
            for r in conn.execute(
                "SELECT body FROM raw_entry WHERE idea_id = ? AND redacted_at IS NULL",
                (idea["id"],),
            )
        )
        if idea["latest_revision_id"]:
            rv = conn.execute(
                "SELECT body FROM revision WHERE id = ?", (idea["latest_revision_id"],)
            ).fetchone()
            if rv:
                bodies += " " + rv["body"]
        bodies = unicodedata.normalize("NFC", bodies)

        hit_title = [t for t in tokens if hits(t, title)]
        hit_tags = [t for t in tokens if any(hits(t, g) or g in t for g in tags)]
        hit_sub = [t for t in tokens if hits(t, bodies)]

        s_title = len(hit_title) / len(tokens)
        s_tag = len(hit_tags) / len(tokens)
        s_sub = len(hit_sub) / len(tokens)
        s_fts = (fts_raw.get(idea["id"], 0.0) / fts_max) if fts_max > 0 else 0.0

        age_days = max(0.0, (now - parse_iso(idea["updated_at"])).total_seconds() / 86400.0)
        s_recent = 0.5 ** (age_days / half)

        base = (
            w["title"] * s_title
            + w["tag"] * s_tag
            + w["substring"] * s_sub
            + w["fts"] * s_fts
            + w["recency"] * s_recent
        )
        if base <= 0:
            continue
        if s_title or s_tag or s_sub or s_fts:
            matched_ids.add(idea["id"])
        prelim.append({
            "idea": idea,
            "base": base,
            "signals": {"title": s_title, "tag": s_tag, "substring": s_sub,
                        "fts": s_fts, "recency": s_recent, "relation": 0.0},
            "common_terms": sorted(set(hit_title + hit_tags + hit_sub)),
            "common_tags": [g for g in tags if any(hits(t, g) or g in t for t in tokens)],
        })

    out = []
    for item in prelim:
        idea = item["idea"]
        rel = conn.execute(
            "SELECT 1 FROM idea_relation WHERE (from_idea_id = ? AND to_idea_id IN "
            "(SELECT value FROM json_each(?))) OR (to_idea_id = ? AND from_idea_id IN "
            "(SELECT value FROM json_each(?))) LIMIT 1",
            (idea["id"], json.dumps(list(matched_ids)),
             idea["id"], json.dumps(list(matched_ids))),
        ).fetchone()
        s_rel = 1.0 if rel else 0.0
        item["signals"]["relation"] = s_rel
        score = (item["base"] + w["relation"] * s_rel) * factor.get(idea["storage_state"], 1.0)
        out.append({
            "ref": idea["ref"],
            "idea_id": idea["id"],
            "title": idea["title"],
            "storage_state": idea["storage_state"],
            "dev_stage": idea["dev_stage"],
            "updated_at": idea["updated_at"],
            "score": round(min(1.0, score), 4),
            "signals": {k: round(v, 4) for k, v in item["signals"].items()},
            "common_terms": item["common_terms"],
            "common_tags": item["common_tags"],
        })
    out.sort(key=lambda x: (-x["score"], ref_sort_key(x["ref"])))
    return out[:limit]


def decide(cfg: dict, cands: list[dict]) -> dict:
    """PRD §6.3 확신도 분기. 판단 결과만 돌려주고 실행하지는 않는다."""
    top = cfg["auto_link_threshold"]
    ask = cfg["ask_threshold"]
    margin = cfg["auto_link_margin"]
    floor = cfg.get("mention_threshold", 0.0)
    if not cands:
        return {"action": "new_idea", "reason": "후보 없음", "candidates": []}
    best = cands[0]
    second = cands[1]["score"] if len(cands) > 1 else 0.0
    shown = [c for c in cands if c["score"] >= floor][: cfg["candidate_limit"]]
    if best["score"] >= top and (best["score"] - second) >= margin:
        return {"action": "auto_link", "reason": f"유사도 {best['score']} ≥ {top}",
                "target": best["ref"], "candidates": shown}
    if best["score"] >= ask:
        return {"action": "ask", "reason": f"유사도 {best['score']} ≥ {ask}",
                "candidates": shown}
    return {"action": "new_idea", "reason": f"최고 유사도 {best['score']} < {ask}",
            "candidates": shown}


# --------------------------------------------------------------------------
# CLI 공통 (PRD §10 — JSON 출력, 실패 시 종료 코드 1)
# --------------------------------------------------------------------------

def emit(payload: dict) -> None:
    payload.setdefault("ok", True)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def read_body(args) -> str:
    """본문은 원문 그대로 보존한다. 앞뒤 개행만 정리하고 내부는 손대지 않는다."""
    if getattr(args, "body_file", None):
        text = Path(args.body_file).read_text(encoding="utf-8")
    elif getattr(args, "body", None) is not None:
        text = args.body
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise ClioError("no_body", "본문이 없습니다. --body, --body-file 또는 표준입력을 사용하세요.")
    text = text.strip("\n")
    if not text.strip():
        raise ClioError("empty_body", "본문이 비어 있습니다. 저장하지 않았습니다.")
    return text


def run_cli(build_parser, dispatch) -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "cmd", None):
        parser.print_help()
        sys.exit(2)
    try:
        cfg = load_config()
        result = dispatch(args, cfg)
        emit(result if isinstance(result, dict) else {"result": result})
    except ClioError as e:
        print(json.dumps(
            {"ok": False, "error": {"code": e.code, "message": e.message, **e.extra}},
            ensure_ascii=False, indent=2))
        sys.exit(1)
    except sqlite3.Error as e:
        print(json.dumps(
            {"ok": False, "error": {"code": "db_error", "message": str(e)}},
            ensure_ascii=False, indent=2))
        sys.exit(1)
    except OSError as e:
        # 데이터 디렉터리에 접근할 수 없는 경우 등. 성공한 것처럼 응답하지 않는다 (TC9).
        print(json.dumps(
            {"ok": False, "error": {"code": "storage_unavailable", "message": str(e)}},
            ensure_ascii=False, indent=2))
        sys.exit(1)


# --------------------------------------------------------------------------
# 저장 지시어 (PRD §6.1)
# --------------------------------------------------------------------------
#
# 지시어 판정은 결정론적 규칙이므로 판단 계층이 아니라 저장 계층이 담당한다.
# 이렇게 해야 "지시어가 없는데 저장됐다"와 "지시어가 있는데 무시됐다"가
# 모두 테스트로 검증된다 (TC13, TC14, TC16).

TRIGGER_RE = re.compile(
    r"^[ \t]*(?P<word>글감|아이디어|ㄱ)"      # 별칭
    r"[ \t]*(?P<plus>\+)?"                     # 보충 지시
    r"[ \t]*(?P<ref>[A-Za-z]+-\d+)?"           # 지정 글감
    r"[ \t]*:[ \t]*"                            # 구분자
)

RETRO_RE = re.compile(r"^[ \t]*(방금|아까|직전)[^\n]{0,30}?저장")


def parse_trigger(message: str) -> dict:
    """메시지에서 저장 지시어를 판정한다.

    - 첫 줄의 첫 토큰에만 유효하다. 문장 중간의 '글감:'은 지시어가 아니다.
    - 본문은 지시어 토큰 이후 전부이며, 줄바꿈과 빈 줄을 그대로 보존한다.
    - 저장되는 본문에 지시어 토큰은 포함하지 않는다.
    """
    if message is None:
        return {"trigger": False, "kind": None, "reason": "빈 메시지"}
    text = message.lstrip("\n")
    m = TRIGGER_RE.match(text)
    if not m:
        if RETRO_RE.match(text):
            return {"trigger": True, "kind": "retro", "idea_ref": None, "body": None,
                    "reason": "직전 메시지 소급 저장 요청"}
        return {"trigger": False, "kind": None, "idea_ref": None, "body": None,
                "reason": "저장 지시어가 없습니다. 일반 대화로 처리합니다."}
    body = text[m.end():].strip("\n")
    ref = m.group("ref").upper() if m.group("ref") else None
    if m.group("plus"):
        kind = "supplement"
    elif ref:
        kind = "target"
    else:
        kind = "new"
    return {"trigger": True, "kind": kind, "idea_ref": ref, "body": body,
            "empty": not body.strip(),
            "reason": f"지시어 '{m.group('word')}' 인식"}
