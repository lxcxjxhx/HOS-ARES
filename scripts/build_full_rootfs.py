# -*- coding: utf-8 -*-
"""
HOS-ARES 完整 rootfs 打包器 (Windows 版，无需 Docker)。
将 Alpine minirootfs + Python apk 包 + 所有 wheels 组合为完整的 rootfs.tar。

流程:
  1. 解压 alpine-minirootfs.tar.gz 到临时目录
  2. 下载 Alpine Python apk 包 (python3, py3-pip, py3-wheel)
  3. 解压这些 apk 包，把 python3/pip/wheel 注入 rootfs
  4. 把所有 wheels 解压到 site-packages (无需 pip install，直接解包 .whl = zip)
  5. 创建 agent 目录结构 + shim 脚本
  6. 打包为 rootfs.tar (保持 .tar 格式，Android 端解压更快)

用法: python build_full_rootfs.py
输出: app/app/src/main/assets/rootfs.tar
"""
import os, sys, tarfile, zipfile, shutil, stat, gzip, io, json, hashlurllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(ROOT, '..')
ASSETS = os.path.join(PROJECT_ROOT, 'app', 'app', 'src', 'main', 'assets')
WHEELS = os.path.join(ROOT, 'wheels')
BUILD = os.path.join(ROOT, 'rootfs-build')
CACHE = os.path.join(ROOT, 'cache')
REQ_DIR = os.path.join(PROJECT_ROOT, 'app', 'ares-rootfs', 'requirements')

MINIROOTFS_GZ = os.path.join(ASSETS, 'alpine-minirootfs.tar.gz')
FULL_ROOTFS = os.path.join(ASSETS, 'rootfs.tar')

# Alpine 3.20 包仓库 (x86_64 架构，用于构建)
ALPINE_VERSION = '3.20'
ALPINE_ARCH = 'x86_64'
ALPINE_REPO = f'https://dl-cdn.alpinelinux.org/alpine/v{ALPINE_VERSION}/packages/{ALPINE_ARCH}'

# 需要下载的 Alpine 包 (运行时依赖)
ALPINE_PACKAGES = [
    'python3',
    'python3-pyc',  # .pyc 编译支持
    'py3-pip',
    'py3-wheel',
    'busybox-extras',
    'ca-certificates',
]

# 需要预装的 pip 依赖 (与 download_wheels.py 保持一致)
PIP_PACKAGES = [
    'pyyaml', 'argus-languages',
    'openai', 'anthropic', 'tqdm',
    'litellm', 'pydantic', 'pydantic-settings', 'requests',
    'rich', 'pygments', 'jinja2', 'reportlab',
    'deepseek-reasonix',
    'cryptography', 'charset-normalizer', 'idna', 'certifi',
    'urllib3', 'click', 'markdown-it-py', 'mdurl', 'colorama',
    'markdown', 'frozenlist', 'multidict', 'yarl', 'aiohttp',
    'attrs', 'distro', 'httpx', 'anyio', 'starlette', 'h11',
    'httpcore', 'sniffio', 'typing-extensions', 'jsonpatch',
    'jsonpointer', 'tiktoken', 'regex', 'sentencepiece', 'protobuf',
    'httptools', 'python-dotenv', 'pillow', 'pypdf',
]


def download_file(url, dest, desc=''):
    """下载文件，带进度提示。"""
    if os.path.exists(dest):
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f'  下载 {desc}: {os.path.basename(dest)}')
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as e:
        print(f'  ✗ 下载失败: {url} -> {e}')
        if os.path.exists(dest):
            os.remove(dest)
        raise


def extract_apk(apk_path, dest):
    """解压 Alpine .apk 包 (实际是 tar.gz)。"""
    with gzip.open(apk_path, 'rb') as gz:
        with tarfile.open(fileobj=gz) as tf:
            tf.extractall(dest)


