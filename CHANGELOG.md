# 更新日志（Changelog）

本文件记录 llama.cpp 飞牛（fnOS）应用的版本变更，倒序排列。
详细设计背景见 `docs/superpowers/specs/`，里程碑计划见 `docs/superpowers/plans/`。

## [0.4.9] - 2026-11-12

- 新增 Embedding 向量模型运行支持：配置新增 `mode`（`chat` / `embedding`，
  默认 `chat`）与 `mmproj_model`（多模态投影 GGUF 路径）两个字段
- `mode = embedding` 时以 `--embedding` 启动 llama-server，仅暴露
  `/v1/embeddings` 接口，用于 RAG / 向量检索（如 EmbeddingGemma）；`--mmproj`
  路径非空时附加投影模型
- 设置页新增「运行模式」下拉与「mmproj 路径」输入框；状态页显示当前模式
  与 mmproj 加载状态；启动前校验 mmproj 文件存在
- 更新 README 配置字段表、API 调用示例与常见问题排查

## [0.4.8] - 2026-11-12

- 新增 `cuda-12.8-x86_64` GPU 变体：基于 llama.cpp b12220 以 CUDA 12.8 自建编译
  （含 cudart/cublas/nccl 运行库），面向 Vulkan 未覆盖或性能不理想的 NVIDIA
  显卡——尤其 Pascal 架构（GTX 10 系，CUDA 13 起官方不再支持，12.8 为最后可用版本）
- 新增 `scripts/build-cuda-nas.sh`：NAS Docker 自建编译链（增量编译、宿主真机
  验证通过才允许上传，令牌经 `GITEA_TOKEN` 环境变量注入）
- 变体资产上传 Gitea `b12220` release（主包 + cudart 包 + 源码包）

## [0.4.7] - 2026-11-11

- 修复引擎服务实际可用但状态页显示异常退出/启动中的问题（状态机判定修正）

## [0.4.6] - 2026-11-11

- 引擎二进制源从 b12229 降级到 `b12220`（ubuntu 22.04 基线），修复新版
  nightly 依赖 GLIBC_2.38 而 fnOS 系统库过低导致的无法启动
- `vendor-binaries.py`、变体清单同步钉死 b12220

## [0.4.5] - 2026-11-10

- 修复变体安装时下载源白名单误拒（GitHub 源无 extra 后缀时必然被拒的问题）
- 硬件检测按 GPU 架构代次推荐 CUDA 变体（Pascal 及更早推荐 cuda-12.8）

## [0.4.4] - 2026-11-10

- 模型市场「本机推荐」按本机物理内存 + 显存预算适配，标注可运行 / 勉强
  与最小量化档位大小；只展示真实含 GGUF 文件的仓库

## [0.4.1 - 0.4.3] - 2026-11-10

- 模型市场对标 LM Studio：卡片化列表、仓库详情双栏面板
- 详情 README 升级为 Markdown 富文本渲染（marked + DOMPurify）

## [0.4.0] - 2026-11-10（M4 里程碑）

- 硬件检测：CPU 架构、内存、NVIDIA GPU（nvidia-smi）、Vulkan ICD
- 加速变体按需下载安装（CPU / Vulkan / CUDA），断点续传
- 模型市场搜索下载（ModelScope / hf-mirror）、下载进度轮询、任务取消
- 下载安全：host 白名单 + 每跳重定向校验 + 原子落盘

## [0.3.0] - 2026-11-10（M3 里程碑）

- 本地模型扫描（默认数据卷 `models/`）
- 内置聊天页（iframe 内嵌 llama-server WebUI，仅内网）
- OpenAI 兼容 API 联通（`/v1/chat/completions`）

## [0.3.1] - 2026-11-10

- 修复聊天页 iframe 不显示的问题

## [0.2.0 - 0.2.2] - 2026-11-10（M2 里程碑）

- llama-server 子进程生命周期管理（启动 / 停止 / 重启、崩溃自动重启）
- 内存环形缓冲 + 落盘日志、启动前推理端口占用预检
- 运行参数设置（端口 / 上下文 / 线程 / GPU 层数 / API Key）

## [0.1.0] - 2026-11-09（M1 里程碑，真机验收通过）

- fpk 包骨架：manifest、生命周期脚本（install/upgrade/uninstall/config/main）、
  同源 CGI 入口、管理服务最小版、应用图标、vendor 与构建脚本
- Gitea release 首发 `llamacpp-0.1.0-x86.fpk`
