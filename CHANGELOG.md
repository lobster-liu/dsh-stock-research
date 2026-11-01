# Changelog

## 0.6.6 — 2026-11-01
**上游复核 v0.6.5 后的 2 项：观点句豁免经承重测试证伪后整体撤除；`capital_amount` 补机构主语豁免。**

### 一、观点句豁免（`_is_opinion` / `OPINION_VERBS`）整体撤除
- **承重测试**：把 `OPINION_VERBS` 在内存里换成永不匹配（不改文件），再跑 `test_gate.py` 的 64 条：
  63 条照常通过，唯一翻转的是「我认为我的份额 3 万元」（PASS → FALSE-BLOCK）。
  即 v0.6.5 要修的观点句误伤，已被新的 A/B1/B2 锚点方案**顺带修好**；豁免在自检里
  唯一「撑住」的那条，正是测试载体（用「份额」验证 personal_amount 路径），不是语义决策。
- **残留（比 v0.6.5 自报的更宽）**：观点句豁免只认 B1 所有格，以下 4 个名词 + 金额在观点句里放行：
  `我认为我的份额 3 万元` / `我认为我的股份 3 万元` / `我认为我的出资 3 万元` / `我认为我的资产 3 万元`。
  - 非观点句下 10 个 B1 名词全部被拦；`我的仓位/本金/资金/投入` 因同时命中 `capital_amount` 也一律被拦。
  - 金额单位不限（`万元/亿元/元/块/万` 都漏）；`万份/万股` 也漏（`万` 命中单位、尾随的 `份/股` 被忽略）。
  - 观点动词只要出现在句首即可：「我认为这个基金我的份额 3 万元」也漏。
  - **会落到决策账本**：`decision_log.py append` 收「我认为我的份额 3 万元」→ `written=true` 直接落盘；
    收「我持有 3 万元」→ `sanitize_blocked:personal_amount`。不是观感问题，是真实落盘路径。
- → **整体撤除 B1 的观点句豁免**（采纳上游建议 A）：删除 `OPINION_VERBS`、`_is_opinion()`、
  `_SELF_HOLDING_NOUNS`、`_ANCHOR_B1` 与 `scan_text` 里的调用。新锚点已修好误伤，撤除后 63/64 仍通过；
  「我认为我的份额 3 万元」从 MUST_PASS 挪到 MUST_BLOCK。
- → 「我的份额/我的股份/我的出资/我的资产」在观点句里**一律照拦**（新增 4 条 MUST_BLOCK 守卫）。

### 二、`capital_amount` 补机构主语紧邻豁免（口径不对称，v0.6.5 既有缺陷）
- **现象**：「我方」只进了 `personal_amount` 的机构豁免，`capital_amount` 没有：
  `我方持有 3.39 亿份` → PASS / `我方持仓 3 亿元` → PASS / `我方资金 3 亿元` → BLOCK（capital_amount）/
  `我方本金/仓位/投入 3 亿元` → BLOCK。
- → `scan_text` 的机构主语豁免改为对 `personal_amount`（仅 A 类）与 `capital_amount` **同时**生效，
  仍只做紧邻 ≤4 非数字字符的局部豁免，绝不整行豁免。
- → 新增守卫：`我方资金 3 亿元。` / `我方本金 3 亿元。` / `我方仓位 3 亿元。` /
  `我方投入 3 亿元。` / `公司总资产 3 亿元。` → PASS。

### 测试
- → `test_gate.py` 64 → **69 场景**：+1 条观点句 B1 所有格守卫（份额，由 PASS 挪 BLOCK）、
  +5 条 `capital_amount` 机构主语守卫。
- → 全部套件：`npm test` **30/30**、`test_gate` **69/69**、`test_fund_tools` **38/38**、
  `test_peer_tools` **24/24**、`test_decision_tools` **77/77**（合计 238）。

## 0.6.5 — 2026-10-31
**上游复核 v0.6.4 后新发现的 3 项（1 项 P2 + 2 项 P3），全部围绕 `personal_amount` 规则。**

### P2（安全面）观点句被误判为个人头寸：注释承诺的 `_is_opinion` 从未实现
- **现象**：`sanitize_check.py` 的注释写「观点句（我认为规模…）由 OPINION_VERBS 豁免（见 _is_opinion）」，
  但整个文件里 `grep` 不到 `OPINION_VERBS`、也 `grep` 不到 `_is_opinion` —— 只有那行注释本身。
  豁免「写了但没做」，观点句直接被判 CRITICAL（`scan_text` 实测）：
  - `我认为规模 3 亿元值得配置。` → `personal_amount`
  - `我按 3 元净值申购了 1000 份。` → `personal_amount`
  两条都是公开口径（基金规模、净值），不是个人头寸。
- → **补齐 `OPINION_VERBS` + `_is_opinion()`**：句首是观点动词
  （`我认为/我觉得/我看/我判断/我预计/我建议/我推测/我想/我估计`）且命中是**第一人称所有格**
  （`我的持仓/我的仓位/…`）时放行。
- ⚠️ **只对第一人称所有格（B1）生效，且 B1 里与自持动词同名的名词不豁免**：
  `持有/持仓/投了/买了/买入/加仓/建仓/头寸` 这些**真自持动词一律照拦** ——
  「我认为我持有 3 万元」「我认为我的持仓 3 万元」仍 BLOCK（新增正向守卫）。
  「我的份额/我的股份」这类非自持名词在观点句下放行（上游规则：个人锚点这一路不生效）。

### P3（规则稳定性）`personal_amount` 判定对「我」与金额的间隔长度敏感
- **现象**：原规则要求锚点与金额之间只隔 ≤6 个非断句非数字字符，于是**同一句式加两个字就翻面**：
  - `我认为规模 3 亿元`（间隔 4 字）→ BLOCK / `我认为该基金规模 3 亿元`（间隔 7 字）→ PASS
  - `我按 3 元净值申购`（间隔 2 字）→ BLOCK / `我算下来持仓市值 3 亿元`（间隔 8 字）→ PASS
  判定依赖作者用词长度，规则不稳。
- → **采用上游方案②**：**去掉裸「我」锚点**，锚点必须**自带自持语义**：
  - **A 类**：无主语强自持动词（`已持有/已持仓/持有/持仓/投了/投进/买了/买入/加仓/建仓/头寸/自有`）；
  - **B1 类**：第一人称 + 的 + 所有格名词（`我的持仓/我的仓位/我的本金/我的份额/我的资金/我的股份/我的投入/我的出资/我的资产`）；
  - **B2 类**：第一人称 + **紧邻**自持动词（`我持有/我买了/我加仓/我押了/我掏了/我配置了/我打算投/我加码/我拿/我放了`）。
  间隔只影响同一锚点下的宽松度，不再决定「是不是个人头寸」。
- → **裸「我」取消后仍全部兜住**：`我押了/我掏了/我配置了/我打算投/我放了/我加码/我拿`
  改由 B2 的紧邻动词命中（新增 7 条 MUST_BLOCK 正向守卫）。
- → **机构主语豁免改为按锚点分类**：只对 A 类生效；B1/B2 本身就是个人化表达，不走机构豁免。
- → 结果一致性：上述 4 条间隔用例现在**全部 PASS**（间隔 4/7/2/8 字不再翻面）。

### P3（补充）机构自述「我方」被个人锚点误拦
- **现象**：`我方持有 3.39 亿份` → BLOCK `personal_amount`。锚点表里有「我方」，
  而机构主语豁免表不含「我方」。
- → **「我方」从个人锚点移除，并加入 `INST_SUBJECT_BEFORE`**（机构自述口径）。
  `我方持有 3.39 亿份。` / `我方持仓 3 亿元。` → PASS（新增 2 条误伤守卫）。

### 测试
- → `test_gate.py` 43 → **64 场景**：+2 条观点句误伤守卫、+4 条间隔稳定性守卫、
  +2 条「我方」守卫、+1 条 `_is_opinion` 所有格守卫、+7 条 B2 自持动词守卫、
  +3 条「观点句不得豁免真自持动词」守卫、+2 条「B1 自持名词不豁免」守卫。
