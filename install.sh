#!/usr/bin/env bash
# Clio 아이디어 관리 스킬 설치·업데이트 (PRD.md §4.2)
#
# 같은 명령으로 첫 설치와 업데이트를 모두 처리한다.
#   ./install.sh
#
# 업데이트는 지우고 다시 까는 방식이 아니다. 바뀐 파일만 덮어쓰고, 원본에서
# 사라진 파일만 지운다. 데이터(~/ClioData)와 사용자가 고친 config.json 값은
# 어떤 경우에도 보존한다. 되돌릴 수 있도록 직전 스킬을 한 세대 보관한다.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${CLIO_SKILL_DIR:-$HOME/.claude/skills/clio-idea-manager}"
DATA_DIR="${CLIO_DATA_DIR:-$HOME/ClioData}"
PREV_DIR="$SKILL_DIR.prev"

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
echo "데이터: $DATA_DIR  (건드리지 않음)"
echo

# 1) 되돌릴 수 있게 직전 스킬을 한 세대만 보관한다 (코드만, 데이터 아님)
if [ "$MODE" = "업데이트" ]; then
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
         "$SKILL_DIR/scripts/idea_export" "$SKILL_DIR/scripts/idea_check"

# 3) 데이터 디렉터리 — 없으면 만들고, config.json은 병합한다.
#    사용자가 고친 값은 그대로 두고 새로 생긴 설정 키만 기본값으로 채운다.
mkdir -p "$DATA_DIR"/{attachments,exports,backups}
CLIO_DATA_DIR="$DATA_DIR" python3 - "$SKILL_DIR" "$DATA_DIR" <<'PY'
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

# 4) 스키마는 멱등하게 보장하고, 끝나면 무결성을 확인한다
CLIO_DATA_DIR="$DATA_DIR" python3 "$SKILL_DIR/scripts/idea_check" init \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['message'], '(스키마 v%s)' % d['schema_version'])"
CLIO_DATA_DIR="$DATA_DIR" python3 "$SKILL_DIR/scripts/idea_check" integrity \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('무결성:', '이상 없음' if d['healthy'] else d['problems'])"

echo
echo "$MODE 완료 — clio-idea-manager $NEW_VER"
echo "데이터베이스: $DATA_DIR/ideas.db"
if [ "$MODE" = "업데이트" ] && [ "$OLD_VER" != "$NEW_VER" ]; then
  echo "변경 내용: $SKILL_DIR/CHANGELOG.md"
fi
exit 0
