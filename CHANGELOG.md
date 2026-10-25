# 변경 이력

이 프로젝트는 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 형식과
[유의적 버전(SemVer)](https://semver.org/lang/ko/)을 따른다.

버전의 단일 원본은 `scripts/clio_core.py`의 `CLIO_VERSION`이며,
이 파일 맨 위 항목과 값이 다르면 테스트가 실패한다.

| 자리 | 올리는 경우 |
|---|---|
| MAJOR | 기존 데이터나 명령 계약이 깨지는 변경 (마이그레이션 필요) |
| MINOR | 명령·출력·판단 규칙의 추가나 변경 (하위 호환) |
| PATCH | 버그 수정, 문서 수정 |

데이터 파일 형식의 버전(`SCHEMA_VERSION`)은 별도로 관리한다. 현재 `1`.

## [1.3.0] — 2026-10-25

### 추가

- **공통 데이터 위치.** 신규 설치는 `~/SecretaryData/services/ideas/`를 쓴다. 위치 판정 순서는
  `CLIO_DATA_DIR` → 위치 기록(`~/.config/clio-idea-manager/data-root.json`) → 기존 `~/ClioData`
  (DB가 있을 때) → 새 기본 위치. 환경 변수 없이 도는 에이전트·예약 작업도 위치 기록을 따른다.
- 데이터 폴더의 위치 표시 `clio-root.json`(`root_id`, `active`/`retired`, 이전 출처·행선지).
- `idea_check where` — 지금 쓰는 위치와 판정 근거, 두 후보 상태, 경고 (쓰기 없음).
- `idea_check init --pin --expect-root` — 판정한 위치를 위치 기록에 고정 (`install.sh`가 사용).
- `idea_relocate` — 데이터 폴더 이전·복구.
  - `plan`: 실제 경로·건수·백업 위치·쓰기 주체(잠금·열린 파일·최근 쓰기·구버전 도구 사본)·
    예약 작업과 셸 설정의 옛 경로 참조(경로·줄 번호만)·DB 안 절대경로·여유 공간·권한·
    예상 중단 시간·복구 방법. 아무것도 바꾸지 않는다.
  - `run`: 배타 잠금 → 검증된 새 백업 → 임시 폴더에 스냅샷·첨부·설정 복사(해시 검증) →
    표별 지문·무결성·파일 목록 대조 → 전환(옛 DB는 `ideas.db.relocated-<id>`로 보존하고 그 자리에
    차단 파일) → 위치 기록 → registry의 `ideas` 항목만 잠금 갱신. 단계별 기록으로 중단 후 재실행.
  - `status`: 현재 위치, 이전 기록, 옛 위치 차단 여부, 전환 뒤 새 기록, 끝나지 않은 작업.
  - `rollback`: 전환 뒤 새 기록이 없으면 보존본을 사본으로 되살리고(새 위치에는 은퇴 표시만
    남긴다), 있으면 새 위치를 먼저 백업하고 후속 관계를 검증한 뒤 최신 데이터·새 첨부를 옛 위치로
    옮긴다(전환 뒤 영구 삭제한 첨부는 되살리지 않는다). 옛 위치의 차단 파일은 마지막 단계에서만
    바꾸므로 멈춰도 두 위치가 동시에 쓰이지 않는다. 중단 후 재실행.
  - 설정값은 `/`·`~/`로 시작하고 원본 폴더 안을 가리키는 것만 새 위치로 바꾼다.
- 모든 Clio 명령이 데이터 폴더에 공유 잠금(`.clio.lock`)을 잡는다. 이전·복구 중에는
  `CLIO_LOCK_TIMEOUT`(기본 30초)까지 기다렸다가 `root_busy`로 실패한다.
- 백업 검증 목록 `manifest.json`(형식 2: 도구·종류·표별 지문·파일별 SHA-256)과 즉시 검증.
  `restore-test`가 목록 대조 후 격리 복원하며 `--restore-to`로 사본을 남길 수 있다.
- 설정 키 `backup_root`(기본 `~/SecretaryBackups/ideas`).
- 예상하지 못한 오류도 traceback 대신 JSON 실패(`internal_error`)로 알린다.
- 테스트 37건 추가 (`tests/test_relocation.py`, 총 67건).

### 변경

- 백업 위치가 데이터 폴더 안(`backups/`)에서 `backup_root`로 바뀌었다. 기존 백업은 그대로 둔다.
- 백업에 `config.json`도 넣고, 백업 DB는 파일 하나로 완결(저널 모드 DELETE)한다.
- `restore-test`는 백업 폴더의 DB를 열지 않고 복사본으로 검증한다 (백업 폴더를 바꾸지 않는다).
- `backup_mirror`는 그 폴더가 **이미 있을 때만** 복사한다. 설정만으로 미러 폴더를 새로 만들지 않는다.
- 백업 정리는 이 도구의 일상 백업만 대상으로 한다. 이전·복구 백업과 다른 도구의 백업은 지우지 않는다.
- `install.sh`는 위치를 먼저 판정하고, 판정할 수 없으면 아무것도 바꾸지 않고 멈춘다.
  기존 설치는 그 위치를 그대로 쓰며 데이터를 옮기지 않는다.
- 무결성 검사의 첨부 경로를 데이터 폴더 기준으로 해석한다.

### 수정

- 데이터 폴더가 없을 때(외장 디스크 분리, 잘못된 경로 등) 쓰기 명령이 그 자리에 빈 DB를 새로
  만들고 저장하던 문제. 위치 기록·위치 표시가 있는데 DB가 없으면 `root_missing`·`db_missing`으로,
  두 위치 모두에 DB가 있으면 `root_ambiguous`로 멈춘다.

## [1.2.2] — 2026-08-19

### 수정

- **롤백본이 스킬로 중복 등록되던 문제.** v1.2.0~1.2.1은 직전 스킬을
  `~/.claude/skills/clio-idea-manager.prev`에 보관했는데, 그 안에 `SKILL.md`가 있어
  같은 설명을 가진 스킬이 하나 더 등록됐다. 어느 쪽이 호출될지 알 수 없어 위험하다.
  보관 위치를 `~/ClioData/skill-prev/`(환경 변수 `CLIO_PREV_DIR`로 변경 가능)로 옮겼다.
- 이전 위치(`<스킬 경로>.prev`)에 남아 있는 롤백본은 업데이트 시 자동으로 새 위치로 옮긴다.
- README의 `git clone` 주소에 남아 있던 자리표시자(`<your-account>`)를 실제 주소로 교체.

## [1.2.1] — 2026-08-19

### 수정

- v1.1.0 이전(=`CLIO_VERSION`이 없던) 설치본을 업데이트할 때 **신규 설치로 오인해
  롤백본(`<스킬 경로>.prev`)을 만들지 않던 문제.** 설치 여부를 버전 문자열이 아니라
  `scripts/clio_core.py`의 존재로 판정하도록 고쳤다. 이 경로를 검증하는 테스트 추가 (총 30건).
- 스크립트 마지막 줄의 조건부 `echo`를 `if` 문으로 바꿨다 (`set -e`에서 종료 코드가
  조건 결과에 좌우되지 않도록).

## [1.2.0] — 2026-08-18

### 추가

- `update.sh` — 최신 버전을 받아 그대로 적용한다. `git pull --ff-only` 후 `install.sh`를 부르며,
  받아온 커밋 목록을 보여준다. 커밋하지 않은 로컬 변경이 있으면 멈춘다.
- 업데이트 시 직전 스킬을 `<스킬 경로>.prev`에 한 세대 보관한다. 되돌릴 때 재설치가 필요 없다.
- 설치·업데이트 회귀 테스트 추가 — 데이터·사용자 설정 보존, 낡은 파일 제거, 롤백본 생성 검증.
- README §4에 업데이트·되돌리기 절 신설, FAQ·트러블슈팅 항목 추가.

### 변경

- **`install.sh`가 첫 설치와 업데이트를 같은 명령으로 처리한다.** 기존 설치를 지우고 다시
  까는 방식(`rm -rf` 후 `cp -R`)을 버리고, 바뀐 파일만 덮어쓰고 원본에서 사라진 파일만
  지우는 제자리 동기화로 바꿨다. 결과를 `추가/갱신/유지/삭제` 건수로 보고한다.
- `config.json`을 병합한다. 사용자가 고친 값은 그대로 두고 **새로 생긴 설정 키만** 기본값으로
  채운다. 이전에는 파일이 있으면 통째로 건너뛰어 새 키가 영원히 반영되지 않았다.
- 설치 시 하드코딩된 설정 목록 대신 `clio_core.DEFAULT_CONFIG`를 쓴다(단일 원본).
- 설치·업데이트 로그에 버전 전이(`1.1.0 → 1.2.0`)와 스키마 버전을 표시한다.
- `CHANGELOG.md`도 스킬 디렉터리에 함께 설치된다.

## [1.1.0] — 2026-08-18

### 추가

- `idea_search show`의 원문 헤더가 입력 시각을 **초 단위**까지 보여준다.
  보낸 시각(`captured_at`)과 기록 시각(`ingested_at`)이 다르면 괄호로 함께 낸다.
  (`2026-08-17 09:12:04  (기록 2026-08-18 23:42:47)`)
- `idea_search show`의 JSON 출력 `raw_entries[]`에 `ingested_at` 필드 추가.
- 네 도구 모두 `--version` 플래그 지원. `idea_check init`·`integrity` 응답에 `clio_version` 포함.
- `CHANGELOG.md` 신설, `CLIO_VERSION` 도입, 버전·이력 일치 검사 테스트 추가.
- SKILL.md §2.1 — 브리지가 메시지 원 시각을 주면 `--captured-at`으로 그대로 싣고,
  주지 않으면 붙이지 않으며, **시각을 추정해 만들어 넣지 않는다**는 규칙 명문화.

### 변경

- `idea_search show`의 `생성일`·`최종 업데이트`가 날짜만이 아니라 분 단위 시각까지 표시된다.
- README에 `### 시각` 절 신설(컬럼별 의미와 표시 규칙), FAQ·CLI 레퍼런스 보강.
- PRD §8.4·§10·§11을 구현에 맞춰 갱신.

### 참고

시각은 v1.0.0부터 이미 ISO8601 초 단위·오프셋 포함으로 저장되고 있었다.
이번 변경은 **표시 계층**만 손댄 것이며 기존 데이터의 마이그레이션은 필요 없다.

## [1.0.0] — 2026-08-18

첫 공개. PRD v1.3의 1~4단계 구현 완료.

### 추가

- 판단 계층(`SKILL.md`)과 결정론적 저장 계층(`scripts/`)의 2계층 분리.
- 저장 지시어(`글감:` / `아이디어:` / `ㄱ:`) 기반 포착. 판정은 저장 계층이 강제한다.
- `idea_store` — parse, capture, new-idea, assign, reassign, revise, relate,
  merge, split, set-state, tag, undo, redact, delete-idea.
- `idea_search` — list, show, query, similar, unassigned, trace, relations.
- `idea_export` — Markdown, JSON 전량 내보내기.
- `idea_check` — init, integrity(8종), backup, restore-test, reconcile, calibrate.
- SQLite FTS5 + 다중 신호 가중 점수 검색, 한국어 근사 어간 처리.
- 원문 append-only·해시 검증, 정제본 이력, 관계·합성·분리, 2축 상태.
- 온라인 스냅샷 백업(일 7/주 4/월 12), redaction manifest, 복구 시험.
- 저장 계층 테스트 26건.

[1.3.0]: https://github.com/pilwonhur/clio-idea-manager/releases/tag/v1.3.0
[1.2.2]: https://github.com/pilwonhur/clio-idea-manager/releases/tag/v1.2.2
[1.2.1]: https://github.com/pilwonhur/clio-idea-manager/releases/tag/v1.2.1
[1.2.0]: https://github.com/pilwonhur/clio-idea-manager/releases/tag/v1.2.0
[1.1.0]: https://github.com/pilwonhur/clio-idea-manager/releases/tag/v1.1.0
[1.0.0]: https://github.com/pilwonhur/clio-idea-manager/releases/tag/v1.0.0
