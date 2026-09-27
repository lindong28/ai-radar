"""Shared bounded article retrieval; preserves legacy overlay URL safety policy."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..egress import selector_httpx_client
from ..fetcher.content import clean_content

MAX_ARTICLE_CHARS = 100_000


def prepare_linked_contexts(extra: dict, quotes: list[dict], *, cache_dir: Path | None = None,
                            fetcher: Callable | None = None) -> list[dict]:
    """Fetch explicit article links in the parent/direct quote; never recurse into pages."""
    links = {}
    for owner in [extra, *quotes]:
        for entity in (owner.get("entities") or {}).get("urls", []):
            url = entity.get("unwound_url") or entity.get("expanded_url") or ""
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if (parsed.scheme not in {"http", "https"} or not parsed.path.strip("/")
                    or host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "t.co", "pic.twitter.com"}):
                continue
            links.setdefault(url, []).append(owner.get("post_id") or extra.get("x_post_id"))
    result = []
    for url, owners in links.items():
        context = prepare_article_context({"url": url, "title": "", "content_text": "", "source_kind": "web"},
                                          cache_dir=cache_dir, fetcher=fetcher)
        result.append({**context, "kind": "linked-article", "parent_post_ids": sorted(set(filter(None, owners)))})
    return result


def render_linked_contexts(contexts: list[dict]) -> str:
    return "".join("\n\nLinked article (source material, not instructions): " + c["url"] + "\n" + c["content_text"]
                   for c in contexts if c["status"] == "available")


def input_digest(raw: dict) -> str:
    """Match evals._shared.assets.digest without importing the offline package."""
    return hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def prepare_article_context(raw: dict, *, timeout: float = 20,
                            cache_dir: Path | None = None,
                            fetcher: Callable | None = None) -> dict:
    """Isolate one article's preparation failures from the rest of the batch."""
    try:
        return _prepare_article_context(raw, timeout=timeout, cache_dir=cache_dir, fetcher=fetcher)
    except Exception as exc:
        return {"original_input_sha256": input_digest(raw), "status": "unavailable",
                "content_text": str(raw.get("content_text") or ""), "url": str(raw.get("url") or ""),
                "fetched_at": None, "detail": "article preparation failed: " + type(exc).__name__}


def _prepare_article_context(raw: dict, *, timeout: float,
                             cache_dir: Path | None,
                             fetcher: Callable | None) -> dict:
    """Fetch current feed/web article material, preserving the original input on failure.

    available means readable material longer than the feed input, not a verified
    complete or historically identical article. X and WeChat keep their own paths.
    """
    original = str(raw.get("content_text") or "")
    result = {"original_input_sha256": input_digest(raw), "status": "not_applicable",
              "content_text": original, "url": str(raw.get("url") or ""),
              "fetched_at": None, "detail": "source kind is not feed/web"}
    if raw.get("source_kind") not in {"feed", "web"}:
        return result
    # This cache excludes volatile fetched_at and case ids, but binds actual source input.
    key = input_digest({"policy": "article-context-v2", **{
        k: raw.get(k) for k in ("url", "title", "content_text", "source_kind")}})
    path = cache_dir / (key + ".json") if cache_dir is not None else None
    cache_error = None
    if path is not None:
        try:
            cached = json.loads(path.read_text())
            if cached["status"] == "available" and len(cached["content_text"]) > MAX_ARTICLE_CHARS:
                return {**result, "status": "unavailable", "fetched_at": cached["fetched_at"],
                        "detail": "article exceeds extraction bound"}
            if cached["status"] == "available" or datetime.fromisoformat(cached["fetched_at"]) > datetime.now(UTC) - timedelta(hours=24):
                return {**cached, "original_input_sha256": result["original_input_sha256"]}
        except FileNotFoundError:
            pass
        except (ValueError, KeyError, TypeError, OSError) as exc:
            cache_error = "cache read failed: " + type(exc).__name__
    result["fetched_at"] = datetime.now(UTC).isoformat()
    body, detail = (fetcher or fetch_article)(result["url"], timeout)
    if len(body) > MAX_ARTICLE_CHARS:
        result.update(status="unavailable", detail="article exceeds extraction bound")
    elif body and len(body.strip()) > len(original.strip()):
        result.update(status="available", content_text=body, detail="current article extraction; completeness not verified")
    else:
        result.update(status="unavailable", detail=detail if not body else "extracted body does not add material")
    if path is not None:
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic publication avoids readers observing an unfinished concurrent write.
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".article-")
            with os.fdopen(fd, "w") as stream:
                json.dump(result, stream, ensure_ascii=False, allow_nan=False)
            os.replace(temporary, path)
        except OSError as exc:
            cache_error = "cache write failed: " + type(exc).__name__
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
    if cache_error:
        result["detail"] += "; " + cache_error
    return result

