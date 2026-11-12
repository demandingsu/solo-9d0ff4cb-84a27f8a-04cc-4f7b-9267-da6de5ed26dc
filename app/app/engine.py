"""公告链还原与停运有效性判定。

规则（对应业务约定）：

* 每条公告按编号唯一标识；更正/撤销公告通过“引用编号”指向上一条公告，
  更正也可以引用另一条更正，从而形成链。
* 链的顺序由引用关系确定，同一节点存在多个后继时按发布时间排序。
* 更正公告携带的停运起止（及线路）替换其之前链上的停运信息；
  链上最后一条非撤销公告的信息即当前生效信息。
* 撤销公告使它所在的整条链失效。
* 查询时刻之后才发布的公告在该时刻尚不可见，不参与判定。
* 同一起始公告若出现分叉后继，每个分支独立成链、独立判定；
  不同根公告（同线路的独立公告）天然分别判断。

详情失败的处理（见 :class:`FailedNotice`）：

* 详情抓取/解析失败的公告，不能当作“不存在”：它可能是一条撤销/更正，
  若忽略会把本应失效的停运误报为有效。
* 列表页标明失败公告引用了哪些编号时，这些编号所属的公告链整体列为
  “待核实”（``pending_verification``），不给出有效/失效结论；
  与它们无关的独立链照常判定。
* 列表页没有足够信息确认失败公告影响哪条链（例如撤销/更正未标引用、
  编号缺失）时，整次查询标记为“无法核定”（``indeterminate``），
  并清空 ``active_suspensions``，不给出任何确定的有效停运结论。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .models import (
    ActiveSuspension,
    Announcement,
    InactiveChain,
    NoticeKind,
    UnmatchedReference,
)


@dataclass
class FailedNotice:
    """详情抓取/解析失败的公告。

    只携带列表页已知的信息（编号、类型、引用编号、地址），
    详情内容本身未知。
    """

    notice_id: str | None
    kind: NoticeKind | None
    references: list[str]
    url: str
    reason: str = ""
    stage: str = "fetch"  # fetch | parse
    title: str | None = None


@dataclass
class _Node:
    announcement: Announcement
    children: list[str] = field(default_factory=list)  # 引用本公告的后继编号


@dataclass
class PendingVerification:
    """待核实链的内部表示（对应模型 ``PendingVerificationChain``）。"""

    root_id: str
    route: str | None
    reason: str
    chain: list[str]
    sources: list[str]
    failed_notice_ids: list[str] = field(default_factory=list)
    failed_urls: list[str] = field(default_factory=list)
    latest_published_at: datetime | None = None


@dataclass
class ReconstructionResult:
    active: list[ActiveSuspension]
    inactive: list[InactiveChain]
    unmatched: list[UnmatchedReference]
    # 编号 -> 公告（便于测试/诊断）
    by_id: dict[str, Announcement]
    pending: list[PendingVerification] = field(default_factory=list)
    determination: str = "determined"
    determination_reason: str | None = None


def _sort_key(published_at: datetime) -> datetime:
    return published_at


def reconstruct(
    announcements: list[Announcement],
    query_time: datetime,
    failures: list[FailedNotice] | None = None,
) -> ReconstructionResult:
    """根据引用关系与发布时间还原公告链并判定有效性。

    ``failures`` 是详情抓取/解析失败的公告；它们不参与正常链判定，
    而是按上面的失败处理规则把相关链列为待核实或把整次查询标为无法核定。
    """
    failures = failures or []

    by_id: dict[str, Announcement] = {}
    for a in announcements:
        if a.notice_id in by_id:
            # 同编号重复抓取：保留发布时间一致的第一条，其余忽略
            continue
        by_id[a.notice_id] = a

    # 1) 无法匹配的引用：被引用编号在整站公告中不存在
    unmatched: list[UnmatchedReference] = []
    for a in announcements:
        for ref in a.references:
            if ref not in by_id:
                unmatched.append(
                    UnmatchedReference(
                        notice_id=a.notice_id, missing_reference=ref, url=a.url, kind=a.kind
                    )
                )

    # 2) 构建后继索引（只连存在的边），并区分查询时刻可见/不可见
    nodes: dict[str, _Node] = {nid: _Node(a) for nid, a in by_id.items()}
    for a in by_id.values():
        for ref in a.references:
            if ref in nodes:
                nodes[ref].children.append(a.notice_id)
    for node in nodes.values():
        node.children.sort(key=lambda cid: _sort_key(by_id[cid].published_at))

    visible = {nid for nid, a in by_id.items() if a.published_at <= query_time}

    # 3) 根 = 不引用任何“查询时刻已发布且存在”的公告的节点（链的出边起点）。
    #    悬空引用（编号不存在）与指向未来公告的引用不阻止其成为根。
    def has_visible_existing_ref(a: Announcement) -> bool:
        return any(
            ref in by_id and by_id[ref].published_at <= query_time
            for ref in a.references
        )

    roots = [nid for nid, a in by_id.items() if not has_visible_existing_ref(a)]
    roots.sort(key=lambda nid: (by_id[nid].published_at, nid))

    # 4) 失败详情定位：把列表给出的引用编号映射到已解析的公告。
    failed_by_id = {
        f.notice_id: f for f in failures if f.notice_id and f.notice_id not in by_id
    }

    def ancestor_paths(node_id: str) -> list[list[str]]:
        """node -> 根 的所有引用路径（根在前），含 node 自身。"""
        paths: list[list[str]] = []

        def dfs(cur: str, path: list[str]) -> None:
            parents = [r for r in by_id[cur].references if r in by_id]
            if not parents or any(p in path for p in parents):
                paths.append(list(reversed(path)))
                return
            for p in parents:
                if p in path:  # 引用环：当前路径即一条以 p 为根的链
                    paths.append(list(reversed(path)))
                else:
                    dfs(p, path + [p])

        dfs(node_id, [node_id])
        return paths

    def resolve_ref(ref: str, visited: set[str]) -> tuple[set[str], bool]:
        """把引用编号解析为已解析公告编号集合。

        返回 ``(ids, unresolved)``：``unresolved=True`` 表示引用经由另一条
        失败公告仍无法落到任何已解析公告（该失败公告的引用关系本身未知）。
        指向整站都不存在的编号不算 unresolved（按既有规则它不改变其他链）。
        """
        if ref in by_id:
            return {ref}, False
        if ref in failed_by_id:
            other = failed_by_id[ref]
            if ref in visited or not other.references:
                return set(), True
            ids: set[str] = set()
            unresolved = False
            for sub in other.references:
                sub_ids, sub_unresolved = resolve_ref(sub, visited | {ref})
                ids |= sub_ids
                unresolved = unresolved or sub_unresolved
            return ids, unresolved
        # 悬空引用：整站不存在，不影响任何既有链
        return set(), False

    # 每条失败公告的归因结果
    pending_entries: list[PendingVerification] = []
    pending_nodes: set[str] = set()     # 已解析公告：其所在链须待核实
    indeterminate_failures: list[FailedNotice] = []
    attributable_failures: list[FailedNotice] = []
    # 受影响的链（根在前） -> 关联的失败公告
    attribution: dict[tuple[str, ...], list[FailedNotice]] = {}

    def leaf_paths(node_id: str, path: list[str]) -> list[list[str]]:
        """从 node 沿查询时刻可见的后继走到叶，返回若干条完整链（含 node 自身）。"""
        kids = [c for c in nodes[node_id].children if c in visible and c not in path]
        if not kids:
            return [path]
        out: list[list[str]] = []
        for kid in kids:
            out.extend(leaf_paths(kid, path + [kid]))
        return out

    for f in failures:
        if f.notice_id and f.notice_id in by_id:
            # 同一编号已有成功解析的公告，失败页不改变结论
            continue

        impacted_ids: set[str] = set()
        unresolved = False

        if not f.notice_id:
            # 地址/编号都无法从列表确认：可能是任意一条链上的公告
            unresolved = True
        elif f.references:
            for ref in f.references:
                ids, ref_unresolved = resolve_ref(ref, {f.notice_id})
                impacted_ids |= ids
                unresolved = unresolved or ref_unresolved
        elif f.kind is NoticeKind.SUSPENSION:
            # 列表已标明是独立停运：不影响其他链，内容未知
            impacted_ids = set()
        else:
            # 更正/撤销（或类型未知）且列表未给引用：无法知道它改的是哪条链
            unresolved = True

        if unresolved:
            indeterminate_failures.append(f)
            continue

        attributable_failures.append(f)

        if not impacted_ids:
            # 自成一链：内容未知，单独待核实
            own_id = f.notice_id or f.url
            pending_entries.append(
                PendingVerification(
                    root_id=own_id,
                    route=None,
                    reason="pending_verification",
                    chain=[own_id],
                    sources=[f.url],
                    failed_notice_ids=[f.notice_id] if f.notice_id else [],
                    failed_urls=[f.url],
                )
            )
            if f.notice_id:
                pending_nodes.add(f.notice_id)
            continue

        # 该失败公告引用到的每条链都整体待核实
        for impacted in sorted(impacted_ids):
            pending_nodes.add(impacted)
            # 被引用的公告本身可能还有后续引用：从该点继续走的分支也待核实
            for path in ancestor_paths(impacted):
                for leaf in leaf_paths(impacted, path):
                    attribution.setdefault(tuple(leaf), []).append(f)

    # 待核实链的父链也被遮蔽：若一条正常链的后继中包含待核实节点，
    # 不能仅凭另一条兄弟分支定性（该兄弟可能也被失败的撤销/更正影响）。
    blocked_paths: set[tuple[str, ...]] = set()
    for leaf_key in attribution:
        for i in range(1, len(leaf_key)):
            blocked_paths.add(leaf_key[:i])

    # 每条受影响的链单独列为待核实，并注明是哪些失败公告导致；空列表
    # （仅被遮蔽的父链）不产生独立条目。
    for chain_key, failed in attribution.items():
        chain = list(chain_key)
        if not chain or not failed:
            continue
        route = None
        latest = None
        for nid in chain:
            a = by_id[nid]
            if a.route:
                route = a.route
            if latest is None or a.published_at > latest:
                latest = a.published_at
        pending_entries.append(
            PendingVerification(
                root_id=chain[0],
                route=route,
                reason="pending_verification",
                chain=chain,
                sources=[by_id[nid].url for nid in chain],
                failed_notice_ids=sorted({f.notice_id for f in failed if f.notice_id}),
                failed_urls=sorted({f.url for f in failed}),
                latest_published_at=latest,
            )
        )

    for f in indeterminate_failures:
        pending_entries.append(
            PendingVerification(
                root_id=f.notice_id or f.url,
                route=None,
                reason="indeterminate",
                chain=[f.notice_id] if f.notice_id else [],
                sources=[f.url],
                failed_notice_ids=[f.notice_id] if f.notice_id else [],
                failed_urls=[f.url],
            )
        )

    # 5) 判定：跳过待核实链，其余链照常判断
    active: list[ActiveSuspension] = []
    inactive: list[InactiveChain] = []

    def walk(path: list[str], seen: set[str]) -> None:
        tail = path[-1]
        # 查询时刻之后才发布的后继公告当时尚不可见，不进入链
        kids = [c for c in nodes[tail].children if c not in seen and c in visible]
        if not kids:
            if tuple(path) not in attribution and tuple(path) not in blocked_paths:
                evaluate_chain(path)
            return
        if any(c in pending_nodes for c in kids):
            return
        for kid in kids:  # 已按发布时间排序
            walk(path + [kid], seen | {kid})
    def evaluate_chain(chain_ids: list[str]) -> None:
        chain_ann = [by_id[i] for i in chain_ids]
        sources = [a.url for a in chain_ann]
        root = chain_ann[0]

        # 整条链在查询时刻都还未发布
        if not any(i in visible for i in chain_ids):
            inactive.append(
                InactiveChain(
                    root_id=root.notice_id,
                    route=root.route,
                    reason="not_yet_effective",
                    chain=chain_ids,
                    sources=sources,
                    latest_published_at=max(a.published_at for a in chain_ann),
                )
            )
            return

        # 撤销 -> 整条链失效（只统计查询时刻已发布的撤销）
        cancels = [
            a
            for a in chain_ann
            if a.kind is NoticeKind.CANCELLATION and a.published_at <= query_time
        ]
        if cancels:
            inactive.append(
                InactiveChain(
                    root_id=root.notice_id,
                    route=_current_route(chain_ann),
                    reason="cancelled",
                    chain=chain_ids,
                    sources=sources,
                    latest_published_at=cancels[-1].published_at,
                )
            )
            return

        # 按发布时间依次应用更正，后者替换前者的停运信息
        route, start, end, latest_pub = _current_interval(chain_ann, query_time)
        if start is None or end is None:
            inactive.append(
                InactiveChain(
                    root_id=root.notice_id,
                    route=route,
                    reason="no_interval",
                    chain=chain_ids,
                    sources=sources,
                    latest_published_at=latest_pub,
                )
            )
            return

        if start > query_time:
            reason = "not_yet_effective"
        elif end <= query_time:
            # 半开区间 [start, end)：查询时刻恰为结束时刻时已失效
            reason = "expired"
        else:
            reason = None

        if reason:
            inactive.append(
                InactiveChain(
                    root_id=root.notice_id,
                    route=route,
                    reason=reason,
                    chain=chain_ids,
                    sources=sources,
                    latest_published_at=latest_pub,
                )
            )
        else:
            active.append(
                ActiveSuspension(
                    route=route or "(未知线路)",
                    suspend_start=start,
                    suspend_end=end,
                    source_chain=chain_ids,
                    sources=sources,
                    published_at=latest_pub,
                )
            )

    def _current_route(chain_ann: list[Announcement]) -> str | None:
        route = None
        for a in sorted(chain_ann, key=lambda x: x.published_at):
            if a.published_at > query_time:
                continue
            if a.route:
                route = a.route
        return route

    def _current_interval(
        chain_ann: list[Announcement], now: datetime
    ) -> tuple[str | None, datetime | None, datetime | None, datetime | None]:
        route = None
        start = end = None
        latest_pub = None
        for a in sorted(chain_ann, key=lambda x: x.published_at):
            if a.published_at > now:
                continue
            latest_pub = a.published_at
            if a.route:
                route = a.route
            if a.suspend_start is not None:
                start = a.suspend_start
            if a.suspend_end is not None:
                end = a.suspend_end
        return route, start, end, latest_pub

    for root_id in roots:
        if root_id in pending_nodes:
            continue
        walk([root_id], {root_id})

    # 待核实链只进入 pending_verification_chains，不进入 inactive_chains，
    # 避免它们的 reason 被误读成“已判定失效”；整次查询无法核定时也不给出
    # 任何确定的有效停运结论。
    determination = "determined"
    reason_text: str | None = None
    if indeterminate_failures:
        determination = "indeterminate"
        ids = [f.notice_id or f.url for f in indeterminate_failures]
        reason_text = (
            "列表未提供足够信息确认失败公告影响哪条公告链，整次查询无法核定；"
            "失败公告: " + "、".join(sorted(ids))
        )
        active = []
    elif attributable_failures:
        determination = "partial"
        ids = [f.notice_id or f.url for f in attributable_failures]
        reason_text = "部分公告链因详情失败待核实: " + "、".join(sorted(ids))

    active.sort(key=lambda x: (x.route, x.suspend_start))
    inactive.sort(key=lambda x: (x.root_id, tuple(x.chain)))
    unmatched.sort(key=lambda x: (x.notice_id, x.missing_reference))
    pending_entries.sort(key=lambda x: (x.root_id, tuple(x.chain)))

    return ReconstructionResult(
        active=active,
        inactive=inactive,
        unmatched=unmatched,
        by_id=by_id,
        pending=pending_entries,
        determination=determination,
        determination_reason=reason_text,
    )
