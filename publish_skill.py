#!/usr/bin/env python3
"""
使用 clawhub CLI 将 openwechat-im-client Skill 上传到 ClawHub。
上传信息依据 README 与 SKILL.md 填写。
每次触发会自动将小版本号（patch）+1 并写回本文件。
使用前需先执行 clawhub login 登录，或设置 CLAWHUB_TOKEN 等环境变量。
"""
import os
import re
import subprocess
import sys

# 依据 README / SKILL.md 填写的上传信息
SKILL_PATH = "openwechat-im-client"
SLUG = "openwechat-im-client"
NAME = "OpenWechat-Claw IM Client"
VERSION = "1.0.9"  # 每次 publish 会自动 +1 小版本号
CHANGELOG = (
    "Initial publish. WeChat-like messaging for OpenClaw: register, send/receive messages, "
    "friend list, discover users, block/unblock. Repo: https://github.com/Zhaobudaoyuema/openwechat-claw"
)


def bump_patch_version(version: str) -> str:
    """将 x.y.z 的小版本号 z +1，返回新版本号。"""
    parts = version.strip().split(".")
    if len(parts) < 3:
        return f"{version}.1" if len(parts) == 1 else f"{version}.0"
    try:
        parts[2] = str(int(parts[2]) + 1)
        return ".".join(parts)
    except ValueError:
        return version


def update_version_in_file(new_version: str) -> None:
    """把本文件中 VERSION = \"...\" 的取值更新为 new_version。"""
    path = os.path.abspath(__file__)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    content = re.sub(
        r'(VERSION\s*=\s*")[^"]*(")',
        rf"\g<1>{new_version}\g<2>",
        content,
        count=1,
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    # 每次触发：小版本号 +1 并写回本文件
    new_version = bump_patch_version(VERSION)
    update_version_in_file(new_version)
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
        f'--slug "{SLUG}" --name "{NAME}" --version "{new_version}" '
        f'--changelog "{CHANGELOG}" --tags "latest"'
    )
    result = subprocess.run(cmd, cwd=root, shell=True)
    if result.returncode != 0:
        print("若提示未登录，请先执行: clawhub login", file=sys.stderr)
        sys.exit(result.returncode)
    print(f"Skill 已成功上传至 ClawHub，版本: {new_version}")


if __name__ == "__main__":
    main()
