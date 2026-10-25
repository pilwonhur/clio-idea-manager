# 데이터 위치 · 이전 · 복구 (v1.3.0)

데이터 폴더를 옮기는 일은 교수님의 원문을 다루는 작업이다. **에이전트가 스스로 시작하지 않는다.**
`plan`(미리보기)을 보여드리고 교수님이 명시적으로 요청했을 때만 `run`·`rollback`을 실행한다.

## 1. 데이터 위치는 어떻게 정해지나

모든 도구(`idea_store`·`idea_search`·`idea_export`·`idea_check`)와 `install.sh`가 같은 규칙을 쓴다.
위에서부터 처음 맞는 것을 쓴다.

| 순서 | 출처 | 언제 |
|---|---|---|
| 1 | `CLIO_DATA_DIR` 환경 변수 | 명시한 실행에만 적용. 위치 기록으로 고정하지 않는다 |
| 2 | 위치 기록 `~/.config/clio-idea-manager/data-root.json` | 설치·이전이 쓴다. 환경 변수 없이 도는 에이전트·예약 작업도 이것을 따른다 |
| 3 | 옛 기본 위치 `~/ClioData` | 위치 기록이 없고 그곳에 DB가 있을 때 (v1.2 이하 설치) |
| 4 | 새 기본 위치 `~/SecretaryData/services/ideas` | 신규 설치 |

`~`는 실행하는 사용자의 홈 폴더다. 코드에 특정 사용자 경로를 넣지 않는다.

**멈추는 경우 (빈 DB를 새로 만들지 않는다)**

| 코드 | 상황 | 할 일 |
|---|---|---|
| `root_ambiguous` | 위치 기록이 없는데 옛 위치와 새 기본 위치 모두에 DB가 있다 | 어느 쪽이 정본인지 확인. `CLIO_DATA_DIR`로 명시하거나 한쪽을 정리 |
| `root_missing` | 위치 기록이 가리키는 폴더가 없다 (외장 디스크 분리 등) | 디스크 연결 확인. 옮겼다면 `idea_relocate status` |
| `db_missing` | 위치 표시(`clio-root.json`)는 있는데 DB가 없다 | 백업에서 복구 ([recovery-policy.md](recovery-policy.md)) |
| `root_retired` | 이전을 마친 옛 위치를 가리킨다 | 메시지에 새 위치가 나온다. `CLIO_DATA_DIR`·스크립트를 새 위치로 |
| `root_mismatch` | 폴더의 위치 표시가 위치 기록과 다르다 (다른 데이터로 바뀜) | 수동 확인 |
| `relocation_unfinished` | 끝나지 않은 이전이 놓아 둔 사본을 직접 지정했다 | `idea_relocate run`으로 마치거나 환경 변수를 지운다 |
| `db_unrecognized` | 그 위치의 `ideas.db`가 SQLite가 아니다 (손상 등). 옆 위치로 넘어가 빈 DB를 만들지 않는다 | 백업에서 복구 |
| `root_busy` | 이전·복구가 잠금을 쥐고 있다 (`CLIO_LOCK_TIMEOUT`, 기본 30초) | 끝난 뒤 다시 실행. 저장되지 않았으니 **저장했다고 말하지 않는다** |

지금 어디를 쓰는지:

```bash
scripts/idea_check where        # 판정 결과·출처·두 후보 상태·경고 (아무것도 쓰지 않음)
```

### 데이터 폴더의 표시 파일

| 파일 | 뜻 |
|---|---|
| `clio-root.json` | 위치 표시. `root_id`(고유 번호), `state`(`active`/`retired`), 이전이면 `relocated_from`·`moved_to` |
| `.clio.lock` | 실행 중인 Clio 명령이 공유 잠금, 이전·복구가 배타 잠금을 잡는 파일 |
| `relocation/<id>.json` | 이전·복구 기록 (단계별, 중단 후 재실행 근거) |
| `ideas.db` (텍스트) | 이전을 마친 옛 위치의 **차단 파일**. 구버전 도구가 열면 `file is not a database`로 멈춘다 |
| `ideas.db.relocated-<id>` | 이전 직전의 옛 DB 보존본 (지우지 않는다) |

## 2. 기존 설치를 새 위치로 옮기기

