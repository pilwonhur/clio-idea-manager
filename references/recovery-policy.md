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

- **대상** — `ideas.db` 스냅샷, JSON 전량, Markdown, `attachments/` 사본
- **방식** — `sqlite3` 백업 API로 온라인 스냅샷. 실행 중인 파일을 복사하지 않는다
- **위치** — `~/ClioData/backups/<타임스탬프>/` 생성 후 `~/Dropbox/ClioBackup/`로 복사
- **보존** — 일 7 / 주 4 / 월 12 rolling
- **검증** — 백업 직후 무결성 검사, 월 1회 `restore-test`

**원본 DB를 Dropbox에 두지 않는다.** 동기화 클라이언트는 SQLite의 파일 잠금을 알지 못한 채
파일을 교체하거나 충돌 사본을 만들 수 있고, 쓰기 중이면 DB가 손상된다.
백업 산출물은 이미 정지된 스냅샷이므로 동기화해도 안전하다.

## 영구 삭제와 백업의 관계

기존 백업 파일은 소급 수정하지 않는다. 대신 `backups/redaction_manifest.json`에
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

```bash
# 1. 백업 확인
scripts/idea_check restore-test --source ~/ClioData/backups/<타임스탬프>

# 2. 현재 DB를 보존한 뒤 교체
mv ~/ClioData/ideas.db ~/ClioData/ideas.db.broken
cp ~/ClioData/backups/<타임스탬프>/ideas.db ~/ClioData/ideas.db
cp -R ~/ClioData/backups/<타임스탬프>/attachments/ ~/ClioData/attachments/

# 3. 검증
scripts/idea_check integrity --strict
```

복구 후 건수와 무결성이 맞지 않으면 교체한 파일을 되돌리고 다른 백업을 시도한다.
