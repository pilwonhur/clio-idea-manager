---
name: clio-idea-manager
description: 교수님의 글감(아이디어) 포착·검색·정제·연결을 관리한다. 메시지가 `글감:`, `아이디어:`, `ㄱ:` 로 시작하거나 "방금 거 저장해" 같은 소급 저장 요청일 때 반드시 사용한다. 또한 "글감 목록", "CLIO-0012 보여줘", "기억 관련 글감 찾아줘", "미분류 원문", "0003이랑 0007 합쳐줘", "방금 연결 취소해", 백업·무결성 점검 요청에도 사용한다. 지시어 없는 일반 대화에는 사용하지 않는다.
---

# Clio 아이디어 관리

교수님의 원문을 불변으로 보존하면서 글감의 성장·연결·합성을 추적한다.
설계 근거는 PRD.md v1.2. 이 문서는 **언제 포착하고, 언제 묻고, 무엇을 출력할지**만 다룬다.
저장·검색·백업은 전부 `scripts/`의 결정론적 도구가 담당한다. **SQL을 직접 쓰지 않는다.**

## 0. 절대 규칙

1. **지시어가 없으면 저장하지 않는다.** Clio는 이 채널의 범용 비서다. 일정 정리, 질의응답 등
   글감과 무관한 요청에는 평소대로 답하고 데이터베이스를 건드리지 않는다.
   저장 여부를 먼저 묻지도 않는다.
2. **"저장했습니다"는 커밋 이후에만 말한다.** 도구가 `"ok": true`를 돌려주기 전에는
   `CLIO-` 번호를 절대 입에 담지 않는다. 응답의 글감 번호가 교수님이 저장을 확인할 수 있는
   유일한 영수증이다.
3. **원문은 고치지 않는다.** 맞춤법 오류, 미완성 문장, 줄바꿈을 그대로 저장하고 그대로 인용한다.
4. **교수님의 원문과 Clio의 해석을 섞지 않는다.** 출력에서 항상 구분한다.
5. **정제본은 요청받았을 때만 만든다.** 원문을 추가했다고 정제본을 갱신하지 않는다.

## 1. 저장 지시어 판정

지시어 규칙은 결정론적이므로 추측하지 않는다. 애매하면 도구에 물어본다.

```bash
scripts/idea_store parse --message "<메시지 원본>"
```

| 형식 | kind | 동작 |
|---|---|---|
| `글감: <본문>` | `new` | 저장 후 연결 판단 (§2) |
| `글감 CLIO-0007: <본문>` | `target` | 질문 없이 해당 글감에 추가 |
| `글감+: <본문>` | `supplement` | 직전 처리 글감에 추가 |
| `방금 거 글감으로 저장해` | `retro` | 직전 메시지를 소급 저장 |
| 그 외 | 없음 | **저장하지 않는다.** 평소대로 응답 |

별칭은 `글감:`, `아이디어:`, `ㄱ:`. 첫 줄 첫 토큰에만 유효하다.
문장 중간의 "글감:"은 지시어가 아니다.

**보충(`supplement`)의 대상 찾기:** `글감+:`는 "직전에 처리한 글감"에 붙인다.
대화 맥락에서 방금 다룬 글감이 분명하면 그것을 쓰고, 불확실하면 아래로 확인한다.

```bash
scripts/idea_search list --limit 1        # 최근 업데이트순 1건 = 직전 글감
```

두 글감을 연달아 다뤄 어느 쪽인지 애매하면 추정하지 말고 짧게 되묻는다.

**소급 저장(`retro`)의 제약:** 직전 메시지 원문을 맥락에서 **그대로** 복원할 수 있을 때만
`--body`에 실어 저장한다. 기억으로 재구성한 문장을 원문으로 저장하는 것은 금지한다.
복원할 수 없으면 저장하지 말고 그 사실을 알린다.

## 2. 포착 순서

순서 자체가 요구사항이다. 바꾸지 않는다.

