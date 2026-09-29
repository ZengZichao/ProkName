# prokname 贡献指南

[English](CONTRIBUTING.md) | [中文](CONTRIBUTING.zh.md)

感谢您关注并改进 prokname。本文档列出贡献者必须遵守的核心规则。

## 核心原则

prokname 是**确定性、可审计**的命名工具。引擎把每条规则编码为版本化的 JSON 数据资产，并在每条输出里标注数据来源。以下规则是硬性要求。

## GAN 代码隔离（GPL-3.0 隔离）

工具 `gan-nomenclature`（telatin/gan）以 GPL-3.0 许可发布，而 prokname 为 MIT 许可。为防止许可证传染：

- **严禁**将 GAN 仓库的任何源代码复制到本代码库。
- C 集基准对照实验**仅通过 GAN 的 CLI**（子进程调用）使用其输出，绝不作为导入库使用。
- 如需引用 GAN 的行为，引用其输出，而非其代码。

## 数据规则

- **数据许可以 `DATA_LICENSE` 为唯一权威文本**，它授予三项条款：
  - LPSN 衍生部分为 **CC BY-SA 4.0**。
  - 自研规则文件（`rules.json`、`gender_endings.json`、`person_genitive.json`、`stems.json`）为 **CC0 1.0**。
  - SeqCode Registry 快照（`seqcode_registered.json`）为 **CC BY 4.0**。
- **LPSN 数据**（CC BY-SA 4.0）只能通过官方 API 或 downloads 渠道获取，**严禁爬取**。
  - 衍生数据（如 `genus_gender.json`）必须同时携带署名与相同方式共享义务。
  - ShareAlike 的含义：由 LPSN 文本派生的规则表要以 CC BY-SA 4.0 再发布，不能用 CC0。改动任何资产的许可之前，先核对它的派生来源。
- **SeqCode 数据**：以 `DATA_LICENSE` 的「SeqCode Registry data — resolved 2026-09-25」一节为准。注册中心自己的页面写明其贡献信息以 **CC BY 4.0** 发布，因此 `seqcode_registered.json` 依该条款再分发。义务是署名：保留 `_meta.licence` / `_meta.attribution` 字段，再发布时引用 SeqCode Initiative 以及逐条名的 `https://seqco.de/i:<id>` URI。CC BY 不含相同方式共享义务——但当一个文件同时混有 LPSN 衍生与 SeqCode 衍生内容时，以严格的 BY-SA 义务约束整个文件。`DATA_LICENSE` 仍未解决的不再是许可本身，而是欧洲的 sui generis 数据库权，注册中心从未就此发表过声明。
- **NCBI taxdump**：遵循 NCBI 使用条款，taxdump 可再分发。
- **绝不**将 API 凭据、密码或令牌提交到仓库。凭据放在 OS 钥匙串或环境变量里（见 `prokname config`）。
- 依赖范围以 `pyproject.toml` 为准（权威声明）。`requirements.txt` 与 `requirements-dev.txt` 是手工维护的便利镜像，其中还列了 `typer`/`rich` 的部分传递依赖。两者**不是**锁文件，与 `pyproject.toml` 不同步就会漂移。

## 规则变更

全部规则表（`rules.json`、`person_genitive.json`、`gender_endings.json`、`genus_gender.json`）必须先经**领域专家签字**（M0 门控），代码才能依赖它们。禁止凭记忆填充未验证的格子：参照现有代码处理元音词干属格格子的做法，抛出 `GenitiveCellUnavailable` 或等价异常。

## 测试

- 每次代码变更须通过 `prokname bench`（实名锚定回归种子）。
- holdout 完整性检查（`prokname holdout`）必须通过：发布词库与 **A-“推断”子集**互斥。
  - 该规则的口径刻意比“词库 ∩ A-set = ∅”更窄。
  - 出现在 A **查找**子集中的 15 个词库属按设计就是相交的，不属于泄漏，不要为此删词库条目或删判例。
- 属性测试（hypothesis）覆盖正字法不变量，禁止弱化。
- **常规 CI 不联网，也不回放录制的 HTTP cassette**。
  - 在线适配器以离线方式针对桩件化适配器客户端验证，后者逐字段钉住上游契约（`tests/test_online_adapters.py`，以及 `tests/test_api_fixtures.py` 中的离线与降级用例）。
  - `tests/conftest.py` 如实记录“尚未录制任何 cassette”。回放能力要等真正录出夹具后才可用：需用真实 LPSN 凭据在 `tests/fixtures/` 下录制，属 M0/M2 凭据事项。
  - 在此之前，把 CI 描述为“离线桩件验证”，不要描述为“回放录制的响应”。测试绝不访问真实网络，请使用 mock 或回放。
- 测试数量是生成的，不是手写的：`python scripts/fill_test_counts.py` 写入 `pytest --collect-only` 实际收集到的数量（当前套件收集 967 项），CI 的 `--verify` 步骤会在它失配时报错。现在只有一个数字——本包没有任何模块导入 Qt，因此也没有任何测试被图形扩展组门控。ProkName Studio 的测试随 Studio 一同计数。
  - 3 项 `taxonkit` 交叉验证测试在所有环境都会进入收集清单，但 `PATH` 上缺少 `taxonkit` 二进制时**跳过**。运行 pytest 的 CI 作业两者都不装（`.github/workflows/ci.yml` 中没有 `taxonkit` 步骤），因此该交叉验证门控在本项目 CI 中从不运行。
- lint 须通过：`ruff check src scripts tests`（CI 强制）。

## CI 门控（阻断性）

1. `ruff check src scripts tests` 通过。
2. 全部单元与属性测试在 Linux、Windows、macOS（Python 3.11+）通过。该矩阵安装 `.[dev]`：引擎已没有可供跳过的图形扩展组。
3. `prokname bench`（回归种子）通过。
4. `prokname bench --full`（门禁式基准）退出码为 0。
5. `prokname holdout`（holdout 完整性）通过。
6. `python scripts/verify_examples.py`（M0 示例校验）通过。
7. wheel 构建与干净 venv 安装冒烟通过（包数据完整）。
8. 基准结果相对基线退化超过 2 pp 时，阻断合并。

每周的 `live-smoke` 作业单独调度，目前用 `continue-on-error` 与 `|| true` 包裹，因此它**无法**让 CI 变红。请把它的输出当作漂移报告，而不是门禁。
