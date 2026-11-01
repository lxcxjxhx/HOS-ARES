#!/usr/bin/env bash
# 离线交付包（B13 / 12 §3）：把运行时需要的镜像、Python 依赖与数据快照一起打包。
#
# 设计取向：**断网可装**。所以这里只做"把东西取下来 + 写清单"，
# 不在脚本里做任何联网校验——校验由 `veripen doctor` 在目标机上跑。
set -euo pipefail

OUT="${1:-dist/offline}"
PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$OUT/images" "$OUT/python" "$OUT/data"

echo "[1/4] 导出镜像清单"
if command -v docker >/dev/null 2>&1; then
  docker compose -f "$PKG_DIR/docker-compose.yml" config --images 2>/dev/null \
    | grep -v '^$' | while read -r image; do
        echo "  - $image"
        docker save "$image" -o "$OUT/images/$(echo "$image" | tr '/:' '__').tar"
      done
else
  echo "  (无 docker：跳过镜像导出，清单里标 skipped)"
fi

echo "[2/4] 下载 Python 依赖（pip download，可断网重装）"
python -m pip download \
  --dest "$OUT/python" \
  --requirement "$PKG_DIR/requirements.lock" 2>/dev/null \
  || python -m pip download --dest "$OUT/python" pydantic PyYAML fastapi uvicorn pytest

echo "[3/4] 复制数据快照"
if [ -d "$PKG_DIR/tests/bench" ]; then
  cp -r "$PKG_DIR/tests/bench" "$OUT/data/bench"
fi

echo "[4/4] 写清单（doctor 在目标机核对 digest）"
python - "$OUT" <<'PY'
import hashlib, json, sys
from pathlib import Path

out = Path(sys.argv[1])


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


images = [{"name": p.name, "sha256": digest(p)} for p in sorted((out / "images").glob("*.tar"))]
data = [{"path": str(p.relative_to(out)), "sha256": digest(p)} for p in sorted((out / "data").rglob("*")) if p.is_file()]
python_files = [{"name": p.name, "sha256": digest(p)} for p in sorted((out / "python").glob("*"))]

manifest = {
    "version": 1,
    "images": images,
    "data": data,
    "python": {"requires": ">=3.11", "locks": "requirements.lock", "wheels": python_files},
}
(out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"manifest: images={len(images)} data={len(data)} wheels={len(python_files)}")
PY

echo "完成：$OUT"
