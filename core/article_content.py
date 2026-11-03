from __future__ import annotations

from typing import Any, Tuple

from core.config import cfg
from core.models.base import DATA_STATUS
from core.mp_article_html import extract_article_html, normalize_mp_article_url
from core.print import print_info, print_warning
from core.process_timeout import (
    ProcessExecutionError,
    ProcessExecutionTimeout,
    run_in_process,
)

# Pages that will not gain a body by switching between HTTP and a browser.
_TERMINAL_FETCH_ERRORS = {"parameter_error", "deleted"}


def normalize_content_mode(mode: str | None = None) -> str:
    normalized = (mode or cfg.get("gather.content_mode", "web") or "web").strip().lower()
    if normalized not in {"web", "api"}:
        return "web"
    return normalized


def extract_origin_article_id(article_id: str, mp_id: str | None = None) -> str:
    if not article_id:
        return ""

    mp_prefix = (mp_id or "").replace("MP_WXS_", "").strip()
    if mp_prefix:
        prefixed = f"{mp_prefix}-"
        if article_id.startswith(prefixed):
            return article_id[len(prefixed):]

    return article_id


def build_article_url(article: Any) -> str:
    article_url = (getattr(article, "url", "") or "").strip()
    if article_url:
        return normalize_mp_article_url(article_url)

    origin_id = extract_origin_article_id(
        getattr(article, "id", ""),
        getattr(article, "mp_id", ""),
    )
    if not origin_id:
        return ""

    return normalize_mp_article_url(f"https://mp.weixin.qq.com/s/{origin_id}")


def parse_wechat_article_page(html: str) -> Tuple[str, str]:
    """Return ``(body_html, error_code)``.

    ``error_code`` is empty when ``body_html`` is usable. ``DELETED`` is
    returned as the body for author-deleted pages.
    """
    raw = html or ""
    if not raw.strip():
        return "", "empty_html"

    has_body_marker = (
        'id="js_content"' in raw
        or "id='js_content'" in raw
        or "rich_media_content" in raw
        or "content_noencode" in raw
    )
    deleted_markers = (
        "该内容已被发布者删除",
        "The content has been deleted by the author.",
        "违规无法查看",
        "该内容暂时无法查看",
        "发送失败无法查看",
        "内容审核中",
    )
    if any(marker in raw for marker in deleted_markers) and not has_body_marker:
        return "DELETED", "deleted"
    if (
        "当前环境异常" in raw
        and "完成验证后即可继续访问" in raw
        and not has_body_marker
    ):
        return "", "env_verify"
    if "参数错误" in raw and not has_body_marker:
        return "", "parameter_error"
    if "你暂无权限查看此页面内容" in raw and not has_body_marker:
        return "", "no_permission"
    if "未知错误，请稍后再试" in raw and not has_body_marker:
        return "", "unknown_error"

    body = extract_article_html(raw)
    if body == "DELETED":
        return "DELETED", "deleted"
    if not is_usable_article_content(body):
        return "", "no_article_body"
    return body, ""


def _download_article_html(url: str) -> Tuple[str, str]:
    """GET a public article page. Returns ``(html, error_code)``."""
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://mp.weixin.qq.com/",
    }
    proxies = None
    target = url
    if cfg.get("proxy.enabled", False):
        http_proxy = (cfg.get("proxy.http_url", "") or "").strip()
        deno_proxy = (cfg.get("proxy.deno_url", "") or "").strip()
        if deno_proxy:
            from urllib.parse import quote

            target = f"{deno_proxy}?url={quote(url, safe='')}"
        elif http_proxy:
            proxies = {"http": http_proxy, "https": http_proxy}

    try:
        response = requests.get(target, headers=headers, proxies=proxies, timeout=(10, 45))
    except requests.RequestException as exc:
        return "", f"http_error:{exc.__class__.__name__}"
    if response.status_code != 200:
        return "", f"http_{response.status_code}"
    return response.text or "", ""


