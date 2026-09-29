# 变更日志

[English](CHANGELOG.md) | [中文](CHANGELOG.zh.md)

> 本文件是 [CHANGELOG.md](CHANGELOG.md) 的中文对照版，以英文原文为权威文本。

本项目的所有重要变更都记录在此文件中。

格式遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/spec/v2.0.0.html)。

## [0.1.0] - 2026-09-29

**prokname** 的首个公开发布版：在 ICNP 与 SeqCode 两部法规下，为原核生物新分类单元
命名提供决策支持。下文描述的是工具当前具备的能力；「已知限制」一节同样明确地写出它
现在还做不到什么。

prokname 是确定性的、基于规则的：管线里没有大语言模型，每条规则都存放在带版本号的
JSON 资产里，每一份输出都携带来源署名。它只提供命名辅助决策——名称的有效性完全由
ICNP 或 SeqCode 下的正式发表程序决定，本工具的输出不构成有效性裁定。

桌面前端是一个**独立项目**：[ProkName Studio][studio]（`prokname-studio`），它像任何
其它使用方一样依赖本包。依赖是单向的：引擎不导入任何 GUI 工具包，也不提供 GUI 扩展组，
因此 `pip install prokname` 只安装 typer + rich。两个界面共用的是下文所述的表现层策略。

[studio]: https://github.com/ZengZichao/ProkName-Studio

### 新增 —— 生成与校验

- **三分语法类别。** 种加词被判定为真形容词、属格名词或同位名词，每一类按自己的规则
  变格，而不是被强行塞进同一条变格路径。
- **确定性的候选生成**：由词源输入驱动（`stem`、`type`、`rank`，可选 `genus`、
  `person_gender`、`gender_override`、`genus_suffix`、`adjective_formation`），结果再经
  拉丁正字法规则处理。
- **双模式属名性别判定**：一份人工整定的 58 属词表（23 阴性 / 20 阳性 / 15 中性）加上
  词尾推断，而推断的输出会标注 `needs_review`，不冒充已定论。
- **围绕这些类别的性数格校验。** 缺少属名或种加词会以 `ValueError` 拒绝并报出参数名；
  高阶元的校验被指向生成流程，而不是静默地误分析。
- **人名属格词尾按拉丁化变格范式建模**，不是「人名性别 × 词干细胞首字母」网格：范式定义
  加上一份有实证的姓氏词库，于是 `Burgdorfer → burgdorferi`、`Hensel → henselae`、
  `Gordon → gordonae`、`Boyd → boydii` 都能得出正确结果。只被默认规则覆盖的姓氏返回
  `needs_review` 提案；范式无法推断的姓氏抛出 `GenitiveCellUnavailable`。第三变格
  `-er` 分支会填充 `-i`，因此文档里写的 `-i`/`-ii` 变体容忍是可以触发的，而不是死代码。
- **高阶元名接在型属的属格词干上**（来自 `data/type_genus_stems.json`）：
  `Bacillus → Bacillaceae / Bacillales`、
  `Clostridium → Clostridiales / Clostridiaceae / Clostridia`、
  `Pseudomonas → Pseudomonadota`、`Streptomyces → Streptomycetaceae`。构形规则上成立
  但无实证的名称（如 `Escherichiales`）以 `compliant=None` 返回并附明确的「无实证」
  警告；`Bacilli` 这类保留名被原样维护，而不是被「纠正」。
- **`rules.json` 标注每个超属级后缀的强度**（`rank_suffix_policy`）：`-aceae`/`-ales`
  为 ICNP 强制，`-ota`/`-ia` 为 SeqCode 建议，`-idae`/`-ineae`/`-oideae`/`-eae`/`-inae`
  为采纳的植物学法规惯例。引擎不从「采纳惯例」那一档断言合规。
- **拉丁化转写不止德语字母组合**：ø→oe、å→aa、đ/ð→d、þ→th、ł→l、ı→i 都被转写而非丢弃
  （*Bjørn → bjoern*、*Århus → aarhus*）。

### 新增 —— 双法典路由

- **`prokname route`** 输出可行路径及其利弊对比，按 ICNP 先占优先排序，并交代 GTDB
  边界，而不是假装两部法规覆盖同一片地界。
- 明确的 `icnp_occupied` 结论能在「来源未知」的提前返回中存活，作为冲突警告呈现；
  `pure_culture` 与 `candidatus` 同时成立是一处语义矛盾，现在会给出陈述两种读法的警告，
  而不是静默择一。

### 新增 —— 两级查重

