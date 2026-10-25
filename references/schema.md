# 데이터 모델

정본 DDL은 `scripts/clio_core.py`의 `SCHEMA_SQL`이다. 이 문서는 그 의도를 설명한다.

## 단일 원본 규칙

같은 사실이 두 곳에 저장되면 반드시 어긋난다. 무엇이 원본이고 무엇이 계산값인지 고정한다.

| 항목 | 단일 원본 | 파생(계산) |
|---|---|---|
| 현재 정제본 | `revision` 행 | `idea.latest_revision_id`가 가리키는 참조 |
| 부모·파생 관계 | `idea_relation` | 조회 시 계산 (`idea`에 목록 컬럼 없음) |
| 키워드 | `tag` + `idea_tag` | 표시용 문자열 |
| 원문 | `raw_entry` (append-only) | 목록의 미리보기 |
| 수정일 목록 | `raw_entry.captured_at` + `revision.created_at` | 조회 시 집계 |

## 테이블

- **idea** — 글감의 *현재 상태*만. 번호(`ref`), 제목, 보관 상태, 발전 단계, 최신 revision 참조.
  `deleted_at`은 tombstone이며 번호는 재사용하지 않는다.
- **raw_entry** — 교수님의 원문. 수정하지 않고 계속 쌓는다. `idea_id`가 NULL이면 미분류.
  `body_hash`로 무결성을 검사하고, `redacted_at`은 영구 삭제 흔적이다.
- **source** — 출처. Discord 외 채널로 일반화했다. `(platform, message_id)`가 유니크다.
- **attachment** — 첨부 원본 사본. 파일명은 내용 해시이므로 같은 파일은 한 번만 저장된다.
- **revision** — 정제본 이력. 불변. `rev_no`는 글감 안에서 1부터 증가한다.
- **idea_relation** — 관계의 단일 원본. 근거, 확신도, 교수님 확인 여부를 함께 기록한다.
- **tag / idea_tag** — 키워드. `alias_of`로 명칭을 통합한다.
- **action_log** — 되돌리기와 감사 추적의 근거. `payload.undo`에 역연산이 들어 있다.
- **search_fts** — FTS5 인덱스. 원본이 아니라 파생이므로 언제든 재생성할 수 있다.

## 방향성과 대칭성

`similar`, `related`는 대칭이다. 저장할 때 `from_idea_id < to_idea_id`로 정규화하고
`symmetric=1`을 둔다. `derived_from`, `expands`, `contradicts`는 방향을 그대로 보존한다.
무결성 검사가 이 규칙 위반을 잡는다.

합성 글감은 새 `idea`를 만들고 각 부모에 대해 `(새 글감 → 부모, derived_from)` 관계를 넣는다.
부모는 삭제하거나 흡수하지 않는다.

## 검색 (한국어)

SQLite FTS5의 `unicode61` 토크나이저는 한국어 어절을 통째로 자르므로,
'기억은'으로 검색하면 '기억하는'을 찾지 못한다. 형태소 분석기 없이(표준 라이브러리만 사용)
이를 보완하기 위해 `clio_core.stems()`가 조사·어미를 벗겨낸 어간 후보를 만든다.

    기억은   → {기억은, 기억}
    기억하는 → {기억하는, 기억하, 기억}
    망각을   → {망각을, 망각}

FTS 질의는 가장 짧은 어간의 prefix로 던지고, 부분 문자열 신호도 어간 기준으로 판정한다.
근사이므로 오매칭이 생길 수 있으나, 자동 연결이 초기 비활성이라 확인 질문으로 흡수된다.

점수는 `PRD.md §7.1`의 가중합이며 가중치는 데이터 폴더의 `config.json`(`idea_check where`로 확인)에서 바꿀 수 있다.
