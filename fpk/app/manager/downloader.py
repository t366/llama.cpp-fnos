#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""downloader.py —— HTTPS 断点续传下载器。纯 Python 3 标准库。

设计借鉴 huggingface_hub 的 hf_hub_download（Apache-2.0）核心思路：
- 临时文件 <dest>.part；已有 .part 则带 Range: bytes=<size>- 续传
- 服务器 206 → 追加续传；200（不支持 Range 或源文件已变）→ 从头重写
- 完成后 fsync 并原子改名到目标路径

安全（设计 §4.6）：
- 仅允许 https（Gitea 镜像可为 http 内网地址，显式放行）
- 首跳与每一跳重定向的 host 都必须命中白名单（精确或后缀匹配）
- 白名单默认：github.com / objects.githubusercontent.com / modelscope.cn 后缀
  （覆盖 cdn-lfs-cn-1.modelscope.cn 等下载 CDN）/ hf-mirror.com / huggingface.co，
  外加运行期配置的 Gitea 镜像 base host
"""

import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

CHUNK = 256 * 1024
PROGRESS_INTERVAL = 0.5   # 进度回调节流（秒），CGI 轮询约束 ≥1s
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 30

# host 白名单：精确匹配或以其为后缀结尾（均小写、不带端口）
DEFAULT_HOST_SUFFIXES = (
    "github.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
    "release-assets.githubusercontent.com",
    "modelscope.cn",          # 后缀覆盖 www. 与 cdn-lfs-cn-*.modelscope.cn
    "hf-mirror.com",
    "huggingface.co",
    "cas-bridge.xethub.hf.co",
    "cas-server.xethub.hf.co",
)


class DownloadError(Exception):
    pass


def _host_of(url):
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _host_allowed(host, extra_suffixes):
    """DEFAULT 白名单恒生效，extra_suffixes 为运行期追加（如 Gitea 镜像 host）。

    注意：extra 是"追加"而非"替换"——0.4.4 及以前本函数只检查 extra，
    变体下载（github 源无 extra）因此必然被拒（真机 0.4.4 实测报错）。
    """
    if not host:
        return False
    for suffix in tuple(DEFAULT_HOST_SUFFIXES) + tuple(extra_suffixes or ()):
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """重定向每一跳都校验白名单，防止被 30x 引到白名单外域名。"""

    def __init__(self, extra_suffixes):
        self._extra = extra_suffixes

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = _host_of(newurl)
        if not _host_allowed(host, self._extra):
            raise DownloadError("重定向目标不在白名单内：%s" % host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener(extra_suffixes):
    return urllib.request.build_opener(_SafeRedirect(extra_suffixes))


def validate_url(url, extra_suffixes):
    """下载前静态校验；不合法抛 DownloadError。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https":
        pass
    elif parsed.scheme == "http":
        # http 仅限内网 Gitea 镜像（IP 形式）；公网域名一律要求 https
        host = _host_of(url)
        if not _host_allowed(host, extra_suffixes):
            raise DownloadError("下载地址不在白名单内：%s" % host)
    else:
        raise DownloadError("仅支持 http/https 下载：%s" % url)
    if not _host_allowed(_host_of(url), extra_suffixes):
        raise DownloadError("下载地址不在白名单内：%s" % _host_of(url))


def _total_size(resp, have):
    """从响应头推断总字节数；拿不到则 None（未知大小进度条不定长）。"""
    cr = resp.headers.get("Content-Range")
    if cr:
        m = cr.rsplit("/", 1)
        if len(m) == 2 and m[1].isdigit():
            return int(m[1])
    cl = resp.headers.get("Content-Length")
    if cl and cl.isdigit():
        return have + int(cl)
    return None


def download(url, dest, *, extra_host_suffixes=(), progress=None, cancel=None):
    """断点续传下载 url → dest（原子完成）。

    progress(received, total) 节流回调；cancel() 返回 True 时中止并保留 .part。
    返回 (dest_path, resumed_bytes)。失败抛 DownloadError（.part 保留供续传）。
    """
    validate_url(url, extra_host_suffixes)
    dest = os.path.abspath(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    opener = build_opener(extra_host_suffixes)

    resumed = 0
    have = 0
    if os.path.isfile(part):
        have = os.path.getsize(part)
    headers = {"User-Agent": "llamacpp-fnos-manager/0.4.0"}
    if have > 0:
        headers["Range"] = "bytes=%d-" % have
    req = urllib.request.Request(url, headers=headers)

    try:
        resp = opener.open(req, timeout=CONNECT_TIMEOUT)
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise DownloadError("连接失败：%s（%s）" % (url.split("?")[0], exc))
    try:
        with resp:
            final_host = _host_of(resp.geturl())
            if not _host_allowed(final_host, extra_host_suffixes):
                raise DownloadError("最终地址不在白名单内：%s" % final_host)
            code = getattr(resp, "status", resp.getcode())
            total = _total_size(resp, 0)
            if code == 206 and have > 0:
                # 服务器确认续传：校验起始字节与本地 .part 一致（防源文件已变）
                cr = resp.headers.get("Content-Range", "")
                start_ok = False
                if cr.startswith("bytes "):
                    seg = cr[6:].split("-", 1)
                    if seg and seg[0].isdigit():
                        start_ok = int(seg[0]) == have
                if not start_ok:
                    have = 0
                    resumed = 0
                    total = _total_size(resp, 0)
                    fh = open(part, "wb")
                else:
                    resumed = have
                    total = _total_size(resp, have)
                    fh = open(part, "ab")
            elif code == 200:
                if have > 0 and total is not None and total == have:
                    # .part 已是完整文件（上次下载完成但未改名）：直接收尾
                    _finalize(part, dest)
                    if progress:
                        progress(have, total if total else have)
                    return dest, 0
                have = 0
                total = _total_size(resp, 0)
                fh = open(part, "wb")
            else:
                raise DownloadError("意外响应码 %s（%s）" % (code, url))
            try:
                last = 0.0
                while True:
                    if cancel is not None and cancel():
                        raise DownloadError("下载已取消")
                    block = resp.read(CHUNK)
                    if not block:
                        break
                    fh.write(block)
                    have += len(block)
                    now = time.monotonic()
                    if progress and (now - last >= PROGRESS_INTERVAL):
                        last = now
                        progress(have, total)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fh.close()
            if total is not None and have < total:
                raise DownloadError("连接中断（%d/%d 字节），可重新发起续传"
                                    % (have, total))
            if progress:
                progress(have, total if total else have)
    except DownloadError:
        raise
    except (socket.timeout, OSError) as exc:
        raise DownloadError("下载中断：%s（已保留 .part 可续传）" % exc)
    _finalize(part, dest)
    return dest, resumed


def _finalize(part, dest):
    os.replace(part, dest)
