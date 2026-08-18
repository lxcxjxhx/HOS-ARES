"""Download python-build-standalone for aarch64-musl and merge into rootfs build."""
import urllib.request, ssl, json, os, sys, tarfile, gzip, shutil, re

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# Step 1: Find latest release with aarch64-musl
print('查找 python-build-standalone 最新 aarch64-musl 版本...')
url = 'https://api.github.com/repos/indygreg/python-build-standalone/releases?per_page=30'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
resp = urllib.request.urlopen(req, context=ctx, timeout=15)
data = json.loads(resp.read())

best = None
for release in data:
    for asset in release.get('assets', []):
        name = asset.get('name', '')
        if ('aarch64-unknown-linux-musl' in name and 
            'install_only' in name and 
            name.endswith('.tar.gz') and
            'stripped' not in name):
            if best is None or int(asset['size']) > int(best[2]):
                best = (name, asset['browser_download_url'], int(asset['size']), release.get('tag_name', ''))

if not best:
    print('✗ 未找到合适的 aarch64-musl 版本')
    sys.exit(1)

name, download_url, size, tag = best
print(f'  版本: {tag}')
print(f'  文件: {name} ({size//1024//1024} MB)')

# Step 2: Download
cache = os.path.join(os.path.dirname(__file__), 'cache')
os.makedirs(cache, exist_ok=True)
local_file = os.path.join(cache, name)

if not os.path.exists(local_file):
    print(f'\n下载 Python ({size//1024//1024} MB)...')
    req = urllib.request.Request(download_url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, context=ctx, timeout=300)
    total = int(resp.headers.get('Content-Length', 0))
    downloaded = 0
    with open(local_file, 'wb') as f:
        while True:
            chunk = resp.read(8192 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                print(f'\r  {pct}% ({downloaded//1024//1024} MB)', end='', flush=True)
    print(f'\n  ✓ 下载完成')
else:
    print(f'\n使用缓存: {local_file}')

# Step 3: Extract into BUILD
ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(ROOT, 'rootfs-build')

# Extract python into a temp directory first
temp_dir = os.path.join(ROOT, 'cache', 'py_extract')
if os.path.exists(temp_dir):
    shutil.rmtree(temp_dir, ignore_errors=True)
os.makedirs(temp_dir)

print(f'\n解压 Python 到临时目录...')
with gzip.open(local_file, 'rb') as gz:
    with tarfile.open(fileobj=gz) as tf:
        tf.extractall(temp_dir)

# Find the extracted Python directory
py_dirs = [d for d in os.listdir(temp_dir) if d.startswith('python')]
if not py_dirs:
    print('✗ 未找到 Python 目录')
    sys.exit(1)

py_dir = os.path.join(temp_dir, py_dirs[0])
print(f'  Python 目录: {py_dirs[0]}')

# Step 4: Merge into BUILD
print(f'\n合并 Python 到 rootfs build...')
merged = 0
for item in os.listdir(py_dir):
    src = os.path.join(py_dir, item)
    dst = os.path.join(BUILD, item)
    if os.path.isdir(src):
        if os.path.exists(dst):
            # Merge: copy contents, don't overwrite existing files
            for root, dirs, files in os.walk(src):
                rel_root = os.path.relpath(root, src)
                dst_root = os.path.join(dst, rel_root)
                os.makedirs(dst_root, exist_ok=True)
                for f in files:
                    s = os.path.join(root, f)
                    d = os.path.join(dst_root, f)
                    if not os.path.exists(d):
                        shutil.copy2(s, d)
                        merged += 1
        else:
            shutil.copytree(src, dst, symlinks=True, 
                          ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            merged += 1
    elif os.path.isfile(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        merged += 1

print(f'  ✓ 合并完成: {merged} 个目录/文件')

# Step 5: Create python3 symlink
python_bin = os.path.join(BUILD, 'usr', 'bin', 'python3')
python_real = None
for v in ['3.12', '3.11', '3.10']:
    candidate = os.path.join(BUILD, 'usr', 'bin', f'python{v}')
    if os.path.exists(candidate):
        python_real = f'python{v}'
        break

if python_real and not os.path.exists(python_bin):
    with open(python_bin, 'w') as f:
        f.write(f'#!/bin/sh\nexec /usr/bin/{python_real} "$@"\n')
    os.chmod(python_bin, 0o755)
    print(f'  ✓ 创建 python3 -> {python_real}')

# Step 6: Verify
print(f'\n验证 Python 安装...')
for path in ['usr/bin/python3', 'usr/bin/python3.12', 'usr/bin/python3.11', 'usr/bin/python3.10',
             'usr/local/bin/python3', 'usr/local/bin/python3.12']:
    full = os.path.join(BUILD, path)
    if os.path.exists(full):
        print(f'  ✓ {path}')
        break
else:
    # Check what python files exist
    py_files = []
    for root, dirs, files in os.walk(os.path.join(BUILD, 'usr', 'bin')):
        for f in files:
            if 'python' in f.lower():
                py_files.append(os.path.join(root, f))
    if py_files:
        print(f'  发现 Python 文件:')
        for pf in py_files:
            print(f'    {pf}')

# Check site-packages
for root, dirs, files in os.walk(BUILD):
    for d in dirs:
        if d == 'site-packages':
            print(f'  ✓ site-packages: {os.path.join(root, d)}')
            break

print('\n完成！Python 已就绪。')