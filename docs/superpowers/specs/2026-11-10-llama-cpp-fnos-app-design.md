# llama.cpp 飞牛（fnOS）应用设计文档

日期：2026-11-10
状态：已与需求方对齐（方案 A），待 builder 检查

## 1. 意图与背景

需求方有一台飞牛 fnOS NAS（192.168.0.114，同机运行 Gitea 于 3000 端口）。希望在该 NAS 上本地运行大模型推理：

- 对局域网提供 **OpenAI 兼容 API**，供 Chatbox、沉浸式翻译、Home Assistant 等应用直接调用；
- 提供**带聊天界面和模型管理的 Web UI**；
- 应用以**飞牛应用中心正式安装包（.fpk）**交付：有桌面图标、安装即用、可完整卸载；
- **不使用 Docker**，llama.cpp 以原生二进制运行；
- 硬件**自动检测**（x86_64/ARM64、有无 NVIDIA GPU、有无 Vulkan），自动选择合适的 llama.cpp 构建，用户无需手动选镜像；
- 模型管理双通道：WebUI 内置**模型市场**（ModelScope / HuggingFace 搜索下载 GGUF），同时**扫描本地文件夹**识别用户手动放入的 GGUF；
- 界面以中文为主。

**二进制分发采用方案 A（混合）**：.fpk 只内置 CPU 版二进制（小包），首次启动时按硬件检测结果按需下载加速版（CUDA/Vulkan），下载源可配置为用户 Gitea 上的镜像，避免国内访问 GitHub 慢的问题。

代码托管在 NAS 本机 Gitea（192.168.0.114:3000），应用包在该 NAS 上安装测试。

## 2. 目标 / 非目标 / 反目标

### 目标
1. 应用中心 → 手动安装 .fpk 后，桌面出现应用图标，点击以飞牛窗口（iframe）打开，入口走飞牛登录鉴权；
2. 管理功能：llama-server 的启动/停止/重启、参数配置（推理端口、上下文长度、线程数、GPU 层数、API Key）、日志查看；
3. 模型管理：本地 GGUF 扫描（可指定目录，含飞牛共享文件夹）、模型市场搜索下载（带进度、断点续传）、删除；
4. 聊天：内嵌 llama-server 自带 WebUI（iframe），零维护成本；
5. OpenAI 兼容 API 监听局域网端口（默认 8080，可配置），可选 API Key；
6. 硬件自动检测 + 构建变体选择：NVIDIA → CUDA（12.8/13.5 按驱动版本选），Vulkan 可用 → Vulkan，否则 CPU；检测可手动重跑和覆盖；
7. x86_64 与 ARM64 双 .fpk 包（manifest 的 platform 字段区分，内容一致，各自内置对应架构的 CPU 二进制）；
8. 升级：覆盖安装保留数据；卸载默认保留模型和配置（文档说明手动清理路径）；
9. NAS 重启后服务自动恢复（fnOS 应用服务机制 + 上次运行状态记忆）。

### 非目标
- 不做微调、量化、转换工具的 UI（不暴露 llama-quantize 等）；
- 不做多用户/权限体系（飞牛登录鉴权已是唯一管理入口）；
- 不聚合外部 OpenAI 服务商（纯本地推理）；
- 不使用 Docker；
- v1 不做 llama.cpp 源码编译（只消费官方预编译产物）。

### 反目标（技术正确但产品失败即算失败）
- 需要在 NAS 上编译任何东西才算用起来；
- 以 root 运行或写系统文件；
- 管理接口绕过飞牛登录鉴权对外暴露；
- 安装卸载后有残留服务或开机自启项；
- 模型文件丢失（卸载默认保留）。

## 3. 约束与既有事实（调研结论）

### 3.1 fnOS / FPK 格式（参照 FN-AutoPower，MIT，github.com/wangwanjie/FN-AutoPower）
- 打包工具：官方 fnpack 1.2.3（developer.fnnas.com/docs/cli/fnpack/，提供 Windows x86 二进制，可在 Windows 开发机上运行）。
- 项目结构要求（fnpack build 前置校验）：

