"""完成 rootfs 构建：合并 Python + wheels + agents + shims。"""
import os, sys, shutil, subprocess, hashlib, json, stat

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(ROOT)
BUILD = os.path.join(ROOT, 'rootfs-build')
CACHE = os.path.join(ROOT, 'cache')
ASSETS = os.path.join(PROJECT_ROOT, 'app', 'src', 'main', 'assets')
ARES_ROOTFS = os.path.join(PROJECT_ROOT, 'app', 'ares-rootfs')

# Ensure directories
os.makedirs(CACHE, exist_ok=True)

# Check if BUILD has minirootfs extracted
if not os.path.exists(os.path.join(BUILD, 'bin', 'busybox')):
    print('✗ rootfs-build 中未找到 minirootfs，先运行 build_full_rootfs.py 提取 minirootfs')
    sys.exit(1)

print('=' * 60)
print('ARES 完整 Rootfs 构建')
print('=' * 60)

# =====================================================================
# Step 1: 合并 Python (从 python-build-standalone)
# =====================================================================
print('\n--- Step 1: 合并 Python ---')
py_extract = os.path.join(CACHE, 'py_extract', 'python')
if os.path.exists(py_extract):
    # 确定 Python 版本
    py_ver_dirs = [d for d in os.listdir(os.path.join(py_extract, 'lib')) if d.startswith('python3')]
    py_ver = py_ver_dirs[0] if py_ver_dirs else 'python3.15t'
    py_lib_src = os.path.join(py_extract, 'lib', py_ver)
    
    print(f'  Python 版本: {py_ver}')
    print(f'  Python 源: {py_extract}')
    
    # 合并 bin
    usr_bin = os.path.join(BUILD, 'usr', 'bin')
    os.makedirs(usr_bin, exist_ok=True)
    
    for f in os.listdir(os.path.join(py_extract, 'bin')):
        src = os.path.join(py_extract, 'bin', f)
        dst = os.path.join(usr_bin, f)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            # 确保可执行
            os.chmod(dst, 0o755)
    print(f'  ✓ bin 文件已复制')
    
    # 创建 python3 链接
    python_real = None
    for candidate in ['python3', 'python3.15', 'python3.14', 'python3.13', 'python3.12', 'python3.11']:
        if os.path.exists(os.path.join(usr_bin, candidate)):
            python_real = candidate
            break
    if python_real and python_real != 'python3':
        # 创建 python3 -> python_real 的 shim (使用 script 因为 Android 上符号链接可能不可用)
        shim_path = os.path.join(usr_bin, 'python3')
        with open(shim_path, 'w') as f:
            f.write('#!/bin/sh\nexec /usr/bin/%s "$@"\n' % python_real)
        os.chmod(shim_path, 0o755)
        print(f'  ✓ python3 -> {python_real}')
    
    # 合并 lib
    usr_lib = os.path.join(BUILD, 'usr', 'lib')
    os.makedirs(usr_lib, exist_ok=True)
    
    # 复制 Python 版本目录
    py_lib_dst = os.path.join(usr_lib, py_ver)
    if not os.path.exists(py_lib_dst):
        shutil.copytree(py_lib_src, py_lib_dst, symlinks=True,
                       ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        print(f'  ✓ {py_ver} 已复制')
    else:
        # 合并：只复制缺失的文件
        for root, dirs, files in os.walk(py_lib_src):
            rel = os.path.relpath(root, py_lib_src)
            dst_root = os.path.join(py_lib_dst, rel)
            os.makedirs(dst_root, exist_ok=True)
            for f in files:
                s = os.path.join(root, f)
                d = os.path.join(dst_root, f)
                if not os.path.exists(d):
                    shutil.copy2(s, d)
    
    # 复制共享库
    for f in os.listdir(os.path.join(py_extract, 'lib')):
        src = os.path.join(py_extract, 'lib', f)
        dst = os.path.join(usr_lib, f)
        if os.path.isfile(src) and f.endswith('.so') and not os.path.exists(dst):
            shutil.copy2(src, dst)
            os.chmod(dst, 0o755)
    
    # 复制 include (头文件，编译 C 扩展可能需要)
    py_include_src = os.path.join(py_extract, 'include', py_ver)
    if os.path.exists(py_include_src):
        py_include_dst = os.path.join(BUILD, 'usr', 'include', py_ver)
        if not os.path.exists(py_include_dst):
            shutil.copytree(py_include_src, py_include_dst, symlinks=True)
            print(f'  ✓ include 头文件已复制')
    
    # 复制 share (tcl/tk 等)
    py_share_src = os.path.join(py_extract, 'share')
    if os.path.exists(py_share_src):
        for item in os.listdir(py_share_src):
            src = os.path.join(py_share_src, item)
            dst = os.path.join(BUILD, 'usr', 'share', item)
            if os.path.isdir(src) and not os.path.exists(dst):
                shutil.copytree(src, dst, symlinks=True)
        print(f'  ✓ share 文件已复制')
    
    # 创建 site-packages 目录
    sp_dir = os.path.join(py_lib_dst, 'site-packages')
    os.makedirs(sp_dir, exist_ok=True)
    print(f'  ✓ site-packages 就绪: {sp_dir}')
    
    PYTHON_READY = True
else:
    print('  ✗ Python 未下载，尝试使用 apk 中的 Python...')
    PYTHON_READY = False
    sp_dir = None

# =====================================================================
# Step 2: 注入 Python Wheels
# =====================================================================
print('\n--- Step 2: 注入 Python Wheels ---')
WHEELS_DIR = os.path.join(CACHE, 'wheels')
if os.path.exists(WHEELS_DIR) and PYTHON_READY:
    wheel_files = [f for f in os.listdir(WHEELS_DIR) if f.endswith('.whl')]
    sp_dir = os.path.join(BUILD, 'usr', 'lib', py_ver, 'site-packages')
    
    injected = 0
    for whl in wheel_files:
        whl_path = os.path.join(WHEELS_DIR, whl)
        # wheel 就是 zip 文件，直接解压到 site-packages
        import zipfile
        try:
            with zipfile.ZipFile(whl_path, 'r') as zf:
                zf.extractall(sp_dir)
            injected += 1
            # 显示进度
            if injected % 5 == 0:
                print(f'  已注入 {injected}/{len(wheel_files)} 个 wheels...')
        except Exception as e:
            print(f'  ⚠ 注入 {whl} 失败: {e}')
    
    print(f'  ✓ {injected} 个 wheels 已注入')
else:
    if not PYTHON_READY:
        print('  ⚠ Python 未就绪，跳过 wheels 注入')
    else:
        print('  ⚠ 未找到 wheels 目录，跳过')

# =====================================================================
# Step 3: 注入 Agent 源码
# =====================================================================
print('\n--- Step 3: 注入 Agent 源码 ---')

AGENTS = [
    ('agents/argus', 'argus'),
    ('agents/repo_audit', 'repo_audit'),
    ('agents/strIx', 'strIx'),
    ('agents/reasonix', 'reasonix'),
    ('agents/tengu', 'tengu'),
    ('agents/ghostprobe', 'ghostprobe'),
    ('agents/mcts', 'mcts'),
    ('agents/pentestgpt', 'pentestgpt'),
]

VENDOR_AGENTS_DIR = os.path.join(PROJECT_ROOT, 'vendor', 'agents')

for src_rel, agent_name in AGENTS:
    # 先看 vendor 目录
    vendor_path = os.path.join(VENDOR_AGENTS_DIR, agent_name)
    # 再看 agents 目录
    src_path = os.path.join(PROJECT_ROOT, src_rel)
    
    if os.path.exists(vendor_path):
        src_path = vendor_path
    
    if not os.path.exists(src_path):
        print(f'  ⚠ {agent_name} 源码未找到，跳过')
        continue
    
    # 目标路径: opt/agents/<name>
    dst = os.path.join(BUILD, 'opt', 'agents', agent_name)
    if os.path.exists(dst):
        shutil.rmtree(dst, ignore_errors=True)
    
    # 复制源码，排除大文件和缓存
    shutil.copytree(src_path, dst, symlinks=True,
                   ignore=shutil.ignore_patterns(
                       '__pycache__', '*.pyc', '*.pyo', '.git', '.gitignore',
                       'node_modules', '.venv', 'venv', '*.egg-info',
                       'logs', 'temp', '*.log', '*.tmp'
                   ))
    
    # 如果有 requirements.txt，记录下来（但不安装，由 bootstrap 处理或预装）
    req_file = os.path.join(dst, 'requirements.txt')
    if os.path.exists(req_file):
        with open(req_file, 'r') as f:
            req_count = len([l for l in f.readlines() if l.strip() and not l.startswith('#')])
        print(f'  ✓ {agent_name} ({req_count} deps)')
    else:
        print(f'  ✓ {agent_name}')

# =====================================================================
# Step 4: 创建 Shim 脚本
# =====================================================================
print('\n--- Step 4: 创建 Shim 脚本 ---')

# 工具 shim (在 usr/bin 中)
TOOL_SHIMS = {
    'argus': '#!/bin/sh\nexec /opt/agents/argus/run "$@"\n',
    'repo-audit': '#!/bin/sh\nexec /opt/agents/repo_audit/run "$@"\n',
    'strIx': '#!/bin/sh\nexec /opt/agents/strIx/run "$@"\n',
    'strix': '#!/bin/sh\nexec /opt/agents/strIx/run "$@"\n',
    'tengu': '#!/bin/sh\nexec /opt/agents/tengu/run "$@"\n',
    'ghostprobe': '#!/bin/sh\nexec /opt/agents/ghostprobe/run "$@"\n',
    'mcts': '#!/bin/sh\nexec /opt/agents/mcts/run "$@"\n',
    'pentestgpt': '#!/bin/sh\nexec /opt/agents/pentestgpt/run "$@"\n',
}

# LLM 工具 shim
LLM_SHIMS = {
    'llm-chat': '#!/bin/sh\nexec python3 /opt/tools/llm_chat.py "$@"\n',
    'llm-connect': '#!/bin/sh\nsource /opt/tools/llm_connect.sh\n',
}

usr_bin = os.path.join(BUILD, 'usr', 'bin')
for name, content in {**TOOL_SHIMS, **LLM_SHIMS}.items():
    path = os.path.join(usr_bin, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    os.chmod(path, 0o755)

print(f'  ✓ {len(TOOL_SHIMS)} 个 agent shims')
print(f'  ✓ {len(LLM_SHIMS)} 个 LLM shims')

# =====================================================================
# Step 5: 创建预装标记
# =====================================================================
print('\n--- Step 5: 创建预装标记 ---')
marker = os.path.join(BUILD, 'opt', 'HOSARES_PREINSTALLED')
with open(marker, 'w') as f:
    f.write('')
print(f'  ✓ /opt/HOSARES_PREINSTALLED')

# =====================================================================
# Step 6: 创建版本信息
# =====================================================================
print('\n--- Step 6: 创建版本信息 ---')
version_info = {
    'version': '1.0.0-offline',
    'build_time': '2026-08-15T12:00:00Z',
    'python': py_ver.replace('python', ''),
    'agents': [name for _, name in AGENTS if os.path.exists(os.path.join(BUILD, 'opt', 'agents', name))],
    'preinstalled': True,
    'arch': 'aarch64',
    'platform': 'android',
}
ver_file = os.path.join(BUILD, 'opt', 'version.json')
with open(ver_file, 'w', encoding='utf-8') as f:
    json.dump(version_info, f, indent=2, ensure_ascii=False)
print(f'  ✓ version.json')

# =====================================================================
# Step 7: 验证
# =====================================================================
print('\n--- Step 7: 验证 ---')
checks = [
    ('bin/busybox', 'busybox'),
    ('bin/ash', 'ash'),
    ('usr/bin/python3', 'python3'),
    ('usr/bin/pip3', 'pip3'),
    ('opt/HOSARES_PREINSTALLED', '预装标记'),
    ('opt/agents/argus', 'argus agent'),
    ('opt/agents/repo_audit', 'repo_audit agent'),
    ('opt/tools/bootstrap.sh', 'bootstrap'),
    ('opt/tools/llm_connect.sh', 'llm_connect'),
    ('usr/bin/argus', 'argus shim'),
    ('usr/bin/tengu', 'tengu shim'),
]

for path, desc in checks:
    full = os.path.join(BUILD, path)
    exists = os.path.exists(full)
    status = '✓' if exists else '✗'
    print(f'  {status} {desc}: {path}')

# Check Python site-packages
sp_dir = os.path.join(BUILD, 'usr', 'lib', py_ver, 'site-packages')
if os.path.exists(sp_dir):
    pkgs = [d for d in os.listdir(sp_dir) if os.path.isdir(os.path.join(sp_dir, d)) and not d.startswith('_')]
    print(f'\n  Python 包: {len(pkgs)} 个')
    # Show some key packages
    key_pkgs = ['pip', 'setuptools', 'wheel', 'requests', 'urllib3', 'certifi', 
                'charset_normalizer', 'idna', 'sniffio', 'anyio', 'starlette',
                'fastapi', 'uvicorn', 'httpx', 'click', 'rich', 'yaml', 'PIL']
    for kp in key_pkgs:
        found = kp in pkgs or kp.lower() in [p.lower() for p in pkgs]
        status = '✓' if found else '✗'
        print(f'    {status} {kp}')

# =====================================================================
# Step 8: 打包 rootfs.tar
# =====================================================================
print('\n--- Step 8: 打包 rootfs.tar ---')
ROOTFS_TAR = os.path.join(CACHE, 'rootfs.tar')
ROOTFS_GZ = os.path.join(CACHE, 'rootfs.tar.gz')

# 打包
result = subprocess.run(
    ['tar', '-cf', ROOTFS_TAR, '-C', BUILD, '.'],
    capture_output=True, text=True, cwd=ROOT, timeout=300
)
if result.returncode != 0:
    print(f'  ✗ tar 打包失败: {result.stderr}')
    sys.exit(1)

# 压缩
import gzip as gzip_mod
with open(ROOTFS_TAR, 'rb') as f_in:
    with gzip_mod.open(ROOTFS_GZ, 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out, 1024 * 1024)

# 获取大小
tar_size = os.path.getsize(ROOTFS_TAR)
gz_size = os.path.getsize(ROOTFS_GZ)
print(f'  ✓ rootfs.tar: {tar_size // 1024 // 1024} MB')
print(f'  ✓ rootfs.tar.gz: {gz_size // 1024 // 1024} MB')

# SHA256
sha = hashlib.sha256()
with open(ROOTFS_GZ, 'rb') as f:
    while True:
        chunk = f.read(1024 * 1024)
        if not chunk:
            break
        sha.update(chunk)
sha256 = sha.hexdigest()
print(f'  ✓ SHA256: {sha256}')

# 保存 SHA256
sha_file = ROOTFS_GZ + '.sha256'
with open(sha_file, 'w') as f:
    f.write(f'{sha256}  rootfs.tar.gz\n')
print(f'  ✓ SHA256 已保存')

print('\n' + '=' * 60)
print('构建完成！')
print(f'  输出: {ROOTFS_GZ}')
print(f'  大小: {gz_size // 1024 // 1024} MB')
print('=' * 60)