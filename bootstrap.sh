#!/usr/bin/env bash
# ChemCritic 引擎自举：把仓库里的单文件引擎装到固定路径，并立刻自检。
#
# 用法（新对话第一条命令，一条搞定，不探索、不试错）：
#   cd /workspace && rm -rf .cc_src && git clone --depth 1 \
#     https://github.com/LRJ888-Max/Chemcritic_sciencepal-hackathon.git .cc_src \
#     && bash .cc_src/bootstrap.sh
#
# 固定路径（提示词里直接引用，不再现场找）：
#   引擎  /workspace/chemcritic/chemcritic_engine.py
#   技能  /workspace/skills/chemcritic-comparability/SKILL.md
#
# 成功时最后一行是：ENGINE_READY /workspace/chemcritic/chemcritic_engine.py
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${CHEMCRITIC_HOME:-/workspace/chemcritic}"
SKILL_SRC="$SRC_DIR/skills/chemcritic-comparability"
SKILLS_ROOT="${CHEMCRITIC_SKILLS:-/workspace/skills}"
SKILL_DST="$SKILLS_ROOT/chemcritic-comparability"

PY="$(command -v python || command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "FATAL: no python/python3 in this sandbox" >&2
  exit 1
fi
if [ ! -f "$SKILL_SRC/scripts/chemcritic_engine.py" ]; then
  echo "FATAL: repo is missing skills/chemcritic-comparability/scripts/chemcritic_engine.py" >&2
  echo "       先在本机跑 engine/build_bundle.py 并 push" >&2
  exit 1
fi

# 1) 固定路径安装引擎
mkdir -p "$HOME_DIR"
cp "$SKILL_SRC/scripts/chemcritic_engine.py" "$HOME_DIR/chemcritic_engine.py"
cp "$SKILL_SRC/SKILL.md" "$HOME_DIR/SKILL.md"
if [ -d "$SKILL_SRC/scripts/examples" ]; then
  rm -rf "$HOME_DIR/examples"
  cp -r "$SKILL_SRC/scripts/examples" "$HOME_DIR/examples"
fi

# 1.5) 可选依赖：rdkit（chemsense 化学感知层；失败只告警不中断——引擎自动降级到别名判同）
if ! "$PY" -c "import rdkit" >/dev/null 2>&1; then
  if "$PY" -m pip install --quiet --disable-pip-version-check rdkit >/dev/null 2>&1; then
    echo "deps:   rdkit installed (chemsense enabled)"
  else
    echo "warn:   rdkit install failed; chemsense 降级运行（判定行为不变）"
  fi
else
  echo "deps:   rdkit already present"
fi

# 2) 技能注册（平台从这里加载 skill；失败只告警不中断）
if mkdir -p "$SKILLS_ROOT" 2>/dev/null && [ -w "$SKILLS_ROOT" ]; then
  rm -rf "$SKILL_DST"
  if cp -r "$SKILL_SRC" "$SKILL_DST" 2>/dev/null; then
    echo "skill:  $SKILL_DST/SKILL.md"
  else
    echo "warn:   skill copy failed (engine still usable)"
  fi
else
  echo "warn:   $SKILLS_ROOT not writable (engine still usable)"
fi

echo "engine: $HOME_DIR/chemcritic_engine.py"
"$PY" "$HOME_DIR/chemcritic_engine.py" --version

# 3) 自检：只做验证，不做安装
OUT="$("$PY" "$HOME_DIR/chemcritic_engine.py" --selftest 2>&1)" || true
echo "$OUT" | tail -1
if echo "$OUT" | grep -q "0 failed"; then
  echo "ENGINE_READY $HOME_DIR/chemcritic_engine.py"
else
  echo "ENGINE_FAILED: selftest did not pass" >&2
  echo "$OUT" | tail -5 >&2
  exit 1
fi
