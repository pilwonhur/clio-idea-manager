# Clio 아이디어 관리 시스템 PRD

문서 버전: 1.3
작성일: 2026-08-17 (최종 수정 2026-08-18)
상태: **1~4단계 구현 완료.** 구현체는 `clio-idea-manager/`, 테스트 25건 통과 (§13).
변경(1.1): 저장 진입 방식을 `글감:` 지시어 기반으로 확정 (§4.1, §6.1, §6.2)
변경(1.2): 잔여 결정 항목 D1·D2·D3·D5·D6·D7을 모두 확정하고 본문에 반영 (§4.2, §5.2, §6.3, §7.1, §10, §11, §14)
변경(1.3): 구현 결과를 반영 — 지시어 판정을 저장 계층으로 이동(§6.1), 한국어 어간 처리 추가(§7.1), FTS 구성과 `meta` 테이블 반영(§5.2), `parse` 명령 추가(§10), `mention_threshold` 추가(§11), 단계별 구현 상태 기록(§12)
근거 문서: `clio-idea-channel-draft.md`(초안), `clio-idea-management-design-review.md`(검토안)

---

## 1. 개요

### 1.1 배경

교수님은 일상 중 떠오르는 짧은 아이디어와 키워드를 Discord 채널을 통해 Clio에게 전달한다. 현재는 이러한 입력이 대화 로그에만 남아 축적되지 않으며, 시간이 지나면 어떤 생각이 어떻게 발전했는지 추적할 수 없다.

### 1.2 제품 정의

Clio 아이디어 관리 시스템은 **원문을 불변으로 보존하면서 아이디어의 성장·연결·합성을 추적하는 글감 관리 시스템**이다. 단순 메모장이 아니라, 하나의 생각이 여러 시점의 원문으로 누적되고, 정제본으로 다듬어지고, 다른 생각과 연결되어 새로운 글감으로 파생되는 과정을 데이터로 남기는 것이 목적이다.

### 1.3 설계 철학

1. **포착은 명시적 지시로만 시작하되, 지시받은 뒤에는 분류보다 우선한다.** Clio는 채널의 범용 비서이므로 모든 메시지를 저장하지 않는다. 교수님이 저장을 선언한 메시지에 한해, 어디에 속할지 정하기 전에 원문부터 저장한다.
2. **원문과 Clio의 산출물은 절대 섞이지 않는다.** 원문은 append-only, 정제본은 별도 계층.
3. **단일 원본(single source of truth)을 명확히 한다.** 같은 사실이 두 곳에 저장되지 않는다.
4. **"저장했다"는 말은 트랜잭션 커밋 이후에만 한다.**
5. **판단은 LLM이, 저장은 결정론적 도구가 한다.**

---

## 2. 목표와 비목표

### 2.1 목표

| # | 목표 | 성공 판단 기준 |
|---|---|---|
| G1 | 아이디어 유실 0건 | 저장 지시된 원문은 어떤 중단 상황에서도 DB에 남거나, 저장되지 않았다는 사실이 응답에서 드러난다 |
| G2 | 포착 마찰 최소화 | 짧은 입력 1건 처리에 교수님의 추가 응답이 평균 0.5회 이하 |
| G3 | 원문 무결성 | 저장된 Raw Entry의 본문 해시가 영구 불변(§9 예외 절차 제외) |
| G4 | 재발견 가능성 | 한국어 키워드·부분 문장으로 목표 글감을 상위 5건 내에서 찾는다 |
| G5 | 데이터 이식성 | 언제든 Markdown·JSON 전량 내보내기가 가능하고 복구가 검증된다 |

### 2.2 비목표 (v1 범위 밖)

- 다중 사용자 지원, 권한 관리
- 서버형 DB(PostgreSQL 등) 및 원격 동기화 서비스
- 웹 UI, 모바일 앱
- 초기 단계의 임베딩 기반 의미 검색 (5단계에서 필요성 검증 후 도입)
- 글 자체의 집필·편집(글감 관리까지가 범위이며 완성 원고 작성은 별도)
- 저장 지시 없는 일반 대화의 자동 저장 (§6.1)
- Discord 브리지 자체의 수정·대체 (제3자 도구로 전제)

---

## 3. 사용자 및 사용 시나리오

**주 사용자:** 교수님 1인. 입력 채널은 Discord, 그 외 채널(CLI, 파일 임포트)로 확장 가능한 구조를 유지한다.

**핵심 시나리오**

| ID | 시나리오 | 기대 동작 |
|---|---|---|
| S0 | 글감과 무관한 업무를 요청한다 | 평소대로 응답하고 데이터베이스를 변경하지 않는다 |
| S1 | 이동 중 떠오른 한 문장을 `글감:`으로 보낸다 | 즉시 저장 + 연결 후보 1줄 안내, 질문 최소화 |
| S2 | `글감 CLIO-0007:` 로 글감을 지목한다 | 질문 없이 해당 글감에 Raw Entry 추가 |
| S3 | `글감+:` 로 직전 입력을 보충한다 | 직전 글감에 연결 + 결과 통지 |
| S9 | 지시어 없이 보낸 글을 뒤늦게 저장하고 싶다 | "방금 거 글감으로 저장해"로 소급 저장 |
| S4 | "글감 목록 보여줘" | active 글감을 최근 업데이트순 20건 표시 |
| S5 | "CLIO-0012 보여줘" | 날짜별 원문 전체 + 최신 정제본 + 관계 표시 |
| S6 | "0003, 0007, 0012 합쳐줘" | 새 번호 발급, 부모 관계 기록, 원본 글감 유지 |
| S7 | "방금 연결 취소해" | 직전 작업 1건 되돌리기 + 되돌림 이력 기록 |
| S8 | "기억 관련 글감 찾아줘" | 한국어 검색 + 유사 판단 근거 표시 |

---

## 4. 시스템 구조

### 4.1 2계층 분리

```
[Discord] → [브리지: 제3자, 수정 불가] → [Clio 스킬: 판단 계층]
                                              ↓ (§6.1 저장 지시어가 있을 때만)
                                        [관리 도구: 결정론적 저장 계층] → [SQLite]
                                                                          ↓
                                                     [Markdown / JSON 내보내기·백업]
```

