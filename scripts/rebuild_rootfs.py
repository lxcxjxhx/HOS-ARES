# -*- coding: utf-8 -*-
"""HOS-ARES rootfs 重建：预装 python3 运行时 + 全部依赖 + 三工具，首次启动零安装。
用法: python rebuild_rootfs.py
Windows 上无法创建真实 symlink：解包时跳过 symlink 条目并记录，
打包阶段以 SYMTYPE 注入 tar，Android 端 toybox tar 解压时正常建链。
"""
import tarfile, zipfile, os, shutil, glob, stat

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, '..', 'app/app/src/main/assets/rootfs.tar')
BUILD = os.path.join(ROOT, 'rootfs-build')
APKS = os.path.join(ROOT, 'apks')
WHEELS = os.path.join(ROOT, 'wheels')
SYMLINKS = []  # (arcpath, linkname) 相对 BUILD

def extract_safe(tf, dest):
    """提取 tar 成员；symlink 只记录不创建（Windows）。"""
    for m in tf.getmembers():
        base = os.path.basename(m.name)
        if base == '.PKGINFO' or base.startswith('.SIGN'):
            continue
        if m.issym():
            rel = os.path.relpath(m.name, '.').replace(os.sep, '/')
            SYMLINKS.append((rel.lstrip('./'), m.linkname))
            continue
        if m.isdir():
            os.makedirs(os.path.join(dest, m.name.lstrip('./')), exist_ok=True)
            continue
        if m.isfile():
            target = os.path.join(dest, m.name.lstrip('./'))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tf.extractfile(m) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            if m.mode & 0o111:
                os.chmod(target, 0o755)

# 1) 解压原 rootfs
shutil.rmtree(BUILD, ignore_errors=True)
os.makedirs(BUILD)
with tarfile.open(SRC, 'r:gz') as tf:
    extract_safe(tf, BUILD)
print('[1] 原 rootfs 解压完成')

# 2) 解包 apk（python3 + 依赖 + gcompat）
n = 0
for apk in sorted(glob.glob(os.path.join(APKS, '*.apk'))):
    with tarfile.open(apk, 'r:gz') as tf:
        extract_safe(tf, BUILD)
    n += 1
print(f'[2] apk 解包完成: {n} 个（记录 symlink {len(SYMLINKS)} 条）')

# 3) 解包全部 wheels 到 site-packages
SP = os.path.join(BUILD, 'usr/lib/python3.12/site-packages')
os.makedirs(SP, exist_ok=True)
n = 0
for w in glob.glob(os.path.join(WHEELS, '*.whl')):
    with zipfile.ZipFile(w) as z:
        for m in z.infolist():
            if '.data/' in m.filename:
                continue
            z.extract(m, SP)
    n += 1
print(f'[3] wheels 预装完成: {n} 个')

# 4) 注入三仓库
def copy_tree(src, dst, keep=None):
    src = os.path.normpath(src)
    dst = os.path.normpath(dst)
    # 旧 rootfs.tar 可能已含同名注入目录：先删目标，保证注入最新 vendor 内容
    if os.path.exists(dst):
        shutil.rmtree(dst, ignore_errors=True)
    if keep is None:
        shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns('.git', '.github'))
        return
    os.makedirs(dst, exist_ok=True)
    for item in keep:
        s = os.path.join(src, item)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(dst, item), symlinks=True,
                            ignore=shutil.ignore_patterns('.git', '.github'))
        elif os.path.isfile(s):
            shutil.copy2(s, os.path.join(dst, item))

