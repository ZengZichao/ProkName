# SeqCode Registry —— 端点与许可记录（2026-09-25）

[English](seqcode-registry-2026-09-25.md) | [中文](seqcode-registry-2026-09-25.zh.md)

> **以英文原文 `seqcode-registry-2026-09-25.md` 为权威文本。** 本文件是其中文对照版；
> 两者不一致时以英文原文为准。所有 URL、HTTP 响应头、被引原文与计数一律保留原始形态。

本记录解决两件事 —— `src/prokname/dedup/seqcode.py` 与 `DATA_LICENSE` 曾把它们记为未
决问题：

1. M0 翻转清单第 1 项（"record the endpoint path/method/parameters from
   https://registry.seqco.de/page/api"）与第 3 项（"confirm the field names that
   answer 'registered / valid under SeqCode'"）；
2. `DATA_LICENSE` 当时记下的"未解决 —— SeqCode 衍生数据的再分发条款"，其内容是：本
   仓库从未读过、也未引用过注册中心自己公布的条款。

以下全部内容读取自 **2026-09-25（UTC）** 的线上服务，属本仓库自身优化轮的一部分。
没有编造任何端点；`prokname` 禁止猜测 URL 的策略依然有效，而本文件存在的理由正是让
这条策略的证据留在仓库之内，而不是留在某个 `/tmp` 目录或某个维护者的记忆里。

## 查问过来源

| 内容 | URL | 备注 |
| --- | --- | --- |
| REST API 文档页 | `https://registry.seqco.de/page/api` | HTTP 200，`<title>API Documentation \| SeqCode Registry</title>`；该页是服务端渲染的 Rails HTML，无机器可读 schema |
| API 根 | `https://api.seqco.de/v1/` | HTTP 200，`text/html`（一个索引页，不是 JSON） |
| 健康探测 | `https://api.seqco.de/v1/page/status.json` | HTTP 200，`application/json`，正文逐字为 `{"status":"ok"}` |
| 名单 | `https://api.seqco.de/v1/names.json` | HTTP 200，`application/json` |

`api.seqco.de` 的响应头：`server: nginx/1.24.0 + Phusion Passenger(R) 6.1.1`、
`x-content-type-options: nosniff`、`content-type: application/json; charset=utf-8`。
**未返回任何 `Retry-After`、`X-RateLimit-*` 或 `Link: rel="rate limit"` 头。**

## 文档记载的路由（逐字摘自 API 页）

全部为 `GET`，全部挂在 `https://api.seqco.de/v1/` 之下：

```
/page/status.json     names.json        names/(id).json     genomes.json
genomes/(id).json     type-genomes.json registers.json      registers/(acc).json
authors.json          authors/(id).json journals.json       journals/(name).json
publications.json     publications/(id).json  subjects.json  subjects/(id).json
strains/(id).json
```

模型（只保留裁定名称占用所需的部分）：

```
name_item       {id:int, name:str, url:str, uri:str}
response        {status:"ok", message_type:str}
response_paginated
                {status, message_type, count:int, current_page:int,
                 total_pages:int, next:str}
names/(id).json 另外记载 rank, status_name, priority_date,
                etymology, nomenclatural_type, classification, children,
                register, proposed_in, not_validly_proposed_in, corrigendum_in,
                emended_in, qc_warnings, created_at, updated_at
```

## 线上服务的实际行为

| 探测 | 文档记载 | 2026-09-25 实测 |
| --- | --- | --- |
| `page/status.json` | `{"status": "ok"}` | ✅ 与文档一致 |
| `names.json` 参数 | 仅 `page`、`status` | ✅ 两者都生效；**没有其他参数被记载，也没有其他参数可用** |
| `names.json?status=…` 词表 | `public`、`automated`、`SeqCode`、`ICNP`、`ICNafp`、`valid` | ✅ 六个取值都返回数据；**未知取值返回 `count: 0` 而不报错**（静默为空 —— 拼错一个字母读起来就像"没人注册过"） |
| `names.json?status=public` | — | `count: 48407`，`total_pages: 1614` |
| `names.json?status=valid` | — | `count: 39339` |
| `names.json?status=ICNP` | — | `count: 34951` |
| `names.json?status=SeqCode` | — | `count: 3350`，`total_pages: 112` |
| `names.json?status=automated` | — | `count: 9068` |
| `names.json?status=ICNafp` | — | `count: 1038` |
| 每页条数 | 未记载 | **固定 30 条；`per_page` / `limit` / `pp` / `page_size` 全部被忽略** |
| 按名查询 | 未记载 | **不存在。** `?name=`、`?q=`、`?search=` 都被静默忽略，返回未经筛选的 48 407 条全量名单 |
| `names/1.json` | 有记载，且以 `https://api.seqco.de/v1/names/1.json` 为示例 | ❌ **返回 `text/html`（该名的网页）而非 JSON** —— 文档中的 JSON 详情端点并未在文档中的路径上提供服务 |

