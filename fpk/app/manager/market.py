#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market.py —— 模型市场适配器（ModelScope / hf-mirror）。纯 Python 3 标准库。

API 形态均为真机/实测确认（2026-11）：
- ModelScope 搜索：PUT https://www.modelscope.cn/api/v1/dolphin/models
  body {"Name": q, "PageSize": n, "PageNumber": 1, "SortBy": "Default"}
  → Data.Model.Models[]（Path/Name/Downloads/TotalCount）
- ModelScope 文件列表：GET .../api/v1/models/{owner}/{name}/repo/files?Revision=master
  → Data.Files[]（Path/Size/Type=="blob"）
- ModelScope 仓库详情：GET .../api/v1/models/{owner}/{name}
  → Data（ChineseName/Downloads/Stars/LastUpdatedTime(unix 秒)/License/
    Tags/Tasks[].Name/ReadMeContent —— README 原文直接内含，无需二次拉取）
- ModelScope 下载：.../api/v1/models/{repo}/repo?Revision=master&FilePath={path}
  （302 → cdn-lfs-cn-*.modelscope.cn，支持 Range 续传）
- hf-mirror 同 huggingface.co 路径约定：
  搜索 GET /api/models?search=&limit= → 数组（author/id/downloads/likes）
  详情 GET /api/models/{repo}?blobs=true
    → lastModified(ISO)/downloads/likes/tags/pipeline_tag/siblings
  README GET /{repo}/raw/main/README.md → 原文（带 YAML frontmatter，展示前剥离）
  下载 GET /{repo}/resolve/main/{path}（Accept-Ranges: bytes）
- 空关键词搜索：ModelScope 可用（Default 排序即推荐列表）；
  hf-mirror 返回新建仓库不适合做推荐，故默认源为 ModelScope。

统一输出条目：{"source", "repo", "file", "size", "quant"}（size 可为 None）。
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MS_API = "https://www.modelscope.cn/api/v1"
HF_MIRROR = "https://hf-mirror.com"
DEFAULT_LIMIT = 15
FILES_LIMIT = 60
# 推荐列表（LM Studio 式"为本机推荐"）：候选池与探测并发
RECOMMEND_QUERY = "GGUF"
REC_POOL = 40
REC_CONCURRENCY = 8
RAM_OVERHEAD_MIB = 1536   # 运行余量（MiB），与前端 ramTag 同口径

GGUF_RE = re.compile(r"\.gguf$", re.IGNORECASE)
# 从文件名提取量化后缀（Q4_K_M / IQ3_XS / F16 等），供前端展示
QUANT_RE = re.compile(
    r"-(I?Q\d_[A-Z0-9_]+|F(?:16|32)|BF16|FP16|FP32|MLX)_?",
    re.IGNORECASE)
# 从名称提取参数规模（0.6B / 7B / 8x7B / 72B…），供 LM Studio 式卡片展示
PARAM_RE = re.compile(
    r"(\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?)B(?![A-Za-z0-9])", re.IGNORECASE)
# README 展示截断长度
README_LIMIT = 12000
# YAML frontmatter（hf raw README 开头的元数据块），展示前剥离
FM_RE = re.compile(r"\A-{3,}\s*\n.*?\n-{3,}\s*\n?", re.DOTALL)
# 仓库 tags → 能力标签分组（按顺序取首个命中，每仓最多展示 6 个）
CAP_GROUPS = (
    ("vision", ("vision", "image-text-to-text", "multimodal", "any-to-any")),
    ("tools", ("tool-use", "tools", "agents", "agent")),
    ("reasoning", ("reasoning",)),
    ("chat", ("conversational", "chat")),
    ("embedding", ("embedding", "feature-extraction", "sentence-similarity",
                   "reranker")),
    ("t2i", ("text-to-image", "text2image", "image-generation")),
    ("speech", ("automatic-speech-recognition", "text-to-speech", "audio",
                "asr")),
    ("textgen", ("text-generation",)),
)
CAP_LABELS = {"vision": "视觉", "tools": "工具调用", "reasoning": "推理",
              "chat": "对话", "embedding": "向量", "t2i": "文生图",
              "speech": "语音", "textgen": "文本生成"}
# tags 里的基础设施/元信息前缀，不参与能力匹配
TAG_SKIP_PREFIX = ("base_model:", "license:", "region:", "deploy:", "arxiv:",
                   "endpoints", "transformers", "safetensors",
                   "text-generation-inference", "dataset:")


def _params_of(text):
    """提取参数规模标签（如 0.6B / 7B / 8x7B）；无匹配返回 None。"""
    m = PARAM_RE.search(text or "")
    if not m:
        return None
    val = m.group(1).upper()
    if "X" in val:
        return val.replace("X", "x") + "B"
    return val + "B"


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


