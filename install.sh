#!/usr/bin/env bash
# Clio 아이디어 관리 스킬 설치·업데이트 (PRD.md §4.2)
#
# 같은 명령으로 첫 설치와 업데이트를 모두 처리한다.
#   ./install.sh
#
# 업데이트는 지우고 다시 까는 방식이 아니다. 바뀐 파일만 덮어쓰고, 원본에서
# 사라진 파일만 지운다. 데이터와 사용자가 고친 config.json 값은 어떤 경우에도
# 보존한다. 되돌릴 수 있도록 직전 스킬을 한 세대 보관한다.
#
# 데이터 위치 (v1.3.0~) — 설치·업데이트는 데이터를 옮기지 않는다.
#   CLIO_DATA_DIR 환경 변수 > 위치 기록(~/.config/clio-idea-manager/data-root.json)
#   > 기존 설치(~/ClioData) > 새 기본값(~/SecretaryData/services/ideas)
# 기존 설치는 그 자리를 계속 쓰고, 위치 기록에 고정해 둔다. 새 위치로 옮기려면
# scripts/idea_relocate plan → run 을 따로 실행한다.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${CLIO_SKILL_DIR:-$HOME/.claude/skills/clio-idea-manager}"

json_get() {  # $1 = 점으로 이은 키. 표준입력의 JSON에서 값을 꺼낸다 (없으면 빈 문자열)
  python3 -c '
import json, sys
v = json.load(sys.stdin)
for k in sys.argv[1].split("."):
    v = v.get(k) if isinstance(v, dict) else None
print("" if v is None else v)' "$1"
}

# 0) 데이터 위치 판정 — 새 코드로 판정하되 아무것도 쓰지 않는다.
#    판정할 수 없으면(두 위치에 모두 DB, 위치 기록이 가리키는 폴더 없음 등) 설치 전에 멈춘다.
WHERE_JSON="$(python3 "$SRC/scripts/idea_check" where)" || {
  echo "데이터 위치를 판정하지 못했습니다. 아무것도 바꾸지 않았습니다:" >&2
  echo "$WHERE_JSON" >&2
  exit 1
}
DATA_DIR="$(printf '%s' "$WHERE_JSON" | json_get data_dir)"
DATA_SOURCE="$(printf '%s' "$WHERE_JSON" | json_get source)"
DATA_LABEL="$(printf '%s' "$WHERE_JSON" | json_get source_label)"
if [ "$(printf '%s' "$WHERE_JSON" | json_get usable)" != "True" ]; then
  echo "데이터 위치에 문제가 있어 설치를 멈춥니다. 아무것도 바꾸지 않았습니다." >&2
  echo "  위치: $DATA_DIR ($DATA_LABEL)" >&2
  echo "  사유: $(printf '%s' "$WHERE_JSON" | json_get problem.message)" >&2
  exit 1
fi

# 롤백본은 스킬 디렉터리 **밖**에 둔다. ~/.claude/skills/ 안에 두면 SKILL.md가 딸려 있어
# 같은 설명을 가진 스킬이 하나 더 등록되고, 어느 쪽이 불릴지 알 수 없게 된다.
PREV_DIR="${CLIO_PREV_DIR:-$DATA_DIR/skill-prev}"

# 스킬 디렉터리에 두는 것 (데이터는 여기 들어오지 않는다)
PAYLOAD=(SKILL.md CHANGELOG.md scripts references evals tests)

version_of() {  # $1 = 스킬 루트. 설치되어 있지 않으면 빈 문자열
  local f="$1/scripts/clio_core.py"
  [ -f "$f" ] || return 0
  python3 - "$f" <<'PY'
import re, sys
m = re.search(r'^CLIO_VERSION\s*=\s*"([^"]+)"', open(sys.argv[1], encoding='utf-8').read(), re.M)
print(m.group(1) if m else "")
PY
}

NEW_VER="$(version_of "$SRC")"
OLD_VER="$(version_of "$SKILL_DIR")"

# 설치 여부는 파일 존재로 판정한다. v1.1.0 이전 스킬에는 CLIO_VERSION이 없으므로
# 버전 문자열로 판정하면 기존 설치를 신규로 오인해 롤백본을 만들지 않는다.
if [ -f "$SKILL_DIR/scripts/clio_core.py" ]; then
  MODE="업데이트"
  if [ -z "$OLD_VER" ]; then
    echo "== Clio 스킬 업데이트: 버전 미상(1.1.0 이전) → $NEW_VER =="
  elif [ "$OLD_VER" = "$NEW_VER" ]; then
    echo "== Clio 스킬 재적용 (버전 $OLD_VER, 변경분만 반영) =="
  else
    echo "== Clio 스킬 업데이트: $OLD_VER → $NEW_VER =="
  fi
else
  MODE="설치"
  echo "== Clio 스킬 설치: $NEW_VER =="
fi
echo "스킬  : $SKILL_DIR"
echo "데이터: $DATA_DIR  ($DATA_LABEL · ideas.db·첨부·백업은 옮기거나 지우지 않음)"
printf '%s' "$WHERE_JSON" | python3 -c '
import json, sys
for w in json.load(sys.stdin).get("warnings", []):
    print("  주의:", w["message"])'
echo