def _fetch_public_article(url: str) -> Tuple[str, dict]:
    """Fast path: download the public page and parse the article body."""
    url = normalize_mp_article_url(url)
    html, error = _download_article_html(url)
    meta = {"url": url, "fetch_error": error, "article_type": "", "source": "http"}
    if error or not (html or "").strip():
        if not error:
            meta["fetch_error"] = "empty_html"
        return "", meta

    content, reason = parse_wechat_article_page(html)
    if content and content != "DELETED":
        try:
            from driver.wxarticle import Web

            content = (Web.clean_article_content(content) or content).strip()
        except Exception as exc:
            print_warning(f"clean public article html failed: {exc}")
    meta["fetch_error"] = "" if content else (reason or "no_article_body")
    return content, meta


def is_usable_article_content(content: str | None) -> bool:
    """Reject WeChat error/shell pages while allowing short media articles."""
    if not (content or "").strip():
        return False

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    text = soup.get_text(" ", strip=True)
    hard_error_markers = (
        "未知错误，请稍后再试",
        "你暂无权限查看此页面内容",
        "当前环境异常，完成验证后即可继续访问",
    )
    if any(marker in text for marker in hard_error_markers):
        return False

    has_wechat_shell = (
        "微信扫一扫可打开此内容" in text
        and "使用完整服务" in text
    )
    meaningful_length = sum(character.isalnum() for character in text)
    if has_wechat_shell and meaningful_length < 300:
        return False

    return bool(text) or soup.find(["img", "video", "audio", "iframe"]) is not None


def _fetch_with_web(url: str) -> Tuple[str, Any]:
    from driver.wxarticle import Web

    url = normalize_mp_article_url(url)
    content, meta = _fetch_public_article(url)
    if content == "DELETED" or is_usable_article_content(content):
        return content, meta

    fetch_error = str((meta or {}).get("fetch_error") or "")
    if fetch_error in _TERMINAL_FETCH_ERRORS:
        print_warning(f"skip browser fetch: {fetch_error} url={url}")
        return "", meta

    result = Web.get_article_content(url) or {}
    browser_content = (result.get("content") or "").strip()
    if not browser_content and not result.get("fetch_error") and fetch_error:
        result["fetch_error"] = fetch_error
    return browser_content, result


def _fetch_with_api(url: str) -> Tuple[str, Any]:
    content, meta = _fetch_public_article(url)
    if content:
        return content, meta
    reason = str((meta or {}).get("fetch_error") or "no_article_body")
    if reason == "no_article_body":
        print_warning("api response does not contain an article body")
    else:
        print_warning(f"api article fetch failed: {reason}")
    return content, meta


def _fetch_article_content_unbounded(
    url: str,
    preferred_mode: str | None = None,
    allow_fallback: bool = True,
) -> Tuple[str, str, str]:
    mode = normalize_content_mode(preferred_mode)
    modes = [mode]
    if allow_fallback:
        modes += [item for item in ("web", "api") if item != mode]

    article_type = ""
    last_mode = mode
    last_detail = ""

    for current_mode in modes:
        last_mode = current_mode
        try:
            if current_mode == "api":
                content, result = _fetch_with_api(normalize_mp_article_url(url))
            else:
                content, result = _fetch_with_web(url)
            article_type = str((result or {}).get("article_type") or "")
        except Exception as exc:
            print_warning(f"fetch article content failed in {current_mode} mode: {exc}")
            last_detail = f"{current_mode}:{exc.__class__.__name__}"
            continue

        fetch_error = str((result or {}).get("fetch_error") or "")
        if fetch_error:
            last_detail = fetch_error
        if content == "DELETED" or fetch_error == "deleted":
            return "DELETED", current_mode, "deleted"
        if is_usable_article_content(content):
            source = str((result or {}).get("source") or "")
            used_mode = "http" if source == "http" else current_mode
            return content, used_mode, article_type
        if content:
            print_warning(f"ignoring unusable article content from {current_mode} mode")
        if fetch_error in _TERMINAL_FETCH_ERRORS:
            break

    return "", last_mode, last_detail or "empty body"