ARGUS_KEEP = ['orchestrator', 'cli', 'demo', 'demo_target', 'docs', 'README.md', 'LICENSE', 'NOTICE', 'SECURITY.md']
copy_tree(os.path.join(ROOT, '..', 'vendor/argus'), os.path.join(BUILD, 'opt/argus'), ARGUS_KEEP)
copy_tree(os.path.join(ROOT, '..', 'vendor/pentestgpt'), os.path.join(BUILD, 'opt/pentestgpt'))
copy_tree(os.path.join(ROOT, '..', 'vendor/repoaudit'), os.path.join(BUILD, 'opt/repoaudit'))
copy_tree(os.path.join(ROOT, '..', 'vendor/tengu'), os.path.join(BUILD, 'opt/tengu'))
copy_tree(os.path.join(ROOT, '..', 'vendor/ghostprobe'), os.path.join(BUILD, 'opt/ghostprobe'))
copy_tree(os.path.join(ROOT, '..', 'vendor/mcts'), os.path.join(BUILD, 'opt/mcts'))
copy_tree(os.path.join(ROOT, 'zap-unpacked/ZAP_2.17.0'), os.path.join(BUILD, 'opt/zap'))
print('[4] 六工具+ZAP 注入 /opt: argus/pentestgpt/repoaudit/tengu/ghostprobe/mcts/zap')

# 5) console shim（wheel 预装不生成入口脚本；新工具走 PYTHONPATH 源码）
SHIMS = {
    'pentestgpt-legacy': 'PYTHONPATH=/opt/pentestgpt exec /usr/bin/python3 -c "from pentestgpt_legacy.main import main; import sys; sys.exit(main())" "$@"',
    'argus-probe': 'PYTHONPATH=/opt/argus/cli exec /usr/bin/python3 -m argus_probe "$@"',
    'tengu': 'PYTHONPATH=/opt/tengu/src exec /usr/bin/python3 -m tengu.server "$@"',
    'mcts-mcp': 'PYTHONPATH=/opt/mcts/src exec /usr/bin/python3 -c "from mcts.mcp_server.main import run; import sys; sys.exit(run())" "$@"',
    'ghostprobe': 'PYTHONPATH=/opt/ghostprobe exec /usr/bin/python3 -c "from ghostprobe.cli import main; import sys; sys.exit(main())" "$@"',
    'mitmproxy': 'exec /usr/bin/python3 -c "from mitmproxy.tools.main import mitmproxy; import sys; sys.exit(mitmproxy(sys.argv[1:]))" "$@"',
    'mitmdump': 'exec /usr/bin/python3 -c "from mitmproxy.tools.main import mitmdump; import sys; sys.exit(mitmdump(sys.argv[1:]))" "$@"',
    'mitmweb': 'exec /usr/bin/python3 -c "from mitmproxy.tools.main import mitmweb; import sys; sys.exit(mitmweb(sys.argv[1:]))" "$@"',
    'zap-daemon': 'export JAVA_HOME=/usr/lib/jvm/java-17-openjdk; export PATH=$JAVA_HOME/bin:$PATH; exec /opt/zap/zap.sh -daemon -host 127.0.0.1 -port 8082 -config api.key=hosares -config api.addrs.addr.name=localhost -config api.addrs.addr.regex=true "$@"',
}
for name, body in SHIMS.items():
    p = os.path.join(BUILD, 'usr/local/bin', name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', newline='\n') as f:
        f.write('#!/bin/sh\n' + body + '\n')
    os.chmod(p, 0o755)
print('[5] shim 创建完成')

# 6) 重新打包（regular 文件 + 记录的 symlink）
with tarfile.open(SRC, 'w:gz') as tf:
    for root, dirs, files in os.walk(BUILD):
        for name in dirs + files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, BUILD).replace(os.sep, '/')
            arc = './' + rel
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode):
                info = tarfile.TarInfo(arc)
                info.type = tarfile.SYMTYPE
                info.linkname = os.readlink(full)
                tf.addfile(info)
            else:
                tf.add(full, arcname=arc, recursive=False)
    for arc, link in SYMLINKS:
        info = tarfile.TarInfo('./' + arc)
        info.type = tarfile.SYMTYPE
        info.linkname = link
        tf.addfile(info)
print(f'[6] 新 rootfs.tar 已生成（含 {len(SYMLINKS)} 条 symlink，{os.path.getsize(SRC)//1024//1024} MB）')
