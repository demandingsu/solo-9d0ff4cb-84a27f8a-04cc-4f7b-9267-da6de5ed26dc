"""解析器：分页链接、详情链接与详情字段。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.parser import (
    is_same_site,
    parse_detail,
    parse_detail_links,
    parse_next_page,
)
from app.models import NoticeKind

HTML_DIR = Path(__file__).parent.parent / "site" / "html"


def test_next_page_and_detail_links_index():
    html = (HTML_DIR / "index.html").read_text(encoding="utf-8")
    nxt = parse_next_page(html, "http://site:8080/index.html")
    assert nxt == "http://site:8080/page2.html"

    links = parse_detail_links(html, "http://site:8080/index.html")
    assert len(links) == 5
    assert links[0].notice_id == "N-2026-001"
    assert links[0].url == "http://site:8080/notices/N-2026-001.html"
    assert links[0].kind is NoticeKind.SUSPENSION


def test_last_page_has_no_next():
    html = (HTML_DIR / "page4.html").read_text(encoding="utf-8")
    assert parse_next_page(html, "http://site:8080/page4.html") is None


def test_offsite_link_absolute_and_detected():
    html = (HTML_DIR / "page3.html").read_text(encoding="utf-8")
    links = parse_detail_links(html, "http://site:8080/page3.html")
    offsite = [l.url for l in links if "external.example.org" in l.url]
    assert offsite == ["http://external.example.org/notice/N-EXT-001.html"]
    assert not is_same_site(offsite[0], "http://site:8080/index.html")
    assert is_same_site("http://site:8080/notices/x.html", "http://site:8080/index.html")


def test_list_entry_carries_reference_ids_for_failed_detail():
    # 014 的详情返回 503，列表页标明了它引用的编号
    html = (HTML_DIR / "page3.html").read_text(encoding="utf-8")
    links = parse_detail_links(html, "http://site:8080/page3.html")
    link = next(l for l in links if l.notice_id == "N-2026-014")
    assert link.kind is NoticeKind.CANCELLATION
    assert link.references == ["N-2026-001"]


def test_parse_suspension_detail():
    html = (HTML_DIR / "notices" / "N-2026-001.html").read_text(encoding="utf-8")
    a = parse_detail(html, "http://site:8080/notices/N-2026-001.html")
    assert a.notice_id == "N-2026-001"
    assert a.route == "12 路"
    assert a.kind.value == "suspension"
    assert a.suspend_start.isoformat() == "2026-11-10T06:00:00+08:00"
    assert a.suspend_end.isoformat() == "2026-11-12T22:00:00+08:00"
    assert a.published_at.utcoffset() is not None


def test_parse_correction_with_chained_reference():
    html = (HTML_DIR / "notices" / "N-2026-010.html").read_text(encoding="utf-8")
    a = parse_detail(html, "http://site:8080/notices/N-2026-010.html")
    assert a.kind.value == "correction"
    assert a.references == ["N-2026-006"]


def test_parse_cancellation_detail():
    html = (HTML_DIR / "notices" / "N-2026-007.html").read_text(encoding="utf-8")
    a = parse_detail(html, "http://site:8080/notices/N-2026-007.html")
    assert a.kind.value == "cancellation"
    assert a.suspend_start is None
    assert a.references == ["N-2026-004"]


def test_parse_multi_reference_detail():
    html = (HTML_DIR / "notices" / "N-2026-011.html").read_text(encoding="utf-8")
    a = parse_detail(html, "http://site:8080/notices/N-2026-011.html")
    assert a.references == ["N-2026-009", "N-2026-999"]


def test_missing_id_raises():
    bad = "<html><body><table class='notice-fields'></table></body></html>"
    with pytest.raises(ValueError):
        parse_detail(bad, "http://site/x.html")