- **本地近似名（parahomonym）扫描**，三种成文口径：`--near-match-mode whole|stem|both`
  （默认 `whole`）。`stem` 依据从随包规则资产导出的后缀表，为每个词剥离一个拉丁屈折词尾，
  并把词干距离 ≤ 1 者标注为命中，使仅后缀有别的变体（`-ensis/-ense`、`colii/coli`、
  `boydii/boydiae`）归并为词干相同对；`both` 报告两者并集，每条命中取最小距离。所选口径
  会记录进报告载荷。扫描使用带缓存的长度分带索引，语料只归一化一次。
- **权威源适配器**（LPSN、SeqCode Registry、GNA）：
  - *LPSN* 依官方客户端的真实契约驱动——先 `search()` 再 `retrieve()`，带
    `match_mode="exact"`，并在分类前对每条返回的 `full_name` 做同一性核对，因为该服务的
    默认行为是子串检索。子串命中给出诚实的 `not_found` 并列出最接近的记录；认证失败、
    被拒绝的查询与真正的零结果查询三者可区分；客户端的 stdout 被捕获，`--json` 输出保持干净。
  - *SeqCode* 依据 `scripts/build_seqcode_snapshot.py` 构建的带日期占用快照离线裁定，
    因此一个已依 SeqCode 有效发表的名称会在**无凭据、无网络**的情况下给出 `conflict`
    （退出码 4）。**未命中不等于裁定**：实测发现 `status=SeqCode` 名单会漏掉自身记录写着
    `status_name: "Valid (SeqCode)"` 的名称，且两次相同爬取约有 4 % 的内容不一致，因此
    缺席返回 `found_unknown`，其阻断裁定的效果与权威源不可达完全相同。
  - *GNA* 依当前 GNverifier 的 REST 契约（POST + JSON 请求体，
    `bestResult` / `matchedName` / `isSynonym` / `editDistance`），并附一份真实线上抓取
    录制成的回放夹具。
- **状态分类是枚举映射，不是子串测试。** `lpsn_status.json` 逐 token 把成文的
  `lpsn_taxonomic_status` 标签映射到类别，于是 `Non-validly published name` 不可能被读成
  有效发表；命名学词表与分类学词表在构造上互不混同，同时归属两者的标签判为 unknown 而
  不是猜一个。
- **`unavailable` 永远不是 `not found`。** 裁定集合为 `conflict`、`blocked`、
  `verify_warning`、`no_clear_conflict`、`parahomonym_warning`；无法查证的适配器会阻断
  裁定，而不是给名称放行。在 SeqCode 离线快照与 LPSN 凭据门控的现状下，真实 `check`
  今天只会退出 3 或 4；本地近似名命中在 `blocked` 下作为警告呈现，标注「仅警告，不构成
  裁定」，报告同时披露语料规模与是否截断，因此空命中列表不会被当作「不存在」的证据。
- **稳定退出码**：`0` 正常 · `1` 错误 · `2` CLI 框架抛出的用法/参数错误 · `3` 阻断 ·
  `4` 冲突。打错一个参数不再会被误读成名称已被占用。
- **查重缓存**：按（名称、来源、日期）缓存，7 天 TTL，批量运行支持检查点续跑；缓存命中
  会被如实标注，适配器崩溃时降级为诚实的 `unavailable`，而不是让整次检查失败。

### 新增 —— 项目存储与导出

- `prokname project` 创建 / 添加 / 查看 / 打分 / 导出 / 删除，携带 `data_source` 与
  `target_code` 元数据，评分入库保存。
- 项目文件携带 `schema_version`：无标记的文件是版本 0，仍可加载；来自更新构建的文件
  会被拒绝并给出理由，而不是读一半。结构损坏的 JSON（`name` 缺失或类型错误、
  `candidates` 不是列表）抛 `ProjectLoadError`，而不是裸的 `KeyError`/`TypeError`。
- JSON 写入统一走共享的原子写辅助函数；缓存与项目文件名带上原始名称的短哈希，于是
  `Bacillus subtilis` 与 `Bacillus_subtilis`、`my paper` 与 `my_project` 不再互相覆盖。
- 导出（JSON / CSV / Markdown）始终携带合规免责声明与 LPSN / SeqCode 署名；被保存的
  查重裁定随候选一起带走；`--format` 会校验取值，不再以别的名义静默输出 JSON；Markdown
  导出会为每个候选追加一张 SeqCode Registry 要求格式的 etymology 草表，其中词素行刻意
  留空由作者补全——prokname 绝不臆造未被告知的词源。

### 新增 —— 基准与可复现性

