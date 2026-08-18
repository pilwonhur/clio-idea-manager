#!/usr/bin/env bash
# 최신 버전을 받아 적용한다.
#
#   ./update.sh
#
# 저장소를 최신으로 당긴 뒤 install.sh를 부른다. 기존 설치를 지우지 않고
# 바뀐 파일만 제자리에서 갱신하며, 데이터와 설정은 그대로 둔다.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "이 디렉터리는 git 저장소가 아닙니다." >&2
  echo "clone 해서 쓰는 경우에만 update.sh가 최신 버전을 받아올 수 있습니다:" >&2
  echo "  git clone https://github.com/pilwonhur/clio-idea-manager.git" >&2
  echo "직접 받은 사본이라면 새 사본에서 ./install.sh 를 실행하면 같은 결과가 됩니다." >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "로컬에 커밋하지 않은 변경이 있습니다. 먼저 정리한 뒤 다시 실행하세요." >&2
  git status --short >&2
  exit 1
fi

BEFORE="$(git rev-parse --short HEAD)"
git pull --ff-only
AFTER="$(git rev-parse --short HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
  echo "이미 최신입니다 ($AFTER). 설치본만 다시 맞춥니다."
else
  echo
  echo "받아온 변경:"
  git --no-pager log --oneline "$BEFORE..$AFTER"
  echo
fi

exec ./install.sh