def _strip_frontmatter(text):
    """剥离 README 开头的 YAML frontmatter 元数据块。"""
    return FM_RE.sub("", text or "", count=1).strip()


def _clip_readme(text):
    text = (text or "").strip()
    if not text:
        return None
    if len(text) > README_LIMIT:
        return text[:README_LIMIT] + "\n\n…（内容过长已截断，完整版见仓库主页）"
    return text


def _iso_to_unix(s):
    """hf lastModified（ISO 8601）→ unix 秒；解析失败返回 None。"""
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", str(s or ""))
    if not m:
        return None
    try:
        import calendar
        from datetime import datetime
        dt = datetime.strptime(m.group(1) + " " + m.group(2),
                               "%Y-%m-%d %H:%M:%S")
        return int(calendar.timegm(dt.timetuple()))
    except ValueError:
        return None


def _capabilities(tags):
    """tags → 能力标签列表 [{"key","label"}]；忽略基础设施类 tag。"""
    tset = set()
    for t in tags or []:
        t = str(t).strip().lower()
        if not t or t.startswith(TAG_SKIP_PREFIX):
            continue
        tset.add(t)
    out = []
    for key, names in CAP_GROUPS:
        if tset & set(names):
            out.append({"key": key, "label": CAP_LABELS[key]})
    return out[:6]


def _license_of(tags):
    """从 hf tags（license:xxx）提取许可证名；无则 None。"""
    for t in tags or []:
        t = str(t).strip().lower()
        if t.startswith("license:"):
            return t.split(":", 1)[1] or None
    return None


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
            "params": _params_of(name) or _params_of(path),
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
                        "size": f.get("Size"), "quant": _quant_of(path),
                        "params": _params_of(path)})
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
            "params": _params_of(rid),
            "gguf_hint": any(t.find("gguf") >= 0 for t in tags) or any(
                GGUF_RE.search(s.get("rfilename") or "") for s in sib),
        })
    return out


def _hf_files(repo):
    url = "%s/api/models/%s?blobs=true" % (HF_MIRROR, urllib.parse.quote(repo, safe="/"))
    data = _http_json(url)
    return _hf_files_from(repo, data)


def _hf_files_from(repo, data):
    """从已拉取的 ?blobs=true 详情响应中提取 GGUF 文件（避免二次请求）。"""
    out = []
    for s in data.get("siblings") or []:
        path = s.get("rfilename") or ""
        if GGUF_RE.search(path):
            out.append({"source": "hf", "repo": repo, "file": path,
                        "size": s.get("size"), "quant": _quant_of(path),
                        "params": _params_of(path)})
    return out[:FILES_LIMIT]


