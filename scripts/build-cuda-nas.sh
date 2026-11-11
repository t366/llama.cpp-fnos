#!/usr/bin/env bash
# llama.cpp b12220 CUDA 12.8 变体 NAS Docker 编译脚本
# 用法（SSH 到 NAS）：
#   bash build-cuda-nas.sh                  # 编译 + 宿主验证
#   UPLOAD=1 bash build-cuda-nas.sh         # 编译 + 验证 + 上传 Gitea（验证通过才上传）
# 可调（环境变量）：WORK / CUDA_ARCH=61 / JOBS / DOCKER_IMAGE / SUDO_DOCKER
# 说明：
# - 源码与编译目录挂载在宿主 $WORK/src，容器销毁不丢产物；重跑时若
#   build/bin/llama-server 已存在则跳过编译直接打包（增量模式）
# - 编译容器无 NVIDIA 驱动：容器内 --version 借 stub 库运行，失败不致命；
#   真正的运行验证在宿主 NAS（有驱动）上进行，通过后才允许上传
set -euo pipefail

TAG=b12220                       # 与 variants.py PINNED_TAG 一致
GITEA_RAW=http://192.168.0.114:3000/T366/llama.cpp-fnos
GITEA_API=http://192.168.0.114:3000/api/v1/repos/T366/llama.cpp-fnos
TOKEN=REDACTED_GITEA_TOKEN

WORK=${WORK:-$(pwd)/llamacpp-cuda-build}
CUDA_ARCH=${CUDA_ARCH:-61}
JOBS=${JOBS:-$(nproc)}
DOCKER_IMAGE=${DOCKER_IMAGE:-nvidia/cuda:12.8.1-devel-ubuntu22.04}
UPLOAD=${UPLOAD:-0}
DOCK=${SUDO_DOCKER:-}