**브리지 전제:** Discord 메시지를 Clio에게 전달하고 응답을 채널에 올리는 계층은 이미 상시 동작 중인 제3자 도구이며, 이 프로젝트에서 수정하지 않는다. 따라서 저장의 진입점은 브리지가 아니라 Clio 스킬이고, 진입 조건은 §6.1의 저장 지시어다. 브리지가 어떤 메타데이터를 넘겨주는지에 시스템이 의존하지 않도록, §10의 2단 멱등성 규칙을 채택한다(D7).

- **판단 계층 (SKILL.md):** 언제 포착하고, 언제 질문하며, 무엇을 어떻게 출력할지 결정. SQL을 직접 생성하지 않는다.
- **저장 계층 (scripts/):** 검증된 명령만 노출. 스키마 제약, 트랜잭션, 중복 방지, 무결성 검사를 책임진다.

**LLM은 임의 SQL을 실행하지 않는다.** 모든 쓰기는 정의된 커맨드를 통해서만 이루어진다.

**구현 언어 (D3): Python 3.11 이상, 표준 라이브러리만 사용한다.** `sqlite3`, `hashlib`, `json`, `argparse`, `datetime`, `zoneinfo`로 이 PRD의 전 기능을 구현할 수 있다. 외부 패키지 의존이 없으면 가상환경 관리나 버전 충돌 없이 어느 시점에 실행해도 동작하며, 이는 수년 단위로 축적될 데이터를 다루는 도구에 중요한 성질이다. 5단계 임베딩 검색을 도입할 때에 한해 의존성 추가를 재검토한다.

### 4.2 디렉터리 배치

스킬 코드와 데이터를 물리적으로 분리한다. 스킬 재설치·업데이트가 데이터에 영향을 주지 않아야 한다.

```
~/.claude/skills/clio-idea-manager/     # 스킬 (교체 가능)
├── SKILL.md
├── scripts/
│   ├── idea_store          # 쓰기: 원문 저장, 글감 생성/연결, revision, 관계, 상태
│   ├── idea_search         # 읽기: 목록, 상세, 검색, 유사 후보
│   ├── idea_export         # 내보내기: Markdown / JSON
│   └── idea_check          # 무결성 검사, 백업, 복구 시험
├── references/
│   ├── schema.md
│   ├── conversation-rules.md
│   └── recovery-policy.md
└── evals/evals.json

~/ClioData/                              # 데이터 (영속, Dropbox 밖)
├── config.json                          # 임계값·경로 등 운영 설정
├── ideas.db                             # SQLite 원본
├── attachments/                         # 첨부파일 원본 사본 (sha256 앞 2자리로 분산)
│   └── ab/abcd…ef.png
├── exports/                             # Markdown / JSON 내보내기
└── backups/                             # 타임스탬프 백업 + redaction_manifest.json

~/Dropbox/ClioBackup/                    # 백업 사본만 동기화 (DB 원본은 두지 않음)
```

**데이터 위치를 Dropbox 밖에 두는 이유 (D2):** SQLite는 파일 잠금으로 동시성을 제어하는데, Dropbox 같은 동기화 폴더는 잠금 상태를 알지 못한 채 파일을 교체하거나 충돌 사본을 만들 수 있다. 쓰기 중인 DB에 이런 일이 생기면 파일 자체가 손상된다. 따라서 원본은 로컬 `~/ClioData`에 두고, **백업 산출물만** Dropbox로 복사한다. 백업은 이미 정지된 스냅샷이므로 동기화해도 안전하다.

**첨부파일 보존 (D6):** Discord CDN 링크는 만료되거나 채널 삭제 시 소실되므로 **원본 파일을 내려받아 복사 보관**한다. 파일명은 내용 해시(sha256)로 저장하여 같은 파일의 중복 저장을 자동으로 막고, 원래 파일명은 DB에 별도로 기록한다.

---

## 5. 데이터 모델

### 5.1 설계 원칙

| 항목 | 단일 원본 | 파생(계산) |
|---|---|---|
| 현재 정제본 | `revision` 테이블 | `idea.latest_revision_id` 참조 |
| 부모·파생 관계 | `idea_relation` | 조회 시 계산 |
| 키워드 | `tag` + `idea_tag` | 표시용 문자열 |
| 원문 | `raw_entry` (append-only) | 목록의 미리보기 |
| 수정일 목록 | `raw_entry.captured_at` + `revision.created_at` | 조회 시 집계 |

### 5.2 스키마

