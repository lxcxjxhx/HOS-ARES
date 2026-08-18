"""最终 rootfs 构建 v4 - 使用正确的 agent 源码。"""
import os, sys, shutil, hashlib, json, zipfile, tarfile, gzip

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(PROJECT_ROOT, 'tools', 'rootfs-build')
ARES_ROOTFS = os.path.join(PROJECT_ROOT, 'app', 'ares-rootfs')
CACHE = os.path.join(PROJECT_ROOT, 'scripts', 'cache')

PY_VER = 'python3.12'
PY_SP = os.path.join(BUILD, 'usr', 'lib', PY_VER, 'site-packages')

print('=' * 60)
print('ARES 最终 Rootfs 构建 v4')
print('=' * 60)

OPT_AGENTS = os.path.join(BUILD, 'opt', 'agents')
ARES_AGENTS = os.path.join(ARES_ROOTFS, 'opt', 'agents')

# =====================================================================
# Step 0: 用 ARES 源码替换 agents
# =====================================================================
print('\n--- Step 0: 替换 agent 源码 ---')

# agents 存在于 app/ares-rootfs/opt/agents/ 中
ares_agents = ['argus', 'reasonix', 'repoaudit', 'strix']
# agents 只存在于 tools/rootfs-build/ 中
extra_agents = ['tengu', 'ghostprobe', 'mcts', 'pentestgpt']

