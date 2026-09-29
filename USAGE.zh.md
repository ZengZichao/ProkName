# prokname 使用文档

[English](USAGE.md) | [中文](USAGE.zh.md)

本指南详细介绍 prokname 的每一条命令：安装、日常使用、机器可读输出、配置、Python API、基准测试与故障排查。快速概览请见 [README](README.zh.md)。

> **免责声明。** prokname 仅提供命名辅助决策。名称的有效性完全由 ICNP 或 SeqCode 下的正式发表程序决定——本工具的输出不构成有效性裁定。

---

## 目录

1. [安装](#1-安装)
2. [核心概念](#2-核心概念)
3. [命令参考](#3-命令参考)
   - [`prokname gen`](#prokname-gen)
   - [`prokname check`](#prokname-check)
   - [`prokname route`](#prokname-route)
   - [`prokname project`](#prokname-project)
   - [`prokname bench` / `prokname holdout`](#prokname-bench--prokname-holdout)
   - [`prokname data`](#prokname-data)
   - [`prokname config`](#prokname-config)
   - [ProkName Studio](#prokname-studio)
4. [退出码与输出模式](#4-退出码与输出模式)
5. [Python API](#5-python-api)
6. [基准测试](#6-基准测试)
7. [数据资产与许可](#7-数据资产与许可)
8. [环境变量与配置文件](#8-环境变量与配置文件)
9. [故障排查与 FAQ](#9-故障排查与-faq)
10. [已知局限](#10-已知局限)

---

## 1. 安装

环境要求：Python ≥ 3.11（测试覆盖 3.11～3.14），pip。

```bash
cd prokname
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # 运行时依赖 + 测试工具链
pip install -e ".[online]"       # 可选：`prokname check --online` 的客户端
```

验证安装：

```bash
prokname --version               # → prokname 0.1.0
prokname bench                   # 必须退出码 0（自行打印 "passed n/n" 行）
pytest                           # 当前环境能收集到的全部测试
pytest --collect-only            # 总数：其末尾的 "N tests collected" 行
pytest --collect-only -q         # 权威的按模块测试计数
```

**下面这个数字是生成的，不是手写的。** `python scripts/fill_test_counts.py` 写入 `pytest --collect-only` 实际收集到的数量，CI 的 `--verify` 步骤会在它失配时报错。套件覆盖范围：

- 安装 `.[dev]` 后收集 967 项，而且不再分档：本包没有任何模块导入 Qt，因此也没有任何测试被图形扩展组门控。ProkName Studio 的测试随 Studio 一同计数；
- `tests/test_property.py` 依赖 `hypothesis`；`tests/test_taxonkit_crosscheck.py`（3 项）在所有环境都会被收集，但若 `PATH` 上没有 `taxonkit` 二进制就会被**跳过**——本仓库随附的 CI 镜像并不安装 taxonkit，因此该交叉验证在本项目 CI 中从不运行。

以 `pytest --collect-only`（总数）与 `pytest --collect-only -q`（按模块计数）这两条命令为准：本文引用的任何总数都只是快照，而套件仍在增长。

> 若移动项目目录后出现 `prokname: bad interpreter`，说明 editable 安装仍指向旧路径。在虚拟环境中重新执行 `pip install -e ".[dev]"` 即可修复入口脚本（见[故障排查](#9-故障排查与-faq)）。

Docker：

```bash
docker build -t prokname .
docker run --rm prokname gen --stem Boyd --type person --rank species \
    --genus Shigella --person-gender male
```

---

## 2. 核心概念

prokname 将**词源描述**转化为合规的候选学名，检查命名冲突，并辅助选择正确的**命名法典**。四种词源类型驱动引擎；每种类型映射到一个或两个*语法类别*（映射关系存于 `src/prokname/data/rules.json`，可由专家审阅）：

| 词源类型 | `--type` | 语法类别 | 是否随属名性别变格 | 示例 |
|---|---|---|---|---|
| 地名 | `place` | 形容词（地名构词法） | **是** | *Klebsiella beijingensis* / *Rhizobium beijingense* |
| 人名 | `person` | 属格名词 | **否**（被纪念者性别 × 词干结尾） | *Shigella boydii*（而非 \**boydiae*） |
| 事物名 | `thing` | 属格名词 | **否**（源名词自身变格） | *Vibrio cholerae* |
| 特征 | `feature` | 形容词**或**同位名词（两者都输出） | 形容词：是；同位名词：否 | *Thermus thermophilus* |

**性别判定为双模式**，绝不静默猜测：

- **查找（lookup）**——属名在 `genus_gender.json` 词库中；高置信；
- **推断（inference）**——词尾/词素启发式；输出中**始终**标记 `needs_review`；
- **未知（unknown）**——无适用规则：引擎拒绝猜测，返回阻断的候选，绝不默认阳性。

**人名种加词是属格名词。** 在 ICNP 下，词尾取决于姓氏**拉丁化成哪个变格范式**，而**不**取决于被纪念者的生理性别，也不只取决于词干末字符。三个范式的对应关系如下：

- 第一变格：`-a` → `-ae`。
- 第二变格：`-ius` 或 `-us` → `-ii`。
- 第三变格：`-er` → `-i`。

`person_genitive.json` 因此按范式建模，由范式表、**已证实的姓氏词库**（`boyd → boydii`、`burgdorfer → burgdorferi`、`hensel → henselae`、`gordon → gordonae`）与若干*默认*规则三部分组成。

引擎只在“姓氏已被证实且范式 `verified: true`”时断言合规。默认规则给出的提案一律返回 `needs_review` 与 `compliant=None`。范式也无法推断的姓氏（通常是元音结尾且未被证实）抛出 `GenitiveCellUnavailable`，而不是臆测。把其余 `verified: false` 的范式对照 ICNP 正字法附录与 LPSN 补齐，仍是未完成的 M0 专家事项，见[已知局限](#10-已知局限)。

**高阶元名称以型属的属格词干为基底构造。** 法典强制后缀（`family → -aceae`、`order → -ales`、`class → -ia`、`phylum → -ota` 等，含亚阶元）接在型属的**属格单数词干**上，因此要先删除或改写主格词尾。示例如下：

- *Bacillus*（属格 *Bacilli*）→ **Bacill** + `-aceae` = `Bacillaceae`。
- *Clostridium* → *Clostridi-* + `-ales` = `Clostridiales`。
- *Pseudomonas* → *Pseudomonad-* + `-ota` = `Pseudomonadota`。
- *Streptomyces*（属格 *Streptomycetis*）→ *Streptomycet-* + `-aceae` = `Streptomycetaceae`。

因此在这些阶元上，`--stem` 的预期输入是**主格属名**。词干取自 `data/type_genus_stems.json`。引擎**只有**在推导出的名称同时收录于该文件的 `attested_higher_rank_names` 时才断言合规。`Escherichiales` 这类“构成规则正确但未见发表”的名称会以 `compliant=None` 加显式警告返回，不会当作已发表名呈现。

---

## 3. 命令参考

`src/prokname/cli.py` 注册 **八** 条命令：`gen`、`check`、`route`、`project`、`bench`、`holdout`、`data`、`config`。八条都是核心的无头 CLI：本包没有任何模块导入图形工具包。桌面前端 **ProkName Studio** 是独立项目，像其他使用方一样依赖本包——见 [ProkName Studio](#prokname-studio)。

每条命令均支持 `--json` 机器可读输出（以下示例均可加 `--json`）。退出码见[第 4 节](#4-退出码与输出模式)。

### `prokname gen`

从词源词干生成候选学名。

```bash
# 人名词源 → 属格名词，与属名性别无关
prokname gen --stem Boyd --type person --rank species \
    --genus Shigella --person-gender male
# → Shigella boydii   （属格名词：属名性别无关）

# 地名形容词随属名语法性别变格
prokname gen --stem Beijing --type place --rank species --genus Rhizobium
# → Rhizobium beijingense        （中性 → -ense）
prokname gen --stem Beijing --type place --rank species --genus Klebsiella
# → Klebsiella beijingensis      （阴性 → -ensis）

# 特征词源同时输出形容词形式和同位名词形式
prokname gen --stem Wukong --type feature --rank species --genus Bacillus
# → Bacillus wukongus（形容词）与 Bacillus wukong（同位名词）

# 高阶元把法典强制后缀接在型属的"属格词干"上
# （--stem 传主格属名，见第 2 节）
prokname gen --stem Bacillus     --type feature --rank family    # Bacillaceae
prokname gen --stem Bacillus     --type feature --rank order     # Bacillales
prokname gen --stem Clostridium  --type feature --rank order     # Clostridiales
prokname gen --stem Clostridium  --type feature --rank class     # Clostridia
prokname gen --stem Streptomyces --type feature --rank family    # Streptomycetaceae
prokname gen --stem Pseudomonas  --type feature --rank phylum    # Pseudomonadota

# 属名无强制后缀；建议词尾可选
prokname gen --stem Wukong --type feature --rank genus --genus-suffix monas
# → Wukongomonas（辅音连缀处自动插入连接元音 -o-）
```

> 上述高阶元示例有两点保留。(1) **保留名**不会被规则“纠正”：`--stem Bacillus --rank class` 返回带“conserved name”警告的 `Bacilli`，而非常规构成形式 `Bacillia`。(2) 各后缀由哪部法典强制并不统一：`rules.json` 把 `-aceae`/`-ales` 标为 ICNP 强制词尾，把 `-idae`、`-ineae`、`-oideae`、`-eae`、`-inae` 标为“采纳植物学惯例”（亚阶元输出返回 `compliant=None`），把 `phylum → -ota` / `class → -ia` 标为 SeqCode 建议。这些归属仍需专家裁断——见[已知局限](#10-已知局限)。

选项：

| 选项 | 取值 | 说明 |
|---|---|---|
| `--stem` | 文本（任意文字；音译） | 必填；使用前拉丁化（德语 ü→ue、ä→ae、ö→oe、ß→ss，北欧 ø→oe、å→aa，æ→ae、þ→th、ł→l 等；去连字符；最终转写表待 M0 专家签字） |
| `--type` | `place` \| `person` \| `thing` \| `feature` | 默认 `feature` |
| `--rank` | `phylum` `class` `subclass` `order` `suborder` `family` `subfamily` `tribe` `subtribe` `genus` `species` `subspecies` | 默认 `species` |
| `--genus` | 属名或完整双名 | species/subspecies 必填；subspecies 接受双名（`"Bacillus subtilis"`） |
| `--person-gender` | `male` \| `female` | **被纪念者**的性别（非属名性别）；`person` 必填 |
| `--gender` | `m` \| `f` \| `n` | 专家复核后覆盖属名性别 |
| `--genus-suffix` | 如 `monas` | 属名等级的可选建议词尾 |
| `--adjective-formation` | `place` \| `second_declension` \| `third_declension` \| `loving` \| `nourishing` | 显式指定形容词变格范式 |
| `--json` | 开关 | 机器可读输出 |

每个候选行报告：完整名称、语法类别、属名性别、合规性（`yes` / `no` / `review`）、构词推导与警告。无法安全构造词尾的候选（如缺少 `--person-gender`，或属名性别未知）输出为 `Genus [?]` 并附解释，绝不静默丢弃。

### `prokname check`

两级查重：权威源状态 + 本地近似名（parahomonym）扫描。

```bash
# "Wukomonas" 是演示用的虚构属名（在 corpus_seed.json 中标为
# `synthetic-demo`）——它不是已发表名。
prokname check "Wukomonas beijingensis"
prokname check "Wukomonas beijingensis" --json
prokname check "Escherichia colii" --max-distance 1   # 收紧扫描阈值
prokname check "Wukomonas beijingense" --near-match-mode stem   # 词干级口径（v2）
prokname check "Some name" --no-near-match            # 仅查权威源
prokname check "Some name" --online                   # 查询权威 API（需凭据，M0 门控）
```

可依赖的行为：

- **默认离线。** 未指定 `--online` 且无凭据/配置时，四个源（LPSN、SeqCode Registry、GNA GNverifier、NCBI）均报告 `unavailable`。
- **`unavailable` ≠ `not found`。** 权威源无法查证时，裁定为 `blocked`（退出码 3）：“无法查证”绝不伪装为“未发现”。
- **参考源只警示。** GNA/NCBI 命中产生 `verify_warning`，绝不做冲突裁定。
- 本地近似名扫描报告编辑距离在 `--max-distance`（默认 2——经典易混对 `-ensis/-ense` 恰好位于距离 2）内的全部语料条目，并同时报告语料日期与溯源；语料超过 180 天将追加过期警告。随包语料是**带日期的演示语料**：`src/prokname/data/corpus_seed.json` 共 **16** 条（`corpus_date` 2026-08-16；来源标签 LPSN ×12、synthetic-demo ×2、SeqCode Registry ×1、NCBI ×1）。在其上的命中/未命中是**演示**，不是召回率或假阳率测量。
- **两种扫描口径。**`--near-match-mode whole`（默认）比较整名；`stem` 先剥离屈折词尾再比较（Taxamatch/GNmatcher 思想），词干距离 ≤ 1 即命中——`-ensis/-ense` 这类仅词尾有别的名称在词干空间坍缩为相同词干；`both` 输出两种口径的并集，每个命中保留最小距离。
- **常规 CI 不联网。** 在线适配器在 `tests/` 中以离线方式验证：桩件化的适配器客户端（见 `tests/test_online_adapters.py`）逐字段钉住上游请求/响应契约，`tests/test_api_fixtures.py` 覆盖离线与降级行为。回放*录制下来的* HTTP 响应（`tests/fixtures/` 下的 VCR 录制带）**目前不属于**常规 CI 环节：`tests/conftest.py` 如实记录了“尚未录制/提交任何 cassette”（录制需要真实 LPSN 凭据）。见[已知局限](#10-已知局限)。

五种裁定状态：`conflict`、`parahomonym_warning`、`verify_warning`、`blocked`、`no_clear_conflict`。以当前数据源，实际可到达的是 `conflict` 与 `blocked`；其余三种需要两个权威源都能给出答复（见 FAQ）。

### `prokname route`

ICNP 与 SeqCode 双法典路由；输出可行路径与利弊对比，绝不强行指定唯一选项。

```bash
prokname route --source MAG --icnp-occupied no
# → SeqCode（唯一可行）：基因组为模式、Registry DOI、质量阈值
prokname route --source pure_culture --icnp-occupied no
# → ICNP（默认：IJSEM + 模式菌株保藏）与 SeqCode（替代路径）双双列出
prokname route --source MAG --icnp-occupied yes
# → 冲突引导：SeqCode 承认 ICNP 优先权
prokname route --source MAG --candidatus
# → 仅 SeqCode 可行 + Candidatus 格式规则
```

| 选项 | 取值 | 说明 |
|---|---|---|
| `--source` | `pure_culture` \| `MAG` \| `SAG` \| `unknown` | 必填；`unknown` 时 prokname 要求先指定来源 |
| `--candidatus` | 开关 | Candidatus 格式引导 |
| `--icnp-occupied` | `auto` \| `yes` \| `no` | `auto`（默认）= 未查证 → 路径标记为临时 |

GTDB 占位标签（如 JABL01 风格）明确不在支持范围：它们不是两部法典下的名称，既不解析也不映射。

### `prokname project`

候选管理（本地持久化，FR-08）。项目为 JSON 文件，存储于 `~/.config/prokname/projects/`（可用 `XDG_CONFIG_HOME` 覆盖目录）。

```bash
# 创建带元数据的命名项目
prokname project create my-paper --data-source MAG --target-code SeqCode

# 生成候选并加入项目
prokname project add my-paper --stem Wukong --type feature --rank species --genus Wukomonas
prokname project add my-paper --stem Beijing --type place --rank species --genus Wukomonas

# 查看
prokname project list
prokname project show my-paper

# 为候选打分 0-5
prokname project rate my-paper --candidate "Wukomonas beijingensis" --score 5

# 导出（json | csv | markdown）——导出携带合规免责声明与 LPSN/SeqCode 署名
prokname project export my-paper --format markdown
prokname project export my-paper --format csv --json     # 包装为机器可读

# 删除
prokname project delete my-paper
```

说明：

- `add` 与 `prokname gen` 走同一生成管线（人名词源需传 `--person-gender`），并将全部生成候选追加进项目；词源参数非法时以退出码 1 报错。
- `rate` 将分数截断到 0～5；项目或候选不存在时退出码 1。
- `export --format` 仅接受 `json`、`csv`、`markdown`；其他取值以退出码 1 报错，绝不静默回退为 JSON。
- 项目名可含空格；磁盘文件名附加碰撞保护后缀，`my paper` 与 `my_project` 仍是两个独立项目。项目文件损坏时报明确错误（`ProjectLoadError`），不会伪装成“项目不存在”。
- 导出均标注：工具版本、导出时间戳、免责声明，以及代码原样写出的数据署名字符串（“LPSN data: CC BY-SA 4.0”与“SeqCode data: CC-BY 4.0”）。
  - 权威许可文本 `DATA_LICENSE` 目前**只授予两项条款**：LPSN 衍生部分为 CC BY-SA 4.0，自研规则文件为 CC0 1.0。
  - `DATA_LICENSE` 中**没有** SeqCode 衍生条款，其再分发条款在状态说明里列为待澄清问题，见[数据资产与许可](#7-数据资产与许可)。
- Markdown 导出还会为每个候选附带一张 **SeqCode Registry 风格的 etymology 草表**。整词与语法依存储的推导信息预填，词素行刻意留空，由作者补全。prokname 绝不臆造未被告知的词源。

### `prokname bench` / `prokname holdout`

```bash
prokname bench            # 引擎内置回归种子（25 个实名锚定用例）
prokname bench --full     # A/B1/B2/C/D 基准套件（含基线与自助法置信区间）
prokname holdout          # CI 门禁：A-"推断"子集 ∩ 已发布词库必须为空
```

`prokname holdout` 只强制一件事（`src/prokname/benchmark/holdout.py`）：A-**推断**子集中的任何属名都不得经词库命中。它**不**要求、也不应要求 `genus_gender.json ∩ A-set = ∅`：出现在 A **查找**子集里的 15 个词库属**本就应在词库中**（查找覆盖率正是拿它们度量的），因此完整 A 集与词库相交是设计使然。请勿靠删词库条目或删查找判例去“修”这个相交。

`bench --full` 逐套报告以下内容：

- A：查找覆盖率，以及引擎对多数类与朴素词尾基线的 macro-F1 与准确率（含 95% 自助法置信区间）。
- B1：一致性校验准确率与漏报率，目标为不超过 1%。
- B2：生成精确匹配率与 top-3 命中率。
- D：路由准确率与 ICNP 先占子类，目标为 100%。
- C：框架报告。它以 `skip_gan=True` 运行，即只跑 prokname 一侧，**与 `gan` 的双边对比从未执行**。

该命令同时是 **CI 门禁**：留出检查失败、B1 漏报率超标或 D 集先占子类未达 100% 时，以退出码 1 结束。每个结果同时携带拒绝感知准确率（含独立置信区间）：对真正未知的属，诚实的 `needs_review` 拒猜计为**正确**，拒不臆测正是设计行为。自助法区间是对“逐例是否正确”向量做的准确度区间，**不是** macro-F1 的区间，在全对样本上会坍缩为一点，见[已知局限](#10-已知局限)。

### `prokname data`

```bash
prokname data          # 每个打包数据资产的版本 + 门控状态
prokname data --json
```

### `prokname config`

LPSN 凭据状态与离线数据配置（FR-10）。

```bash
prokname config                                        # 查看状态
prokname config --taxdump-dir ~/data/taxdump           # 持久保存 NCBI taxdump 位置
prokname config --offline-snapshot ~/data/lpsn_export  # 持久保存 LPSN 快照位置（保留项：M0 前无适配器消费）
prokname config --clear                                # 清除已保存路径
prokname config --json
```

持久化路径写入 `~/.config/prokname/config.json`，跨调用生效；环境变量优先。密码绝不写入配置文件——LPSN 凭据应存入系统钥匙串（服务名 `prokname`）或 `PROKNAME_LPSN_USER` / `PROKNAME_LPSN_PASSWORD` 环境变量。LPSN API 免费注册： <https://api.lpsn.dsmz.de/>。

### ProkName Studio

桌面前端已是独立项目：**ProkName Studio**，安装名为 `prokname-studio`，中英双语界面、亮暗两套外观。它像其他使用方一样依赖本包，不新增任何命名逻辑——界面上每个结果都来自上文所述的那套 API。安装与启动方式见其[仓库](https://github.com/ZengZichao/ProkName-Studio)；该项目 `docs/STUDIO_PLAN.md` 记录它的设计与里程碑。

从本包这一侧看，成立的事实是：

- Studio 与 `prokname project` 使用同一个 `ProjectStore`，因此在任一入口建的项目在另一入口照样打开。
- Studio 不联网。上文为 `check` 描述的离线边界，就是图形界面报告的边界——没有 LPSN 凭据时哪些结论仍然不可达，两边一致。
- 两个入口的配色都经由 `prokname.presentation.decision`，因此“已阻塞”不可能在某个窗口里显得紧急、在另一个窗口里显得平静。

---

## 4. 退出码与输出模式

| 退出码 | 含义 |
|---|---|
| `0` | 成功（可能带警告） |
| `1` | 命令自身处理的运行时错误（非法输入、项目缺失、项目文件损坏等） |
| `2` | CLI 框架抛出的用法/参数错误（未知选项、缺少必填项、取值越界） |
| `3` | `check` 裁定：blocked——权威源不可用，**或答复无法据以裁定**（`found_unknown`）；本地近似名命中会以警告并列展示，绝不升格为裁定（见 FAQ） |
| `4` | `check` 裁定：conflict——权威源报告该名称已存在 |

退出码 2 属于 CLI 框架本身（typer/click 惯例），因此 conflict 裁定刻意使用 **4**：管线仅凭退出码即可区分“参数打错了”和“名称已被占用”。

每条命令在人类可读模式末尾打印一行免责声明，在 `--json` 模式内嵌 `disclaimer` 字段，便于下游管线延续署名信息。

---

## 5. Python API

```python
from prokname.engine import generate_candidates, validate_agreement, gender_of, Gender
from prokname.routing import route
from prokname.dedup import check_name

# 1. 生成候选
candidates = generate_candidates(
    "Beijing", "place", "species", genus="Klebsiella")
for c in candidates:
    print(c.name, c.grammatical_category, c.compliant, c.warnings)

# 2. 校验既有双名
result = validate_agreement("Rhizobium", "beijingense", "place")
print(result.compliant, result.expected_ending, result.warnings)

# 3. 性别判定（双模式）
gr = gender_of("Treponema")            # 词库命中 → 高置信
gr = gender_of("Mesoplasma")           # 推断（希腊 -ma 中性）→ needs_review=True
gr = gender_of("Zzz")                  # 未知 → 拒绝猜测
gr = gender_of("Somegenus", override=Gender.F)

# 4. 双法典路由
res = route("MAG", icnp_occupied=False)
print([p.code for p in res.viable_paths])

# 5. 两级查重（离线；"Wukomonas" 为演示用虚构属名）
report = check_name("Wukomonas beijingensis")
print(report.verdict, [s.status for s in report.sources])
```

全部规则表均为 `src/prokname/data/` 下的版本化 JSON；经 `prokname.engine.data`（`rules()`、`gender_lexicon()`、`gender_heuristics()`、`person_genitive()`、`stems()`、`corpus_seed()`）以只读方式加载。

---

## 6. 基准测试

随包发布的种子基准（v0.1，2026-09-04 扩充）覆盖：

| 套件 | 用例数 | 指标 | 种子结果 |
|---|---|---|---|
| A — 性别判定 | 74（词库查找 15 + 推断 59） | 查找覆盖率；推断 macro-F1/准确率对比多数类与朴素词尾基线；拒绝感知准确率 | 拒绝感知准确率 **1.00**；原始准确率 0.98；needs_review 率 1.7% |
| B1 — 一致性校验 | 26，其中仅 **4 例负例**（person 2、place 2、feature 0、thing 0） | 准确率；漏报率（目标 ≤ 1%） | 准确率 **1.00**，漏报率 **0.00** |
| B2 — 生成精确匹配 | 17 | 精确匹配率 / top-3 命中率 | **1.00 / 1.00** |
| C — GAN 对比 | 5 词干 × 3 次重复 | 框架：相对 GAN 的覆盖/可行性，CLI 隔离（GPL-3.0） | **未执行**——`bench --full` 以 `skip_gan=True` 跑该套件，与 `gan` 的双边对比从未运行 |
| D — 双法典路由 | 11（3 例 ICNP 先占） | 准确率；先占子类（目标 100%） | **1.00**，先占 **100%** |

这些文件里的标注者（annotator）取值只有 `seed` 与 `curated-seed` 两种：既没有第三方标注者，也没有标注者间一致性度量。

复现方式：`prokname bench --full --json > results.json`。留出门控（`prokname holdout`）强制 **A 推断子集**与已发布词库不相交。A 查找子集里那 15 个词库属按设计就是相交的，见 [`prokname bench`、`prokname holdout`](#prokname-bench--prokname-holdout)。

2026-09-04 扩充新增的用例一律标注 `curated-seed`。“待 M0 LPSN API 批量核验”这条显式注记只在 `a_set.json` 中逐例携带（55 例），其余套件在文件级 `_meta.expanded` 携带，并非每个套件的每个用例都带该注记。升级为 LPSN 衍生、留出控制的论文级集合（不少于 1,920 例）属于 M1/M2 交付物。

`bench --full` 报告的置信区间是对“逐例是否正确”向量做的**自助法**准确度区间，不是 macro-F1 的区间，在全对样本上会坍缩为一点。小样本的精确二项界限见[已知局限](#10-已知局限)。

---

## 7. 数据资产与许可

| 资产 | 用途 | 状态 |
|---|---|---|
| `rules.json` | 阶元后缀、形容词范式、类型→类别映射 | M0 草案，待专家签字 |
| `person_genitive.json` | 拉丁化变格范式表 + 已证实姓氏词库 + 默认规则 | 仅“已证实且 verified:true”的条目可断言合规；默认规则一律 `needs_review`；3 个范式 `verified: false` |
| `genus_gender.json` | 查找词库（**58** 个策展属：23 f / 20 m / 15 n） | 人工策展种子 |
| `type_genus_stems.json` | 型属属格词干 + 已证实的超属级名称（用于高阶元推导） | M0 草案；驱动“未见证实即 `compliant=None`”的门控 |
| `gender_endings.json` | 推断启发式（词素 → 例外 → 一般词尾） | 启发式，始终 `needs_review` |
| `stems.json` | 用户可扩展的词干库 | 示例种子（尚未接入 CLI） |
| `corpus_seed.json` | 近似名**演示**语料：16 条，`corpus_date` 2026-08-16（LPSN ×12、synthetic-demo ×2、SeqCode Registry ×1、NCBI ×1） | 仅测试/演示 |
| `lpsn_status.json` | 可专家审阅的 LPSN `lpsn_taxonomic_status` 标签→类别映射（取代旧的子串判定） | 由 `dedup/lpsn.py` 消费；`prokname data` 尚未列出该资产 |
| `rate_limit_budget.json` | 语料重建的 API 限流预算表 | M2 交付项 |

`prokname data` 报告的是经 `engine/data.py::asset_status()` 接线的八个资产；`lpsn_status.json` 由 LPSN 适配器直接加载，因此不在该清单中。

58（词库规模）与 59（A 推断子集规模）是两个互不相关的计数；A 集本身为 74 = 15 查找 + 59 推断。

**数据许可以 `DATA_LICENSE` 为唯一权威文本，它只授予两项条款：** LPSN 衍生部分为 CC BY-SA 4.0（署名与相同方式共享），自研规则文件（`rules.json`、`gender_endings.json`、`person_genitive.json`、`stems.json`）为 CC0 1.0。代码本身为 MIT（`LICENSE`）。

`DATA_LICENSE` 中**没有** SeqCode 衍生条款。`storage/store.py` 写出的“SeqCode data: CC-BY 4.0”与 `corpus_seed.json` 的 `_meta.provenance` 只是散文表述，不构成已授予的许可。SeqCode 衍生数据的再分发条款已在 `DATA_LICENSE` 的状态说明里列为待澄清问题。

所有导出与 JSON 输出仍携带上述署名字符串，请把它理解为对上游来源的署名。再发布衍生资产之前，先重新核读 `DATA_LICENSE` 的状态说明。

---

## 8. 环境变量与配置文件

| 变量 | 作用 |
|---|---|
| `PROKNAME_LPSN_USER` / `PROKNAME_LPSN_PASSWORD` | LPSN API 凭据（或系统钥匙串，服务名 `prokname`） |
| `PROKNAME_TAXDUMP_DIR` | NCBI taxdump 目录（离线 NCBI 查询、语料构建） |
| `PROKNAME_LPSN_SNAPSHOT` | 本地 LPSN 官方导出目录——**保留项**（FR-10）；M0 前无适配器消费 |
| `PROKNAME_CACHE_DIR` | 查重缓存目录（默认 `~/.cache/prokname`） |
| `XDG_CONFIG_HOME` | 项目（`…/prokname/projects`）与配置（`…/prokname/config.json`）的根目录 |

优先级：环境变量 → 持久化配置文件 → 内置默认。`prokname config --taxdump-dir …` / `--offline-snapshot …` 写入配置文件；`prokname config --clear` 清除。

---

## 9. 故障排查与 FAQ

**`prokname: bad interpreter: …/prokname/.venv/bin/python3.14: no such file or directory`** `pip install -e .` 之后项目目录被移动所致。修复：`.venv/bin/python -m pip install -e ".[dev]"`（重新生成入口脚本 shebang 与 editable 路径）。

**`prokname --version` 打印“Missing command.”** 2026-09 起已修复（`invoke_without_command=True`）。若仍出现，说明运行的是旧安装——按上文重装。

**`import prokname` 能导入但没有 `__version__`，`prokname.cli` 找不到** editable `.pth` 指向不存在的路径，Python 回退到遗留 `site-packages/prokname` 目录形成的命名空间包。重装（`pip install -e ".[dev]"`）或重建虚拟环境。

**`gen` 输出候选为 `Genus [?]` **引擎无法安全构造词尾：`person` 词源需传 `--person-gender`；属名性别未知时专家复核后传 `--gender m|f|n`。这是刻意设计——见[核心概念](#2-核心概念)。

**`check` 对未被占用的名字退出码 3（blocked）。** 两件事同时成立，且都来自实测：（1）`4`（冲突）可达，且自 2026-09-25 起覆盖面更大——SeqCode 适配器改依带日期的占用快照裁定，凡在 SeqCode 下已注册并有效发表的名字，即使完全离线也判为冲突；（2）其余情形返回 `3`，因为目前没有任何权威源能回答“没有”——SeqCode 公开 API 无按名字查询，且实测其 `status=SeqCode` 列表会漏掉注册中心自己标注为 `Valid (SeqCode)` 的名字，因此“不在列表里”不等于“注册中心里没有”，适配器有意返回 `found_unknown` 并阻断裁定；LPSN 本可定论，但需凭据且必须可达。请把 `3` 读作“工具没能问到”，绝不是“这个名字没问题”。本地近似名命中仍以警告形式并列展示，不会静默丢弃。离线模式下所有权威源报告 `unavailable`，按设计阻断裁定。

如需获得裁定结果，有两种做法：

- 安装可选的 online 扩展组（`pip install -e ".[online]"`，含官方 `lpsn` 客户端、`httpx` 与 `keyring`），存好凭据（keyring，或 `PROKNAME_LPSN_USER` 与 `PROKNAME_LPSN_PASSWORD`），再用 `--online` 运行。
- 在明知权威源未查证的前提下解读本地近似名结果。

**人名属格抛出 `GenitiveCellUnavailable` **该姓氏既未在 `person_genitive.json` 中被证实，也没有任何有记录的默认规则能覆盖它——通常是因为姓氏以元音结尾，其拉丁化范式无法从拼写推断。相关默认范式格子按设计保持 `verified: false`，待 M0 对照 ICNP 正字法附录由专家签字。引擎拒绝臆测。

**支持 GTDB 标签吗？**不支持——GTDB 占位标签不是 ICNP 或 SeqCode 下的名称，按设计既不解析也不映射。

---

## 10. 已知局限

以下为本仓库当前**真实**能证明的边界，逐条都是种子数据的属性，不是使用不当。

- **仅为种子规模的基准。** A 74 / B1 26 / B2 17 / C 5×3 / D 11 例。所有“1.00”都是小样本全对：精确（Clopper–Pearson）95% 置信区间分别为 B1（26/26）[0.87, 1.00]、B2（17/17）[0.80, 1.00]、D（11/11） [0.72, 1.00]、D 先占子类（3/3）[0.29, 1.00]——3/3 不足以支撑“达成 100% 目标”。`bench --full` 打印的自助法区间是准确度区间，在全对样本上坍缩为一点。
- **B1 的“漏报率 ≤ 1%”目标在当前数据下不可检验。** B1 只有 **4** 例负例，且集中在四条词源支中的两条（person 2、place 2、**feature 0、thing 0**）；0/4 的单侧 95% 上界为 **52.7%**，而没有负样本的分支根本不受该门控约束。
- **标注来源为自标注。** 基准集里 annotator 只有 `seed` 与 `curated-seed` 两种取值：没有独立标注者，也没有标注者间一致性度量。
- **专家签字尚未完成（M0）。**`rules.json`、`person_genitive.json` 与转写表均标注 M0 草案；依赖它们的结论只能表述为“可由专家审阅、待签字”，而非“已核验”。
- **高阶元后缀→法典的归属需要专家裁断。**`rules.json` 现在已为每个超属级后缀标注强度（`rank_suffix_policy`：`-aceae`/`-ales` 为 ICNP 强制，`-ota`/`-ia` 为 SeqCode 建议，`-idae`/`-ineae`/`-oideae`/`-eae`/`-inae` 为采纳植物学惯例），引擎也拒绝对“采纳惯例”级阶元断言合规。但 `rules.json` 仍把这些归属本身及其条款引证标注为 `verified: false`（`rule_citation_status`），因此“哪个后缀由哪部法典强制”仍是专家问题而非既定结论；`conserved_names` 只列 16 个保留名，远少于法规附录所列。
- **近似名语料是 16 条演示集**（`corpus_seed.json`，`corpus_date` 2026-08-16；LPSN ×12、synthetic-demo ×2、SeqCode Registry ×1、NCBI ×1）。未在其上测量假阳率、召回率或阈值敏感性；`Wukomonas` 是演示用虚构属名。
- **C 集对比从未运行。**`bench --full` 以 `skip_gan=True` 评估 C 套件；gan CLI 适配器与公平性协议存在但不产出任何对比数字，CI 也不安装 gan。
- **录制的 HTTP 回放在常规 CI 中不存在。** 在线适配器由离线桩件客户端钉住；在 `tests/fixtures/` 下录制 cassette 需要真实 LPSN 凭据，属 M0/M2 事项（`tests/conftest.py` 已如实说明）。
- **taxonkit 交叉验证门控是 opt-in，且随包 CI 镜像会跳过**：CI 只装 `.[dev]`，不安装 `taxonkit` 二进制；这 3 项测试收集后立即跳过，除非使用方自备该二进制。
- **`check` 目前只能出具两种裁定。** 冲突（4）与阻塞（3）；中间裁定态要等 LPSN 能给出否定答复，SeqCode 侧还缺一个完整且稳定的名单（或按名字查询的端点）。

English version: [USAGE.md §10](USAGE.md#10-known-limitations)。
