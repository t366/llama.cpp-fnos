#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""models.py —— 本地 GGUF 模型扫描与删除（M3）。纯 Python 3 标准库。

- 扫描根目录可配置（设置项 models_dir），空则用数据目录下 models/；
  递归扫描（跳过隐藏目录），支持把 GGUF 放进共享文件夹的子目录。
- 删除仅允许扫描根目录内的 .gguf 文件（realpath 防越界/软链逃逸）。
"""

import os


class ModelError(ValueError):
    """模型操作参数/路径非法。"""


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            return ("%d B" % int(n)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def resolve_models_dir(data_dir, setting):
    """空设置 → 数据目录下 models/（自动创建）；否则用设置值。"""
    s = (setting or "").strip()
    if s:
        return os.path.normpath(s)
    d = os.path.join(data_dir, "models")
    os.makedirs(d, exist_ok=True)
    return d


def scan(root):
    """递归扫描 root 下 .gguf，按修改时间倒序。
    返回 [{path, name, size, size_human, mtime}]；root 不存在返回空列表。"""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if not fn.lower().endswith(".gguf"):
                continue
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({
                "path": p,
                "name": fn,
                "size": st.st_size,
                "size_human": human_size(st.st_size),
                "mtime": int(st.st_mtime),
            })
    out.sort(key=lambda m: m["mtime"], reverse=True)
    return out


def delete_model(root, path):
    """安全删除：目标必须 realpath 落在扫描根目录内且为 .gguf 文件。"""
    if not path or not str(path).strip():
        raise ModelError("缺少 path 参数")
    real_root = os.path.realpath(root)
    target = os.path.realpath(str(path))
    if not (target.startswith(real_root + os.sep) and target != real_root):
        raise ModelError("仅允许删除扫描目录内的 .gguf 文件")
    if not target.lower().endswith(".gguf"):
        raise ModelError("仅允许删除 .gguf 文件：%s" % path)
    if not os.path.isfile(target):
        raise ModelError("文件不存在：%s" % path)
    os.remove(target)
    return target
