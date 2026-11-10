#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hardware.py —— 硬件检测与加速变体推荐。纯 Python 3 标准库。

检测项（全部容错，任何一步失败不影响整体）：
- 架构：platform.machine() → x86_64 / aarch64
- NVIDIA：nvidia-smi 读型号/驱动/显存；驱动主版本对照内置 CUDA 支持表
  （CUDA 13.x 需 >=580，CUDA 12.x 需 >=525，均为 Linux 官方最低驱动）
- Vulkan：/usr/share/vulkan/icd.d/*.json 存在非空，或 vulkaninfo 可执行
推荐顺序：cuda-13.5 > cuda-12.8 > vulkan > cpu（ARM 不做 CUDA，见设计 §4.3）。
"""

import glob
import os
import platform
import re
import shutil
import subprocess

# 驱动主版本 → 支持的 CUDA 变体档位（从高到低取第一个满足的）
CUDA_DRIVER_TABLE = (
    (580, "cuda-13.5"),
    (525, "cuda-12.8"),
)
CUDA_VARIANTS = ("x86_64",)  # ARM 不做 CUDA（设计 §4.3）

SMI_TIMEOUT = 10  # nvidia-smi 超时秒数


def arch():
    m = (platform.machine() or "").lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    return m or "unknown"


def _detect_nvidia():
    """返回 GPU 信息 dict 或 None。失败一律静默回退。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=SMI_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    # 多卡取第一行；字段可能带引号与逗号（部分驱动型号名含逗号）
    first = out.stdout.strip().splitlines()[0]
    parts = [p.strip().strip('"') for p in first.split(",")]
    if len(parts) < 3:
        return None
    name, driver, mem = parts[0], parts[1], parts[2]
    m = re.search(r"\d+", driver)
    driver_major = int(m.group()) if m else 0
    try:
        mem_mib = int(float(mem))
    except ValueError:
        mem_mib = None
    return {"name": name, "driver": driver, "driver_major": driver_major,
            "memory_mib": mem_mib}


def _detect_vulkan():
    """ICD 文件存在非空，或 vulkaninfo 可执行，即认为 Vulkan 栈可用。"""
    try:
        for path in glob.glob("/usr/share/vulkan/icd.d/*.json"):
            try:
                if os.path.getsize(path) > 0:
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return shutil.which("vulkaninfo") is not None


def cuda_variant_for(gpu, machine):
    """按驱动主版本给出可用 CUDA 变体名；不满足/非 x86 返回 None。"""
    if gpu is None or machine not in CUDA_VARIANTS:
        return None
    for min_driver, variant in CUDA_DRIVER_TABLE:
        if gpu["driver_major"] >= min_driver:
            return "%s-%s" % (variant, machine)
    return None


def detect():
    """完整检测；返回可 JSON 化的 dict（ hardware 摘要 + 变体建议）。"""
    machine = arch()
    gpu = _detect_nvidia()
    vulkan = _detect_vulkan()

    variants_available = ["cpu-%s" % machine]
    if vulkan:
        variants_available.append("vulkan-%s" % machine)
    # cuda_variant_for 按内置表择高，仅返回一个最优 CUDA 变体
    cuda_v = cuda_variant_for(gpu, machine)
    if cuda_v:
        variants_available.insert(0, cuda_v)
    recommended = cuda_v or ("vulkan-%s" % machine if vulkan else "cpu-%s" % machine)
    return {
        "arch": machine,
        "gpu": gpu,
        "vulkan": vulkan,
        "cuda_recommended": cuda_v,
        "variants_available": variants_available,
        "recommended": recommended,
    }


def summary_text(info):
    """一行人类可读摘要，用于状态页与日志。"""
    if info.get("gpu"):
        g = info["gpu"]
        mem = ("%d MiB" % g["memory_mib"]) if g.get("memory_mib") else "未知显存"
        return "NVIDIA %s（驱动 %s，%s）" % (g["name"], g["driver"], mem)
    if info.get("vulkan"):
        return "无 NVIDIA 独显，检测到 Vulkan 支持"
    return "未检测到 GPU，使用 CPU 变体"
