# clio-idea-manager

**원문을 불변으로 보존하면서 아이디어의 성장·연결·합성을 추적하는 글감 관리 시스템.**

Discord·Telegram·WhatsApp 같은 메신저로 던진 한 문장이 대화 로그에 묻혀 사라지지 않도록,
지시어 하나로 원문을 그대로 데이터베이스에 커밋하고, 그 뒤에 분류·정제·연결을 이어붙인다.
판단은 LLM이 하고 저장은 결정론적 CLI 도구가 한다. 외부 의존성은 없다.

[![version](https://img.shields.io/badge/version-1.2.2-informational)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![dependencies](https://img.shields.io/badge/dependencies-none-success)](#요구사항)
[![storage](https://img.shields.io/badge/storage-SQLite%20%2B%20FTS5-003B57?logo=sqlite&logoColor=white)](#8-데이터-모델)
[![tests](https://img.shields.io/badge/tests-30%20passing-brightgreen)](#15-테스트)
[![license](https://img.shields.io/badge/license-MIT-blue)](#20-라이선스)

> Claude Code / Claude Agent Skill 형식으로 배포된다. 스킬을 설치하지 않고
> CLI 도구만 단독으로 쓰는 것도 가능하다 ([§5 빠른 시작](#5-빠른-시작-cli-단독)).

---

## 목차

1. [왜 만들었나](#1-왜-만들었나)
2. [설계 원칙 다섯 가지](#2-설계-원칙-다섯-가지)
3. [동작 예시](#3-동작-예시)
4. [설치](#4-설치)
5. [빠른 시작 (CLI 단독)](#5-빠른-시작-cli-단독)
6. [시스템 구조](#6-시스템-구조)
7. [저장 지시어](#7-저장-지시어)
8. [데이터 모델](#8-데이터-모델)
9. [포착 파이프라인](#9-포착-파이프라인)
10. [검색](#10-검색)
11. [CLI 레퍼런스](#11-cli-레퍼런스)
12. [출력 규약과 오류 코드](#12-출력-규약과-오류-코드)
13. [설정](#13-설정)
14. [운영: 백업·무결성·복구](#14-운영-백업무결성복구)
15. [테스트](#15-테스트)
16. [프로젝트 구조와 구현 현황](#16-프로젝트-구조와-구현-현황)
17. [FAQ와 트러블슈팅](#17-faq와-트러블슈팅)
18. [버전 정책](#18-버전-정책)
19. [기여](#19-기여)
20. [라이선스](#20-라이선스)

---

## 1. 왜 만들었나

일상 중 떠오른 아이디어를 메신저로 보내두면 대화 로그에는 남지만 **축적되지는 않는다.**
시간이 지나면 어떤 생각이 언제 어떻게 발전했는지 추적할 수 없고, 비슷한 생각을
여러 번 반복해 적었다는 사실조차 알기 어렵다.

이 시스템은 단순 메모장이 아니다. 하나의 생각이

- 여러 시점의 **원문(raw entry)** 으로 누적되고,
- 요청이 있을 때만 **정제본(revision)** 으로 다듬어지고,
- 다른 생각과 **연결(relation)** 되어 새 글감으로 **파생·합성**되는

과정을 데이터로 남기는 것이 목적이다.

### Clio는 누구인가 — 이름의 배경

**Clio는 저자의 개인 아이디어 관리 비서(AI 에이전트)의 이름이다.** 메신저 채널에 상주하면서
일정 정리나 질의응답 같은 범용 비서 노릇을 하고, 이 저장소의 스킬은 그중 **글감 관리** 능력을
맡는다. 그래서 문서와 응답 예시에 "Clio"가 화자로 등장하고, 글감 번호도 `CLIO-0001`로 붙는다.

이름은 그리스 신화의 아홉 뮤즈 중 **역사와 기록의 뮤즈 클레이오(Κλειώ, Kleiō)** 에서 왔다.
어원인 *kleiein*은 '알리다·기려 전하다'라는 뜻으로, 클레이오는 사람들이 잊어버릴 일을
두루마리에 적어 남기는 역할을 맡는다.

그 역할이 이 시스템의 임무와 정확히 겹친다. **이 시스템은 글을 대신 써주지 않는다.**
떠오른 말을 고치지 않고 그대로 남기고, 언제 무엇이 어떤 순서로 쌓였는지 추적하고,
필요할 때 다시 찾아낼 수 있게 하는 것 — 창작이 아니라 **보존과 재발견**이 전부다.
원문을 append-only로 두고 정제본을 별도 계층으로 분리한 설계([§2](#2-설계-원칙-다섯-가지))도
같은 태도에서 나왔다.

> 비서 이름이 다르다면 바꿔 쓰면 된다. 번호 접두어는 `config.json`의 `ref_prefix`로,
> 응답에 등장하는 화자 이름은 `SKILL.md`의 문안으로 각각 분리되어 있다.

### 해결하는 문제

| 문제 | 이 시스템의 답 |
|---|---|
| 저장 여부가 애매해서 놓친다 | `글감:` 지시어가 있으면 **무조건** 저장한다. 지시어가 없으면 **절대** 저장하지 않는다 |
| "저장했다"는 말이 거짓일 수 있다 | 트랜잭션 커밋 후에만 글감 번호를 발급한다. **번호 없는 응답 = 저장 안 됨** |
| LLM이 원문을 다듬어 버린다 | 원문은 append-only. 맞춤법·미완성 문장·줄바꿈까지 그대로 보존하고 해시로 검증한다 |
| 분류를 고민하다 입력이 끊긴다 | 원문을 먼저 커밋하고 분류는 나중에 한다. 답을 안 해도 `unassigned`로 안전하게 남는다 |
| 자동 연결이 조용히 오염시킨다 | 자동 연결은 **초기 비활성**. 실측으로 임계값 근거가 생긴 뒤에만 켠다 |
| 도구가 사라지면 데이터도 사라진다 | 표준 라이브러리만 사용하는 SQLite 단일 파일 + 언제든 Markdown/JSON 전량 내보내기 |

### 목표와 성공 기준

| # | 목표 | 판단 기준 |
|---|---|---|
| G1 | 아이디어 유실 0건 | 저장 지시된 원문은 어떤 중단 상황에서도 DB에 남거나, 저장되지 않았다는 사실이 응답에 드러난다 |
| G2 | 포착 마찰 최소화 | 짧은 입력 1건 처리에 되묻기 평균 0.5회 이하 |
| G3 | 원문 무결성 | 저장된 원문의 본문 해시가 영구 불변 (명시적 영구 삭제 절차 제외) |
| G4 | 재발견 가능성 | 한국어 키워드·부분 문장으로 목표 글감을 상위 5건 내에서 찾는다 |
| G5 | 데이터 이식성 | 언제든 Markdown·JSON 전량 내보내기가 가능하고 복구가 검증된다 |

### 범위 밖 (v1)

다중 사용자·권한 관리, 서버형 DB, 웹/모바일 UI, 임베딩 기반 의미 검색(5단계에서 조건부 검토),
완성 원고 집필, 지시어 없는 대화의 자동 저장.

---

## 2. 설계 원칙 다섯 가지

1. **포착은 명시적 지시로만 시작하되, 지시받은 뒤에는 분류보다 우선한다.**
   비서는 채널의 모든 메시지를 저장하지 않는다. 저장을 선언한 메시지에 한해,
   어디에 속할지 정하기 **전에** 원문부터 커밋한다.
2. **원문과 산출물은 절대 섞이지 않는다.** 원문은 append-only, 정제본은 별도 계층.
   출력에서도 `[날짜별 원문] ← 교수님 원문, 무수정` / `[정제본 rev.N] ← Clio 작성`으로 구분한다.
3. **단일 원본(single source of truth)을 명확히 한다.** 같은 사실을 두 곳에 저장하지 않는다.
   무엇이 원본이고 무엇이 계산값인지 스키마 수준에서 고정한다 ([§8](#8-데이터-모델)).
4. **"저장했다"는 말은 트랜잭션 커밋 이후에만 한다.** 부분 저장은 존재하지 않는다.
5. **판단은 LLM이, 저장은 결정론적 도구가 한다.** LLM은 임의 SQL을 실행하지 않으며,
   저장 지시어 판정조차 LLM이 아니라 저장 계층(`idea_store`)이 강제한다.

---

## 3. 동작 예시

### 새 글감 포착

```
사용자 ▸ 글감: 기억하는 능력보다 선택적으로 잊는 능력이 중요하다

Clio  ▸ CLIO-0021로 원문을 보존했습니다.
        핵심: 기억보다 망각의 선택 기준이 중요하다는 관점
        연결 후보: CLIO-0007
        현재는 별도 글감으로 두었습니다.
```

`핵심:` 한 줄은 대화용 확인 문구이며 **정제본이 아니고 저장되지 않는다.**
`CLIO-0021`이라는 번호가 저장을 확인할 수 있는 유일한 영수증이다.

### 확인이 필요할 때 — 물어보되, 원문은 이미 안전하다

```
Clio  ▸ 유사한 글감이 있습니다.
        - CLIO-0007: AI 비서의 선택적 기억 (공통어: 기억, 선택)
        - CLIO-0012: 기록보다 중요한 망각 (공통어: 망각)
        CLIO-0007에 추가할까요, 새 글감으로 만들까요?
        원문은 이미 보존했습니다.
```

답하지 않으면 원문은 `unassigned` 상태로 남고 `idea_search unassigned`로 언제든 다시 처리한다.
**추정해서 배정하지 않는다.**

### 저장 실패 — 번호를 절대 쓰지 않는다

```
Clio  ▸ 저장에 실패했습니다. 원문은 아직 기록되지 않았습니다.
        사유: storage_unavailable
        아래 원문을 보관해 두었다가 다시 보내주세요.
        > 기억하는 능력보다 선택적으로 잊는 능력이 중요하다
```

### 그 아래에서 실제로 실행되는 것

```bash
# 1) 원문을 먼저 커밋한다. 지시어 판정도 이 명령이 강제한다.
scripts/idea_store capture --raw-message "글감: 기억하는 능력보다…" \
  --platform discord --message-id 1234 --author-id 5678 --channel-id 9012
# → {"entry_id": 21, "assign_state": "unassigned", "ok": true}

# 2) 그다음에 분류한다.
scripts/idea_search similar --entry-id 21
# → {"decision": "new_idea" | "ask" | "auto_link", "candidates": [...]}

# 3) 결정에 따라 새 글감을 만들거나 기존 글감에 배정한다.
scripts/idea_store new-idea --entry-id 21 --title "선택적 망각" --tags "기억,망각"
# → {"ref": "CLIO-0021", "ok": true}
```

---

## 4. 설치

### 요구사항

- **Python 3.11 이상** (`zoneinfo` 사용). 표준 라이브러리만 쓰므로 `pip install`이 없다.
- SQLite는 Python에 내장된 것을 쓴다. **FTS5 확장이 활성화된 빌드**여야 한다
  (macOS·Ubuntu의 기본 Python은 활성화되어 있다. [확인 방법](#트러블슈팅)).
- 스킬로 쓰려면 Claude Code 등 Agent Skill을 읽는 클라이언트.

### 설치 스크립트

```bash
git clone https://github.com/pilwonhur/clio-idea-manager.git
cd clio-idea-manager
./install.sh
```

`install.sh`는 다음을 수행한다.

1. 스킬 코드를 `~/.claude/skills/clio-idea-manager/`에 넣고 실행 권한을 준다.
2. 데이터 디렉터리 `~/ClioData/`를 만들고 `config.json`을 준비한다.
3. 스키마를 생성(`idea_check init`, 멱등)하고 무결성 검사를 돌려 결과를 출력한다.

```
== Clio 스킬 설치: 1.2.2 ==
스킬  : /Users/you/.claude/skills/clio-idea-manager
데이터: /Users/you/ClioData  (ideas.db·첨부·백업은 건드리지 않음)

파일: 추가 12 · 갱신 0 · 유지 0 · 삭제 0
config.json 생성 (자동 연결은 초기 비활성 — PRD D1)
데이터베이스를 새로 만들었습니다. (스키마 v1)
무결성: 이상 없음

설치 완료 — clio-idea-manager 1.2.2
데이터베이스: /Users/you/ClioData/ideas.db
```

**스킬 코드와 데이터는 물리적으로 분리되어 있다.** 스킬을 몇 번을 다시 설치하거나
업데이트해도 `~/ClioData/`는 건드리지 않는다.

### 업데이트

**지우고 다시 설치하지 않는다.** 같은 명령이 첫 설치와 업데이트를 모두 처리하며,
바뀐 파일만 제자리에서 갱신한다.

```bash
cd clio-idea-manager
./update.sh              # git pull --ff-only → install.sh
```

`git clone`이 아니라 압축본으로 받았다면 새 사본에서 `./install.sh`를 실행하면 된다.
기존 설치 디렉터리를 미리 지울 필요가 없다.

```bash
cd ~/Downloads/clio-idea-manager-new
./install.sh             # 기존 설치를 알아서 찾아 갱신한다
```

실행하면 무엇이 바뀌었는지 건수로 보고한다.

```
== Clio 스킬 업데이트: 1.2.1 → 1.2.2 ==
스킬  : /Users/you/.claude/skills/clio-idea-manager
데이터: /Users/you/ClioData  (ideas.db·첨부·백업은 건드리지 않음)

이전 버전 보관: /Users/you/ClioData/skill-prev
파일: 추가 0 · 갱신 3 · 유지 9 · 삭제 0
config.json 유지 (변경 없음)
기존 데이터베이스를 그대로 사용합니다. (스키마 v1)
무결성: 이상 없음

업데이트 완료 — clio-idea-manager 1.2.2
변경 내용: /Users/you/.claude/skills/clio-idea-manager/CHANGELOG.md
```

업데이트가 보장하는 것은 다음과 같다.

| 항목 | 처리 |
|---|---|
| `~/ClioData/ideas.db`, `attachments/`, `backups/` | **손대지 않는다** |
| `config.json`의 사용자 수정값 | **그대로 유지한다** (임계값·경로 등) |
| 새 버전에서 추가된 설정 키 | 기본값으로 **채워 넣는다** — 없던 키만 |
| 바뀐 스킬 파일 | 덮어쓴다 |
| 이전 버전에만 있던 파일 | 지운다 (낡은 스크립트가 남아 오작동하지 않도록) |
| 직전 스킬 코드 | `~/ClioData/skill-prev/`에 한 세대 보관한다 (스킬 디렉터리 밖) |
| 스키마 | `idea_check init`이 멱등하게 보장하고, 끝나면 무결성을 검사한다 |

설치된 버전은 언제든 확인할 수 있다.

```bash
~/.claude/skills/clio-idea-manager/scripts/idea_check --version    # clio-idea-manager 1.2.0
```

변경 내용은 [`CHANGELOG.md`](CHANGELOG.md)와 [Releases](https://github.com/pilwonhur/clio-idea-manager/releases)에 있다.

### 업데이트를 되돌리기

새 버전이 이상하면 직전 스킬 코드로 되돌린다. **데이터는 관여하지 않으므로 그대로 남는다.**

```bash
rm -rf ~/.claude/skills/clio-idea-manager
cp -R ~/ClioData/skill-prev ~/.claude/skills/clio-idea-manager
~/.claude/skills/clio-idea-manager/scripts/idea_check --version    # 되돌아간 버전 확인
```

> 롤백본은 **`~/.claude/skills/` 밖에** 둔다. 스킬 디렉터리 안에 사본을 두면 `SKILL.md`가
> 딸려 있어 같은 설명의 스킬이 하나 더 등록되고, 어느 쪽이 호출될지 알 수 없게 된다.
> 보관 위치는 `CLIO_PREV_DIR`로 바꿀 수 있다.

특정 버전으로 다시 설치하고 싶다면 저장소에서 그 태그를 꺼내 `install.sh`를 실행한다.

```bash
cd clio-idea-manager
git checkout v1.1.0 && ./install.sh
git checkout main               # 나중에 최신으로 복귀
```

### 설치 경로 바꾸기

| 환경 변수 | 기본값 | 의미 |
|---|---|---|
| `CLIO_SKILL_DIR` | `~/.claude/skills/clio-idea-manager` | 스킬 설치 위치 (설치 시점에만 사용) |
| `CLIO_DATA_DIR` | `~/ClioData` | 데이터 위치 (**모든 CLI 실행 시 참조**) |

```bash
CLIO_DATA_DIR=~/work/ClioData ./install.sh
CLIO_DATA_DIR=~/work/ClioData python3 scripts/idea_search list --format text
```

`CLIO_DATA_DIR`는 격리된 실험이나 테스트용 데이터셋을 만들 때도 그대로 쓰인다.

### 제거

```bash
rm -rf ~/.claude/skills/clio-idea-manager   # 스킬만 제거. 데이터는 남는다
# 데이터까지 지우려면 (되돌릴 수 없다)
# rm -rf ~/ClioData
```

---

## 5. 빠른 시작 (CLI 단독)

스킬 없이도 CLI만으로 전 기능을 쓸 수 있다. 아래는 임시 디렉터리에서 실행하는 3분 투어다.

```bash
export CLIO_DATA_DIR=/tmp/clio-demo
python3 scripts/idea_check init
```

**1. 지시어 판정 (쓰기 없음)**

```bash
python3 scripts/idea_store parse --message "글감: 기억하는 능력보다 선택적으로 잊는 능력이 중요하다"
```
```json
{ "trigger": true, "kind": "new", "idea_ref": null,
  "body": "기억하는 능력보다 선택적으로 잊는 능력이 중요하다",
  "empty": false, "reason": "지시어 '글감' 인식", "ok": true }
```

**2. 원문 커밋**

```bash
python3 scripts/idea_store capture \
  --raw-message "글감: 기억하는 능력보다 선택적으로 잊는 능력이 중요하다" \
  --platform discord --message-id m1
```
```json
{ "entry_id": 1, "duplicate": false, "assign_state": "unassigned",
  "captured_at": "2026-08-18T21:48:35+09:00",
  "body_hash": "1db7a0dd346e45ec…", "attachments": [],
  "message": "원문을 보존했습니다. 아직 글감에 배정되지 않았습니다.", "ok": true }
```

**3. 분류 판단 → 글감 생성**

```bash
python3 scripts/idea_search similar --entry-id 1
python3 scripts/idea_store new-idea --entry-id 1 --title "선택적 망각" --tags "기억,망각"
# → {"ref": "CLIO-0001", "idea_id": 1, "storage_state": "active", "dev_stage": "fragment", "ok": true}
```

**4. 정제본과 관계**

```bash
python3 scripts/idea_store revise --idea-ref CLIO-0001 \
  --body "선택적 망각은 기억 용량의 문제가 아니라 선택 기준의 문제다." \
  --summary "초점을 선택 기준으로 좁힘"

python3 scripts/idea_store relate --from CLIO-0001 --to CLIO-0002 --type similar \
  --rationale "무엇을 보존할지 판단하는 문제" --by user --confirmed
```

**5. 조회 — 사람이 읽는 형식**

```bash
python3 scripts/idea_search show --idea-ref CLIO-0001 --format text
```
`text` 형식도 JSON 봉투의 `text` 필드로 나온다. 그 값을 출력하면 다음과 같다.

```
CLIO-0001 — 선택적 망각
보관 상태: active   발전 단계: developed
생성일: 2026-08-18 21:48   최종 업데이트: 2026-08-18 21:48
전체 기록일: 08-18
키워드: 기억, 망각
관련 글감: CLIO-0002 (similar, 확인됨)
부모 글감: 없음
파생 글감: 없음

[날짜별 원문]  ← 교수님 원문, 무수정
2026-08-18 21:48:35
기억하는 능력보다 선택적으로 잊는 능력이 중요하다

[정제본 rev.1]  ← Clio 작성
선택적 망각은 기억 용량의 문제가 아니라 선택 기준의 문제다.
```

**6. 검색 — 판단 근거를 함께 낸다**

```bash
python3 scripts/idea_search query --q "기억 망각" --format text
```
```
CLIO-0001 — 공통어: 기억, 망각
제목: 선택적 망각  (점수 0.85)

CLIO-0002 — 공통어: 기억
제목: AI 비서의 기억 기준  (점수 0.5508)
```

**7. 내보내기와 백업**

```bash
python3 scripts/idea_export md          # ~/ClioData/exports/markdown/ (Obsidian 열람용)
python3 scripts/idea_check backup       # 스냅샷 + 내보내기 + 미러 복사
python3 scripts/idea_check integrity    # 8종 무결성 검사
```

---

## 6. 시스템 구조

### 2계층 분리

```
[메신저: Discord · Telegram · WhatsApp · CLI …]
        ↓
[브리지: 제3자 도구, 수정하지 않음]
        ↓
[Clio 스킬: 판단 계층 (SKILL.md)]
        ↓   저장 지시어가 있을 때만
[관리 도구: 결정론적 저장 계층 (scripts/)]
        ↓
[SQLite]
        ↓
[Markdown / JSON 내보내기 · 백업]
```

- **판단 계층 (`SKILL.md`)** — 언제 포착하고, 언제 묻고, 무엇을 어떻게 출력할지 결정한다.
  SQL을 직접 만들지 않는다.
- **저장 계층 (`scripts/`)** — 검증된 명령만 노출한다. 스키마 제약, 단일 트랜잭션,
  중복 방지, 무결성 검사를 책임진다.

**브리지 전제:** 메신저 메시지를 전달하고 응답을 되돌리는 계층은 제3자 도구이며 이 프로젝트에서
수정하지 않는다. 따라서 저장의 진입점은 브리지가 아니라 스킬이고, 진입 조건은 저장 지시어다.
브리지가 어떤 메타데이터를 넘겨주는지에 의존하지 않도록 **2단 멱등성 규칙**을 쓴다
([§9 중복 방지](#중복-방지-2단-멱등성)).

현재 저자가 쓰는 입력 채널은 Discord지만, 그것은 브리지가 붙어 있는 채널일 뿐이다.
`source.platform`이 채널을 일반화하므로 Telegram·WhatsApp·CLI·파일 임포트를
코드 변경 없이 함께 쓸 수 있다.

### 디렉터리 배치

```
~/.claude/skills/clio-idea-manager/     # 스킬 (교체 가능)
├── SKILL.md                            # 판단 계층
├── scripts/                            # 저장 계층
├── references/                         # 스키마·대화 규칙·복구 정책
├── evals/evals.json
└── tests/test_clio.py

~/ClioData/                             # 데이터 (영속, Dropbox 밖)
├── config.json                         # 임계값·경로 등 운영 설정
├── ideas.db                            # SQLite 원본 (WAL)
├── attachments/ab/abcd…ef.png          # 첨부 원본 사본 (sha256 앞 2자리로 분산)
├── exports/                            # markdown/ · json
└── backups/<타임스탬프>/                # 스냅샷 + redaction_manifest.json

~/Dropbox/ClioBackup/                   # 백업 산출물만 동기화
```

> **왜 데이터를 Dropbox 밖에 두는가.** SQLite는 파일 잠금으로 동시성을 제어하는데,
> 동기화 클라이언트는 잠금 상태를 모른 채 파일을 교체하거나 충돌 사본을 만든다.
> 쓰기 중인 DB에 그런 일이 생기면 파일 자체가 손상된다. 원본은 로컬에 두고
> **정지된 스냅샷인 백업 산출물만** 동기화한다. 이 규칙은 테스트(TC20)로 강제된다.

---

## 7. 저장 지시어

저장은 지시어가 있을 때만 시작된다. 판정은 **결정론적이며 저장 계층이 강제한다.**
LLM이 "저장할 만한 내용 같다"고 판단해 저장하는 경로는 존재하지 않는다.

| 형식 | `kind` | 동작 |
|---|---|---|
| `글감: <본문>` | `new` | 저장 후 유사 후보를 보고 연결을 판단한다 |
| `글감 CLIO-0007: <본문>` | `target` | 묻지 않고 해당 글감에 원문을 추가한다 |
| `글감+: <본문>` | `supplement` | 직전에 처리한 글감에 추가한다 |
| `방금 거 글감으로 저장해` | `retro` | 직전 메시지를 소급 저장한다 |
| 그 외 전부 | — | **저장하지 않는다.** 평소대로 대화한다 |

- **별칭:** `글감:` · `아이디어:` · `ㄱ:`
- **첫 줄 첫 토큰에만 유효하다.** 문장 중간의 "글감:"은 지시어가 아니다(TC16).
- 여러 줄 입력은 줄바꿈·빈 줄까지 원문 그대로 보존되며, 지시어 토큰만 본문에서 빠진다(TC14).
- 지시어만 있고 본문이 없으면 `empty_body`로 거부한다. 조용히 빈 원문을 만들지 않는다.
- `retro`는 직전 원문을 **그대로 복원할 수 있을 때만** `--body`로 넘겨 저장한다.
  기억으로 재구성한 문장을 원문으로 저장하는 것은 금지한다.

```bash
scripts/idea_store parse --message "<메시지 원본>"   # 쓰기 없이 판정만
```

---

## 8. 데이터 모델

정본 DDL은 `scripts/clio_core.py`의 `SCHEMA_SQL`이다. `references/schema.md`가 그 의도를 설명한다.

### 단일 원본 규칙

같은 사실이 두 곳에 저장되면 반드시 어긋난다. 무엇이 원본이고 무엇이 계산값인지 고정한다.

| 항목 | 단일 원본 | 파생(계산) |
|---|---|---|
| 현재 정제본 | `revision` 행 | `idea.latest_revision_id`가 가리키는 참조 |
| 부모·파생 관계 | `idea_relation` | 조회 시 계산 (`idea`에 목록 컬럼이 없다) |
| 키워드 | `tag` + `idea_tag` | 표시용 문자열 |
| 원문 | `raw_entry` (append-only) | 목록의 미리보기 |
| 수정일 목록 | `raw_entry.captured_at` + `revision.created_at` | 조회 시 집계 |

### 테이블

| 테이블 | 역할 |
|---|---|
| `idea` | 글감의 **현재 상태**만. 번호(`ref`), 제목, 보관 상태, 발전 단계, 최신 revision 참조. `deleted_at`은 tombstone |
| `raw_entry` | 원문. 수정하지 않고 계속 쌓는다. `idea_id`가 NULL이면 미분류. `body_hash`로 무결성 검증, `redacted_at`은 영구 삭제 흔적. 시각은 두 개다 — `captured_at`(보낸 시각), `ingested_at`(기록한 시각) |
| `source` | 출처. `(platform, message_id)`가 유니크. Discord 외 채널로 일반화 |
| `attachment` | 첨부 원본 사본. 파일명이 내용 해시이므로 같은 파일은 한 번만 저장된다 |
| `revision` | 정제본 이력. 불변. `rev_no`는 글감 안에서 1부터 증가 |
| `idea_relation` | 관계의 단일 원본. 근거·확신도·사용자 확인 여부를 함께 기록 |
| `tag` / `idea_tag` | 키워드. `alias_of`로 명칭을 통합 |
| `action_log` | 되돌리기와 감사 추적의 근거. `payload.undo`에 역연산이 들어 있다 |
| `search_fts` | FTS5 인덱스. 원본이 아니라 파생이므로 언제든 재생성 가능 |
| `meta` | 스키마 버전 등 |

### 상태 2축

보관 상태와 발전 단계는 **독립된 두 축**이다. 한쪽만 바꿔도 된다.

| 축 | 값 |
|---|---|
| 보관 상태 `storage_state` | `inbox` → `active` → `archived` |
| 발전 단계 `dev_stage` | `fragment` → `developed` → `outlined` → `drafted` → `published` |

`revise`로 정제본을 처음 만들면 `fragment`인 글감만 `developed`로 자동 승격된다.
그 이상의 단계는 명시적으로 지정해야 올라간다.

### 관계 유형과 방향성

| 유형 | 대칭 | 의미 |
|---|---|---|
| `similar` | ○ | 유사하다 |
| `related` | ○ | 관련 있다 |
| `expands` | ✕ | A가 B를 확장한다 |
| `contradicts` | ✕ | A가 B와 상충한다 |
| `derived_from` | ✕ | A가 B에서 파생됐다 (합성·분리에서 자동 생성) |

대칭 관계는 저장 시 `from_idea_id < to_idea_id`로 정규화하고 `symmetric=1`을 둔다.
무결성 검사가 이 규칙 위반을 잡는다.

**합성은 부모를 삭제하지 않는다.** 새 번호를 발급하고, 각 부모에 대해
`(새 글감 → 부모, derived_from)` 관계를 만든다. 부모는 그대로 남는다(TC4).

### 시각

모든 시각은 **ISO8601, 초 단위, 오프셋 포함**으로 저장한다 (`2026-08-17T09:12:04+09:00`).
마이크로초는 버린다. 시간대는 `config.json`의 `timezone`(기본 `Asia/Seoul`)을 따른다.

| 컬럼 | 뜻 |
|---|---|
| `raw_entry.captured_at` | **보낸 시각.** 브리지가 메시지 원 타임스탬프를 주면 `--captured-at`으로 그대로 싣는다 |
| `raw_entry.ingested_at` | **기록한 시각.** 도구가 항상 자동으로 남긴다 |
| `idea.created_at` / `updated_at` | 글감 생성·최종 변경 시각 |
| `revision.created_at`, `idea_relation.created_at`, `action_log.created_at` | 각 사건의 발생 시각 |

`--captured-at`을 주지 않으면 `captured_at`은 처리 시각과 같아진다. **시각을 추정해서
채워 넣지 않는다** — 원문 위조와 같기 때문이다.

표시 계층은 용도에 따라 잘라 쓴다. 목록(`list`)은 날짜만, 상세(`show`)의 원문 헤더는
**초 단위까지** 보여주며, 보낸 시각과 기록한 시각이 다르면 함께 낸다.

```
[날짜별 원문]  ← 교수님 원문, 무수정
2026-08-17 09:12:04  (기록 2026-08-18 23:42:47)
기억하는 능력보다 선택적으로 잊는 능력이 중요하다
```

저장된 전체 값(오프셋 포함)은 `show`의 JSON 출력, `trace`, `export json`,
`export md`에서 그대로 볼 수 있다.

### 글감 번호

`CLIO-0001` 형식(기본 4자리). 9999를 넘으면 자릿수가 늘어나며, 정렬은 문자열이 아니라
숫자 순서를 지킨다. **번호는 재사용하지 않는다.** 영구 삭제로 번호에 공백이 생기는 것은
정상이며 무결성 검사도 이를 오류로 보지 않는다.

---

## 9. 포착 파이프라인

**순서 자체가 요구사항이다.** 원문 커밋이 항상 분류보다 먼저다.

```
지시어 판정 ──▶ ① capture (트랜잭션 A: 원문 + 출처 + 첨부)
                      │  실패하면 여기서 멈춘다. 저장됐다고 말하지 않는다.
                      ▼
                 ② similar (읽기: 후보 점수 계산 + 확신도 분기)
                      ▼
   ┌──────────────────┼──────────────────┐
auto_link            ask             new_idea
   │                  │                  │
assign 실행     후보 1~3건 제시     new-idea 실행
+ 취소 방법 안내   (한 번만 묻는다)   (후보는 "연결 후보"로만 언급)
```

### 확신도 분기

| 결정 | 조건 | 처리 |
|---|---|---|
| `auto_link` | 1위 점수 ≥ `auto_link_threshold` **그리고** 1위−2위 ≥ `auto_link_margin` | 자동 배정 후 결과와 취소 방법을 알린다 |
| `ask` | 1위 점수 ≥ `ask_threshold` | 후보를 최대 3건 제시하고 **한 번만** 묻는다 |
| `new_idea` | 그 외 | 새 글감을 만든다 |

`mention_threshold`(기본 0.15) 미만의 후보는 "연결 후보"로도 언급하지 않는다. 출력 소음 방지다.

기본 설정에서 `auto_link_threshold = 1.01`, 즉 **자동 연결은 꺼져 있다.** 실수가 아니라 결정이다.
잘못된 자동 연결은 조용히 누적되지만, 확인 질문의 비용은 즉시 드러나고 되돌리기도 쉽다.
켜는 절차는 [§14 임계값 조정](#임계값을-함부로-바꾸지-않는다)에 있다.

사용자가 확인 질문에 답하지 않으면 그대로 둔다. 원문은 `unassigned`로 남고
`idea_search unassigned`로 언제든 다시 처리할 수 있다. **추정해서 배정하지 않는다(TC11).**

### 중복 방지: 2단 멱등성

브리지가 어떤 메타데이터를 주는지에 의존하지 않기 위해 두 단계로 판정한다.

1. `message_id`가 있으면 `(platform, message_id)`로 판정한다 (DB 유니크 제약).
2. 없으면 `(body_hash, 같은 날짜)`로 판정한다.

어느 쪽이든 코드 변경 없이 동작한다. 중복이면 새로 저장하지 않고 기존 `entry_id`를 돌려준다
(TC1, TC19). 의도적으로 다시 저장하려면 `--force`를 쓴다.

### 첨부파일

CDN 링크는 만료되거나 채널 삭제와 함께 사라지므로 **원본 파일을 내려받아 복사 보관한다.**
파일명은 내용 해시(sha256)라 같은 파일의 중복 저장이 자동으로 막히고, 원래 파일명은 DB에
따로 기록한다. 무결성 검사가 파일 존재와 해시 일치를 확인한다(TC17).

```bash
scripts/idea_store capture --raw-message "글감: …" --attach ./photo.png
scripts/idea_store capture --raw-message "글감: …" --attach-url "https://…/photo.png"
```

---

## 10. 검색

SQLite FTS5 + 다중 신호 가중 점수. 외부 형태소 분석기 없이 한국어를 다룬다.

### 점수 산출

| 신호 | 가중 | 산출 방식 |
|---|---|---|
| 제목 일치 | 0.30 | 질의어가 제목에 포함된 비율 |
| 태그 일치 | 0.25 | 일치 태그 수 / 질의에서 추출한 태그 수 |
| 원문·정제본 부분 문자열 일치 | 0.20 | 조사 변화 보완, 2자 이상 어절 기준 |
| FTS5 점수 | 0.15 | `bm25()`를 0~1로 정규화 |
| 최근성 | 0.05 | 최종 업데이트 기준 90일 반감 |
| 연결 글감 정보 | 0.05 | 관계된 글감이 매칭되면 가산 |

```
score = Σ(가중 × 신호) × 상태계수
상태계수: active 1.0 / inbox 1.0 / archived 0.7
```

`archived`는 감점하되 **검색 결과에서 배제하지는 않는다**(TC6). 기본 목록에서만 숨긴다.

### 한국어 어간 처리

FTS5의 `unicode61` 토크나이저는 한국어 어절을 통째로 자른다. '기억은'으로 검색하면
'기억하는'을 찾지 못한다. 형태소 분석기는 외부 의존성을 요구하므로(설계 결정 D3 위배)
조사·어미 목록과 접두 절단을 결합한 **근사 어간 추출**을 쓴다.

```
기억은   → {기억은, 기억}
기억하는 → {기억하는, 기억하, 기억}
망각을   → {망각을, 망각}
```

FTS 질의는 가장 짧은 어간의 prefix로 던지고, 부분 문자열 신호도 어간 기준으로 판정한다.
실측에서 이 처리만으로 동일 주제 글감의 점수가 **0.09 → 0.28**로 올랐다.
근사이므로 오매칭이 생길 수 있으나, 자동 연결이 초기 비활성이라 확인 질문 단계에서 걸러진다.

### 근거를 반드시 함께 낸다

검색·연결 후보를 제시할 때는 점수와 함께 판단 근거를 출력한다.

```
CLIO-0007 — 공통어: 기억, 선택
제목: AI 비서의 선택적 기억  (점수 0.5508)
```

`similar --entry-id N`의 JSON에는 신호별 기여도까지 들어 있다.

```json
{ "ref": "CLIO-0001", "score": 0.245,
  "signals": { "title": 0.0, "tag": 0.1, "substring": 0.1,
               "fts": 1.0, "recency": 1.0, "relation": 0.0 },
  "common_terms": ["기억하고"], "common_tags": ["기억"] }
```

### 의미 검색은 아직 넣지 않았다

임베딩 검색은 5단계 조건부 항목이다. 실제로 축적된 글감으로 현재 방식의 재현율을 평가하고,
미달 사례가 반복 확인될 때만 도입한다. 처음부터 의미 검색에 의존하면 표현은 유사하나
**논점이 다른 글감을 잘못 병합할** 위험이 있다.

---

## 11. CLI 레퍼런스

도구는 넷이다. 모든 명령은 JSON을 표준출력으로 내고, 실패 시 종료 코드 1을 낸다.

```
idea_store    쓰기   — 원문 저장, 글감 생성·연결, 정제본, 관계, 상태, 되돌리기, 삭제
idea_search   읽기   — 목록, 상세, 검색, 유사 후보, 미분류, 역추적, 관계
idea_export   내보내기 — Markdown, JSON
idea_check    운영   — 초기화, 무결성, 백업, 복구 시험, 대조, 임계값 근거
```

### `idea_store` — 쓰기 계층

모든 명령은 **단일 트랜잭션**이다. 부분 저장을 허용하지 않는다(TC2).

| 명령 | 설명 | 주요 옵션 |
|---|---|---|
| `parse` | 저장 지시어 판정 (쓰기 없음) | `--message` (필수) |
| `capture` | 원문 저장 (트랜잭션 A) | `--raw-message` \| `--body` \| `--body-file`, `--platform`, `--message-id`, `--guild-id`, `--channel-id`, `--thread-id`, `--author-id`, `--reply-to-id`, `--edited`, `--raw-payload`, `--captured-at`, `--type`, `--attach`, `--attach-url`, `--force` |
| `new-idea` | 새 글감 생성 | `--entry-id`, `--title`, `--tags`, `--storage`, `--stage`, `--force` |
| `assign` | 원문을 글감에 배정 | `--entry-id`, `--idea-ref` (필수), `--force` |
| `reassign` | 잘못 연결된 원문 이동 | `--entry-id`, `--to-idea-ref` (필수) |
| `revise` | 정제본 revision 추가 | `--idea-ref`, `--summary` (필수), `--body` \| `--body-file`, `--author` |
| `relate` | 글감 관계 설정 | `--from`, `--to`, `--type` (필수), `--rationale`, `--confidence`, `--by`, `--confirmed` |
| `merge` | 합성 글감 생성 (부모 유지) | `--parents`, `--title` (필수), `--tags`, `--rationale`, `--by` |
| `split` | 일부 원문을 새 글감으로 분리 | `--entry-ids` (필수), `--title`, `--link-parent`, `--by` |
| `set-state` | 보관 상태·발전 단계 변경 | `--idea-ref` (필수), `--storage`, `--stage` |
| `tag` | 태그 추가·제거·통합 | `--idea-ref`, `--add`, `--remove`, `--alias 옛이름=새이름` |
| `undo` | 직전 작업 취소 | `--action-id` |
| `redact` | 원문 영구 삭제 | `--entry-id`, `--reason` (필수), `--confirm` |
| `delete-idea` | 글감 tombstone 처리 | `--idea-ref`, `--reason` (필수), `--confirm` |

- `--raw-message`는 지시어 판정을 **강제한다.** 지시어가 없으면 `no_trigger`로 거부하고
  아무것도 쓰지 않는다(TC13).
- `--body` / `--body-file` / 표준입력은 지시어 판정을 거치지 않는다. 소급 저장과
  파일 임포트 경로다. 원문 그대로 저장하며 앞뒤 개행만 정리한다.
- `--captured-at`은 **메시지를 보낸 시각**이다(ISO8601, 오프셋 포함). 생략하면 처리 시각이
  들어가며, `ingested_at`은 어느 경우에나 도구가 자동으로 남긴다 ([§8 시각](#시각)).
- `undo`가 되돌릴 수 있는 작업: `assign`, `new_idea`, `reassign`, `revise`, `relate`,
  `merge`, `split`, `set_state`, `tag`. **`capture`와 영구 삭제는 되돌리지 않는다.**

### `idea_search` — 읽기 계층

데이터베이스를 변경하지 않는다. 예외는 `similar` 하나로, 임계값 조정 근거를 남기기 위해
후보 제시 이력을 `action_log`에 기록한다(`--no-log`로 끌 수 있다).

| 명령 | 설명 | 주요 옵션 |
|---|---|---|
| `list` | 글감 목록 (기본: active, 최근 업데이트순 20건) | `--state {inbox,active,archived,all}`, `--stage`, `--limit`, `--order {updated,created,ref}`, `--format {json,text}` |
| `show` | 특정 글감 상세 | `--idea-ref` (필수), `--include-deleted`, `--format` |
| `query` | 검색 (판단 근거 포함) | `--q` (필수), `--state`, `--since`, `--until`, `--limit`, `--format` |
| `similar` | 유사 후보 + 확신도 분기 | `--text` \| `--entry-id`, `--limit`, `--no-log` |
| `unassigned` | 미분류 원문 목록 | `--limit` |
| `trace` | 출처 메시지 역추적 | `--entry-id` (필수) |
| `relations` | 관계와 그 근거 조회 | `--idea-ref` (필수) |

자연어 조회 대응 — "보관된 글감" → `--state archived` · "이번 달" → `query --since`
· "최근 10개" → `list --limit 10` · "CLIO-0007과 연결된" → `relations`.

### `idea_export` — 내보내기

| 명령 | 설명 | 옵션 |
|---|---|---|
| `md` | Markdown 내보내기 (Obsidian 열람용) | `--out`, `--include-deleted` |
| `json` | JSON 전량 내보내기 | `--out`, `--include-deleted` |

Markdown은 글감당 한 파일 + `index.md`를 만든다. 원문과 정제본이 절대 섞이지 않는다.

```markdown
# CLIO-0001 — 선택적 망각

- 보관 상태: active
- 발전 단계: developed
- 키워드: 기억, 망각
- 관련 글감: CLIO-0002 (similar)

## 원문 (교수님 작성 · 무수정)

### 2026-08-18T21:48:35+09:00  ·  entry 1  ·  idea

기억하는 능력보다 선택적으로 잊는 능력이 중요하다

## 정제본 (Clio 작성)

### rev.1 · 2026-08-18T21:48:53+09:00 ← 최신

> 변경: 초점을 선택 기준으로 좁힘

선택적 망각은 기억 용량의 문제가 아니라 선택 기준의 문제다.
```

### `idea_check` — 운영

| 명령 | 설명 | 옵션 |
|---|---|---|
| `init` | 데이터 디렉터리와 스키마 생성 (멱등) | — |
| `integrity` | 무결성 검사 8종 | `--strict` (문제가 있으면 종료 코드 1) |
| `backup` | 스냅샷 + 내보내기 + 미러 복사 | `--out`, `--no-mirror` |
| `restore-test` | 백업 복구 시험 | `--source` (필수) |
| `reconcile` | 채널 이력 대조용 보고 | `--since` |
| `calibrate` | 자동 연결 임계값 조정 근거 | — |

---

## 12. 출력 규약과 오류 코드

### 성공

```json
{ "…명령별 필드…", "ok": true }
```

`--format text`를 준 명령도 JSON 봉투를 유지하며 사람이 읽을 문자열을 `text` 필드에 담는다.

```json
{ "text": "CLIO-0001 | 2026-08-18 → 2026-08-18\n키워드: …", "count": 2, "ok": true }
```

### 실패

종료 코드 **1**, 표준출력에 오류 JSON.

```json
{ "ok": false, "error": { "code": "no_trigger", "message": "저장 지시어가 없습니다.", "parsed": {…} } }
```

서브커맨드를 주지 않으면 도움말을 출력하고 종료 코드 **2**를 낸다.

### 오류 코드

| 코드 | 뜻 |
|---|---|
| `no_trigger` | `--raw-message`에 저장 지시어가 없다. 아무것도 쓰지 않았다 |
| `empty_body` / `no_body` / `no_text` | 본문이 비었거나 전달되지 않았다 |
| `retro_needs_body` | 소급 저장은 원문을 `--body`로 함께 넘겨야 한다 |
| `idea_not_found` / `entry_not_found` / `tag_not_found` | 대상이 없다 |
| `idea_deleted` | tombstone 처리된 글감이다 |
| `already_assigned` | 이미 배정된 원문이다 (`reassign`을 쓴다) |
| `self_relation` | 같은 글감끼리 관계를 만들 수 없다 |
| `need_parents` / `need_entries` / `no_title` | 합성·분리에 필요한 인자가 부족하다 |
| `confirm_required` | 영구 삭제에 `--confirm`이 없다 |
| `nothing_to_undo` / `already_undone` / `not_undoable` | 되돌릴 수 없다 |
| `db_missing` / `backup_missing` / `attachment_missing` | 파일이 없다 |
| `integrity_failed` | 무결성 검사 실패 (`--strict`) |
| `db_error` | SQLite 오류 |
| `storage_unavailable` | 데이터 디렉터리에 접근할 수 없다 (TC9) |

**어떤 실패에서도 성공한 것처럼 응답하지 않는다.** 판단 계층은 `ok: true`를 받기 전에는
`CLIO-` 번호를 출력하지 않는다(TC15).

---

## 13. 설정

`~/ClioData/config.json`. 파일에 없는 키는 내장 기본값을 쓴다(딕셔너리는 키 단위 병합).

```json
{
  "backup_mirror": "~/Dropbox/ClioBackup",
  "timezone": "Asia/Seoul",
  "ref_prefix": "CLIO",
  "ref_digits": 4,
  "auto_link_threshold": 1.01,
  "auto_link_margin": 0.15,
  "ask_threshold": 0.35,
  "mention_threshold": 0.15,
  "candidate_limit": 3,
  "default_list_limit": 20,
  "search_weights": {
    "title": 0.30, "tag": 0.25, "substring": 0.20,
    "fts": 0.15, "recency": 0.05, "relation": 0.05
  },
  "state_factor": { "active": 1.0, "inbox": 1.0, "archived": 0.7 },
  "recency_halflife_days": 90,
  "backup_retention": { "daily": 7, "weekly": 4, "monthly": 12 }
}
```

| 키 | 기본값 | 의미 |
|---|---|---|
| `backup_mirror` | `~/Dropbox/ClioBackup` | 백업 산출물 복사 위치 (DB 원본은 두지 않는다) |
| `timezone` | `Asia/Seoul` | 모든 시각은 ISO8601·초 단위·오프셋 포함으로 저장 |
| `ref_prefix` / `ref_digits` | `CLIO` / `4` | 글감 번호 형식. 초과 시 자릿수 확장, 재사용 금지 |
| `auto_link_threshold` | `1.01` | **자동 연결 비활성.** 근거 없이 낮추지 않는다 |
| `auto_link_margin` | `0.15` | 자동 연결에 필요한 1위−2위 점수 차 |
| `ask_threshold` | `0.35` | 확인 질문을 던지는 하한 |
| `mention_threshold` | `0.15` | 이 미만은 "연결 후보"로도 언급하지 않는다 |
| `candidate_limit` | `3` | 제시하는 후보 최대 개수 |
| `default_list_limit` | `20` | 기본 목록 건수 |
| `search_weights` | 위 표 | 검색 신호 가중치. 합이 1이 되도록 유지한다 |
| `state_factor` | active/inbox 1.0, archived 0.7 | 보관 상태별 점수 계수 |
| `recency_halflife_days` | `90` | 최근성 신호의 반감기 |
| `backup_retention` | 일 7 / 주 4 / 월 12 | rolling 보존 정책 |

---

## 14. 운영: 백업·무결성·복구

### 백업

```bash
scripts/idea_check backup              # 스냅샷 + 내보내기 + 미러 복사
scripts/idea_check backup --no-mirror  # 로컬에만
scripts/idea_check backup --out /path/to/dir
```

- **대상** — `ideas.db` 스냅샷, JSON 전량, Markdown, `attachments/` 사본
- **방식** — `sqlite3` 백업 API로 **온라인 스냅샷**. 실행 중인 파일을 복사하지 않는다
- **위치** — `~/ClioData/backups/<타임스탬프>/` 생성 후 `backup_mirror`로 복사
- **보존** — 일 7 / 주 4 / 월 12 rolling (초과분은 `pruned`로 보고)
- **검증** — 백업 직후 건수 대조 (`"verified": true`)

자동 스케줄링은 도구에 포함되어 있지 않다. 외부 스케줄러로 부른다.

```cron
# 매일 새벽 4시 백업, 매주 월요일 복구 시험
0 4 * * *  CLIO_DATA_DIR=$HOME/ClioData $HOME/.claude/skills/clio-idea-manager/scripts/idea_check backup >> $HOME/ClioData/backup.log 2>&1
0 5 * * 1  CLIO_DATA_DIR=$HOME/ClioData $HOME/.claude/skills/clio-idea-manager/scripts/idea_check integrity --strict >> $HOME/ClioData/backup.log 2>&1
```

### 무결성 검사 8종

```bash
scripts/idea_check integrity           # 보고만
scripts/idea_check integrity --strict  # 문제가 있으면 종료 코드 1
```

1. 원문 본문 해시 일치 (redacted 제외)
2. `latest_revision_id`가 최신 revision을 가리키는지
3. 고아 레코드 (revision, relation, tag, attachment, raw_entry)
4. 번호 중복 · 공백 보고
5. 미분류 원문 건수 보고
6. 대칭 관계 정규화 위반
7. 첨부 파일 존재와 해시 일치
8. `PRAGMA integrity_check`

### 삭제 계층

| 요청 | 처리 | 되돌리기 |
|---|---|---|
| "지워줘" (일반) | `set-state --storage archived` — 데이터는 남는다 | 가능 |
| 민감정보·오입력 | `redact --confirm` — 본문 제거, `redacted_at` 기록 | **불가** |
| 글감 자체 제거 | `delete-idea --confirm` — tombstone | **불가** |

tombstone은 내용 없이 삭제 사실과 시각만 남긴다. 감사 추적과 번호 재사용 방지를 위해
`ref`는 영구히 회수하지 않는다.

**영구 삭제와 백업의 관계:** 기존 백업 파일은 소급 수정하지 않는다. 대신
`backups/redaction_manifest.json`에 삭제 대상을 누적 보관하고 백업마다 갱신한다.
**과거 백업에서 복구할 때는 복구 직후 이 매니페스트를 다시 적용해야 한다.**
그렇게 하지 않으면 지운 내용이 복구와 함께 되살아난다.

### 복구 절차

```bash
# 1. 백업이 온전한지 먼저 시험한다
scripts/idea_check restore-test --source ~/ClioData/backups/<타임스탬프>

# 2. 현재 DB를 보존한 뒤 교체한다
mv ~/ClioData/ideas.db ~/ClioData/ideas.db.broken
cp ~/ClioData/backups/<타임스탬프>/ideas.db ~/ClioData/ideas.db
cp -R ~/ClioData/backups/<타임스탬프>/attachments/ ~/ClioData/attachments/

# 3. 검증한다
scripts/idea_check integrity --strict
```

건수와 무결성이 맞지 않으면 교체한 파일을 되돌리고 다른 백업을 시도한다.

### 임계값을 함부로 바꾸지 않는다

```bash
scripts/idea_check calibrate
```

`similar`이 남긴 후보 제시 이력과, 그 뒤 실제로 어떤 글감에 배정됐는지를 대조해
점수 구간별 **1위 후보 채택률**을 낸다.

```json
{ "samples": 62,
  "buckets": [ { "score_bucket": "0.4~", "samples": 11,
                 "top_candidate_accepted": 11, "rate": 1.0 } ],
  "current_auto_link_threshold": 1.01,
  "recommended_auto_link_threshold": 0.4,
  "verdict": "1위 후보 채택률 95% 이상 구간의 하한은 0.4입니다." }
```

글감 50건 또는 운영 4주가 지난 뒤, 표본 5건 이상·채택률 95% 이상 구간이 확인될 때만
그 결과를 근거로 `config.json`을 수정한다. **근거 없이 낮추지 않는다.**

재검토는 일정이 아니라 조건으로 촉발한다.

| 대상 | 촉발 조건 |
|---|---|
| 자동 연결 활성화 | 글감 50건 또는 운영 4주 → `calibrate` 결과 |
| 검색 가중치 | 검색 실패 사례 3건 누적 → 신호별 기여도 분석 |
| 의미 검색 도입 | 가중치 조정으로도 반복 실패 → 방식의 한계 확인 |

---

## 15. 테스트

```bash
python3 tests/test_clio.py            # 30건, 격리된 임시 데이터 디렉터리에서 실제 스크립트 호출
python3 tests/test_clio.py -v         # 개별 결과
python3 -m unittest tests.test_clio.ClioTest.test_TC19_same_day_duplicate_without_message_id
```

```
Ran 30 tests in 7.787s

OK
```

테스트는 목(mock)이 아니라 **실제 CLI를 서브프로세스로 실행하고 결과 DB를 검증한다.**

| ID | 검증 내용 |
|---|---|
| TC1 | 동일 메시지 2회 처리 → 원문 1건, 두 번째는 기존 ID 반환 |
| TC2 | 저장 도중 강제 종료 → 부분 저장 없음 |
| TC3 | 기존 글감에 원문 추가 → 과거 원문·정제본 해시 불변 |
| TC4 | 합성 → 부모 3건 유지 + `derived_from` 3건 생성 |
| TC5 | 원문 재배정 → 이동 성공 + `action_log` 이력 존재 |
| TC6 | archived → 기본 목록에서 숨김, 검색으로는 조회 가능 |
| TC7 | 백업으로 새 DB 복구 → 건수·해시 전량 일치 |
| TC8 | 한국어 키워드·부분 문장 검색 → 목표 글감 상위 5건 내 |
| TC9 | DB 접근 불가 → 성공 응답 없이 원문 반환 |
| TC10 | 상세 출력 → 원문과 해석이 명확히 구분 |
| TC11 | 확인 질문 무응답 → `unassigned`로 남고 미분류 목록에 조회됨 |
| TC12 | 영구 삭제 → 본문 제거 + `redacted_at` + tombstone, 번호 미재사용 |
| TC13 | 지시어 없는 일반 요청 → 어떤 테이블도 변경되지 않음 |
| TC14 | 여러 줄 입력 → 줄바꿈 보존, 지시어 토큰만 제외 |
| TC15 | 도구 실패 → 응답에 `CLIO-` 번호 없음 |
| TC16 | 문장 중간의 "글감:" → 지시어로 오인하지 않음 |
| TC17 | 이미지 첨부 → `attachments/`에 복사, 해시 일치 |
| TC18 | 원문 추가 → 정제본 revision이 새로 생기지 않음 |
| TC19 | 같은 날 동일 문장 2회 → 저장 안 하고 기존 `entry_id` 안내 |
| TC20 | 백업 미러 → `ideas.db` 원본이 동기화 경로에 존재하지 않음 |

추가 10건이 번호 자릿수 초과 시 정렬, 대칭 관계 정규화, `undo` 계열, 지시어 변형,
상세 출력의 시각 표기, 버전과 `CHANGELOG.md`의 일치, 그리고 설치·업데이트가
데이터와 사용자 설정을 보존하는지(구버전 설치본에서의 업그레이드 포함)를 검증한다.

판단 계층(`SKILL.md`)의 대화 규칙은 `evals/evals.json`의 시나리오로 회귀 검증한다.
저장 계층 테스트가 검증할 수 없는 것(무엇을 묻고 무엇을 묻지 않는가, 실패 시 문안)이 대상이다.

---

## 16. 프로젝트 구조와 구현 현황

```
SKILL.md              판단 계층 — 언제 포착하고 언제 묻고 무엇을 출력할지
CHANGELOG.md          버전별 변경 이력 (버전의 단일 원본은 clio_core.CLIO_VERSION)
install.sh            설치·업데이트 (첫 설치와 제자리 갱신을 같은 명령으로)
update.sh             최신 버전 받아 적용 (git pull → install.sh)
scripts/
  clio_core.py        공용 코어 — 스키마, 트랜잭션, 지시어 파서, 어간 처리, 유사도 점수
  idea_store          쓰기 — parse, capture, new-idea, assign, reassign, revise,
                             relate, merge, split, set-state, tag, undo, redact, delete-idea
  idea_search         읽기 — list, show, query, similar, unassigned, trace, relations
  idea_export         내보내기 — md, json
  idea_check          운영 — init, integrity, backup, restore-test, reconcile, calibrate
references/
  schema.md           데이터 모델과 단일 원본 규칙
  conversation-rules.md  응답 문안 모음
  recovery-policy.md  삭제·백업·복구 정책
evals/evals.json      판단 계층 회귀 검증 시나리오
tests/test_clio.py    저장 계층 자동 테스트 30건
```

| 단계 | 범위 | 상태 |
|---|---|---|
| 1. 안전한 포착 | 원문 저장, 글감 생성·지정, 목록·상세, 중복 방지 | **완료** |
| 2. 편집과 이력 | revision, 2축 상태, 태그, 재배정, undo | **완료** |
| 3. 연결과 합성 | 관계·근거·확인 상태, merge, split, 유사 후보 | **완료** |
| 4. 운영 안정성 | 내보내기, 온라인 스냅샷 백업, 복구 시험, 무결성 8종 | **완료** |
| 5. 의미 검색 | 축적 데이터로 검색 품질 평가 후 조건부 임베딩 도입 | 미착수 (조건 미충족) |

**알려진 미착수 항목**

- 자동 백업 스케줄링 — 현재는 `idea_check backup`을 외부 스케줄러가 부른다.
- `idea_check reconcile`의 채널 이력 대조 — 브리지 제약으로 DB 쪽 사실만 보고한다.

---

## 17. FAQ와 트러블슈팅

### FAQ

**Q. Discord가 없어도 쓸 수 있나?**
쓸 수 있다. `source.platform`이 채널을 일반화하므로 CLI로 직접 `capture`하거나
파일에서 임포트해도 된다. `--platform cli`처럼 아무 문자열이나 줄 수 있다.

**Q. 지시어 없이 저장하고 싶다.**
`--body`(또는 `--body-file`, 표준입력)를 쓰면 지시어 판정을 건너뛴다. 대화 채널에서
지시어를 강제하는 이유는 "저장할 만한 내용"을 LLM이 추측하지 않게 하기 위해서다.
CLI에서 직접 부르는 것은 이미 명시적 의사 표시다.

**Q. 왜 `핵심:` 한 줄은 저장되지 않나?**
그것은 대화용 확인 문구이지 정제본이 아니다. 정제본은 명시적으로 요청받았을 때만
`revise`로 만든다. 원문을 추가했다는 이유로 정제본을 갱신하지도 않는다(TC18).

**Q. 어떤 글감이 언제 몇 시에 들어왔는지 보고 싶다.**
시각은 초 단위로 저장되어 있다. `idea_search show --idea-ref CLIO-0001 --format text`의
원문 헤더가 `2026-08-17 09:12:04` 형태로 보여주고, 원문 하나만 볼 때는
`idea_search trace --entry-id N`이 `captured_at`(보낸 시각)과 `ingested_at`(기록한 시각)을
모두 낸다. 목록(`list`)이 날짜만 보여주는 것은 요약을 위한 표시일 뿐 저장 한계가 아니다.

**Q. 번호가 중간에 비어 있다.**
정상이다. 영구 삭제된 번호는 회수하지 않는다. 무결성 검사도 공백을 오류로 보지 않고
보고만 한다.

**Q. 합치면 원래 글감이 사라지나?**
사라지지 않는다. `merge`는 새 번호를 발급하고 각 부모에 `derived_from` 관계를 만든다.
부모는 그대로 남는다.

**Q. 잘못 배정했다.**
`idea_store reassign --entry-id N --to-idea-ref CLIO-XXXX`, 또는 방금 한 작업이라면
`idea_store undo`. `undo`는 `capture`와 영구 삭제를 제외한 작업을 되돌린다.

**Q. 자동 연결은 언제 켜나?**
글감 50건 또는 운영 4주 후 `idea_check calibrate`가 채택률 95% 이상 구간을 보고할 때만.
[§14](#임계값을-함부로-바꾸지-않는다) 참조.

**Q. 업데이트하면 그동안 쌓은 글감이 날아가나?**
날아가지 않는다. 업데이트는 `~/.claude/skills/…`의 **코드만** 갱신하고 `~/ClioData/`는
읽지도 쓰지도 않는다(스키마 보장과 무결성 검사 제외). `config.json`에서 직접 고친 임계값도
유지되며, 새 버전에서 생긴 키만 기본값으로 추가된다. 이 동작은 테스트로 강제된다.

**Q. 업데이트 전에 기존 설치를 지워야 하나?**
지우면 안 된다. `./update.sh`(또는 새 사본에서 `./install.sh`)만 실행하면 제자리에서
갱신된다. 이전 버전에만 있던 파일은 스크립트가 알아서 정리한다.

**Q. 데이터를 다른 도구로 옮기고 싶다.**
`idea_export json`이 전량을 하나의 JSON으로, `idea_export md`가 글감당 한 파일로 낸다.
DB 자체도 단일 SQLite 파일이라 그대로 복사하면 된다.

**Q. 여러 기기에서 쓰고 싶다.**
v1의 범위 밖이다. DB 원본을 동기화 폴더에 두면 손상 위험이 있으므로
([§6](#디렉터리-배치)), 당분간은 한 기기를 원본으로 두고 백업만 동기화한다.

### 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| `no such module: fts5` | Python에 딸린 SQLite에 FTS5가 없다 | `python3 -c "import sqlite3;sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)')"`로 확인. 실패하면 FTS5가 켜진 Python(공식 배포판, Homebrew, `python.org` 설치본)을 쓴다 |
| `database is locked` | 다른 프로세스가 쓰는 중이거나 DB가 동기화 폴더에 있다 | 실행 중인 프로세스를 정리한다. `~/ClioData`가 Dropbox/iCloud 안이면 밖으로 옮긴다 |
| `storage_unavailable` | 데이터 디렉터리에 접근할 수 없다 | `CLIO_DATA_DIR` 확인, 권한 확인, `idea_check init` 재실행 |
| `no_trigger`인데 저장하고 싶다 | `--raw-message`가 지시어를 강제한다 | 지시어를 붙이거나 `--body`를 쓴다 |
| 스킬이 반응하지 않는다 | 설치 위치 또는 트리거 문구 | `ls ~/.claude/skills/clio-idea-manager/SKILL.md` 확인. 메시지를 `글감:`으로 시작한다 |
| `scripts/idea_store: Permission denied` | 실행 권한이 없다 | `chmod +x scripts/idea_*` 또는 `python3 scripts/idea_store …`로 호출 |
| 검색에 안 걸린다 | 어간 추출이 근사라 어절이 길면 놓칠 수 있다 | 짧은 명사로 질의한다("기억하는것에대해" → "기억"). 반복되면 `search_weights` 조정 검토 |
| `SyntaxError` / `ModuleNotFoundError: zoneinfo` | Python 3.11 미만 | `python3 --version` 확인 후 3.11 이상으로 실행 |
| 백업 미러가 비어 있다 | `backup_mirror` 경로가 없거나 `--no-mirror`를 줬다 | `config.json`의 경로를 만들고 다시 실행. 응답의 `mirrored_to`로 확인 |
| 복구 후 지운 내용이 되살아났다 | 매니페스트 미적용 | `backups/redaction_manifest.json`을 복구 직후 다시 적용한다 |
| 업데이트 후 동작이 이상하다 | 새 버전의 문제 | `~/ClioData/skill-prev`로 되돌린다 ([§4](#업데이트를-되돌리기)). 데이터는 영향받지 않는다 |
| 같은 스킬이 두 개로 보인다 | v1.2.2 이전이 `~/.claude/skills/` 안에 롤백본을 남겼다 | `./install.sh`를 다시 실행하면 `~/ClioData/skill-prev`로 옮긴다 |
| `update.sh`가 로컬 변경으로 멈춘다 | 저장소에 커밋하지 않은 수정이 있다 | `git status`로 확인 후 정리(`git stash` 또는 커밋)하고 다시 실행한다 |
| 업데이트했는데 옛 동작이 그대로다 | 낡은 `__pycache__` | 설치 스크립트가 정리하지만, 수동 실행 중이라면 `find . -name __pycache__ -exec rm -rf {} +` |

---

## 18. 버전 정책

배포 버전은 [유의적 버전(SemVer)](https://semver.org/lang/ko/)을 따르고,
변경 이력은 [`CHANGELOG.md`](CHANGELOG.md)에 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)
형식으로 남긴다.

```bash
scripts/idea_store --version        # clio-idea-manager 1.2.2
scripts/idea_check integrity        # 응답 JSON에 "clio_version" 포함
```

| 자리 | 올리는 경우 | 예 |
|---|---|---|
| MAJOR | 기존 데이터나 명령 계약이 깨지는 변경 (마이그레이션 필요) | 스키마 비호환 변경, 명령 삭제 |
| MINOR | 명령·출력·판단 규칙의 추가나 변경 (하위 호환) | 새 서브커맨드, 출력 형식 확장 |
| PATCH | 버그 수정, 문서 수정 | 오탈자, 오동작 수정 |

**버전의 단일 원본은 `scripts/clio_core.py`의 `CLIO_VERSION`이다.** 네 도구의 `--version`과
`idea_check`의 응답이 모두 이 값을 읽고, 테스트가 `CHANGELOG.md` 맨 위 항목과의 일치를
검사하므로 한쪽만 올리면 빌드가 깨진다.

데이터 파일 형식의 버전은 `SCHEMA_VERSION`으로 따로 관리한다(현재 `1`).
스킬 버전이 올라가도 스키마가 그대로면 기존 `~/ClioData`는 손대지 않는다.

**갱신 절차**

```bash
# 1) CLIO_VERSION 수정 → 2) CHANGELOG.md 맨 위에 항목 추가 → 3) 테스트
python3 tests/test_clio.py
# 4) 커밋 → 5) 태그 → 6) 릴리스
git commit -am "..." && git tag -a v1.2.0 -m "v1.2.0" && git push origin main --tags
gh release create v1.2.0 --title "v1.2.0" --notes "CHANGELOG.md의 해당 절 내용"
```

각 버전은 GitHub Releases에도 같은 내용으로 발행한다.

---

## 19. 기여

이슈와 PR을 환영한다. 다만 이 프로젝트에는 **타협하지 않는 제약**이 몇 가지 있다.

### 지켜야 할 제약

1. **외부 의존성을 추가하지 않는다.** Python 3.11+ 표준 라이브러리만 쓴다.
   수년 단위로 축적될 데이터를 다루는 도구라, 어느 시점에 실행해도 동작하는 것이
   기능 하나보다 중요하다. (의미 검색 도입 시에 한해 재검토한다.)
2. **원문은 불변이다.** `raw_entry`를 수정하는 코드는 명시적 영구 삭제 절차 외에 받지 않는다.
3. **판단 계층은 SQL을 만들지 않는다.** 새 기능은 `scripts/`의 서브커맨드로 노출한다.
4. **모든 쓰기는 단일 트랜잭션이다.** 부분 저장 가능성이 있는 변경은 받지 않는다.
5. **임계값 기본값을 근거 없이 바꾸지 않는다.** 특히 `auto_link_threshold`.

### PR 체크리스트

- [ ] `python3 tests/test_clio.py` 전부 통과
- [ ] 동작을 바꿨다면 테스트를 추가하거나 갱신했다
- [ ] 대화 규칙을 바꿨다면 `SKILL.md`와 `evals/evals.json`을 함께 갱신했다
- [ ] 스키마를 바꿨다면 `references/schema.md`와 마이그레이션 경로를 함께 제시했다
- [ ] 설정 키를 추가했다면 `clio_core.DEFAULT_CONFIG`와 이 README의 설정 표를 갱신했다
- [ ] 외부 패키지를 추가하지 않았다
- [ ] `CLIO_VERSION`을 올리고 `CHANGELOG.md`에 항목을 추가했다 ([§18](#18-버전-정책))

### 이슈를 올릴 때

버그 리포트에는 다음을 함께 적어주면 재현이 빠르다.

```
- OS / Python 버전:  python3 --version
- 실행한 명령 (원문은 지우고 구조만):
- 받은 JSON 응답 (특히 error.code):
- idea_check integrity 결과:
```

**원문·개인정보는 이슈에 붙이지 않는다.** 재현에 필요한 구조만 남기고 내용은 치환한다.

---

## 20. 라이선스

MIT License. 자세한 내용은 [LICENSE](LICENSE) 참조.

```
Copyright (c) 2026 Pilwon Hur

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction…
```

---

## 관련 문서

| 문서 | 내용 |
|---|---|
| [`CHANGELOG.md`](CHANGELOG.md) | 버전별 변경 이력 |
| [`SKILL.md`](SKILL.md) | 판단 계층 — 포착·질문·출력 규칙 |
| [`references/schema.md`](references/schema.md) | 데이터 모델과 단일 원본 규칙 |
| [`references/conversation-rules.md`](references/conversation-rules.md) | 표준 응답 문안 |
| [`references/recovery-policy.md`](references/recovery-policy.md) | 삭제·백업·복구 정책 |
| [`tests/test_clio.py`](tests/test_clio.py) | 저장 계층 테스트 30건 |
| [`docs/PRD.md`](docs/PRD.md) | 설계 근거 문서 v1.3 (요구사항·결정 사항 D1~D7·성공 기준) |
| [`docs/manual.md`](docs/manual.md) | 사용자용 설명서 (CLI 없이 대화만으로 쓰는 법) |
