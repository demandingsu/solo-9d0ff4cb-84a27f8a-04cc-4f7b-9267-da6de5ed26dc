# 公交临时停运公告采集 API

调度员出车前，需要从运营方**分页发布**的线路公告中，查出“查询时刻仍有效”的临时停运。
本服务用 FastAPI + BeautifulSoup 抓取同站分页与详情页，按公告编号引用关系与发布时间
还原**更正/撤销公告链**，返回有效停运区间、来源链接及无法匹配的引用。

## 业务规则

| 规则 | 实现 |
| --- | --- |
| 公告按编号唯一标识 | `Announcement.notice_id` |
| 更正可引用另一条更正 | 引用图上从根（无有效出边引用的公告）沿后继遍历，支持任意深度 |
| 链顺序 | 引用关系定链，同一节点多个后继按发布时间排序 |
| 更正替换原停运信息 | 按发布时间依次应用，链上最后一条可见公告的起止/线路为当前生效信息 |
| 撤销使整条链失效 | 链上出现查询时刻已发布的撤销公告 → `inactive_chains[].reason=cancelled` |
| 未来公告不可见 | 发布时间晚于查询时刻的公告不参与链还原与判定 |
| 同线路独立公告分别判断 | 每条根公告独立成链；示例中 12 路有两条同时有效的独立停运 |
| 无法匹配的引用 | 被引用编号在整站不存在时收集到 `unmatched_references`，不影响其余判定 |
| 同站限制 | 只沿协议+主机+端口一致的链接爬行，跨站链接跳过并在 `skipped_offsite_links` 返回 |
| 详情失败不可当作“公告不存在” | 失败详情保留列表编号/类型/地址/原因到 `failed_details`，绝不忽略后把可能已撤销的停运报为有效 |
| 列表标明失败公告的引用编号 | 被引用编号所属的公告链列为 `pending_verification_chains`（`reason=pending_verification`），不给出有效/失效结论；其他独立链照常判断 |
| 列表不足以定位失败公告影响哪条链 | 整次查询 `determination=indeterminate`，清空 `active_suspensions`，不给出任何确定的有效停运结论 |
| 详情解析失败同样适用 | 抓取 503 与解析失败（如缺发布时间）走同一套规则，均返回失败地址与原因 |

停运区间按半开 `[suspend_start, suspend_end)` 判定：查询时刻等于结束时刻即视为已失效。
所有时间均要求带时区（ISO 8601 偏移），比较基于同一时间线瞬间。

### 详情失败与可核定程度

`/query` 返回的 `determination` 表明结论的完整程度：

| 取值 | 含义 | `active_suspensions` |
| --- | --- | --- |
| `determined` | 全部详情成功，结论完整 | 完整 |
| `partial` | 失败公告已定位到具体链，这些链待核实 | 仅包含可核定的独立链，绝不包含待核实链 |
| `indeterminate` | 列表信息不足以确认失败公告影响哪条链 | 清空，不给出确定结论 |

配套字段：

* `failed_details[]`：每条失败详情的 `notice_id`、`kind`、`url`、`stage`（`fetch`/`parse`）、
  `reason`、`references`（列表标明的引用编号）、`title`。即使详情不可用，也保留列表已知信息。
* `pending_verification_chains[]`：待核实链的 `root_id`、`chain`、`sources`、
  `failed_notice_ids`、`failed_urls`，以及 `reason`（`pending_verification` / `indeterminate`）。
  待核实链不会出现在 `inactive_chains` 中，避免被误读为“已判定失效”。

列表页若已知某条公告的引用关系，用 `data-ref-ids="编号1,编号2"` 或
`<a class="reference" data-ref-id="...">` 标明；没有这类信息且失败的公告是更正/撤销时，
按“无法核定”处理。

## 目录结构

```
app/                 FastAPI 采集服务
  app/models.py      Pydantic 模型（入参/出参/公告实体/失败详情/待核实链）
  app/parser.py      BeautifulSoup 解析：分页 rel=next、详情链接、详情字段
  app/crawler.py     同站分页 + 详情抓取（httpx 异步，限量/去重/失败详情收集）
  app/engine.py      公告链还原与有效性判定、详情失败归因（纯函数，无 IO）
  app/main.py        POST /query、GET /health
  Dockerfile
site/                本地示例公告站（数据驱动生成纯静态 HTML）
  build_site.py      生成 html/（4 个列表页 + 15 条详情 + uncertain 子站）
  serve_site.py     标准库服务器：对 _*.unavailable 标记的详情返回 503
  Dockerfile        托管静态站 + 503 模拟
tests/               35 个单元/集成测试（pytest）
demo/                本地与 Compose 演示脚本
docker-compose.yml   api(8000) + notice-site(8080)
```