PKG_MAIN=llama-${TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz
PKG_CUDART=cudart-llama-${TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz
SRC_TGZ=src-llama.cpp-${TAG}.tar.gz

mkdir -p "$WORK/out" && cd "$WORK"
$DOCK docker pull "$DOCKER_IMAGE"
if [ ! -s "$SRC_TGZ" ]; then
  echo "==> 下载源码包 $SRC_TGZ"
  curl -fL --retry 3 -o "$SRC_TGZ" "$GITEA_RAW/releases/download/$TAG/$SRC_TGZ"
fi

cat > compile.sh <<'COMPILE_EOF'
#!/bin/bash
set -euo pipefail
echo "==> 容器内：安装编译依赖"
apt-get update -qq && apt-get install -y -qq build-essential cmake curl >/dev/null
# stubs 目录只有 libcuda.so（无版本后缀），ld 解析 DT_NEEDED "libcuda.so.1"
# 需要同名文件，补一个别名软链（幂等）
ln -sf libcuda.so /usr/local/cuda/lib64/stubs/libcuda.so.1

mkdir -p /work/src
if [ ! -d /work/src/tools ]; then
  echo "==> 解压源码"
  tar xzf /work/src-llama.cpp-*.tar.gz --strip-components=1 -C /work/src
fi
cd /work/src

if [ ! -x build/bin/llama-server ]; then
  echo "==> cmake 配置（CUDA arch $CUDA_ARCH, LLAMA_CURL=OFF）"
  # libggml-cuda 链接 libcuda stub（SONAME=libcuda.so.1），链接 llama-server
  # 主程序时需 rpath-link 指到 stubs 让 ld 解析传递依赖；运行时真 libcuda.so.1
  # 由宿主 NVIDIA 驱动提供，stub 仅链接/验证期使用
  cmake -B build -DCMAKE_BUILD_TYPE=Release \
    -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH" \
    -DLLAMA_CURL=OFF \
    -DCMAKE_EXE_LINKER_FLAGS="-Wl,-rpath-link,/usr/local/cuda/lib64/stubs -Wl,-rpath-link,/usr/local/cuda/lib64"
  echo "==> 编译 llama-server（nvcc 编译 ggml-cuda 较慢，耐心等待）"
  cmake --build build --target llama-server -j"${JOBS:-$(nproc)}"
else
  echo "==> 检测到已有编译产物，跳过编译（增量模式）"
fi

echo "==> 容器内验证 --version（无 GPU，借 stub 库运行；失败不致命）"
LD_LIBRARY_PATH=build/bin:/usr/local/cuda/lib64/stubs ./build/bin/llama-server --version \
  || echo "!! 容器内无 NVIDIA 驱动，运行验证跳过（属预期）；宿主验证见下"

echo "==> 收集 llama-server 依赖闭包，拆分打包（tar -h 按 SONAME 实体平铺）"
# 打包策略（通用排除，不假设 toolkit 库的具体安装路径）：
# ldd 完整闭包 - 主包自带的 build/bin 库 - stub 目录 - 系统 C/C++ 运行库
# = CUDA 附加库（cudart/cublas/cublasLt/nccl，可能位于 /usr/local/cuda/lib64
#   或 /usr/lib/x86_64-linux-gnu，取决于镜像内安装方式），全部进 cudart 包
rm -rf /work/out/cudart && mkdir -p /work/out/cudart
cd /work/src/build/bin
LD_LIBRARY_PATH=/work/src/build/bin:/usr/local/cuda/lib64:/usr/local/cuda/lib64/stubs \
  ldd llama-server > /work/out/ldd.txt || true
echo "---- llama-server ldd 闭包 ----"
cat /work/out/ldd.txt
CUDA_DEPS=$( (awk '/=> \//{print $3}' /work/out/ldd.txt \
  | grep -v '^/work/src/build/bin/' \
  | grep -v '/stubs/' \
  | grep -vE '/(ld-linux|libc\.so|libm\.so|libdl\.so|libpthread\.so|librt\.so|libstdc\+\+\.so|libgcc_s\.so)' \
  | sort -u) || true )
if [ -z "$CUDA_DEPS" ]; then
  echo "!! 未收集到 CUDA 运行时库（ldd 输出见上），终止"
  exit 1
fi
echo "CUDA 运行时库："
for f in $CUDA_DEPS; do echo "  $f"; cp -L "$f" /work/out/cudart/; done
# b12220 的 llama-server 实现已拆分到 libllama-server-impl.so（运行时加载），
# 必须与 ggml 库一并入包；用 *.so* 兜底，避免运行时依赖逐个漏打包
tar czhf "/work/out/$PKG_MAIN" llama-server *.so*
cd /work/out/cudart
tar czhf "/work/out/$PKG_CUDART" *.so*
echo "==> 容器内完成："
ls -lh /work/out/*.tar.gz
COMPILE_EOF

echo "==> 启动编译容器（时长取决于 NAS CPU，约 30~120 分钟）"
$DOCK docker run --rm \
  -v "$PWD:/work" \
  -e CUDA_ARCH="$CUDA_ARCH" -e JOBS="$JOBS" \
  -e PKG_MAIN="$PKG_MAIN" -e PKG_CUDART="$PKG_CUDART" \
  "$DOCKER_IMAGE" bash /work/compile.sh

echo "==> 宿主验证：解包双包并运行 --version（NAS 有 NVIDIA 驱动，这才是真验证）"
rm -rf verify && mkdir -p verify
tar xzf "out/$PKG_MAIN"   -C verify
tar xzf "out/$PKG_CUDART" -C verify
LD_LIBRARY_PATH="$PWD/verify" ./verify/llama-server --version

if [ "$UPLOAD" = "1" ]; then
  for f in "out/$PKG_MAIN" "out/$PKG_CUDART"; do
    name=$(basename "$f")
    echo "==> 上传 $name"
    curl -sS --retry 3 \
      -H "Authorization: token $TOKEN" \
      -F "attachment=@$f" \
      "$GITEA_API/releases/tags/$TAG/assets?name=$name" | head -c 400
    echo
  done
fi
echo "==> 全部完成。产物目录: $WORK/out/"
ls -lh out/*.tar.gz 2>/dev/null || true
