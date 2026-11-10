# llama.cpp 飞牛（fnOS）应用

在飞牛 fnOS 上以原生二进制（无 Docker）运行 [llama.cpp](https://github.com/ggml-org/llama.cpp)：提供 OpenAI 兼容 API、内置聊天界面、模型市场下载与硬件自适应加速，全部基于 Python 3 标准库与 bash 实现，不引入第三方运行时依赖。

## 功能

- 引擎管理：启动 / 停止 / 重启、崩溃自动重启（可关）、实时日志
- 模型市场：ModelScope / hf-mirror 搜索下载、断点续传、进度轮询；
  「本机推荐」按物理内存 + 显存预算过滤，只展示真实含 GGUF 的仓库
- 本地模型：扫描任意目录的 GGUF（默认数据卷 `models/`）
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

## 安装

1. 从 [Gitea Releases](http://192.168.0.114:3000/T366/llama.cpp-fnos/releases) 下载对应架构的 `.fpk`：
   - `llamacpp-<版本>-x86.fpk` —— x86_64 NAS
   - `llamacpp-<版本>-arm.fpk` —— arm64 NAS
2. 飞牛「应用中心 → 手动安装」上传 `.fpk` 文件。
3. 安装完成后桌面出现 llama.cpp 图标，点击进入管理页（走飞牛登录鉴权）。

> 校验：发布页附 `SHA256SUMS`，可用 `sha256sum` 核对下载文件。

## 快速上手

1. **获取模型**：进入「模型」页 —— 空关键词即「本机推荐」（每项标注
   可运行 / 勉强 与最小量化档位大小）；也可搜索关键词，或直接把 GGUF
   文件放到数据卷 `models/` 目录。
2. **启动引擎**：在「设置」页选择模型文件（或填绝对路径）→「状态」页点
   「启动」。首次启动前可在「加速」处确认/安装推荐的引擎变体。
3. **开始对话**：「聊天」页直接对话；或用任意 OpenAI 兼容客户端连接（见下）。

## 端口与访问

| 端口 | 用途 | 监听范围 |
| --- | --- | --- |
| 18080 | 管理后端（manager-server） | 仅 127.0.0.1，经 fnOS nginx 鉴权反代 |
| 8080 | llama-server 推理 API（OpenAI 兼容） | 默认 0.0.0.0，可在设置改为 127.0.0.1 |

- 管理页唯一入口：`http://<NAS_IP>/cgi/ThirdParty/llamacpp/index.cgi/`（需登录飞牛）。
- 推理 API 端口可在设置页修改（1024–65535）；绑定 `0.0.0.0` 时同一局域网内
  任何设备都可访问，如需收紧请改 `bind` 为 `127.0.0.1` 并设置 `api_key`。

### OpenAI 兼容 API

```bash
curl http://<NAS_IP>:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"default","messages":[{"role":"user","content":"你好"}]}'
```

客户端（如 Chatbox）配置：API 地址 `http://<NAS_IP>:8080/v1`，API Key 留空
（除非设置页已配置），模型名任意（llama-server 单模型服务）。

### 管理后端 API（JSON）

均经管理页入口反代（`/cgi/ThirdParty/llamacpp/index.cgi/api/...`）：

```
GET  /api/status                  引擎运行态 + 硬件摘要
GET  /api/hardware                硬件检测（POST redetect 重新检测）
POST /api/engine/start|stop|restart
GET  /api/engine/logs?tail=N      引擎日志尾部
GET  /api/variants                已装/可装引擎变体 + 推荐
POST /api/variants/install        按需下载安装变体（后台任务）
GET/PUT /api/settings             运行配置
GET  /api/models                  本地模型扫描
GET  /api/models/search?q=&source=&limit=    模型市场搜索
GET  /api/models/files?source=&repo=         仓库内 GGUF 列表
GET  /api/models/recommend?limit=            按本机内存推荐（仅含 GGUF 的仓库）
POST /api/models/download         市场模型下载（后台任务）
GET  /api/tasks  /api/tasks/<id>  任务列表 / 进度；POST /api/tasks/<id>/cancel
```

## 目录结构

| 路径 | 内容 |
| --- | --- |
| `/var/apps/llamacpp/target` | 应用本体（升级时被覆盖） |
| `/var/apps/llamacpp/var` | 数据目录（升级、卸载均保留） |
| `/var/apps/llamacpp/var/models` | 默认模型存放目录 |
| `/var/apps/llamacpp/var/settings.json` | 运行配置 |
| `/var/apps/llamacpp/var/manager.log` | 管理后端日志（1 MB 轮转） |

## 升级与卸载

- **升级**：应用中心对已装版本上传新版 `.fpk` 覆盖安装；模型、配置、日志
  全部保留（升级脚本不触碰数据目录）。
- **卸载**：卸载时自动停止服务并保留数据目录；重新安装后模型与配置原样可用。
- **彻底清理**：卸载后手动删除 `/var/apps/llamacpp/var`（内含模型文件，占用
  可能很大，请确认后再删）。

## GPU 加速

- x86 + NVIDIA：硬件检测会推荐 `cuda-12.8` / `cuda-13.5` 变体，在「加速」处
  点击安装（约 1–2 GB，含 cudart）；设置页将 `-ngl` 调为非 0（如 999 = 全部
  层进显存），启动后日志可见 GPU offload。
- Vulkan：无 CUDA 环境时可试 `vulkan-x86_64` / `vulkan-aarch64` 变体。
- CPU：默认变体，零配置；线程数 `0` 交由 llama.cpp 自动选择。

变体资产默认从 GitHub 下载（nightly `b12229` 钉死版本）；国内网络可把设置页
`download_source` 切为 `gitea` 并填 `mirror_base`（本仓库地址，release 附同
名资产）。

## 从源码构建（开发者）

```powershell
# 1. vendor llama.cpp nightly 二进制（x86_64 + aarch64 各一次）
python scripts\vendor-binaries.py
python scripts\vendor-binaries.py --arch aarch64

# 2. 打包（fpk\manifest 的 platform 字段决定目标架构）
powershell -ExecutionPolicy Bypass -File scripts\build-fpk.ps1          # x86
#   arm 包：先把 manifest platform 改为 arm，再：
powershell -ExecutionPolicy Bypass -File scripts\build-fpk.ps1 -Platform arm
```

产物输出到 `dist-fpk\`，同时按架构追加记录到 `SHA256SUMS`。

## 故障排查

| 现象 | 排查 |
| --- | --- |
| 安装后桌面无图标 | SSH 执行 `systemctl restart trim_app_center.service` 后刷新 |
| 启动失败：python3 未找到 | fnOS 需自带 python3；`which python3` 确认 |
| 引擎反复崩溃 | 「状态」页看日志；常见原因：推理端口被占用、模型路径不对、内存不足 |
| 下载变体失败 | 设置页把 `download_source` 切为 `gitea` 并填 `mirror_base` |
| 公网 HTTPS 下聊天页空白 | 浏览器混合内容限制，请从内网 HTTP 访问 |

## 许可证

- 本应用代码：MIT
- [llama.cpp](https://github.com/ggml-org/llama.cpp)：MIT（vendor 二进制取自官方 release）
- 内嵌前端库：[marked](https://github.com/markedjs/marked)（MIT）、
  [DOMPurify](https://github.com/cure53/DOMPurify)（Apache-2.0）
