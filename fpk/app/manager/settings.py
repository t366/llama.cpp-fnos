#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""settings.py —— 配置持久化（config.json，位于数据目录）。纯 Python 3 标准库。

字段均为 llama-server 启动参数或 manager 行为开关；
未知字段读取时忽略、写入时拒绝合并，保证向前兼容。
"""

import copy
import json
import os
import threading

# 默认值（首次启动或 config.json 损坏时使用）
DEFAULTS = {
    "inference_port": 8080,   # OpenAI 兼容 API 监听端口
    "bind": "0.0.0.0",        # 监听地址；127.0.0.1 = 仅本机可用
    "ctx_size": 4096,         # 上下文长度（--ctx-size）
    "threads": 0,             # 计算线程数；0 = 交由 llama.cpp 自动
    "ngl": 0,                 # GPU offload 层数（--n-gpu-layers；CPU 变体保持 0）
    "api_key": "",            # 为空则不启用鉴权（llama-server --api-key）
    "model": "",              # 当前 GGUF 模型绝对路径
    "mode": "chat",           # 运行模式：chat = 对话模型；embedding = 向量检索（--embedding）
    "mmproj_model": "",       # 多模态投影 GGUF 路径（--mmproj）；空 = 不加载
    "variant": "",            # 引擎变体目录名；空 = 自动（硬件检测推荐）
    "auto_restart": True,     # 崩溃自动重启
    "models_dir": "",         # 模型扫描目录；空 = 数据目录下 models/
    "download_source": "github",   # 变体资产源：github | gitea（镜像）
    "mirror_base": "",        # Gitea 镜像 base（如 http://192.168.0.114:3000/T366/llama.cpp-fnos）
    "market_source": "modelscope",  # 模型市场默认源：modelscope | hf
}

# 允许写入的字段与类型
_TYPES = {
    "inference_port": int, "bind": str, "ctx_size": int, "threads": int,
    "ngl": int, "api_key": str, "model": str, "mode": str,
    "mmproj_model": str, "variant": str,
    "auto_restart": bool, "models_dir": str,
    "download_source": str, "mirror_base": str, "market_source": str,
}

# 整数型字段取值范围
_RANGE_CHECKS = (
    ("inference_port", 1024, 65535),
    ("ctx_size", 256, 1 << 20),
    ("threads", 0, 256),
    ("ngl", 0, 999),
)

_BIND_CHOICES = ("0.0.0.0", "127.0.0.1")
_MODE_CHOICES = ("chat", "embedding")


class Settings:
    """线程安全的配置存储；写入走「临时文件 + os.replace」保证原子性。"""

    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "config.json")
        os.makedirs(data_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if not isinstance(stored, dict):
                stored = {}
        except (OSError, ValueError):
            stored = {}
        merged = dict(DEFAULTS)
        for key, typ in _TYPES.items():
            if key in stored and isinstance(stored[key], typ):
                merged[key] = stored[key]
        with self._lock:
            self._data = merged

    def get(self):
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, patch):
        """校验并合并写入；返回 (ok, error_message)。"""
        if not isinstance(patch, dict):
            return False, "请求体必须是 JSON 对象"
        clean = {}
        for key, value in patch.items():
            if key not in _TYPES:
                continue  # 未知字段忽略，不报错（前端多发的开关字段）
            typ = _TYPES[key]
            # bool 是 int 的子类，需先排除，避免 True 被当作 1 写入数值字段
            if isinstance(value, bool) and typ is int:
                return False, "%s 必须是整数" % key
            if not isinstance(value, typ):
                return False, "%s 类型应为 %s" % (key, typ.__name__)
            clean[key] = value
        for key, lo, hi in _RANGE_CHECKS:
            if key in clean and not lo <= clean[key] <= hi:
                return False, "%s 需在 %d~%d 之间" % (key, lo, hi)
        if "bind" in clean and clean["bind"] not in _BIND_CHOICES:
            return False, "bind 仅支持 0.0.0.0 或 127.0.0.1"
        if "mode" in clean and clean["mode"] not in _MODE_CHOICES:
            return False, "mode 仅支持 chat 或 embedding"
        if "mmproj_model" in clean and clean["mmproj_model"]:
            if not clean["mmproj_model"].lower().endswith(".gguf"):
                return False, "mmproj_model 必须是 .gguf 文件路径（可留空）"
        if "download_source" in clean and clean["download_source"] not in ("github", "gitea"):
            return False, "download_source 仅支持 github 或 gitea"
        if "market_source" in clean and clean["market_source"] not in ("modelscope", "hf"):
            return False, "market_source 仅支持 modelscope 或 hf"
        if "mirror_base" in clean and clean["mirror_base"]:
            if not clean["mirror_base"].lower().startswith(("http://", "https://")):
                return False, "mirror_base 需以 http:// 或 https:// 开头"
        with self._lock:
            self._data.update(clean)
            self._save_locked()
        return True, None

    # 供引擎在持有锁的语境外安全读取单个字段
    def value(self, key):
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key))

    def _save_locked(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, self.path)
