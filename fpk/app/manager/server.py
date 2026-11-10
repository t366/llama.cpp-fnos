#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llamacpp manager-server —— 飞牛 fnOS llama.cpp 应用管理后端（M4 下载版）。

只监听 127.0.0.1；唯一外部入口是 fnOS nginx 的
/cgi/ThirdParty/llamacpp/index.cgi/（走飞牛登录鉴权）。
纯 Python 3 标准库实现，无第三方依赖。

路由（JSON）：
- GET  /api/status                  引擎运行态 + 服务信息 + 硬件摘要
- GET  /api/hardware                硬件检测（缓存）；POST redetect 重新检测
- POST /api/engine/start|stop|restart
- GET  /api/engine/logs?tail=N      环形缓冲尾部（默认 200，上限 2000）
- GET  /api/variants                已装/可装变体 + 推荐项
- POST /api/variants/install        按需下载安装变体（后台任务）
- GET  /api/settings / PUT /api/settings
- GET  /api/models                  本地扫描
- GET  /api/models/search?q=&source=&limit=   模型市场搜索
- GET  /api/models/files?source=&repo=        仓库内 GGUF 文件列表
- GET  /api/models/recommend?limit=           按本机内存推荐 GGUF 模型（仅含 .gguf 的仓库）
- POST /api/models/download         市场模型下载（后台任务）
- GET  /api/tasks、/api/tasks/<id>  任务列表/进度轮询；POST /api/tasks/<id>/cancel
被停止（TERM）或退出时自动终止 llama-server 子进程。
"""

import argparse
import atexit
import json
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import downloader
import hardware
import market
import variants
from engine import Engine
from models import ModelError, delete_model, resolve_models_dir, scan as scan_models
from settings import Settings
from tasks import TaskRegistry

APP_NAME = "llamacpp"
APP_VERSION = "0.4.6"
MILESTONE = "M4"

# 限制
BODY_MAX_BYTES = 1024 * 1024      # 请求体上限 1MB（设计 §4.6）
DEFAULT_LOG_TAIL = 200

# HTTP 状态码
OK = 200
BAD_REQUEST = 400
NOT_FOUND = 404
METHOD_NOT_ALLOWED = 405
INTERNAL_ERROR = 500

engine = None      # 在 main() 中初始化
settings = None
server = None      # HTTPServer 引用，供 SIGTERM 处理器调用 shutdown()
tasks_reg = TaskRegistry()   # 后台下载任务表（变体安装 + 模型下载）
_hw_cache = None              # 硬件检测结果缓存（None = 未检测）
_hw_lock = threading.Lock()


class ManagerServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # 客户端中断连接（CGI 超时/轮询被掐断）属正常噪音，不打印堆栈污染日志
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "llamacpp-manager/" + APP_VERSION
    protocol_version = "HTTP/1.1"

    # ---------- 基础响应 ----------
    def send_json(self, obj, status=OK):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json({"ok": False, "error": message}, status)

    def log_message(self, fmt, *args):
        # 精简默认访问日志：CGI 反代下前端会轮询，避免刷爆日志
        pass

    def _read_json_body(self):
        """读取并解析 JSON 请求体；返回 (obj, error_message)。"""
        ctype = self.headers.get("Content-Type", "")
        if "application/json" not in ctype:
            return None, "Content-Type 必须为 application/json"
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None, "Content-Length 非法"
        if length <= 0:
            return None, "请求体为空"
        if length > BODY_MAX_BYTES:
            return None, "请求体超过 1MB 上限"
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")), None
        except (ValueError, UnicodeDecodeError) as exc:
            return None, "JSON 解析失败：%s" % exc

    # ---------- 路由 ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/status":
                self.handle_status()
            elif path == "/api/engine/logs":
                self.handle_logs(query)
            elif path == "/api/settings":
                self.send_json({"ok": True, "settings": settings.get()})
            elif path == "/api/models":
                self.handle_models_list()
            elif path == "/api/hardware":
                self.handle_hardware()
            elif path == "/api/variants":
                self.handle_variants()
            elif path == "/api/models/search":
                self.handle_market_search(query)
            elif path == "/api/models/files":
                self.handle_market_files(query)
            elif path == "/api/models/detail":
                self.handle_market_detail(query)
            elif path == "/api/models/recommend":
                self.handle_market_recommend(query)
            elif path == "/api/tasks":
                self.send_json({"ok": True, "tasks": self._task_views()})
            elif path.startswith("/api/tasks/"):
                self.handle_task_get(path[len("/api/tasks/"):])
            else:
                self.send_error_json(NOT_FOUND, "not found: %s" % path)
        except Exception as exc:  # 防御：任何路由内异常都回 JSON 而非断连
            self.send_error_json(INTERNAL_ERROR, "internal error: %s" % exc)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path in ("/api/engine/start", "/api/engine/stop", "/api/engine/restart"):
                self.handle_engine_action(path.rsplit("/", 1)[1])
            elif path == "/api/hardware/redetect":
                self.handle_hardware_redetect()
            elif path == "/api/variants/install":
                self.handle_variant_install()
            elif path == "/api/models/download":
                self.handle_model_download()
            elif path.startswith("/api/tasks/") and path.endswith("/cancel"):
                self.handle_task_cancel(
                    path[len("/api/tasks/"):-len("/cancel")])
            else:
                self.send_error_json(NOT_FOUND, "not found: %s" % path)
        except Exception as exc:
            self.send_error_json(INTERNAL_ERROR, "internal error: %s" % exc)

    def do_PUT(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/settings":
                body, err = self._read_json_body()
                if err:
                    self.send_error_json(BAD_REQUEST, err)
                    return
                ok, verr = settings.update(body)
                if not ok:
                    self.send_error_json(BAD_REQUEST, verr)
                    return
                self.send_json({"ok": True, "settings": settings.get(),
                                "note": "配置已保存；引擎运行中时修改端口/参数需重启引擎生效"})
            else:
                self.send_error_json(NOT_FOUND, "not found: %s" % path)
        except Exception as exc:
            self.send_error_json(INTERNAL_ERROR, "internal error: %s" % exc)

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/models":
                body, err = self._read_json_body()
                if err:
                    self.send_error_json(BAD_REQUEST, err)
                    return
                root = self._models_root()
                try:
                    removed = delete_model(root, body.get("path"))
                except ModelError as exc:
                    self.send_error_json(BAD_REQUEST, str(exc))
                    return
                self.send_json({"ok": True, "removed": removed})
            else:
                self.send_error_json(NOT_FOUND, "not found: %s" % path)
        except Exception as exc:
            self.send_error_json(INTERNAL_ERROR, "internal error: %s" % exc)

    # ---------- 处理器 ----------
    def handle_status(self):
        hw = _hardware()
        self.send_json({
            "ok": True,
            "app": APP_NAME,
            "version": APP_VERSION,
            "milestone": MILESTONE,
            "service": "manager",
            "engine": engine.status(),
            "hardware_summary": hardware.summary_text(hw),
            "recommended_variant": hw.get("recommended"),
        })

    # ---------- 硬件检测 ----------
    def handle_hardware(self):
        hw = _hardware()
        self.send_json({"ok": True, "hardware": hw,
                        "summary": hardware.summary_text(hw)})

    def handle_hardware_redetect(self):
        global _hw_cache
        with _hw_lock:
            _hw_cache = hardware.detect()
            hw = _hw_cache
        engine.log_line("[manager] 重新硬件检测：%s"
                        % hardware.summary_text(hw))
        self.send_json({"ok": True, "hardware": hw,
                        "summary": hardware.summary_text(hw)})

    # ---------- 变体 ----------
    def handle_variants(self):
        cfg = settings.get()
        hw = _hardware()
        self.send_json({
            "ok": True,
            "installed": variants.installed(engine.app_dir, engine.data_dir),
            "available": sorted(variants.VARIANTS.keys()),
            "recommended": hw.get("recommended"),
            "current": cfg.get("variant") or None,
            "download_source": cfg.get("download_source"),
            "mirror_base": cfg.get("mirror_base") or None,
            "pinned_tag": variants.PINNED_TAG,
        })

    def handle_variant_install(self):
        body, err = self._read_json_body()
        if err:
            self.send_error_json(BAD_REQUEST, err)
            return
        name = body.get("variant")
        if not variants.exists(name):
            self.send_error_json(BAD_REQUEST, "未知变体：%s" % name)
            return
        # 同变体安装任务去重
        for t in tasks_reg.list("variant"):
            if t["state"] in ("queued", "running") and \
                    t["payload"].get("variant") == name:
                self.send_json({"ok": True, "task": _task_view(t),
                                "note": "该变体已在安装中"})
                return
        cfg = settings.get()
        source = body.get("source") or cfg.get("download_source") or "github"
        task = tasks_reg.create("variant", {"variant": name, "source": source})

        def work(t):
            def progress(received, total, stage):
                tasks_reg.progress(t["id"], received or 0, total)
                tasks_reg.update(t["id"], stage=stage)
            ok, verr, ver = variants.install(
                name, engine.app_dir, engine.data_dir, source,
                cfg.get("mirror_base") or "", progress=progress,
                cancel=lambda: tasks_reg.cancel_requested(t["id"]))
            if not ok:
                raise RuntimeError(verr)
            return {"version": ver, "dir": os.path.join(
                engine.data_dir, "bin", name)}

        tasks_reg.run(task, work)
        engine.log_line("[manager] 开始安装变体 %s（源 %s）" % (name, source))
        self.send_json({"ok": True, "task": _task_view(task)})

    # ---------- 模型市场 ----------
    def handle_market_search(self, query):
        q = (query.get("q", [""])[0] or "").strip()
        source = query.get("source", [settings.value("market_source")])[0]
        try:
            limit = int(query.get("limit", [market.DEFAULT_LIMIT])[0])
        except ValueError:
            limit = market.DEFAULT_LIMIT
        try:
            items = market.search(source, q, limit)
        except market.MarketError as exc:
            self.send_error_json(BAD_REQUEST, str(exc))
            return
        self.send_json({"ok": True, "source": source,
                        "count": len(items), "items": items})

    def handle_market_files(self, query):
        source = query.get("source", [settings.value("market_source")])[0]
        repo = (query.get("repo", [""])[0] or "").strip()
        try:
            files = market.list_files(source, repo)
        except market.MarketError as exc:
            self.send_error_json(BAD_REQUEST, str(exc))
            return
        self.send_json({"ok": True, "source": source, "repo": repo,
                        "count": len(files), "files": files})

    def handle_market_detail(self, query):
        source = query.get("source", [settings.value("market_source")])[0]
        repo = (query.get("repo", [""])[0] or "").strip()
        try:
            detail = market.repo_detail(source, repo)
        except market.MarketError as exc:
            self.send_error_json(BAD_REQUEST, str(exc))
            return
        self.send_json({"ok": True, "detail": detail})

    def handle_market_recommend(self, query):
        """按本机配置推荐：可用内存 = 物理内存 +（加速变体下的）显存，
        与前端 ramTag 同口径；逐仓探测只保留真实含 .gguf 的仓库。"""
        try:
            limit = int(query.get("limit", [market.DEFAULT_LIMIT])[0])
        except ValueError:
            limit = market.DEFAULT_LIMIT
        limit = max(1, min(limit, 30))
        hw = _hardware()
        avail = hw.get("mem_total_mib") or 0
        rec = hw.get("recommended") or ""
        if rec.startswith(("cuda-", "vulkan-")):
            avail += (hw.get("gpu") or {}).get("memory_mib") or 0
        try:
            items = market.recommend(avail or None, limit)
        except market.MarketError as exc:
            self.send_error_json(BAD_REQUEST, str(exc))
            return
        self.send_json({"ok": True, "count": len(items), "items": items,
                        "avail_mib": avail or None})

    def handle_model_download(self):
        body, err = self._read_json_body()
        if err:
            self.send_error_json(BAD_REQUEST, err)
            return
        source = body.get("source") or settings.value("market_source")
        repo = (body.get("repo") or "").strip()
        path = (body.get("file") or "").strip()
        try:
            url = market.download_url(source, repo, path)
        except market.MarketError as exc:
            self.send_error_json(BAD_REQUEST, str(exc))
            return
        root = self._models_root()
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as exc:
            self.send_error_json(BAD_REQUEST, "模型目录不可创建：%s" % exc)
            return
        filename = path.rsplit("/", 1)[-1]
        dest = os.path.join(root, filename)
        # 目标已存在（或别处同名任务在跑）时拒绝重复下载
        if os.path.isfile(dest):
            self.send_error_json(BAD_REQUEST, "文件已存在：%s（可先删除再下载）" % filename)
            return
        for t in tasks_reg.list("model"):
            if t["state"] in ("queued", "running") and \
                    t["payload"].get("file") == path and \
                    t["payload"].get("repo") == repo:
                self.send_json({"ok": True, "task": _task_view(t),
                                "note": "该文件已在下载中"})
                return
        task = tasks_reg.create("model", {
            "source": source, "repo": repo, "file": path, "dest": dest})

        def work(t):
            # 断点续传：既有 .part 大小立即写入任务，轮询端即刻可见
            part = dest + ".part"
            if os.path.isfile(part):
                tasks_reg.update(t["id"], resumed=os.path.getsize(part))

            def progress(received, total):
                tasks_reg.progress(t["id"], received, total)
            final, resumed = downloader.download(
                url, dest, extra_host_suffixes=downloader.DEFAULT_HOST_SUFFIXES,
                progress=progress,
                cancel=lambda: tasks_reg.cancel_requested(t["id"]))
            engine.log_line("[manager] 模型下载完成：%s（续传 %d 字节）"
                            % (filename, resumed))
            return {"dest": final, "resumed": resumed}

        tasks_reg.run(task, work)
        engine.log_line("[manager] 开始下载模型 %s/%s（%s）"
                        % (repo, path, source))
        self.send_json({"ok": True, "task": _task_view(task)})

    # ---------- 任务 ----------
    def handle_task_get(self, task_id):
        task = tasks_reg.get(task_id)
        if not task:
            self.send_error_json(NOT_FOUND, "任务不存在：%s" % task_id)
            return
        self.send_json({"ok": True, "task": _task_view(task)})

    def handle_task_cancel(self, task_id):
        task = tasks_reg.get(task_id)
        if not task:
            self.send_error_json(NOT_FOUND, "任务不存在：%s" % task_id)
            return
        if task["state"] in ("done", "error", "canceled"):
            self.send_json({"ok": True, "task": _task_view(task),
                            "note": "任务已结束"})
            return
        tasks_reg.cancel(task_id)
        self.send_json({"ok": True, "task": tasks_reg.get(task_id)})

    def _task_views(self):
        return [_task_view(t) for t in tasks_reg.list()]

    def handle_engine_action(self, action):
        if action == "start":
            ok, err = engine.start()
        elif action == "stop":
            ok, err = engine.stop()
        elif action == "restart":
            ok, err = engine.restart()
        else:  # 路由保证不会到达
            return self.send_error_json(NOT_FOUND, "unknown action")
        payload = {"ok": ok, "engine": engine.status()}
        if err:
            payload["error"] = err
        self.send_json(payload, OK if ok else BAD_REQUEST)

    def handle_logs(self, query):
        try:
            tail = int(query.get("tail", [DEFAULT_LOG_TAIL])[0])
        except ValueError:
            tail = DEFAULT_LOG_TAIL
        self.send_json({"ok": True, "lines": engine.logs(tail)})

    def _models_root(self):
        return resolve_models_dir(engine.data_dir, settings.value("models_dir"))

    def handle_models_list(self):
        root = self._models_root()
        found = scan_models(root)
        self.send_json({"ok": True, "models_dir": root,
                        "count": len(found), "models": found})


def _hardware():
    """硬件检测结果（进程内缓存，POST /api/hardware/redetect 可刷新）。"""
    global _hw_cache
    with _hw_lock:
        if _hw_cache is None:
            _hw_cache = hardware.detect()
        return _hw_cache


def _task_view(task):
    """任务字典 → 前端视图：剔除 _ 开头的内部字段。"""
    return {k: v for k, v in task.items() if not k.startswith("_")}


def _terminate_engine_and_exit():
    if engine is not None:
        engine.shutdown_cleanup()


def main():
    parser = argparse.ArgumentParser(description="llamacpp manager-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--app-dir", default=".",
                        help="应用安装目录（TRIM_APPDEST），存放二进制与 ui")
    parser.add_argument("--data-dir", default=".",
                        help="数据目录（TRIM_PKGVAR），存放模型/配置/日志")
    args = parser.parse_args()

    global engine, settings, server
    settings = Settings(args.data_dir)
    engine = Engine(args.app_dir, args.data_dir, settings)

    # 收到 TERM（fnOS stop）时：serve_forever 返回 → finally 中清理子进程并退出
    signal.signal(signal.SIGTERM, _handle_term)
    atexit.register(_terminate_engine_and_exit)

    server = ManagerServer((args.host, args.port), Handler)
    print("[%s] manager %s (%s) listening on %s:%d, app=%s, data=%s"
          % (APP_NAME, APP_VERSION, MILESTONE, args.host, args.port,
             args.app_dir, args.data_dir),
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _terminate_engine_and_exit()


def _handle_term(signum, frame):
    # serve_forever 在主线程，shutdown() 需在其他线程调用避免死锁；
    # shutdown() 返回后 main() 的 finally 负责清理 llama-server 并退出进程
    threading.Thread(target=server.shutdown, daemon=True).start()


if __name__ == "__main__":
    main()
