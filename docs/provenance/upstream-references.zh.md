# 上游引文登记表

[English](upstream-references.md) | [中文](upstream-references.zh.md)

> **以英文原文 `upstream-references.md` 为权威文本。** 本文件是其中文对照版；两者不一致
> 时以英文原文为准。表中的 URL、commit SHA、行号区间与被引原文一律保留原始语言。

prokname 代码所依赖的每一个外部事实，都登记在仓库内的这一个地方。

## 为什么需要这个文件

本项目过去的代码注释用**仓库之外的路径**来引用来源 ——
`ProkName-参考项目/gnverifier/fuzzy-matching.md:11-23`、
`/tmp/prokname_fix/nomenclature_facts.md §8`、`审阅报告-项目代码 §2 M5`。对于把本仓库
检出到一台同时装着这些目录的笔记本上的人来说，这是可行的。但对于从 wheel 安装
`prokname` 的人、在 CI runner 上阅读它的人、或八个月之后来审查它的人来说，这些指向
什么都解析不到，而 "`/tmp`" 解析到的是一个按设计早已不存在的目录。

对一个对外宣称的产品是*可审计*的命名学裁定的工具而言，这不是外观问题：一条关于某个
名称、却无法追溯到其来源的断言并不具备可审计性，它只是看起来很自信。

所以：这些事实搬到这里，附上读取时所依据的上游 **commit**，代码注释则指向本文件的
某个小节。新增引文的规则是：

> 只有当本文件登记了仓库、commit SHA、文件、行号区间、该段文字确立了什么、以及最近
> 一次复核重读的日期之后，代码注释才可以引用该上游事实。

分支名不是版本钉。写这份文件时就当场演示过：
`LeibnizDSMZ/lpsn-api@main/README.md` 返回 **404**，而代码原本已携带的那个 commit
`@c15229e7` 能取到被引用的那几行原文。按分支引用会悄然腐烂，按 SHA 引用不会。

逐条复核，并在被引段落已不存在时判失败：

```bash
.venv/bin/python scripts/check_upstream_citations.py    # 漂移则退出码 1
```

---

## L1 — LPSN 客户端的响应结构

| | |
| --- | --- |
| 确立内容 | `client.retrieve()` 每个名称产出一条记录；字段为 `full_name`（裸名，不含作者引证）与 `lpsn_taxonomic_status`（单一字符串标签）；记录携带 `id`。 |
| 来源 | `LeibnizDSMZ/lpsn-api` @ **`c15229e7`**，`README.md:81-96` |
| URL | `https://raw.githubusercontent.com/LeibnizDSMZ/lpsn-api/c15229e7/README.md` |
| 复核 | 2026-09-25，由 `scripts/check_upstream_citations.py` 核验 —— 其打印示例逐字为 `{782310: [{'full_name': 'Sulfolobus acidocaldarius'}, {'lpsn_taxonomic_status': 'correct name'}], ...}` |
| 消费方 | `dedup/lpsn.py`（同一性守卫 + 状态分类）、`tests/fixtures/lpsn_retrieve_entries.json` |
| 若出错的后继影响 | 字段一旦被改名，同一性守卫就不再匹配，于是已发表之名会降级为 `not_found`；此时始终返回 unavailable 的 SeqCode 适配器会把它变成 `BLOCKED`，而不是给出一个虚假的"无冲突"裁定。这正是那个计划任务 live-smoke 存在的目的。 |

**一条并非来自上游文档、因此数据资产里仍标注 `verified: false` 的推论**：真实的 LPSN
页面与 API 有时会把多条命名学事实用逗号拼进同一个字符串，例如
`"correct name (and explicitly recommended for medical use)"`。`dedup/lpsn.py` 里的
逗号/括号处理就是为这一形态而写的。它当初的依据是记在 `/tmp/prokname_fix/` 里的一条
笔记，而那份笔记已不可恢复；在把一个带拼接标签的真实 LPSN 响应录制为 cassette
（`tests/fixtures/manifest.json` 里的 `outstanding_live_captures`）之前，这段处理依据的
是观察而非可引证的来源，受影响的若干状态行就保持未核验。

## L2 — GNmatcher 的词干化口径