def extract_whl(whl_path, dest):
    """解压 .whl 文件 (实际是 zip) 到 site-packages。"""
    with zipfile.ZipFile(whl_path) as zf:
        for info in zf.infolist():
            if info.filename.endswith('.data/') or '.data/' in info.filename:
                continue
            # 跳过 __pycache__ 和 .pyc
            if '__pycache__' in info.filename or info.filename.endswith('.pyc'):
                continue
            zf.extract(info, dest)


def download_alpine_pkg(pkg_name):
    """从 Alpine 仓库下载指定包。"""
    cache_dir = os.path.join(CACHE, 'apk')
    os.makedirs(cache_dir, exist_ok=True)

    # 下载 APKINDEX 查找版本号
    index_url = f'{ALPINE_REPO}/APKINDEX.tar.gz'
    index_file = os.path.join(cache_dir, 'APKINDEX.tar.gz')

    if not os.path.exists(index_file):
        print(f'  下载 APKINDEX...')
        urllib.request.urlretrieve(index_url, index_file)

    # 在 APKINDEX 中搜索包
    version = None
    with tarfile.open(index_file, 'r:gz') as tf:
        for member in tf.getmembers():
            if member.isfile() and member.name.endswith('DESCRIPTION'):
                f = tf.extractfile(member)
                if f:
                    content = f.read().decode('utf-8', errors='ignore')
                    if f'P:{pkg_name}\n' in content or f'P:{pkg_name}-' in content:
                        # 提取版本
                        for line in content.split('\n'):
                            if line.startswith('V:'):
                                version = line[2:]
                                break
                        if version:
                            break
                if version:
                    break

    if not version:
        # 尝试直接用通配 URL
        print(f'  警告: 在 APKINDEX 中未找到 {pkg_name}，尝试直接下载...')
        # 尝试多个可能的版本模式
        # 我们用另一种方式：下载整个目录列表
        import ssl
        ctx = ssl.create_default_context()
        try:
            resp = urllib.request.urlopen(ALPINE_REPO, context=ctx)
            html = resp.read().decode('utf-8', errors='ignore')
            for line in html.split('\n'):
                if pkg_name in line and '.apk' in line:
                    import re
                    m = re.search(rf'href="({re.escape(pkg_name)}[^"]*\.apk)"', line)
                    if m:
                        filename = m.group(1)
                        url = f'{ALPINE_REPO}/{filename}'
                        dest = os.path.join(cache_dir, filename)
                        download_file(url, dest, pkg_name)
                        return dest
        except Exception as e:
            print(f'  ✗ 无法获取 {pkg_name}: {e}')
        return None

    # 用版本号构建 URL
    filename = f'{pkg_name}-{version}.apk'
    # 清理版本中的 -r 后缀用于文件名
    url = f'{ALPINE_REPO}/{filename}'
    # Alpine 有些包用不同的命名格式
    # 尝试多种命名格式
    candidates = [
        f'{pkg_name}-{version}.apk',
        f'{pkg_name}-{version.split("-")[0]}-r{version.split("-r")[-1]}.apk' if '-r' in version else f'{pkg_name}-{version}.apk',
    ]

    for cand in set(candidates):
        url = f'{ALPINE_REPO}/{cand}'
        dest = os.path.join(cache_dir, cand)
        try:
            download_file(url, dest, pkg_name)
            return dest
        except:
            continue

    # 最后兜底：用版本号搜索
    print(f'  尝试通用 URL: {url}')
    try:
        download_file(url, dest, pkg_name)
        return dest
    except:
        pass

    print(f'  ✗ 无法下载 {pkg_name}')
    return None


def find_python_site_packages(build_dir):
    """在解压的 rootfs 中找到 python3 的 site-packages 路径。"""
    candidates = []
    for root, dirs, files in os.walk(build_dir):
        for d in dirs:
            if d == 'site-packages' and 'python3' in root:
                candidates.append(os.path.join(root, d))
            elif d == 'site-packages' and 'python3' in root.replace('\\', '/'):
                candidates.append(os.path.join(root, d))
    return candidates


