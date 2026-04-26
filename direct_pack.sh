#!/bin/bash
# 实时字幕翻译 - 绝对路径直连打包器 (Bypassing Conda Activation)

# 0. 自动进入脚本所在的文件夹 (项目根目录)
cd "$(dirname "$0")"

# 1. 设置核心环境变量名
APP_NAME="实时字幕翻译"
PROJECT_DIR=$(pwd)
CONDA_ENV_PY="/Users/weizixun/anaconda3/envs/subtitle_pack/bin/python3"
PYINSTALLER_BIN="/Users/weizixun/anaconda3/envs/subtitle_pack/bin/pyinstaller"

echo "--------------------------------------------------------"
echo "🚀 启动【直联打包模式】..."
echo "📍 环境路径: $CONDA_ENV_PY"
echo "📍 启动路径: $PROJECT_DIR"
echo "--------------------------------------------------------"

# 2. 验证二进制文件是否存在
if [ ! -f "$CONDA_ENV_PY" ]; then
    echo "❌ 找不到项目专有的 Python 环境: $CONDA_ENV_PY"
    echo "💡 提示：请确保您的 [subtitle_pack] 环境已正确安装。"
    exit 1
fi

if [ ! -f "$PYINSTALLER_BIN" ]; then
    echo "❌ 环境内找不到 PyInstaller: $PYINSTALLER_BIN"
    echo "🏗️ 正在为您尝试修复 (此操作仅在您的终端有效)..."
    "$CONDA_ENV_PY" -m pip install pyinstaller
fi

# 3. 清理构建残留
echo "🧹 正在彻底清理旧的构建环境 (build/ dist/)..."
rm -rf build/ dist/*.app dist/*.dmg 2>/dev/null

# 4. 执行正式打包
echo "📦 正在使用 [subtitle_pack] 环境下的 PyInstaller 进行打包..."
"$PYINSTALLER_BIN" --clean --noconfirm realtime_subtitle.spec

if [ $? -eq 0 ]; then
    echo "--------------------------------------------------------"
    echo "✅ 基础打包阶段成功完成！"
    echo "📂 二进制文件已生成在: ${PROJECT_DIR}/dist/${APP_NAME}.app"
    echo "--------------------------------------------------------"
    
    # 💡 增加调试指引
    echo "📢 [重要] 调试指引："
    echo "如果双击图标启动失败，请打开终端并运行以下命令查看报错日志："
    echo "  ./dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"
    echo "--------------------------------------------------------"

    # 5. 自动接力生成 DMG
    if [ -f "./make_dmg.sh" ]; then
        echo "🎨 正在自动触发 DMG 封装逻辑..."
        bash ./make_dmg.sh
    else
        echo "⚠️ 找不到 make_dmg.sh，请手动执行封装逻辑。"
    fi
else
    echo "❌ 打包编译失败，请在控制台检查详细日志。"
    exit 1
fi