def fetch_article_content(
    url: str,
    preferred_mode: str | None = None,
    timeout: float | None = None,
) -> Tuple[str, str, str]:
    """Fetch article content in an isolated process with a hard timeout."""
    fetch_timeout = float(timeout or cfg.get("gather.content_fetch_timeout", 60) or 60)
    allow_fallback = bool(cfg.get("gather.content_fallback", True))
    return run_in_process(
        _fetch_article_content_unbounded,
        url,
        preferred_mode,
        allow_fallback,
        timeout=fetch_timeout,
    )


def mark_article_fetch_failed(session, article: Any, reason: str) -> None:
    failures = int(getattr(article, "fix_fail_count", 0) or 0) + 1
    max_failures = int(cfg.get("gather.content_max_failures", 3) or 3)
    has_existing_content = is_usable_article_content(
        getattr(article, "content", "")
    )
    article.fix_fail_count = failures
    article.has_content = 1 if has_existing_content else 0
    if hasattr(article, "fetch_started_at"):
        article.fetch_started_at = None
    article.status = (
        DATA_STATUS.FAILED
        if failures >= max_failures and not has_existing_content
        else DATA_STATUS.ACTIVE
    )
    session.commit()
    print_warning(
        f"article {getattr(article, 'id', '')} content fetch failed "
        f"({failures}/{max_failures}): {reason}"
    )


def sync_article_content(
    session,
    article: Any,
    preferred_mode: str | None = None,
    force: bool = False,
) -> Tuple[bool, str]:
    existing_content = (getattr(article, "content", "") or "").strip()
    if is_usable_article_content(existing_content) and not force:
        if getattr(article, "has_content", 0) == 0:
            print_info(f"article {article.id} already has content, skipping fetch")
            article.has_content = 1
            article.status = DATA_STATUS.ACTIVE
            if hasattr(article, "fetch_started_at"):
                article.fetch_started_at = None
            session.commit()
            session.refresh(article)
            return True, "cached"
        return False, "cached"

    article_url = build_article_url(article)
    stored_url = (getattr(article, "url", "") or "").strip()
    if article_url and stored_url and article_url != stored_url:
        print_warning(
            f"article {getattr(article, 'id', '')} url normalized: {stored_url} -> {article_url}"
        )
        article.url = article_url
        session.commit()
    if not article_url:
        print_warning(f"article {getattr(article, 'id', '')} has no valid url")
        mark_article_fetch_failed(session, article, "missing_url")
        return False, "missing_url"

    try:
        content, mode, article_type = fetch_article_content(article_url, preferred_mode)
    except ProcessExecutionTimeout as exc:
        mark_article_fetch_failed(session, article, str(exc))
        return False, "timeout"
    except ProcessExecutionError as exc:
        mark_article_fetch_failed(session, article, str(exc))
        return False, "process_error"
    except Exception as exc:
        session.rollback()
        mark_article_fetch_failed(session, article, str(exc))
        return False, "error"

    if not content:
        detail = article_type or "empty body"
        mark_article_fetch_failed(session, article, f"empty response via {mode}: {detail}")
        return False, mode

    try:
        if content == "DELETED":
            article.content = ""
            article.content_html = ""
            article.status = DATA_STATUS.DELETED
            article.has_content = 0
            if hasattr(article, "fetch_started_at"):
                article.fetch_started_at = None
            session.commit()
            session.refresh(article)
            print_info(f"article {article.id} marked as deleted via {mode}")
            return True, mode

        from driver.wxarticle import Web
        from tools.fix import fix_html

        article.content = content
        article.content_html = fix_html(content)
        article.show_type=article_type or article.show_type
        article.status = DATA_STATUS.ACTIVE
        article.has_content = 1
        if hasattr(article, "fetch_started_at"):
            article.fetch_started_at = None
        if not (getattr(article, "description", "") or "").strip():
            article.description = Web.get_description(content)
        # 修正成功,重置失败计数
        if hasattr(article, 'fix_fail_count'):
            article.fix_fail_count = 0
        session.commit()
        session.refresh(article)
        print_info(f"article {article.id} content synced via {mode}")
        return True, mode
    except Exception as exc:
        session.rollback()
        mark_article_fetch_failed(session, article, str(exc))
        return False, "save_error"