```sql
-- 글감 본체: '현재 상태'만 보유
CREATE TABLE idea (
  id                 INTEGER PRIMARY KEY,           -- 내부 정수 키
  ref                TEXT UNIQUE NOT NULL,          -- 'CLIO-0001' 표시용, 재사용 금지
  title              TEXT NOT NULL,
  storage_state      TEXT NOT NULL DEFAULT 'inbox'  -- 보관 상태
                     CHECK (storage_state IN ('inbox','active','archived')),
  dev_stage          TEXT NOT NULL DEFAULT 'fragment' -- 발전 단계
                     CHECK (dev_stage IN ('fragment','developed','outlined','drafted','published')),
  latest_revision_id INTEGER REFERENCES revision(id),
  created_at         TEXT NOT NULL,                 -- ISO8601, 초 단위, Asia/Seoul 오프셋 포함
  updated_at         TEXT NOT NULL,
  deleted_at         TEXT                           -- tombstone (영구삭제 시각)
);

-- 원문: 불변, append-only
CREATE TABLE raw_entry (
  id             INTEGER PRIMARY KEY,
  idea_id        INTEGER REFERENCES idea(id),       -- NULL 허용 = unassigned
  assign_state   TEXT NOT NULL DEFAULT 'unassigned'
                 CHECK (assign_state IN ('unassigned','assigned')),
  body           TEXT NOT NULL,                     -- 입력 원문 그대로 (맞춤법·미완성 포함)
  body_hash      TEXT NOT NULL,                     -- SHA-256, 무결성 검사용
  entry_type     TEXT NOT NULL DEFAULT 'idea'
                 CHECK (entry_type IN ('idea','keyword','supplement','quote','question')),
  captured_at    TEXT NOT NULL,                     -- 원본 작성 시각
  ingested_at    TEXT NOT NULL,                     -- 시스템 수집 시각
  source_id      INTEGER REFERENCES source(id),
  redacted_at    TEXT,                              -- 영구삭제 시 본문 제거 시각
  UNIQUE (body_hash, source_id)
);

-- 출처: Discord 외 채널로 일반화
CREATE TABLE source (
  id               INTEGER PRIMARY KEY,
  platform         TEXT NOT NULL,                   -- 'discord' | 'cli' | 'import'
  guild_id         TEXT,
  channel_id       TEXT,
  thread_id        TEXT,
  message_id       TEXT,
  author_id        TEXT,
  reply_to_id      TEXT,
  edited           INTEGER NOT NULL DEFAULT 0,
  raw_payload      TEXT,                            -- 원본 메시지 JSON 보존
  UNIQUE (platform, message_id)                     -- 재시도 시 중복 수집 방지
);
-- D7: message_id는 NULL 허용. 브리지가 ID를 넘겨주지 않아도 시스템이 동작해야 한다.
-- SQLite는 NULL을 서로 다른 값으로 취급하므로 위 UNIQUE는 ID가 있을 때만 작동하고,
-- 없을 때의 중복 판정은 §10의 대체 규칙(도구 계층)이 담당한다.

-- 첨부파일: 원본을 복사 보관 (D6)
CREATE TABLE attachment (
  id           INTEGER PRIMARY KEY,
  raw_entry_id INTEGER NOT NULL REFERENCES raw_entry(id),
  filename     TEXT NOT NULL,                    -- 원래 파일명
  mime_type    TEXT,
  byte_size    INTEGER,
  sha256       TEXT NOT NULL,                    -- 내용 해시 = 저장 파일명
  stored_path  TEXT NOT NULL,                    -- attachments/<앞2자리>/<sha256><확장자>
  source_url   TEXT,                             -- 원본 URL (만료 전제, 참고용)
  fetched_at   TEXT NOT NULL,
  redacted_at  TEXT,
  UNIQUE (raw_entry_id, sha256)
);

-- 정제본: 불변 이력
CREATE TABLE revision (
  id             INTEGER PRIMARY KEY,
  idea_id        INTEGER NOT NULL REFERENCES idea(id),
  rev_no         INTEGER NOT NULL,                  -- 글감 내 1부터 증가
  body           TEXT NOT NULL,
  change_summary TEXT NOT NULL,
  author         TEXT NOT NULL DEFAULT 'clio'       -- 'clio' | 'user'
                 CHECK (author IN ('clio','user')),
  created_at     TEXT NOT NULL,
  UNIQUE (idea_id, rev_no)
);

-- 관계: 부모·파생의 단일 원본
CREATE TABLE idea_relation (
  id            INTEGER PRIMARY KEY,
  from_idea_id  INTEGER NOT NULL REFERENCES idea(id),
  to_idea_id    INTEGER NOT NULL REFERENCES idea(id),
  rel_type      TEXT NOT NULL
                CHECK (rel_type IN ('similar','related','expands','contradicts','derived_from')),
  symmetric     INTEGER NOT NULL DEFAULT 0,         -- similar/related=1, derived_from/expands=0
  created_at    TEXT NOT NULL,
  created_by    TEXT NOT NULL CHECK (created_by IN ('user','clio')),
  rationale     TEXT,                               -- 관계 설정 근거
  confidence    REAL,                               -- 0.0~1.0, Clio 판단 시
  confirmed_by_user INTEGER NOT NULL DEFAULT 0,
  CHECK (from_idea_id <> to_idea_id),
  UNIQUE (from_idea_id, to_idea_id, rel_type)
);

-- 태그
CREATE TABLE tag (
  id    INTEGER PRIMARY KEY,
  name  TEXT UNIQUE NOT NULL,
  alias_of INTEGER REFERENCES tag(id)               -- 명칭 통합용
);
CREATE TABLE idea_tag (
  idea_id INTEGER NOT NULL REFERENCES idea(id),
  tag_id  INTEGER NOT NULL REFERENCES tag(id),
  PRIMARY KEY (idea_id, tag_id)
);

-- 작업 로그: 취소(undo)와 감사 추적의 근거
CREATE TABLE action_log (
  id          INTEGER PRIMARY KEY,
  action      TEXT NOT NULL,                        -- 'capture','link','create','revise','merge','reassign','delete',...
  target      TEXT NOT NULL,                        -- 'idea:12', 'raw_entry:88'
  payload     TEXT NOT NULL,                        -- 되돌리기에 필요한 이전 상태 JSON
  undone_at   TEXT,
  created_at  TEXT NOT NULL
);

-- 스키마 버전 등 운영 메타
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- 한국어 검색 인덱스 (rowid = idea.id)
CREATE VIRTUAL TABLE search_fts USING fts5(
  ref, title, tags, raw_body, revision_body,
  tokenize='unicode61 remove_diacritics 2'
);
```

**FTS 구성에 관한 결정:** 초안에서는 저장 공간을 아끼려 `content=''`(contentless) 방식을 고려했으나,
contentless 테이블은 행 갱신·삭제가 제약되어 글감이 수정될 때마다 인덱스를 안정적으로 다시 쓰기 어렵다.
검색 인덱스는 원본이 아니라 파생이므로 언제든 재생성할 수 있고 중복 저장의 비용도 크지 않다.
따라서 일반 FTS5 테이블을 쓰고 글감 변경 시 해당 행만 삭제 후 재삽입한다.

**DDL의 정본은 `clio-idea-manager/scripts/clio_core.py`의 `SCHEMA_SQL`이다.** 위 내용은 설계 의도를
설명하기 위한 것이며, 실제 실행되는 정의와 어긋날 경우 코드가 우선한다.

**대칭 관계 처리:** `similar`, `related`는 저장 시 `from_idea_id < to_idea_id`로 정규화하고 `symmetric=1`을 부여한다. `derived_from`, `expands`, `contradicts`는 방향을 그대로 보존한다.

**합성 글감:** 새 `idea`를 발급하고 각 부모에 대해 `(new, parent, 'derived_from')` 관계를 생성한다. 부모 글감은 삭제·흡수하지 않는다.

### 5.3 상태 2축

| 축 | 값 | 의미 |
|---|---|---|
| 보관 상태 | `inbox` | 아직 분류하지 않음 |
| | `active` | 발전 중 (기본 목록 노출) |
| | `archived` | 보존만 (기본 목록 숨김, 검색으로는 조회 가능) |
| 발전 단계 | `fragment` | 단편 |
| | `developed` | 정제본 존재 |
| | `outlined` | 개요 수준 |
| | `drafted` | 초안 작성 |
| | `published` | 발표·출판 |