```bash
# 0. 코드부터 v1.3.0 이상으로 (데이터는 그대로, 옛 위치를 위치 기록에 고정)
./update.sh

# 1. 미리보기 — 아무것도 바꾸지 않는다
~/.claude/skills/clio-idea-manager/scripts/idea_relocate plan

# 2. 교수님 승인 후 실행
~/.claude/skills/clio-idea-manager/scripts/idea_relocate run

# 3. 확인
~/.claude/skills/clio-idea-manager/scripts/idea_relocate status
~/.claude/skills/clio-idea-manager/scripts/idea_check integrity --strict
```

`plan`이 보여주는 것: 원본·대상의 실제 경로와 상태, 기록 건수, 백업 위치, 쓰기 주체(열린 파일·잠금·
최근 쓰기·구버전 도구 사본), 예약 작업과 셸 설정의 옛 경로 참조(경로·줄 번호만, 내용·비밀값은 출력하지
않음), DB 안 절대경로, 여유 공간·권한, 예상 중단 시간, 단계, 복구 방법. 차단 사유가 있으면 `ready: false`.

**차단 사유 (하나라도 있으면 `run`은 아무것도 바꾸지 않는다)**

| 코드 | 뜻 |
|---|---|
| `old_tools_installed` | 설치된 도구가 v1.3.0 미만 — 먼저 `update.sh` (`--allow-old-tools`로 무시 가능, 권장하지 않음) |
| `writer_active` | Clio 명령이 실행 중이거나 다른 프로세스가 DB를 열고 있다 |
| `source_problems` | 원본에 무결성 문제 (첨부 누락·해시 불일치 등). 그대로 옮기려면 `--accept-source-problems` |
| `target_conflict` | 대상에 다른 파일·다른 DB가 있다. 덮어쓰지 않는다 |
| `insufficient_space` · `permission_denied` | 대상 쪽 여유 공간·쓰기 권한 부족 |
| `rollback_in_progress` | 끝나지 않은 복구가 있다 — `idea_relocate rollback`으로 먼저 마친다 |

`run`이 하는 일 (단계마다 `relocation/<id>.json`에 기록):

1. 원본 폴더 배타 잠금, 열린 파일·최근 쓰기(`--quiet-seconds`, 기본 60초) 재확인
2. 새 백업 `~/SecretaryBackups/ideas/<시각>/` — SQLite 백업 API(WAL에만 있는 기록 포함), 첨부·설정,
   파일별 해시 목록(`manifest.json`), 즉시 검증
3. 임시 폴더 `.ideas.staging-<id>`에 스냅샷·첨부·설정 복사 (해시 검증)
4. 지문(표별 행 해시)·무결성·파일 목록 대조. 원본 폴더 안을 가리키는 절대경로 첨부만 상대경로로 보정
   (과거 기록 `action_log` 등은 고치지 않는다). `config.json`은 `/`나 `~/`로 시작하는 값 중 원본 폴더
   안을 가리키는 것만 새 위치로 바꾼다 (`Asia/Seoul`·URL 같은 값은 그대로)
5. 전환: 임시 폴더 → 대상, 옛 `ideas.db`는 `ideas.db.relocated-<id>`로 보존하고 그 자리에 차단 파일,
   위치 기록 갱신
6. 새 위치 검증 → registry의 `operational_roots.ideas`·`cutovers.ideas`만 잠금 갱신 → 완료

옛 위치의 첨부·`skill-prev`·`backups`는 **지우지 않는다**. 정리는 운영 검증이 끝난 뒤 사람이 한다.

### registry

`~/SecretaryData/registry.json`은 여러 비서가 함께 쓴다. 이 도구는 `ideas` 항목 두 개만 바꾸고,
`.registry.json.lock` 잠금 아래 원자적으로 쓴다. 파일이 없거나 형식이 다르면 고치지 않고
`registry_update.deliver_to_chris`에 갱신 내용을 담아 돌려준다. `--no-registry`도 같다.

### 중단 범위

전환 동안 **Clio 명령만** 잠시 멈춘다 (`plan`의 `interruption.estimate_seconds`). 그 사이 들어온 명령은
최대 `CLIO_LOCK_TIMEOUT`초 기다렸다가 끝나면 이어서, 넘으면 `root_busy`로 실패한다. 다른 비서·메일·
캘린더와는 무관하다. `plan`이 예약 작업에서 옛 경로를 찾으면(`schedule_pins_old_root`) 전환 후 고쳐야 한다.

### 중간에 멈추면

같은 `run`을 다시 실행한다.

