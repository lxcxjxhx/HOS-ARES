#!/usr/bin/env bash
# 按 vendor.lock.json 从各项目官方仓库（codeload tarball）拉取源码到 vendor/<slug>/。
# 幂等：已存在且 .vendor-sha 一致则跳过。源码不入 git（见 .gitignore）。
# 用法：bash scripts/vendor-fetch.sh [slug ...]
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
mkdir -p vendor

mapfile -t ROWS < <(node -e '
const l=require("./vendor.lock.json");
for(const t of l.targets) console.log([t.slug,t.repo,t.branch,t.commit].join("\t"));
')
only=("$@")
want(){ [ ${#only[@]} -eq 0 ] && return 0; for x in "${only[@]}"; do [ "$x" = "$1" ] && return 0; done; return 1; }

for row in "${ROWS[@]}"; do
  IFS=$'\t' read -r slug repo branch commit <<<"$row"
  want "$slug" || continue
  dest="vendor/$slug"
  if [ -d "$dest" ] && [ -f "$dest/.vendor-sha" ] && [ "$(cat "$dest/.vendor-sha")" = "$commit" ]; then
    echo "skip  $slug (already @ ${commit:0:12})"; continue
  fi
  echo "fetch $slug <- $repo@$branch ${commit:0:12}"
  tmp="$(mktemp -d)"
  url="https://codeload.github.com/$repo/tar.gz/$commit"
  if ! curl -fsSL "$url" -o "$tmp/src.tgz"; then
    url="https://codeload.github.com/$repo/tar.gz/refs/heads/$branch"
    curl -fsSL "$url" -o "$tmp/src.tgz"
  fi
  sha=$(sha256sum "$tmp/src.tgz" | cut -d' ' -f1)
  rm -rf "$dest"; mkdir -p "$dest"
  tar -xzf "$tmp/src.tgz" -C "$dest" --strip-components=1
  echo "$commit" > "$dest/.vendor-sha"
  echo "$sha"    > "$dest/.vendor-tarball-sha256"
  node -e '
const f=require("fs");const p="vendor/'"$slug"'/.vendor-meta.json";
f.writeFileSync(p,JSON.stringify({slug:process.argv[1],repo:process.argv[2],branch:process.argv[3],commit:process.argv[4],tarball_sha256:process.argv[5],fetched_at:new Date().toISOString()},null,2));' \
    "$slug" "$repo" "$branch" "$commit" "$sha"
  rm -rf "$tmp"
  echo "  ok  $slug  $(du -sh "$dest" | cut -f1)  tar:${sha:0:12}"
done
echo "done."
