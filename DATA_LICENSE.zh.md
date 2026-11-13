# 数据许可（DATA LICENSE 中文版）

[English](DATA_LICENSE) | [中文](DATA_LICENSE.zh.md)

> **以英文原文 `DATA_LICENSE` 为权威许可记录。** 本文件（`DATA_LICENSE.zh.md`）是
> 其中文对照版，仅供阅读便利：两者不一致时以英文原文为准，且文中引用的上游许可声明
> 在两份文本中一律保留原始语言，不做转译。本条只作文本优先性说明，不改变任何许可
> 条款，也不增加任何义务。

prokname 的*代码*依 MIT 许可分发（见 `LICENSE`）。

打包在 `src/prokname/data/` 内的*数据资产*沿用其来源的许可，并按相应条款分发：

## `genus_gender.json`、`corpus` 中 LPSN 衍生部分

衍生自 LPSN（List of Prokaryotic names with Standing in Nomenclature，
https://lpsn.dsmz.de/）。LPSN 数据（含数据下载与 API 数据）以 **CC BY-SA 4.0**
分发，因此衍生作品必须：

1. 引用 LPSN 站点指定的 LPSN 参考文献，并说明数据的访问日期。
2. 以电子方式再分发时回链至 LPSN 源页面。
3. 继承 CC BY-SA 4.0 许可（相同方式共享）。

由 prokname 作者人工整理、并非源自 LPSN 文本的条目（纯事实：属名 → 语法性别）以
CC0 1.0 发布；而整个文件依 CC BY-SA 4.0 分发，以符合 LPSN 衍生部分的相同方式共享
义务。

## `rules.json`、`gender_endings.json`、`person_genitive.json`、`stems.json`

由《国际原核生物命名法规》（ICNP）正字法附录与通用拉丁语法提炼而来的规则。命名法规
与规则本身不受著作权保护；此处的 JSON 表达以 **CC0 1.0**（由作者贡献至公有领域）发布，
除非某个格子被明确标注为抄自受著作权保护的来源——那种情况下适用该来源的条款。

## 状态说明

`person_genitive.json` 建模的是人名源属格种加词背后的拉丁化*变格范式*（ICNP Rule 20
正字法），而不是“人名性别 × 词干字母”的网格。只有锚定到已发表 LPSN 种加词的范式，
以及在此基础上构成的已证实姓氏，才标注 `verified: true`；`-iae`、`-is` 与默认
`-ae` 这几个范式以 `verified: false` 的提案形式发布，待依 ICNP 正字法附录签字确认；
正字法变体容忍表同样是 M0 交付项。

商业使用者还应留意 DSMZ 就 LPSN 衍生数字序列信息的使用所提示的潜在 CBD 惠益分享
义务（Cali Fund）。

## SeqCode Registry 数据 —— 2026-09-25 已解决

上游条款已被实际读取，此处原文引用而不作转述。摘自
https://registry.seqco.de/page/api 页脚，检索于 2026-09-25：

> © 2022-2026 The SeqCode Initiative
> All information contributed to the SeqCode Registry is released under the
> terms of the Creative Commons Attribution (CC BY) 4.0 license

因此 “SeqCode data: CC-BY 4.0” 署名是**正确的**，且如今有出处支撑，而不再是早期草稿的
遗留。该字符串由 `storage/store.py` 写出，写出的是 `src/prokname/__init__.py` 中定义的
唯一常量 `SEQCODE_ATTRIBUTION`；许可条款同样声明于
`src/prokname/data/seqcode_registered.json` 的 `_meta.licence` / `_meta.attribution`
字段，以及 `corpus_seed.json` 的 `_meta.provenance` 之中。`tests/fixtures/manifest.json`
不携带任何许可字符串：它是一份测试证据清单。

这对本仓库的具体含义：

- `src/prokname/data/seqcode_registered.json`——一份带日期的快照，内容是注册中心报告
  为依 SeqCode 有效发表的名单，由 `scripts/build_seqcode_snapshot.py` 构建——依 CC BY 4.0
  再分发。义务是署名：保留 `_meta.licence` / `_meta.attribution` 字段，并在再发布时引用
  SeqCode Initiative 以及逐条名的 `https://seqco.de/i:<id>` URI。
- CC BY 4.0 **不含相同方式共享**义务，这与约束上文 LPSN 衍生资产的 CC BY-SA 4.0 不同。
  当一个文件同时混有两类内容时，以更严格的一方（BY-SA）约束整个文件——见下方第 2 项。
- 这只解决著作权与许可问题。它**并不**判定：提取完整名单是否触发欧洲的
  sui generis 数据库权，注册中心并未就此发表过任何声明。快照自身的 `_meta` 记录了检索
  日期与源端点，至少让提取的范围保持透明。若某个发布产物将该快照单独再分发，而不是
  随包一起交付，请先向 SeqCode Initiative 确认。

**仍未解决，如实记录而不假装已经处理：**

1. **CC BY-SA 4.0 的相同方式共享义务对我们自有衍生资产意味着什么。**
   相同方式共享随*衍生作品*走，而不随“某个数值是人工重新录入的”这个事实走。具体说：
   凡是再现或基于 LPSN 文本的资产（例如 `genus_gender.json`，其性别数值抄自 LPSN）必须以
   CC BY-SA 4.0 再发布——这就是该文件即便含由作者整理、本可归入 CC0 的格子，整体仍以
   BY-SA 分发的原因。一份*措辞与取舍*独立于 LPSN 文本的规则表（命名规则本身不受著作权
   保护）可以保持 CC0 1.0——但一旦这类文件的内容抄自 LPSN 衍生来源、或是大量参照
   LPSN 衍生来源编制而成，BY-SA 义务就附着于整个文件，文件级许可必须随之改变。因此把
   两类内容混在同一资产里会被迫采用更严格的许可，而上述逐资产的衍生关系并未经过逐格
   审计。
2. **对再发布衍生规则资产的后果。** 任何资产在被镜像进公共数据集、发布归档或任何再
   分发的补充材料之前，必须先记录其衍生来源（源、访问日期、哪些格子由作者整理）。
   这项审计尚未执行，属 M0 事项。
3. **有两个随包资产未被上述各节列举。** `type_genus_stems.json`（型属变格词干，外加
   一份已证实的超属级名列表）与 `lpsn_status.json`（LPSN 的 `lpsn_taxonomic_status`
   标签映射表）都以 LPSN 发表过的词表为键，因此 CC BY-SA 4.0 一节很可能是其约束条款
   ——但这未经逐条审计，且两个文件都不带许可字段。在审计完成之前，请把两者都按 LPSN
   条款对待，不要将其改为 CC0 许可。

解决第 1～3 项（以及上文的数据库权问题）是这些资产任何公开再分发（包括数据可得性
声明）的前置条件。
