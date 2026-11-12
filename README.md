# llama.cpp 飞牛（fnOS）应用

在飞牛 fnOS 上以原生二进制（无 Docker）运行 [llama.cpp](https://github.com/ggml-org/llama.cpp)：提供 OpenAI 兼容 API、内置聊天界面、模型市场下载与硬件自适应加速。

- **零第三方运行时依赖**：管理后端与前端全部基于 Python 3 标准库（无 pip、无 Node、无 Docker），二进制取自 llama.cpp 官方 release。
- **面向国内网络**：模型市场对接 ModelScope / hf-mirror，变体资产支持 Gitea 镜像，下载全部支持断点续传。
- **硬件自适应**：自动检测 CPU 架构 / 内存 / NVIDIA GPU / Vulkan ICD，推荐并一键安装对应加速变体。

> 当前版本 0.4.9，引擎二进制钉死 llama.cpp `b12220`（ubuntu 22.04 基线，兼容 fnOS 的 GLIBC）。
> 版本历史见 [CHANGELOG.md](CHANGELOG.md)。

## 功能

- 引擎管理：启动 / 停止 / 重启、崩溃自动重启（可关）、实时日志、启动前端口预检
- 模型市场：ModelScope / hf-mirror 搜索下载、断点续传、进度轮询；「本机推荐」
  按物理内存 + 显存预算过滤，只展示真实含 GGUF 的仓库
- 本地模型：扫描任意目录的 GGUF（默认数据卷 `models/`），支持删除
- Embedding 模式：运行向量模型（如 EmbeddingGemma），暴露 `/v1/embeddings`
  接口，可用于 RAG / 向量检索；支持 mmproj 多模态投影模型
- 加速变体：按硬件检测推荐 CPU / Vulkan / CUDA（12.8 / 13.5）变体，按需下载安装
- OpenAI 兼容 API：局域网内 Chatbox、Open WebUI 等客户端可直接接入
- 硬件检测：CPU 架构、内存、NVIDIA GPU（nvidia-smi）、Vulkan ICD

## 系统要求

| 项目 | 要求 |
| --- | --- |
| 系统 | 飞牛 fnOS（x86_64 或 arm64） |
| Python | 系统 `python3`（fnOS 自带，无 pip 依赖） |
| 内存 | 与所加载模型大小相关，建议 ≥ 8 GB |
| GPU（可选） | NVIDIA 显卡（CUDA 12.8 / 13.5 变体）；其余 GPU 可试 Vulkan 变体 |

## 安装（NAS 侧）

1. 从 [Gitea Releases](http://192.168.0.114:3000/T366/llama.cpp-fnos/releases) 下载对应架构的 `.fpk`：
   - `llamacpp-<版本>-x86.fpk` —— x86_64 NAS
   - `llamacpp-<版本>-arm.fpk` —— arm64 NAS
2. 飞牛「应用中心 → 手动安装」上传 `.fpk` 文件。
3. 安装完成后桌面出现 llama.cpp 图标，点击进入管理页（走飞牛登录鉴权）。

> 校验：发布页附 `SHA256SUMS`，可用 `sha256sum` 核对下载文件。

## 使用指南

### 快速上手

1. **获取模型**：进入「模型」页 —— 空关键词即「本机推荐」（每项标注
   可运行 / 勉强 与最小量化档位大小）；也可搜索关键词，或直接把 GGUF
   文件放到数据卷 `models/` 目录。
2. **启动引擎**：在「设置」页选择模型文件（或填绝对路径）→「状态」页点
   「启动」。首次启动前可在「加速」处确认/安装推荐的引擎变体。
3. **开始对话**：「聊天」页直接对话；或用任意 OpenAI 兼容客户端连接（见下）。

### 管理页五个标签

| 标签 | 功能 |
| --- | --- |
| 状态 | 引擎运行态（运行中 / 已停止 / 启动中…）、启动 / 停止 / 重启按钮、硬件摘要、推荐变体 |
| 模型 | 市场搜索（ModelScope / hf）、本机推荐、仓库内 GGUF 列表、下载进度、本地模型扫描与删除 |
| 聊天 | 内置对话页（iframe 内嵌 llama-server WebUI，仅内网可访问） |
| 日志 | llama-server 实时日志尾部（含崩溃原因、GPU offload 信息） |
| 设置 | 推理端口 / 绑定地址 / 运行模式（对话或 Embedding）/ 上下文长度 / 线程数 / GPU 层数 / API Key / 模型路径 / mmproj 路径 / 变体 / 下载源等 |

### 运行配置字段（config.json）

设置页与 `PUT /api/settings` 共用以下字段，存储于数据目录 `config.json`
（未知字段忽略、损坏自动回退默认值）：

| 字段 | 类型 / 默认值 | 说明 |
| --- | --- | --- |
| `inference_port` | int / `8080` | OpenAI 兼容 API 监听端口（1024–65535） |
| `bind` | str / `0.0.0.0` | 监听地址，仅支持 `0.0.0.0`（局域网）或 `127.0.0.1`（本机） |
| `ctx_size` | int / `4096` | 上下文长度（256–1048576） |
| `threads` | int / `0` | 计算线程数，0 = llama.cpp 自动 |
| `ngl` | int / `0` | GPU offload 层数（0–999，999 ≈ 全部层进显存；CPU 变体保持 0） |
| `api_key` | str / `""` | 为空不启用鉴权；非空作为 llama-server `--api-key` |
| `model` | str / `""` | 当前 GGUF 模型绝对路径 |
| `mode` | str / `chat` | 运行模式：`chat` = 对话模型；`embedding` = 向量模型（`--embedding`，仅 `/v1/embeddings`） |
| `mmproj_model` | str / `""` | 多模态投影 GGUF 路径（`--mmproj`）；留空不加载 |
| `variant` | str / `""` | 引擎变体目录名；空 = 按硬件检测自动选择 |
| `auto_restart` | bool / `true` | 崩溃自动重启（最多连续 5 次，退避 2/5/10/30/60s） |
| `models_dir` | str / `""` | 模型扫描目录；空 = 数据目录下 `models/` |
| `download_source` | str / `github` | 变体资产源：`github` 或 `gitea`（镜像） |
| `mirror_base` | str / `""` | Gitea 镜像 base，如 `http://<NAS_IP>:3000/T366/llama.cpp-fnos` |
| `market_source` | str / `modelscope` | 模型市场默认源：`modelscope` 或 `hf` |

> 引擎运行中修改端口 / 参数后需重启引擎生效。

### 端口与访问

| 端口 | 用途 | 监听范围 |
| --- | --- | --- |
| 18080 | 管理后端（manager-server） | 仅 127.0.0.1，经 fnOS nginx 鉴权反代 |
| 8080 | llama-server 推理 API（OpenAI 兼容） | 默认 0.0.0.0，可在设置改为 127.0.0.1 |

- 管理页唯一入口：`http://<NAS_IP>/cgi/ThirdParty/llamacpp/index.cgi/`（需登录飞牛）。
- 推理 API 端口可在设置页修改（1024–65535）；绑定 `0.0.0.0` 时同一局域网内
  任何设备都可访问，如需收紧请改 `bind` 为 `127.0.0.1` 并设置 `api_key`。

### OpenAI 兼容 API（推理服务）

```bash
curl http://<NAS_IP>:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"default","messages":[{"role":"user","content":"你好"}]}'
```

客户端（如 Chatbox）配置：API 地址 `http://<NAS_IP>:8080/v1`，API Key 留空
（除非设置页已配置），模型名任意（llama-server 单模型服务）。

Embedding 模式（`mode = embedding`）下仅暴露向量接口：

```bash
curl http://<NAS_IP>:8080/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"default","input":"今天天气怎么样"}'
```

> 向量模型（如 [unsloth/embeddinggemma-2-GGUF](https://www.modelscope.cn/models/unsloth/embeddinggemma-2-GGUF)）
> 可通过模型市场搜索下载；Embedding 模式下聊天页与 `/v1/chat/completions`
> 不可用，切回 `chat` 模式并重启即可恢复对话。

### 管理后端 API（JSON）

均经管理页入口反代（`/cgi/ThirdParty/llamacpp/index.cgi/api/...`），返回
JSON；错误统一为 `{"ok": false, "error": "<原因>"}`。请求体（POST/PUT/DELETE）
需 `Content-Type: application/json`，上限 1 MB。

| 方法 | 路径 | 参数 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/status` | — | 应用版本、引擎运行态、硬件摘要、推荐变体 |
| GET | `/api/hardware` | — | 硬件检测详情（结果缓存） |
| POST | `/api/hardware/redetect` | — | 重新检测硬件 |
| POST | `/api/engine/start` `/stop` `/restart` | — | 引擎启停 |
| GET | `/api/engine/logs` | `tail=N` | 引擎日志尾部（默认 200 行） |
| GET | `/api/settings` | — | 读取运行配置 |
| PUT | `/api/settings` | 任意字段子集 | 校验并合并写入（非法字段名 / 类型 / 范围返回 400） |
| GET | `/api/models` | — | 本地模型扫描（大小 / 参数量提示） |
| DELETE | `/api/models` | `{"path": "..."}` | 删除本地模型文件（仅限扫描目录内） |
| GET | `/api/models/search` | `q` `source` `limit` | 模型市场搜索（source: `modelscope` / `hf`） |
| GET | `/api/models/files` | `source` `repo` | 仓库内 GGUF 文件列表（含大小） |
| GET | `/api/models/detail` | `source` `repo` | 仓库详情（含 README Markdown，用于详情页渲染） |
| GET | `/api/models/recommend` | `limit` | 按本机内存 / 显存预算推荐（仅含 GGUF 的仓库） |
| POST | `/api/models/download` | `{"repo","file","source"?}` | 后台任务：市场模型下载 |
| GET | `/api/variants` | — | 已装 / 可装引擎变体 + 本机推荐 |
| POST | `/api/variants/install` | `{"variant","source"?}` | 后台任务：下载安装引擎变体 |
| GET | `/api/tasks` | — | 后台任务列表 |
| GET | `/api/tasks/<id>` | — | 单任务进度（前端 ≥1s 轮询） |
| POST | `/api/tasks/<id>/cancel` | — | 取消任务（保留 `.part` 支持续传） |

## 目录结构

### NAS 部署目录

| 路径 | 内容 |
| --- | --- |
| `/var/apps/llamacpp/target` | 应用本体（升级时被覆盖） |
| `/var/apps/llamacpp/var` | 数据目录（升级、卸载均保留） |
| `/var/apps/llamacpp/var/models` | 默认模型存放目录 |
| `/var/apps/llamacpp/var/config.json` | 运行配置 |
| `/var/apps/llamacpp/var/logs/llama-server.log` | 引擎日志（超 5 MB 轮转） |
| `/var/apps/llamacpp/var/manager.log` | 管理后端日志（1 MB 轮转） |

### 仓库代码结构

```
fpk/                        # fpk 应用包（打包最小结构）
  manifest                  # 包清单（版本 / 平台 / 资源 / 端口声明）
  ICON.PNG / ICON_256.PNG   # 应用图标（scripts/make-icons.ps1 生成）
  cmd/                      # 生命周期脚本：main(install/upgrade/uninstall/config)
  config/                   # privilege / resource 声明
  app/
    manager/                # 管理后端（纯 Python 3 标准库，模块见下表）
    ui/                     # index.cgi 入口 + 单页前端（web/index.html）
    bin/                    # vendor 的 llama.cpp 二进制（不入 git，构建时下载填充）
scripts/                    # 构建与工具脚本（见「开发者指南」）
docs/superpowers/           # 设计文档（specs）与实施计划（plans）
```

管理后端模块（`fpk/app/manager/`，共 9 个，全部纯标准库）：

| 模块 | 职责 |
| --- | --- |
| `server.py` | HTTP 路由 / JSON 编解码 / 入口参数（HTTPServer + Threading） |
| `engine.py` | llama-server 子进程生命周期、崩溃守护、日志环形缓冲 |
| `settings.py` | `config.json` 持久化（原子写入 + 类型/范围校验） |
| `hardware.py` | CPU / 内存 / NVIDIA GPU / Vulkan 检测与变体推荐 |
| `variants.py` | 变体清单、资产 URL、下载安装 |
| `models.py` | 本地 GGUF 扫描、安全删除 |
| `market.py` | ModelScope / HF-mirror 搜索、文件列表、详情 |
| `downloader.py` | HTTPS 断点续传下载器（host 白名单 + 每跳重定向校验） |
| `tasks.py` | 后台任务注册表（daemon 线程、进度、取消） |

## 升级与卸载

- **升级**：应用中心对已装版本上传新版 `.fpk` 覆盖安装；模型、配置、日志
  全部保留（升级脚本不触碰数据目录）。
- **卸载**：卸载时自动停止服务并保留数据目录；重新安装后模型与配置原样可用。
- **彻底清理**：卸载后手动删除 `/var/apps/llamacpp/var`（内含模型文件，占用
  可能很大，请确认后再删）。

## GPU 加速

- x86 + NVIDIA：硬件检测会推荐 `cuda-12.8` / `cuda-13.5` 变体（Pascal 及更早
  架构建议 cuda-12.8），在「加速」处点击安装（约 1–2 GB，含 cudart）；设置页
  将 `ngl` 调为非 0（如 999 = 全部层进显存），启动后日志可见 GPU offload。
- Vulkan：无 CUDA 环境时可试 `vulkan-x86_64` / `vulkan-aarch64` 变体。
- CPU：默认变体，零配置；线程数 `0` 交由 llama.cpp 自动选择。

变体资产默认从 GitHub 下载（钉死 `b12220`）；国内网络可把设置页
`download_source` 切为 `gitea` 并填 `mirror_base`（本仓库地址，release 附同
名资产）。

## 常见问题排查

| 现象 | 排查步骤 |
| --- | --- |
| 安装后桌面无图标 | SSH 执行 `systemctl restart trim_app_center.service` 后刷新 |
| 启动失败：python3 未找到 | fnOS 需自带 python3；`which python3` 确认 |
| 启动引擎提示端口被占用 | 改设置页 `inference_port`，或找出占用进程：`ss -tlnp \| grep 8080` |
| 引擎反复崩溃 | 「日志」页看末尾报错；常见原因：模型路径不对、内存不足、上下文过大（调小 `ctx_size`）；自动重启连续 5 次后停止 |
| 引擎实际在跑但状态显示异常 | 重启应用（管理后端状态机修复）；0.4.7 已修复已知场景 |
| 下载变体失败 | 国内网络把 `download_source` 切为 `gitea` 并填 `mirror_base`；中断后重新发起会自动从 `.part` 续传 |
| 模型下载失败 / 任务卡住 | 「模型」页任务进度可取消后重试；`.part` 文件保留续传；确认磁盘空间足够 |
| 引擎日志报 GLIBC 版本错误 | 本应用已钉死 ubuntu 22.04 基线的 `b12220` 二进制；若自行更换请勿使用更新的 nightly |
| Embedding 模式下聊天不可用 | 属正常：`--embedding` 仅暴露 `/v1/embeddings`；需对话时切回 `chat` 模式并重启引擎 |
| 启动失败提示 mmproj 文件不存在 | 检查设置页 `mmproj_model` 路径；不需要多模态时清空该字段 |
| 公网 HTTPS 下聊天页空白 | 浏览器混合内容限制，请从内网 HTTP 访问 |
| NAS 配置了 `http_proxy` 后引擎状态误报不可用 | 应用已对 127.0.0.1 健康探测绕过系统代理；若仍异常请检查代理脚本是否劫持 localhost |
| 配置改错想恢复 | 删除 `config.json` 后重启应用，自动回退默认值 |
| GPU 加速未生效 | 确认已安装对应变体且 `ngl` > 0；`nvidia-smi` 确认驱动正常；日志中查找 `GPU offload` |

## 开发者指南

### 开发环境配置

| 项 | 要求 |
| --- | --- |
| 操作系统 | Windows（PowerShell 5.1+）/ Linux / macOS 均可 |
| Python | 3.8+（全部脚本仅用标准库，无需 pip 安装任何依赖） |
| fpk 打包 | 无需 fnpack.exe —— 官方打包器易被杀软拦截，本仓库用纯 Python 实现（`scripts/pack-fpk.py`，打包格式经逆向验证与 fnpack 一致） |
| 网络 | 首次构建需访问 GitHub 下载 vendor 二进制（也可 `--base` 指向镜像） |

```powershell
git clone http://192.168.0.114:3000/T366/llama.cpp-fnos.git
cd llama.cpp-fnos
python -m py_compile fpk\app\manager\*.py   # 快速语法自检（可选）
```

### 从源码构建

```powershell
# 1. vendor llama.cpp 钉死版二进制（x86_64 + aarch64 各一次；约 60MB/架构）
python scripts\vendor-binaries.py                          # x86_64
python scripts\vendor-binaries.py --arch aarch64           # arm64

# 2. 打包（fpk\manifest 的 platform 字段决定目标架构）
powershell -ExecutionPolicy Bypass -File scripts\build-fpk.ps1          # x86
#   arm 包：先把 manifest platform 改为 arm，再：
powershell -ExecutionPolicy Bypass -File scripts\build-fpk.ps1 -Platform arm
```

产物输出到 `dist-fpk\`（`llamacpp-<版本>-<平台>.fpk` + 追加式 `SHA256SUMS`）。
`fpk/app/bin/` 不入 git，换机构建需先执行第 1 步。

### 工具脚本

| 脚本 | 用途 |
| --- | --- |
| `vendor-binaries.py` | 从 GitHub release 下载指定 tag 的 CPU 二进制并填充 `fpk/app/bin/`（`--arch` x86_64 / aarch64，`--tag` 默认 b12220） |
| `pack-fpk.py` | 纯 Python fpk 打包器：`python pack-fpk.py --dir fpk --out <路径>.fpk` |
| `build-fpk.ps1` | 一键打包（结构校验 → pack-fpk.py → dist-fpk + SHA256SUMS） |
| `make-icons.ps1` | 用 .NET System.Drawing 生成应用图标（原创简笔羊驼） |
| `sync-mirror-gitea.ps1` | 把 GitHub release 资产镜像到 Gitea（-Token 必填，Tag 默认与 PINNED_TAG 一致） |
| `build-cuda-nas.sh` | 在 NAS 上用 Docker 自建 CUDA 变体编译链（需 `export GITEA_TOKEN`；`UPLOAD=1` 时验证通过才上传） |

### 发布流程

1. 改 `fpk/manifest` 版本号与 `fpk/app/manager/server.py` 的 `APP_VERSION`；
2. `build-fpk.ps1` 分别构建 x86 / arm；
3. 在 Gitea 新建 release，上传两个 `.fpk` 与 `SHA256SUMS`；
4. 如需变体镜像同步，运行 `sync-mirror-gitea.ps1`。

## 许可证

- 本应用代码：MIT
- [llama.cpp](https://github.com/ggml-org/llama.cpp)：MIT（vendor 二进制取自官方 release）
- 内嵌前端库：[marked](https://github.com/markedjs/marked)（MIT）、
  [DOMPurify](https://github.com/cure53/DOMPurify)（Apache-2.0）
