# prokname

[English](README.md) | [中文](README.zh.md)

原核生物命名辅助工具。prokname 以确定性规则和可审计的决策支持为核心，帮助研究者为细菌与古菌的新分类单元拟定学名，并核对 **ICNP**（国际原核生物命名法规）与 **SeqCode**（原核生物序列命名规范）的合规要求。

> prokname 仅提供命名辅助决策。名称的有效性完全由 ICNP 或 SeqCode 下的正式发表程序决定，本工具的输出不构成有效性裁定。

下表逐项说明本仓库实际交付了什么。规则的出处不会被写成某份未随包文档的小节：外部事实可在 `docs/provenance/` 核对，许可条款见 `DATA_LICENSE`，规则本身见随包数据资产。

| 方案模块 | 本仓库状态 |
|---|---|
| M0 规则资产（`rules.json`、`person_genitive.json`、`gender_endings.json`、`genus_gender.json`、`stems.json`） | 已交付，**专家签字待定**，见 `prokname data` |
| M1 构词引擎：三分语法类别、双模式性别判定、性数格校验、生成、正字法 | 已实现 |
| 双法典路由（可行路径与利弊对比、ICNP 先占优先、GTDB 边界交代） | 已实现 |
| 两级查重：本地近似名（parahomonym）扫描 | 已实现（演示级种子语料） |
| 查重：权威源适配器（LPSN、SeqCode Registry） | **M0 前置门控脚手架**，诚实返回 `unavailable`（绝不伪造）；实时端点与凭据待 M0 记录 |
| 三源全量近似扫描语料、论文级 A/B/C/D 基准集 | 种子级基准已实现（A 74 / B1 26 / B2 17 / C 5×3 / D 11 例，留出受控）；LPSN 衍生论文级扩充为 M1/M2 交付物 |
| 项目存储（创建/添加/展示/打分/导出/删除） | 已实现（`prokname project`） |

## 安装

```bash
cd prokname
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# 可选：启用 `prokname check --online`（官方 lpsn 客户端、httpx、keyring）
pip install -e ".[online]"
```

完整文档见 **[使用文档（USAGE.zh.md）](USAGE.zh.md)**，其中包含每条命令的示例、退出码、Python API、基准复现方法与故障排查。英文版本见 [USAGE.md](USAGE.md)。

## 使用

```bash
# 人名词源 → 种加词候选生成
prokname gen --stem Boyd --type person --rank species \
    --genus Shigella --person-gender male
# → Shigella boydii   （属格名词：与属名性别无关）

# 地名形容词随属名语法性别变格
prokname gen --stem Beijing --type place --rank species --genus Rhizobium
# → Rhizobium beijingense        （中性 → -ense）
prokname gen --stem Beijing --type place --rank species --genus Klebsiella
# → Klebsiella beijingensis      （阴性 → -ensis）

# 特征词源同时输出形容词形式和同位名词形式
prokname gen --stem Wukong --type feature --rank species --genus Bacillus

# 高阶元把法典强制后缀接在型属的"属格词干"上（--stem 传主格属名）
prokname gen --stem Bacillus    --type feature --rank family   # Bacillaceae
prokname gen --stem Clostridium --type feature --rank order    # Clostridiales
prokname gen --stem Pseudomonas --type feature --rank phylum   # Pseudomonadota

# 两级查重（默认离线：近似名扫描 + 诚实返回 BLOCKED）
prokname check "Wukomonas beijingensis"
# 词干级口径（Taxamatch/GNmatcher 思想）：先剥离屈折词尾再比较
prokname check "Wukomonas beijingense" --near-match-mode stem

# 双法典路由（ICNP 先占感知）
prokname route --source MAG --icnp-occupied no
prokname route --source pure_culture --icnp-occupied no   # 双路径 + 利弊
prokname route --source MAG --icnp-occupied yes           # 冲突引导

# 引擎回归种子（实名锚定）与数据资产状态
prokname bench
prokname data
```

