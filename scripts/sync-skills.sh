#!/usr/bin/env bash
# 从 vendor/_agents_tmp (wshobson/agents) 同步合适 skill 到本项目 .dsh/skills/<plugin>--<skill>/
# 幂等：已存在则跳过（保留本地修改）。denylist 里的离域插件不同步。
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="vendor/wshobson-agents"
DEST=".dsh/skills"
[ -d "$SRC/plugins" ] || { echo "缺少 $SRC，先 bash scripts/vendor-fetch.sh（wshobson/agents 由 _agents_tmp 手工克隆）"; exit 1; }
mkdir -p "$DEST"
DENY="pptx-deck-creation blockchain-web3 quantitative-trading hr-legal-compliance game-development hermes-tweet social-publishing content-marketing brand-landingpage meigen-ai-design dotnet-contribution dgx-spark-ops startup-business-analyst customer-sales-automation business-analytics seo-analysis-monitoring seo-content-creation seo-technical-optimization ui-design payment-processing accessibility-compliance machine-learning-ops"
added=0; skipped=0
for p in "$SRC"/plugins/*/; do
  pn=$(basename "$p")
  case " $DENY " in *" $pn "*) continue;; esac
  [ -d "$p/skills" ] || continue
  for s in "$p"skills/*/; do
    [ -d "$s" ] || continue
    sn=$(basename "$s"); id="${pn}--${sn}"
    if [ -e "$DEST/$id" ]; then skipped=$((skipped+1)); continue; fi
    cp -r "$s" "$DEST/$id"
    added=$((added+1))
  done
done
echo "added=$added skipped_existing=$skipped total=$(ls "$DEST" | wc -l)"
