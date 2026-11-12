"""生成离线示例公告站（纯静态 HTML）。

主站场景覆盖（查询时刻 2026-11-11T10:00+08:00）：
  001 12 路原始停运，查询时有效；014 撤销公告详情返回 503（列表标明引用 001）
      -> 001 所在链列为待核实，不再报为有效
  002 18 路已过停运结束时间（过期）
  003 25 路原始停运，006 更正、010 再更正（更正引用更正，区间被两次替换）
  004 33 路停运，007 撤销（整条链失效）
  005 12 路另一条独立停运（同线路独立判断），012 更正发布于查询时刻之后（不可见）
  008 5 路停运区间尚未开始
  009 40 路停运，011 更正同时引用了不存在的 N-2026-999（无法匹配的引用）
  013 50 路整条公告发布于查询时刻之后（时间门控）
  015 70 路停运公告详情缺“发布时间”，解析失败（列表可知为独立停运）
  另有一个跨站详情链接，验证同站限制。

uncertain/ 子站（查询时刻同上）：
  020 撤销公告详情返回 503，列表未标明引用 -> 整次查询无法核定
  021 80 路停运，本来有效，但因为整次查询无法核定，不给出确定结论
"""
from __future__ import annotations

import html
import shutil
from dataclasses import dataclass, field
from pathlib import Path

OUT_DIR = Path(__file__).parent / "html"
PAGE_SIZE = 5
TZ = "+08:00"


@dataclass
class Notice:
    notice_id: str
    kind: str  # suspension | correction | cancellation
    title: str
    route: str | None
    published_at: str
    suspend_start: str | None = None
    suspend_end: str | None = None
    references: list[str] = field(default_factory=list)
    offsite: bool = False  # 列表项指向外站
    fail_mode: str | None = None  # http_503：详情由示例站服务器返回 503
    list_refs: list[str] = field(default_factory=list)  # 列表页标明的引用编号
    omit_fields: set[str] = field(default_factory=set)  # 生成详情时省略的字段


NOTICES: list[Notice] = [
    Notice("N-2026-001", "suspension", "12 路临时停运公告", "12 路",
           "2026-11-08T09:00:00+08:00", "2026-11-10T06:00:00+08:00",
           "2026-11-12T22:00:00+08:00"),
    Notice("N-2026-002", "suspension", "18 路临时停运公告", "18 路",
           "2026-11-03T14:00:00+08:00", "2026-11-05T06:00:00+08:00",
           "2026-11-08T20:00:00+08:00"),
    Notice("N-2026-003", "suspension", "25 路临时停运公告", "25 路",
           "2026-11-07T16:30:00+08:00", "2026-11-09T06:00:00+08:00",
           "2026-11-12T20:00:00+08:00"),
    Notice("N-2026-004", "suspension", "33 路临时停运公告", "33 路",
           "2026-11-08T11:00:00+08:00", "2026-11-10T05:30:00+08:00",
           "2026-11-20T22:00:00+08:00"),
    Notice("N-2026-005", "suspension", "12 路临时停运公告（施工绕行）", "12 路",
           "2026-11-09T17:20:00+08:00", "2026-11-11T05:00:00+08:00",
           "2026-11-13T21:00:00+08:00"),
    Notice("N-2026-006", "correction", "25 路停运时间更正公告", "25 路",
           "2026-11-09T15:00:00+08:00", "2026-11-10T00:00:00+08:00",
           "2026-11-15T22:00:00+08:00", references=["N-2026-003"]),
    Notice("N-2026-007", "cancellation", "33 路停运撤销公告", "33 路",
           "2026-11-09T10:00:00+08:00", references=["N-2026-004"]),
    Notice("N-2026-008", "suspension", "5 路临时停运公告", "5 路",
           "2026-11-09T08:00:00+08:00", "2026-11-12T06:00:00+08:00",
           "2026-11-14T20:00:00+08:00"),
    Notice("N-2026-009", "suspension", "40 路临时停运公告", "40 路",
           "2026-11-10T09:30:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-13T20:00:00+08:00"),
    Notice("N-2026-010", "correction", "25 路停运时间第二次更正公告", "25 路",
           "2026-11-10T08:00:00+08:00", "2026-11-10T00:00:00+08:00",
           "2026-11-20T22:00:00+08:00", references=["N-2026-006"]),
    Notice("N-2026-011", "correction", "40 路停运更正公告（含一处错误引用）", "40 路",
           "2026-11-10T18:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-16T20:00:00+08:00",
           references=["N-2026-009", "N-2026-999"]),
    Notice("N-2026-012", "correction", "12 路施工绕行安排更正（次日发布）", "12 路",
           "2026-11-12T09:00:00+08:00", "2026-11-11T05:00:00+08:00",
           "2026-11-14T21:00:00+08:00", references=["N-2026-005"]),
    Notice("N-2026-013", "suspension", "50 路临时停运公告（提前发布）", "50 路",
           "2026-11-14T09:00:00+08:00", "2026-11-15T06:00:00+08:00",
           "2026-11-18T20:00:00+08:00"),
    Notice("N-EXT-001", "suspension", "外站转载公告（跨站链接示例）", "99 路",
           "2026-11-08T09:00:00+08:00", offsite=True),
    # 详情页返回 503：列表标明它撤销了 001，故 001 所在链应列为待核实
    Notice("N-2026-014", "cancellation", "12 路停运撤销公告（详情暂不可用）", "12 路",
           "2026-11-09T12:00:00+08:00", references=["N-2026-001"],
           fail_mode="http_503", list_refs=["N-2026-001"]),
    # 详情页缺发布时间：列表已知是 70 路独立停运，其自身内容待核实
    Notice("N-2026-015", "suspension", "70 路临时停运公告（页面字段不全）", "70 路",
           "2026-11-09T11:00:00+08:00", "2026-11-11T06:00:00+08:00",
           "2026-11-14T20:00:00+08:00", omit_fields={"published_at"}),
]

