"""同站分页 + 详情抓取。

约束：
* 只沿同站链接爬行（协议/主机/端口一致），跨站分页或详情链接跳过并记录；
* 分页跟随 ``rel=next``，带最大页数与死循环保护；
* 详情 URL 去重，抓取或解析失败不中断整体查询，作为失败详情收集返回；
* 失败详情仍保留列表页声明的编号、类型、地址及引用编号，
  以便引擎判断它会影响哪条公告链（见 ``engine.reconstruct``）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .models import Announcement
from .parser import (
    DetailLink,
    is_same_site,
    parse_detail,
    parse_detail_links,
    parse_next_page,
)


@dataclass
class DetailFailure:
    """详情抓取/解析失败记录（含列表页已知信息）。"""

    link: DetailLink
    stage: str  # fetch | parse
    reason: str


@dataclass
class CrawlResult:
    announcements: list[Announcement] = field(default_factory=list)
    list_pages: list[str] = field(default_factory=list)
    detail_pages: int = 0
    skipped_offsite: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    failures: list[DetailFailure] = field(default_factory=list)


class _FetchError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Crawler:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_list_pages: int = 50,
        max_detail_pages: int = 500,
    ) -> None:
        self._client = client
        self._max_list_pages = max_list_pages
        self._max_detail_pages = max_detail_pages

    async def _get(self, url: str) -> str:
        """抓取页面；失败抛 ``_FetchError``（携带可读原因）。"""
        try:
            resp = await self._client.get(url, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _FetchError(f"HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise _FetchError(f"{type(exc).__name__}: {exc}") from exc
        return resp.text

    async def crawl(self, index_url: str) -> CrawlResult:
        result = CrawlResult()
        seen_pages: set[str] = set()
        detail_links: list[DetailLink] = []
        seen_details: set[str] = set()

        # 1) 抓列表分页（只走同站 rel=next）
        page_url: str | None = index_url
        while page_url and page_url not in seen_pages and len(seen_pages) < self._max_list_pages:
            seen_pages.add(page_url)
            try:
                html = await self._get(page_url)
            except _FetchError as exc:
                result.errors.append(f"列表页抓取失败 {page_url}: {exc.reason}")
                break
            result.list_pages.append(page_url)

            for link in parse_detail_links(html, page_url):
                if not is_same_site(link.url, index_url):
                    result.skipped_offsite.append(link.url)
                    continue
                if link.url not in seen_details:
                    seen_details.add(link.url)
                    detail_links.append(link)

            nxt = parse_next_page(html, page_url)
            if nxt and not is_same_site(nxt, index_url):
                result.skipped_offsite.append(nxt)
                nxt = None
            page_url = nxt

        # 2) 抓详情（同站已保证），限量保护
        for link in detail_links[: self._max_detail_pages]:
            try:
                html = await self._get(link.url)
            except _FetchError as exc:
                result.errors.append(f"详情页抓取失败 {link.url}: {exc.reason}")
                result.failures.append(
                    DetailFailure(link=link, stage="fetch", reason=exc.reason)
                )
                continue
            result.detail_pages += 1
            try:
                result.announcements.append(parse_detail(html, link.url))
            except ValueError as exc:
                result.errors.append(f"详情页解析失败 {link.url}: {exc}")
                result.failures.append(
                    DetailFailure(link=link, stage="parse", reason=str(exc))
                )

        return result