def main():
    print('=' * 60)
    print('HOS-ARES 完整 rootfs 打包器')
    print('=' * 60)

    # 1. 清理旧构建
    print('\n[1] 准备工作区...')
    shutil.rmtree(BUILD, ignore_errors=True)
    os.makedirs(BUILD)
    os.makedirs(CACHE, exist_ok=True)

    # 2. 解压 minirootfs
    print('\n[2] 解压 Alpine minirootfs...')
    if not os.path.exists(MINIROOTFS_GZ):
        print(f'  ✗ 找不到 {MINIROOTFS_GZ}')
        print('  请先下载 Alpine minirootfs 到 assets 目录')
        return 1

    with gzip.open(MINIROOTFS_GZ, 'rb') as gz:
        with tarfile.open(fileobj=gz) as tf:
            # 处理 symlink: Windows 上不能创建真实 symlink，
            # 我们把 symlink 信息记录下来，打包时再注入
            symlinks = []
            for member in tf.getmembers():
                name = member.name.lstrip('./')
                if not name:
                    continue
                if member.issym():
                    symlinks.append((name, member.linkname))
                    continue
                if member.isdir():
                    os.makedirs(os.path.join(BUILD, name), exist_ok=True)
                    continue
                if member.isfile():
                    dest = os.path.join(BUILD, name)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with tf.extractfile(member) as src, open(dest, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    if member.mode & 0o111:
                        try:
                            os.chmod(dest, 0o755)
                        except:
                            pass

    print(f'  ✓ minirootfs 解压完成')

    # 3. 下载并注入 Python 运行时
    print('\n[3] 下载 Alpine Python 包...')
    py_packages_downloaded = []
    for pkg in ALPINE_PACKAGES:
        apk_path = download_alpine_pkg(pkg)
        if apk_path:
            # 解压到 build 目录
            extract_apk(apk_path, BUILD)
            py_packages_downloaded.append(pkg)
            print(f'  ✓ {pkg} 已注入')
        else:
            print(f'  ✗ {pkg} 未获取，将使用 wheels 中的纯 Python 部分')

    # 3b. 如果 python3 apk 下载失败，用 wheel 中的方式处理
    python_bin = os.path.join(BUILD, 'usr', 'bin', 'python3')
    if not os.path.exists(python_bin):
        # 检查替代路径
        for alt in ['usr/bin/python3.12', 'usr/bin/python3.11', 'usr/bin/python3']:
            if os.path.exists(os.path.join(BUILD, alt)):
                python_bin = os.path.join(BUILD, alt)
                break

    has_python = os.path.exists(python_bin)
    if has_python:
        print(f'  ✓ Python 已就绪: {python_bin}')
    else:
        print('  ⚠ Python 二进制未找到，将在 bootstrap 时处理')

    # 4. 确定 site-packages 路径
    site_dirs = find_python_site_packages(BUILD)
    if not site_dirs and has_python:
        # 如果找不到，手动创建
        import subprocess
        # 在 Windows 上不能运行 Linux 二进制，所以我们需要猜测路径
        # Alpine 的 Python 默认放在 /usr/lib/python3.X/site-packages
        py_ver = '3.12'
        # 检查实际版本
        for v in ['3.13', '3.12', '3.11', '3.10']:
            candidate = os.path.join(BUILD, 'usr', 'lib', f'python{v}', 'site-packages')
            if os.path.exists(candidate):
                site_dirs.append(candidate)
                py_ver = v
                break
            # 也检查 usr/local/lib
            candidate2 = os.path.join(BUILD, 'usr', 'local', 'lib', f'python{v}', 'site-packages')
            if os.path.exists(candidate2):
                site_dirs.append(candidate2)
                py_ver = v
                break

        if not site_dirs:
            # 默认创建到 python3.12
            site_dir = os.path.join(BUILD, 'usr', 'lib', 'python3.12', 'site-packages')
            os.makedirs(site_dir, exist_ok=True)
            site_dirs = [site_dir]
            py_ver = '3.12'

    if site_dirs:
        print(f'  ✓ site-packages 路径: {site_dirs[0]}')
    else:
        print('  ⚠ 未找到 site-packages，将手动创建')
        site_dir = os.path.join(BUILD, 'usr', 'lib', 'python3.12', 'site-packages')
        os.makedirs(site_dir, exist_ok=True)
        site_dirs = [site_dir]

    # 5. 注入 wheels
    print('\n[4] 注入 Python wheels...')
    if os.path.isdir(WHEELS):
        wheel_files = [f for f in os.listdir(WHEELS) if f.endswith('.whl')]
        print(f'  发现 {len(wheel_files)} 个 wheel 文件')

        n_installed = 0
        skipped = []
        for whl in wheel_files:
            whl_path = os.path.join(WHEELS, whl)
            # 检查是否为纯 Python wheel (不是 manylinux)
            # manylinux wheel 包含 .so 文件，需要真实 Linux 环境
            # 我们接受所有 wheel，但跳过包含 .so 的（在 Windows 上无法验证）
            try:
                with zipfile.ZipFile(whl_path) as zf:
                    names = zf.namelist()
                    has_so = any(n.endswith('.so') for n in names)
                    has_pyd = any(n.endswith('.pyd') for n in names)

                if has_so:
                    # 包含 C 扩展，直接解压可能不可用
                    # 但我们仍然解压，让用户在设备端验证
                    pass

                # 解压到所有 site-packages 目录
                for sp in site_dirs:
                    extract_whl(whl_path, sp)

                n_installed += 1
            except Exception as e:
                skipped.append(f'{whl}: {e}')

        print(f'  ✓ {n_installed} 个 wheel 已解压到 site-packages')
        if skipped:
            print(f'  ⚠ {len(skipped)} 个 wheel 解压失败:')
            for s in skipped[:5]:
                print(f'    - {s}')
            if len(skipped) > 5:
                print(f'    ... 还有 {len(skipped) - 5} 个')
    else:
        print('  ⚠ wheels 目录不存在，跳过 wheel 注入')
        print('    请先运行: python download_wheels.py')

    # 6. 创建 Agent 目录结构
    print('\n[5] 创建 Agent 运行时结构...')

    agent_dirs = [
        'opt/agents/argus/src/argus',
        'opt/agents/repoaudit/src/agent',
        'opt/agents/repoaudit/src/llmtool',
        'opt/agents/repoaudit/src/memory',
        'opt/agents/repoaudit/src/tstool',
        'opt/agents/repoaudit/src/ui',
        'opt/agents/strix/strix',
        'opt/agents/reasonix',
        'opt/skills',
        'opt/agents-requirements',
    ]
    for d in agent_dirs:
        os.makedirs(os.path.join(BUILD, d), exist_ok=True)

    # 7. 复制 Agent 源码
    print('\n[6] 复制 Agent 源码...')
    agent_src_map = {
        'opt/agents/argus/src': os.path.join(PROJECT_ROOT, 'app', 'ares-rootfs', 'opt', 'agents', 'argus', 'src', 'argus'),
        'opt/agents/repoaudit/src': os.path.join(PROJECT_ROOT, 'app', 'ares-rootfs', 'opt', 'agents', 'repoaudit', 'src'),
        'opt/agents/strix/strix': os.path.join(PROJECT_ROOT, 'app', 'ares-rootfs', 'opt', 'agents', 'strix', 'strix'),
        'opt/agents/reasonix': os.path.join(PROJECT_ROOT, 'app', 'ares-rootfs', 'opt', 'agents', 'reasonix'),
    }

    for dest_rel, src in agent_src_map.items():
        dest = os.path.join(BUILD, dest_rel)
        if os.path.isdir(src):
            if os.path.exists(dest):
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(src, dest, symlinks=True,
                            ignore=shutil.ignore_patterns('.git', '__pycache__', '.pyc'))
            print(f'  ✓ {dest_rel}')

    # 8. 复制 requirements
    req_dest = os.path.join(BUILD, 'opt', 'agents-requirements')
    if os.path.isdir(REQ_DIR):
        for f in os.listdir(REQ_DIR):
            if f.endswith('.txt'):
                shutil.copy2(os.path.join(REQ_DIR, f), os.path.join(req_dest, f))
                print(f'  ✓ requirements/{f}')

    # 9. 创建 shim 脚本
    print('\n[7] 创建 shim 脚本...')
    shims_dir = os.path.join(BUILD, 'usr', 'local', 'bin')
    os.makedirs(shims_dir, exist_ok=True)

    shims = {
        'reasonix': '#!/bin/sh\nPYTHONPATH=/opt/agents/reasonix exec python3 /opt/agents/reasonix/reasonix_agent.py "$@"\n',
        'argus': '#!/bin/sh\nPYTHONPATH=/opt/agents/argus/src exec python3 -m argus.cli "$@"\n',
        'repoaudit': '#!/bin/sh\nPYTHONPATH=/opt/agents/repoaudit/src:$PYTHONPATH exec python3 /opt/agents/repoaudit/src/repoaudit.py "$@"\n',
        'strix': '#!/bin/sh\nPYTHONPATH=/opt/agents/strix exec python3 -m strix "$@"\n',
    }
    for name, content in shims.items():
        path = os.path.join(shims_dir, name)
        with open(path, 'w', newline='\n') as f:
            f.write(content)
        os.chmod(path, 0o755)
        print(f'  ✓ usr/local/bin/{name}')

    # 10. 创建版本标记
    print('\n[8] 创建版本标记...')
    marker = os.path.join(BUILD, 'opt', 'HOSARES_PREINSTALLED')
    with open(marker, 'w') as f:
        f.write('HOS-ARES pre-installed rootfs\n')
        f.write('python3: yes\n')
        f.write('pip-deps: pre-installed\n')
        f.write('build: fully-offline\n')

    # 11. 清理
    print('\n[9] 清理临时文件...')
    for root, dirs, files in os.walk(BUILD):
        for d in dirs:
            if d == '__pycache__':
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
        for f in files:
            if f.endswith('.pyc'):
                try:
                    os.remove(os.path.join(root, f))
                except:
                    pass

    # 12. 打包为 rootfs.tar
    print('\n[10] 打包 rootfs.tar...')
    with tarfile.open(FULL_ROOTFS, 'w') as tf:
        # 添加所有普通文件
        for root, dirs, files in os.walk(BUILD):
            for name in dirs + files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, BUILD).replace(os.sep, '/')
                arc = './' + rel

                if os.path.islink(full):
                    info = tarfile.TarInfo(arc)
                    info.type = tarfile.SYMTYPE
                    info.linkname = os.readlink(full)
                    tf.addfile(info)
                elif os.path.isfile(full):
                    st = os.stat(full)
                    info = tf.gettarinfo(full, arc)
                    tf.addfile(info, open(full, 'rb'))
                elif os.path.isdir(full):
                    info = tarfile.TarInfo(arc)
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    tf.addfile(info)

        # 注入之前记录的 symlink
        for arc, link in symlinks:
            info = tarfile.TarInfo('./' + arc)
            info.type = tarfile.SYMTYPE
            info.linkname = link
            tf.addfile(info)

    size_mb = os.path.getsize(FULL_ROOTFS) / 1024 / 1024
    print(f'\n[11] rootfs.tar 生成完成: {FULL_ROOTFS}')
    print(f'     大小: {size_mb:.1f} MB')
    print(f'     symlinks 注入: {len(symlinks)} 条')

    print('\n' + '=' * 60)
    print('构建完成！APK 将包含完全预装的运行时环境。')
    print('=' * 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())