- → 全部套件：`npm test` **30/30**、`test_gate` **64/64**、`test_fund_tools` **38/38**、
  `test_peer_tools` **24/24**、`test_decision_tools` **77/77**（合计 233）。

## 0.6.4 — 2026-10-30
**上游复核 v0.6.3 后新发现的 2 项（1 项 P2 + 1 项 P3）。**

### P2（安全面）第一人称资金量散文绕过 capital_amount / holding_qty
- **现象**：脱敏规则是**关键词锚定**的 —— 锚点表只有「资金/本金/仓位/投入」（`capital_amount`）
  与「持仓/持有/买入/卖出/加仓/减仓/清仓」（`holding_qty`）。以下 6 条写法**不进锚点表**，
  原门禁整条放行（实测 6 条全 PASS，应 BLOCK）：
  `已持有 3 万元` / `头寸 3 万元` / `投了 5 万元` / `我买了 3 万元的这只基金` /
  `自有 15 万元` / `我持有 3 万元`。
- **对照组**（证明规则是活的）：`我的仓位是 3 万元` → `capital_amount`、
  `资金量 3 万元` → `capital_amount`、`我持仓 12000 股，投入资金 10 万元` →
  `holding_qty` + `capital_amount`。
- → **新增 `personal_amount` 组合规则（CRITICAL）**：锚点改为
  **第一人称标记 / 自持动词**（`我自己/自己/本人/自有/我方/我` +
  `已持有/已持仓/持有/持仓/投了/投进/买了/买入/加仓/建仓/头寸`），且与金额之间
  只隔 **≤6 个非断句、非数字字符**，金额仍需 `万/亿/元/块/W` 单位（沿用现有货币单位约束）。
- ⚠️ **刻意不加「金额 + 元/万元」裸规则**（上游明确反对）：会误伤基金披露份额、
  政策条款数字 —— 白名单只有紧邻 ≤24 字符的局部豁免，扛不住整行/跨句误伤。
  新规则要求「个人标记 + 金额」**同时**出现，公开披露没有个人标记，不会被误伤。
- → **机构主语豁免**：`持有/持仓/头寸` 这类机构与个人通用的动词，若锚点紧邻前文 ≤4 字符
  出现机构主语（`本基金/该基金/基金/机构/公司/产品/组合/资管/理财/信托/年金/社保/集合计划/
  专户/管理人/托管/保险资金/保险/北向资金/主力/QFII/外资/银行/券商`），判为公开披露放行。
  **只做紧邻局部豁免，绝不做整行豁免**（否则「基金规模扩大。同时我持有 3 万元」会被放过）。
- → **公司财务口径白名单**：`自有资金 / 自有资产 / 自有资本 / 自持 / 自营资金 / 自营`
  加入 `FIN_TERM_SAFE`，避免个人锚点「自有」误伤公司财报披露。
- → **修复 `_is_policy_term` 跨句豁免缺陷**：原实现用 `line[:match_start][-24:]` 取前文，
  不截断到当前句 —— 「基金规模扩大。同时我持有 3 万元」里上一句的「规模」会被当成
  政策豁免词，把第一人称头寸一起放行。→ 改为**先按 `。；;！!？?\n` 断句取当前句**，
  再在句内取 ≤24 字符窗口。
- → `test_gate.py` 27 → **43 场景**：6 条原绕过转正向对照（MUST_BLOCK）、
  3 条关键词锚点写法留作回归守卫、5 条机构披露 + 1 条公司自有资金作误伤守卫、
  1 条「机构披露掩护第一人称」混合陷阱。

### P3（如实标注 + 方案②）门禁文档描述得比代码实际覆盖范围宽
- **现象**：`decision-loop/SKILL.md` 称阶段 1–4 四个落点「全部强制过脱敏门禁」，
  但代码里**只有 `decision_log.py` 有脱敏逻辑**；`settle.py` / `decision_score.py`
  没有（`grep sanitize` = 0 hits）。文档比代码宽。
- → **方案②（上游倾向，更彻底）**：把 `sanitize_record()` 提成同目录**公共件**
  `fund-research/tools/sanitize_gate.py`，暴露 `sanitize_record` / `sanitize_text` /
  `is_blocked`；`decision_log.py` 与 `settle.py` **共用同一份规则与同一套失败关闭语义**，
  杜绝两处各写一遍导致口径漂移。
  - `settle.py` 写 `settlements.json` 前跑门禁，命中 CRITICAL/HIGH **拒绝写入并返回 1**；
    门禁脚本缺失同样拒绝写入（失败关闭）。
- → **如实标注机器强制范围**：`decision-loop/SKILL.md` 门禁小节改为**分阶段表**：
  - 阶段 1 `ledger.jsonl`（`decision_log.py append`）→ **代码强制**；
  - 阶段 2 `settlements.json`（`settle.py`）→ **代码强制**（v0.6.4 起）；
  - 阶段 3 `reflections.jsonl`（角色写）→ **角色自查**（代码不拦，漏跑即真泄漏）；
  - 阶段 4 `decision-quality.md`（`decision_score.py`）→ **角色自查**。
- → `roles/settlement.md` 铁律 8 改写为「`settle.py` 已内置门禁，命中即拒写并返回 1」。
- → `test_decision_tools.py` 69 → **77 断言**：断言 `decision_log.sanitize_record` 与
  `sanitize_gate.sanitize_record` **是同一个对象**（口径不漂移）；
  `settle.py` 对「标签字段被写成第一人称散文」的记录返回 1 且**不落盘**，
  合法标签照常落盘。

## 0.6.3 — 2026-10-30
**上游复核 v0.6.2 的 7 项反馈（1 项 P1 + 1 项 P2 + 4 项 P3 + 1 项开放问题）。**

### P1（可能阻断）pick-funds 套件 8 个技能 frontmatter 的 `tags:` 缩进 4 空格
- 8 个 `SKILL.md`（`pick-funds` / `fund-profile` / `fund-screen` / `fund-watch` /
  `holdings-style` / `manager-profile` / `report-render` / `xlsx-author`）第 7 行的 `tags:`
  被缩进 4 空格：严格 YAML 解析抛 `ScannerError`；宽松解析下 `tags` 被并入 `author` 标量。
- → 8 个 `tags:` 全部改为**顶格**。
- **根因澄清**：本插件 `lib/skill.js` 的 `parseSkillFile()` 用的是**行式正则**
  （`^key:[ \t]*(.*)$`），只取 `name` / `description`，**不读 `tags`** —— 所以注册没炸、
  `tags` 本来就是死字段。风险在于：一旦宿主改用 YAML 解析、或任何工具按 `tags`
  过滤/路由，这 8 个技能会整体失效（与 v0.6.1 修过的「同层重名 → 一个技能都没注册」同类）。
- **回归防护**：`test/smoke.mjs` 新增 **T6**，对全部 11 个 `SKILL.md` 断言「frontmatter
  无缩进顶层键」；`npm test` 29 → 30。

### P2（安全面）decision-loop 全流程不过脱敏门禁
- `decision-loop` 会落盘 `ledger.jsonl` / `settlements.json` / `reflections.jsonl` /
  `decision-quality.md`，其中 `label_conditions` / `conditions` / 复盘散文是自由文本，
  而 stock-research / fund-research 两条流水线都有门禁，闭环这条没有。
- → **机器强制（无绕过开关）**：`decision_log.py append` 落盘前调用
  `stock-research/tools/sanitize_check.py` 的 `scan_text()`，命中 CRITICAL/HIGH 直接返回
  `sanitize_blocked:<规则>` 且**不写入**；门禁脚本缺失时 `sanitize_missing` **失败关闭**
  （宁可写不进，也不静默放行）。
- → **文档强制**：`decision-loop/SKILL.md` 新增「脱敏门禁」小节（覆盖阶段 1–4 全部落点、
  与 stock/fund 共用同一份脚本不另复制），核心原则 10 明写「**账本只存公开结论，
  不含任何个人头寸**」；`roles/settlement.md` 与 `roles/reflection.md` 各加一条
  「个人头寸零落盘 + 门禁非 0 禁止写入」。
- 门禁只放行公开研究结论与公开数据；公开政策条款（`单户上限100万元…`）与否定式合规声明
  （`本报告不涉及任何资金规模…`）不误伤（沿用 `test_gate.py` 的既有豁免语义）。