```
<app>/
├── manifest                  # INI 风格键值对
├── config/
│   ├── privilege             # JSON，如 {"defaults": {"run-as": "package"}}
│   └── resource              # JSON
├── cmd/
│   ├── main                  # 服务主入口（生命周期脚本，fnOS 负责守护）
│   ├── install_init / install_callback
│   ├── upgrade_init / upgrade_callback
│   ├── uninstall_init / uninstall_callback
│   └── config_init / config_callback
├── app/                      # 应用内容
│   └── ui/                   # desktop_uidir 指向的目录
│       ├── config           # 桌面入口 JSON
│       └── index.cgi        # 入口 CGI（见 3.2）
├── wizard/                   # 必须存在；v1 可不放向导
├── ICON.PNG
└── ICON_256.PNG
```

- manifest 实际字段（AutoPower 实例）：appname、version、display_name、desc、platform（x86|arm）、source=thirdparty、category、maintainer、maintainer_url、distributor、distributor_url、desktop_uidir=ui、desktop_applaunchname、service_port、checkport=false、ctl_stop=true、changelog。
- **同源 CGI 接入模式**（关键，来自 AutoPower 公网访问修复文档）：
  - `app/ui/config`：`{"type":"iframe","protocol":"http","url":"/cgi/ThirdParty/<appname>/index.cgi/"}`，**不写 port**；
  - 飞牛自带 nginx（/usr/trim/nginx）以 CGI 执行应用 `ui/index.cgi`，静态文件直接读，`/api/*` 反代到 `127.0.0.1:<service_port>`；
  - 该路径走**飞牛登录鉴权**；**绝不设置 `disable_authorization_path`**；
  - 内网（:5000）公网（:4433）访问均无需额外端口配置；
  - 后端只监听 127.0.0.1。
- 权限：`run-as: package` 普通用户运行（llama-server 不需要 root）；**不要**写 `username/groupname: root`（会触发用户创建冲突）。
- 应用安装路径约定：`/var/apps/<appname>/...`（AutoPower 实例为 /var/apps/autopower/target/AutoPower，**装机后以实际为准**）。
- 安装方式：应用中心 → 手动安装 .fpk；桌面入口不刷新时 `systemctl restart trim_app_center.service`。
- fnOS 为 Debian 系，自带 python3 与 curl；**避免 pip 第三方依赖**（AutoPower 的后端即 Python 3 标准库 HTTP server，纯标准库方案已实机验证可行）。
- 前端窗口可被拉得很矮：弹层必须处理最大高度与内部滚动、点击空白收起（AutoPower 踩坑记录）。
- 前端接口请求走相对路径（基于 document.baseURI），静态资源相对引用（在 /cgi/ThirdParty/.../index.cgi/ 子路径下正常工作）。

### 3.2 llama.cpp 官方预编译产物（MIT，github.com/ggml-org/llama.cpp）
- 正式 tag（如 v0.9.0）**不带**二进制；产物在 **nightly release**（tag 形如 b12229，每天更新，38 个资产）。
- Linux 相关资产（以 b12229 为例）：
  - `llama-bXXXX-bin-ubuntu-x64.tar.gz` / `-arm64`（CPU）
  - `llama-bXXXX-bin-ubuntu-vulkan-x64.tar.gz` / `-arm64`
  - `llama-bXXXX-bin-ubuntu-cuda-12.8-x64.tar.gz`、`-cuda-13.5-x64` / `-cuda-13.5-arm64`
  - `cudart-llama-bXXXX-bin-ubuntu-cuda-*.tar.gz`（CUDA 运行时库，**NAS 上无需安装 CUDA 工具链**）
- 选一个 nightly 版本**钉死（pin）**为已知可用版本；升级走"重新下载新 nightly"按钮，失败可回退。
- **风险**：ubuntu 构建的 glibc 依赖与 fnOS（Debian）的兼容性需装机实测（`ldd llama-server`）；不行则换更早 nightly，最坏情况在 NAS 上从源码编译（超出 v1 范围，见 6.1）。

