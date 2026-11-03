# -*- coding: utf-8 -*-
"""Rewrite stored mp.weixin.qq.com short links that use WeRead's ``~`` token.

WeChat returns ``参数错误`` for those URLs (no ``og:title``, no article body).
The canonical token uses ``_``. This script updates the stored URL and reopens
content fetch for rows that still have no body.

Usage: python scripts/fix_weread_mp_urls.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from core.db import DB
from core.models.article import Article
from core.models.base import DATA_STATUS
from core.mp_article_html import normalize_mp_article_url


def main():
    session = DB.get_session()
    try:
        rows = (
            session.query(Article)
            .filter(Article.url.contains("~"))
            .all()
        )
        fixed = 0
        for art in rows:
            new_url = normalize_mp_article_url(art.url or "")
            if not new_url or new_url == art.url:
                continue
            print(f"[修复] {art.id}\n  {art.url}\n  -> {new_url}")
            art.url = new_url
            if not (art.content or "").strip():
                art.fix_fail_count = 0
                art.has_content = 0
                if art.status == DATA_STATUS.FAILED:
                    art.status = DATA_STATUS.ACTIVE
            fixed += 1
        if fixed:
            session.commit()
        print(f"\n修复 {fixed} 条")
    finally:
        session.close()


if __name__ == "__main__":
    main()
