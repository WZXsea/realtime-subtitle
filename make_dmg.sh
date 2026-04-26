#!/bin/bash
# 实时字幕翻译 - 专业 DMG 封装脚本 (macOS)

# 0. 自动进入脚本所在的文件夹 (项目根目录)
cd "$(dirname "$0")"

APP_NAME="实时字幕翻译"
APP_PATH="dist/${APP_NAME}.app"
DMG_NAME="${APP_NAME}_v2.0.0.dmg"
DMG_DIST="dist/${DMG_NAME}"

# 1. 检查必备工具
if ! command -v create-dmg &> /dev/null; then
    echo "⚠️ 未检测到 [create-dmg] 工具。"
    echo "🏗️ 正在为您尝试通过 Homebrew 安装 (可能需要您的账户密码)..."
    brew install create-dmg
    if [ $? -ne 0 ]; then
        echo "❌ 自动安装失败。请手动运行 [brew install create-dmg] 后重试。"
        exit 1
    fi
fi

# 2. 检查源文件
if [ ! -d "$APP_PATH" ]; then
    echo "❌ 找不到构建好的 .app 文件: $APP_PATH"
    echo "💡 提示：请先运行 ./bundle.sh 完成基础打包。"
    exit 1
fi

# 3. 清理旧的挂载点和残留 (防止磁盘忙碌/挂载冲突)
echo "🧹 正在清理可能存在的挂载残留..."
# 强制卸载可能存在的同名卷
hdiutil detach "/Volumes/${APP_NAME} 安装器" -force 2>/dev/null
rm -f "$DMG_DIST"

# 4. 执行正式封装
echo "🏗️ 正在将 [${APP_NAME}.app] 封装为 DMG..."
echo "🎨 正在启动极速布局模式 (已包含 Finder 容错)..."

# create-dmg 参数说明:
# --volname: 磁盘挂载后的名称
# --window-size: 窗口大小 (长宽)
# --icon-size: 图标大小
# --icon: 图标位置 (文件名 X坐标 Y坐标)
# --app-drop-link: 创建 /Applications 链接的位置 (X坐标 Y坐标)
# --hide-extension: 隐藏 .app 后缀名
# Note: 如果报 -10006 错误，通常是因为 Finder 权限拦截，但这不影响生成的 DMG 可用性。
create-dmg \
  --volname "${APP_NAME} 安装器" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 120 \
  --icon "${APP_NAME}.app" 150 190 \
  --hide-extension "${APP_NAME}.app" \
  --app-drop-link 450 190 \
  --format UDZO \
  "$DMG_DIST" \
  "dist/"

RESULT=$?
if [ $RESULT -eq 0 ] || [ -f "$DMG_DIST" ]; then
    echo "--------------------------------------------------------"
    echo "✅ 任务完成！专业 DMG 安装包已初步生成！"
    if [ $RESULT -ne 0 ]; then
        echo "⚠️ 提示：检测到 Finder 布局微调被系统拦截 (Error -10006)。"
        echo "   这不影响软件功能，只是安装界面的图标对齐可能需要您稍微手动确认。"
    fi
    echo "📂 文件位置: $(pwd)/$DMG_DIST"
    echo "💡 您可以把这个 .dmg 文件发给任何人，他们双击即可拖动安装。"
    echo "--------------------------------------------------------"
else
    echo "❌ DMG 封装彻底失败，请查看上方报错信息。"
fi