# 0) v1.2.2 이전이 스킬 디렉터리 옆에 남긴 롤백본을 걷어낸다.
#    그 자리에 있으면 중복 스킬로 등록되므로 위치만 옮겨 보관한다.
LEGACY_PREV="$SKILL_DIR.prev"
if [ -d "$LEGACY_PREV" ]; then
  mkdir -p "$(dirname "$PREV_DIR")"
  rm -rf "$PREV_DIR"
  mv "$LEGACY_PREV" "$PREV_DIR"
  echo "이전 위치의 롤백본을 옮겼습니다: $LEGACY_PREV → $PREV_DIR"
  echo "  (스킬 디렉터리 안에 있으면 중복 스킬로 등록됩니다)"
fi

# 1) 되돌릴 수 있게 직전 스킬을 한 세대만 보관한다 (코드만, 데이터 아님)
if [ "$MODE" = "업데이트" ]; then
  mkdir -p "$(dirname "$PREV_DIR")"
  rm -rf "$PREV_DIR"
  cp -R "$SKILL_DIR" "$PREV_DIR"
  echo "이전 버전 보관: $PREV_DIR  (되돌리려면 이 디렉터리를 다시 복사하면 된다)"
fi

# 2) 파일 동기화 — 전체 삭제 없이, 바뀐 것만 덮어쓰고 사라진 것만 지운다
mkdir -p "$SKILL_DIR"
python3 - "$SRC" "$SKILL_DIR" "${PAYLOAD[@]}" <<'PY'
import filecmp, shutil, sys
from pathlib import Path

src, dst, *payload = sys.argv[1:]
src, dst = Path(src), Path(dst)

def files_under(root, names):
    out = set()
    for name in names:
        p = root / name
        if p.is_file():
            out.add(Path(name))
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and "__pycache__" not in f.parts:
                    out.add(f.relative_to(root))
    return out

want = files_under(src, payload)
have = files_under(dst, payload)

added = updated = kept = 0
for rel in sorted(want):
    s, d = src / rel, dst / rel
    if d.exists():
        if filecmp.cmp(s, d, shallow=False):
            kept += 1
            continue
        updated += 1
    else:
        added += 1
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s, d)

removed = 0
for rel in sorted(have - want):
    (dst / rel).unlink()
    removed += 1

# 원본에서 사라진 빈 디렉터리와 낡은 바이트코드 정리
for cache in dst.rglob("__pycache__"):
    shutil.rmtree(cache, ignore_errors=True)
for name in payload:
    p = dst / name
    if p.is_dir():
        for d in sorted((x for x in p.rglob("*") if x.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()

print(f"파일: 추가 {added} · 갱신 {updated} · 유지 {kept} · 삭제 {removed}")
PY

chmod +x "$SKILL_DIR/scripts/idea_store" "$SKILL_DIR/scripts/idea_search" \
         "$SKILL_DIR/scripts/idea_export" "$SKILL_DIR/scripts/idea_check" \
         "$SKILL_DIR/scripts/idea_relocate"

# 3) 데이터 디렉터리 — 스키마를 멱등하게 보장하고(새 위치면 새로 만든다), 판정된 위치를
#    위치 기록에 고정한다. 환경 변수로 지정한 경우에는 기록을 쓰지 않는다.
#    --expect-root: 판정 이후 위치가 바뀌었으면 아무것도 만들지 않고 실패한다.
INIT_JSON="$(python3 "$SKILL_DIR/scripts/idea_check" init --pin --expect-root "$DATA_DIR")" || {
  echo "데이터베이스 준비에 실패했습니다:" >&2
  echo "$INIT_JSON" >&2
  exit 1
}
printf '%s' "$INIT_JSON" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(d["message"], "(스키마 v%s)" % d["schema_version"])
if d.get("pinned"):
    print("위치 기록 고정:", d["pinned"]["path"], "(%s)" % d["pinned"]["reason"])'

#    config.json은 병합한다. 사용자가 고친 값은 그대로 두고 새로 생긴 설정 키만 기본값으로 채운다.
python3 - "$SKILL_DIR" "$DATA_DIR" <<'PY'
import json, sys
from pathlib import Path

skill, data = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(skill / "scripts"))
from clio_core import DEFAULT_CONFIG  # noqa: E402

path = data / "config.json"
if not path.exists():
    path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print("config.json 생성 (자동 연결은 초기 비활성 — PRD D1)")
    raise SystemExit

user = json.loads(path.read_text(encoding="utf-8"))
added = []

def merge(defaults, current, prefix=""):
    for k, v in defaults.items():
        if k not in current:
            current[k] = v
            added.append(prefix + k)
        elif isinstance(v, dict) and isinstance(current[k], dict):
            merge(v, current[k], prefix + k + ".")

merge(DEFAULT_CONFIG, user)
if added:
    path.write_text(json.dumps(user, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("config.json 유지 + 새 설정 키 추가:", ", ".join(added))
else:
    print("config.json 유지 (변경 없음)")
PY

# 4) 끝나면 무결성을 확인한다
python3 "$SKILL_DIR/scripts/idea_check" integrity \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('무결성:', '이상 없음' if d['healthy'] else d['problems'])"

echo
echo "$MODE 완료 — clio-idea-manager $NEW_VER"
echo "데이터베이스: $DATA_DIR/ideas.db"
if [ "$DATA_SOURCE" = "legacy" ]; then
  echo "기존 위치를 그대로 씁니다. 새 공통 위치로 옮기려면 (자동으로 옮기지 않음):"
  echo "  $SKILL_DIR/scripts/idea_relocate plan    # 미리보기, 아무것도 바꾸지 않음"
fi
if [ "$MODE" = "업데이트" ] && [ "$OLD_VER" != "$NEW_VER" ]; then
  echo "변경 내용: $SKILL_DIR/CHANGELOG.md"
fi
exit 0