### P3 文档修正（4 项）
1. `fund-research/SKILL.md`：删除第 101–102 行**逐字重复**的同一句（`fundf10` 补抓说明）。
2. `fund-research/roles/orchestrator.md`：原称「本文件、`peer-orchestrator.md` 与
   `comparator.py` 都从 `rating.py` 读词表」——**不实**。实测 `comparator.py` 在代码里
   `from rating import LABELS, REVIEW, parse_label, is_review`；两个角色提示词
   （`orchestrator.md` / `peer-orchestrator.md`）**并不 import**，只是把同一 5 档标签
   写进提示词约束。→ 改写为「代码导入 vs 提示词约束」两条路径的准确描述，并说明
   `peer-orchestrator.md` 的对比结论只落 `comparison.md`、不写 `decision.json`。
3. `decision-loop/roles/reflection.md` 铁律 5 断句：「来当初就知道」→「来替代「当初已知的信息」」。
4. `fund-research/SKILL.md` 的 `$TOOLS` 清单漏列 `rating.py` / `decision_log.py` /
   `settle.py` / `decision_score.py` / `test_peer_tools.py` / `test_decision_tools.py`
   （正文已在调用）→ 清单补全。

### 开放问题定调：阶段 6 对比层结论不写 `decision.json`
- 上游问「设计如此，还是漏了」。**定调：按设计不登记**。`ledger.jsonl` 每条记录绑定单个
  `code`，而对比层的「谁更优 / 是否可排序」是**相对判断**，不属于任一单标的的决策；
  复用阶段 5 的 append 会把它错记到锚点基金名下，污染 `decision_score.py` 的分组统计。
- → 在 `fund-research/SKILL.md` 阶段 6「第四步 · 协调员裁决」下加边界说明：每只同业基金
  各自在阶段 5 已写各自的 `decision.json`，对比层只产出 `comparison.md`；若未来要让对比
  结论进入统计，须先设计不挂 `code` 的独立记录类型，而不是复用现有 append。

### 测试
- `test/smoke.mjs` **29 → 30**（新增 T6 frontmatter 结构断言）。
- `test_decision_tools.py` **64 → 69**（新增 5 条：PII 记录被拒且不落盘、政策条款放行、
  否定式声明放行、门禁通过后累计条数）。
- 全量：`npm test` 30/30 · `test_gate` 27/27 · `test_fund_tools` 38/38 ·
  `test_peer_tools` 24/24 · `test_decision_tools` 69/69。

## 0.6.2 — 2026-10-29
**修正 `decision-loop` 技能里的工具路径（文档修正，无代码变更）。**

`decision-loop` 的 `SKILL.md` 写着 `$TOOLS = $BASE/tools`，但该技能目录**只有** `SKILL.md` 与
`roles/`，四个脚本实际与 `fund-research` 共用（`assets/skills/fund-research/tools/`）。
照文档执行会去一个不存在的位置找 `decision_log.py` / `settle.py` / `decision_score.py`。
→ 改为 `$TOOLS = $BASE/../fund-research/tools`，并把「本 skill 不自带 tools 目录、不要去找
`$BASE/tools`」写清楚；正文里残留的 `tools/rating.py` 一并对齐为 `$TOOLS/rating.py`。

> 同类路径在另外两处本来就是对的，可作对照：`fund-research` 用自己的 `$BASE/tools`；
> `stock-research` 用 `$SHARED = $BASE/../fund-research/tools`。

### 测试
无断言变更（纯文档）。全量仍为：`npm test` 29/29 · `test_gate` 27/27 ·
`test_fund_tools` 38/38 · `test_peer_tools` 24/24 · `test_decision_tools` 64/64。

## 0.6.1 — 2026-10-29
**修复阻断级缺陷：插件在真实 Host 上**一个技能都没注册**（`active` 但技能列表为空）。**

### 根因
Host 的 `skills.registerProvider()` 对**同层内重名的 provider 直接 throw**
（"Duplicate names within one layer and reserved names throw"）。
本插件 `providerNameFor()` 此前让**全部 11 个 provider 共用同一个名字** `dsh-invest-research`：
第 1 个注册成功，第 2 个开始抛出；异常逃出 `ctx.inject` 回调 → Cordis 销毁该 inject fiber
→ **已注册的那个 provider 也一并被注销**。结果：插件 `fiberPhase=active`，但
`stock-research` / `fund-research` / `pick-funds` … 全部 `unknown or no longer available`。

这个缺陷**自 v0.3.0 加入第 2 个技能起就存在**（v0.1.0 只有 1 个技能，不会撞名），
此前版本在真实 Host 上其实一直是 0 技能可用。自测没发现的原因是：
`smoke.mjs` 的假 `ctx.registerProvider()` **无条件收下**，从不校验重名 —— 单元测试全绿，
线上却是空的。

### 修复
1. **provider 名逐个唯一**：`providerNameFor(dir)` 改为按技能目录命名
   （`dsh-invest-research:stock-research`、`…:fund-research` …），既保证唯一、又保留插件归属可读性。
   新增 `skillIdFromDir()` 取目录末段。
2. **注册循环全程加固**：`registerProvider` 也纳入 try/catch（此前只保护了 `createSkillProvider`）。
   一个技能被 Host 拒绝 → **只损失这一个技能**并 warn，不再连带炸掉整批。
   （原则与已有的「SKILL.md 损坏只跳过该技能」一致。）
3. **测试补上重名闸门**：`smoke.mjs` 的假 `ctx.skills.registerProvider()` 现在**照真实 Host 行为
   对重名 throw**（并可指定 `rejectNames` 模拟保留名），另加 **T5**：拒绝其中一个 provider 时，
   其余 10 个照常注册、只 warn 1 次、被拒的技能不在结果里。

### 验证（真实 Host）
重载插件后技能目录从 **0 → 11** 全部出现：`stock-research`、`fund-research`、`decision-loop`、
`pick-funds`、`fund-screen`、`fund-profile`、`fund-watch`、`holdings-style`、`manager-profile`、
`report-render`、`xlsx-author`。

### 测试
- `npm test`（注册冒烟）**21 → 29 条**：新增 provider 名唯一性 3 条、`skillIdFromDir` 1 条、
  T5 注册健壮性 4 条。
- 全量：`npm test` 29/29 · `test_gate` 27/27 · `test_fund_tools` 38/38 · `test_peer_tools` 24/24 ·
  `test_decision_tools` 64/64。

## 0.6.0 — 2026-10-29
**消化 TradingAgents（TauricResearch/TradingAgents）的三个可迁移机制：时点纪律、可解析的决策信号、决策闭环。**

对上游逐项比对后，判定**值得吸收**的是三件事（本插件已强于上游、不吸收的部分：多 agent 角色隔离、
审计轮次、PII 门禁、来源标注强度；上游强于本插件而必须补的：下面三项）。
上游另有「组合持仓上下文 / checkpoint 恢复 / LangGraph 编排」三项**刻意不吸收**：
前两者与本插件的 PII 门禁与「单一数据源锁定」直接冲突，后者依赖本插件没有的运行时。

### 1. 时点纪律 Point-in-Time（新增 `pit.py`，两 skill 共享）
此前本插件只做了「披露滞后标注」（报告期 vs 检索于、`stale` 标记），**没有做时点纪律**：
所有历史序列一律取到「当下」，所有财报一律取「最新版」。于是一份声称基准日为 2026-06-30 的报告，
只要用了这些工具产出的数据，就会静默地把 7—10 月的行情、后来重述的财报、
以及基准日之后才披露的持仓算进结论 —— 典型的**前视偏差**。
- `assets/skills/stock-research/tools/pit.py`（新）：披露日期推算（季报 25 日 / 半年报 60 日 / 年报 90 日，
  **保守上界**：宁可认为数据更晚可见，也不误判为基准日已可见）、`AsOf.visible()` 可见性判定、
  `truncate()` 序列截断。
- `fetch_data.py` / `fetch_fund.py` 新增 **`--as-of <YYYY-MM-DD>`**：历史行情/净值序列截断到基准日；
  `fetch_data.py` 里写死的 `end=20500101`（远期终点）改为按基准日下发；
  报告期晚于基准日的持仓**退回上一可见期**或置空，并在「数据缺口」写明。
