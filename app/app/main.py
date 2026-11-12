"""FastAPI 入口：POST /query 采集并判定临时停运。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException

from .crawler import Crawler
from .engine import FailedNotice, reconstruct
from .models import (
    FailedDetail,
    FailureStage,
    PendingVerificationChain,
    QueryRequest,
    QueryResponse,
)

logger = logging.getLogger("bus_notice_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        headers={"User-Agent": "bus-suspension-api/1.0"},
    ) as client:
        app.state.http_client = client
        yield


app = FastAPI(
    title="公交临时停运公告采集 API",
    version="1.0.0",
    description=(
        "从运营方分页发布的线路公告中，还原更正/撤销公告链，"
        "返回查询时刻仍有效的临时停运区间。"
    ),
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    crawler = Crawler(app.state.http_client)
    crawl = await crawler.crawl(req.index_url)

    if not crawl.list_pages:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "公告首页无法抓取或无任何分页内容",
                "errors": crawl.errors,
            },
        )

    failed_details = [
        FailedDetail(
            notice_id=f.link.notice_id,
            kind=f.link.kind,
            kind_raw=f.link.kind_raw,
            url=f.link.url,
            stage=FailureStage(f.stage),
            reason=f.reason,
            references=f.link.references,
            title=f.link.title,
        )
        for f in crawl.failures
    ]

    result = reconstruct(
        crawl.announcements,
        req.query_time,
        failures=[
            FailedNotice(
                notice_id=f.link.notice_id,
                kind=f.link.kind,
                references=f.link.references,
                url=f.link.url,
                reason=f.reason,
                stage=f.stage,
                title=f.link.title,
            )
            for f in crawl.failures
        ],
    )

    if crawl.errors:
        logger.warning("抓取过程出现 %d 个错误: %s", len(crawl.errors), crawl.errors[:3])

    return QueryResponse(
        index_url=req.index_url,
        query_time=req.query_time,
        list_pages_crawled=crawl.list_pages,
        detail_pages_crawled=crawl.detail_pages,
        announcements_parsed=len(crawl.announcements),
        determination=result.determination,
        determination_reason=result.determination_reason,
        active_suspensions=result.active,
        inactive_chains=result.inactive,
        pending_verification_chains=[
            PendingVerificationChain(
                root_id=p.root_id,
                route=p.route,
                reason=p.reason,
                chain=p.chain,
                sources=p.sources,
                failed_notice_ids=p.failed_notice_ids,
                failed_urls=p.failed_urls,
                latest_published_at=p.latest_published_at,
            )
            for p in result.pending
        ],
        failed_details=failed_details,
        unmatched_references=result.unmatched,
        skipped_offsite_links=crawl.skipped_offsite,
    )