## 对 ProkName 的后果（这就是本次发现）

**1. `check` 无法在联网模式下经此 API 回答"是否已依 SeqCode 注册？"**
不存在按名查询。要拿一个名字去比对 `status=SeqCode`，唯一的办法是走查全部 112 页；
要比对整个注册中心，则是 1 614 页。就单次查询而言，这既不礼貌、也不快，还与
`data/rate_limit_budget.json` 想要表达的那份请求预算不相容。因此适配器**绝不能**凭本
记录就把 `_M0_ENDPOINT_VERIFIED = True` 翻上去：清单第 2 与第 5 项（每个*实际使用*的
端点都要有一份提交的线上 JSON cassette，以及一次全绿的计划 live-smoke）仍然成立，而
第 1 项的答案是"能回答这个问题的端点并不存在"。

**2. `status=valid` 绝不可读作"已依 SeqCode 有效发表"。**
它返回 39 339 个名字，是 `status=SeqCode` 那 3 350 个的十二倍。这正是清单第 3 项所警告
的那个陷阱（"do NOT reuse LPSN's `full_name` / `lpsn_taxonomic_status` naming"）：直观
的筛选名在上游意味着别的东西。任何后续实现都必须以 `status=SeqCode` 为键，并且必须把
无法识别的 status 取值上的 `count: 0` 当作 `unavailable` 处理，绝不当作 `not_found`。

**3. 离线快照这条路是唯一可行的，而且代价很低。**
3 350 个 SeqCode 名 × 每页 30 条 = 112 页，一次有界的抓取，与本仓库
`scripts/rebuild_corpus.py` 对 LPSN 衍生近似名语料所做的事形状相同。一份带版本号的
`seqcode_registered.json` 快照可以让 `check` 从本地数据给出当前无法达到的三个裁定
（`NO_CLEAR_CONFLICT`、`VERIFY_WARNING`、`PARAHOMONYM_WARNING`），这也正是 M0 想要的
"可专家签字、可审计"的姿态：一份有日期、有署名的快照，胜过一次答案每次都在变的答案的
在线调用。

这是一次设计变更（新增随包数据资产、新增刷新节奏、并了结一项许可义务 —— 见下），
所以此处按"建议路径"记录，而未单方面实施。

**4. 与本仓库已携带的计数交叉核对。**
`seqcode.py` 的文档字符串写着 "48,386 names / 3,338 validly published under SeqCode
as of 2026-08-16"。2026-09-25 线上实测：`status=public` 48 407，`status=SeqCode` 3 350。
两者都按预期方向增长了约 0.05 % 与 0.4 %，历时六周。这独立地佐证了文档字符串所指确实是
`status=SeqCode` 那个计数 —— 而不是 `status=valid`（39 339）。

## 许可条款（了结 DATA_LICENSE 未解决项之一）

逐字摘自 `https://registry.seqco.de/page/api` 页脚，检索于 2026-09-25：

> © 2022-2026 The SeqCode Initiative
> All information contributed to the SeqCode Registry is released under the
> terms of the Creative Commons Attribution (CC BY) 4.0 license

因此 `storage/store.py` 与 `corpus_seed.json` 输出的 "SeqCode data: CC-BY 4.0" 署名字符串
**在许可上是正确的** —— 它现在是引自上游，而不是早期草稿的遗留。CC BY 4.0 只要求署名
（不含相同方式共享），这比约束 LPSN 衍生资产的 CC BY-SA 4.0 的 copyleft 成分*更少*；
对再分发意味着什么，见 `DATA_LICENSE`。

**速率限制：未公布。** API 文档页不含任何速率、配额或限流表述（已就
rate / limit / throttle / quota 检索过），也未观察到任何限流响应头。因此
`data/rate_limit_budget.json` 无法为 SeqCode 引用一个上游数字；在这个数字存在之前，
任何抓取都必须自行限流并如实说明。

## 如何重新推出本记录

```bash
.venv/bin/python scripts/probe_seqcode_api.py          # 重跑上文的每一项探测
.venv/bin/python scripts/probe_seqcode_api.py --json   # 机器可读输出
```

该脚本每项探测打印一行并带上实测计数，于是漂移（某个端点出现了、某个参数开始生效了、
详情端点开始返回 JSON 了）在几秒内可见，本文件也可以据此重新标注日期。
