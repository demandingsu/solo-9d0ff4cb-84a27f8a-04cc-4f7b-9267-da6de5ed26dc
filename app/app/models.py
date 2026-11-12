"""领域数据模型。

所有时间在系统内部统一为时区感知的 ``datetime``（UTC 或带原始偏移量均可，
比较时 Python 会自动换算）。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class NoticeKind(str, Enum):
    """公告类型。"""

    SUSPENSION = "suspension"          # 原始停运公告
    CORRECTION = "correction"          # 更正公告
    CANCELLATION = "cancellation"      # 撤销公告


class Announcement(BaseModel):
    """从详情页解析出的单条公告。"""

    notice_id: str = Field(..., description="公告编号，如 2026-001")
    route: str | None = Field(None, description="线路，如 12 路；更正/撤销页可能缺失")
    kind: NoticeKind
    suspend_start: datetime | None = None
    suspend_end: datetime | None = None
    published_at: datetime
    references: list[str] = Field(default_factory=list, description="更正/撤销引用的公告编号")
    url: str
    title: str | None = None

    @field_validator("published_at")
    @classmethod
    def _published_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("published_at 必须带时区信息")
        return v


class QueryRequest(BaseModel):
    """查询入参。"""

    index_url: str = Field(..., description="公告列表首页 URL（同站分页的起点）")
    query_time: datetime = Field(..., description="带时区的查询时刻，ISO 8601")

    @field_validator("index_url")
    @classmethod
    def _url_scheme(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("index_url 必须是 http/https URL")
        return v

    @field_validator("query_time")
    @classmethod
    def _query_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("query_time 必须带时区，例如 2026-11-11T10:00:00+08:00")
        return v


class ActiveSuspension(BaseModel):
    """查询时刻仍然有效的停运区间。"""

    route: str
    suspend_start: datetime
    suspend_end: datetime
    source_chain: list[str] = Field(..., description="形成该结论的公告链编号，根公告在前")
    sources: list[str] = Field(..., description="链上各公告详情页链接")
    published_at: datetime = Field(..., description="链上最新生效公告的发布时间")


class InactiveChain(BaseModel):
    """未产生有效停运的公告链，用于解释判定过程。"""

    root_id: str
    route: str | None
    reason: str = Field(..., description="cancelled / not_yet_effective / expired / no_interval")
    chain: list[str]
    sources: list[str]
    latest_published_at: datetime | None = None


class UnmatchedReference(BaseModel):
    """指向不存在公告的引用。"""

    notice_id: str = Field(..., description="发出引用的公告编号")
    missing_reference: str
    url: str
    kind: NoticeKind


class FailureStage(str, Enum):
    """失败发生的阶段。"""

    FETCH = "fetch"      # 详情页 HTTP 抓取失败（如 503）
    PARSE = "parse"      # 详情页已取回但字段解析失败


class FailedDetail(BaseModel):
    """抓取或解析失败的详情。

    即使详情不可用，只要列表页给出了信息，也要保留列表声明的编号、类型与地址，
    供调度员核对并定位受影响公告链。
    """

    notice_id: str | None = Field(None, description="列表页声明的公告编号（缺失则为 null）")
    kind: NoticeKind | None = Field(None, description="列表页声明的公告类型（可识别时）")
    kind_raw: str | None = Field(None, description="列表页原始类型值（无法识别时保留原文）")
    url: str = Field(..., description="失败的详情页地址")
    stage: FailureStage
    reason: str = Field(..., description="失败原因，如 HTTP 503 / 详情页缺少发布时间")
    references: list[str] = Field(
        default_factory=list, description="列表页标明的引用编号，用于定位受影响公告链"
    )
    title: str | None = None


class PendingVerificationChain(BaseModel):
    """因详情失败而无法核实的公告链。

    这些链不参与有效停运结论，需调度员人工核实失败公告后再判定。
    """

    root_id: str
    route: str | None
    reason: str = Field(
        ...,
        description="pending_verification（已定位到具体链）/ indeterminate（整次查询无法核定）",
    )
    chain: list[str]
    sources: list[str]
    failed_notice_ids: list[str] = Field(default_factory=list)
    failed_urls: list[str] = Field(default_factory=list)
    latest_published_at: datetime | None = None


class Determination(str, Enum):
    """整次查询的可核定程度。"""

    DETERMINED = "determined"        # 所有详情均成功，结论完整
    PARTIAL = "partial"              # 失败公告已定位到具体链，其余独立链照常判定
    INDETERMINATE = "indeterminate"  # 无法确认失败公告影响哪条链，整次查询无法核定


class QueryResponse(BaseModel):
    """查询结果。"""

    index_url: str
    query_time: datetime
    list_pages_crawled: list[str]
    detail_pages_crawled: int
    announcements_parsed: int
    determination: Determination = Field(
        ..., description="determined / partial / indeterminate"
    )
    determination_reason: str | None = Field(
        None, description="无法核定或仅部分可核定时的人类可读原因"
    )
    active_suspensions: list[ActiveSuspension]
    inactive_chains: list[InactiveChain]
    pending_verification_chains: list[PendingVerificationChain] = Field(
        default_factory=list, description="详情失败导致待核实的公告链"
    )
    failed_details: list[FailedDetail] = Field(
        default_factory=list, description="抓取/解析失败的详情，保留列表编号、类型与地址"
    )
    unmatched_references: list[UnmatchedReference]
    skipped_offsite_links: list[str] = Field(default_factory=list)