# 第二个站点：撤销公告详情 503 且列表未标明引用 -> 整次查询无法核定
UNCERTAIN_NOTICES: list[Notice] = [
    Notice("N-2026-021", "suspension", "80 路临时停运公告", "80 路",
           "2026-11-08T09:00:00+08:00", "2026-11-10T06:00:00+08:00",
           "2026-11-13T22:00:00+08:00"),
    Notice("N-2026-020", "cancellation", "停运撤销公告（详情暂不可用）", None,
           "2026-11-09T12:00:00+08:00", fail_mode="http_503"),
]

KIND_CN = {"suspension": "停运", "correction": "更正", "cancellation": "撤销"}


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def detail_path(n: Notice) -> str:
    return f"notices/{n.notice_id}.html"


def _marker_path(n: Notice) -> str:
    """需返回 503 的详情，写入占位标记文件而非 HTML（生成器脚本名以 _ 开头）。"""
    return f"notices/_{n.notice_id}.unavailable"


def render_detail(n: Notice) -> str:
    rows: list[str] = []

    def row(field_name: str, label: str, value: str | None, is_time: bool = False) -> None:
        if value is None or field_name in n.omit_fields:
            return
        if is_time:
            cell = f'<time datetime="{_esc(value)}">{_esc(value.replace("T", " "))}</time>'
        else:
            cell = _esc(value)
        rows.append(
            f'      <tr data-field="{field_name}"><th>{label}</th><td>{cell}</td></tr>'
        )

    row("notice_id", "公告编号", n.notice_id)
    row("kind", "类型", KIND_CN[n.kind])
    row("route", "线路", n.route)
    row("suspend_start", "停运开始时间", n.suspend_start, is_time=True)
    row("suspend_end", "停运结束时间", n.suspend_end, is_time=True)
    row("published_at", "发布时间", n.published_at, is_time=True)

    refs_html = ""
    if n.references:
        links = "、".join(
            f'<a class="reference" data-ref-id="{_esc(r)}" href="/notices/{_esc(r)}.html">'
            f"{_esc(r)}</a>"
            for r in n.references
        )
        refs_html = f"\n    <section class=refs><h2>引用公告</h2><p>{links}</p></section>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>{_esc(n.title)}</title></head>
