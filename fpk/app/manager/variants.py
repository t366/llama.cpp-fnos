#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""variants.py —— llama.cpp 加速变体清单与按需安装。纯 Python 3 标准库。

资产沿用 GitHub releases/download/<tag>/<file> 路径约定；Gitea 镜像
（配置 mirror_base）路径完全一致，仅 base 不同（设计 §4.4）。
安装流程：下载 tar.gz（+cudart）→ 校验 tar 成员路径安全 → 解出
llama-server 与 lib* 到 bin/<variant>/ → chmod +x → --version 校验。
"""

import os
import shutil
import stat
import subprocess
import tarfile
import time
import urllib.parse

import downloader

# 与 scripts/vendor-binaries.py 的 DEFAULT_TAG 保持一致。
# 选 b12220（2026-11-09）：官方 ubuntu x64 CPU/Vulkan 资产的 CI 构建基线仍为
# ubuntu-22.04（GLIBC 2.35，兼容 fnOS/Debian 12 的 glibc 2.36）；其后的版本
# 切到 ubuntu-24.04 基线（GLIBC 2.38+），在 fnOS 上报 "GLIBC_2.38 not found"。
# 注意：官方 CUDA 资产自首次发布（b11000）起就基于 ubuntu-24.04 镜像，
# 全系不兼容 fnOS——CUDA 变体改用自建 22.04 基线资产（Gitea 镜像分发）。
PINNED_TAG = "b12220"
GG_BASE = "https://github.com/ggml-org/llama.cpp/releases/download/%s" % PINNED_TAG

# 变体 → 资产清单（os_part 按 GitHub 资产命名：x64 / arm64）
_ARCH_OS_PART = {"x86_64": "x64", "aarch64": "arm64"}
VARIANTS = {
    "cpu-x86_64":      {"assets": ("llama-{tag}-bin-ubuntu-x64.tar.gz",)},
    "cpu-aarch64":     {"assets": ("llama-{tag}-bin-ubuntu-arm64.tar.gz",)},
    "vulkan-x86_64":   {"assets": ("llama-{tag}-bin-ubuntu-vulkan-x64.tar.gz",)},
    "vulkan-aarch64":  {"assets": ("llama-{tag}-bin-ubuntu-vulkan-arm64.tar.gz",)},
    "cuda-12.8-x86_64": {
        "assets": ("llama-{tag}-bin-ubuntu-cuda-12.8-x64.tar.gz",
                   "cudart-llama-{tag}-bin-ubuntu-cuda-12.8-x64.tar.gz")},
    "cuda-13.5-x86_64": {
        "assets": ("llama-{tag}-bin-ubuntu-cuda-13.5-x64.tar.gz",
                   "cudart-llama-{tag}-bin-ubuntu-cuda-13.5-x64.tar.gz")},
}

VERIFY_TIMEOUT = 20


class VariantError(Exception):
    pass


def exists(name):
    return name in VARIANTS


def asset_names(name, tag=PINNED_TAG):
    spec = VARIANTS.get(name)
    if not spec:
        raise VariantError("未知变体：%s" % name)
    return [a.format(tag=tag) for a in spec["assets"]]


def installed(app_dir, data_dir):
    """已安装变体列表：检查 llama-server 存在即算（不做版本区分）。"""
    found = []
    for base in (os.path.join(data_dir, "bin"), os.path.join(app_dir, "bin")):
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for name in entries:
            if name in VARIANTS and os.path.isfile(
                    os.path.join(base, name, "llama-server")) and name not in found:
                found.append(name)
    return found


def urls_for(name, source, mirror_base):
    """变体资产下载 URL 列表。source: github | gitea。"""
    if source == "gitea":
        base = (mirror_base or "").rstrip("/")
        if not base or "://" not in base:
            raise VariantError("Gitea 镜像 base 未配置或非法（设置页 mirror_base）")
        base = base + "/releases/download/%s" % PINNED_TAG
    else:
        base = GG_BASE
    return ["%s/%s" % (base, urllib_quote(a)) for a in asset_names(name)]


def urllib_quote(path):
    return urllib.parse.quote(path)


def _extra_hosts(source, mirror_base):
    """变体下载的动态白名单：Gitea 镜像 host（github 白名单已内置）。"""
    hosts = []
    if source == "gitea" and mirror_base:
        try:
            from urllib.parse import urlparse
            host = (urlparse(mirror_base).hostname or "").lower()
            if host:
                hosts.append(host)
        except ValueError:
            pass
    return hosts


def _safe_extract(tgz, out_dir):
    """解出 llama-server 与全部 lib*/libcudart*；逐成员校验路径防穿越。"""
    wanted = ("llama-server",)
    n = 0
    with tarfile.open(tgz, "r:gz") as tf:
        for m in tf.getmembers():
            base = os.path.basename(m.name.rstrip("/"))
            hit = base in wanted or base.startswith("lib")
            if not hit or not (m.isreg() or m.issym() or m.islnk()):
                continue
            norm = os.path.normpath(m.name.replace("\\", "/"))
            if norm.startswith("/") or ".." in norm.split(os.sep):
                raise VariantError("tar 成员路径非法：%s" % m.name)
            dest = os.path.join(out_dir, base)
            if m.isreg():
                with tf.extractfile(m) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR
                         | stat.S_IXGRP | stat.S_IXOTH)
            elif m.issym():
                # Linux 数据卷保留符号链接原样（Windows vendor 时才需平替）
                if os.path.lexists(dest):
                    os.remove(dest)
                os.symlink(m.linkname, dest)
            else:  # 硬链接：tarfile 可直接取目标内容复制平替
                with tf.extractfile(m) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR
                         | stat.S_IXGRP | stat.S_IXOTH)
            n += 1
    return n


def install(name, app_dir, data_dir, source, mirror_base, progress=None, cancel=None):
    """下载并安装变体到 data_dir/bin/<name>/；返回 (ok, error, version)。

    progress(received, total, stage)：stage = 下载资产名 / 解压 / 校验。
    幂等：目标目录已有可执行 llama-server 则直接校验通过返回。
    """
    if not exists(name):
        return False, "未知变体：%s" % name, None
    out_dir = os.path.join(data_dir, "bin", name)
    os.makedirs(out_dir, exist_ok=True)
    if os.path.isfile(os.path.join(out_dir, "llama-server")):
        ok, err, ver = verify(out_dir)
        if ok:
            return True, None, ver

    urls = urls_for(name, source, mirror_base)
    extra = _extra_hosts(source, mirror_base)
    dl_dir = os.path.join(data_dir, "downloads")
    os.makedirs(dl_dir, exist_ok=True)
    try:
        for url in urls:
            asset = url.rsplit("/", 1)[1]
            tgz = os.path.join(dl_dir, asset)
            if not os.path.isfile(tgz):
                if progress:
                    progress(0, None, asset)
                downloader.download(
                    url, tgz, extra_host_suffixes=extra,
                    progress=(lambda r, t, a=asset: progress(r, t, a)) if progress else None,
                    cancel=cancel)
            if progress:
                progress(0, None, "解压 " + asset)
            _safe_extract(tgz, out_dir)
        if not os.path.isfile(os.path.join(out_dir, "llama-server")):
            return False, "解压后未找到 llama-server", None
        if progress:
            progress(0, None, "校验 --version")
        ok, err, ver = verify(out_dir)
        return ok, err, ver
    except (downloader.DownloadError, VariantError, OSError) as exc:
        return False, str(exc), None
    finally:
        # 清掉本次下载的 .part（成功与否均不残留半截 tar）
        for url in urls:
            part = os.path.join(dl_dir, url.rsplit("/", 1)[1] + ".part")
            if os.path.isfile(part):
                try:
                    os.remove(part)
                except OSError:
                    pass


def verify(out_dir):
    """LD_LIBRARY_PATH 指向变体目录运行 llama-server --version。"""
    exe = os.path.join(out_dir, "llama-server")
    if not os.path.isfile(exe):
        return False, "llama-server 不存在", None
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = out_dir + (
        ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    try:
        out = subprocess.run([exe, "--version"], capture_output=True,
                             text=True, timeout=VERIFY_TIMEOUT, env=env, cwd=out_dir)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "可执行校验失败：%s" % exc, None
    if out.returncode != 0:
        return False, ("--version 退出码 %d：%s"
                       % (out.returncode, (out.stderr or out.stdout or "")[:200])), None
    ver = (out.stdout or out.stderr or "").strip().splitlines()
    return True, None, (ver[0][:120] if ver else "unknown")