- 전환 전(백업·복사·검증 중): 원본은 그대로다. 남은 임시 폴더를 치우고 처음부터 한다.
- 전환 중(사본을 놓은 뒤): 원본이 아직 정본이다. 그 사이 원본에 새 기록이 생겼으면 놓아 둔 사본을
  `.ideas.abandoned-<id>`로 치우고 처음부터 다시 한다. 아니면 남은 단계를 이어서 끝낸다.
- 원본과 사본이 **둘 다** 바뀌었으면 `relocation_conflict` — 자동으로 고르지 않는다.

`idea_relocate status`가 끝나지 않은 작업(`unfinished`)과 할 일을 알려준다.

## 3. 되돌리기

```bash
scripts/idea_relocate rollback --dry-run    # 무엇을 할지 (쓰기 없음)
scripts/idea_relocate rollback              # 실행 (멈추면 같은 명령을 다시)
```

| 모드 | 언제 | 하는 일 |
|---|---|---|
| `no_writes` | 전환 뒤 새 기록이 없다 | 새 위치를 백업 → 보존본을 검증해 **사본으로** 되살림 (보존본은 그대로) → 새 위치는 `.ideas.rolledback-<id>`로 옮기고, 그 자리에는 **은퇴 표시만 있는 빈 폴더** → 옛 위치를 정본으로 |
| `with_writes` | 전환 뒤 새 기록이 있다 | 새 위치를 **먼저** 백업 → 새 DB가 옛 DB의 후속인지 검증 → 최신 DB·새 첨부·설정을 옛 위치에 임시 이름으로 복사·검증 → 새 위치는 차단 파일로 은퇴 → 옛 위치에 넣고 정본으로. 전환 뒤 영구 삭제(redact)한 첨부는 옛 위치에서도 지운다 |

전환 뒤 기록이 있는데 옛 DB로 단순 복귀하지 않는다 (새 기록이 사라진다). 후속 검증에 실패하면
(`rollback_refused`) 아무것도 바꾸지 않는다. 순서는 항상 "현재 위치를 닫고 → 옛 위치를 연다"이고,
옛 위치의 차단 파일은 마지막 단계에서만 바꾼다. 그래서 중간에 멈춰도 두 위치가 동시에 쓰이지 않고,
구버전 도구가 옛 위치의 낡은 DB를 여는 일도 없다. 멈춘 사이 새 위치에 기록이 더 생기면 그 복구는
포기(`abandoned`)하고 처음부터 다시 한다.

`no_writes` 뒤 새 위치에 남는 은퇴 표시(`clio-root.json`, `placeholder: true`)는 `CLIO_DATA_DIR`로
그곳을 지정한 실행이 빈 DB를 새로 만들어 갈라지는 것을 막는다(`root_retired`).

복구 뒤 다시 옮길 때: `no_writes`였으면 그냥 `run` (은퇴 표시만 있는 자리는 빈 폴더로 본다).
`with_writes`였으면 새 위치가 은퇴 상태라 `run --replace-retired`가 필요하다 (은퇴한 폴더는 옆으로
옮겨 보존).

## 4. 코드 되돌리기와 데이터 위치 되돌리기는 별개다

- **코드**: `<데이터 폴더>/skill-prev/`를 스킬 위치로 다시 복사 (README §4). 데이터는 그대로다.
- **데이터 위치**: `idea_relocate rollback`.

이전 뒤 코드를 v1.2 이하로 되돌리면, 구버전 도구는 위치 기록을 모르므로 `~/ClioData`를 연다.
그곳의 차단 파일 때문에 `file is not a database`로 멈추고 **아무 파일도 만들지 않는다** (조용히 빈 DB에
쓰지 않는다). 구버전으로 새 위치를 써야 하면 `CLIO_DATA_DIR=~/SecretaryData/services/ideas`를 명시한다
(스키마 버전이 같아 열린다). 옛 위치로 돌아가려면 먼저 `idea_relocate rollback`.

## 5. 백업 위치

새 백업은 `config.json`의 `backup_root`(기본 `~/SecretaryBackups/ideas`)에 만든다. 데이터 폴더 안이
아니다. `backup_mirror`(기본 `~/Dropbox/ClioBackup`)는 **그 폴더가 이미 있을 때만** 복사한다 — 설정만으로
새로 만들지 않는다. 정리(`backup_retention`)는 이 도구의 일상 백업만 대상이다. 이전·복구 백업과
다른 도구의 백업은 지우지 않는다. 자세한 내용은 [recovery-policy.md](recovery-policy.md).
