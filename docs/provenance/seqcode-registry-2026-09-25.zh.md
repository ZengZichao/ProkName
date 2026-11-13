# SeqCode Registry —— 端点与许可记录（2026-09-25）

[English](seqcode-registry-2026-09-25.md) | [中文](seqcode-registry-2026-09-25.zh.md)

> **以英文原文 `seqcode-registry-2026-09-25.md` 为权威文本。** 本文件是其中文对照版；
> 两者不一致时以英文原文为准。所有 URL、HTTP 响应头、被引原文与计数一律保留原始形态。

本记录解决两件事——`src/prokname/dedup/seqcode.py` 与 `DATA_LICENSE` 曾把它们记为未
决问题：

1. M0 翻转清单第 1 项（"record the endpoint path/method/parameters from
   https://registry.seqco.de/page/api"）与第 3 项（"confirm the field names that
   answer 'registered / valid under SeqCode'"）；
2. `DATA_LICENSE` 关于 SeqCode 衍生数据再分发条款的那个未解决项——也就是该文件现在
   题为 `SeqCode Registry data — resolved 2026-09-25` 的那一节，它当时记录的是：本仓库
   从未读过、也未引用过注册中心自己公布的条款。

以下全部内容读取自 **2026-09-25（UTC）** 的线上服务，属本仓库自身优化轮的一部分。
没有编造任何端点；`prokname` 禁止猜测 URL 的策略依然有效，而本文件存在的理由正是让
这条策略的证据留在仓库之内，而不是留在某个 `/tmp` 目录或某个维护者的记忆里。

## 查考过的来源

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
| `names.json?status=…` 词表 | `public`、`automated`、`SeqCode`、`ICNP`、`ICNafp`、`valid` | ✅ 六个取值都返回数据；**未知取值返回 `count: 0` 而不报错**（静默为空——把取值打错时，读起来就像“没人注册过”） |
| `names.json?status=public` | — | `count: 48407`，`total_pages: 1614` |
| `names.json?status=valid` | — | `count: 39339` |
| `names.json?status=ICNP` | — | `count: 34951` |
| `names.json?status=SeqCode` | — | `count: 3350`，`total_pages: 112` |
| `names.json?status=automated` | — | `count: 9068` |
| `names.json?status=ICNafp` | — | `count: 1038` |
| 每页条数 | 未记载 | **固定 30 条；`per_page` / `limit` / `pp` / `page_size` 全部被忽略** |
| 按名查询 | 未记载 | **不存在。** `?name=`、`?q=`、`?search=` 都被静默忽略，返回未经筛选的 48 407 条全量名单 |
| `names/1.json` | 有记载，且以 `https://api.seqco.de/v1/names/1.json` 为示例 | ❌ **返回 `text/html`（该名的网页）而非 JSON**——文档中的 JSON 详情端点并未在文档中的路径上提供服务 |

## 对 ProkName 的后果（这就是本次发现）

**1. `check` 无法在联网模式下经此 API 回答“是否已依 SeqCode 注册？”**
不存在按名查询。要拿一个名字去比对 `status=SeqCode`，唯一的办法是走查全部 112 页；
要比对整个注册中心，则是 1 614 页。就单次查询而言，这既不礼貌、也不快，还与
`data/rate_limit_budget.json` 想要表达的那份请求预算不相容。因此**绝不能**依据本记录，就把该适配器
当作有权给出否定答复的一方：本记录所能支持的开关只有 `dedup/seqcode.py` 里的
`_POSITIVE_RULINGS_VERIFIED = True`，它覆盖的是实际观察到的占用认定；快照中不存在的名字
返回 `FOUND_UNKNOWN`，绝不返回 `not_found`。其中只有靠录制才能了结的两项——每个*实际
使用*的端点都要有一份已提交的线上 JSON 录制带，以及一次全绿的计划任务 live-smoke——
仍然成立，而在本源得以给出否定答复之前仍未了结的事项，以数据形式保存在同一模块的
`M0_FLIP_CHECKLIST` 中；该清单里“按名查询”那一项的答案是“能回答这个问题的端点并不存在”。