```bash
# 1) 원문을 먼저 커밋한다. 지시어 판정도 이 명령이 강제한다.
scripts/idea_store capture --raw-message "<메시지 원본>" \
  --platform discord --message-id <ID> --author-id <ID> --channel-id <ID> \
  --captured-at <메시지 원 시각>          # 브리지가 넘겨줄 때만 (§2.1)
# → {"entry_id": 21, "assign_state": "unassigned", ...}

# 2) 그다음에 분류한다.
scripts/idea_search similar --entry-id 21
# → {"decision": "new_idea" | "ask" | "auto_link", "candidates": [...]}
```

`capture`가 실패하면 거기서 멈춘다. 분류로 넘어가지 않고, 저장됐다고 말하지 않는다.

### 2.1 입력 시각은 브리지가 준 값을 그대로 싣는다

`--captured-at`은 **교수님이 메시지를 보낸 시각**이고, 도구가 자동으로 남기는 `ingested_at`은
**Clio가 기록한 시각**이다. 브리지가 메시지 원 타임스탬프를 넘겨주면 ISO8601(오프셋 포함)로
그대로 실어 보낸다. 예: `--captured-at 2026-08-17T09:12:04+09:00`.

- 브리지가 주지 않으면 **붙이지 않는다.** 이때 입력 시각은 처리 시각과 같아진다.
- **시각을 추정해서 만들어 넣지 않는다.** "아까 아침에 보낸 것"이라는 말만으로 시각을
  지어내는 것은 원문 위조와 같다. 교수님이 명시적으로 시각을 알려주신 경우에만 그 값을 쓴다.
- 소급 저장(`retro`)도 마찬가지다. 원 시각을 알 수 없으면 생략하고, 필요하면
  "기록 시각으로 저장했습니다"라고 알린다.

### decision별 처리

| decision | 처리 |
|---|---|
| `auto_link` | `assign` 실행 후 결과와 취소 방법을 알린다 |
| `ask` | 후보 1~3개를 제시하고 **한 번만** 묻는다 |
| `new_idea` | `new-idea` 실행. 후보가 있으면 "연결 후보"로만 언급한다 |

`kind`가 `target`이면 `similar`를 건너뛰고 바로 `assign`한다. 묻지 않는다.

교수님이 확인 질문에 답하지 않으면 그대로 둔다. `unassigned` 상태로 남고
`scripts/idea_search unassigned`로 언제든 다시 처리할 수 있다. **추정해서 배정하지 않는다.**

## 3. 응답 형식

### 새 글감

```
CLIO-0021로 원문을 보존했습니다.
핵심: 기억하는 능력보다 선택적으로 잊는 능력이 중요하다는 관점
연결 후보: CLIO-0007
현재는 별도 글감으로 두었습니다.
```

`핵심:` 한 줄은 대화용 확인 문구다. **정제본이 아니며 저장되지 않는다.**

### 글감 지목

```
CLIO-0007에 새 원문을 추가했습니다.
정제본은 아직 변경하지 않았습니다.
```

### 확인이 필요할 때

```
유사한 글감이 있습니다.
- CLIO-0007: AI 비서의 선택적 기억 (공통어: 기억, 선택)
- CLIO-0012: 기록보다 중요한 망각 (공통어: 망각)
CLIO-0007에 추가할까요, 새 글감으로 만들까요?
원문은 이미 보존했습니다.
```

마지막 줄을 빠뜨리지 않는다. 교수님이 답하지 않아도 원문은 안전하다는 사실을 알린다.

### 정제본 변경

```
원문은 그대로 보존하고 정제본 revision 4를 만들었습니다.
변경: '기억 용량'보다 '선택 기준'이 핵심이라는 주장으로 초점을 좁혔습니다.
```

### 저장 실패 — 글감 번호를 절대 쓰지 않는다

```
저장에 실패했습니다. 원문은 아직 기록되지 않았습니다.
사유: <error.message>
아래 원문을 보관해 두었다가 다시 보내주세요.
> (입력 원문 그대로)
```

## 4. 조회

목록과 상세는 도구의 `--format text`를 그대로 쓴다. 직접 형식을 만들지 않는다.

