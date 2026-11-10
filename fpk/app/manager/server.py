#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llamacpp manager-server —— 飞牛 fnOS llama.cpp 应用管理后端（M2 引擎管理版）。

只监听 127.0.0.1；唯一外部入口是 fnOS nginx 的
/cgi/ThirdParty/llamacpp/index.cgi/（走飞牛登录鉴权）。
纯 Python 3 标准库实现，无第三方依赖。

路由（JSON）：
- GET  /api/status                  引擎运行态 + 服务信息
- POST /api/engine/start|stop|restart
- GET  /api/engine/logs?tail=N      环形缓冲尾部（默认 200，上限 2000）
- GET  /api/settings                当前配置
- PUT  /api/settings                修改配置（application/json）
被停止（TERM）或退出时自动终止 llama-server 子进程。
"""

import argparse
import atexit
import json
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from engine import Engine
from settings import Settings

APP_NAME = "llamacpp"
APP_VERSION = "0.2.1"
MILESTONE = "M2"

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
                self.send_error_json(NOT_FOUND, "hardware detect 在 M4 提供")
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
        self.send_error_json(NOT_FOUND, "not found")

    # ---------- 处理器 ----------
    def handle_status(self):
        self.send_json({
            "ok": True,
            "app": APP_NAME,
            "version": APP_VERSION,
            "milestone": MILESTONE,
            "service": "manager",
            "engine": engine.status(),
        })

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
    print("[%s] manager %s (M2) listening on %s:%d, app=%s, data=%s"
          % (APP_NAME, APP_VERSION, args.host, args.port, args.app_dir, args.data_dir),
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
