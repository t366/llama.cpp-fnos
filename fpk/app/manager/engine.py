#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""engine.py —— llama-server 子进程生命周期管理。纯 Python 3 标准库。

职责：
- 按设置拼装命令行，拉起 llama-server（新会话进程组，LD_LIBRARY_PATH 指向变体目录）
- 崩溃守护：异常退出且开启 auto_restart 时按退避间隔自动重启
- 日志：内存环形缓冲（供 /api/engine/logs 取尾部）+ 落盘数据目录 logs/llama-server.log
- 停止：进程组 TERM → 5s → KILL
"""

import collections
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request

# 引擎状态
STOPPED = "stopped"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
ERROR = "error"

LOG_MAX_LINES = 2000          # 内存环形缓冲行数（tail 上限）
LOG_FILE_MAX = 5 * 1024 * 1024  # 落盘日志超过 5MB 时轮转
START_TIMEOUT = 90            # /health 就绪等待秒数；超时后降级为端口连通性判定
POLL_INTERVAL = 1.0           # /health 轮询间隔（CGI 轮询约束 ≥1s）
CRASH_BACKOFF = (2, 5, 10, 30, 60)  # 连续崩溃重启的退避序列（秒）
MAX_CRASH_RESTARTS = 5        # 连续崩溃自动重启上限（确定性错误不应无限循环）

# 健康探测禁用系统代理：NAS 配置了 http_proxy 时，对 127.0.0.1 的探测会被
# 转发给代理而必然失败（服务实际可用），必须直连（ProxyHandler({}) 绕过）
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Engine:
    """线程安全；所有状态变更持 _lock（RLock，守护线程重启时可能重入）。"""

    def __init__(self, app_dir, data_dir, settings):
        self.app_dir = app_dir
        self.data_dir = data_dir
        self.settings = settings
        self._lock = threading.RLock()
        self._proc = None            # 当前子进程（可能已被守护线程接管的旧引用）
        self._state = STOPPED
        self._started_at = 0.0
        self._manual_stop = False    # 手动停止标志，守护线程据此不自动重启
        self._exit_code = None       # 最近一次退出码（正常停止不清空）
        self._crash_count = 0        # 连续崩溃次数（成功 RUNNING 后清零）
        self._generation = 0         # 启停代次；守护线程据此识别自己是否已过期
        self._logs = collections.deque(maxlen=LOG_MAX_LINES)
        self._log_lock = threading.Lock()

    # ---------- 变体与路径 ----------
    def default_variant(self):
        machine = os.uname().machine if hasattr(os, "uname") else "x86_64"
        return "cpu-aarch64" if machine == "aarch64" else "cpu-x86_64"

    def variant_dir(self):
        """变体目录：数据目录 bin/<variant>（M4 下载的加速版）优先，退回应用内置。"""
        name = self.settings.value("variant") or self.default_variant()
        for base in (os.path.join(self.data_dir, "bin"),
                     os.path.join(self.app_dir, "bin")):
            d = os.path.join(base, name)
            if os.path.isfile(os.path.join(d, "llama-server")):
                return d
        return None

    # ---------- 模型路径解析 ----------
    def _resolve_model(self, path):
        """允许填 GGUF 文件路径，或仅含一个 GGUF 的目录（自动选用）。
        返回 (resolved_path, error)；多文件时列出候选由用户指定。"""
        if not path:
            return None, "未配置模型路径，请先在设置页填写 GGUF 路径"
        if os.path.isfile(path):
            return path, None
        if os.path.isdir(path):
            try:
                names = sorted(f for f in os.listdir(path)
                               if f.lower().endswith(".gguf"))
            except OSError as exc:
                return None, "模型目录不可读：%s（%s）" % (path, exc)
            if not names:
                return None, "模型目录中未找到 .gguf 文件：%s" % path
            if len(names) > 1:
                return None, ("模型目录含 %d 个 GGUF，请指定具体文件：%s"
                              % (len(names), "、".join(names[:8])))
            return os.path.join(path, names[0]), None
        return None, "模型路径不存在：%s" % path

    # ---------- 启动 ----------
    def _build_cmd(self, cfg, vdir, model):
        cmd = [os.path.join(vdir, "llama-server"),
               "--host", cfg["bind"], "--port", str(cfg["inference_port"])]
        if model:
            cmd += ["-m", model]
        cmd += ["--ctx-size", str(cfg["ctx_size"])]
        if cfg.get("threads"):
            cmd += ["--threads", str(cfg["threads"])]
        if cfg.get("ngl"):
            cmd += ["--n-gpu-layers", str(cfg["ngl"])]
        if cfg.get("api_key"):
            cmd += ["--api-key", cfg["api_key"]]
        # Embedding 模式：llama-server 仅暴露 /v1/embeddings，不加载对话采样参数
        if cfg.get("mode") == "embedding":
            cmd += ["--embedding"]
        # 多模态投影模型（视觉编码）；chat 与 embedding 模式均可按需加载
        if cfg.get("mmproj_model"):
            cmd += ["--mmproj", cfg["mmproj_model"]]
        return cmd

    def _check_port_free(self, host, port):
        """启动前预检推理端口；被占用时给出明确提示，避免 llama-server 反复崩溃。
        绑定测试带 SO_REUSEADDR：TIME_WAIT 不算占用，真实监听才算。"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, int(port)))
            finally:
                s.close()
            return None
        except OSError as exc:
            return ("推理端口不可用：%s:%d（%s）。该端口可能已被其他程序占用，"
                    "请在设置页更换推理端口后重试"
                    % (host, int(port), exc))

    def start(self, reset_crashes=True):
        """返回 (ok, error)。失败不改变既有状态。
        reset_crashes=False 用于守护线程的自动重启（保持连续崩溃计数）。"""
        with self._lock:
            if reset_crashes:
                self._crash_count = 0
            if self._state in (STARTING, RUNNING, STOPPING):
                return False, "引擎当前为 %s，无需重复启动" % self._state
            cfg = self.settings.get()
            vdir = self.variant_dir()
            if not vdir:
                return False, "未找到变体 %s 的二进制（llama-server 不存在）" % (
                    cfg.get("variant") or self.default_variant())
            model, merr = self._resolve_model(cfg.get("model"))
            if merr:
                return False, merr
            mmproj = cfg.get("mmproj_model")
            if mmproj and not os.path.isfile(mmproj):
                return False, "mmproj 文件不存在：%s" % mmproj
            perr = self._check_port_free(cfg.get("bind", "0.0.0.0"),
                                         cfg.get("inference_port", 8080))
            if perr:
                self.log_line("[manager] 启动中止：" + perr)
                return False, perr
            if model != cfg.get("model"):
                self.log_line("[manager] 模型解析：%s -> %s" % (cfg.get("model"), model))
            self._log_rotate_if_needed()
            self._manual_stop = False
            self._exit_code = None
            self._generation += 1
            gen = self._generation
            self._state = STARTING
            env = dict(os.environ)
            # llama.cpp 官方包的动态库都在变体目录内，必须置于库搜索路径首位
            env["LD_LIBRARY_PATH"] = vdir + (
                ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
            try:
                proc = subprocess.Popen(
                    self._build_cmd(cfg, vdir, model),
                    cwd=vdir, env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    start_new_session=True,   # 独立进程组，停止时可整组 TERM
                    text=True, errors="replace", bufsize=1)
            except OSError as exc:
                self._state = STOPPED
                self.log_line("[manager] 启动失败：%s" % exc)
                return False, "启动失败：%s" % exc
            self._proc = proc
            self._started_at = time.time()
            self._logs.clear()
            self.log_line("[manager] 启动 llama-server（变体 %s，pid %d）"
                          % (os.path.basename(vdir), proc.pid))
            self.log_line("[manager] 参数：%s" % " ".join(self._build_cmd(cfg, vdir, model)))
        # 输出泵 / 就绪探测 / 崩溃守护各一线程，均随主进程退出而消亡
        threading.Thread(target=self._pump_output, args=(proc, gen),
                         daemon=True).start()
        threading.Thread(target=self._await_ready, args=(proc, gen),
                         daemon=True).start()
        threading.Thread(target=self._guard, args=(proc, gen),
                         daemon=True).start()
        return True, None

    # ---------- 停止 ----------
    def stop(self):
        with self._lock:
            self._manual_stop = True
            self._generation += 1
            proc = self._proc
            if self._state == STOPPED or proc is None:
                self._state = STOPPED
                return True, None
            self._state = STOPPING
            self.log_line("[manager] 停止 llama-server（pid %d）" % proc.pid)
        self._terminate(proc, timeout=5.0)
        with self._lock:
            self._proc = None
            self._state = STOPPED
            self._started_at = 0.0
            self.log_line("[manager] llama-server 已停止")
        return True, None

    def restart(self):
        # 先停后启；stop/start 各自持锁，中间状态短暂为 stopped 属正常
        ok, err = self.stop()
        if not ok:
            return False, err
        return self.start()

    def _terminate(self, proc, timeout):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                pass
        if self._wait_exit(proc, timeout):
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
        self._wait_exit(proc, 3.0)

    @staticmethod
    def _wait_exit(proc, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(0.1)
        return proc.poll() is not None

    # ---------- 后台线程 ----------
    def _pump_output(self, proc, gen):
        """读取子进程输出到环形缓冲与日志文件；EOF 即退出（守护线程负责收尾）。"""
        fh = None
        try:
            fh = open(self._log_path(), "a", encoding="utf-8", errors="replace")
            for line in proc.stdout:
                self.log_line(line.rstrip("\n"))
                fh.write(line)
                fh.flush()
        except (OSError, ValueError):
            pass
        finally:
            if fh:
                fh.close()

    def _await_ready(self, proc, gen):
        """轮询 127.0.0.1:<port>/health，就绪则置 RUNNING。

        探测禁用系统代理（127.0.0.1 走代理必然失败）。健康端点始终探不通时
        降级判定：进程存活且推理端口已监听（TCP 可连）即视为 RUNNING——
        服务实际可用，仅 /health 被代理/防火墙拦截或端点行为变化。
        """
        port = self.settings.value("inference_port")
        url = "http://127.0.0.1:%d/health" % port
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            with self._lock:
                if gen != self._generation or proc is not self._proc:
                    return
                if self._state != STARTING:
                    return
            try:
                with _NO_PROXY_OPENER.open(url, timeout=2) as resp:
                    if resp.status == 200:
                        with self._lock:
                            if gen == self._generation and proc is self._proc:
                                self._state = RUNNING
                                self._crash_count = 0
                                self.log_line("[manager] llama-server 就绪（%s）" % url)
                        return
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)
        # 健康端点超时：降级判定端口连通性；通则按 RUNNING（服务可用），
        # 不通则继续等待（大模型加载中端口尚未监听；进程退出由守护线程定论）
        self.log_line("[manager] /health 等待 %ds 未成功，降级为端口探测…" % START_TIMEOUT)
        while True:
            with self._lock:
                if gen != self._generation or proc is not self._proc:
                    return
                if self._state != STARTING:
                    return
            if proc.poll() is not None:
                return  # 进程已退出，守护线程定论
            if self._tcp_ok(port):
                with self._lock:
                    if (gen == self._generation and proc is self._proc
                            and self._state == STARTING):
                        self._state = RUNNING
                        self._crash_count = 0
                        self.log_line("[manager] /health 超时但端口已监听，"
                                      "按运行中处理（健康检查降级，服务可用）")
                return
            time.sleep(POLL_INTERVAL)

    @staticmethod
    def _tcp_ok(port):
        """推理端口 TCP 连通性测试（健康检查降级用）。"""
        try:
            s = socket.create_connection(("127.0.0.1", int(port)), timeout=2)
            s.close()
            return True
        except OSError:
            return False

    def _guard(self, proc, gen):
        """等待进程退出；若非手动停止且开启自动重启，则按退避重启。"""
        code = proc.wait()
        with self._lock:
            self._exit_code = code
            stale = (gen != self._generation) or (proc is not self._proc)
            if stale:
                return  # 手动 stop() 或新一轮 start() 已接管状态
            self._proc = None
            self.log_line("[manager] llama-server 退出，exit code=%d" % code)
        if self._manual_stop:
            return
        cfg = self.settings
        if not cfg.value("auto_restart"):
            with self._lock:
                self._state = ERROR
                self._started_at = 0.0
            return
        if self._crash_count >= MAX_CRASH_RESTARTS:
            # 连续崩溃达到上限：多半是确定性配置错误（模型路径/参数非法），停止循环
            with self._lock:
                self._state = ERROR
                self._started_at = 0.0
            self.log_line("[manager] 已连续崩溃 %d 次，停止自动重启；"
                          "请检查模型路径与参数后手动启动" % self._crash_count)
            return
        # 退避重启（连续崩溃计数限制在退避序列长度内）
        delay = CRASH_BACKOFF[min(self._crash_count, len(CRASH_BACKOFF) - 1)]
        self._crash_count += 1
        self.log_line("[manager] %ds 后自动重启（第 %d 次崩溃）" % (delay, self._crash_count))
        self._state_stash_for_backoff()
        time.sleep(delay)
        with self._lock:
            # 退避期间用户手动停止/启动（代次变化）则放弃本次自动重启：
            # 否则旧守护线程会与新状态互相踩踏（误导日志、手动停止后仍被拉起）
            if gen != self._generation or self._manual_stop:
                return
        ok, err = self.start(reset_crashes=False)
        if not ok:
            with self._lock:
                if gen != self._generation:
                    return  # 期间已被手动接管，失败无需定性、也不刷误导日志
                self._state = ERROR
            self.log_line("[manager] 自动重启失败：%s" % err)

    def _state_stash_for_backoff(self):
        # 退避等待期间展示为 error（进程已死），避免前端误以为仍在运行
        with self._lock:
            self._state = ERROR
            self._started_at = 0.0

    # ---------- 查询 ----------
    def status(self):
        with self._lock:
            cfg = self.settings.get()
            proc = self._proc
            return {
                "state": self._state,
                "variant": cfg.get("variant") or self.default_variant(),
                "variant_dir": self.variant_dir(),
                "port": cfg.get("inference_port"),
                "bind": cfg.get("bind"),
                "model": cfg.get("model") or None,
                "mode": cfg.get("mode") or "chat",
                "mmproj_model": cfg.get("mmproj_model") or None,
                "pid": proc.pid if proc else None,
                "uptime_s": (int(time.time() - self._started_at)
                             if self._state == RUNNING and self._started_at else None),
                "auto_restart": cfg.get("auto_restart"),
                "exit_code": self._exit_code,
                "crash_count": self._crash_count,
            }

    def logs(self, tail):
        with self._log_lock:
            lines = list(self._logs)
        return lines[-max(1, min(tail, LOG_MAX_LINES)):]

    def log_line(self, text):
        with self._log_lock:
            self._logs.append(text)

    # ---------- 日志文件 ----------
    def _log_path(self):
        d = os.path.join(self.data_dir, "logs")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "llama-server.log")

    def _log_rotate_if_needed(self):
        path = self._log_path()
        try:
            if os.path.isfile(path) and os.path.getsize(path) > LOG_FILE_MAX:
                os.replace(path, path + ".1")
        except OSError:
            pass

    # ---------- 退出清理（manager 被停止时由 SIGTERM/atexit 调用） ----------
    def shutdown_cleanup(self):
        with self._lock:
            proc = self._proc
            gen = self._generation
            self._manual_stop = True
            self._generation += 1
        if proc is not None and proc.poll() is None:
            self._terminate(proc, timeout=3.0)
            self.log_line("[manager] 退出清理：llama-server 已终止（gen=%d）" % gen)