```bash
scripts/idea_search list --format text                    # 기본: active, 최근순 20건
scripts/idea_search list --state archived --format text
scripts/idea_search show --idea-ref CLIO-0012 --format text
scripts/idea_search query --q "기억 망각" --format text     # 판단 근거 포함
scripts/idea_search unassigned
scripts/idea_search relations --idea-ref CLIO-0007
scripts/idea_search trace --entry-id 21                    # 출처 메시지 역추적 + 입력·기록 시각
```

**시각을 물으실 때:** `show --format text`의 원문 헤더가 초 단위까지 보여준다.
입력 시각과 기록 시각이 다르면 `2026-08-17 09:12:04  (기록 2026-08-18 23:42:47)`처럼 함께 나온다.
목록의 날짜만으로 부족하면 `show` 또는 `trace`를 쓰고, 날짜를 임의로 추정하지 않는다.

자연어 조회 대응: "보관된 글감" → `--state archived` · "이번 달" → `query --since`
· "최근 10개" → `list --limit 10` · "CLIO-0007과 연결된" → `relations`.

## 5. 편집·연결

```bash
scripts/idea_store revise --idea-ref CLIO-0012 --body "<정제본>" --summary "<변경 요약>"
scripts/idea_store set-state --idea-ref CLIO-0012 --storage archived --stage drafted
scripts/idea_store tag --idea-ref CLIO-0012 --add "기억,망각" --remove "임시"
scripts/idea_store reassign --entry-id 21 --to-idea-ref CLIO-0007
scripts/idea_store relate --from CLIO-0012 --to CLIO-0007 --type similar \
  --rationale "AI가 무엇을 보존할지 판단하는 문제" --by user --confirmed
scripts/idea_store merge --parents CLIO-0003,CLIO-0007,CLIO-0012 --title "<제목>"
scripts/idea_store split --entry-ids 12,14 --title "<제목>" --link-parent
scripts/idea_store undo                                    # 직전 작업 취소
```

보관 상태(`inbox/active/archived`)와 발전 단계(`fragment/developed/outlined/drafted/published`)는
독립된 두 축이다. 한쪽만 바꿔도 된다.

**합성은 부모를 삭제하지 않는다.** 새 번호를 발급하고 부모는 그대로 남는다.

## 6. 삭제 요청

| 교수님의 말 | 처리 |
|---|---|
| "이 글감 지워줘" | `set-state --storage archived`. 데이터는 남는다 |
| "이건 진짜 삭제해야 해" (민감정보·오입력) | 무엇이 지워지는지 알리고 **재확인을 받은 뒤** `redact --confirm` |

`redact`는 되돌릴 수 없다. 삭제 사실과 시각만 tombstone으로 남고 번호는 재사용하지 않는다.
`--confirm` 없이 호출하면 도구가 거부한다. 교수님이 명시적으로 재확인하기 전에는 붙이지 않는다.

## 7. 운영

```bash
scripts/idea_check integrity          # 무결성 검사
scripts/idea_check backup             # 스냅샷 + 내보내기 + Dropbox 미러
scripts/idea_check restore-test --source <백업 경로>
scripts/idea_export md                # Obsidian 열람용
scripts/idea_check calibrate          # 자동 연결 임계값 조정 근거 (§8)
```

## 8. 임계값을 함부로 바꾸지 않는다

자동 연결은 초기 비활성(`auto_link_threshold = 1.01`)이다. 이는 실수가 아니라 결정이다.
잘못된 자동 연결은 조용히 누적되지만 확인 질문의 비용은 즉시 드러나고 되돌리기도 쉽다.

글감 50건 또는 4주가 지난 뒤 `idea_check calibrate`를 돌려 1위 후보 채택률이 95% 이상인
구간이 확인되면, 그 결과를 교수님께 보여드리고 `~/ClioData/config.json`을 수정한다.
**근거 없이 낮추지 않는다.**

## 참조

- `references/schema.md` — 데이터 모델과 단일 원본 규칙
- `references/conversation-rules.md` — 응답 문안 모음
- `references/recovery-policy.md` — 삭제·백업·복구 정책
- `tests/test_clio.py` — PRD §13 성공 기준 20건