- 记录 schema 新增 **`披露日期`** 字段（取不到记「未取得」，**不得以数据日期冒充**），
  披露日期晚于基准日的记录强制 `stale=true`（`stale` 原本只说明「标签有多旧」，
  不说明「内容在基准日是否可见」，这里补上后视维度）。
- 角色文件同步：`stock-research` 核心原则新增 5 条（基准日声明 / 序列截断 / 披露日期分列 /
  取原始披露版本不取最新版本 / 结论标签落盘）；`roles/auditor.md` 新增 B2 时点纪律检查与 D2 结论标签检查；
  `roles/policy.md` 时间窗改为以基准日为终点；`roles/data.md` 补「巨潮与东财 F10 给的是**当前（可能已重述）**的数」
  的取数纪律；`fund-research` 核心原则新增 3 条，`roles/red-team.md` 新增 A2 时点纪律检查。

### 2. 可解析的决策信号（新增 `rating.py`）
- **结论标签词表收敛为唯一权威来源**（`rating.py` 的 `LABELS`）：此前同样的 5 档标签在
  `fund-research/SKILL.md`、`roles/orchestrator.md`、`roles/peer-orchestrator.md`
  与 `tools/comparator.py` 各写一遍，词表会漂移。
- 新增 **`REVIEW` 哨兵**：解析不出结论标签时返回 `REVIEW`，**绝不**静默退化成「等待验证」。
  一个读不懂的决策不是中性决策，把它记成中性标签等于伪造了一个从未作出的判断。
- 锚定解析器：NFKC 归一化（全角冒号/括号）、容忍 Markdown 加粗与破折号分隔、
  剔除「标签尺度说明」行、**只认第一处「结论标签：xxx」**。
- **顺带修掉 `comparator.py` 的真实缺陷**：原实现是对整篇报告做 `if l in md` 子串扫描 ——
  会把正文里讨论到的**他人结论**当成本次结论（tuple 顺序靠前的标签还会抢先命中），
  且失败时静默返回空串。实测历史报告 `fund-runs/012733-20261021-2355/report.md`
  是「两只基金各一个标签」的比较式报告，旧实现会误判成 `可以关注`，新实现正确返回 `REVIEW`。
- `stock-research` 补上**加权总分 → 结论标签**的映射表（此前 0–100 总分**没有任何阈值映射**，
  分数与结论之间是断的），并把标签写进 `roles/valuation.md` / `roles/orchestrator.md` / `roles/auditor.md`。

### 3. 决策闭环 Decision Loop（新增 `decision-loop` skill + 3 个工具）
一次运行只给出一个决策，**无法说明这套方法决策得好不好**。v0.6.0 把它变成闭环：
```
研究 → decision.json → 决策日志（只增追加）→ 结算（对齐已发生行情）→ 复盘 → 下次研究可读
```
- `tools/decision_log.py`：只增追加的 `ledger.jsonl`；按 `run_id` 幂等去重；
  损坏行不阻断读取但计数上报；`verify` 校验重复与缺字段。
- `tools/settle.py`：结算。**入场取决策日之后第一个收盘日**（决策日当天收盘后才发布的信息不计入）；
  算原始收益 / 基准同期 / 超额；判 `SETTLED` / `PENDING`（窗口未走完）/ `SETTLE_FAILED`（数据缺失）。
- `tools/decision_score.py`：按标签聚合 `n` / 平均超额 / 命中率。
  **命中率只对方向性标签计算**（`优先研究`/`可以关注`/`暂不优先`）；
  `等待验证`/`数据不足`/`REVIEW` 不宣称方向，**不计命中率**但样本数单独列出；
  未到期/结算失败/不可解析的单元**全部显式列出**；附**前视偏差说明**
  （按 `vocabulary_version` 分组，跨版本**不合并**）。
- `assets/skills/decision-loop/`（新 skill，3 角色）：结算员 / 复盘员 / 质量分析师。
- **边界写进角色文件**：历史决策与复盘里的数字**不得**写进新报告的数据表 ——
  它只提供「先验教训」与「上次结论是否被证伪」；`fund.json` 仍是唯一事实底稿。
  这条边界必须显式声明，否则下一轮审计员会（正确地）把它判成违反「单一数据源锁定」。

### 4. 顺带修掉的既有缺陷
- `test_fund_tools.py` 的 `_pkg_version 无清单时返回 None` 断言**在插件根目录下运行必失败**（既有缺陷，
  非本次引入）：夹具目录建在 cwd 下，向上走几步就撞上插件自己的 `package.json`，
  「无清单」根本构造不出来。→ 给测试辅助函数加 `stop_at` 边界参数，夹具与真实插件树隔开。
- `decision_score.py` 渲染时的**样本数丢失**：已结算样本为 0 的标签（如全部未到期的「等待验证」）
  原本整行不渲染，连样本数一起消失 —— 恰好抹掉了「这个标签一条都没验证过」这个最该看见的信息。
  → 改为覆盖所有出现过的标签，并单列「未到期 / 结算失败」两列。

### 测试
- `test_decision_tools.py`（新）**64 条**：时点纪律（披露日期推算 / 可见性判定 / 序列截断）、
  标签解析（加粗 / 全角 / 破折号 / 尺度行 / REVIEW 哨兵）、日志（只增 / 幂等 / 损坏行）、
  结算口径（入场在决策日之后 / alpha / PENDING / FAILED）、命中率口径（仅方向性标签 / 跨词表版本不合并）。
- `test_peer_tools.py` **23 → 24 条**：`extract_report_tags` 空输入改断言 `REVIEW` 哨兵，
  并新增「正文提到某个标签但本次结论没写时，不得把提到的标签当结论」。
- 全量：`npm test` 21/21 · `test_gate` 27/27 · `test_fund_tools` 38/38 · `test_peer_tools` 24/24 ·
  `test_decision_tools` 64/64。

## 0.5.7 — 2026-10-25
**上游复核 v0.5.6 后的两条可选优化（非阻断，均已落地）。**

1. **`test_fund_tools.py` 的 SKIP 提示不够醒目 / 无清单布局下守卫不生效**（上游可选 ①）：
   原实现找不到插件清单时静默 `SKIP`，`_VERSION_FALLBACK` 的「防人肉抬值脱节」守卫随之失效。
   → 改为：
   - 无论是否带清单，**先校验 `_VERSION_FALLBACK` 是合法的 `x.y.z` 版本号**（格式闸，无清单布局下仍生效）；
   - SKIP 时改为 **`⚠️ SKIP` 显式告警**，说明本布局下守卫不生效、请人工确认回落值。
2. **`_resolve_version()` 的插件根依赖未写进安装约束**（上游可选 ②）：现要求插件根同时有
   `assets/` + `package.json`，只拷 `skills/` 会静默回落版本、令人困惑。→ README「Install 安装」
   新增**安装约束**说明（含 `_VERSION_FALLBACK` 回落预期与 `⚠️ SKIP` 提示）；
   顺带修正 Quick check 里过期的 `npm run test:all` 计数（`21+27+30+14` → `21+27+37+23`）。

### 测试
- `test_fund_tools.py` **37 → 38 条**：新增 `_VERSION_FALLBACK 是合法的 x.y.z 版本号（无清单布局下仍生效）`。
- 全量：`npm test` 21/21 · `test_gate` 27/27 · `test_fund_tools` 38/38 · `test_peer_tools` 23/23。

## 0.5.6 — 2026-10-25
**上游复核 v0.5.5 后的唯一技术项收敛：测试自身跨安装形态可跑（纯测试改动，不影响运行时）。**

1. **`test_fund_tools.py` 的 `_pkg_version()` 只适用于仓库布局**（上游 P3）：原来写死
   `HERE/../../../..` 取仓库根 `package.json`，Qoder 等把 `skills/fund-research/tools` 独立安装时
   会直接 `FileNotFoundError`。→ 改为**向上找 `.qoder-plugin/plugin.json` → 带 `assets/` 标记的
   `name=='dsh-invest-research'` 清单 → 都没有返回 `None`**；`None` 时同步断言**跳过**（打印 SKIP，
   不计失败），测试自身在任何安装形态下都能跑完。