두 축은 독립이다. `archived + drafted`(초안까지 쓰고 묵혀둔 글감), `active + published`(출판 후에도 계속 발전시키는 글감) 모두 유효하다.

---

## 6. 핵심 동작: 포착 파이프라인

### 6.1 저장 지시어

Clio는 해당 채널의 범용 비서이므로 일정 정리, 질의응답 등 글감과 무관한 요청도 받는다. **저장은 교수님이 명시적으로 지시한 메시지에서만 일어난다.** Clio가 "이건 글감 같다"를 추측해 저장하지 않으며, 추측 오판이라는 실패 모드를 설계에서 제거한다.

기본 지시어는 메시지 첫 줄 첫머리의 `글감:` 접두어다.

| 형식 | 동작 |
|---|---|
| `글감: <본문>` | 새 Raw Entry로 저장한 뒤 연결 판단 (§6.3) |
| `글감 CLIO-0007: <본문>` | 지정 글감에 즉시 추가, 확인 질문 없음 |
| `글감+: <본문>` | 직전에 처리한 글감에 보충으로 추가 |
| `방금 거 글감으로 저장해` | 직전 교수님 메시지를 소급 저장 |

**인식 규칙**

- 별칭: `글감:`, `아이디어:`, `ㄱ:` — 앞뒤 공백은 무시한다.
- 위치: 메시지 **첫 줄의 첫 토큰**에만 유효하다. 문장 중간에 나온 "글감:"은 지시어가 아니다.
- 본문 범위: 지시어 토큰 다음부터 메시지 끝까지 전부. 여러 줄, 줄바꿈, 빈 줄을 원문 그대로 보존한다.
- 저장되는 본문에 지시어 토큰 자체는 **포함하지 않는다**.
- 지시어가 없는 메시지는 Clio가 평소대로 응답하고 데이터베이스를 일절 변경하지 않는다.
- 지시어만 있고 본문이 비었으면 저장하지 않고 되묻는다.

**소급 저장의 한계:** `방금 거 글감으로 저장해`는 브리지가 직전 메시지 본문을 대화 맥락으로 전달한다는 전제에서만 동작한다. 맥락에서 원문을 그대로 복원할 수 없으면 저장하지 않고 그 사실을 알린다. 기억에 의존해 재구성한 문장을 원문으로 저장하는 것은 금지한다.

**판정 주체 (구현 결정):** 위 규칙은 전부 결정론적이므로 판단 계층이 아니라 **저장 계층이 강제한다.** `idea_store capture --raw-message <메시지 원본>`이 지시어를 직접 판정하고, 지시어가 없으면 `no_trigger` 오류를 내며 **아무것도 쓰지 않는다.** 판정만 따로 확인하려면 `idea_store parse --message ...`를 쓴다.

이렇게 하면 "지시어가 없는데 저장됐다"와 "지시어가 있는데 무시됐다"가 모두 자동 테스트로 검증된다(TC13, TC14, TC16). LLM이 지시어 인식을 매번 다시 판단하지 않으므로 동작이 흔들리지 않는다.

### 6.2 처리 순서 (순서 자체가 요구사항이다)

```
1. 메시지 수신
2. 저장 지시어 판정 → 없으면 일반 대화로 처리하고 종료 (DB 무변경)
3. [트랜잭션 A] source + raw_entry 저장 (assign_state='unassigned')  ← 커밋 완료
4. 유사 글감 검색 및 확신도 산출
5. 확신도별 분기 (§6.3)
6. [트랜잭션 B] raw_entry.idea_id 지정 + assign_state='assigned' + idea.updated_at 갱신
7. 결과 응답
```

3단계 커밋 이후에는 5~6단계가 실패하거나 교수님이 응답하지 않아도 원문은 보존된다. 미배정 원문은 "미분류 목록"으로 언제든 조회·처리할 수 있다.

**응답이 곧 영수증이다.** 저장 지시를 보냈는데 `CLIO-00xx` 번호가 포함된 확인 응답이 오지 않았다면, 그것은 저장되지 않은 것이다. 이 대응 관계가 교수님이 저장 여부를 확인할 수 있는 유일한 수단이므로, Clio는 **커밋 전에 어떤 경우에도 글감 번호를 언급하지 않는다.** 이 규칙에 의해 "답변은 왔는데 기록은 없는" 조용한 누락이 구조적으로 발생할 수 없다.

### 6.3 확신도 기반 확인 정책

| 조건 | 동작 | 질문 |
|---|---|---|
| `글감 CLIO-0007:` 로 번호 명시 | 해당 글감에 즉시 추가 | 없음 |
| `글감+:` 보충 지시 | 직전 처리 글감에 연결 | 없음, 결과만 통지 |
| 유사도 ≥ 상위 임계 | 자동 연결 | 없음, 결과 + 취소 방법 안내 |
| 하위 임계 ≤ 유사도 < 상위 임계 | 후보 1~3개 제시 | 확인 1회 |
| 유사도 < 하위 임계 | 새 글감 생성 | 없음, 결과 + 취소 방법 안내 |

**확정 임계값 (D1).** 유사도 점수는 §7.1의 가중합을 0.0~1.0으로 정규화한 값이다.

| 설정 키 | 초기값 | 의미 |
|---|---|---|
| `auto_link_threshold` | `1.01` | 자동 연결 기준. 초기값은 **도달 불가능한 값으로 두어 자동 연결을 사실상 비활성화**한다 |
| `auto_link_margin` | `0.15` | 자동 연결 시 1위와 2위 후보의 최소 점수 격차 |
| `ask_threshold` | `0.35` | 이 값 이상이면 후보를 제시하고 확인, 미만이면 새 글감 |
| `candidate_limit` | `3` | 확인 시 제시할 최대 후보 수 |

**초기값을 이렇게 두는 이유:** 자동 연결의 오류는 조용하다. 잘못 붙은 원문은 교수님이 그 글감을 다시 열어볼 때까지 발견되지 않고, 그사이 정제본까지 오염될 수 있다. 반면 과도한 확인 질문의 비용은 즉시 드러나고 되돌리기도 쉽다. 따라서 실제 점수 분포를 모르는 상태에서 숫자를 추측해 자동 연결을 켜기보다, **끈 상태로 시작해 실측 후 켠다.**