def _hf_readme(repo):
    """拉取 hf raw README 原文；失败返回 None（不阻断详情）。"""
    url = "%s/%s/raw/main/README.md" % (
        HF_MIRROR, urllib.parse.quote(repo, safe="/"))
    req = urllib.request.Request(url, headers={"User-Agent": "llamacpp-fnos-manager"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return None


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


def recommend(avail_mib, limit=DEFAULT_LIMIT):
    """按本机配置推荐 GGUF 模型（LM Studio 式"为本机推荐"）。

    流程：搜索 GGUF 候选池 → 逐仓拉取文件列表（并发探测）→ 只保留真实含
    .gguf 文件的仓库 → 以"最小完整档位"（分片 GGUF 按完整模型求和）预估
    内存占用，剔除本机跑不动的仓库 → 适配度优先、下载量次之排序。
    avail_mib：可用内存 MiB（物理内存 + 加速变体下的显存）；0/None 时
    不做内存过滤。条目在搜索条目之上追加：
    min_file / min_size / min_quant / fit("ok"|"tight"|None) / pct。
    """
    pool = _ms_search(RECOMMEND_QUERY, REC_POOL)
    split_re = re.compile(r"-\d{4,5}-of-\d{4,5}\.gguf$", re.IGNORECASE)
    # 辅助 GGUF（mmproj 视觉投影 / imatrix 校准矩阵 / mtp、draft 投机解码
    # 模块）远小于或非独立模型本体，不剔除会把 27B 模型误判成十几 MB
    aux_re = re.compile(r"(?:^|[/\\])(?:mmproj|imatrix|mtp|draft)[^/\\]*\.gguf$",
                        re.IGNORECASE)

    def probe(it):
        try:
            files = _ms_files(it["repo"])
        except MarketError:
            return None
        if not files:
            return None              # 仓库实际不含 .gguf → 不展示
        groups = {}                  # 分片（-00001-of-00003）按完整模型分组求和
        for f in files:
            if aux_re.search(f["file"]):
                continue
            key = split_re.sub(".gguf", f["file"])
            g = groups.setdefault(key, {"size": 0, "quant": f.get("quant")})
            g["size"] += f.get("size") or 0
        if not groups:
            return None              # 只有辅助文件、没有模型本体 → 不展示
        key, best = min(groups.items(), key=lambda kv: kv[1]["size"])
        item = dict(it)
        item["min_file"] = key
        item["min_size"] = best["size"]
        item["min_quant"] = best["quant"]
        if avail_mib:
            required = best["size"] / 1048576 + RAM_OVERHEAD_MIB
            pct = required / avail_mib
            if pct > 1.0:
                return None          # 最轻档位都超内存预算 → 本机跑不动
            item["fit"] = "ok" if pct < 0.8 else "tight"
            item["pct"] = round(pct, 3)
        else:
            item["fit"] = None
            item["pct"] = None
        return item

    with ThreadPoolExecutor(max_workers=REC_CONCURRENCY) as ex:
        futs = [ex.submit(probe, it) for it in pool]
        out = [r for f in futs for r in [f.result()] if r]
    # 适配度优先（可运行在前），同档按下载量
    out.sort(key=lambda x: (x.get("fit") != "ok", -(x.get("downloads") or 0)))
    return out[:limit]


def list_files(source, repo):
    if source not in SOURCES:
        raise MarketError("未知市场源：%s" % source)
    if not repo or "/" not in repo:
        raise MarketError("repo 格式应为 owner/name：%r" % repo)
    return _ms_files(repo) if source == "modelscope" else _hf_files(repo)


def _ms_detail(repo):
    url = "%s/models/%s" % (MS_API, urllib.parse.quote(repo, safe="/"))
    data = _http_json(url)
    if data.get("Code") != 200:
        raise MarketError("ModelScope 返回 Code=%s" % data.get("Code"))
    return data.get("Data") or {}


def repo_detail(source, repo):
    """仓库详情面板数据：元数据 + 能力标签 + README 摘要 + GGUF 文件列表。

    统一输出：{"source","repo","title","downloads","stars","last_modified",
              "license","params","gguf","capabilities","readme","files"}
    last_modified 为 unix 秒（可能 None）；readme 为截断文本（可能 None）。
    """
    if source not in SOURCES:
        raise MarketError("未知市场源：%s" % source)
    if not repo or "/" not in repo:
        raise MarketError("repo 格式应为 owner/name：%r" % repo)

    if source == "modelscope":
        d = _ms_detail(repo)
        name = d.get("Name") or repo
        tags = [str(t) for t in (d.get("Tags") or [])]
        for t in d.get("Tasks") or []:
            n = (t or {}).get("Name")
            if n:
                tags.append(n)
        last = d.get("LastUpdatedTime")
        if not isinstance(last, (int, float)) or last <= 0:
            last = None
        return {
            "source": "modelscope", "repo": repo,
            "title": d.get("ChineseName") or name,
            "downloads": d.get("Downloads") or 0,
            "stars": d.get("Stars") or 0,
            "last_modified": last,
            "license": d.get("License") or None,
            "params": _params_of(name) or _params_of(repo),
            "gguf": any(t.lower() == "gguf" for t in tags),
            "capabilities": _capabilities(tags),
            "readme": _clip_readme(_strip_frontmatter(d.get("ReadMeContent"))),
            "files": _ms_files(repo),
        }

    d = _http_json("%s/api/models/%s?blobs=true"
                   % (HF_MIRROR, urllib.parse.quote(repo, safe="/")))
    tags = [str(t) for t in (d.get("tags") or [])]
    if d.get("pipeline_tag"):
        tags.append(d["pipeline_tag"])
    sib_gguf = any(GGUF_RE.search(s.get("rfilename") or "")
                   for s in (d.get("siblings") or []))
    return {
        "source": "hf", "repo": repo,
        "title": d.get("modelId") or d.get("id") or repo,
        "downloads": d.get("downloads") or 0,
        "stars": d.get("likes") or 0,
        "last_modified": _iso_to_unix(d.get("lastModified")),
        "license": _license_of(tags),
        "params": _params_of(d.get("modelId") or d.get("id") or repo),
        "gguf": "gguf" in [t.lower() for t in tags] or sib_gguf,
        "capabilities": _capabilities(tags),
        "readme": _clip_readme(_strip_frontmatter(_hf_readme(repo))),
        "files": _hf_files_from(repo, d),
    }


def download_url(source, repo, path):
    if source not in SOURCES:
        raise MarketError("未知市场源：%s" % source)
    if not repo or not path:
        raise MarketError("repo 与 file 均必填")
    return _ms_download_url(repo, path) if source == "modelscope" \
        else _hf_download_url(repo, path)