for agent in ares_agents:
    src = os.path.join(ARES_AGENTS, agent)
    dst = os.path.join(OPT_AGENTS, agent)
    if os.path.exists(src):
        if os.path.exists(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, symlinks=True,
                       ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        print(f'  ✓ {agent} (from app/ares-rootfs)')
    else:
        print(f'  ⚠ {agent} 源码未找到于 app/ares-rootfs')

for agent in extra_agents:
    dst = os.path.join(OPT_AGENTS, agent)
    if os.path.exists(dst):
        print(f'  ✓ {agent} (保留 tools/rootfs-build 版本)')
    else:
        print(f'  ⚠ {agent} 未找到')

# =====================================================================
# Step 1: 添加 run.sh 脚本
# =====================================================================
print('\n--- Step 1: 部署 run.sh 脚本 ---')

# 从 app/ares-rootfs/agents/ 复制
agents_run_src = os.path.join(ARES_ROOTFS, 'agents')
if os.path.exists(agents_run_src):
    for agent_dir in os.listdir(agents_run_src):
        agent_src = os.path.join(agents_run_src, agent_dir)
        agent_dst = os.path.join(OPT_AGENTS, agent_dir)
        if os.path.isdir(agent_src):
            os.makedirs(agent_dst, exist_ok=True)
            for fname in os.listdir(agent_src):
                fsrc = os.path.join(agent_src, fname)
                fdst = os.path.join(agent_dst, fname)
                if os.path.isfile(fsrc):
                    shutil.copy2(fsrc, fdst)
                    os.chmod(fdst, 0o755)
            print(f'  ✓ {agent_dir}/run.sh')

# 为缺失 run.sh 的 agent 创建
run_sh_map = {
    'tengu': '#!/bin/sh\nset -e\nTARGET="$1"\nexec python3 -c "from tengu.server import main; main()" "$@"\n',
    'ghostprobe': '#!/bin/sh\nset -e\nTARGET="$1"\nexec python3 /opt/agents/ghostprobe/ghostprobe/cli.py "$@"\n',
    'mcts': '#!/bin/sh\nset -e\nTARGET="$1"\nexec python3 /opt/agents/mcts/src/mcts/__main__.py "$@"\n',
    'pentestgpt': '#!/bin/sh\nset -e\nTARGET="$1"\nexec python3 /opt/agents/pentestgpt/pentestgpt_legacy/main.py "$@"\n',
}
for agent, content in run_sh_map.items():
    agent_dst = os.path.join(OPT_AGENTS, agent)
    if os.path.exists(agent_dst):
        run_sh = os.path.join(agent_dst, 'run.sh')
        if not os.path.exists(run_sh):
            with open(run_sh, 'w', encoding='utf-8') as f:
                f.write(content)
            os.chmod(run_sh, 0o755)
            print(f'  ✓ {agent}/run.sh (创建)')

# =====================================================================
# Step 2: 确保 bootstrap.sh 在 rootfs 根目录
# =====================================================================
print('\n--- Step 2: bootstrap.sh ---')
bootstrap_src = os.path.join(ARES_ROOTFS, 'bootstrap.sh')
bootstrap_dst = os.path.join(BUILD, 'bootstrap.sh')
if os.path.exists(bootstrap_src):
    shutil.copy2(bootstrap_src, bootstrap_dst)
    os.chmod(bootstrap_dst, 0o755)
    print(f'  ✓ bootstrap.sh')
else:
    print(f'  ✗ bootstrap.sh 未找到')

# =====================================================================
# Step 3: llm_connect.sh
# =====================================================================
print('\n--- Step 3: llm_connect.sh ---')
llm_src = os.path.join(ARES_AGENTS, 'llm_connect.sh')
if os.path.exists(llm_src):
    llm_dst = os.path.join(OPT_AGENTS, 'llm_connect.sh')
    shutil.copy2(llm_src, llm_dst)
    os.chmod(llm_dst, 0o755)
    
    # Also copy to opt/tools
    tools_dir = os.path.join(BUILD, 'opt', 'tools')
    os.makedirs(tools_dir, exist_ok=True)
    shutil.copy2(llm_src, os.path.join(tools_dir, 'llm_connect.sh'))
    os.chmod(os.path.join(tools_dir, 'llm_connect.sh'), 0o755)
    print(f'  ✓ llm_connect.sh')

# =====================================================================
# Step 4: 更新 shim
# =====================================================================
print('\n--- Step 4: shim 脚本 ---')
usr_bin = os.path.join(BUILD, 'usr', 'bin')
shims = {
    'argus': '#!/bin/sh\nexec python3 /opt/agents/argus/src/argus/cli.py "$@"\n',
    'repo-audit': '#!/bin/sh\nexec python3 /opt/agents/repoaudit/src/repoaudit.py "$@"\n',
    'strix': '#!/bin/sh\nexec python3 /opt/agents/strix/strix/interface/cli.py "$@"\n',
    'tengu': '#!/bin/sh\nexec python3 -c "from tengu.server import main; main()" "$@"\n',
    'ghostprobe': '#!/bin/sh\nexec python3 /opt/agents/ghostprobe/ghostprobe/cli.py "$@"\n',
    'mcts': '#!/bin/sh\nexec python3 /opt/agents/mcts/src/mcts/__main__.py "$@"\n',
    'pentestgpt': '#!/bin/sh\nexec python3 /opt/agents/pentestgpt/pentestgpt_legacy/main.py "$@"\n',
    'llm-chat': '#!/bin/sh\nexec python3 /opt/tools/llm_chat.py "$@"\n',
    'llm-connect': '#!/bin/sh\nsource /opt/tools/llm_connect.sh\n',
    'zap': '#!/bin/sh\nexec java -jar /opt/zap/zap.jar "$@"\n',
}
for name, content in shims.items():
    path = os.path.join(usr_bin, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    os.chmod(path, 0o755)
print(f'  ✓ {len(shims)} 个 shim')

# =====================================================================
# Step 4.5: 安装预下载 wheels 到 site-packages
# =====================================================================
print('\n--- Step 4.5: 安装预下载 wheels ---')
WHEELS_DIR = os.path.join(PROJECT_ROOT, 'scripts', 'wheels')
PY_DEPS = os.path.join(BUILD, 'opt', 'python-deps')
os.makedirs(PY_DEPS, exist_ok=True)

if os.path.exists(WHEELS_DIR):
    wheel_files = [f for f in os.listdir(WHEELS_DIR) if f.endswith('.whl')]
    print(f'  找到 {len(wheel_files)} 个预下载 wheels')
    
    for whl in wheel_files:
        whl_src = os.path.join(WHEELS_DIR, whl)
        # Copy to python-deps as fallback
        shutil.copy2(whl_src, os.path.join(PY_DEPS, whl))
        # Also install to site-packages if not already present
        try:
            with zipfile.ZipFile(whl_src, 'r') as zf:
                # Only extract package dirs, skip dist-info and __pycache__
                for name in zf.namelist():
                    if '.dist-info/' in name or '.data/' in name or '__pycache__' in name:
                        continue
                    if name.endswith('.pyc'):
                        continue
                    zf.extract(name, PY_SP)
            print(f'  ✓ {whl}')
        except Exception as e:
            print(f'  ⚠ {whl}: {e}')
else:
    print(f'  ⚠ scripts/wheels 目录不存在')

print(f'  python-deps fallback: {len(os.listdir(PY_DEPS))} 个 wheels')

# =====================================================================
# Step 5: 标记和版本
# =====================================================================
print('\n--- Step 5: 标记和版本 ---')
marker = os.path.join(BUILD, 'opt', 'HOSARES_PREINSTALLED')
with open(marker, 'w') as f:
    f.write('')
print(f'  ✓ 预装标记')

existing_pkgs = [d for d in os.listdir(PY_SP) if os.path.isdir(os.path.join(PY_SP, d)) and not d.startswith('_') and not d.endswith('.dist-info')]
agents_list = ['argus', 'reasonix', 'repoaudit', 'strix', 'tengu', 'ghostprobe', 'mcts', 'pentestgpt']

version_info = {
    'version': '1.0.0-offline',
    'build_time': '2026-08-15T12:00:00Z',
    'python': PY_VER,
    'agents': agents_list,
    'tools': ['zap', 'busybox', 'proot'],
    'preinstalled': True,
    'arch': 'aarch64',
    'platform': 'android',
    'packages': len(existing_pkgs),
}
ver_file = os.path.join(BUILD, 'opt', 'version.json')
with open(ver_file, 'w', encoding='utf-8') as f:
    json.dump(version_info, f, indent=2, ensure_ascii=False)
print(f'  ✓ version.json')

# =====================================================================
# Step 6: 最终验证
# =====================================================================
print('\n--- Step 6: 最终验证 ---')
checks = [
    ('bin/busybox', 'busybox'),
    (f'usr/bin/{PY_VER}', f'{PY_VER}'),
    ('bootstrap.sh', 'bootstrap.sh'),
    ('opt/HOSARES_PREINSTALLED', '预装标记'),
    ('opt/python-deps', 'python-deps fallback'),
    ('opt/agents/argus/src/argus/cli.py', 'argus cli.py'),
    ('opt/agents/argus/run.sh', 'argus run.sh'),
    ('opt/agents/reasonix/reasonix_agent.py', 'reasonix'),
    ('opt/agents/repoaudit/src/repoaudit.py', 'repoaudit'),
    ('opt/agents/strix/strix/interface/cli.py', 'strix'),
    ('opt/agents/tengu/src/tengu/__init__.py', 'tengu'),
    ('opt/agents/ghostprobe/ghostprobe/cli.py', 'ghostprobe'),
    ('opt/agents/mcts/src/mcts/__main__.py', 'mcts'),
    ('opt/agents/pentestgpt/pentestgpt_legacy/main.py', 'pentestgpt'),
    ('opt/tools/llm_connect.sh', 'llm_connect.sh'),
    ('usr/bin/argus', 'argus shim'),
    ('usr/bin/tengu', 'tengu shim'),
    ('opt/zap/zap.sh', 'ZAP'),
]
for path, desc in checks:
    full = os.path.join(BUILD, path)
    exists = os.path.exists(full)
    status = '✓' if exists else '✗'
    print(f'  {status} {desc}')

# =====================================================================
# Step 7: 打包
# =====================================================================
print('\n--- Step 7: 打包 ---')
os.makedirs(CACHE, exist_ok=True)
ROOTFS_TAR = os.path.join(CACHE, 'rootfs-offline.tar')
ROOTFS_GZ = ROOTFS_TAR + '.gz'

for f in [ROOTFS_TAR, ROOTFS_GZ]:
    if os.path.exists(f):
        os.remove(f)

print('  创建 tar...')
file_count = 0
error_count = 0

with tarfile.open(ROOTFS_TAR, 'w', format=tarfile.GNU_FORMAT) as tar:
    for root, dirs, files in os.walk(BUILD):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for fname in files:
            full_path = os.path.join(root, fname)
            arcname = os.path.relpath(full_path, BUILD)
            try:
                tar.add(full_path, arcname=arcname, recursive=False)
                file_count += 1
                if file_count % 5000 == 0:
                    print(f'    {file_count} 个...')
            except Exception as e:
                error_count += 1
                if error_count <= 5:
                    print(f'    ⚠ {arcname}: {e}')

print(f'  ✓ {file_count} 个文件 ({error_count} 跳过)')

tar_size = os.path.getsize(ROOTFS_TAR)
print(f'  tar: {tar_size // 1024 // 1024} MB')

print('  压缩中...')
with open(ROOTFS_TAR, 'rb') as f_in:
    with gzip.open(ROOTFS_GZ, 'wb', compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out, 1024 * 1024)
os.remove(ROOTFS_TAR)

gz_size = os.path.getsize(ROOTFS_GZ)
print(f'  ✓ rootfs-offline.tar.gz: {gz_size // 1024 // 1024} MB')

sha = hashlib.sha256()
with open(ROOTFS_GZ, 'rb') as f:
    while True:
        chunk = f.read(1024 * 1024)
        if not chunk: break
        sha.update(chunk)
sha256 = sha.hexdigest()
print(f'  ✓ SHA256: {sha256}')

with open(ROOTFS_GZ + '.sha256', 'w') as f:
    f.write(f'{sha256}  rootfs-offline.tar.gz\n')

print('\n' + '=' * 60)
print(f'完成! 大小: {gz_size // 1024 // 1024} MB, 文件: {file_count}, 包: {len(existing_pkgs)}, Agents: {len(agents_list)}')
print('=' * 60)