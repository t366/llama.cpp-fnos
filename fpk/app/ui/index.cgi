#!/bin/bash
# index.cgi — llamacpp 的 fnOS 同源 CGI 入口（参照 AutoPower / wol / xunlei 模式）
# 由飞牛自带 nginx 在 /cgi/ThirdParty/llamacpp/index.cgi/ 下以 CGI 方式执行：
#   1. 静态文件（HTML/CSS/JS/图片）从本应用 ui/web 目录直接返回
#   2. /api/ 请求反向代理到本机 manager-server（仅监听 127.0.0.1:18080）
# 入口为同源相对路径，内网 :5000 与公网 :4433 均可访问，且全程走飞牛登录鉴权，
# 无需对外暴露管理端口。

# ========== 配置 ==========
# 应用安装后 ui 目录实际位置（app/ 目录在安装时映射到 target/）
BASE_PATH="/var/apps/llamacpp/target/ui/web"
# 后端端口：从 manifest 动态读取，默认 18080
MANIFEST_FILE="/var/apps/llamacpp/manifest"
BACKEND_PORT="18080"
if [ -f "$MANIFEST_FILE" ]; then
    _port=$(grep -E '^service_port[[:space:]]*=' "$MANIFEST_FILE" | awk -F '=' '{print $2}' | tr -d ' \r')
    if [ -n "$_port" ]; then
        BACKEND_PORT="$_port"
    fi
fi
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"

# ========== 解析请求路径 ==========
# 取出 index.cgi 之后的相对路径
#   /cgi/ThirdParty/llamacpp/index.cgi/api/status -> /api/status
#   /cgi/ThirdParty/llamacpp/index.cgi/app.js     -> /app.js
URI_NO_QUERY="${REQUEST_URI%%\?*}"
REL_PATH="/"
case "$URI_NO_QUERY" in
    *index.cgi*)
        REL_PATH="${URI_NO_QUERY#*index.cgi}"
        ;;
esac
[ -z "$REL_PATH" ] && REL_PATH="/"
[ "$REL_PATH" = "/" ] && REL_PATH="/index.html"

# ========== /api 反向代理 ==========
case "$REL_PATH" in
    /api/*)
        TARGET_URL="${BACKEND_URL}${REL_PATH}"
        [ -n "$QUERY_STRING" ] && TARGET_URL="${TARGET_URL}?${QUERY_STRING}"
        case "$REQUEST_METHOD" in
            POST|PUT)
                POST_DATA=""
                if [ -n "$CONTENT_LENGTH" ] && [ "$CONTENT_LENGTH" -gt 0 ] 2>/dev/null; then
                    POST_DATA=$(dd bs=1 count="$CONTENT_LENGTH" 2>/dev/null)
                fi
                RESPONSE=$(curl -s -w $'\n%{http_code}' -X "$REQUEST_METHOD" \
                    -H "Content-Type: ${CONTENT_TYPE:-application/json}" \
                    --data-binary "$POST_DATA" \
                    "$TARGET_URL" 2>/dev/null)
                ;;
            DELETE)
                RESPONSE=$(curl -s -w $'\n%{http_code}' -X DELETE "$TARGET_URL" 2>/dev/null)
                ;;
            *)
                RESPONSE=$(curl -s -w $'\n%{http_code}' -X GET "$TARGET_URL" 2>/dev/null)
                ;;
        esac
        HTTP_CODE=$(printf '%s' "$RESPONSE" | tail -n 1)
        BODY=$(printf '%s' "$RESPONSE" | sed '$d')
        # 后端未启动时 curl 返回 000，需换成合法 502 与 JSON 错误，前端才能给出提示
        case "$HTTP_CODE" in
            [1-5][0-9][0-9])
                ;;
            *)
                HTTP_CODE=502
                BODY='{"ok": false, "error": "llama.cpp 管理服务未运行，请在应用中心启动应用"}'
                ;;
        esac
        echo "Status: $HTTP_CODE"
        echo "Content-Type: application/json; charset=utf-8"
        echo "Cache-Control: no-store"
        echo ""
        printf '%s' "$BODY"
        exit 0
        ;;
esac

# ========== 静态文件服务 ==========
TARGET_FILE="${BASE_PATH}${REL_PATH}"
# 安全检查：禁止路径穿越
case "$TARGET_FILE" in
    *..*)
        echo "Status: 400 Bad Request"
        echo "Content-Type: text/plain; charset=utf-8"
        echo ""
        echo "Bad Request"
        exit 0
        ;;
esac
if [ ! -f "$TARGET_FILE" ]; then
    echo "Status: 404 Not Found"
    echo "Content-Type: text/plain; charset=utf-8"
    echo ""
    echo "404 Not Found: ${REL_PATH}"
    exit 0
fi
ext="${TARGET_FILE##*.}"
case "$ext" in
    html|htm) mime="text/html; charset=utf-8" ;;
    css)      mime="text/css; charset=utf-8" ;;
    js)       mime="application/javascript; charset=utf-8" ;;
    json)     mime="application/json; charset=utf-8" ;;
    png)      mime="image/png" ;;
    jpg|jpeg) mime="image/jpeg" ;;
    gif)      mime="image/gif" ;;
    svg)      mime="image/svg+xml" ;;
    ico)      mime="image/x-icon" ;;
    woff2)    mime="font/woff2" ;;
    woff)     mime="font/woff" ;;
    txt|log)  mime="text/plain; charset=utf-8" ;;
    *)        mime="application/octet-stream" ;;
esac
echo "Content-Type: $mime"
# 页面与脚本每次向服务端确认，应用升级后不会继续使用浏览器缓存的旧版本
case "$ext" in
    html|htm|css|js) echo "Cache-Control: no-cache" ;;
esac
echo ""
cat "$TARGET_FILE"
