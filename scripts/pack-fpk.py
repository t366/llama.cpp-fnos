#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pack-fpk.py —— 纯 Python fpk 打包器（fnpack build 等效实现）。仅标准库。

背景：本机安全监控拦截新建 PE 二进制的留存与执行，官方 fnpack.exe 无法稳定
使用。fpk 格式已由官方 fnpack 1.2.3（windows-amd64）产物逆向验证，0.1.0~0.4.3
各版本均为该格式且真机安装正常，故以 Python 标准库复刻。

fpk 格式（gzip 压缩的 tar）：
  外层成员：app.tgz（app/ 目录整体内嵌包）、cmd/*（9 个生命周期脚本）、
            config/privilege、config/resource、ICON.PNG、ICON_256.PNG、
            manifest、wizard/*
  元数据约定（Windows 版 fnpack 不保留 Unix 权限，fnOS 安装器已验证接受）：
            目录 mode=777、文件 mode=666、uid/gid=0、uname/gname 空

校验（对应官方文档的打包检查项）：
  manifest 存在且含必要字段；config/privilege、config/resource 为合法 JSON；
  ICON.PNG / ICON_256.PNG、cmd/ 9 个脚本、app/、wizard/ 存在。

用法：python scripts\\pack-fpk.py --dir fpk --out fpk\\llamacpp.fpk
"""

import argparse
import json
import os
import sys
import tarfile

CMD_SCRIPTS = ("main", "install_init", "install_callback", "upgrade_init",
               "upgrade_callback", "uninstall_init", "uninstall_callback",
               "config_init", "config_callback")
MANIFEST_REQUIRED = ("appname", "version", "platform")


def _fail(msg):
    print("错误：%s" % msg, file=sys.stderr)
    sys.exit(1)


def check_structure(root):
    """fnpack build 预检查：必需文件/目录 + manifest 字段 + JSON 合法性。"""
    for p in ("manifest", "config/privilege", "config/resource",
              "ICON.PNG", "ICON_256.PNG", "app", "wizard"):
        if not os.path.exists(os.path.join(root, p.replace("/", os.sep))):
            _fail("缺少 %s" % p)
    for s in CMD_SCRIPTS:
        if not os.path.isfile(os.path.join(root, "cmd", s)):
            _fail("缺少 cmd/%s" % s)
    mf = {}
    with open(os.path.join(root, "manifest"), "r", encoding="utf-8") as fh:
        for line in fh:
            if "=" in line:
                k, v = line.split("=", 1)
                mf[k.strip()] = v.strip()
    for k in MANIFEST_REQUIRED:
        if not mf.get(k):
            _fail("manifest 缺少必要字段: %s" % k)
    for p in ("config/privilege", "config/resource"):
        try:
            with open(os.path.join(root, p.replace("/", os.sep)),
                      "r", encoding="utf-8") as fh:
                json.load(fh)
        except (OSError, ValueError) as exc:
            _fail("%s 不是合法 JSON：%s" % (p, exc))
    return mf


def _add(tar, arcname, path):
    """按 fnpack（Windows 版）元数据约定加入条目：目录 777、文件 666、uid/gid 0。"""
    ti = tarfile.TarInfo(arcname.replace("\\", "/").rstrip("/"))
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.mtime = int(os.stat(path).st_mtime)
    if os.path.isdir(path):
        ti.type = tarfile.DIRTYPE
        ti.mode = 0o777
        ti.size = 0
        tar.addfile(ti)
    else:
        ti.mode = 0o666
        ti.size = os.path.getsize(path)
        with open(path, "rb") as fh:
            tar.addfile(ti, fh)


def make_app_tgz(root, out_path, platform=None):
    """fpk/app/ 内容 → app.tgz。成员不带 app/ 前缀（以 app/ 为根，fnOS
    安装器直接解包到应用目录）；排除 __pycache__ 与 *.pyc 编译产物。

    platform（manifest 的 x86/arm）非空时，bin/ 下他架构二进制目录一并剔除：
    fpk 是单架构包，vendor 后 app/bin/ 可能同时存在 cpu-x86_64 与 cpu-aarch64。
    """
    app_dir = os.path.join(root, "app")
    # manifest platform → 打包时剔除的 bin 子目录后缀
    skip_suffix = {"x86": "-aarch64", "arm": "-x86_64"}.get(platform)
    with tarfile.open(out_path, "w:gz") as tar:
        def walk(dir_path, prefix):
            for name in sorted(os.listdir(dir_path)):
                if name == "__pycache__" or name.endswith(".pyc"):
                    continue
                if skip_suffix and prefix == "bin/" and name.endswith(skip_suffix):
                    print("  剔除他架构二进制目录: bin/%s" % name)
                    continue
                full = os.path.join(dir_path, name)
                _add(tar, prefix + name, full)
                if os.path.isdir(full):
                    walk(full, prefix + name + "/")
        walk(app_dir, "")


def make_fpk(root, out_path, app_tgz):
    with tarfile.open(out_path, "w:gz") as tar:
        _add(tar, "app.tgz", app_tgz)
        _add(tar, "cmd", os.path.join(root, "cmd"))
        for s in CMD_SCRIPTS:
            _add(tar, "cmd/" + s, os.path.join(root, "cmd", s))
        _add(tar, "config", os.path.join(root, "config"))
        _add(tar, "config/privilege", os.path.join(root, "config", "privilege"))
        _add(tar, "config/resource", os.path.join(root, "config", "resource"))
        _add(tar, "ICON.PNG", os.path.join(root, "ICON.PNG"))
        _add(tar, "ICON_256.PNG", os.path.join(root, "ICON_256.PNG"))
        _add(tar, "manifest", os.path.join(root, "manifest"))
        _add(tar, "wizard", os.path.join(root, "wizard"))
        for name in sorted(os.listdir(os.path.join(root, "wizard"))):
            _add(tar, "wizard/" + name, os.path.join(root, "wizard", name))


def main():
    ap = argparse.ArgumentParser(description="纯 Python fpk 打包器（fnpack build 等效）")
    ap.add_argument("--dir", required=True, help="fpk 项目目录")
    ap.add_argument("--out", required=True, help="输出 .fpk 路径")
    args = ap.parse_args()
    mf = check_structure(args.dir)
    app_tgz = args.out + ".tmp.app.tgz"
    make_app_tgz(args.dir, app_tgz, platform=mf.get("platform"))
    try:
        make_fpk(args.dir, args.out, app_tgz)
    finally:
        if os.path.exists(app_tgz):
            os.remove(app_tgz)
    print("打包完成：%s（%s %s）" % (args.out, mf["appname"], mf["version"]))


if __name__ == "__main__":
    main()