**조정 절차:** 운영 4주 또는 글감 50건 축적 시점에 `idea_check calibrate`로 그동안의 후보 제시 이력과 교수님의 실제 선택을 대조한다. "1위 후보를 그대로 선택한 비율"이 95% 이상인 점수 구간이 확인되면 `auto_link_threshold`를 그 구간 하한으로 낮춘다. 근거 없이는 낮추지 않는다. 모든 값은 `~/ClioData/config.json`에서 변경하며 코드 수정을 요구하지 않는다.

**미응답 처리:** 확인 질문에 응답이 없으면 해당 Raw Entry는 `unassigned`로 남는다. 임의로 추정 배정하지 않는다.

### 6.4 정제본 생성 시점 (D5)

**정제본은 교수님이 요청할 때만 생성한다.** 원문을 저장하면서 Clio가 자동으로 정제본을 만들거나 갱신하지 않는다.

- 새 원문이 들어와도 기존 정제본은 변하지 않는다. 응답에 "정제본은 아직 변경하지 않았습니다"를 명시한다.
- `글감 CLIO-0012 정리해줘` 같은 명시적 요청이 있을 때만 새 revision을 만든다.
- 포착 응답에 Clio가 붙이는 한 줄 `핵심:` 요약은 **정제본이 아니다.** DB에 저장되지 않는 대화용 확인 문구이며, revision 테이블에 기록되지 않는다.

자동 정제는 두 가지 문제를 만든다. 원문 한 줄마다 revision이 쌓여 이력이 무의미해지고, 교수님이 검토하지 않은 Clio의 해석이 그 글감의 대표 서술로 자리 잡는다. 정제는 판단이 개입하는 작업이므로 명시적 요청을 진입 조건으로 둔다.

### 6.5 취소(undo)

직전 작업 1건은 `action_log`를 근거로 되돌릴 수 있다. 되돌리기는 **삭제가 아니라 역연산 기록**이다. 원문 자체는 어떤 경우에도 취소로 사라지지 않는다.

---

## 7. 검색

### 7.1 초기 방식 (v1)

SQLite FTS5 + 다중 신호 가중 점수 조합. 한국어 기본 토큰화 한계를 보완하기 위해 부분 문자열 매칭을 병행한다.

| 신호 | 가중 | 산출 방식 |
|---|---|---|
| 제목 일치 | 0.30 | 질의어가 제목에 포함된 비율 |
| 태그 일치 | 0.25 | 일치 태그 수 / 질의 추출 태그 수 |
| 원문·정제본 부분 문자열 일치 | 0.20 | 한국어 조사 변화 보완, 2자 이상 어절 기준 |
| FTS5 점수 | 0.15 | `bm25()` 를 0~1로 정규화 |
| 최근성 | 0.05 | 최종 업데이트 기준 90일 반감 |
| 연결 글감 정보 | 0.05 | 관계된 글감이 매칭되면 가산 |

```
score = Σ(가중 × 신호) × 상태계수
상태계수: active 1.0 / inbox 1.0 / archived 0.7
```

**한국어 어간 처리 (구현 결정):** SQLite FTS5의 `unicode61` 토크나이저는 어절을 통째로 자르므로 '기억은'으로 검색하면 '기억하는'을 찾지 못한다. 형태소 분석기는 외부 의존성을 요구하므로(D3 위배) 조사·어미 목록과 접두 절단을 결합한 근사 어간 추출을 쓴다.

```
기억은   → {기억은, 기억}
기억하는 → {기억하는, 기억하, 기억}
망각을   → {망각을, 망각}
```

FTS 질의는 가장 짧은 어간의 prefix로 던지고, 부분 문자열 신호도 어간 기준으로 판정한다. 실측에서 이 처리만으로 동일 주제 글감의 점수가 0.09에서 0.28로 올랐다. 근사이므로 오매칭이 생길 수 있으나, 자동 연결이 초기 비활성이라 확인 질문 단계에서 걸러진다.

가중치는 `~/ClioData/config.json`의 `search_weights`에서 변경할 수 있다. 초기값은 이론적 추정이므로 D1의 조정 절차와 함께 실측 후 재검토한다. archived는 점수를 감점하되 검색 결과에서 배제하지는 않는다(TC6).

### 7.2 유사 근거 표시

검색·연결 후보 제시 시 판단 근거를 반드시 함께 출력한다.

```
CLIO-0007 — 공통어: 기억, 선택
공통 논점: AI가 무엇을 보존할지 판단한다는 문제
```

### 7.3 의미 검색 (5단계, 조건부)

실제 축적된 글감으로 §7.1 방식의 재현율을 평가하고, 미달 사례가 반복 확인될 때만 임베딩 검색을 추가한다. 처음부터 의미 검색에 의존하면 표현은 유사하나 논점이 다른 글감을 잘못 병합할 위험이 있다.

---

## 8. 대화 및 출력 규칙

### 8.1 원칙

1. **저장 결과와 편집 제안을 분리한다.** 어디까지가 사실이고 어디부터가 Clio의 해석인지 형식으로 구분한다.
2. **"저장했습니다"는 커밋 이후에만 말한다.** 실패 시 성공한 것처럼 응답하지 않는다.
3. **원문은 인용 시에도 수정하지 않는다.** 맞춤법 오류·미완성 문장도 그대로 표시한다.
4. **간결하게.** 짧은 포착에 긴 응답을 붙이지 않는다.
5. **확인 응답이 곧 영수증이다.** 저장 지시에 대한 응답에 `CLIO-00xx` 번호가 없으면 저장되지 않은 것이다. 커밋 전에 번호를 말하지 않는다 (§6.2).
6. **지시어가 없으면 저장 이야기를 꺼내지 않는다.** 일반 요청에 "글감으로 저장할까요?"를 덧붙이지 않는다.

### 8.2 표준 응답 형식

**지시어 없는 일반 요청**
```
(글감 저장이 일어나지 않는다. Clio는 해당 요청에 평소대로 응답하고,
 저장 여부를 언급하지 않는다.)
```

**짧은 아이디어 입력 (`글감:` → 새 글감)**
```
CLIO-0021로 원문을 보존했습니다.
핵심: 기억하는 능력보다 선택적으로 잊는 능력이 중요하다는 관점
연결 후보: CLIO-0007
현재는 별도 글감으로 두었습니다.
```