**2. `status=valid` 绝不可读作“已依 SeqCode 有效发表”。**
它返回 39 339 个名字，是 `status=SeqCode` 那 3 350 个的十二倍。这正是清单第 3 项所警告
的那个陷阱（"do NOT reuse LPSN's `full_name` / `lpsn_taxonomic_status` naming"）：直观
的筛选名在上游意味着别的东西。任何后续实现都必须以 `status=SeqCode` 为键，并且必须把
无法识别的 status 取值上的 `count: 0` 当作 `unavailable` 处理，绝不当作 `not_found`。

**3. 离线快照这条路是唯一可行的，而且代价很低。**
3 350 个 SeqCode 名 × 112 页（每页 30 条），是一次有界的抓取，其形状与本仓库
`scripts/rebuild_corpus.py` 已为 LPSN 衍生近似名语料所做的事相同。一份带版本号的
`seqcode_registered.json` 快照可以让 `check` 依据本地数据作出裁定，而不必为一个问题
花掉 112 次请求，这也正是 M0 想要的“可专家签字、可审计”的姿态：一份有日期、有署名的
快照，胜过一次答案每次运行都在变的在线调用。

这是一次设计变更（新增随包数据资产、新增刷新节奏、并了结一项许可义务——见下），
而它此后确已落地：`scripts/build_seqcode_snapshot.py` 构建
`src/prokname/data/seqcode_registered.json`，`dedup/seqcode.py` 据此作出裁定。它换来的
只有肯定答复——在 `status=SeqCode` 之下查到的名字即为占用证据，于是一个以基因组为模式、
由 SeqCode 发表而 LPSN 从未听说过的名字，如今给出 `CONFLICT` 而不再是 `BLOCKED`。那三个
干净裁定（`NO_CLEAR_CONFLICT`、`VERIFY_WARNING`、`PARAHOMONYM_WARNING`）经由此适配器仍然
不可达，而说明原因的正是产出这份快照的同一次抓取：缺席无法被认定，因此未命中只答复
`FOUND_UNKNOWN`，裁定保持阻塞。要解锁它们，还得另一个权威源同样能给出否定答复，该事项以
数据形式保存在 `dedup/seqcode.py` 的 `M0_FLIP_CHECKLIST` 中。

**4. 与本仓库已携带的计数交叉核对。**
`seqcode.py` 的文档字符串报告：全表 48 407 个名字，`status=SeqCode` 抓取为 112 页 /
3 350 行，二者均为 2026-09-25 的实测值——与本次线上实测的数值一致，这就确定了代码以
`status=SeqCode` 为键，而不是 `status=valid`（39 339，为 SeqCode 计数的十二倍）。

## 许可条款（了结 `DATA_LICENSE` 在 `SeqCode Registry data — resolved 2026-09-25` 一节中记录的那个问题）

逐字摘自 `https://registry.seqco.de/page/api` 页脚，检索于 2026-09-25：

> © 2022-2026 The SeqCode Initiative
> All information contributed to the SeqCode Registry is released under the
> terms of the Creative Commons Attribution (CC BY) 4.0 license

因此 `storage/store.py` 输出的 “SeqCode data: CC-BY 4.0” 署名字符串（`corpus_seed.json` 的
`_meta.provenance` 亦按该条款登记其许可）**在许可上是正确的**——它现在是引自上游，而不是早期草稿的遗留。CC BY 4.0 只要求署名
（不含相同方式共享），这比约束 LPSN 衍生资产的 CC BY-SA 4.0 的 copyleft 成分*更少*；
对再分发意味着什么，见 `DATA_LICENSE`。

**速率限制：未公布。** API 文档页不含任何速率、配额或限流表述（已就
rate / limit / throttle / quota 检索过），也未观察到任何限流响应头。因此
`data/rate_limit_budget.json` 无法为 SeqCode 引用一个上游数字；在这个数字存在之前，
任何抓取都必须自行限流并如实说明。

## 如何重新推导出本记录

```bash
.venv/bin/python scripts/probe_seqcode_api.py          # 重跑上文的每一项探测
.venv/bin/python scripts/probe_seqcode_api.py --json   # 机器可读输出
```

该脚本每项探测打印一行并带上实测计数，于是漂移（某个端点出现了、某个参数开始生效了、
详情端点开始返回 JSON 了）在几秒内可见，本文件也可以据此重新标注日期。
