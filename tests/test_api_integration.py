"""端到端：FastAPI + 爬虫 + 引擎，对本地示例公告站做完整查询。

前置：示例站点已在 127.0.0.1:8080 提供服务（见 README / demo 脚本；
``site/serve_site.py`` 负责对占位标记的详情返回 503）。
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

BASE = os.environ.get("TEST_SITE_BASE", "http://127.0.0.1:8080")
SITE = f"{BASE}/index.html"
UNCERTAIN_SITE = f"{BASE}/uncertain/index.html"
Q = "2026-11-11T10:00:00+08:00"

pytestmark = pytest.mark.asyncio


async def _query(index_url: str) -> dict:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # lifespan 由 LifespanManager 之外的方式不保证执行，手动触发
        async with app.router.lifespan_context(app):
            r = await ac.post("/query", json={"index_url": index_url, "query_time": Q})
    assert r.status_code == 200, r.text
    return r.json()


async def test_full_query_workflow():
    data = await _query(SITE)

    # 四个列表页全部爬到，15 条同站详情（跨站 1 条被跳过）
    assert len(data["list_pages_crawled"]) == 4
    # 014 详情 503、015 详情解析失败：抓取成功 14 个，但只剩 13 条可解析
    assert data["detail_pages_crawled"] == 14
    assert data["announcements_parsed"] == 13
    assert len(data["skipped_offsite_links"]) == 1

    # 失败详情已定位到具体链：整次查询部分可核定，其余独立链照常判定
    assert data["determination"] == "partial"
    assert "N-2026-014" in (data["determination_reason"] or "")

    active = data["active_suspensions"]
    chains = {tuple(a["source_chain"]) for a in active}
    # 有效链：005 原始（012 次日更正不可见）；
    # 003→006→010 两次更正；009→011 更正（带悬空引用）
    assert ("N-2026-005",) in chains
    assert ("N-2026-003", "N-2026-006", "N-2026-010") in chains
    assert ("N-2026-009", "N-2026-011") in chains
    # 001 被 014 撤销：014 详情 503，但链已列为待核实，绝不能仍报为有效
    assert ("N-2026-001",) not in chains

    by_route = {a["route"]: a for a in active}
    # 两次更正后的最终区间
    assert by_route["25 路"]["suspend_end"].startswith("2026-11-20T22:00:00")
    # 同线路两条独立公告分别判断：12 路只剩 005 这条有效停运
    routes_12 = [a for a in active if a["route"] == "12 路"]
    assert len(routes_12) == 1

    reasons = {(c["root_id"], c["reason"]) for c in data["inactive_chains"]}
    assert ("N-2026-002", "expired") in reasons
    assert ("N-2026-004", "cancelled") in reasons
    assert ("N-2026-008", "not_yet_effective") in reasons
    assert ("N-2026-013", "not_yet_effective") in reasons
    # 待核实链不混进“已判定失效”的列表
    assert "N-2026-001" not in {c["root_id"] for c in data["inactive_chains"]}

    # 失败详情保留列表编号、类型、地址与原因
    failed = {f["notice_id"]: f for f in data["failed_details"]}
    assert failed["N-2026-014"]["stage"] == "fetch"
    assert "503" in failed["N-2026-014"]["reason"]
    assert failed["N-2026-014"]["kind"] == "cancellation"
    assert failed["N-2026-014"]["url"].endswith("/notices/N-2026-014.html")
    assert failed["N-2026-014"]["references"] == ["N-2026-001"]
    assert failed["N-2026-015"]["stage"] == "parse"
    assert "发布时间" in failed["N-2026-015"]["reason"]
    assert failed["N-2026-015"]["kind"] == "suspension"

    # 001 链待核实并指出是 014 导致的
    pending = data["pending_verification_chains"]
    p_001 = next(p for p in pending if p["root_id"] == "N-2026-001")
    assert p_001["reason"] == "pending_verification"
    assert p_001["chain"] == ["N-2026-001"]
    assert p_001["failed_notice_ids"] == ["N-2026-014"]
    # 015 是列表已知的独立停运，自身待核实
    p_015 = next(p for p in pending if p["root_id"] == "N-2026-015")
    assert p_015["failed_notice_ids"] == ["N-2026-015"]

    unmatched = data["unmatched_references"]
    assert len(unmatched) == 1
    assert unmatched[0]["notice_id"] == "N-2026-011"
    assert unmatched[0]["missing_reference"] == "N-2026-999"

    # 来源链接完整
    chain25 = next(a for a in active if a["route"] == "25 路")
    assert all(u.startswith(f"{BASE}/notices/") for u in chain25["sources"])


async def test_failed_detail_without_ref_makes_query_indeterminate():
    data = await _query(UNCERTAIN_SITE)

    # 撤销公告详情 503，且列表未标明它引用了哪条链 -> 整次查询无法核定
    assert data["determination"] == "indeterminate"
    assert data["active_suspensions"] == []
    assert data["inactive_chains"] == []

    failed = data["failed_details"]
    assert len(failed) == 1
    assert failed[0]["notice_id"] == "N-2026-020"
    assert failed[0]["kind"] == "cancellation"
    assert failed[0]["stage"] == "fetch"
    assert "503" in failed[0]["reason"]

    pending = data["pending_verification_chains"]
    assert len(pending) == 1
    assert pending[0]["reason"] == "indeterminate"
    assert pending[0]["failed_notice_ids"] == ["N-2026-020"]


async def test_naive_query_time_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            r = await ac.post(
                "/query",
                json={"index_url": SITE, "query_time": "2026-11-11T10:00:00"},
            )
    assert r.status_code == 422


async def test_bad_index_url_returns_502():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            r = await ac.post(
                "/query",
                json={
                    "index_url": "http://127.0.0.1:9/never.html",
                    "query_time": Q,
                },
            )
    assert r.status_code == 502