更习惯图形界面？**ProkName Studio** 就是本引擎的桌面前端——中英双语、亮暗两套外观——它以独立项目的形式发布，依赖 `prokname`。像任何其它使用方那样安装它（见 [Studio 仓库](https://github.com/ZengZichao/ProkName-Studio)），然后用 `prokname-studio` 启动。它不新增任何命名逻辑：所有调用都走下面这套公开 API。

CLI 共注册 **八** 条命令：`gen`、`check`、`route`、`project`、`bench`、`holdout`、`data`、`config`。八条命令都支持 `--json` 机器可读输出。

退出码含义：

- `0`：正常结束，可能附带警告。
- `1`：错误。
- `2`：参数错误（由 CLI 框架返回）。
- `3`：阻断。权威源不可用，无法裁定；本地近似名命中以警告形式并列展示。
- `4`：冲突。权威源报告该名称已被占用。

**当前 `check` 能回答什么。** `4`（冲突）可达，并且自 2026-09-25 起更强：SeqCode 适配器改为依据一份带日期的占用快照裁定，凡在 SeqCode 下已注册并有效发表的名字，即便离线也会判为冲突，不再等 LPSN。其余情形返回 `3`（阻塞），这是实测出来的边界而非未测的占位符——SeqCode Registry 公开 API 没有“按名字查询”，且其 `status=SeqCode` 列表被证明会漏掉注册方自己标注为有效的名字，所以“不在列表里”不能读成“未被占用”（见 docs/provenance/seqcode-registry-2026-09-25.md）。带告警的 `0` 与“无明确冲突”的 `0` 需要两个权威源都能给出否定答复；LPSN 在带凭据且可达时可以，因此这三个裁定态在端到端仍不可达。请把 `3` 读作“工具没能问到”，绝不是“这个名字没问题”。

## 设计决策

- **人名种加词是属格名词**，不随属名变格。词尾取决于姓氏拉丁化后落入哪个*变格范式*，例如 *Shigella boydii*、*Bartonella henselae*、*Borrelia burgdorferi*。它既不受纪念者性别影响，也不随属名的语法性别变格。只有真形容词才随属名性别变格。
- 上述人名属格规则是引擎的核心规则，已由回归测试锚定。`person_genitive.json` 按范式建模，并收录一份已证实姓氏词库：只有“已证实且 `verified: true`”的条目才断言合规，默认规则给出的提案一律标记 `needs_review`（见[已知局限](#已知局限)）。
- **高阶元名称以型属的属格词干为基底**（*Bacillus*，属格 *Bacilli* → `Bacillaceae` / `Bacillales`；*Clostridium* → `Clostridiales`；*Pseudomonas* → `Pseudomonadota`），绝不把后缀直接拼在主格上。引擎原样保留保留名（如 class `Bacilli`），不会“纠正”它们；构成规则正确但未见于发表的名称返回 `compliant=None`，并附警告。
- **`person_genitive.json` 绝不凭记忆填格**：未核验的范式随包发布时标注 `verified: false`，其输出只是带标记的提案；范式无法推断的姓氏抛出 `GenitiveCellUnavailable`，而不是臆测结果。补齐这些格子属于 M0 签字项，须对照 ICNP 正字法附录逐一核验。
- **性别判定双模式**：一是词库查找（高置信，`genus_gender.json` 收录 58 个策展属），二是词尾启发式推断（**始终**标记 `needs_review`）。引擎绝不默认阳性。
- **权威源不可用时阻断裁定**（`blocked`，退出码 3）：“无法查证”绝不伪装成“未发现”。
- **常规 CI 不联网**：在线适配器改由离线桩件客户端钉住契约。`tests/fixtures/` 现已含有一份真正来自运行中服务的录制——`gna_verifications_live.json`，由 `scripts/record_live_gna.py` 录制（GNA 无需凭据），并由周期 live-smoke 作业复检上游漂移。LPSN（需凭据）与 SeqCode 的 REST 契约仍未录制，因此权威层未经实时验证；`manifest.json` 如实区分二者。
- **数据许可以 `DATA_LICENSE` 为准**：代码 MIT（`LICENSE`）；LPSN 衍生数据 CC BY-SA 4.0（署名并相同方式共享）；自研规则文件 CC0 1.0；SeqCode Registry 数据 CC BY 4.0——该条款于 2026-09-25 摘自注册中心自己的 API 页面，因此部分导出写出的“SeqCode data: CC-BY 4.0”现在有了出处，而不再是沿用早期草稿的署名散文。
- `DATA_LICENSE` 仍留有两个许可问题，并已如实列出：混合来源规则资产的逐格来源审计，以及再分发完整 SeqCode 名单是否触及欧盟数据库特殊权利。

## 开发

```bash
pytest                           # 当前环境能收集到的全部测试
pytest --collect-only -q         # 权威的按模块测试计数
ruff check src scripts tests     # lint 门禁（CI 强制）
prokname bench                   # 内置实名锚定回归种子
prokname bench --full            # 种子基准 A/B1/B2/C/D（门禁式：未达标退出码 1）
prokname holdout                 # CI 门禁：A-"推断"子集 ∩ 词库 = ∅
```

下面这个数字是生成的，不是手写的：`python scripts/fill_test_counts.py` 写入 `pytest --collect-only` 实际收集到的数量，CI 会在它过期时报错（`pytest --collect-only -q` 给出构成它的按模块计数）。

- 本仓库自己的套件收集 967 项，无需安装任何扩展组。ProkName Studio 的测试随 Studio 一同计数；这里没有任何东西依赖 Qt，因为这里没有任何东西导入它。
- 3 项 `taxonkit` 交叉验证测试在所有环境都会进入收集清单，但 `PATH` 缺少该二进制时**跳过**。`.github/workflows/ci.yml` 中没有 `taxonkit` 安装步骤，因此该交叉验证门控在本项目 CI 中从不运行。

## 已知局限

以下是本仓库当前能真实证明的边界，详见 [USAGE.zh.md §10](USAGE.zh.md#10-已知局限) 与 [USAGE.md §10](USAGE.md#10-known-limitations)：

- 基准集为**种子规模**（A 74 = 15 查找 + 59 推断；B1 26，仅 **4 例负例**，`feature` 与 `thing` 两支各 0 例；B2 17；C 5 词干 × 3 次重复；D 11）。n = 3～26 上的全对结果不足以支撑“漏报率 ≤ 1%”或“100%”这类目标。自助法置信区间给出的是准确度区间，在全对样本上会坍缩为一点。
- 近似名语料是一份 **16 条、带日期的演示语料**（`corpus_seed.json`，2026-08-16；LPSN ×12、synthetic-demo ×2、SeqCode Registry ×1、NCBI ×1），尚未测量召回率与假阳率。
- **与 `gan` 的 C 集对比从未运行**：`bench --full` 以 `skip_gan=True` 评估该套件，CI 不安装 gan。
- 规则资产仍待 **M0 专家签字**。`rules.json` 已为每个超属级后缀标注强度（ICNP 强制、SeqCode 建议、亚阶元为采纳植物学惯例），但这些归属本身及其条款引证仍标注为 `verified: false`，需要专家裁断。

## 引用

若 prokname 对您的研究有帮助，请引用本软件（`CITATION.cff`）：

- **Zichao Zeng**（ORCID [0000-0001-6553-970X](https://orcid.org/0000-0001-6553-970X)）

代码采用 MIT 许可（见 `LICENSE`）。数据许可见 `DATA_LICENSE`：LPSN 衍生部分为 CC BY-SA 4.0，自研规则文件为 CC0 1.0，SeqCode Registry 数据为 CC BY 4.0（2026-09-25 摘自上游页面）。

## M0 检查清单

以下 5 项是 M1 依赖规则资产的前置门控。

1. `rules.json` / `person_genitive.json` 专家签字（从 ICNP 正字法附录填充元音词干格子；把人名属格表按拉丁化变格范式重构，并逐格对照真实 LPSN 名称复核）。
2. 方案中全部示例名的 LPSN 实名校验脚本。
3. LPSN 端点契约的实时记录（`dedup/lpsn.py` 随后从 `unavailable` 毕业；CI 回放所用的录制带随该步一并交付）。**SeqCode 已于 2026-09-25 完成记录**：契约见 docs/provenance/seqcode-registry-2026-09-25.md，适配器改为依据带日期的占用快照裁定，且只给肯定答复。SeqCode 还缺的不是一次录制，而是注册中心目前没有的能力：完整且稳定的名单，或按名字查询（见第 6 项）。
4. 引文 DOI 核验（Freese 2026、Ratatoskr、Trüper & de'Clari 系列）。
5. **已于 2026-09-25 完成**：`DATA_LICENSE` 已引用注册中心自己页面的 SeqCode 条款（CC BY 4.0）。该文件中剩下的许可工作是逐格来源审计与数据库权利问题。
6. 由同一次探测新增：在 SeqCode 能提供完整且稳定的名单（或按名字查询端点）之前，`dedup/seqcode.py` 一律不得回答“未注册”。在此之前，未命中只返回 `found_unknown`，其阻断裁定的效果与权威源不可用完全相同——这是有意的，因为不完整的名单不等于不存在的证据。