- **种子基准集 A / B1 / B2 / C / D**，每条用例携带完整的七字段溯源
  （`source`、`lpsn_id`、标签或 `grammatical_category`、`annotator`、`annotated_at`、
  `license`、`note`），并配多数类与朴素词尾基线。
- **自我披露方法的统计报告**：冻结标签集用 Clopper–Pearson 精确区间，其余用逐例自助法
  百分位区间；拒绝感知准确率（对真正未知属诚实给出 `needs_review` 的拒答计为正确）；
  可判定类（m/f/n）macro-F1；比较用配对差自助法与精确 McNemar；混淆矩阵保留每个实际
  出现的标签。区间名称逐字打印，读者无法把错误的区间挂到错误的指标上。
- **`prokname bench --full` 是 CI 门禁**：检查留出集完整性、B1 漏报率目标与 D 集 ICNP
  先占正确性。被**违反**的门禁一律失败；而随包种子**无法检验**其目标的门禁作为证据欠账
  报告，只在 `--require-complete`（里程碑模式）下失败。没有任何东西靠沉默获得认证：
  这一行打印为 `b1_fnr=pass(NOT CERTIFIED)`，JSON 里也这么写。
- **留出集完整性检查**（`prokname holdout`）以与引擎完全一致的方式归一化属名
  （转写 + 首字母大写），因此带变音符号拼写的属名无法绕过互斥门禁；该不变量是 A 集的
  推断子集与已发表词表互斥，而 A 集查表子集中的 15 个词表属按设计会重叠，不属于泄漏。
- **GAN 对比（C 集）**在 GPL-3.0 隔离下运行，固定随机种子、每个词干 ≥ 3 次重复，并需要
  显式命令规范：适配器拒绝猜测竞争工具的参数，所以在给出命令规范之前不存在任何 GAN
  数值，报告也会写明当前处于哪种状态。两侧由同一个中立的正字法裁判评判——prokname 不用
  自己的 `compliant` 裁定给自己打分——而该裁判的局限被写明而非隐藏。
- **示例核验**（`scripts/verify_examples.py`）把每个成文示例对引擎重跑一遍，保留名按结构化
  列表精确匹配而非子串匹配，并为每个未核验的格子标注它等待的是哪条规则。
- 语料重建（`scripts/rebuild_corpus.py`）提供 LPSN / SeqCode / NCBI taxdump 三源重建脚手架，
  带限流器、缓存、检查点与原子导出；失败或部分的运行退出码非零且不写出任何东西。

### 新增 —— 界面与配置

- **九条命令**（`gen`、`check`、`route`、`project`、`config`、`bench`、`data`、
  `holdout`，`--version` 在命令组层级），每条都是双模式输出：人类可读与 `--json`，
  遵守「每条命令都接受 `--json`」这一契约。
- **持久化配置**写在 `~/.config/prokname/config.json`，用于 taxdump 与快照位置；环境
  变量仍然优先，且该文件绝不存储任何凭据；LPSN 凭据存放在操作系统钥匙串或环境变量里。
- **诊断**：`--debug` 与 `PROKNAME_DEBUG=1` 会回显 `check --online` 通常吞掉的第三方
  客户端输出，让操作者能区分「答复：未找到」与「请求从未成功」。不改变任何裁定与退出码。
- **`prokname data` 报告每个规则资产的状态**——版本、门控情况、由哪个模块消费
  （`stems.json` 标注 NOT CONNECTED，`rate_limit_budget.json` 标注 PARTIAL），以及仍有
  多少条断言等待专家核验。随包携带一个资产因此不再意味着它产生了作用。
- **随包数据资产为只读**：改动其一会在越界那一行抛 `TypeError`，而不是改写进程内所有
  其它模块正在读取的规则。`prokname.reload_data()` 会重读资产并连带丢弃全部派生缓存，
  因此专家审阅工作流（评审一份候选 `rules.json`、替换文件）不再需要重启进程。
- **`prokname.presentation`**——一个不依赖 Qt 的共享表现层，持有语义色值 token 与
  「裁定—角色」映射，于是终端与桌面前端渲染的是同一个含义，同一个裁定不可能在一个界面里
  显得紧急、在另一个界面里显得平静。未知角色失败安全地退到中性 token，而不是抛异常。
- 性能测试固定非功能目标：生成 < 100 ms、路由 < 10 ms、五万名称的近似名扫描 < 3 s。

### 新增 —— 打包与文档

- `pyproject.toml`（hatchling）中版本只在一个地方声明——`prokname.__version__`——由构建
  后端动态读取，于是 wheel、`prokname --version` 与导出指纹不可能互相矛盾。