### 测试
- `test_fund_tools.py` **35 → 37 条**：新增 `_pkg_version 认 Qoder 布局的 .qoder-plugin/plugin.json`、
  `_pkg_version 无清单时返回 None（跨安装形态可跑）`。
- 全量：`npm test` 21/21 · `test_gate` 27/27 · `test_fund_tools` 37/37 · `test_peer_tools` 23/23。

## 0.5.5 — 2026-10-25
**上游复核确认 v0.5.3 / v0.5.4 后，对 3 项遗留 P3 的收敛（不新增功能）。**

1. **`roles/peer-builder.md` 未同步 v0.5.4 的新行为**（上游 P3 ①）：v0.5.4 只在 SKILL.md 写了
   「列表外代码 → `fetch_fund_identity()` / `type_unknown_codes[]` /
   `agent_review_required(type_unverified)`」，角色文件没改 → Agent-P1 按旧角色文件执行时
   不知道要复核这一项。→ 补进 `roles/peer-builder.md` 的**「核对筛选依据」**（列表外代码说明）
   与**「字段契约」**（`type_unknown_codes` 行）。
2. **`_VERSION_FALLBACK` 仍是手写常量**（上游 P3 ②）：漏改就会重现"包版本 X、脚本报 Y"的脱节。
   → 新增**一致性断言**：`test_fund_tools.py` 读插件根 `package.json.version`，断言
   `_VERSION_FALLBACK == package.json.version`。发版抬版本时若漏改回落值，测试直接失败。
3. **`_resolve_version()` 搜索范围再收紧**（上游 P3 ③）：已加 `name == 'dsh-invest-research'`
   校验，现**再加插件根标记**——只认**目录下同时存在 `assets/`** 的清单，进一步避免安装路径之上
   碰巧出现同名清单；真实路径（`<插件根>/assets/skills/fund-research/tools/`）向上 5 级命中插件根，
   安装路径（`node_modules/dsh-invest-research/...`）同样命中。补 1 条测试
   （name 匹配但无 `assets/` → 不认）。

### 测试
- `test_fund_tools.py` **33 → 35 条**：新增 `_VERSION_FALLBACK 与 package.json.version 同步`、
  `_resolve_version 要求目录下存在 assets/（插件根标记），否则不认`；原
  `_resolve_version 采用 name 匹配的本插件 package.json` 升级为带 `assets/` 的插件根。
- 全量：`npm test` 21/21 · `test_gate` 27/27 · `test_fund_tools` 35/35 · `test_peer_tools` 23/23。

## 0.5.4 — 2026-10-25
**上游复核第三轮修复（4 项 P3）+ 版本解析加固。**

1. **`fetch_fund.py` `_resolve_version()` 回落字面量仍为 `'0.5.2'`**（与包版本脱节）→ 提取为
   `_VERSION_FALLBACK`，改为当前版本 `0.5.4`；`package.json` 版本变更时同步。
2. **`_resolve_version()` 向上找 6 级 package.json，范围偏宽**（可能命中安装路径之上的无关项目）
   → 改为**只认 `name == 'dsh-invest-research'` 的清单**，name 不匹配一律跳过；新增 2 条测试
   （忽略无关清单 / 采用本插件清单）。
3. **`--codes` 里"列表外代码"被当"非被动型"误拒**（真实缺陷）：注释写"靠抓取跟踪标的判定"，
   但循环里先 `is_passive('')`，空类型直接判非被动型并 `continue`，**不会再去抓跟踪标的**
   ——东财列表未收录的新基金/特殊代码即使有效也会被误拒。→ 新增 `fetch_fund_identity()`
   抓基金页取简称/类型；**类型仍未知时跳过判据 B、交判据 A（跟踪标的严格相等）兜底**，
   并记入 `type_unknown_codes[]` 与 `agent_review_required`（`type_unverified`），不再误拒。
4. **`--max-candidates=0` 会把候选全部截断**（未校验下限）→ `build()` 入口校验 `>= 1`，
   否则报错退出；help 文本同步注明。

### 测试
- `test_fund_tools.py` **30 → 33 条**：新增 `VERSION 对齐当前版本`、`_resolve_version 忽略 name
  不匹配的无关 package.json`、`_resolve_version 采用本插件 package.json`。
- `test_peer_tools.py` **21 → 23 条**：新增 `test_build_out_of_list_code_not_rejected_as_nonpassive`
  （列表外代码不被误拒 + 记 `type_unknown_codes`）、`test_build_max_candidates_zero_errors`。
- 全量：`npm test` 21/21 · `test_gate` 27/27 · `test_fund_tools` 33/33 · `test_peer_tools` 23/23。

## 0.5.3 — 2026-10-25
**上游复核 v0.5.2 后追加的非阻断修复（P1–P3）。**

### 上游复核追加修复（第二轮）
1. **P1/P2 `load_fund_list()` 的 https→http 回退只覆盖"解析失败"，不覆盖"连接失败"**（已实测复现）：
   原实现 `raw = http_get(SEARCH_URL)` 在 try 之外，TLS/连接层直接拦截时异常先抛出、回退不执行；
   `json.loads` 也在回退范围之外。→ 改为 `for url in (SEARCH_URL, SEARCH_URL_FALLBACK)` 循环，
   **抓取与解析都放进 try**，连接层失败与畸形内容都触发回退；两者都失败抛
   `RuntimeError('fundcode_search.js https/http 均失败: ...')`。补 3 条行为测试。
2. **P3 `SKILL.md` 写「anchor.name 留空」与代码不符**（代码写占位符）→ 改为与代码一致的
   `(未解析，须 Agent-P1 从 fund.json 补齐)`，避免下游按空字符串判断。
3. **P3 docstring 说 bash/PowerShell 均可，但三条示例仍是 PowerShell 语法** → 示例改
   `"$PY" peer_builder.py ...`（bash / PowerShell 通用）。
4. **P3 `--keyword` 仍是串行抓取** → `SKILL.md` 注明最坏耗时（上限 60 × 单只 20s ≈ 20 分钟），
   并保留「优先 --codes」的建议。
5. **P3 `fetch_fund.py` `VERSION='0.3.0'` 滞后** → 改为**向上找最近的 `package.json` 读版本**，
   找不到回落字面量；现解析为 `0.5.2`，不再与插件版本脱节。

### 测试
- `test_peer_tools.py` **18 → 21 条**：新增 `test_load_fund_list_falls_back_on_connection_failure`
  （https 连接层失败 → 回退 http）、`test_load_fund_list_falls_back_on_parse_failure`
  （https 返回畸形内容 → 回退 http）、`test_load_fund_list_both_fail_raises`（双失败抛错）。
- 全量：`npm test` 21/21 · `test_gate` 27/27 · `test_fund_tools` 30/30 · `test_peer_tools` 21/21 ·
  `report-render/selftest.py` PDF 半程通过。

## 0.5.2 — 2026-10-25
**上游复核 v0.5.1 后追加修复（P0–P2）+ report-render / xlsx-author 环境准备。**
**注意：docx 逐页视觉验收与 xlsx 公式重算仍依赖 LibreOffice，本机未装成功，走降级路径并如实标注。**

### 环境准备
- **已装 Python 依赖**（DSH 自带运行时 site-packages，`python -m pip install -r assets/skills/report-render/scripts/requirements.txt`）：
  `reportlab 5.1.0` / `matplotlib 3.11.2` / `pypdf 6.21.2` / `pypdfium2 5.14.0` /
  `fonttools 4.67.0` / `pymupdf 2.0`（`pillow` 原有）。由此 **PDF 直出、图表、矢量/墨量校验、
  CJK 字形覆盖检查（`check_glyph_coverage`）全部可用**。
