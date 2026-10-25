# 삭제·백업·복구 정책

## 원칙

원문은 **일반적으로** 불변으로 보존한다. 다만 잘못 게시한 개인정보나 민감한 내용은
실제 제거가 필요하므로 명시적인 영구 삭제 절차를 둔다.

## 삭제 계층

| 요청 | 처리 | 되돌리기 |
|---|---|---|
| "지워줘" (일반) | `set-state --storage archived` | 가능 |
| 민감정보·오입력 | `redact --confirm` — 본문 제거, `redacted_at` 기록 | 불가 |
| 글감 자체 제거 | `delete-idea --confirm` — tombstone | 불가 |

tombstone은 내용 없이 삭제 사실과 시각만 남긴다. 감사 추적과 번호 재사용 방지를 위해
`ref`는 영구히 회수하지 않는다. 번호에 공백이 생기는 것은 정상이며 무결성 검사도
이를 오류로 보지 않는다.

## 백업

- **대상** — `ideas.db` 스냅샷, JSON 전량, Markdown, `attachments/` 사본, `config.json`
- **방식** — `sqlite3` 백업 API로 온라인 스냅샷. 실행 중인 파일을 복사하지 않는다.
  WAL에만 있고 아직 본 파일에 반영되지 않은 기록도 스냅샷에 들어간다. 백업 DB는 저널 모드를
  DELETE로 바꿔 파일 하나로 완결한다 (열어 보기만 해도 옆에 `-wal`·`-shm`이 생기지 않게).
  `restore-test`는 백업 폴더의 DB를 열지 않고 복사본으로 검증한다
- **위치** — `config.json`의 `backup_root`(기본 `~/SecretaryBackups/ideas/<타임스탬프>/`).
  데이터 폴더 안에 두지 않는다. `backup_mirror`(기본 `~/Dropbox/ClioBackup/`)는 **그 폴더가 이미
  있을 때만** 복사한다. 설정만으로 미러 폴더를 새로 만들지 않는다
- **검증 목록** — 백업마다 `manifest.json`(형식 2): 만든 도구·종류(`routine`/`relocation`/`rollback`)·
  표별 지문·파일별 크기와 SHA-256. 만든 직후 목록대로 다시 읽어 검증한다
- **보존** — 일 7 / 주 4 / 월 12 rolling. **이 도구의 일상(`routine`) 백업만** 정리한다.
  이전·복구 백업, 다른 도구가 같은 폴더에 둔 백업은 지우지 않는다
- **검증** — 월 1회 `restore-test` (검증 목록 대조 → 격리 위치에 복원 → 무결성·지문 대조)

v1.2 이하가 `~/ClioData/backups/`에 만든 백업은 그 자리에 그대로 둔다 (옮기거나 지우지 않는다).

**원본 DB를 Dropbox에 두지 않는다.** 동기화 클라이언트는 SQLite의 파일 잠금을 알지 못한 채
파일을 교체하거나 충돌 사본을 만들 수 있고, 쓰기 중이면 DB가 손상된다.
백업 산출물은 이미 정지된 스냅샷이므로 동기화해도 안전하다.

## 영구 삭제와 백업의 관계

기존 백업 파일은 소급 수정하지 않는다. 대신 백업 루트의 `redaction_manifest.json`에
삭제 대상 목록을 누적 보관하고, 백업마다 갱신한다.

과거 백업에서 복구할 때는 복구 직후 이 매니페스트를 다시 적용해 삭제를 재현해야 한다.
그렇게 하지 않으면 지운 내용이 복구와 함께 되살아난다.

## 무결성 검사 항목

1. 원문 본문 해시 일치 (redacted 제외)
2. `latest_revision_id`가 최신 revision을 가리키는지
3. 고아 레코드 (revision, relation, tag, attachment, raw_entry)
4. 번호 중복 · 공백 보고
5. 미분류 원문 건수 보고
6. 대칭 관계 정규화 위반
7. 첨부 파일 존재와 해시 일치
8. `PRAGMA integrity_check`

## 복구 절차

데이터 폴더 위치는 설치마다 다르다. 먼저 확인한다 (`<데이터>`, `<백업>`은 아래 출력의 경로).

```bash
scripts/idea_check where                # data_dir = 현재 데이터 폴더
```

```bash
# 1. 백업 검증 — 검증 목록 대조, 격리 위치에 복원해 무결성·지문 확인
scripts/idea_check restore-test --source <백업>/<타임스탬프>

# 2. 격리 사본을 남겨 미리 확인할 수도 있다 (운영 정본은 그대로)
scripts/idea_check restore-test --source <백업>/<타임스탬프> --restore-to /tmp/clio-restore
CLIO_DATA_DIR=/tmp/clio-restore scripts/idea_search list --format text

# 3. Clio 명령이 돌지 않을 때, 현재 DB를 보존한 뒤 교체
mv <데이터>/ideas.db <데이터>/ideas.db.broken
[ -f <데이터>/ideas.db-wal ] && mv <데이터>/ideas.db-wal <데이터>/ideas.db.broken-wal   # 짝을 맞춰 보존
rm -f <데이터>/ideas.db-shm                                                            # 공유 메모리 파일 (내용 없음)
cp <백업>/<타임스탬프>/ideas.db <데이터>/ideas.db
cp -R <백업>/<타임스탬프>/attachments/ <데이터>/attachments/

# 4. 검증
scripts/idea_check integrity --strict
```

복구 후 건수와 무결성이 맞지 않으면 교체한 파일을 되돌리고 다른 백업을 시도한다.
복구 직후 `redaction_manifest.json`을 다시 적용한다 (위 절).

## 데이터 위치 이전과 복구

데이터 폴더를 옮기거나(`idea_relocate run`) 옮긴 것을 되돌리는(`idea_relocate rollback`) 절차는
[relocation.md](relocation.md)에 있다. 요점:

- 이전 전 새 백업(`kind: relocation`)을 만들고 검증한 뒤에만 진행한다.
- 옛 DB는 지우지 않고 `ideas.db.relocated-<id>`로 보존한다. 그 자리의 차단 파일 때문에 옛 위치에는
  아무도 쓰지 못한다.
- 전환 뒤 새 기록이 있으면 옛 DB로 단순 복귀하지 않는다. 새 위치를 먼저 백업하고, 최신 데이터를
  옛 위치로 옮긴다.
- 코드 되돌리기(`skill-prev`)와 데이터 위치 되돌리기는 별개다.
