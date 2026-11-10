#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market.py —— 模型市场适配器（ModelScope / hf-mirror）。纯 Python 3 标准库。

API 形态均为真机/实测确认（2026-11）：
- ModelScope 搜索：PUT https://www.modelscope.cn/api/v1/dolphin/models
  body {"Name": q, "PageSize": n, "PageNumber": 1, "SortBy": "Default"}
  → Data.Model.Models[]（Path/Name/Downloads/TotalCount）
- ModelScope 文件列表：GET .../api/v1/models/{owner}/{name}/repo/files?Revision=master
  → Data.Files[]（Path/Size/Type=="blob"）
- ModelScope 下载：.../api/v1/models/{repo}/repo?Revision=master&FilePath={path}
  （302 → cdn-lfs-cn-*.modelscope.cn，支持 Range 续传）
- hf-mirror 同 huggingface.co 路径约定：
  搜索 GET /api/models?search=&limit= → 数组（author/id/downloads/likes）
  文件 GET /api/models/{repo}?blobs=true → siblings[].rfilename/size
  下载 GET /{repo}/resolve/main/{path}（Accept-Ranges: bytes）

统一输出条目：{"source", "repo", "file", "size", "quant"}（size 可为 None）。
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

MS_API = "https://www.modelscope.cn/api/v1"
HF_MIRROR = "https://hf-mirror.com"
DEFAULT_LIMIT = 15
FILES_LIMIT = 60

GGUF_RE = re.compile(r"\.gguf$", re.IGNORECASE)
# 从文件名提取量化后缀（Q4_K_M / IQ3_XS / F16 等），供前端展示
QUANT_RE = re.compile(
    r"-(I?Q\d_[A-Z0-9_]+|F(?:16|32)|BF16|FP16|FP32|MLX)_?",
    re.IGNORECASE)


class MarketError(Exception):
    pass


def _http_json(url, payload=None, method=None, timeout=15):
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method or "POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "llamacpp-fnos-manager"})
    else:
        req = urllib.request.Request(url, method=method,
                                     headers={"User-Agent": "llamacpp-fnos-manager"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise MarketError("%s 请求失败：%s" % (url.split("?")[0], exc))


def _quant_of(filename):
    m = QUANT_RE.search(filename)
    return m.group(1).upper() if m else None


# ---------- ModelScope ----------
def _ms_search(q, limit):
    body = {"Name": q or "", "PageSize": max(1, min(limit, 50)),
            "PageNumber": 1, "SortBy": "Default"}
    data = _http_json(MS_API + "/dolphin/models", payload=body, method="PUT")
    items = ((data.get("Data") or {}).get("Model") or {}).get("Models") or []
    out = []
    for it in items:
        path, name = it.get("Path") or "", it.get("Name") or ""
        if not path or not name:
            continue
        out.append({
            "source": "modelscope",
            "repo": "%s/%s" % (path, name),
            "title": it.get("ChineseName") or name,
            "downloads": it.get("Downloads") or 0,
            "stars": it.get("Stars") or 0,
            "gguf_hint": bool(it.get("Tags") and any(
                isinstance(t, str) and t.lower().find("gguf") >= 0 for t in it["Tags"])),
        })
    return out


def _ms_files(repo):
    url = "%s/models/%s/repo/files?Revision=master" % (MS_API, urllib.parse.quote(repo, safe="/"))
    data = _http_json(url)
    if data.get("Code") != 200:
        raise MarketError("ModelScope 返回 Code=%s" % data.get("Code"))
    files = (data.get("Data") or {}).get("Files") or []
    out = []
    for f in files:
        if f.get("Type") != "blob":
            continue
        path = f.get("Path") or ""
        if GGUF_RE.search(path):
            out.append({"source": "modelscope", "repo": repo, "file": path,
                        "size": f.get("Size"), "quant": _quant_of(path)})
    return out[:FILES_LIMIT]


def _ms_download_url(repo, path):
    return ("%s/models/%s/repo?Revision=master&FilePath=%s"
            % (MS_API, urllib.parse.quote(repo, safe="/"),
               urllib.parse.quote(path)))


# ---------- hf-mirror ----------
def _hf_search(q, limit):
    url = "%s/api/models?search=%s&limit=%d" % (
        HF_MIRROR, urllib.parse.quote(q or ""), max(1, min(limit, 50)))
    items = _http_json(url)
    if not isinstance(items, list):
        raise MarketError("hf-mirror 搜索返回格式异常")
    out = []
    for it in items:
        rid = it.get("id") or it.get("modelId") or ""
        if not rid or it.get("private"):
            continue
        tags = [str(t).lower() for t in (it.get("tags") or [])]
        sib = it.get("siblings") or []
        out.append({
            "source": "hf",
            "repo": rid,
            "title": rid,
            "downloads": it.get("downloads") or 0,
            "stars": it.get("likes") or 0,
            "gguf_hint": any(t.find("gguf") >= 0 for t in tags) or any(
                GGUF_RE.search(s.get("rfilename") or "") for s in sib),
        })
    return out


def _hf_files(repo):
    url = "%s/api/models/%s?blobs=true" % (HF_MIRROR, urllib.parse.quote(repo, safe="/"))
    data = _http_json(url)
    out = []
    for s in data.get("siblings") or []:
        path = s.get("rfilename") or ""
        if GGUF_RE.search(path):
            out.append({"source": "hf", "repo": repo, "file": path,
                        "size": s.get("size"), "quant": _quant_of(path)})
    return out[:FILES_LIMIT]


def _hf_download_url(repo, path):
    return "%s/%s/resolve/main/%s" % (
        HF_MIRROR, urllib.parse.quote(repo, safe="/"), urllib.parse.quote(path))


# ---------- 统一入口 ----------
SOURCES = ("modelscope", "hf")


def search(source, q, limit=DEFAULT_LIMIT):
    if source not in SOURCES:
        raise MarketError("未知市场源：%s" % source)
    items = _ms_search(q, limit) if source == "modelscope" else _hf_search(q, limit)
    # GGUF 仓库优先展示，其次按下载量
    items.sort(key=lambda x: (not x.get("gguf_hint"), -x.get("downloads", 0)))
    return items


def list_files(source, repo):
    if source not in SOURCES:
        raise MarketError("未知市场源：%s" % source)
    if not repo or "/" not in repo:
        raise MarketError("repo 格式应为 owner/name：%r" % repo)
    return _ms_files(repo) if source == "modelscope" else _hf_files(repo)


def download_url(source, repo, path):
    if source not in SOURCES:
        raise MarketError("未知市场源：%s" % source)
    if not repo or not path:
        raise MarketError("repo 与 file 均必填")
    return _ms_download_url(repo, path) if source == "modelscope" \
        else _hf_download_url(repo, path)
