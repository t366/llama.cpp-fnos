#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tasks.py —— 后台下载任务注册表。纯 Python 3 标准库。

每个任务一个 daemon 线程；状态机 queued → running → done|error|canceled。
前端经 GET /api/tasks/<id> 轮询（CGI 反代下不用 SSE，见设计 §4.5）。
任务表仅驻内存（断点续传能力由 .part 文件承担：进程重启后重新发起
下载会自动从 .part 续传，历史任务无需持久化）。
"""

import threading
import time
import uuid

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELED = "canceled"

MAX_TASKS = 50          # 注册表上限，超出丢弃最旧已完成任务
FINAL_STATES = (DONE, ERROR, CANCELED)


class TaskRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._tasks = {}         # id -> task dict（插入有序，py3.7+ dict 保序）
        self._cancel_flags = {}  # id -> bool

    def create(self, kind, payload):
        tid = uuid.uuid4().hex[:12]
        with self._lock:
            self._prune_locked()
            self._tasks[tid] = {
                "id": tid,
                "kind": kind,          # "variant" | "model"
                "payload": payload,
                "state": QUEUED,
                "created_at": int(time.time()),
                "started_at": None,
                "finished_at": None,
                "received": 0,
                "total": None,
                "speed": None,          # 字节/秒
                "resumed": 0,
                "error": None,
                "result": None,
            }
            self._cancel_flags[tid] = False
        return self._tasks[tid]

    def run(self, task, fn):
        """以 daemon 线程执行 fn(task)；fn 内部调用 update/progress。"""
        with self._lock:
            task["state"] = RUNNING
            task["started_at"] = int(time.time())
        t = threading.Thread(target=self._wrap, args=(task, fn), daemon=True)
        t.start()
        return t

    def _wrap(self, task, fn):
        try:
            result = fn(task)
            self.update(task["id"], state=DONE, finished_at=int(time.time()),
                        result=result or {})
        except Exception as exc:  # 任务内任何异常都落为 error 态
            if self.cancel_requested(task["id"]):
                # 用户主动取消（downloader 检测标志后中断）不算错误
                self.update(task["id"], state=CANCELED, error="已取消",
                            finished_at=int(time.time()))
            else:
                self.update(task["id"], state=ERROR, error=str(exc),
                            finished_at=int(time.time()))

    def update(self, task_id, **fields):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.update(fields)

    def progress(self, task_id, received, total):
        """下载进度回调：记录速率（简单指数滑动平均）。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            prev = task.get("received") or 0
            now = time.monotonic()
            last_t = task.get("_last_t") or 0.0
            dt = now - last_t if last_t else 0.0
            if dt > 0.3:
                inst = (received - prev) / dt
                old = task.get("speed")
                task["speed"] = int(inst if old is None else old * 0.6 + inst * 0.4)
                task["_last_t"] = now
            task["received"] = received
            task["total"] = total

    def cancel(self, task_id):
        with self._lock:
            self._cancel_flags[task_id] = True

    def cancel_requested(self, task_id):
        with self._lock:
            return self._cancel_flags.get(task_id, False)

    def get(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def list(self, kind=None):
        with self._lock:
            items = [dict(t) for t in self._tasks.values()]
        if kind:
            items = [t for t in items if t["kind"] == kind]
        items.reverse()  # 新任务在前
        return items

    def _prune_locked(self):
        """控制内存占用：终态任务超过配额时从最旧开始丢弃。"""
        final = [t for t in self._tasks.values() if t["state"] in FINAL_STATES]
        overflow = len(self._tasks) - MAX_TASKS
        for t in final[:max(0, overflow + 1)]:
            self._tasks.pop(t["id"], None)
            self._cancel_flags.pop(t["id"], None)