<body>
  <article>
    <h1 class="notice-title">{_esc(n.title)}</h1>
    <table class="notice-fields">
{chr(10).join(rows)}
    </table>{refs_html}
    <p><a href="../index.html">返回公告列表</a></p>
  </article>
</body>
</html>
"""


def render_list(page_notices: list[Notice], page_idx: int, total_pages: int) -> str:
    items: list[str] = []
    for n in page_notices:
        if n.offsite:
            href = "http://external.example.org/notice/N-EXT-001.html"
        else:
            href = detail_path(n)
        ref_attr = ""
        if n.list_refs:
            ref_attr = f' data-ref-ids="{_esc(",".join(n.list_refs))}"'
        items.append(
            f'    <article class="notice" data-id="{_esc(n.notice_id)}" '
            f'data-kind="{n.kind}"{ref_attr}>\n'
            f'      <a class="detail-link" href="{_esc(href)}">{_esc(n.title)}</a>\n'
            f'      <span class="published">{_esc(n.published_at)}</span>\n'
            f"    </article>"
        )

    pager_parts: list[str] = []
    if page_idx > 0:
        prev_file = "index.html" if page_idx == 1 else f"page{page_idx}.html"
        pager_parts.append(f'<a rel="prev" href="{prev_file}">上一页</a>')
    if page_idx < total_pages - 1:
        next_file = "page2.html" if page_idx == 0 else f"page{page_idx + 2}.html"
        pager_parts.append(f'<a rel="next" href="{next_file}">下一页</a>')
    pager = f'  <nav class="pager">{" ".join(pager_parts)}</nav>' if pager_parts else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>线路公告列表（第 {page_idx + 1} 页）</title></head>
<body>
  <h1>线路公告列表</h1>
{chr(10).join(items)}
{pager}
</body>
</html>
"""


def build() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    (OUT_DIR / "notices").mkdir(parents=True)

    real_notices = [n for n in NOTICES if not n.offsite]
    for n in real_notices:
        if n.fail_mode == "http_503":
            # 页面实体不存在；服务器脚本看到占位标记后返回 503
            (OUT_DIR / _marker_path(n)).write_text("503\n", encoding="utf-8")
            continue
        (OUT_DIR / detail_path(n)).write_text(render_detail(n), encoding="utf-8")

    pages: list[list[Notice]] = [
        NOTICES[i : i + PAGE_SIZE] for i in range(0, len(NOTICES), PAGE_SIZE)
    ]
    total = len(pages)
    for idx, page_notices in enumerate(pages):
        name = "index.html" if idx == 0 else f"page{idx + 1}.html"
        (OUT_DIR / name).write_text(
            render_list(page_notices, idx, total), encoding="utf-8"
        )

    # 第二个站点：列表未标明失败撤销公告引用的是什么，整次查询将无法核定
    u_dir = OUT_DIR / "uncertain"
    (u_dir / "notices").mkdir(parents=True)
    for n in UNCERTAIN_NOTICES:
        if n.fail_mode == "http_503":
            (u_dir / _marker_path(n)).write_text("503\n", encoding="utf-8")
            continue
        (u_dir / detail_path(n)).write_text(render_detail(n), encoding="utf-8")
    (u_dir / "index.html").write_text(
        render_list(UNCERTAIN_NOTICES, 0, 1), encoding="utf-8"
    )

    # 主站根列表显式链到 uncertain 子站，便于离线演示“无法核定”
    index_path = OUT_DIR / "index.html"
    index_html = index_path.read_text(encoding="utf-8")
    index_html = index_html.replace(
        "</body>",
        '  <p><a href="uncertain/index.html">另一公告站（含无法核定的撤销公告）</a></p>\n</body>',
    )
    index_path.write_text(index_html, encoding="utf-8")

    print(
        f"示例站点已生成: {OUT_DIR}（主站 {len(real_notices)} 条公告，{total} 个列表页；"
        f"uncertain 子站 {len(UNCERTAIN_NOTICES)} 条公告）"
    )


if __name__ == "__main__":
    build()
