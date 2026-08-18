#!/usr/bin/env bash
# Clio 아이디어 관리 스킬 설치 (PRD.md §4.2)
#
# 스킬 코드와 데이터를 분리한다. 스킬을 재설치해도 데이터는 영향을 받지 않는다.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${CLIO_SKILL_DIR:-$HOME/.claude/skills/clio-idea-manager}"
DATA_DIR="${CLIO_DATA_DIR:-$HOME/ClioData}"

echo "스킬  : $SKILL_DIR"
echo "데이터: $DATA_DIR"

# 1) 스킬 설치 (데이터는 건드리지 않는다)
mkdir -p "$SKILL_DIR"
rm -rf "$SKILL_DIR/scripts" "$SKILL_DIR/references" "$SKILL_DIR/evals" "$SKILL_DIR/tests"
cp -R "$SRC/SKILL.md" "$SRC/scripts" "$SRC/references" "$SRC/evals" "$SRC/tests" "$SKILL_DIR/"
chmod +x "$SKILL_DIR/scripts/idea_store" "$SKILL_DIR/scripts/idea_search" \
         "$SKILL_DIR/scripts/idea_export" "$SKILL_DIR/scripts/idea_check"

# 2) 데이터 디렉터리 초기화 (이미 있으면 그대로 둔다)
mkdir -p "$DATA_DIR"/{attachments,exports,backups}
if [ ! -f "$DATA_DIR/config.json" ]; then
  cat > "$DATA_DIR/config.json" <<'JSON'
{
  "backup_mirror": "~/Dropbox/ClioBackup",
  "timezone": "Asia/Seoul",
  "auto_link_threshold": 1.01,
  "auto_link_margin": 0.15,
  "ask_threshold": 0.35,
  "mention_threshold": 0.15,
  "candidate_limit": 3,
  "default_list_limit": 20
}
JSON
  echo "config.json 생성 (자동 연결은 초기 비활성 — PRD D1)"
else
  echo "config.json 유지"
fi

# 3) 스키마 생성 (이미 있으면 그대로 둔다) 후 무결성 확인
CLIO_DATA_DIR="$DATA_DIR" python3 "$SKILL_DIR/scripts/idea_check" init \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['message'])"
CLIO_DATA_DIR="$DATA_DIR" python3 "$SKILL_DIR/scripts/idea_check" integrity \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('무결성:', '이상 없음' if d['healthy'] else d['problems'])"

echo "설치 완료. 데이터베이스: $DATA_DIR/ideas.db"
