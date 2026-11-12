"""引擎层：公告链还原与有效性判定。"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.engine import FailedNotice, reconstruct
from app.models import Announcement, NoticeKind

TZ = "+08:00"


def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def make(
    notice_id: str,
    kind: NoticeKind,
    published: str,
    *,
    route: str | None = "12 路",
    start: str | None = None,
    end: str | None = None,
    refs: list[str] | None = None,
) -> Announcement:
    return Announcement(
        notice_id=notice_id,
        route=route,
        kind=kind,
        suspend_start=parse_dt(start) if start else None,
        suspend_end=parse_dt(end) if end else None,
        published_at=parse_dt(published),
        references=refs or [],
        url=f"http://x/notices/{notice_id}.html",
    )


Q = parse_dt(f"2026-11-11T10:00:00{TZ}")


def test_plain_active_suspension():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-08T09:00:00{TZ}",
             start=f"2026-11-10T06:00:00{TZ}", end=f"2026-11-12T22:00:00{TZ}")
    ]
    res = reconstruct(anns, Q)
    assert len(res.active) == 1
    assert res.active[0].source_chain == ["A1"]


def test_correction_chain_replaces_interval():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-07T09:00:00{TZ}",
             start=f"2026-11-09T06:00:00{TZ}", end=f"2026-11-12T20:00:00{TZ}"),
        make("A2", NoticeKind.CORRECTION, f"2026-11-09T15:00:00{TZ}",
             start=f"2026-11-10T00:00:00{TZ}", end=f"2026-11-15T22:00:00{TZ}",
             refs=["A1"]),
        make("A3", NoticeKind.CORRECTION, f"2026-11-10T08:00:00{TZ}",
             start=f"2026-11-10T00:00:00{TZ}", end=f"2026-11-20T22:00:00{TZ}",
             refs=["A2"]),
    ]
    res = reconstruct(anns, Q)
    assert len(res.active) == 1
    a = res.active[0]
    assert a.source_chain == ["A1", "A2", "A3"]
    assert a.suspend_end == parse_dt(f"2026-11-20T22:00:00{TZ}")
    assert a.published_at == parse_dt(f"2026-11-10T08:00:00{TZ}")
    assert a.sources == [f"http://x/notices/{i}.html" for i in ("A1", "A2", "A3")]


def test_cancellation_kills_whole_chain():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-08T09:00:00{TZ}",
             start=f"2026-11-10T06:00:00{TZ}", end=f"2026-11-20T22:00:00{TZ}"),
        make("A2", NoticeKind.CORRECTION, f"2026-11-09T08:00:00{TZ}",
             start=f"2026-11-10T06:00:00{TZ}", end=f"2026-11-21T22:00:00{TZ}",
             refs=["A1"]),
        make("A3", NoticeKind.CANCELLATION, f"2026-11-09T10:00:00{TZ}",
             route=None, refs=["A2"]),
    ]
    res = reconstruct(anns, Q)
    assert res.active == []
    assert len(res.inactive) == 1
    assert res.inactive[0].reason == "cancelled"
    assert res.inactive[0].chain == ["A1", "A2", "A3"]


def test_future_correction_is_invisible():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-09T09:00:00{TZ}",
             start=f"2026-11-11T05:00:00{TZ}", end=f"2026-11-13T21:00:00{TZ}"),
        make("A2", NoticeKind.CORRECTION, f"2026-11-12T09:00:00{TZ}",
             start=f"2026-11-11T05:00:00{TZ}", end=f"2026-11-14T21:00:00{TZ}",
             refs=["A1"]),
    ]
    res = reconstruct(anns, Q)
    assert len(res.active) == 1
    assert res.active[0].source_chain == ["A1"]
    assert res.active[0].suspend_end == parse_dt(f"2026-11-13T21:00:00{TZ}")


def test_independent_notices_same_route_judged_separately():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-05T09:00:00{TZ}",
             start=f"2026-11-05T06:00:00{TZ}", end=f"2026-11-06T20:00:00{TZ}"),
        make("A2", NoticeKind.SUSPENSION, f"2026-11-09T09:00:00{TZ}",
             start=f"2026-11-11T05:00:00{TZ}", end=f"2026-11-13T21:00:00{TZ}"),
    ]
    res = reconstruct(anns, Q)
    assert len(res.active) == 1
    assert res.active[0].source_chain == ["A2"]
    assert {i.reason for i in res.inactive} == {"expired"}
    assert res.inactive[0].root_id == "A1"


def test_unmatched_reference_reported_but_chain_still_evaluated():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-10T09:00:00{TZ}",
             start=f"2026-11-11T06:00:00{TZ}", end=f"2026-11-13T20:00:00{TZ}"),
        make("A2", NoticeKind.CORRECTION, f"2026-11-10T18:00:00{TZ}",
             start=f"2026-11-11T06:00:00{TZ}", end=f"2026-11-16T20:00:00{TZ}",
             refs=["A1", "GHOST"]),
    ]
    res = reconstruct(anns, Q)
    assert len(res.active) == 1
    assert res.active[0].suspend_end == parse_dt(f"2026-11-16T20:00:00{TZ}")
    assert len(res.unmatched) == 1
    assert res.unmatched[0].notice_id == "A2"
    assert res.unmatched[0].missing_reference == "GHOST"


def test_not_yet_started_and_whole_chain_future():
    future_chain = make("F1", NoticeKind.SUSPENSION, f"2026-11-14T09:00:00{TZ}",
                        route="50 路",
                        start=f"2026-11-15T06:00:00{TZ}",
                        end=f"2026-11-18T20:00:00{TZ}")
    upcoming = make("U1", NoticeKind.SUSPENSION, f"2026-11-09T08:00:00{TZ}",
                    route="5 路",
                    start=f"2026-11-12T06:00:00{TZ}",
                    end=f"2026-11-14T20:00:00{TZ}")
    res = reconstruct([future_chain, upcoming], Q)
    reasons = {i.root_id: i.reason for i in res.inactive}
    assert reasons == {"F1": "not_yet_effective", "U1": "not_yet_effective"}


def test_boundary_end_exclusive_start_inclusive():
    anns = [
        make("A1", NoticeKind.SUSPENSION, f"2026-11-09T09:00:00{TZ}",
             start=f"2026-11-11T10:00:00{TZ}", end=f"2026-11-12T10:00:00{TZ}"),
    ]
    # 起点 == 查询时刻：有效
    assert len(reconstruct(anns, parse_dt(f"2026-11-11T10:00:00{TZ}")).active) == 1
    # 终点 == 查询时刻：已失效（半开区间 [start, end)）
    res = reconstruct(anns, parse_dt(f"2026-11-12T10:00:00{TZ}"))
    assert res.active == []
    assert res.inactive[0].reason == "expired"


def test_dangling_correction_is_its_own_root():
    # 引用全部悬空的更正公告自身作为根，仍需可被解释
    anns = [
        make("A9", NoticeKind.CORRECTION, f"2026-11-10T18:00:00{TZ}",
             refs=["GHOST"]),
    ]
    res = reconstruct(anns, Q)
    assert res.active == []
    assert res.inactive[0].reason == "no_interval"
    assert len(res.unmatched) == 1


def test_timezone_comparison_uses_instant():
    # 同一时刻不同偏移表达，判定结果应一致
    anns = [
        make("A1", NoticeKind.SUSPENSION, "2026-11-08T09:00:00+08:00",
             start="2026-11-11T08:00:00+08:00", end="2026-11-11T12:00:00+08:00"),
    ]
    q_utc = parse_dt("2026-11-11T03:00:00+00:00")  # == 11:00 +08:00
    res = reconstruct(anns, q_utc)
    assert len(res.active) == 1


def test_naive_datetime_rejected_by_model():
    with pytest.raises(Exception):
        Announcement(
            notice_id="X", route="r", kind=NoticeKind.SUSPENSION,
            suspend_start=None, suspend_end=None,
            published_at=parse_dt("2026-11-11T10:00:00"),  # 无时区
            references=[], url="http://x", title=None,
        )


# --- 详情失败：不能把可能已撤销的停运误报为有效 ---


def failed(
    notice_id: str | None,
    kind: NoticeKind | None,
    refs: list[str] | None = None,
    *,
    stage: str = "fetch",
    reason: str = "HTTP 503",
) -> FailedNotice:
    return FailedNotice(
        notice_id=notice_id,
        kind=kind,
        references=refs or [],
        url=f"http://x/notices/{notice_id or 'unknown'}.html",
        reason=reason,
        stage=stage,
    )


def _active_ann(
    notice_id: str, kind: NoticeKind = NoticeKind.SUSPENSION, route: str = "12 路"
) -> Announcement:
    return make(
        notice_id,
        kind,
        f"2026-11-08T09:00:00{TZ}",
        route=route,
        start=f"2026-11-10T06:00:00{TZ}",
        end=f"2026-11-12T22:00:00{TZ}",
    )


def test_failed_cancellation_with_reference_marks_chain_pending():
    anns = [_active_ann("A1")]
    res = reconstruct(
        anns, Q, [failed("A2", NoticeKind.CANCELLATION, ["A1"])]
    )
    # 撤销详情不可用：不能再把 A1 报为有效
    assert res.active == []
    assert res.inactive == []
    assert len(res.pending) == 1
    p = res.pending[0]
    assert p.root_id == "A1"
    assert p.chain == ["A1"]
    assert p.failed_notice_ids == ["A2"]
    assert p.reason == "pending_verification"
    assert res.determination == "partial"


def test_failed_correction_chain_pending_but_other_independent_chain_judged():
    # A1 -> B1 的链被失败更正指向；C1 是另一条独立停运，照常判断
    anns = [
        _active_ann("A1"),
        _active_ann("C1", route="18 路"),
    ]
    res = reconstruct(
        anns, Q, [failed("B1", NoticeKind.CORRECTION, ["A1"])]
    )
    assert {tuple(a.source_chain) for a in res.active} == {("C1",)}
    assert len(res.pending) == 1
    assert res.pending[0].root_id == "A1"
    assert res.determination == "partial"


def test_failed_detail_without_reference_makes_query_indeterminate():
    anns = [_active_ann("A1"), _active_ann("C1", route="18 路")]
    res = reconstruct(
        anns, Q, [failed("B1", NoticeKind.CANCELLATION)]
    )
    # 无法确认影响哪条链：整次查询无法核定，不给出任何有效停运结论
    assert res.active == []
    assert res.determination == "indeterminate"
    assert res.determination_reason
    assert any(p.reason == "indeterminate" for p in res.pending)
    # 已有确定原因的链仍然可以解释
    assert res.inactive == []


def test_failed_detail_missing_id_is_indeterminate():
    res = reconstruct([_active_ann("A1")], Q, [failed(None, None)])
    assert res.active == []
    assert res.determination == "indeterminate"


def test_parse_failure_follows_same_rules():
    # 详情解析失败的撤销公告同样会使相关链待核实，并保留失败地址与原因
    res = reconstruct(
        [_active_ann("A1")],
        Q,
        [failed("A2", NoticeKind.CANCELLATION, ["A1"], stage="parse",
                reason="详情页缺少发布时间: http://x/notices/A2.html")],
    )
    assert res.active == []
    assert res.pending[0].failed_urls == ["http://x/notices/A2.html"]
    assert res.pending[0].root_id == "A1"


def test_failed_independent_suspension_is_pending_without_blocking_others():
    anns = [_active_ann("A1")]
    res = reconstruct(anns, Q, [failed("B1", NoticeKind.SUSPENSION)])
    # 列表已标明是独立停运：只影响自身，A1 照常有效
    assert [a.source_chain for a in res.active] == [["A1"]]
    assert [p.root_id for p in res.pending] == ["B1"]
    assert res.determination == "partial"


def test_failed_reference_chain_through_another_failure_is_indeterminate():
    # B1 的详情失败，B2 引用 B1；B1 的列表又没给引用，无法定位链
    anns = [_active_ann("A1")]
    res = reconstruct(
        anns,
        Q,
        [failed("B2", NoticeKind.CORRECTION, ["B1"]), failed("B1", NoticeKind.CANCELLATION)],
    )
    assert res.determination == "indeterminate"
    assert res.active == []


def test_failed_reference_to_nonexistent_id_is_own_unknown_chain():
    # 失败更正引用了整站不存在的编号：按既有规则它自成一链，内容未知 -> 待核实
    anns = [_active_ann("A1")]
    res = reconstruct(
        anns, Q, [failed("B1", NoticeKind.CORRECTION, ["GHOST"])]
    )
    assert [a.source_chain for a in res.active] == [["A1"]]
    assert [p.root_id for p in res.pending] == ["B1"]
    assert res.determination == "partial"


def test_successful_duplicate_of_failed_id_is_not_pending():
    # 同编号已有成功解析的公告时，失败页不再影响结论
    anns = [
        _active_ann("A1"),
        make("B1", NoticeKind.CANCELLATION, f"2026-11-09T12:00:00{TZ}",
             route="12 路", refs=["A1"]),
    ]
    res = reconstruct(anns, Q, [failed("B1", NoticeKind.CANCELLATION, ["A1"])])
    assert res.determination == "determined"
    assert res.pending == []
    assert res.active == []
    assert res.inactive[0].reason == "cancelled"


def test_failure_inside_visible_chain_blocks_that_branch_only():
    # A1 有两个分支：A2 与 C1；失败更正指向 A2 所在分支
    anns = [
        _active_ann("A1"),
        make("A2", NoticeKind.CORRECTION, f"2026-11-09T09:00:00{TZ}",
             start=f"2026-11-10T06:00:00{TZ}", end=f"2026-11-20T22:00:00{TZ}",
             refs=["A1"]),
        _active_ann("C1", route="18 路"),
    ]
    res = reconstruct(
        anns, Q, [failed("B1", NoticeKind.CANCELLATION, ["A2"])]
    )
    # 受影响分支待核实（A1->A2），C1 照常有效
    assert [a.source_chain for a in res.active] == [["C1"]]
    assert [p.chain for p in res.pending] == [["A1", "A2"]]


def test_pending_branch_shadows_sibling_branch_of_same_root():
    # A1 有 A2、A3 两个更正分支；失败公告指向 A2。
    # 不能仅凭 A3 分支给出有效结论：它同样可能被失败公告影响。
    anns = [
        _active_ann("A1"),
        make("A2", NoticeKind.CORRECTION, f"2026-11-09T09:00:00{TZ}",
             start=f"2026-11-10T06:00:00{TZ}", end=f"2026-11-20T22:00:00{TZ}",
             refs=["A1"]),
        make("A3", NoticeKind.CORRECTION, f"2026-11-09T10:00:00{TZ}",
             start=f"2026-11-10T06:00:00{TZ}", end=f"2026-11-21T22:00:00{TZ}",
             refs=["A1"]),
        _active_ann("X1", route="18 路"),
    ]
    res = reconstruct(
        anns, Q, [failed("B1", NoticeKind.CORRECTION, ["A2"])]
    )
    # 根 A1 下的所有分支都不给结论；无关的 X1 照常有效
    assert [a.source_chain for a in res.active] == [["X1"]]
    assert [p.chain for p in res.pending] == [["A1", "A2"]]
    assert res.determination == "partial"
