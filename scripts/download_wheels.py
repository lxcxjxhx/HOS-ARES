# -*- coding: utf-8 -*-
"""
HOS-ARES wheel 下载器：在 Windows 上下载 Alpine Linux 兼容的 Python wheels。
这些 wheels 会被打包进 APK，首次启动时解压到 site-packages，实现完全离线。

用法: python download_wheels.py [--python-version 3.12]
输出: wheels/ 目录下所有 .whl 文件

关键点:
  - --platform manylinux2014_x86_64: Alpine x86_64 兼容
  - --python-version: 与 Alpine 内 Python 版本一致
  - --only-binary :all: 强制下载 wheel（不用 sdist）
  - --no-deps: 精确控制依赖，不自动拉取传递依赖
"""
import os, sys, subprocess, argparse, glob, shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
WHEELS = os.path.join(ROOT, 'wheels')
REQ_DIR = os.path.join(ROOT, '..', 'app', 'ares-rootfs', 'requirements')

# 合并所有 requirements 到一个列表（去重）
PACKAGES = [
    # Argus
    'pyyaml',
    'argus-languages',
    # RepoAudit
    'openai',
    'anthropic',
    'tqdm',
    # Strix
    'litellm',
    'pydantic',
    'pydantic-settings',
    'requests',
    'rich',
    'pygments',
    'jinja2',
    'reportlab',
    # Reasonix
    'deepseek-reasonix',
    # 通用传递依赖（部分纯 Python，部分带 C 扩展）
    'cryptography',
    'charset-normalizer',
    'idna',
    'certifi',
    'urllib3',
    'click',
    'markdown-it-py',
    'mdurl',
    'pygments',
    'colorama',
    'markdown',
    'frozenlist',
    'multidict',
    'yarl',
    'aiohttp',
    'attrs',
    'distro',
    'httpx',
    'anyio',
    'starlette',
    'h11',
    'httpcore',
    'sniffio',
    'typing-extensions',
    'jsonpatch',
    'jsonpointer',
    'tiktoken',
    'regex',
    'sentencepiece',
    'protobuf',
    'httptools',
    'python-dotenv',
    'pillow',
    'pypdf',
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--python-version', default='3.20', help='Alpine Python version')
    ap.add_argument('--platform', default='manylinux_2_31_arm64', help='Target platform (aarch64 for Android)')
    ap.add_argument('--output', default=WHEELS, help='Output directory')
    ap.add_argument('--skip-download', action='store_true', help='Skip download if wheels exist')
    args = ap.parse_args()

    py_ver = args.python_version
    platform = args.platform
    output = args.output

    os.makedirs(output, exist_ok=True)

    existing = glob.glob(os.path.join(output, '*.whl'))
    if args.skip_download and existing:
        print(f'[skip-download] 已有 {len(existing)} 个 wheel，跳过下载')
        return 0

    # 清空旧的
    for f in existing:
        os.remove(f)

    # 去重
    pkgs = sorted(set(PACKAGES))
    print(f'准备下载 {len(pkgs)} 个包的 wheels...')
    print(f'  Python: {py_ver}, Platform: {platform}')

    cmd = [
        sys.executable, '-m', 'pip', 'download',
        '--dest', output,
        '--python-version', py_ver,
        '--platform', platform,
        '--only-binary', ':all:',
        '--no-deps',
        '--no-build-isolation',
    ] + pkgs

    print(f'\n执行: {" ".join(cmd)}\n')
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print('下载部分包失败（可能是某些包无对应平台 wheel）：')
        # 尝试逐个下载，跳过失败的
        failed = []
        for pkg in pkgs:
            r = subprocess.run(
                [sys.executable, '-m', 'pip', 'download',
                 '--dest', output,
                 '--python-version', py_ver,
                 '--platform', platform,
                 '--only-binary', ':all:',
                 '--no-deps',
                 '--no-build-isolation',
                 pkg],
                capture_output=True, text=True
            )
            if r.returncode != 0:
                failed.append(pkg)
                print(f'  ✗ {pkg}: {r.stderr.strip().split(chr(10))[-1]}')
            else:
                print(f'  ✓ {pkg}')

        if failed:
            print(f'\n{len(failed)} 个包无 Linux wheel（将在 Docker 构建中处理）')
            if len(failed) == len(pkgs):
                print('所有包都下载失败，可能是 pip 版本过旧，建议升级 pip:')
                print('  python -m pip install --upgrade pip')
                return 1

    # 统计
    wheels = glob.glob(os.path.join(output, '*.whl'))
    total_size = sum(os.path.getsize(w) for w in wheels) / 1024 / 1024
    print(f'\n下载完成: {len(wheels)} 个 wheels, {total_size:.1f} MB')

    # 列出
    for w in sorted(wheels):
        name = os.path.basename(w)
        size = os.path.getsize(w) / 1024
        print(f'  {name} ({size:.0f} KB)')

    return 0

if __name__ == '__main__':
    sys.exit(main())