**글감 번호를 지정한 입력 (`글감 CLIO-0007:`)**
```
CLIO-0007에 새 원문을 추가했습니다.
정제본은 아직 변경하지 않았습니다.
```

**정제본 변경**
```
원문은 그대로 보존하고 정제본 revision 4를 만들었습니다.
변경: '기억 용량'보다 '선택 기준'이 핵심이라는 주장으로 초점을 좁혔습니다.
```

**저장 실패** — 글감 번호를 절대 포함하지 않는다
```
저장에 실패했습니다. 원문은 아직 기록되지 않았습니다.
사유: 데이터베이스 파일에 접근할 수 없습니다.
아래 원문을 보관해 두었다가 다시 보내주세요.
> (입력 원문 그대로)
```

### 8.3 목록 출력

기본값: `storage_state='active'`, 최근 업데이트순, 20건.

```
CLIO-0012 | 2026-08-10 → 2026-08-17
키워드: AI 비서, 기억, 망각
원문: AI 비서는 모든 것을 기억하는 것보다 무엇을 잊어야 하는지를…
수정일: 08-10, 08-12, 08-17
```

지원 자연어 조회: 보관 글감, 키워드 검색, 기간 검색, 최근 N건, 특정 글감과 연결된 글감, 미분류 원문 목록.

### 8.4 상세 출력

```
CLIO-0012 — 기록보다 중요한 망각
보관 상태: active   발전 단계: developed
생성일: 2026-08-10 14:32   최종 업데이트: 2026-08-17 20:05
전체 기록일: 08-10, 08-12, 08-17
키워드: AI 비서, 기억, 망각
관련 글감: CLIO-0007 (similar, 교수님 확인됨)
부모 글감: 없음

[날짜별 원문]  ← 교수님 원문, 무수정
2026-08-10 14:32:11
…
2026-08-12 09:10:47  (기록 2026-08-12 09:11:20)
…

[정제본 rev.4]  ← Clio 작성
…
```

원문 헤더는 `captured_at`을 **초 단위까지** 보여준다. 브리지가 메시지 원 시각을 넘겨주어
`captured_at`과 `ingested_at`이 다를 때만 괄호로 기록 시각을 덧붙인다. 목록(§8.3)은 날짜만
보여주지만 이는 표시상의 요약이며, 저장된 값은 언제나 오프셋을 포함한 초 단위 시각이다
(§11). 시각을 추정해 채워 넣는 것은 원문 위조로 간주하여 금지한다.

---

## 9. 보존·삭제·복구 정책

### 9.1 원칙 수정

"원문은 절대 삭제하지 않는다"가 아니라 **"원문은 일반적으로 불변으로 보존하되, 명시적인 영구 삭제 절차를 둔다"**로 한다. 잘못 게시한 개인정보·민감 정보는 실제 제거가 필요하다.

### 9.2 삭제 계층

| 요청 유형 | 처리 |
|---|---|
| 일반 삭제 요청 | `storage_state='archived'` 처리. 데이터 유지 |
| 민감정보·오입력 제거 | 명시적 재확인 → `raw_entry.body` 제거, `redacted_at` 기록 |
| 글감 자체 제거 | 재확인 → `idea.deleted_at` tombstone, 관계는 유지 |

**Tombstone:** 내용 없이 "삭제 사실과 시각"만 남긴다. 감사 추적과 번호 재사용 방지를 위해 `ref`는 영구히 회수하지 않는다.

**백업 반영:** 영구 삭제 실행 시 (a) 이후 생성 백업에는 자동 반영, (b) 기존 백업 파일은 삭제 대상 목록(`redaction_manifest.json`)을 함께 보관하고, 복구 시 해당 항목을 재적용한다. 기존 백업을 소급 수정하지는 않는다.

### 9.3 백업

- 대상: `ideas.db` 스냅샷 + JSON 전량 내보내기 + Markdown 내보내기 + `attachments/` 사본
- 방식: `sqlite3` 백업 API로 온라인 스냅샷을 뜬다. 실행 중인 DB 파일을 그대로 복사하지 않는다
- 주기: 쓰기 작업 발생일 1회 (일 단위), 수동 트리거 지원
- 보존: 일 7개, 주 4개, 월 12개 (rolling)
- 위치: `~/ClioData/backups/`에 생성한 뒤 `~/Dropbox/ClioBackup/`로 복사 (D2). **DB 원본은 Dropbox에 두지 않는다**
- 검증: 백업 직후 무결성 검사 실행, 월 1회 복구 시험(빈 DB에 복구 후 건수 대조)

### 9.4 무결성 검사 항목

1. `raw_entry.body_hash`와 실제 본문 해시 일치 (redacted 제외)
2. `idea.latest_revision_id`가 해당 글감의 최대 `rev_no`를 가리키는지
3. 고아 레코드: 존재하지 않는 idea를 참조하는 revision/relation/tag
4. `ref` 번호 중복·누락
5. `unassigned` Raw Entry 건수 보고
6. 대칭 관계 정규화 위반
7. `attachment.sha256`와 `stored_path` 실제 파일의 해시 일치, 파일 누락 여부

---

## 10. 도구 인터페이스 (결정론적 계층)

Clio 스킬이 호출할 수 있는 명령의 계약이다. 모든 명령은 JSON을 출력하고, 실패 시 0이 아닌 종료 코드와 오류 JSON을 반환한다. 부분 저장을 허용하지 않는다(단일 트랜잭션).