| | |
| --- | --- |
| 确立内容 | GNA 的模糊匹配比较的是*词干化规范形*（"where suffixes of specific epithets are removed"），并把编辑距离在词干串上收敛到 1。 |
| 来源 | `gnames/gnverifier`，`fuzzy-matching.md:11-39` |
| 复核 | 2026-09-25 —— 第 16 行原文为 `using "stemmed canonical forms" where suffixes of specific epithets are` |
| 消费方 | `dedup/nearmatch.py`（`stem` 扫描模式） |
| 备注 | 因为是文档而非代码、且所述行为稳定，此处按默认分支引用。一旦词干化口径要在任何地方作为可引证的断言使用（发布说明、数据可得性声明），就先钉到 SHA。 |

## L3 — SeqCode 的策展与先占建议

| | |
| --- | --- |
| 确立内容 | 策展人会确认父属"validly published under the SeqCode, ICNP, or ICNafp"；高阶元名由型属构成；该文档仓库载明策展流程与许可，但**不含 REST API 契约**。 |
| 来源 | `seq-code/documentation` @ **`10d08dec`**，`guide/curation.md:88` 与 `:175` |
| 复核 | 2026-09-25 —— 第 88 行原文为 `a validly published genus under the SeqCode, ICNP, or ICNafp, only page/s for` |
| 消费方 | `routing/router.py`（先占建议文本及其文档化的负向适用范围） |

## L4 — SeqCode Registry 的 REST 契约与许可

| | |
| --- | --- |
| 确立内容 | Base `https://api.seqco.de/v1/`；17 条 GET 路由；`names.json` 只接受 `page` 与 `status`（`public` / `automated` / `SeqCode` / `ICNP` / `ICNafp` / `valid`），每页 30 行，未知参数被静默忽略；`names/{id}.json` 对某些 id 返回 HTML；无公布的速率限制；全部贡献数据为 CC BY 4.0。 |
| 来源 | 线上服务 + `https://registry.seqco.de/page/api` |
| 复核 | 2026-09-25，直接探测。完整记录见 [`seqcode-registry-2026-09-25.md`](seqcode-registry-2026-09-25.md)（中文对照见 [`seqcode-registry-2026-09-25.zh.md`](seqcode-registry-2026-09-25.zh.md)） |
| 消费方 | `dedup/seqcode.py`（占用快照语义）、`DATA_LICENSE`、`data/rate_limit_budget.json` |

## L5 — NCBI taxdump 的 `name_class` 词表

| | |
| --- | --- |
| 确立内容 | 存在哪些 `name_class` 取值、各自含义 —— 特别是 `authority` 是作者引证而非同物异名，以及 `misspelling` 才是近似名（parahomonym）信号。 |
| 来源 | `https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump_readme.txt` —— **未随包收录**，且随包的 taxonkit 快照在解析 `names.dmp` 时并未记录这份词表。 |
| 复核 | 未对照真实下载核验。`dedup/ncbi.py` 与 `data/lpsn_status.json` 邻近结构里得到的那张语义表，每一行都正因如此标注 `verified: false`。 |
| 消费方 | `dedup/ncbi.py`、`tests/fixtures/ncbi_names_tiny.dmp` |
| 怎样闭合 | 下载该 README，把取值清单连同检索日期原文引用到此处，然后把表里的 `verified` 标志翻过来。 |

## L6 — 不随本源发布的设计文档

本项目在规划时依据一份工程方案与一份基准设计草案，另有一次架构评审记录了带编号的
若干发现。这些文档都不随本仓库发布，因此在这里都不能当作证据被引用。

由此得出两条规则，而且都是被强制执行、不是口头声明的：

1. 代码注释在注释内部说明代码保证了什么。一个发现编号不是读者能够核验的推理过程，
   因此注释承载的是结论实质，而不是它来自哪份报告的引用。
2. 原本会写成 `plan §x.x` 的出处，要么删掉，要么换成读者*真的*可以核对的东西：外部
   事实引本文件里钉了 commit 的条目，许可条款引 `DATA_LICENSE`，规则本身引那份数据
   资产。`README` 里的模块对应表也是这么写的——它说明本仓库交付了什么，而不是某份
   方案承诺过什么。

## L7 — 随包的参考快照

`ProkName-参考项目/` 是若干上游项目（lpsn-api、gnverifier、seqcode-documentation、
taxonkit）的只读本地镜像，2026-09-07 下载，其 commit 钉与许可记录在它自己的 README
里。`tests/fixtures/manifest.json` 把它记名为 `vendored_snapshot_root`。

它**不是**本仓库的一部分，因此它不能是某条引文唯一能解析到的地方。它的作用是让阅读
更便利（离线查阅、grep）；某个来源究竟说了什么的权威依据，是上文那些钉了 commit 的
条目，可由 `scripts/check_upstream_citations.py` 重新核验。
