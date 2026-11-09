# llama.cpp 飞牛应用实施计划

日期：2026-11-10
依据：[设计文档](../specs/2026-11-10-llama-cpp-fnos-app-design.md)（唯一权威，已获批准）
代码托管：http://192.168.0.114:3000/T366/llama.cpp-fnos

## 0. 工作区布局

```
d:\Project3\llama.cpp-fnos\
├── docs\superpowers\{specs,plans}\   # 设计与计划（已有）
├── fpk\                              # fnpack 打包根目录
│   ├── manifest                      # INI 风格（x86/arm 两份差异仅 platform）
│   ├── config\
│   │   ├── privilege                 # {"defaults":{"run-as":"package"}}
│   │   └── resource                  # JSON
│   ├── cmd\
│   │   ├── main                      # 服务主入口：起 manager-server 并守护
│   │   ├── install_init / install_callback        # 建数据目录、首次配置
│   │   ├── upgrade_init / upgrade_callback        # 覆盖安装不动数据
│   │   ├── uninstall_init / uninstall_callback    # 停服务，保留数据
│   │   └── config_init / config_callback
│   ├── app\
│   │   ├── manager\                  # manager-server（Python 3 标准库）
│   │   │   ├── server.py             # 入口：HTTP 服务 + 路由
│   │   │   ├── engine.py             # llama-server 子进程管理
│   │   │   ├── hardware.py            # 硬件检测与变体选择
│   │   │   ├── models.py             # 模型扫描/下载/删除
│   │   │   ├── settings.py           # 配置持久化
│   │   │   └── downloader.py         # 下载任务（进度、断点续传）
│   │   ├── bin\cpu-x86_64\           # vendor 的 CPU 二进制（构建时填充）
│   │   └── ui\                       # desktop_uidir
│   │       ├── config                # {"type":"iframe","protocol":"http","url":"/cgi/ThirdParty/llamacpp/index.cgi/"}
│   │       ├── index.cgi             # bash CGI：静态直读 + /api/* 反代 127.0.0.1:18080
│   │       └── web\                   # 前端静态资源（index.html、css、js）
│   ├── wizard\                       # 空目录（v1 无向导）
│   ├── ICON.PNG / ICON_256.PNG
├── scripts\
│   ├── vendor-binaries.ps1           # 下载钉死 nightly CPU 双架构产物进 fpk\app\bin\
│   ├── build-fpk.ps1                 # x86/arm 各打一包 → dist-fpk\ + SHA256SUMS
│   └── sync-mirror-gitea.ps1         # nightly 全部资产上传 Gitea release（镜像）
├── tools\fnpack.exe                  # 官方打包工具 1.2.3（Windows amd64）
├── tests\                            # python unittest
└── dist-fpk\                          # 构建产物（gitignore）
```

钉死 nightly：**b12229**；下载 URL 模板：
`https://github.com/ggml-org/llama.cpp/releases/download/b12229/<file>`

## 1. 里程碑任务分解

### M1 fpk 骨架（打通 FPK 全链路）
1. 下载 fnpack 1.2.3 Windows 版 → `tools\fnpack.exe`
2. 脚手架 `fpk\` 全目录（见上）；manifest 用 category=utility（装机报错则回退 system）
3. `cmd\main`：bash 启动 `python3 app/manager/server.py`（后台、记 pid、前台 wait 守护）
4. manager-server 最小版：`GET /api/status` 返回 `{ok:true}`；静态托管 ui\web
5. `index.cgi`：静态文件直读 + `/api/*` curl 反代（参照 FN-AutoPower 模式）
6. 图标：.NET System.Drawing 生成原创简笔风格（256px + 缩放）
7. `scripts\vendor-binaries.ps1`：下载 `llama-b12229-bin-ubuntu-x64.tar.gz` 解出 llama-server → `fpk\app\bin\cpu-x86_64\`
8. `scripts\build-fpk.ps1`：manifest platform=x86 → `fnpack build` → `dist-fpk\`
9. **验收（用户真机操作）**：应用中心手动安装 → 桌面图标出现 → 点击打开页面（飞牛鉴权）→ `/api/status` 返回 OK；`ldd llama-server` 无缺库
   - 不刷新时 `systemctl restart trim_app_center.service`

### M2 引擎管理
- `engine.py`：启动/停止/重启、崩溃自动重启（可关）、进程组隔离、日志环形缓冲
- 参数拼装：模型、host/port、--ctx-size、-ngl、--api-key、线程数
- API：`GET /api/status`（完整）、`POST /api/engine/start|stop|restart`、`GET /api/engine/logs`
- 设置页：参数表单 + `GET/PUT /api/settings`
- 验收：真机起停 CPU 引擎、加载小模型、日志可见

### M3 模型与聊天
- 本地 GGUF 扫描（可配置目录，默认数据卷 models/）
- 聊天页 iframe 内嵌 `http://<host>:<port>/`；公网 HTTPS 上下文显示"请从内网访问"
- OpenAI API 局域网可用（用户 Chatbox 实测）

### M4 下载
- 硬件检测（nvidia-smi → CUDA 12.8/13.5；Vulkan ICD；CPU）+ 手动覆盖
- ModelScope / hf-mirror 搜索下载（适配器 + 降级）；断点续传、进度轮询
- 加速变体按需下载（GitHub 或 Gitea 镜像 base URL，同 `releases/download/<tag>/<file>` 路径）
- `scripts\sync-mirror-gitea.ps1`（Gitea API 上传资产）
- 验收：真机 CUDA 变体下载、运行、-ngl 生效（日志确认 GPU offload）

### M5 收尾
- ARM 包（vendor arm64 CPU 二进制 → platform=arm 构建）
- 覆盖升级保留数据实测、卸载保留数据实测、NAS 重启自恢复实测
- README（安装/卸载/端口/API 说明）

## 2. 提交与验收节奏

- 每个 M 完成即 commit + push（Gitea：`git push http://T366:<token>@.../llama.cpp-fnos.git main`，token 不入库）
- M1/M2/M3/M4 各含一次真机验收，需用户配合时以明确命令清单提出
- 风险项（设计文档 §6）逐项在真机闭环，任何偏差回报后再继续

## 3. 当前状态

- [x] Gitea 仓库 T366/llama.cpp-fnos 已建，main 已推送
- [ ] M1 进行中
