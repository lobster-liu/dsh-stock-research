# dsh-invest-research

[![npm version](https://img.shields.io/npm/v/dsh-invest-research.svg?style=flat-square)](https://www.npmjs.com/package/dsh-invest-research)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

> Multi-agent equity & fund research pipeline **skill plugin** for [DeepSeek Harness (DSH)](https://harness.deepseek.com).
> 同一包内提供**11 个 skill**，共用脱敏门禁与环境约定：
>
> | 类别 | skill | 用途 |
> |---|---|---|
> | 个股 | `stock-research` | 单只 A 股行情/估值/周期/政策分析 | 6 角色（协调员/数据员/政策员/估值周期员/风控员/审计员），双盲隔离 + 三轮审计 |
> | 基金重研究 | `fund-research` | **基金穿透式研究**（主动/指数/ETF/联接/FOF/QDII/债基）+ **可选同业对比层** | 7 角色 + 对比层 3 角色（peer-orchestrator/peer-builder/peer-red-team），五阶段 + 阶段 6 对比 |
> | 基金轻工作流 | `pick-funds`（入口）· `fund-screen` · `fund-profile` · `fund-watch` · `holdings-style` · `manager-profile` · `report-render` · `xlsx-author` | 筛选 / 单基金画像 / 经理画像 / 持仓风格 / 持续跟踪 / PDF·DOCX·XLSX 交付 | 单 agent 按技能逐步执行，产出带 `[披露]/[测算]/…` 溯源标签的交付物 |
> | 决策闭环 | `decision-loop` | 登记结论 → 对齐已发生行情 → 结算 → 复盘 → 决策质量统计 | 3 角色（结算员/复盘员/质量分析师），只增决策日志 + 按标签聚合命中率 |
>
> 最终都产出**每个数字带【来源|日期|口径|类型】、落盘前过 PII 脱敏**的研究报告。
>
> **v0.6.0 起新增时点纪律（point-in-time）**：所有取数脚本支持 `--as-of`，
> 历史序列一律截断到分析基准日，披露日期与数据日期分列 —— 详见下方「时点纪律」。

**仅供研究参考,不构成投资建议。不接入任何交易通道,全程只读公开数据。**

## Install 安装

```bash
# 从 GitHub
dsh plugin --profile <profile-name> add github:lobster-liu/dsh-invest-research

# 或从本地目录 / zip 解包后
dsh plugin --profile <profile-name> add path/to/dsh-invest-research
```

> **v0.3.0 起包名由 `dsh-stock-research` 改为 `dsh-invest-research`**（加入了基金研究能力）。
> GitHub 旧地址会自动重定向；已安装旧版的用户请移除后按新名重装。
> **v0.4.0 起 `fund-research` 新增可选同业对比层**（阶段 6）：同一主题下多只基金"谁更优"，
> 由对比协调员亲自派发互相隔离的独立角色 agent 做跨基金对比 + 独立 red-team 质疑。

重启 DSH → 新会话里说 **"分析 600048"**（stock-research）或 **"分析基金 025490"**、**"穿透这只基金的持仓"**（fund-research）即触发；说 **"筛选基金"**、**"这只基金怎么样"**、**"基金经理画像"**、**"出一份 PDF 报告"** 则命中 pick-funds 套件。

> **安装约束（v0.5.5 起）**：`fund-research/tools/fetch_fund.py` 的 `_resolve_version()` 依赖插件根
> **同时存在 `assets/` 子目录与 `package.json`**（`name=="dsh-invest-research"`）来读真实版本；
> **只拷贝 `skills/` 子目录**（没有插件根清单）时，版本会回落到脚本内的 `_VERSION_FALLBACK` 常量——
> 属预期行为、不影响取数，但会让「防人肉抬值脱节」的同步守卫不生效（离线自测会打 `⚠️ SKIP` 显式告警）。
> 手动拆分安装请连同插件根一起拷，或直接用 `dsh plugin add`。

> **安装约束（v0.6.3 起，安全相关）**：`fund-research` 的落盘门禁与 `decision-loop` 的
> 台账/结算门禁都**硬依赖同级的 `stock-research/tools/sanitize_check.py`**（同一文件、
> 不复制规则）。两个 skill **必须同级同装**：
> - 只装 `fund-research` 一个 skill 时，`decision_log.py append` / `settle.py` 拿不到
>   `sanitize_check.py`，会返回 `sanitize_missing` 并**拒绝写入**（失败关闭）；
> - 这是**安全正确的行为**，不是 bug —— 门禁缺失时宁可写不进，也不静默放行。
> 需要把 skill 单独拆走时，请连同 `stock-research/tools/sanitize_check.py` 一起拷，
> 否则决策闭环会整条不可用。

## Quick check 安装自检

```bash
# 1) 个股取数冒烟(需可访问 push2.eastmoney.com;失败多为网络环境而非代码问题)
python assets/skills/stock-research/tools/fetch_data.py --code 600000 --out /tmp/sr/data.json
# 2) 基金取数冒烟(需可访问 fund.eastmoney.com)
python assets/skills/fund-research/tools/fetch_fund.py --code 025490 --out /tmp/fr/fund.json --peer-stocks 10
# 3) 基金取数器离线自测(38 条断言,不依赖网络,期望 exit 0)
python assets/skills/fund-research/tools/test_fund_tools.py
# 4) 脱敏门禁行为自检(69 场景,期望 exit 0)
python assets/skills/stock-research/tools/test_gate.py
# 5) 对比层工具离线自测(24 条断言,期望 exit 0;含 ETF/联接份额、短码补零、--track 仍解析锚点名、关键词截断、https 回退、列表外代码不误拒、标签锚定解析)
python assets/skills/fund-research/tools/test_peer_tools.py
# 6) 时点纪律与决策闭环离线自测(77 条断言,期望 exit 0)
python assets/skills/fund-research/tools/test_decision_tools.py
# 统一入口:npm test 跑 30 条注册冒烟;npm run test:all 跑全部(30+69+38+24+77)
```

## 时点纪律 Point-in-Time（v0.6.0 新增）

此前的实现只做了「披露滞后标注」（报告期 vs 检索于、stale 标记），**没有做时点纪律**：
历史序列一律取到「当下」，财报一律取「最新版」。于是一份声称基准日为 2026-06-30 的报告，
只要用了这些工具产出的数据，就会静默地把 7—10 月的行情、后来重述的财报、
以及基准日之后才披露的持仓算进结论 —— 典型的**前视偏差**。

v0.6.0 补齐三件事：

| 机制 | 实现 | 作用 |
|---|---|---|
| **序列按基准日截断** | `fetch_data.py --as-of` / `fetch_fund.py --as-of` | 历史行情/净值/指数序列只取 `日期 ≤ 基准日`；基准日之后的数据**不进入任何计算窗口**（含分位、回撤、收益） |
| **披露日期与数据日期分列** | 记录新增 `披露日期` 字段 + `pit.py` | `数据日期`=统计截止，`披露日期`=公众首次可读之日；取不到记「未取得」，**不得以数据日期冒充**（重述靠这个字段暴露） |
| **可见性判定** | `pit.py` 的 `AsOf.visible()` | 报告期（或其推算最早公开日）晚于基准日 → 该期**不得使用**，退回上一可见期并在「数据缺口」写明 |

披露日期用**监管最晚披露时限**推算（季报 25 日 / 半年报 60 日 / 年报 90 日），
是刻意的**保守上界** —— 实际披露通常更早，用最晚时限意味着宁可认为数据更晚可见，
也不误判为「基准日已可见」。推算值在备注里标明「推算」，不得当作实际披露日期。

```powershell
# 按基准日取数（--as-of 不可省）
python assets/skills/stock-research/tools/fetch_data.py --code 600000 --out /tmp/sr/data.json --as-of 2026-06-30
python assets/skills/fund-research/tools/fetch_fund.py --code 025490 --out /tmp/fr/fund.json --as-of 2026-06-30
```

## 决策闭环 Decision Loop（v0.6.0 新增）

一次运行只给出一个决策，**无法说明这套方法决策得好不好**。v0.6.0 把它变成一个闭环：

```
研究 → decision.json → 决策日志（只增追加）→ 结算（对齐已发生行情）→ 复盘 → 下次研究可读
```

| 组成 | 作用 |
|---|---|
| `tools/rating.py` | **唯一权威的结论标签词表**（5 档）+ `REVIEW` 哨兵 + 锚定解析器（容忍 Markdown 加粗与全角标点） |
| `tools/decision_log.py` | 只增追加的决策日志（`ledger.jsonl`）；按 `run_id` 幂等去重；`verify` 校验 |
| `tools/settle.py` | 结算：入场取决策日**之后**第一个收盘日，算原始收益 / 基准同期 / 超额，判 `SETTLED`/`PENDING`/`SETTLE_FAILED` |
| `tools/decision_score.py` | 按标签聚合决策质量：n / 平均超额 / 命中率 + **前视偏差说明**（按词表版本分组，跨版本不合并） |
| `assets/skills/decision-loop/` | 3 角色（结算员 / 复盘员 / 质量分析师）+ 完整流程 |

三个关键口径：

1. **`REVIEW` 哨兵** —— 解析不出结论标签时返回 `REVIEW`，**绝不**静默退化成「等待验证」。
   一个读不懂的决策不是中性决策，把它记成中性标签等于伪造了一个从未作出的判断。
   （v0.6.0 顺带修掉了 `comparator.py` 里 `if l in md` 全文档子串扫描的真实缺陷：
   它会把正文里讨论到的他人结论当成本次结论，且失败时静默返回空串。）
2. **命中率只对方向性标签计算** —— `优先研究`/`可以关注`/`暂不优先` 宣称了方向；
   `等待验证`/`数据不足`/`REVIEW` 不宣称方向，参与统计会稀释结果，
   它们的样本数单独列出。
3. **前视偏差必须披露** —— 结论标签词表与阈值会随版本变化，把今天的词表回填到历史决策
   会凭空制造「当年就会做出这个判断」的效果，因此按 `vocabulary_version` 分组统计，
   跨版本**不合并**。

```powershell
# 登记本次决策（只增追加）
python assets/skills/fund-research/tools/decision_log.py append --ledger invest-decisions/ledger.jsonl --record $RUN/decision.json
# 结算 + 回测
python assets/skills/fund-research/tools/settle.py --ledger invest-decisions/ledger.jsonl --series series.json --benchmark bench.json --out $RUN/settlements.json
python assets/skills/fund-research/tools/decision_score.py --ledger invest-decisions/ledger.jsonl --settlements $RUN/settlements.json --out $RUN/decision-quality.md
```

> **边界**：历史决策与复盘里的数字**不得**写进新报告的数据表 ——
> 它只提供「先验教训」与「上次结论是否被证伪」。`fund.json` 仍是唯一事实底稿。

## What's inside

| 组成 | 说明 |
|---|---|
| `assets/skills/stock-research/SKILL.md` | 个股流程总纲 + 环境解析(`EQUITY_PY` / `EQUITY_SECRET_SCANNER` / `$RUN` / `$ARCHIVE`) |
| `assets/skills/stock-research/roles/*.md` ×6 | 各角色完整提示词(含 2026-10-15 真实事故教训固化:单一数据源、口径并列、先落盘再细化) |
| `assets/skills/stock-research/tools/fetch_data.py` | 东财双通道行情/历史取数 + 分位/最大回撤衍生计算;**空数据退出码 1**;**(v0.6.0)** 支持 `--as-of` 基准日截断,记录带 `披露日期` |
| `assets/skills/stock-research/tools/pit.py` | **(v0.6.0)** 时点纪律底座:披露日期推算(季报 25/半年报 60/年报 90 日,保守上界)、可见性判定、序列截断;`stock-research` 与 `fund-research` 共用 |
| `assets/skills/stock-research/tools/fetch_fundamentals.py` | **(v0.2.0)** 可选 akshare 财务基本面通道:ROE/每股净资产/负债率/现金流 + PE·PB 近一年真分位;未装 akshare 时退出码 1 不阻断主流程 |
| `install_akshare.ps1` / `requirements.txt` | **(v0.2.0)** 沙箱内离线装 akshare(Node fetch 下 wheel + `--no-index --find-links`),绕开 pip 平台临时目录 `WinError 5` 与 schannel 两个坑 |
| `assets/skills/fund-research/SKILL.md` | **(v0.3.0 / v0.4.0)** 基金流程总纲:五阶段 + 阶段6 对比层、10 章报告、5 个结论标签、三种情景、跟踪清单、条件式行动、铁律 11(对比层必由独立 agent 派发) |
| `assets/skills/fund-research/roles/*.md` ×10 | **(v0.3.0 / v0.4.0)** 协调员 + Agent1–Agent6 + 对比层 3 角色(`peer-orchestrator` / `peer-builder` / `peer-red-team`),统一提交格式 |
| `assets/skills/fund-research/tools/fetch_fund.py` | **(v0.3.0 / 版本自 v0.5.5 起从本插件根 `package.json` 读,依赖插件根 `assets/` + `package.json` 同时存在;回落值 `_VERSION_FALLBACK` 有格式 + 同步双重测试断言)** 基金身份/净值/多期持仓/行业配置/债券持仓/规模份额/持有人/资产配置/经理/基准对比/重仓股快照;多通道降级,**空数据退出码 1** |
| `assets/skills/fund-research/tools/test_fund_tools.py` | **(v0.3.0)** 38 条离线断言(内联 fixture + 网络桩,零网络;`_pkg_version()` 跨仓库/Qoder 安装形态可跑,无清单时打印 `⚠️ SKIP` 显式告警) |
| `assets/skills/fund-research/tools/peer_builder.py` | **(v0.5.5)** 同业筛选器:**脚本执行 3 条**(跟踪标的严格相等 / 仅被动型 / 非同一基金不同份额)+ **标记 1 条**(结构差异);**交易状态与份额类别一致性不执行**,写入 `agent_review_required[]` 交 Agent-P1 人工复核,**只筛不评**;候选支持 `--codes`(列表外代码先抓身份页兜底,类型仍未知时记 `type_unknown_codes[]` + `agent_review_required(type_unverified)`,不按非被动型误拒)/ `--keyword`(被动型预筛 + `--max-candidates>=1` 上限)/ `--track`(仍解析锚点简称,不静默关掉判据 C);基金列表源 https 优先、**抓取与解析失败都回退 http** |
| `assets/skills/fund-research/tools/comparator.py` | **(v0.4.0)** 对比矩阵生成器:读 `peers.json` + 各基金 `report.md`/`fund.json`,对齐已冻结结论、暴露口径冲突,**不重算指标**;**(v0.6.0)** 结论标签改走 `rating.py` 锚定解析器(修复全文档子串扫描缺陷),`REVIEW` 单独统计 |
| `assets/skills/fund-research/tools/rating.py` | **(v0.6.0)** 结论标签**唯一权威词表**(5 档)+ `REVIEW` 哨兵 + NFKC/加粗容忍的锚定解析器 + 方向性标签集合 |
| `assets/skills/fund-research/tools/decision_log.py` | **(v0.6.0 / v0.6.3 起 append 内建落盘脱敏门禁)** 只增追加的决策日志(`ledger.jsonl`):按 `run_id` 幂等去重、损坏行不阻断读取但计数上报、`verify` 校验;**追加前经 `sanitize_gate` 跑 `sanitize_check.py`,命中 CRITICAL/HIGH 直接拒绝写入(无绕过开关)** |
| `assets/skills/fund-research/tools/settle.py` | **(v0.6.0;v0.6.4 起写盘前内建落盘脱敏门禁)** 决策结算:入场取决策日之后第一个收盘日,算原始/基准/超额,判 `SETTLED`/`PENDING`/`SETTLE_FAILED`;**写 `settlements.json` 前经 `sanitize_gate` 跑门禁,命中即拒写并返回 1(失败关闭)** |
| `assets/skills/fund-research/tools/sanitize_gate.py` | **(v0.6.4)** 落盘门禁公共件:`decision_log.py` 与 `settle.py` **共用**同一份规则与失败关闭语义(`sanitize_record` / `sanitize_text` / `is_blocked`),避免两处各写一遍导致口径漂移;门禁脚本缺失返回 `missing` |
| `assets/skills/fund-research/tools/decision_score.py` | **(v0.6.0)** 决策质量回测:按标签聚合 n/平均超额/命中率(仅方向性标签),未结算单元显式列出,附**前视偏差说明** |
| `assets/skills/fund-research/tools/test_decision_tools.py` | **(v0.6.0 / v0.6.3 +5 条 / v0.6.4 +8 条)** 77 条离线断言(时点纪律 + 标签解析 + 日志幂等 + **落盘脱敏门禁(含 settle 拒写)** + 结算口径 + 命中率口径;零网络) |
| `assets/skills/decision-loop/SKILL.md` + `roles/*.md` ×3 | **(v0.6.0 / v0.6.3 起过脱敏门禁)** 决策闭环技能:登记 → 结算 → 复盘 → 质量统计;**阶段 1/2 由代码强制(decision_log.py / settle.py 内置),阶段 3/4 由角色自查**;**账本只存公开结论,不含任何个人头寸** |
| `assets/skills/fund-research/tools/test_peer_tools.py` | **(v0.5.4)** 对比层 24 条离线断言(含 `某ETF` 不得判成 F 份额、`11839→011839` 补零、`--track` 不得让判据 C 失效、`--keyword` 预筛+截断+下限、https 连接层/解析层回退、列表外代码不得按非被动型误拒、标签锚定解析七项回归;纯逻辑 + 工作区临时目录 fixture,零网络) |
| `tools/sanitize_check.py` | 落盘前 PII 门禁:泄漏规则(持仓/成本/资金/盈亏/账户 + **v0.6.4 起 `personal_amount`:自持动词/第一人称所有格/第一人称+自持动词 + 金额的组合规则**) + 5 层公开信息白名单(财报科目/政策条款/金融术语/**基金公开披露**/**机构主语 + 持仓动词/资金名词**/否定声明);混合陷阱必拦、政策豁免不跨句 |
| `tools/test_gate.py` | 门禁行为自检 **69 场景**(真泄漏必拦(含第一人称散文绕过 + 观点句里的个人头寸一律照拦)/公开条款必放(含观点句里的公开口径)/基金披露必放/机构主语必放(personal_amount + capital_amount)/公司自有资金必放/机构自述「我方」必放/陷阱必拦) |
| `tools/run_pipeline.ps1` | Windows PowerShell 引导脚本(ASCII-only,PS 5.1 兼容) |

## pick-funds 分析师套件（v0.5.0 新增）

移植自 ZCode 官方插件 **pick-funds v0.1.1**，把原「1 agent + 7 skills」改为纯 skill 承载，
入口技能 `pick-funds` 承载工作模式路由、数据源优先级与共享护栏。

| 技能 | 触发场景 |
|---|---|
| `pick-funds` | 基金研究类任务入口；共享护栏 + **DSH 数据源映射** |
| `fund-screen` | 筛选基金 / 找基金 / 近一年收益前 10% |
| `fund-profile` | 基金画像 / XX 基金怎么样 |
| `fund-watch` | 基金跟踪 / 持仓基金有没有问题 / 规模异动 |
| `holdings-style` | 持仓分析 / 风格漂移 / 两只基金重合度 |
| `manager-profile` | 基金经理 / 经理画像 / 这个经理靠谱吗 |
| `report-render` | 出一份 PDF / 导出报告 / 存成 docx |
| `xlsx-author` | 表格类交付落成 .xlsx |

### 与 `fund-research` 的分工

要「筛 / 画像 / 盯 / 比 / 出报告」用本套件（单 agent 工作流）；要「穿透底层 + 多 agent
交叉验证 + 固定 10 章」用 `fund-research`。两者都只读公开数据、不出具投资建议。

### DSH 数据源映射

本套件按 DSH 原生方式接线：**无 MCP server 依赖**，直接走本插件自带取数脚本 + Web 检索兜底。

| 需要的数据 | DSH 取法 |
|---|---|
| 基金档案/净值/持仓/规模/份额/持有人/经理/费率/基准/重仓股 | 本插件 `fetch_fund.py`（`数据` / `持仓` / `净值序列` 等字段） |
| 指数/个股行情、板块、财务基本面 | 本插件 `fetch_data.py` + `web_fetch` |
| 基金经理变更公告、基金公司事件 | `web_search` + `web_fetch`（基金公司公告 / 巨潮 / 交易所披露页） |
| 定性背景（经理访谈、基金公司事件） | `web_search` + `web_fetch`（媒体档标 `[媒体]`） |
| 基金筛选（全市场按条件过滤） | **无等价脚本** → `web_search` + `web_fetch`（天天基金等筛选页），须在覆盖度写明引擎口径 |

没有对应能力时记 `源不可用` 并写入覆盖度区块，**不得静默跳到通用搜索兜底**。
`fetch_fund.py` 退出码 `1` 表示基金身份与净值都取不到，此时停止并如实报告，不带空数据继续。

### 依赖边界

- `report-render` 另需 `reportlab / matplotlib / pypdf / pypdfium2 / pillow / fonttools / pymupdf`
  （见 `assets/skills/report-render/scripts/requirements.txt`；`pymupdf` 为可选项，缺则字形覆盖
  检查自动跳过）与系统 CJK 字体；
- `xlsx-author` 需 `openpyxl`，公式重算校验走 headless LibreOffice（缺失时用技能定义的强制
  替代检查并如实标注，不假装通过）；
- `peer_builder.py` 基金列表源为 `https://fund.eastmoney.com/js/fundcode_search.js`，
  个别网络只放行 http，故**保留 `http://` 回退**（设计取舍，非缺陷）；连接层或解析层失败都会触发回退。
- 两者脚本均为纯 Python，随技能原样携带，无平台耦合。

### report-render / xlsx-author 完整度
本仓库技能**只要求 `soffice` 在 PATH 上**（探测用 `shutil.which("soffice")`）。缺失时
docx 排版量测 / xlsx 公式重算**如实降级**为"未测量 / 未经视觉验收 / recalc_unavailable"，
**不假装通过**。

```powershell
# 1) Python 依赖(dsh 自带 python 或 $PY) —— 已装并验证:
& "$PY" -m pip install -r assets/skills/report-render/scripts/requirements.txt
#    reportlab / matplotlib / pypdf / pypdfium2 / pillow / fonttools / pymupdf
#    → PDF 直出、图表、墨量/空白页校验、CJK 字形覆盖全部可用(无需 LibreOffice)

# 2) LibreOffice(仅 docx 排版量测 + xlsx 公式重算需要)。无管理员时用「管理镜像」解包:
#    winget 不可用的环境示例 —— 官方 mirror 下载 Win_x86-64 MSI 后:
#    msiexec /a "LibreOffice_X.Y.Z.msi" /qn TARGETDIR="D:\Users\...\LibreOffice"
#    !!! 坑:解包后 program\bootstrap.ini 里 InstallMode=<installmode>(未替换占位符)
#        会报"配置文件已损坏";改成 InstallMode=1 后 soffice.COM 通常可用,soffice.exe 不一定。
#        若仍报错,请用管理员权限正常安装,不要沿用管理镜像。
#    然后把 <target>\program 加入用户 PATH。

# 3) 自检:python assets/skills/report-render/scripts/selftest.py <输出目录>
#    PDF 半程应全过;docx 排版量测有无取决于步骤 2。
```
> 本机现状（2026-10-25）：Python 依赖已装齐；LibreOffice 三种安装方式（`winget` / 用户级 MSI /
> 管理镜像）均未稳定成功，已从用户 PATH 移除，docx/xlsx 校验走降级路径。**这不影响 PDF 交付
> 与数据正确性，只影响 docx 逐页视觉验收和公式重算校验两项。**

## Requirements
- DSH ≥ 0.2.0-rc(skill 注册走 Host `skills` 服务;**无该服务时插件加载为 no-op,不会报错**)
- Python 3.10+(DSH 自带 runtime 直接可用;取数零第三方依赖,akshare 可选)
- 网络可达 `push2.eastmoney.com` / `push2his.eastmoney.com`(行情);政策核对建议搭配 [`dsh-web-search-pro`](https://github.com/anweat/dsh-web-search-pro)(免费 anonymous 源,本插件按 `read.fetch`-为主设计)

## 环境变量(全部可选)
| 变量 | 作用 |
|---|---|
| `EQUITY_PY` | 指定 Python 解释器(默认自动解析 DSH 自带 runtime) |
| `EQUITY_SECRET_SCANNER` | 外接凭据扫描器路径(如 audit-secrets.py);未设则明示 skipped,内置 PII 规则照常生效 |
| `EQUITY_WORK` | run 产物根目录(默认当前工作目录 `./equity-runs/`) |

## Privacy
- 报告**禁止出现**个人持仓/成本/资金量——落盘前 `sanitize_check.py` 强制拦截(退出码非 0 禁止写入,无绕过开关);
- 政策原文中的条款数字(如"单户上限100万元")是公开信息,白名单放行且**不改写**;
- 所有产物默认留在用户工作区,是否归档进个人笔记库(Vault/Obsidian)由用户显式配置 `$ARCHIVE`。

## License
MIT
