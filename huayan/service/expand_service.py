# -*- coding: utf-8 -*-
"""
service/expand_service.py —— 短链递归展开
================================
最大跳转 5 层，超过判定【循环跳转，可疑】并终止解析。
mock 模式返回与前端原型一致的演示跳转链；real 模式走真实 HTTP 重定向追踪。
任何异常都不抛出，降级为无法解析。
"""
from urllib.parse import urlparse

import requests
from config import RUN_MODE, MAX_REDIRECTS, REQUEST_TIMEOUT, THIRD_PARTY


def _mock_expand(url: str) -> dict:
    """
    mock 模式：按域名特征返回与前端原型测试样例匹配的跳转链。
    样例对应：形近钓鱼站 / 短链中转站 / 循环跳转站 / 正常站点
    """
    host = (urlparse(url).hostname or "").lower()

    if "loop" in host:
        chain = [{"hop": i, "url": "https://loop.demo-redirect.top/hop%d" % i, "status": 302}
                 for i in range(1, 7)]
        return {"original": url, "final": None, "chain": chain,
                "exceeded": True, "hopCount": 6,
                "note": "跳转层数超过上限 %d 层，判定为循环跳转" % MAX_REDIRECTS}

    if "verify" in host or "login" in host or "account" in host:
        return {
            "original": url,
            "final": "https://account-verify.demo-login.top/signin",
            "chain": [
                {"hop": 1, "url": url, "status": 301},
                {"hop": 2, "url": "https://t.cn/x9k2Lm", "status": 302},
                {"hop": 3, "url": "https://demo-s3.oss-cn.top/p/8f3a", "status": 302},
                {"hop": 4, "url": "https://account-verify.demo-login.top/signin", "status": 200},
            ],
            "exceeded": False, "hopCount": 3,
            "note": "短链展开成功，共 3 层跳转",
        }

    if "0k.cn" in host:
        return {"original": url, "final": url,
                "chain": [{"hop": 1, "url": url, "status": 200}],
                "exceeded": False, "hopCount": 0, "note": "直接访问，无跳转层"}

    return {"original": url, "final": url,
            "chain": [{"hop": 1, "url": url, "status": 200}],
            "exceeded": False, "hopCount": 0, "note": "直接访问，无跳转层"}


def _real_expand(url: str) -> dict:
    """真实模式：手动跟踪重定向，逐层记录，超上限即终止"""
    chain = []
    current = url
    visited = set()

    for hop in range(1, MAX_REDIRECTS + 2):
        if current in visited:
            # 再次访问同一地址 → 循环跳转
            return {"original": url, "final": None, "chain": chain,
                    "exceeded": True, "hopCount": len(chain),
                    "note": "检测到循环跳转：第 %d 层重复访问 %s" % (hop, current)}
        visited.add(current)

        try:
            resp = requests.get(current, timeout=REQUEST_TIMEOUT,
                                allow_redirects=False, stream=True)
        except requests.exceptions.Timeout:
            return {"original": url, "final": current, "chain": chain,
                    "exceeded": False, "hopCount": len(chain),
                    "error": "TIMEOUT", "note": "短链解析超时"}
        except requests.exceptions.ConnectionError:
            return {"original": url, "final": current, "chain": chain,
                    "exceeded": False, "hopCount": len(chain),
                    "error": "CONNECTION_FAILED", "note": "目标站点连接失败"}
        except Exception as e:
            return {"original": url, "final": current, "chain": chain,
                    "exceeded": False, "hopCount": len(chain),
                    "error": str(e)[:100], "note": "短链解析异常"}

        try:
            # 判断是否为重定向响应
            if resp.is_redirect or resp.is_permanent_redirect:
                location = resp.headers.get("Location", "")
                if not location:
                    chain.append({"hop": hop, "url": current, "status": resp.status_code})
                    break
                if location.startswith("/"):
                    p = urlparse(current)
                    location = "%s://%s%s" % (p.scheme, p.netloc, location)
                chain.append({"hop": hop, "url": current, "status": resp.status_code})
                current = location
                if hop > MAX_REDIRECTS:
                    return {"original": url, "final": None, "chain": chain,
                            "exceeded": True, "hopCount": len(chain),
                            "note": "跳转层数超过上限 %d 层，判定为循环跳转" % MAX_REDIRECTS}
            else:
                chain.append({"hop": hop, "url": current, "status": resp.status_code})
                return {"original": url, "final": current, "chain": chain,
                        "exceeded": False, "hopCount": len(chain),
                        "note": "解析完成，共 %d 层跳转" % (len(chain) - 1)}
        finally:
            # 及时释放连接，避免资源泄漏
            try:
                resp.close()
            except Exception:
                pass

    return {"original": url, "final": current, "chain": chain,
            "exceeded": len(chain) > MAX_REDIRECTS, "hopCount": len(chain),
            "note": "解析完成"}


def _normalize(result: dict) -> dict:
    """
    归一化 expand 结果，补齐前端与 detect 流程所需的字段。
    is_loop 是 exceeded 的语义别名（是否循环跳转），两个字段并存，
    兼容不同调用方的命名习惯。
    """
    result.setdefault("chain", [])
    result.setdefault("final", None)
    result.setdefault("original", None)
    result.setdefault("hopCount", len(result.get("chain") or []))
    result["exceeded"] = bool(result.get("exceeded"))
    result["is_loop"] = result["exceeded"]      # 是否循环跳转
    return result


def expand_url(url: str) -> dict:
    """
    短链递归展开统一入口。
    返回结构严格对齐接口文档 /url/expand 契约。
    """
    # 真实模式下若配置了第三方短链API，优先走第三方
    if RUN_MODE == "real" and THIRD_PARTY["expand"]["enabled"] \
            and THIRD_PARTY["expand"]["api_url"]:
        from utils import http_get_json
        cfg = THIRD_PARTY["expand"]
        r = http_get_json(cfg["api_url"], params={"url": url, "max": MAX_REDIRECTS},
                          headers={"Authorization": "Bearer " + cfg["api_key"]})
        if r.get("ok") and isinstance(r.get("data"), dict):
            return _normalize(r["data"])
        # 第三方失败 → 退回自研追踪
        result = _real_expand(url)
        result["thirdPartyError"] = r.get("error")
        return _normalize(result)

    if RUN_MODE == "real":
        return _normalize(_real_expand(url))
    return _normalize(_mock_expand(url))