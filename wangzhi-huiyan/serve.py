# -*- coding: utf-8 -*-
"""
网址慧眼 - 前端静态服务（防缓存版）

【为什么需要这个文件】
标准命令 `python -m http.server` 发出的响应头里没有 Cache-Control，
浏览器会自己做"启发式缓存"：哪怕你在网址后面加了 ?v=123 这样的
时间戳，某些浏览器 / 浏览器插件仍然会把整个页面缓存住，
甚至往页面里注入自己的脚本（我们在你截图里看到的 "Summarize" 就是这类插件干的）。

所以这里我们自己做响应头：
  1. Cache-Control: no-store, no-cache, must-revalidate  -> 让浏览器不要缓存
  2. Access-Control-Allow-Origin: *                      -> 允许跨域，方便调试
  3. 支持 /  -> 自动重定向到 diagnose.html（先看诊断结果，确认环境 OK 再看主页面）

用法：
  python serve.py            # 默认 8899 端口
  python serve.py 9000       # 指定端口
然后浏览器打开：
  http://127.0.0.1:8899/
"""

import os
import sys
import time
import functools
import http.server
import socketserver

# 默认端口，可以被命令行参数覆盖
DEFAULT_PORT = 8899

# 前端根目录 = 本文件所在目录
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """在标准静态文件服务的基础上，加上"禁止缓存"的响应头"""

    def end_headers(self):
        # no-store        : 完全不存缓存（最重要）
        # no-cache        : 每次都要回源校验
        # must-revalidate  : 过期后必须重新验证
        # no-transform    : 防止某些代理把内容转换（比如压缩/改写）
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0, no-transform")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        # 允许跨域，方便你用别的工具调试
        self.send_header("Access-Control-Allow-Origin", "*")
        # 告诉浏览器这是我们自己改过的实现，别再套一层缓存
        self.send_header("X-Served-By", "wangzhi-huiyan-nocache")
        super().end_headers()

    def send_response(self, code, message=None):
        # 顺便把 Server 头改掉，避免暴露 python 版本
        self.log_request(code)
        self.send_response_only(code, message)
        self.send_header("Server", "WangZhiHuiYan/1.0")

    def do_GET(self):
        # 访问根路径 / 时，自动跳到 diagnose.html
        # 这样用户打开 http://127.0.0.1:8899/ 就能直接看到"环境是否正常"
        if self.path == "/" or self.path.startswith("/?"):
            self.send_response(302)
            self.send_header("Location", "/diagnose.html")
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, format, *args):
        """打印访问日志，方便确认浏览器到底有没有真的请求到文件"""
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), format % args))


def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print("端口参数不合法，请输入数字，例如：python serve.py 9000")
            return

    # 把静态根目录切到脚本所在目录
    handler = functools.partial(NoCacheHandler, directory=ROOT_DIR)

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print("=" * 60)
        print("  网址慧眼 前端服务已启动（防缓存版）")
        print("=" * 60)
        print("  诊断页面 : http://127.0.0.1:%d/diagnose.html" % port)
        print("  主页面   : http://127.0.0.1:%d/index.html" % port)
        print("  根路径 / : 自动跳转到诊断页面")
        print("")
        print("  已禁用浏览器缓存，请直接刷新页面即可看到最新代码")
        print("  关闭服务：在此窗口按 Ctrl + C")
        print("=" * 60)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n服务已停止")


if __name__ == "__main__":
    main()