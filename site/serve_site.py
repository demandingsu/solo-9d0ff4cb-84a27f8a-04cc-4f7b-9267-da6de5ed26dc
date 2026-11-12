"""离线示例公告站服务器（仅标准库）。

在 ``html/`` 之上提供静态 HTML，并按约定模拟“详情页暂不可用”：

* 请求的详情页若存在同名占位标记 ``notices/_<编号>.unavailable``，
  服务器返回 **503 Service Unavailable**（页面实体本身不存在）。

标记文件由 ``build_site.py`` 生成，脚本名以 ``_`` 开头，因此 ``http.server``
不会把它当作可下载页面；这里再做一次显式拦截。

用法::

    python serve_site.py [--bind 127.0.0.1] [--port 8080] [--directory html]
"""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


class NoticeSiteHandler(SimpleHTTPRequestHandler):
    def _unavailable(self) -> bool:
        path = unquote(urlparse(self.path).path)
        if not path.endswith(".html"):
            return False
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2 or "notices" not in parts[:-1]:
            return False
        if any(p in (".", "..") for p in parts):
            return False
        stem = Path(parts[-1]).stem
        marker = Path(self.directory).joinpath(*parts[:-1]) / f"_{stem}.unavailable"
        return marker.is_file()

    def do_GET(self) -> None:  # noqa: N802 (http.server 命名约定)
        if self._unavailable():
            body = "503 Service Unavailable: 该公告详情暂时无法访问\n".encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        if self._unavailable():
            self.send_response(503)
            self.end_headers()
            return
        super().do_HEAD()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--directory", default="html")
    args = parser.parse_args()

    handler = partial(NoticeSiteHandler, directory=args.directory)
    httpd = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"示例公告站: http://{args.bind}:{args.port}/ （目录 {args.directory}）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
