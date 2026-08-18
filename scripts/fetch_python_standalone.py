"""
下载 python-build-standalone 的 aarch64-musl Python 构建。
这是一个自包含的 Python 构建，不需要 Alpine 包管理器。
"""
import urllib.request, ssl, os, tarfile, gzip, shutil, sys

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# python-build-standalone latest release for aarch64-unknown-linux-musl
# 使用 20240415 release (Python 3.12.3)
BASE = 'https://github.com/indygreg/python-build-standalone/releases/download'
RELEASE = '20240415'
PY_VER = '3.12.3'
FILENAME = f'cpython-{PY_VER}+{RELEASE}-aarch64-unknown-linux-musl-install_only.tar.gz'
URL = f'{BASE}/{RELEASE}/{FILENAME}'

CACHE = os.path.join(os.path.dirname(__file__), 'cache')
os.makedirs(CACHE, exist_ok=True)
OUTPUT = os.path.join(CACHE, FILENAME)

if not os.path.exists(OUTPUT):
    print(f'下载 Python standalone: {FILENAME}')
    print(f'URL: {URL}')
    try:
        req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
        total = int(resp.headers.get('Content-Length', 0))
        downloaded = 0
        with open(OUTPUT, 'wb') as f:
            while True:
                chunk = resp.read(8192 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f'\r  {pct}% ({downloaded//1024//1024} MB)', end='', flush=True)
        print(f'\n  ✓ 下载完成: {downloaded//1024//1024} MB')
    except Exception as e:
        print(f'  ✗ 下载失败: {e}')
        sys.exit(1)
else:
    print(f'使用缓存: {OUTPUT}')

# 提取 Python 到 build 目录
BUILD = os.path.join(os.path.dirname(__file__), 'rootfs-build')
PY_DEST = os.path.join(BUILD, 'usr', 'local')
os.makedirs(PY_DEST, exist_ok=True)

print(f'解压 Python 到 {PY_DEST}...')
with gzip.open(OUTPUT, 'rb') as gz:
    with tarfile.open(fileobj=gz) as tf:
        # python-build-standalone extracts to python/ directory
        # We want to merge usr/ into BUILD
        for member in tf.getmembers():
            name = member.name
            # Strip the leading 'python/' prefix
            if name.startswith('python/'):
                rel = name[len('python/'):]
                if not rel:
                    continue
                dest = os.path.join(BUILD, rel)
                if member.isdir():
                    os.makedirs(dest, exist_ok=True)
                elif member.isfile():
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with tf.extractfile(member) as src, open(dest, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    if member.mode & 0o111:
                        try:
                            os.chmod(dest, 0o755)
                        except:
                            pass
                elif member.issym():
                    # Record symlink for later injection
                    print(f'  SYMLINK: {rel} -> {member.linkname}')

# 验证
python_bin = os.path.join(BUILD, 'usr', 'bin', 'python3')
if os.path.exists(python_bin):
    print(f'✓ Python 已就绪: {python_bin}')
else:
    # Check alternative paths
    for alt in ['usr/bin/python3.12', 'usr/local/bin/python3', 'usr/local/bin/python3.12']:
        if os.path.exists(os.path.join(BUILD, alt)):
            python_bin = os.path.join(BUILD, alt)
            print(f'✓ Python 已就绪 (alt): {python_bin}')
            break
    else:
        print(f'⚠ Python 二进制未找到，检查 {BUILD}/usr/bin/')
        if os.path.exists(os.path.join(BUILD, 'usr', 'bin')):
            for f in os.listdir(os.path.join(BUILD, 'usr', 'bin')):
                if 'python' in f.lower():
                    print(f'  发现: {f}')

# 创建 python3 -> python3.12 软链接（如果需要）
python312 = os.path.join(BUILD, 'usr', 'bin', 'python3.12')
python3_link = os.path.join(BUILD, 'usr', 'bin', 'python3')
if os.path.exists(python312) and not os.path.exists(python3_link):
    # 在 Windows 上创建文本 "symlink" (Android 上 toybox tar 会正确处理)
    with open(python3_link, 'w') as f:
        f.write('#!/bin/sh\nexec /usr/bin/python3.12 "$@"\n')
    os.chmod(python3_link, 0o755)
    print('✓ 创建 python3 -> python3.12 shim')

print('完成！')