- `Dockerfile`（多阶段、非 root）配 `.dockerignore`；`recipes/bioconda/meta.yaml`
  （noarch Python）；`requirements*.txt` 被说明为 `pyproject.toml` 的手工维护镜像，
  并明确**不是**锁文件。
- `LICENSE`（MIT）、`DATA_LICENSE`（数据条款）、带作者与 ORCID 的 `CITATION.cff`。
- **文档中英双语、以英文为主**：`README`、`USAGE`、`CONTRIBUTING`、`CHANGELOG`、
  `DATA_LICENSE` 都以英文原件 + 中文伴译两份交付，语言切换行英文在前。`USAGE` 逐条命令
  给出示例、退出码、Python API、基准复现与故障排查。配对关系由测试强制，文档不会退化到
  只剩一种语言。
- **`docs/provenance/`** 记录代码依赖的每一个外部事实：仓库、commit SHA、文件:行号、
  检索日期与重检命令，中英两份。代码注释引用这份登记表而不是作者磁盘上的路径；登记表
  还以一次实测的 404 说明分支名不是版本钉。
- 每项许可义务只在一处陈述：`prokname.LPSN_ATTRIBUTION`、`prokname.SEQCODE_ATTRIBUTION`
  与 `source_attribution()`。这些措辞若在别的模块重现，测试会失败——因为同一项义务在两份
  导出里出现两种写法，正是一个以合规陈述为交付物的工具必须消除的失败模式。

### 新增 —— 质量门禁

- ubuntu / windows / macos × Python 3.11–3.14 的测试矩阵；ruff 作为独立作业；覆盖率下限；
  wheel 构建 + 干净虚拟环境的安装冒烟；留出集完整性检查阻断。
- 常规 CI **不发起任何网络调用**：在线适配器由契约固定的离线桩覆盖。一个每周计划作业
  重新核验引文登记表并探测 SeqCode 端点契约，于是上游漂移能被察觉，而上游故障不会把无关
  的拉取请求染红。
- 保证架构诚实的守卫：导入顺序契约（每个模块都必须能作为全新解释器的第一件事被导入，
  包门面造成的环留在包内部）、表现层边界、许可措辞唯一来源、仓库外路径引用禁令、文档
  中英配对，以及由 CI 校验的自动生成测试计数——取自 `pytest --collect-only` 的实测，
  绝不手工誊写。
- 测试套件是自洽的：既不读写开发者的缓存，也不读写工作目录；可选的 `taxonkit` 交叉验证
  ——拿内置 NCBI taxdump 解析器比对参照实现——是「收集后跳过」而非静默缺席，因为随包的
  CI 镜像既不装该二进制，也不带任何录制带存储。

### 数据与许可

- 代码：MIT。数据：LPSN 衍生部分为 **CC BY-SA 4.0**，自研规则文件为 **CC0 1.0**，
  SeqCode Registry 快照为 **CC BY 4.0**——最后一条引自注册中心自己的页面并记录了检索日期。
  `DATA_LICENSE` 是权威文本，并且列出了它**尚未**解决的事项。

### 已知限制

- **规则资产的专家签字仍未完成。** `rules.json` 与 `person_genitive.json` 携带标注为
  `verified: false` 的格子，包括超属级阶元的归属及其条款引证。工具不会代替它们断言有效性。
- **人名属格范式尚不完整。** `-iae`、`-is` 与默认 `-ae` 三套范式仍是等待依 ICNP 正字法
  附录裁断的提案，元音词干细胞也未填满。
- **`check` 目前还不能在实际场景中裁定。** SeqCode 未提供按名查询，因此离线快照只能给出
  肯定占用、永远不能给出缺席；LPSN 需要凭据。在此之前 `check` 返回 `conflict` 或
  `blocked`，三个干净裁定继续被门控。为每个实际使用的端点录制线上夹具仍是未完成事项。
- **基准集为种子级**（A / B1 / B2 / C / D 合计 135 条标注用例，目标为 ≥ 1 920 条 LPSN
  衍生、留出受控的用例）。在该规模下 B1 漏报率目标不可检验，报告会把它陈述为证据欠账，
  而不是一次通过。
- **两项许可问题仍未解决**：混合来源的规则资产在被镜像进公共数据集或发布归档之前需要
  逐格衍生审计；以及完整 SeqCode 名单的再分发是否触发欧洲的 sui generis 数据库权。
- **待补的注册条目**：Zenodo DOI 与 bio.tools 记录。
- 被引文献的 DOI（Freese 2026、Ratatoskr、Trüper & de'Clari）仍有待核验。
