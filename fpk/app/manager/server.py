#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llamacpp manager-server —— 飞牛 fnOS llama.cpp 应用管理后端（M1 骨架版）。

只监听 127.0.0.1；唯一外部入口是 fnOS nginx 的
/cgi/ThirdParty/llamacpp/index.cgi/（走飞牛登录鉴权）。
本版仅提供 GET /api/status，用于打通 FPK → CGI 反代 → manager 链路；
引擎管理、模型管理、硬件检测与下载任务将在后续里程碑加入。
纯 Python 3 标准库实现，无第三方依赖。
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NAME = "llamacpp"
APP_VERSION = "0.1.0"

# HTTP 状态码
OK = 200
NOT_FOUND = 404


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

    # ---------- 路由 ----------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            self.send_json({
                "ok": True,
                "app": APP_NAME,
                "version": APP_VERSION,
                "milestone": "M1",
                "service": "manager",
            })
            return
        self.send_error_json(NOT_FOUND, "not found: %s" % path)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        self.send_error_json(NOT_FOUND, "not found: %s" % path)

    def do_PUT(self):
        path = self.path.split("?", 1)[0]
        self.send_error_json(NOT_FOUND, "not found: %s" % path)

    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        self.send_error_json(NOT_FOUND, "not found: %s" % path)


def main():
    parser = argparse.ArgumentParser(description="llamacpp manager-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--app-dir", default=".",
                        help="应用安装目录（TRIM_APPDEST），存放二进制与 ui")
    parser.add_argument("--data-dir", default=".",
                        help="数据目录（TRIM_PKGVAR），存放模型/配置/日志")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("[%s] manager listening on %s:%d" % (APP_NAME, args.host, args.port),
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