| 명령 | 목적 | 핵심 인자 |
|---|---|---|
| `idea_store parse` | 저장 지시어 판정만 수행 (쓰기 없음) | `--message` |
| `idea_store capture` | 원문 저장 (unassigned). `--raw-message` 사용 시 지시어를 강제 판정 | `--raw-message` 또는 `--body`, `--platform --message-id --captured-at --type --attach --force` |
| `idea_store assign` | Raw Entry를 글감에 배정 | `--entry-id --idea-ref` |
| `idea_store new-idea` | 새 글감 생성 + 배정 | `--title --entry-id` |
| `idea_store reassign` | 잘못 연결된 원문 이동 | `--entry-id --to-idea-ref` |
| `idea_store revise` | 정제본 revision 추가 | `--idea-ref --body --summary` |
| `idea_store relate` | 관계 설정 | `--from --to --type --rationale --confidence --by` |
| `idea_store merge` | 합성 글감 생성 | `--parents --title` |
| `idea_store split` | 일부 원문을 새 글감으로 분리 | `--entry-ids --title` |
| `idea_store set-state` | 보관 상태·발전 단계 변경 | `--idea-ref --storage --stage` |
| `idea_store tag` | 태그 추가·제거·통합 | `--idea-ref --add --remove --alias` |
| `idea_store undo` | 직전 작업 취소 | `--action-id` |
| `idea_search list` | 목록 조회 | `--state --stage --limit --order` |
| `idea_search show` | 상세 조회 (원문별 `captured_at`·`ingested_at` 포함) | `--idea-ref` |
| `idea_search query` | 검색 (근거 포함) | `--q --since --until --limit` |
| `idea_search similar` | 유사 후보 + 점수 | `--text --limit` |
| `idea_search unassigned` | 미분류 원문 목록 | — |
| `idea_search trace` | 출처 메시지 역추적 | `--entry-id` |
| `idea_export md` / `json` | 내보내기 | `--out` |
| `idea_check init` | 데이터 디렉터리·스키마 생성 (멱등) | — |
| `idea_check integrity` | 무결성 검사 | — |
| `idea_check reconcile` | 채널 이력과 DB 대조, 누락 지시어 점검 | `--since` |
| `idea_check calibrate` | 후보 제시 이력과 실제 선택 대조, 임계값 권고 (§6.3) | — |
| `idea_check backup` / `restore-test` | 백업·복구 시험 | `--out` / `--from` |

**멱등성 (D7 확정).** 브리지가 `message_id`를 넘겨주는지 여부를 실측으로 확인한 뒤 구현하지 않는다. **어느 쪽이든 동작하는 2단 규칙**을 기본 구현으로 삼는다.

1. `message_id`가 있으면 → `(platform, message_id)` 유니크 제약으로 판정. 중복이면 기존 `entry_id`를 반환한다.
2. `message_id`가 없으면 → 삽입 전 `(body_hash, 같은 날짜)` 조회로 판정. 일치 항목이 있으면 저장하지 않고 기존 `entry_id`와 함께 그 사실을 알린다.
3. 두 경우 모두 `--force`로 중복 판정을 우회할 수 있다. 같은 문장을 다른 맥락에서 일부러 다시 남기는 경우를 위한 것이다.

2번 규칙은 "같은 날 같은 문장을 두 번 저장"을 막으므로, 재시도 중복은 걸러내면서 며칠 뒤 같은 생각을 다시 적는 것은 정상 처리한다. 실측 결과 `message_id`가 전달된다면 1번 경로가 자동으로 우선 적용되며 코드 변경은 필요 없다.

---

## 11. 기본 설정값

| 항목 | 값 | 근거 |
|---|---|---|
| 글감 번호 형식 | `CLIO-0001` (4자리, 초과 시 자릿수 확장, 재사용 금지) | §5.2 |
| 저장 지시어 | `글감:` (별칭 `아이디어:`, `ㄱ:`) | §6.1 |
| 시간대 | `Asia/Seoul` | — |
| 시각 저장 | ISO8601, 초 단위, 오프셋 포함. `captured_at`(보낸 시각)과 `ingested_at`(기록 시각)을 분리 보관 | §5.2, §8.4 |
| 기본 목록 | active / 최근 업데이트순 / 20건 | §8.3 |
| 정제본 생성 | 명시적 요청 시에만 (자동 생성 없음) | D5 |
| 정제본 보관 | 최신본과 전체 이력 모두 보관 | §5.2 |
| 구현 언어 | Python 3.11+, 표준 라이브러리만 | D3 |
| 데이터 경로 | `~/ClioData/` (Dropbox 밖) | D2 |
| 설정 파일 | `~/ClioData/config.json` | D1 |
| 백업 사본 | `~/Dropbox/ClioBackup/` (백업 산출물만) | D2 |
| 첨부파일 | 원본 내려받아 sha256 파일명으로 복사 보관 | D6 |
| 검색 | FTS5 + 가중 점수 (§7.1 가중치표) | D1 |
| 자동 연결 | 초기 비활성 (`auto_link_threshold = 1.01`) | D1 |
| 확인 질문 기준 | `ask_threshold = 0.35`, 후보 최대 3건 | D1 |
| 후보 언급 하한 | `mention_threshold = 0.15` 미만은 "연결 후보"로도 언급하지 않음 | §6.3 |
| 중복 판정 | `message_id` 우선, 없으면 `(body_hash, 당일)` | D7 |

---

## 12. 구현 단계

구현체: `clio-idea-manager/`. 설치와 업데이트는 모두 `./install.sh`(최신을 받아 적용하려면 `./update.sh`), 테스트는 `python3 tests/test_clio.py`. 업데이트는 기존 설치를 지우지 않고 바뀐 파일만 제자리에서 갱신하며, `~/ClioData`와 사용자가 고친 `config.json` 값은 보존한다 (새로 생긴 설정 키만 기본값으로 채운다).

| 단계 | 범위 | 완료 기준 |
|---|---|---|
| **1. 안전한 포착** | 원문 저장, 새 글감 생성, 지정 글감 추가, 목록·상세 조회, 중복 방지 | TC1~TC3, TC9 통과 |
| **2. 편집과 이력** | revision, 2축 상태, 태그, Raw Entry 재배정, undo | TC5 통과 |
| **3. 연결과 합성** | 관계 설정, 유사 후보 검색, 파생 글감, 근거·확인 상태 기록 | TC4, TC8 통과 |
| **4. 운영 안정성** | Markdown·JSON 내보내기, 자동 백업, 복구 시험, 무결성 검사 | TC6, TC7 통과 |
| **5. 의미 검색** | 축적 데이터로 검색 품질 평가 후 조건부 임베딩 도입 | 재현율 목표 미달 사례 확인 시 착수 |

각 단계는 이전 단계의 테스트가 통과한 뒤에 시작한다. 1단계만으로도 실사용이 가능해야 한다.

### 12.1 구현 현황 (2026-08-18)

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. 안전한 포착 | **완료** | 지시어 판정을 저장 계층이 강제 |
| 2. 편집과 이력 | **완료** | revision, 2축 상태, 태그, 재배정, undo |
| 3. 연결과 합성 | **완료** | 관계·근거·확인 상태, merge, split, 유사 후보 |
| 4. 운영 안정성 | **완료** | 내보내기, 온라인 스냅샷 백업, 복구 시험, 무결성 8종 |
| 5. 의미 검색 | 미착수 | §14.1의 촉발 조건 충족 시 검토 |