### 3.3 模型下载源
- ModelScope（modelscope.cn）：国内主源，HTTP API 可搜索模型/列出文件。
- HuggingFace：国内经 hf-mirror.com 镜像访问；huggingface.co 直连作为可选项。
- 均为直接 HTTP 文件下载，支持 Range 断点续传。

### 3.4 开发/构建/托管环境
- 开发机：Windows（本仓库 d:\Project3\llama.cpp-fnos）；fnpack 有 Windows 版，可本地打包。
- 版本管理：NAS 本机 Gitea http://192.168.0.114:3000（token 已在需求方手中；repo 名建议 llama.cpp-fnos）。
- 真机验收：在 192.168.0.114 上手动安装 fpk 测试。

## 4. 架构

```
浏览器（飞牛桌面 / 局域网）
   │ 管理界面: /cgi/ThirdParty/llamacpp/index.cgi/   ← 飞牛 nginx，登录鉴权
   ▼
飞牛 nginx (/usr/trim/nginx) ──► ui/index.cgi (bash CGI)
   │  静态: 读 ui/ 文件          │  /api/*: 反代 127.0.0.1:18080
   ▼
┌─────────────────────────────────────────────┐
│ manager-server（Python 3 标准库，包用户运行） │
│  监听 127.0.0.1:18080（manifest service_port）│
│  · 硬件检测 / 变体选择与下载（含镜像源配置）   │
│  · llama-server 子进程生命周期守护            │
│  · 模型扫描 / 下载（进度、断点续传）/ 删除     │
│  · 配置持久化 / 日志环形缓冲                  │
│  · 静态页面托管（ui/ 下）                    │
└──────────────┬──────────────────────────────┘
               │ spawn / monitor / kill
               ▼
llama-server（官方预编译二进制，按检测结果选变体）
  监听 0.0.0.0:8080（可配置）—— OpenAI 兼容 API
  + 自带聊天 WebUI（应用内 iframe 内嵌）
```

### 4.1 组件
1. **manager-server**：Python 3 标准库（http.server + threading + subprocess + json + urllib），单文件或少量模块；无任何第三方依赖。
2. **llama-server**：由 manager-server 以子进程方式拉起（进程组隔离），崩溃自动重启（可开关）；启动参数由设置项拼装（模型路径、端口、--ctx-size、-ngl、--api-key、--host 等）。
3. **Web UI**：原生 HTML/CSS/JS，中文界面，SPA 式多页签：状态（引擎/硬件/当前模型）、模型（本地列表 + 市场下载）、设置（端口/参数/下载源/数据目录）、聊天（iframe 内嵌 llama-server UI）。
4. **fpk 包**：manifest + 生命周期脚本 + ui/（index.cgi 反代 + 静态页）+ 图标 + 内置 CPU 变体二进制（vendor 方式打进 app/ 下）。

### 4.2 数据与目录
- 安装目录（fnOS 管理，只读使用）：应用代码 + 内置 CPU 二进制。
- 数据目录（**默认在应用数据卷下自动创建**）：`models/`（GGUF）、`bin/<variant>/`（按需下载的加速版二进制 + cudart）、`config.json`、`logs/`、`downloads/`（.part 临时文件）。
- **模型目录可在设置页改为任意路径**（含飞牛共享文件夹），以覆盖"手动放 GGUF 到共享文件夹再扫描"的场景；切换即扫描新目录，不做拷贝。默认数据目录之外的路径，包用户读写权限以装机实测为准（共享文件夹通常对系统用户可读写）。
- 卸载：默认保留数据目录（卸载说明中告知路径）；升级：覆盖安装不动数据。

### 4.3 硬件检测与变体选择（manager-server 内实现）
- 架构：`platform.machine()` → x86_64 / aarch64。
- NVIDIA：`nvidia-smi --query-gpu=driver_version` 可用则读驱动版本：支持 CUDA 13.5（新驱动）→ cuda-13.5，否则支持 12.8 → cuda-12.8，否则退到 Vulkan/CPU（驱动版本-CUDA 支持表内置常量）。
- Vulkan：存在 `/usr/share/vulkan/icd.d/*.json`（或 vulkaninfo 可执行）→ vulkan 变体。
- 以上顺序择一；检测结果缓存，提供"重新检测"与手动覆盖下拉。
- ARM 包仅 CPU/Vulkan 两条路（不做 ARM CUDA）。
- 每次变体切换需重启 llama-server。

