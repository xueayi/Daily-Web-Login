"""py2app 打包配置 - 将 Daily Web Login 打包为 macOS 独立 .app"""

from setuptools import setup

APP = ["main.py"]
DATA_FILES = ["config.json", ".env.example"]  # .env 本身含敏感信息不入库不打包
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "LSUIElement": True,
        "CFBundleName": "DailyWebLogin",
        "CFBundleDisplayName": "Daily Web Login",
        "CFBundleIdentifier": "com.dailyweblogin.app",
        "CFBundleVersion": "1.1.0",  # 集成 yngal 签到 + 飞书推送
        "CFBundleShortVersionString": "1.1.0",
    },
    "packages": ["rumps", "schedule", "requests", "dotenv"],
}

setup(
    app=APP,
    name="DailyWebLogin",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
    install_requires=["rumps", "schedule", "requests", "python-dotenv"],
)