## 运行方式

### 方式一：Docker Compose（推荐，完整离线演示）

```bash
python3 site/build_site.py          # 生成示例站静态文件
docker compose up -d --build

curl -s -X POST http://127.0.0.1:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"index_url":"http://notice-site:8080/index.html",
       "query_time":"2026-11-11T10:00:00+08:00"}'
```

容器内 API 通过 Compose 网络访问 `notice-site`；从宿主机调用时 `index_url`
也可换成 `http://127.0.0.1:8080/index.html`（两个地址同构，同站判定均成立）。
示例站还有 `http://notice-site:8080/uncertain/index.html`（对应宿主机
`http://127.0.0.1:8080/uncertain/index.html`）：其中撤销公告详情返回 503 且列表
未标明引用，查询结果为 `determination=indeterminate`。
交互式文档：http://127.0.0.1:8000/docs

### 方式二：本地 venv（无 Docker 环境）

```bash
python3 -m venv --without-pip .venv
.venv/bin/python get-pip.py        # 若 ensurepip 不可用，先 bootstrap pip
.venv/bin/pip install -r app/requirements.txt
./demo/run_local_demo.sh           # 自动建站、起两个服务并发起查询
```

### 测试

```bash
.venv/bin/pip install pytest pytest-asyncio
.venv/bin/python -m pytest -q      # 需先在 127.0.0.1:8080 启动示例站
```

## 示例站点场景（查询时刻 2026-11-11 10:00 +08:00）

| 公告 | 场景 | 判定结果 |
| --- | --- | --- |
| N-2026-001 | 12 路原始停运；014 撤销其详情返回 503，列表标明引用 001 | 链待核实，**不报为有效** |
| N-2026-002 | 18 路停运已结束 | `expired` |
| 003 → 006 → 010 | 25 路停运被两次更正（更正引用更正） | 有效，区间为 010 替换后的 11-10 ~ 11-20 |
| 004 → 007 | 33 路停运被撤销 | `cancelled`，整条链失效 |
| N-2026-005 | 12 路另一条独立停运；012 更正次日才发布 | 有效，链上只有 005 |
| N-2026-008 | 5 路停运尚未开始 | `not_yet_effective` |
| 009 → 011 | 40 路更正，且 011 误引用不存在的 N-2026-999 | 有效，同时报告悬空引用 |
| N-2026-013 | 50 路整条公告查询时刻之后才发布 | `not_yet_effective` |
| N-2026-014 | 详情页 HTTP 503（列表给出编号/类型/引用，详情不可用） | `failed_details` 中保留编号、类型、地址、原因 |
| N-2026-015 | 详情缺发布时间（解析失败），列表已知是 70 路独立停运 | 待核实，不影响其他链 |
| N-EXT-001 | 列表中的跨站链接 | 跳过，出现在 `skipped_offsite_links` |

主站查询结果：`determination=partial`，有效链为 005、003→006→010、009→011；
001 链与 015 自身列入 `pending_verification_chains`。

`uncertain/` 子站（查询时刻同上）：

| 公告 | 场景 | 判定结果 |
| --- | --- | --- |
| N-2026-021 | 80 路停运，本身查询时刻有效 | 因整次查询无法核定而不给出结论 |
| N-2026-020 | 撤销公告详情 503，列表未标明引用 | 整次 `determination=indeterminate`，`active_suspensions=[]` |

## 接入真实站点

真实页面结构不同时，只需替换 `app/parser.py` 中的 CSS 选择器与字段键映射
（`FIELD_KEYS` / `KIND_MAP`），引擎、爬虫与 API 契约无需改动。解析器约定详情页
使用 `<time datetime>` 携带带时区的 ISO 8601 时间、引用使用
`<a class="reference" data-ref-id="编号">`，避免依赖文案措辞。列表页若已知引用关系，
用 `data-ref-ids="编号1,编号2"` 或同样的 `a.reference` 元素标明——这直接决定
详情失败时能否把影响定位到具体链。
