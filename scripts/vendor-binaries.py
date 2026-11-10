#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 llama.cpp nightly 预编译 CPU 产物并 vendor 进 fpk 打包目录。

用法（开发机，任意平台）：
  python scripts/vendor-binaries.py                          # x86_64（tag 钉死 b12229）
  python scripts/vendor-binaries.py --arch aarch64           # ARM（M5）
  python scripts/vendor-binaries.py --base https://ghfast.top/https://github.com  # 走镜像

目录名与运行时变体名（manager variants.py / engine.py）保持一致：
cpu-x86_64 / cpu-aarch64。

符号链接平替：nightly 包内 lib*.so / lib*.so.0 为符号链接，Windows 上无法保留；
本脚本将链接目标实体复制一份并命名为链接名（Linux 动态加载器按文件名查找，行为等价）。
仅 vendor llama-server 与全部 lib*.so*（不含 llama-cli 等其余工具二进制）。
"""
import argparse
import shutil
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

DEFAULT_TAG = "b12229"


def extract_members(tf, wanted):
    """从已打开的 tar 对象提取想要的成员；返回 (实体成员表, 链接成员表)。"""
    by_name = {m.name: m for m in tf.getmembers()}

    def resolve_link(link_member):
        """把符号/硬链接解析到实体成员（最多 8 层）。"""
        m = link_member
        for _ in range(8):
            target = by_name.get(m.linkname)
            if target is None:
                # 相对链接目标：相对链接所在目录
                parent = str(PurePosixPath(m.name).parent)
                target = by_name.get(str(PurePosixPath(parent) / m.linkname))
            if target is None:
                return None
            if target.isreg():
                return target
            m = target
        return None

    out = {}
    for m in tf.getmembers():
        base = PurePosixPath(m.name).name
        if not wanted(base):
            continue
        if m.isreg():
            out[base] = m
        elif m.issym() or m.islnk():
            target = resolve_link(m)
            if target is None:
                print(f"  跳过（无法解析链接目标）: {m.name} -> {m.linkname}")
            else:
                out[base] = target  # 链接名 -> 目标实体（复制平替）
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arch", default="x86_64", choices=["x86_64", "aarch64"])
    ap.add_argument("--tag", default=DEFAULT_TAG)
    ap.add_argument("--base", default="https://github.com")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    os_part = {"x86_64": "x64", "aarch64": "arm64"}[args.arch]
    fname = f"llama-{args.tag}-bin-ubuntu-{os_part}.tar.gz"
    url = f"{args.base}/ggml-org/llama.cpp/releases/download/{args.tag}/{fname}"

    out_dir = root / "fpk" / "app" / "bin" / f"cpu-{args.arch}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = root / "build" / "vendor"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tgz = tmp_dir / fname

    if not (tgz.exists() and tgz.stat().st_size > 0):
        print(f"下载: {url}")
        urllib.request.urlretrieve(url, tgz)
    print(f"使用包: {tgz} ({tgz.stat().st_size // 1024 // 1024} MB)")

    def wanted(base):
        return base == "llama-server" or base.startswith("lib")

    with tarfile.open(tgz, "r:gz") as tf:
        members = extract_members(tf, wanted)
        if "llama-server" not in members:
            raise SystemExit("包内未找到 llama-server")
        for base, m in members.items():
            src = tf.extractfile(m)
            with open(out_dir / base, "wb") as out:
                shutil.copyfileobj(src, out)
    print(f"已 vendor {len(members)} 个文件到 {out_dir}")
    for p in sorted(out_dir.iterdir()):
        print(f"  {p.name}  {p.stat().st_size}")


if __name__ == "__main__":
    main()
