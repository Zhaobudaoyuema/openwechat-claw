#!/usr/bin/env python3
"""
将 openwechat-im-client/SKILL.md 发布到 npm。
会先自动把 openwechat-im-client/package.json 中的 version 小版本号（patch）+1，再执行 npm publish。
发布前请：1) npm login 登录；2) 在 npm 账号启用双因素认证(2FA)，或使用带发布权限的 granular access token（bypass 2fa）。
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(ROOT, "openwechat-im-client")
PACKAGE_JSON = os.path.join(SKILL_DIR, "package.json")


def bump_patch_version(version: str) -> str:
    """将 x.y.z 的小版本号 z +1。"""
    parts = version.strip().split(".")
    if len(parts) < 3:
        return f"{version}.1" if len(parts) == 1 else f"{version}.0"
    try:
        parts[2] = str(int(parts[2]) + 1)
        return ".".join(parts)
    except ValueError:
        return version


def main() -> None:
    if not os.path.isdir(SKILL_DIR):
        print(f"Skill 目录不存在: {SKILL_DIR}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(os.path.join(SKILL_DIR, "SKILL.md")):
        print("openwechat-im-client 目录下未找到 SKILL.md", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(PACKAGE_JSON):
        print("openwechat-im-client 目录下未找到 package.json", file=sys.stderr)
        sys.exit(1)

    with open(PACKAGE_JSON, "r", encoding="utf-8") as f:
        pkg = json.load(f)
    current = pkg.get("version", "1.0.0")
    new_version = bump_patch_version(current)
    pkg["version"] = new_version
    with open(PACKAGE_JSON, "w", encoding="utf-8") as f:
        json.dump(pkg, f, indent=2, ensure_ascii=False)
    print(f"版本已更新: {current} -> {new_version}")

    cmd = ["npm", "publish"]
    result = subprocess.run(cmd, cwd=SKILL_DIR, shell=(os.name == "nt"))
    if result.returncode != 0:
        print("发布失败。若为 403：请在 npm 账号启用双因素认证(2FA)，或使用 granular access token 并勾选 bypass 2fa。若未登录：npm login", file=sys.stderr)
        sys.exit(result.returncode)
    print(f"已成功发布到 npm，版本: {new_version}")


if __name__ == "__main__":
    main()