미착수 항목: 자동 백업 스케줄링(현재는 `idea_check backup` 수동·외부 스케줄러 호출),
`idea_check reconcile`의 채널 이력 대조(브리지 제약으로 DB 쪽 사실만 보고).

---

## 13. 테스트 및 성공 기준

| ID | 테스트 | 기대 결과 |
|---|---|---|
| TC1 | 동일 Discord 메시지를 두 번 처리 | Raw Entry 1건만 생성, 두 번째는 기존 ID 반환 |
| TC2 | 저장 도중 프로세스 강제 종료 | Idea만 생기고 Raw Entry가 빠지는 부분 저장 없음 |
| TC3 | 기존 글감에 원문 추가 | 과거 원문·정제본의 해시가 불변 |
| TC4 | 합성 글감 생성 | 부모 글감 3건이 그대로 유지, `derived_from` 관계 3건 생성 |
| TC5 | 잘못 연결한 원문 재배정 | 이동 성공 + `action_log`에 변경 이력 존재 |
| TC6 | archived 글감 조회 | 기본 목록에서 숨김, 검색으로는 조회 가능 |
| TC7 | 백업으로 새 DB 복구 | 원문·revision·관계 건수 및 해시 전량 일치 |
| TC8 | 한국어 키워드·부분 문장 검색 | 목표 글감이 상위 5건 내 등장 |
| TC9 | DB 접근 불가 상태에서 입력 | Clio가 성공 응답을 하지 않고 원문을 되돌려줌 |
| TC10 | 상세 출력 검토 | 교수님 원문과 Clio 해석·제안이 명확히 구분됨 |
| TC11 | 확인 질문에 무응답 | Raw Entry가 `unassigned`로 남고 미분류 목록에서 조회됨 |
| TC12 | 민감정보 영구 삭제 | 본문 제거 + `redacted_at` 기록 + tombstone 유지, 번호 미재사용 |
| TC13 | 지시어 없는 일반 요청 처리 | Raw Entry 증가 0건, 어떤 테이블도 변경되지 않음 |
| TC14 | `글감:` 뒤 여러 줄 입력 | 줄바꿈·빈 줄이 원문 그대로 보존, 지시어 토큰은 본문에서 제외 |
| TC15 | 저장 지시 후 도구 호출 실패 | 응답에 `CLIO-` 번호가 포함되지 않고 원문을 되돌려줌 |
| TC16 | 문장 중간에 "글감:"이 등장하는 일반 메시지 | 지시어로 오인하지 않음 |
| TC17 | 이미지 첨부와 함께 `글감:` 입력 | 원본 파일이 `attachments/`에 복사되고 해시가 일치 |
| TC18 | 기존 글감에 새 원문 추가 | 정제본 revision이 새로 생기지 않음 (D5) |
| TC19 | 같은 날 동일 문장을 두 번 저장 지시 | 두 번째는 저장되지 않고 기존 `entry_id` 안내 (D7 2단 규칙) |
| TC20 | 백업 산출물의 Dropbox 복사 | `ideas.db` 원본은 Dropbox 경로에 존재하지 않음 |

**실행:** `cd clio-idea-manager && python3 tests/test_clio.py` — 위 20건을 포함한 25개 테스트가
격리된 임시 데이터 디렉터리에서 실제 스크립트를 호출해 검증한다. 2026-08-18 기준 전부 통과.

TC13·TC14·TC16은 지시어 판정이 저장 계층에 있으므로 도구 테스트로 직접 검증된다.
판단 계층(SKILL.md)의 대화 규칙은 `evals/evals.json`의 12개 시나리오로 회귀 검증한다.

---

## 14. 확정된 결정 사항

구현 전 결정이 필요했던 항목을 모두 확정했다. **미확정 항목은 없으며 §12의 1단계를 바로 착수할 수 있다.**

| # | 항목 | 확정값 | 반영 위치 |
|---|---|---|---|
| D1 | 유사도 임계값 | 자동 연결 초기 비활성(`1.01`), 확인 기준 `0.35`, 후보 3건. §7.1 가중치표로 점수 산출. 4주/50건 후 `idea_check calibrate` 근거로만 조정 | §6.3, §7.1, §10, §11 |
| D2 | 데이터 디렉터리 | 원본은 `~/ClioData/` (Dropbox 밖), 백업 산출물만 `~/Dropbox/ClioBackup/`로 복사 | §4.2, §9.3, §11 |
| D3 | 구현 언어 | Python 3.11+, 표준 라이브러리만 사용 (외부 의존성 없음) | §4.1, §11 |
| D4 | 수집 방식 | 브리지는 제3자 도구로 수정하지 않음. 저장은 `글감:` 지시어로만 진입 | §4.1, §6.1, §6.2 |
| D5 | 정제본 생성 시점 | 명시적 요청 시에만 생성. 포착 응답의 `핵심:` 한 줄은 정제본이 아님 | §6.4, §11 |
| D6 | 첨부파일 | 원본을 내려받아 sha256 파일명으로 복사 보관. `attachment` 테이블 신설 | §4.2, §5.2, §11 |
| D7 | 브리지 메타데이터 | 실측에 의존하지 않는 2단 멱등성 규칙 채택. `message_id`가 있으면 유니크 제약, 없으면 `(body_hash, 당일)` 조회. 어느 쪽이든 코드 변경 없이 동작 | §5.2, §10, §11 |

### 14.1 재검토 시점

확정값 중 셋은 실측 데이터가 쌓인 뒤 재검토를 예정한다. 재검토는 일정이 아니라 조건으로 촉발한다.

| 대상 | 촉발 조건 | 판단 근거 |
|---|---|---|
| D1 자동 연결 활성화 | 글감 50건 또는 운영 4주 | `idea_check calibrate` 결과, 1위 후보 채택률 95% 이상 구간 |
| §7.1 가중치 | TC8 실패 사례 3건 누적 | 실제로 못 찾은 질의의 신호별 기여도 분석 |
| §7.3 의미 검색 도입 | 가중치 조정으로도 TC8이 반복 실패 | 재현율 목표 미달이 검색 방식의 한계임이 확인될 때 |