- **LibreOffice：本机未装成功（如实记录，未假装通过）**。docx 排版量测 / xlsx 公式重算校验
  仍走技能定义的**降级路径**（`未测量 / 未经视觉验收 / recalc_unavailable`），明确**不算通过**。
  排查记录（供后续复用）：
  - `winget` 本机不可用（`--version` 空、`search` 退出码 `-1978335231`）；
  - 用户级 MSI 安装 `msiexec /i ... /qn ALLUSERS=2 MSIINSTALLPERUSER=1` → 退出码 **1603**（安装器
    custom action 失败，日志见 `Transforming table Error`）；
  - 无管理员可用 **administrative image `msiexec /a`** 解包，但 `program\bootstrap.ini` 里
    `InstallMode=<installmode>` 是**未替换占位符**，改成 `InstallMode=1` 后 `soffice.COM --headless`
    能转换成功、`soffice.exe` 仍弹"配置文件已损坏"——**不保证稳定**。
  - 结论：**技能只要求 `soffice` 在 PATH 上**，探测用 `shutil.which("soffice")`；装好可用环境后
    docx/xlsx 校验自然升级为全量，无需改代码。
- **修 `fin_report/fonts.py`**：字体生成缓存 `CACHE`（默认 `~/.cache/fin_report/fonts`）在
  DSH 沙箱里不可写时此前直接抛 `PermissionError` 中断渲染。现新增 **可写回退**：
  逐候选探测（`CACHE` → cwd 下 `.fin_report_fonts`），取第一个真能写的目录；全失败才报错。
- **修 `fin_report/verify.py`**：`tempfile.TemporaryDirectory` 的清理在 Windows 下会因
  LibreOffice 写入的 profile ACL 抛 `WinError 5`，导致 docx 排版量测后误报失败。现改为在工作区
  暂存目录 + **best-effort 清理**（删不掉不致命）。

### 上游复核追加修复（对 v0.5.1 逐项复核 10 项后的补充，非阻断）
- **① `--track` 不再静默关掉判据 C**（`peer_builder.py build()`）：给 `--track` 时此前只设
  `anchor_track`、`anchor_name` 留空 → `anchor_stem=''` → "同基金不同份额"剔除**实际失效**。
  现统一先从全量基金列表按 `--anchor` 解析锚点简称，`--track` 只跳过抓取、不跳过身份解析；
  解析不到则在 stderr 警告 + `gaps` 显式标注"判据 C 未生效，须 Agent-P1 从 fund.json 补齐"。
- **② 修正 `anchor.name` 回落文案**：`(由 fund.json 提供)` 与实际行为不符（脚本从不读 fund.json）
  → 改为 `(未解析，须 Agent-P1 从 fund.json 补齐)`。
- **③ `--keyword` 增加候选预筛 + 上限**：此前命中几十/上百只时串行逐只 `fetch_tracking_target`
  （单只 20s 超时）耗时不可控。现**先按被动型预筛**（非被动型直接记入 `rejected`，不进抓取循环），
  再按 `--max-candidates`（默认 **60**）截断；超限记入 `rejected`（`keyword-cap`）与 `gaps`，
  提示调高或改用 `--codes`。
- **④ host 中性 + 源协议 + 文案一致性**：docstring 用法行改 **bash / PowerShell 均可**（去掉
  "Windows 必须走 pwsh"）；`SEARCH_URL` 改 **`https://`**（保留 `http://` 作回退）；"某ETF 这类基础盘
  仍须人工核对"改为"**识别不出的份额类别（share_class 为空）一律记为未取得，须人工核对**"，
  与已修的 `TYPE_TAIL_WORDS` 逻辑一致。
- **⑤ 命名统一 `$CMP`**：`SKILL.md` / `peer-orchestrator.md` 用 `$CMP`，`peer-red-team.md` 仍用
  `<对比目录>` → 统一为 `$CMP`，消除角色间路径转述偏差。

### 测试
- `tools/test_peer_tools.py` **10 → 14 → 18 条**：`14 条` 随 v0.5.1 交付（`某ETF` 份额 / 短码补零
  回归），本轮再加 4 项：`test_search_url_is_https`、`test_build_track_still_resolves_anchor_name`、
  `test_build_track_unresolved_anchor_is_flagged`、`test_build_keyword_prescreens_and_caps`。
- 全量：`npm test` 21/21 · `test_gate` 27/27 · `test_fund_tools` 30/30 · `test_peer_tools` 18/18 ·
  `report-render/selftest.py` PDF 半程通过（5 页 PDF 真实生成）；**docx 排版量测 / xlsx 公式重算
  依赖 LibreOffice，本机未装好，已如实降级为「未测量 / recalc_unavailable」，不假装通过。**

## 0.5.1 — 2026-10-24
**修复上游 v0.4.0 复盘指出的 10 项问题（P0×4 / P1×3 / P2×3）。本轮不新增功能，只让"声明"与"实现"对齐。**

### P0 · 实现与声明不符（已修）
1. **`peer_builder.py` docstring 声称 6 条铁律、实际只执行 3 条** → docstring 改为三段式如实陈述：
   **脚本执行 3 条**（跟踪标的严格相等 / 仅被动型 / 非同一基金不同份额）+ **只标记 1 条**（结构差异）
   + **脚本不执行、须 Agent-P1 人工复核 2 条**（交易状态、份额类别一致性）。
   `peers.json` 新增 `criteria_executed[]` 与 `agent_review_required[]` 两个字段，把"哪些是脚本判的、
   哪些要人判"变成机器可读契约。
2. **`detect_share_class()` 把基金类型后缀误当份额类别**（`某ETF` → `F` 份额）→ 新增
   `TYPE_TAIL_WORDS`（发起式联接/ETF联接/联接/LOF/FOF/QDII/ETF/指数增强/指数/增强/基金）
   与 `CURRENCY_PAREN`，**先剥类型缩写与币种括号、再匹配末位字母**。
3. **`--codes` 未补前导零**（传 `11839` 直接「未找到」）→ 新增 `normalize_code()` / `normalize_codes()`，
   锚点与候选一律 `zfill(6)`，并打印 `[代码归一] 11839→011839`。
   *（`roles/peer-builder.md` 从 0.4.0 起就写着"脚本已 zfill(6) 兜底"——但代码里没有。本次补上实现，
   文档这次没有说谎：是代码在说谎。）*
4. **peer 角色未接入主流程**（4 个 peer 角色 + 对比层从未写进角色表/执行流程）→ 0.4.0 的 SKILL.md
   已补「阶段 6 + 对比层角色表 + 对比层铁律」，本次复核确认**已接入**，并补齐 `$CMP` 目录约定。

### P1 · 对比层流程缺陷（已修）
5. **`peer-orchestrator.md` 缺"必须由协调员亲自派发"硬约束** → 已有「铁律 11」段落；本次补
   **第 0 步：$CMP 目录约定**（先建目录再派发，且"对比产物只在工作区生成，绝不写回插件目录"）。
6. **未要求 4 角色"同一消息内并行派发 + 互不引用对方结论"** → `peer-orchestrator.md` 第 2 步
   明确「**同一批**派发」「互不引用对方结论」；SKILL.md 阶段 3/6 同步。
7. **`peer-red-team.md` 缺"未找到反向证据也要标注方向"强制项** → 新增**「方向性核对表」**：
   维度（申赎冲击/流动性/冲击成本/换手）× 是否覆盖 × 方向（利于 A / 利于 B / 不可判定），
   并要求三选一显式结论——**"未找到反向证据"本身必须写出来，不允许留空**。

### P2 · 文案/口径问题（已修）
8. **`peer-builder.md` 输出结构与脚本实际 JSON 不一致**（角色写"接受同业/拒绝同业"，脚本写
   `anchor/peers/rejected` → 下游按名读 `KeyError`）→ 新增**字段契约表**（含 `comparability` 三个分组），
   并明确「禁止使用中文字段名」。
9. **`$CMP` 目录约定缺失** → 新增 `$CMP` 结构块：`peers.json`（脚本所有，任何角色不得手改）/
   `peer-review.md`（Agent-P1 复核产物）/ `work/` / `comparison.md`。
   **Agent-P1 不再"往 peers.json 里追加说明"**（脚本产物只读），改为写独立 `peer-review.md`。
10. **"实测高发"案例数字易被当通用结论** → `peer-red-team.md` / `peer-orchestrator.md` / SKILL.md
    对比层铁律中的 `1.38%`、`2.24%`、`43.51亿 vs 120.51亿`、`3.97%`、`≈54%` 全部标注
    **「历史案例 / 示例数据，非通用统计，须按本次实测重算」**。