### 4.4 二进制获取（方案 A）
- 构建期：`scripts/vendor-binaries.(sh/ps1)` 从**钉死的 nightly**（或用户 Gitea 镜像同路径）下载 CPU x64/arm64 包，解出 `llama-server` 与其依赖库放入打包目录。
- **镜像维护交付物**：`scripts/sync-mirror-gitea`（用 Gitea API + token）把钉死 nightly 的全部所需资产（CPU/Vulkan/CUDA/cudart，x64+arm64）上传到用户 Gitea 上本仓库的 release，**沿用 GitHub `releases/download/<tag>/<file>` 路径约定**——运行期下载源解析逻辑对 GitHub 与 Gitea 镜像完全一致，仅 base URL 不同。镜像是否填充由用户自选（不填则运行期直接走 GitHub）。
- 运行期：用户在"引擎"页点选/自动建议下载加速变体 → manager 从配置的下载源（默认 GitHub release URL，可改 Gitea 镜像 base URL）下载对应 tar.gz + cudart 包 → 解压到 `bin/<variant>/` → 校验可执行（`--version`）→ 切换。
- 下载同样支持断点续传与进度查询；engine 升级 = 重新下载新版 nightly（记录版本号，保留旧变体目录可回退）。

### 4.5 manager API（/api/*，经 index.cgi 反代；JSON）
- `GET /api/status`：引擎运行态、当前变体与版本、已加载模型、端口、硬件摘要
- `GET /api/hardware`、`POST /api/hardware/redetect`
- `POST /api/engine/start|stop|restart`；`GET /api/engine/logs?tail=N`
- `GET /api/variants`（可用/已装变体）；`POST /api/variants/install`（body: variant, 可选 source）
- `GET /api/settings`、`PUT /api/settings`
- `GET /api/models`（扫描结果）；`DELETE /api/models`（body: path）
- `GET /api/models/search?q=&source=modelscope|hf`
- `POST /api/models/download`（body: source, repo, file）→ 返回任务 id
- `GET /api/tasks/<id>`（进度；**轮询**，不用 SSE——CGI 反代下流式不可靠）
- 聊天页 iframe 直连 `http://<NAS主机>:<推理端口>/`
- **聊天仅内网可用**（已定）：公网 HTTPS 打开飞牛桌面时，聊天页检测到非内网/HTTPS 上下文即显示"请从内网访问聊天"提示，不做 llama-server 反代；推理 API 本身公网可达性与否由用户端口映射决定，与聊天页无关

### 4.6 安全
- manager 只听 127.0.0.1；唯一入口 /cgi/ThirdParty/llamacpp/ 走飞牛登录鉴权；不设 disable_authorization_path。
- llama-server 推理端口默认 0.0.0.0:8080（局域网可用是核心需求），设置页提供：绑定 127.0.0.1 选项、API Key（llama-server 原生 --api-key）；安装说明中明示"开 API Key 即可防局域网滥用"。
- 下载仅允许 https，目标 host 限制在配置的白名单（github.com / objects.githubusercontent.com / 用户镜像域 / modelscope.cn / hf-mirror.com / huggingface.co）。
- 写接口只接受 application/json（防跨站表单），请求体限制 1MB。
- 以包用户运行，不写系统文件。

## 5. 技术栈与构建

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | Python 3 标准库 | fnOS 自带 python3；AutoPower 已验证纯标准库 FPK 应用可行；无 pip 依赖 |
| 前端 | 原生 HTML/CSS/JS + 少量原生模块化 | 零构建链；窗口适配（矮窗口/弹层滚动）要求自己可控 |
| 推理核心 | llama.cpp 官方 nightly 预编译 | 上游持续维护，MIT |
| 打包 | fnpack 1.2.3 | 官方工具；Windows 可用 |
| 测试 | python unittest + node --check + 真机清单 | 同 AutoPower 实践 |

