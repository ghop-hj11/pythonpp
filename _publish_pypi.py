# -*- coding: utf-8 -*-
"""把 dist/ 里的产物发布到 PyPI。

为什么要自己写而不是用 twine upload 命令？
    1. 本环境的 pip/curl 走代理时会出现 TLS 凭证问题，用 urllib 反而稳定；
    2. 需要在上传前做"能不能传"的预检查（项目不存在时 token 必须有建项权限）；
    3. 出问题时能明确告诉用户是哪一步失败了。

用法：
    set PYPI_TOKEN=pypi-xxxx
    python _publish_pypi.py            # 发正式站
    python _publish_pypi.py --test     # 发测试站 TestPyPI
"""

from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

UPLOAD_URL = "https://upload.pypi.org/legacy/"
TEST_URL = "https://test.pypi.org/legacy/"
JSON_URL = "https://pypi.org/pypi/{name}/json"


def _multipart(fields, files):
    """手搓 multipart/form-data，避免额外依赖。"""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    for key, filename, payload, content_type in files:
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
        ).encode()
        body += f"Content-Type: {content_type}\r\n\r\n".encode()
        body += payload + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _digest(payload):
    h = hashlib.sha256(payload).digest()
    return "sha256=" + base64.b64encode(h).decode()


def check_project(name):
    """项目是否已存在（决定 token 需要什么权限）。"""
    req = urllib.request.Request(JSON_URL.format(name=name), headers={"User-Agent": "x"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        return True, data["info"].get("version")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, None
        raise


def upload(files, token, url, metadata):
    payloads = []
    for path in files:
        with open(path, "rb") as fp:
            data = fp.read()
        kind = "bdist_wheel" if path.endswith(".whl") else "sdist"
        payloads.append((os.path.basename(path), data, kind))

    for filename, data, kind in payloads:
        fields = dict(metadata)
        fields.update({
            ":action": "file_upload",
            "protocol_version": "1",
            "filetype": kind,
            "pyversion": "py3" if kind == "sdist" else "",
            "metadata_version": "2.1",
            "sha256_digest": _digest(data),
        })
        if kind == "bdist_wheel":
            # 从文件名推断 wheel 的标签，PyPI 会做校验
            parts = filename[:-4].split("-")
            if len(parts) >= 5:
                fields["pyversion"] = parts[2]
        content_type = "application/octet-stream"
        body, ctype = _multipart(
            fields, [("content", filename, data, content_type)]
        )
        auth = base64.b64encode(f"__token__:{token}".encode()).decode()
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": ctype,
                "Content-Length": str(len(body)),
                "User-Agent": "pythonpp-publisher/1.0",
            },
        )
        print(f"  上传 {filename} ...", end=" ", flush=True)
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                print(f"HTTP {r.status}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            print(f"失败 HTTP {e.code}")
            print("  响应：", detail[:600])
            if "already exists" in detail or "File already exists" in detail:
                print("  → 这个版本已经传过了，PyPI 不允许覆盖，需要升版本号")
            return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="发 TestPyPI")
    parser.add_argument("--name", default="pythonpp-plus")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--dist", default="dist")
    args = parser.parse_args()

    token = os.environ.get("PYPI_TOKEN", "").strip()
    if not token:
        print("请先设置环境变量 PYPI_TOKEN")
        return 2

    files = sorted(
        glob.glob(os.path.join(args.dist, "*.whl"))
        + glob.glob(os.path.join(args.dist, "*.tar.gz"))
    )
    if not files:
        print("dist/ 里没有产物，先运行 python setup.py bdist_wheel sdist")
        return 2

    url = TEST_URL if args.test else UPLOAD_URL
    target = "TestPyPI" if args.test else "PyPI"
    print(f"目标：{target}")
    print(f"包名：{args.name}  版本：{args.version}")
    print(f"产物：{[os.path.basename(f) for f in files]}")

    exists, ver = check_project(args.name)
    if exists:
        print(f"提示：{args.name} 已存在（当前版本 {ver}）")
    else:
        print(f"提示：{args.name} 是全新项目，将创建")

    metadata = {
        "name": args.name,
        "version": args.version,
        "metadata_version": "2.1",
        "summary": "Python 语法 + C 加速：内置常用函数、中文报错、整数循环 JIT",
        "author": "Python++",
        "license": "MIT",
        "requires_python": ">=3.8",
        "description_content_type": "text/markdown",
        "home_page": "https://github.com/ghop-hj11/pythonpp",
        "project_urls": "Source, https://github.com/ghop-hj11/pythonpp",
    }
    readme = os.path.join("README.md")
    if os.path.exists(readme):
        with open(readme, encoding="utf-8") as fp:
            metadata["description"] = fp.read()

    ok = upload(files, token, url, metadata)
    if ok:
        print(f"\n发布成功：https://pypi.org/project/{args.name}/")
        return 0
    print("\n发布失败，见上面的响应")
    return 1


if __name__ == "__main__":
    sys.exit(main())