## 0.5.0 — 2026-10-24
- **新增 pick-funds 分析师套件（8 个技能，移植自 ZCode 官方插件 pick-funds v0.1.1，DSH 原生版）**：
  覆盖公募基金 / ETF / LOF 的筛选、单基金画像、经理画像、持仓与风格分析、候选池持续跟踪，
  以及 PDF / DOCX / XLSX 交付。
  - 技能：`pick-funds`（入口）· `fund-screen` · `fund-profile` · `fund-watch` ·
    `holdings-style` · `manager-profile` · `report-render` · `xlsx-author`
  - 入口技能新增「与 `fund-research` 的分工」路由节：轻量单 agent 工作流 → 本套件；
    穿透式 + 多智能体 + 反方审查 + 固定 10 章 → `fund-research`。
- **全面改为 DSH 原生版**：去掉 Hermes 接线，直接按 DSH 技能规范编写（`name` + `description` + `whenToUse` + `tags` 根级字段）；
  入口技能新增「基金数据纪律」与「共享护栏」细则；`fund-screen` 新增 `wind-fund.search_funds` 筛选引擎；
  `report-render` 详细 Step 4 视觉验收闸门（canary + 逐页复核）+ Step 1N 数值版式检查降级；
  `xlsx-author` 详细 LibreOffice exit 3 处理 + 静态 lint 降级路径。
- **新增「DSH 数据源映射」层**：原套件按 Hermes 的 `hexin-*` / `wind-*` / `finance-search`
  MCP server 接线，DSH 版直接映射到本插件自带取数脚本（`fetch_fund.py` /
  `fetch_data.py`）+ `web_fetch` / `web_search`，并保留 `源不可用` 纪律与退出码 `1`
  的「禁止带空数据继续」。
- **`lib/skill.js` 注册数 2 → 10**；`test/smoke.mjs` 断言同步扩到 10 个技能（含名单、
  rank、provider、逐 provider 不串台）。
- **frontmatter 全面改为 DSH 规范**：8 个技能采用 `name` + `description` + `whenToUse` + `version` + `author` + `tags`（根级）；
  去除 `metadata.hermes`；author 追加 DSH 并入说明；非入口技能的共享护栏引用块注入 DSH 取数指引。
- **依赖边界如实标注**：`report-render` 另需 `reportlab / matplotlib / pypdf / pypdfium2 /
  pillow / fonttools` 与系统 CJK 字体；`xlsx-author` 需 `openpyxl`，公式重算校验走 headless
  LibreOffice（缺失时走技能定义的强制替代检查并如实标注，不假装通过）。

## 0.4.0 — 2026-10-23
- **新增 `fund-research` 对比层（阶段 6，跨基金同业对比）**：给定锚点 + 候选同业，先由
  `peer_builder.py` 按「跟踪标的一致 + 被动属性」筛出合法同业
  （**只筛不评**，不构成"谁更优"的判断），再由**具备 spawn 能力的协调员亲自派发**
  互相隔离的独立角色 agent 做跨基金对比（宏观/行业/估值/绩效各一，互不共享结论防锚定），
  最后由独立 `peer-red-team` 逐条质疑、协调员按证据质量裁决。
  > ⚠️ **0.5.1 修正**：本条原写作「跟踪标的一致 + 被动属性 + **同份额类别** + **开放申购**」，
  > 但 0.4.0 的 `peer_builder.py` **并未执行**后两条。0.5.1 起 docstring 与 `peers.json` 的
  > `agent_review_required[]` 已把这两条如实标注为 **Agent-P1 人工复核项**，不再谎称脚本会做。
- **新增 3 个角色文件**：`roles/peer-orchestrator.md`（对比协调员）、
  `roles/peer-builder.md`（同业筛选，只筛不评）、`roles/peer-red-team.md`（对比反方审查：
  同源假验证 / 样本冒充全组合 / 循环论证 / 被忽略的反向证据）。
- **新增 2 个工具**：`tools/peer_builder.py`（同业筛选器）、`tools/comparator.py`
  （对比矩阵生成器，读 `peers.json` + 各基金 `report.md`/`fund.json`，**不重算指标**、
  只对齐已冻结结论并暴露口径冲突）。
- **新增 `tools/test_peer_tools.py`**：对比层 10 条离线断言，覆盖 peer_builder 的
  跟踪标的解析/被动判定/份额类别/结构判定与 comparator 的报告标签提取/对比矩阵构建
  （零网络，fixture 用工作区临时目录——不用 `%TEMP%`，DSH 沙箱拒绝平台临时目录，
  且 `tempfile.mkdtemp` 建的目录在 Windows 下 ACL 不允许再建子目录）。
- **SKILL.md 新增铁律 11（对比层细则）**：跨基金对比必须由具备 spawn 能力的协调者
  亲自派发互相隔离的独立角色 agent + 独立 red-team；**不得把对比层交给无法派生子代理的
  执行者，否则退化为单模型自证**——此时必须如实声明，不得声称真实多智能体。
- **修 `peer_builder.py` 两个坑**（2026-10-22 实测踩到）：① 搜索候选全量抓跟踪标的过慢
  → 改为**逐只只抓锚点** + 候选仅在名字含目标关键词时抓；② 同名不同份额误判同业
  → 显式校验份额类别 + 标记结构差异为"不可比较维度"。
- **发布合规（第三方插件市场标准）**：
  - `package.json` 补 `author` / `homepage` / `bugs` / `publishConfig.access`；
  - `LICENSE` 版权方由旧包名 `dsh-stock-research contributors` 同步为 `dsh-invest-research contributors`；
  - `cordis.patch.yml` 与 `README.md` 的 `github:<user>/...` 占位符改为真实仓库
    `github:lobster-liu/dsh-invest-research`；
  - `.npmignore` 与 `package.json` `files` 排除 `__pycache__` / `*.pyc`；
  - `npm test` 保留 21 条注册冒烟；新增 `npm run test:python` 与 `npm run test:all`
    统一验证入口（21+27+30+10 条断言）；
  - `README.md` 补 badges、对比层说明与自检命令。

## 0.3.1 — 2026-10-17
- **修复 `test/smoke.mjs` 裸跑必挂**（Qoder 侧复核发现）：ROOT 默认 `'.'` 时
  `'file://' + './lib/skill.js'` 拼出 host 段为 `.` 的非法 file URL → `ERR_MODULE_NOT_FOUND`，
  只有传绝对路径才能跑出 21 条断言，换机器/换 shell 极易误判"测试挂了"。
  修法：默认仓库根由 `import.meta.url` 反推（不依赖 cwd），动态 import 改用
  `pathToFileURL(resolve(...)).href`；package.json 加 `"scripts": { "test": "node test/smoke.mjs" }`
  作为可执行入口（`npm test`、仓库内任意位置裸跑、显式传路径三种方式均验证通过）。
  仅动 `test/smoke.mjs` + `package.json`（version/scripts 两处），运行时代码与取数/门禁脚本零改动。