构建流水（Windows 开发机）：`scripts/vendor-binaries`（下载钉死 nightly CPU 双架构产物）→ 拷贝源码进 fpk 模板 → `fnpack build`（x86 / arm 各一次，仅 platform 字段差异）→ 产出 `dist-fpk/llamacpp-<ver>-x86.fpk` 与 `-arm.fpk` + SHA256SUMS。

## 6. 风险与待装机验证项（builder 必须在真机上过一遍）

**目标真机：192.168.0.114 = x86_64 + NVIDIA 独显（已确认）**——x86 包为主验收对象，CUDA 路径可真机端到端验证；ARM 包无法在该机验证，按代码审查 + 结构一致性交付。

1. **glibc 兼容**：ubuntu-x64/arm64 nightly 产物在 fnOS 上 `ldd llama-server` 是否缺库/版本不足；失败则试更早 nightly（如 b12xxx 系列），再失败记录并上报（此时才讨论源码编译方案）。
2. **安装路径与数据目录**：/var/apps 实际布局、可写数据卷路径的获取方式（环境变量/约定），以装机实测为准。
3. **manifest category 合法值**：AutoPower 用 system；本应用尝试 ai/utility 类（装机报错则以 system 兜底）。
4. **index.cgi 反代**：借 AutoPower 的 bash CGI 模式（静态 + curl 反代 /api/*），装机验证 POST/JSON/大响应体。
5. **CGI 下轮询频率**：index.cgi 每次请求都起进程，轮询间隔 ≥1s，日志尾部接口加节流。
6. **ModelScope / hf-mirror API 形态**：以实现时实测为准封装成小适配器；搜索失败降级另一源。
7. **CUDA 真机验收**：nvidia-smi 驱动版本 → CUDA 12.8/13.5 变体选择逻辑实测；cudart 包解压后运行；`-ngl` 生效（日志确认 GPU offload 层数与显存占用）。

## 7. 里程碑
- M1 fpk 骨架：空 manager + ui + 图标 + manifest，装机出桌面图标，入口鉴权可达（打通全部 FPK 链路）
- M2 引擎：CPU 变体启动/停止/重启、状态、日志、参数设置
- M3 模型与聊天：本地扫描、iframe 聊天、OpenAI API 对外可用（Chatbox 实测）
- M4 下载：模型市场（ModelScope/hf-mirror）、加速变体按需下载（含镜像源配置）
- M5 收尾：ARM 包、覆盖升级/卸载数据保留实测、重启自恢复、文档

## 8. 留给 builder 的自由裁量
- API 字段命名与响应包装（保持一致性即可）
- 页面布局与视觉（中文、贴近飞牛系统观感、弹层限高滚动）
- index.cgi 具体实现（遵循 4.6 与 3.1 的模式约束）
- 单元测试用例细节
- 图标设计（原创简笔风格，避免直接搬运 llama.cpp 商标素材）

## 9. 需求方已定决策记录
- 不使用 Docker（原生二进制，方案 A 分发）
- 管理走飞牛鉴权；推理 API 独立局域网端口
- 聊天复用 llama-server 自带 WebUI（iframe），不自研聊天界面
- **聊天仅内网可用**；公网 HTTPS 下显示提示，不做 llama-server 反代
- 卸载默认保留数据；中文界面
- 代码托管于 192.168.0.114:3000 Gitea
- **目标真机 192.168.0.114 为 x86_64 + NVIDIA 独显**；x86 包主验收，CUDA 路径真机端到端验证
- 数据目录默认在应用数据卷自动创建；模型目录可在设置页改指向共享文件夹（builder 拍板，需求方可否决）
- Gitea 镜像由本项目提供 `scripts/sync-mirror-gitea` 同步脚本，沿用 GitHub release 路径约定；是否填充镜像由需求方自选（builder 拍板，需求方可否决）