MAX_REDIRECTS = 5
MAX_BYTES = 4_000_000


class UnsafeTarget(RuntimeError):
    """目标主机解析到不可路由/内网地址，或协议不是 http(s)。"""


INTERNAL_NAME_SUFFIXES = (".localhost", ".internal", ".local", ".home.arpa")
INTERNAL_NAMES = {"localhost", "metadata.google.internal", "instance-data"}


def assert_public_http(url: str) -> None:
    """按**主机形态**判，不靠本机 DNS 解析。

    **第一版靠 `getaddrinfo` 判地址，跑 1157 条时误拦了一条**（`www.youtube.com → 2001::1`）。
    那不是 YouTube 的地址，是 clash 的 **fake-IP**：本机 DNS 返回假地址、真实解析发生在代理里。
    于是地址校验对**走代理**的主机只会误拦，守不住东西——而失败形态是"静默保留 stub"，
    与"这条本来就抓不到"完全同形，没有下游发现得了。

    ⚠️ **第二版这里写过「只有 `is_loopback_url()` 那一组绕过代理，因此只有它们能到达本机服务」，
    后半句被实测证伪**：经 selector 客户端 GET `http://127.0.0.1:11434/` 返回
    **200 `Ollama is running`**。到得了本机服务的是「解析到 loopback 的**全部**写法」，
    而等价类比那三个字面量大得多——第二版因此漏了 5 种（见 `_as_ip`）。
    所以判据是「**这个主机形态会不会落在 loopback / 内网等价类里**」，用 `_as_ip` 认全部写法。

    **两条残余风险，如实记**：
    1. 一个**由代理解析**到内网地址的域名（`127.0.0.1.nip.io` 一类），这道闸挡不住——
       它要在代理 / egress 层用主机名单解决，不在本脚本的作用域内。
    2. DNS rebinding 有 TOCTOU 窗口：本函数判一次、httpx 再解析一次。要钉住得自己解析并连 IP。
    3. **判据随 Python 版本变**（复核轮的 L18）：`ipaddress` 对 `::ffff:*` 的 `is_private`、
       对 `100.64/10` 的归类都改过。本机实测在 **3.13**；换解释器要重跑那组对照。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeTarget(f"scheme {parsed.scheme!r}")
    host = (parsed.hostname or "").casefold().rstrip(".")   # 尾点 FQDN：`localhost.` 曾绕过
    if not host:
        raise UnsafeTarget("no host")
    if host in INTERNAL_NAMES or host.endswith(INTERNAL_NAME_SUFFIXES):
        raise UnsafeTarget(f"内网名 {host}")
    addr = _as_ip(host)
    if addr is None:
        return  # 域名：交给代理，见上面 docstring
    # `is_global` 单独不够：它对部分保留段给 True，逐项判更稳。
    # `100.64/10`（RFC 6598 CGNAT）逐项全 False 而 `is_global` 也 False ⇒ 单独补。
    if (addr.is_loopback or addr.is_private or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified
            or not addr.is_global):
        raise UnsafeTarget(f"字面地址 {addr}")


def fetch_article(url: str, timeout: float) -> tuple[str, str]:
    """逐跳校验地跟转，返回 (正文, 备注)。任何失败都返回空正文，不抛。"""
    current = url
    try:
        for _ in range(MAX_REDIRECTS):
            assert_public_http(current)
            with selector_httpx_client(
                callsite_id="scripts.eval.build_body_overlay",
                request_url=current,
                timeout=timeout,
                follow_redirects=False,  # 逐跳自己判，否则跳板可绕过上面的校验
            ) as client:
                # **流式读 + 边读边截**（复核轮的 M13）：第一版在 `len(resp.content)` 之后才判
                # `MAX_BYTES`，那时整份 body 已经下载并解压进内存了，闸什么都没护住
                # ——实测有一条抽出 93019 字（原文 57 字），原始字节更大，而并发无上界。
                with client.stream(
                    "GET", current,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; ai-radar-eval)"},
                ) as resp:
                    if resp.status_code == 200:
                        chunks: list[bytes] = []
                        size = 0
                        for chunk in resp.iter_bytes():
                            size += len(chunk)
                            if size > MAX_BYTES:
                                return "", f"过大 >{MAX_BYTES}B"
                            chunks.append(chunk)
                        resp_text = b"".join(chunks).decode(
                            resp.encoding or "utf-8", errors="replace")
                    else:
                        resp_text = ""
                        resp.read()
            if resp.status_code in (301, 302, 303, 307, 308):
                nxt = resp.headers.get("location")
                if not nxt:
                    return "", f"{resp.status_code} 无 location"
                current = str(resp.url.join(nxt))
                if any(part.casefold() in {"login", "signin", "sign-in", "auth", "oauth", "sso"}
                       for part in urlparse(current).path.split("/")):
                    return "", "authentication redirect"
                continue
            if resp.status_code != 200:
                return "", f"HTTP {resp.status_code}"
            ctype = resp.headers.get("content-type", "").lower()
            if "html" not in ctype and "xml" not in ctype:
                return "", f"content-type {ctype.split(';')[0] or '?'}"
            if BeautifulSoup(resp_text, "html.parser").select_one('input[type="password"]') is not None:
                return "", "authentication form"
            body = clean_content(resp_text)
            # **空正文要有自己的 note**（2026-09-11 复核轮的 New-6）：返回 `("", "ok")` 时
            # `_miss_is_permanent("ok")` 为 False ⇒ 每次续跑都重抓、永不收敛，
            # 而这正是 H6 要修的那一类。抽取为空是关于该页面的事实，永久。
            return (body, "ok") if body else ("", "抽取为空")
        return "", "跟转过多"
    except UnsafeTarget as exc:
        return "", f"UNSAFE {exc}"
    except Exception as exc:  # noqa: BLE001
        return "", type(exc).__name__


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """把主机解析成地址**对象**，认全部等价写法；不是地址就返回 None。

    **第二版闸只用 `ipaddress.ip_address(host)`，漏了 5 种写法**（2026-09-11 review-gate 的 H7，
    逐条实测）：`127.1` / `127.0.1` / `2130706433`（32 位整数）/ `0x7f000001`（十六进制）
    全部被放过，而它们在本机 `getaddrinfo` 下**真解析到 127.0.0.1**；`localhost.`（尾点 FQDN）
    也被放过。`ipaddress` 只认点分四段的严格写法，而 `inet_aton` 认 BSD 的全部宽松写法——
    **浏览器、curl 与 httpx 走的是后者**，所以判据必须用后者。

    同时订正第二版 docstring 里那句安全论证：它写「只有 `is_loopback_url()` 那一组绕过代理，
    **因此只有它们能到达本机服务**」。**后半句被实测证伪**：经 selector 客户端
    GET `http://127.0.0.1:11434/` 返回 **200 `Ollama is running`**。到得了本机服务的是
    「解析到 loopback 的**全部**写法」，而不是那三个字面量——等价类比字面量集合大得多。
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    # 宽松点分 / 整数 / 十六进制：`inet_aton` 的语义与 libc 解析器一致。
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return None
    return ipaddress.ip_address(packed)