## 0.3.0 — 2026-10-17
- **修复外部复核（Qoder 侧）发现的 3 项问题**：
  - **P1（阻断）** `install_akshare.ps1` 原为 UTF-8 无 BOM，PowerShell 5.1 按 GBK 解码中文串
    导致 parse 失败（line 100「字符串缺少终止符」）——加 UTF-8 BOM 修复，`ParseFile` 复验 0 错。
    ⚠️ 维护约定：**该文件含中文，任何后续编辑保存都必须保持 BOM**（本次修复中内容改完
    BOM 又被无 BOM 工具冲掉过一次，靠复验抓回）；`run_pipeline.ps1` 保持 ASCII-only 无需 BOM。
    原注释「实测 100% 通过（2026-10-16）」已更正为 pwsh 7.x 宿主实测——5.1 宿主此前连 parse
    都过不去，该声明对 5.1 不成立。
  - **P2（功能）** `run_pipeline.ps1` run 目录拼接错误：`'equity-runs' + $runid` 粘连成
    `equity-runs600048-…`，缺中间层级 → 改为 `Join-Path (Join-Path … 'equity-runs') $runid`，
    与 SKILL.md 的 `<工作区>\equity-runs\<标的>-<时间戳>\` 约定一致。
  - **P3（发布）** `install_akshare.ps1` 删除硬编码个人绝对路径（泄露用户名/安装位置且他机必失败），
    node 探测改为四级查找：`$env:NODE` → `$env:DSH_HOME` 下自带 runtime（3 种 Windows 布局）→
    `$env:LOCALAPPDATA` 候选 → PATH；全部取不到则 throw 并列出三种可执行修法。
- **包名改为 `dsh-invest-research`**（原 `dsh-stock-research`）：v0.3.0 加入基金研究能力后，
  旧名已不能覆盖 `fund-research` skill。两个 skill 名不变（`stock-research` / `fund-research`），
  插件层统一为单一 provider `dsh-invest-research`。GitHub 仓库同步改名后旧地址
  自动 301 重定向；已安装旧版的请移除后按新名重装。
- **新增第二个 skill `fund-research`（基金穿透式研究流水线）**，与 `stock-research` 同包发布、
  共用同一套 PII 门禁与环境约定。7 角色：协调员 + Agent1 基金画像与持仓穿透 / Agent2 宏观政策 /
  Agent3 行业趋势 / Agent4 重仓公司与估值 / Agent5 基金管理与绩效 / Agent6 风险与反方审查。
  五阶段流程：身份与基准日 → 冻结共享事实底稿 → 四路并行独立初稿（互不引用防锚定）→
  反方审查 + 定向补证一次 → 协调员按证据质量裁决（不按多数投票、保留分歧）。
  固定 10 章报告 + 5 个结论标签 + 三种情景 + 5–8 项跟踪清单 + 条件式行动参考。
- **`lib/skill.js` 泛化为多 skill 注册**：`registerSkillsWhenAvailable(ctx, dirs)` 按目录批量注册，
  两个 skill 统一归属 provider `dsh-invest-research`（`PLUGIN_NAME` 常量，不再按目录名推导）；
  单个 SKILL.md 损坏只 warn，其余照常注册；
  保留 `registerSkillWhenAvailable()` 单 skill 兼容入口。`test/smoke.mjs` 扩到 21 条断言
  （两 skill 独立注册 / 无 skills 服务降级 / 坏文件隔离 / provider 不串台）。
- **`assets/skills/fund-research/tools/fetch_fund.py`**（新增）：基金身份、净值序列、多期持仓、
  行业配置、债券持仓、规模份额、持有人结构、资产配置、基金经理、申赎状态、基准对比、
  重仓股快照。多通道降级（东财 pingzhongdata / 基金主页 / f10 归档 / 腾讯行情），
  取不到即列「未取得的指标」，**空数据退出码 1**。
  实测修掉的坑：① 东财时间戳是北京时间的当日零点，取 UTC 会整体错一天，
  导致净值与指数错位、跟踪误差/捕获率失真（已 +8h）；
  ② f10 报告期优先取页面「截止至：YYYY-MM-DD」，比从「2026年2季度」推算可靠；
  ③ 季度披露 >20 条判为半年报/年报全量，否则只是前十大；
  ④ `--history-days` 默认 0（全部历史），否则「成立以来」会静默退化成样本区间
     （另修 `_days_between` 返回 0 被 `or 999` 吞掉的 falsy-zero bug——净值首日恰为成立日时
     正是最该标「成立以来」的情况，却总被错标成「样本区间」）；
  ⑤ 联接/FOF 显式标注「f10 股票持仓不含目标 ETF 与子基金」，防重复计入；
  ⑥ 主页持仓无显式报告期时的推断：只认「季度末 + 25 日历日 ≤ 最新净值日」的最新季度末
     （正推最近的季度末会在 10 月中旬得出尚未披露的三季报 09-30，实际持仓是 06-30 的）。
- **`tools/test_fund_tools.py`**（新增）：30 条离线断言（内联 fixture + 网络桩 + 绊线，
  零网络），覆盖解析、陈旧判定、f10 解析、区间/年化/回撤/分位/基准对比。
- **`sanitize_check.py` 扩展**：新增基金公开披露白名单（净值/阶段收益/规模/份额/持有人/
  资产配置/跟踪误差/持仓占比/重仓股估值等），并把「公司盈利」等公司层面表述与
  第一人称盈亏区分开（真泄漏仍必拦）。`test_gate.py` 从 15 场景扩到 27 场景，含 D 组
  「基金公开披露必放」与「公司豁免不得掩护第一人称盈亏」混合陷阱。

## 0.2.1 — 2026-10-16
- **修复真宿主上 skill 无法加载**(`skill "stock-research" is unknown or no longer
  available`)。根因是提供者契约理解错误:宿主注册表取正文时调用的是
  `provider.get(candidate, options)`(**candidate 对象**,见 `@deepseek-ai/dsh-skill`
  的 `match.provider.get(match.candidate, options)`),而 `get()` 却把入参当**字符串**
  与技能名比较,恒不相等 → 恒返回 `undefined` → 工具报"unknown or no longer available"。
  现在同时接受 candidate 对象与纯名字两种形态。
- **回归测试补齐**:`test/smoke.mjs` 新增 `get(candidate)` 与"外部 candidate"两个用例
  (11/11)。原来只用字符串调用 `get()`,所以这个 bug 在 9/9 全绿下漏过了宿主验证。

## 0.2.0 — 2026-10-16
- **akshare 财务基本面通道**（本次核心新增，直接命中 v0.1.0 两次实测的数据缺口）：
  - `tools/fetch_fundamentals.py`:akshare 取财务分析指标(ROE/每股净资产/资产负债率/
    流动·速动比率/每股经营现金流)、财务摘要(归母净利/营收/扣非/现金流 多期)、
    **PE(TTM)·PB 近一年真分位**(百度估值接口,补上 v0.1.0 只有"区间位置"没有"时间排名"
    的缺陷)。与 data.json 同契约(逐数带来源、取不到填 null 禁止估算、逐接口容错)。
    **akshare 未装时退出码 1 并说明原因,不阻断主流程(HTTP 兜底照常)**。
  - `install_akshare.ps1`:沙箱内离线装 akshare 的固定脚本(Node fetch 下 wheel 到工作区
    + `pip --no-index --find-links` 本地装,绕开 DSH 沙箱对平台临时目录的 `WinError 5`
    拦截 + schannel TLS 握手失败两个坑;jsonpath(sdist) 用 `tarfile filter='data'` 手动装)。
  - `requirements.txt`:pin 到 akshare 1.19.1 实测通过的依赖版本。
- SKILL.md 环境限制节改写:从"不建议装 akshare"更新为"已可离线装,用随包脚本,附沙箱根因
  与已知限制"(akshare 1.19.x 已移除银行监管指标接口,不良率/拨备覆盖率/资本充足率
  仍走 fetch_data.py HTTP 通道或列为数据缺口)。

## 0.1.0 — 2026-10-15
- 首次发布:6 角色多智能体 A 股研究流水线 skill(协调员/数据员/政策员/估值周期员/风控员/审计员)
- `tools/fetch_data.py`:东财双通道取数(HTTP 兜底零依赖,akshare 存在即自动启用)、重试、
  价格分位/最大回撤衍生计算、**空数据退出码 1**(禁止带空跑)
- `tools/sanitize_check.py`:落盘前 PII 门禁(持仓/成本/资金/盈亏/账户 5 类规则 +
  金融术语/政策条款/财报科目/否定声明 4 层白名单;混合陷阱必拦);
  可选外接凭据扫描器(环境变量 `EQUITY_SECRET_SCANNER`)
- `tools/test_gate.py`:15 场景门禁行为自检(安装验证用)
- `tools/run_pipeline.ps1`:ASCII-only(PS 5.1 安全),Python 三级解析(`EQUITY_PY` >
  `DSH_HOME` 自带运行时 > PATH),产物只写当前工作目录
- 流程纪律:双盲隔离、两轮审计不可跳过、runid 带秒防并发、单一数据源锁定、
  多口径并列披露、角色任务"先落盘再细化"
- 已知环境适配:内置 `web_search` 在多数环境不可用 → 角色一律用 `web_call`
  (搭配 `dsh-web-search-pro` 时 `read.fetch` 为主、`search.run` 仅辅助)
