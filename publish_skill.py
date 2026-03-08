#!/usr/bin/env python3
"""
使用 clawhub CLI 将 openwechat-im-client Skill 上传到 ClawHub。
上传信息依据 README 与 SKILL.md 填写。
使用前需先执行 clawhub login 登录，或设置 CLAWHUB_TOKEN 等环境变量。
"""
import os
import subprocess
import sys

# 依据 README / SKILL.md 填写的上传信息
SKILL_PATH = "openwechat-im-client"
SLUG = "openwechat-im-client"
NAME = "OpenWechat-Claw IM Client"
VERSION = "1.0.0"
CHANGELOG = (
    "Initial publish. WeChat-like messaging for OpenClaw: register, send/receive messages, "
    "friend list, discover users, block/unblock. Repo: https://github.com/Zhaobudaoyuema/openwechat-claw"
)


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    skill_path = os.path.join(root, SKILL_PATH)
    if not os.path.isdir(skill_path):
        print(f"Skill 目录不存在: {skill_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(os.path.join(skill_path, "SKILL.md")):
        print("openwechat-im-client 目录下未找到 SKILL.md", file=sys.stderr)
        sys.exit(1)

    # 使用 shell 以便 Windows 能找到 clawhub（可能是 .ps1 或 PATH 中的脚本）
    cmd = (
        f'clawhub publish "{skill_path}" '
        f'--slug "{SLUG}" --name "{NAME}" --version "{VERSION}" '
        f'--changelog "{CHANGELOG}" --tags "latest"'
    )
    result = subprocess.run(cmd, cwd=root, shell=True)
    if result.returncode != 0:
        print("若提示未登录，请先执行: clawhub login", file=sys.stderr)
        sys.exit(result.returncode)
    print("Skill 已成功上传至 ClawHub。")


if __name__ == "__main__":
    main()
