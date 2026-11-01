#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
行情报告脱敏门禁 —— 落盘前的最后一道闸

两道检查：
  A. 复用工作区已有的 audit-secrets.py（28 规则 + 高熵兜底，输出强制脱敏）
     —— 拦截密钥、Token、Bearer、私钥等凭据
  B. 行情报告专用 PII 规则（本文件）
     —— 拦截个人持仓、成本价、资金规模、账号等

设计原则：
  1. **失败即拒绝**：任何命中 → 退出码 1，调用方必须拒绝写入。
  2. **输出脱敏**：只显示前 4 + 后 2 + 长度，绝不把明文写进终端或报告。
  3. **零误报容忍度可调**：--strict 下 MEDIUM 也拦截。

用法：
  python sanitize_check.py <file-or-dir> [--strict] [--json]

退出码：
  0 = 通过（可落盘）
  1 = 发现敏感内容（禁止落盘）
  2 = 用法错误 / 目标不存在
"""

import argparse
import json
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
# Optional external credential scanner (e.g. a Vault audit tool).
# Default absent -> explicitly 'skipped'; built-in PII rules still run.
# Enable via env: EQUITY_SECRET_SCANNER=/path/to/audit-secrets.py

# ---------------------------------------------------------------------------
# 个人持仓 PII 规则
#    严重度：CRITICAL=直接暴露个人头寸/资金  HIGH=强关联个人  MEDIUM=需留意
# ⚠️ 设计约束（2026-10-15 实测教训，改规则前必读）：
#    1. 不得把「政策条款数字」当个人资金 —— 「单户上限100万元」「本金年化1个百分点」
#       是公开政策参数。这类误报曾连续出现 3 次。
#    2. 不得把「年份/日期」当资金额 —— 「2026-10-01起实施」的 2026 不是钱。
#    3. 不得把「分数/比例」当数值 —— 「1/3」的 1 不是成本价。
#    因此以下规则都要求「金额词 + 明确货币单位」同时出现才命中，
#    单有数字没有「万/亿/元/W」不触发。
#    4. 不得用「裸金额」规则（金额 + 元/万元）—— 会误伤基金披露份额、政策条款
#       数字；白名单只有紧邻 ≤24 字符的局部豁免，扛不住整行/跨句误伤。
#       真实泄漏常是第一人称散文（「我买了 3 万元的它」「已持有 3 万元」），
#       所以用「第一人称标记 + 金额」的**组合**规则（见 personal_amount）。
# ---------------------------------------------------------------------------
PII_RULES = [
    # 持仓数量：中文语境下"持仓 N 股/手/份"、"持有 N 股"
    ('holding_qty', 'CRITICAL',
     re.compile(r'(?:持仓|持有|买入|卖出|加仓|减仓|清仓)[^\d]{0,8}'
                r'(\d[\d,]*)\s*(?:股|手|份|张|单位)')),
    # 成本价：成本词 + 数字 + 货币单位（元/块），或「成本价 数字」无单位也命中
    # 无单位时用负向断言排除「利率1/3」这类分数与「个百分点」等政策表述
    ('cost_price', 'CRITICAL',
     re.compile(r'(?:成本价?|持仓成本|建仓价|我的成本)[^\d]{0,8}'
                r'(\d+\.?\d*)\s*(?:元|块)?(?![\d/%])(?!个百分点)(?!pct)')),
    # 资金规模：资金词 + 货币单位（万/亿/元/W）。无单位不命中（避免命中年份）
    ('capital_amount', 'CRITICAL',
     re.compile(r'(?:资金|本金|仓位|投入|可用资金|总资产)[^\d]{0,8}'
                r'(\d[\d,]*\.?\d*)\s*(?:万|亿|元|块|W|w)(?![\d/%])')),
    # 第一人称头寸金额（2026-10-30 上游复核 P2 / 同日二轮复核 P2+P3）。
    # 背景：capital_amount / holding_qty 只认「资金/本金/仓位/投入/持仓」这类**金融关键词**
    # 做锚点，真实泄漏常是散文写法（「已持有 3 万元」「我买了 3 万元的这只基金」「自有 15 万元」），
    # 关键词不在锚点表里 → 整条绕过。
    # 设计（二轮复核后定稿）：
    #   A. 无主语的强自持动词：已持有/持有/持仓/投了/投进/买了/买入/加仓/建仓/头寸/自有
    #   B1. 第一人称 + 的 + 所有格名词：我的持仓/我的仓位/我的本金/我的份额…
    #   B2. 第一人称 + 自持动词（**紧邻**，中间无间隔）：我持有/我买了/我加仓/我押了/
    #       我掏了/我配置了/我打算投/我加码/我拿/我放了
    # ⚠️ 关键：**不再用「裸我 + ≤6 字内任意数字」** —— 那会让判定依赖作者用词长度
    #    （「我认为规模 3 亿元」拦、「我认为该基金规模 3 亿元」放；「我按 3 元净值申购」拦、
    #     「我算下来持仓市值 3 亿元」放）。现在锚点本身必须携带自持语义，间隔只影响
    #    同一锚点下的宽松度，不再决定「是不是个人头寸」。
    # 豁免：
    #   - 机构主语紧邻豁免（本基金/机构/公司…持有 N 亿元 = 公开披露），只对 A 生效；
    #   - 「我方」视为机构自述：从个人锚点移除，加入机构主语豁免。
    # ⚠️ **不做观点句豁免**：新锚点（去掉裸「我」、改为自带自持语义）已修好
    #    「我认为规模 3 亿元」这类误伤；而「我的份额 3 万元」在观点句里仍是
    #    个人头寸，豁免它等于在决策账本留真实泄漏路径（见 CHANGELOG 0.6.6）。
    # ⚠️ 刻意不加「金额 + 元/万元」裸规则：会误伤基金披露份额、政策条款数字。
    ('personal_amount', 'CRITICAL',
     re.compile(
         r'(?:'
         r'(?:已持有|已持仓|持有|持仓|投了|投进|买了|买入|加仓|建仓|头寸|自有)'
         r'|'
         r'(?:我自己|自己|本人|我)(?:的)(?:持仓|持有|仓位|本金|份额|资金|股份|投入|出资|资产)'
         r'|'
         r'(?:我自己|自己|本人|我)(?:持仓|持有|投了|投进|押了|掏了|配置了|打算投|买了|买入|加仓|建仓|头寸|加码|拿|放了)'
         r')'
         r'[^。；;！!？?\d]{0,6}'
         r'(\d[\d,]*\.?\d*)\s*(?:万|亿|元|块|W|w)(?![\d/%])'
     )),
    # 盈亏金额。财报中「亏损(-28.11 亿元)」是标准表述（**半角括号内负数**），
    # 故关键词与数字之间不允许出现半角括号或负号；
    # 但**全角括号仍算命中** —— 「浮盈（15 万元）」是真实个人盈亏，必须拦。
    ('pnl_amount', 'CRITICAL',
     re.compile(r'(?:浮盈|浮亏|盈利|亏损|收益|赔了|赚了)'
                r'[^\d(\-—–]{0,8}'
                r'(\d[\d,]*\.?\d*)\s*(?:万|亿|元|块|%|个点)(?![\d/%])')),
    # 账户 / 券商标识
    ('broker_account', 'HIGH',
     re.compile(r'(?:资金账号|股东代码|证券账户|交易账户|券商)[^\d]{0,6}(\d{6,})')),
    # 身份证 / 手机号（连带保护）
    ('cn_idcard', 'HIGH',
     re.compile(r'\b(\d{17}[\dXx])\b')),
    ('cn_phone', 'MEDIUM',
     re.compile(r'\b(1[3-9]\d{9})\b')),
    # 邮箱
    ('email', 'MEDIUM',
     re.compile(r'\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b')),
]

SEVERITY_ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']

# ---------------------------------------------------------------------------
# 金融术语豁免：这些词含敏感关键词但属公开财务概念，不是个人持仓信息
# 例如「股权成本」「每股收益」会被 cost_price / pnl_amount 误伤
# 误报的危害等同漏报：告警疲劳会淹没真泄漏
# ---------------------------------------------------------------------------
FIN_TERM_SAFE = re.compile(
    r'(?:股权成本|资金成本|负债成本|融资成本|存款成本|边际成本|机会成本'
    r'|每股收益|每股净资产|净资产收益|年化收益|收益率|收益增速|收益质量'
    r'|资金面|资金流向|资金利用率|主力资金|北向资金'
    r'|持仓集中度|持仓占比|机构持仓|基金持仓|持仓市值'      # 公开披露口径，非个人
    r'|持仓量|持仓结构|持仓成本线'                          # 分析术语，非个人成本
    # 自有资金/自有资产等公司财务披露口径（个人锚点「自有」不得误伤公司财报）
    r'|自有资金|自有资产|自有资本|自持|自营资金|自营'
    r'|成本费用率|成本收入比|成本管控|成本优势|成本端'
    r'|盈利预测|盈利能力|盈利质量|净利|毛利'
    # 公司/行业层面的盈利表述（公开财务披露），不含第一人称 → 不豁免「我盈利/个人盈利」
    r'|公司盈利|企业盈利|上市公司盈利|行业盈利|板块盈利|预计盈利|预测盈利|全年盈利'
    r'|亏损面|亏损企业|亏损收窄'
    # 基金公开披露口径（净值/收益/规模/份额/持有人/资产配置/跟踪误差等）非个人持仓
    r'|单位净值|累计净值|净值增长率|净值分位|份额净值|区间收益|期间收益|累计收益|持有收益|区间涨幅|期间涨幅|近1年收益|近1月收益'
    r'|近3月收益|近6月收益|近3年收益|今年以来|成立以来收益|任期收益|阶段涨幅|年化收益'
    r'|年化波动|年化跟踪|跟踪误差|跟踪标的|同类排名|同类平均|超额收益|信息比率'
    r'|基金规模|基金净资产|净资产|总份额|基金份额|基金类型|份额类别|风险等级'
    r'|股票占净比|债券占净比|现金占净比|资产配置|机构持有|个人持有|内部持有|持有人结构'
    r'|期间申购|期间赎回|申购|赎回|申赎|管理费|托管费|费率|基金评级|基金管理人'
    r'|基金经理|任职时长|管理规模|分红|派现|换手率|折溢价|流通市值|总市值|份额变动)'
)

# ---------------------------------------------------------------------------
# 否定式声明豁免：「不涉及任何资金规模」「未记录个人持仓」这类表述
# 本身是合规声明，不是泄漏。若命中处的前文含否定词，则不阻断。
# ---------------------------------------------------------------------------
NEGATION_BEFORE = re.compile(
    r'(?:不涉及|未记录|未包含|无任何|不含|不写|不含任何|禁止|不得|未出现|未披露|无个人)'
    r'[^。；\n]{0,24}$'
)

# ---------------------------------------------------------------------------
# 机构主语豁免：personal_amount 的 A 类锚点（持有/持仓/头寸…）与 capital_amount 的
# 资金/本金/仓位/投入…机构与个人都会用。
# 基金披露里「本基金持有 4.76 亿元」「机构持仓 3.39 亿份」「我方资金 3 亿元」是公开数据，
# 不是个人头寸。
# **只**在锚点紧邻前文 ≤4 字符出现机构主语时豁免，绝不做整行豁免
# （否则「基金规模扩大。同时我持有 3 万元」会被一起放过）。
# 「我方」是资管/机构的自述口径，一并列入机构主语。
# ---------------------------------------------------------------------------
INST_SUBJECT_BEFORE = re.compile(
    r'(?:本基金|该基金|这只基金|基金|机构|公司|产品|组合|资管|理财|信托|年金|社保'
    r'|集合计划|专户|管理人|托管|保险资金|保险|北向资金|主力|QFII|外资|银行|券商'
    r'|我方)'
    r'[^\d]{0,4}$'
)

# personal_amount 锚点分三类（匹配串以锚点开头，据此分流豁免）：
#   B1 第一人称 + 的 + 所有格名词（我的持仓/我的仓位…）→ 个人头寸，照拦
#   B2 第一人称 + 自持动词（我持有/我买了…）            → 最个人化，照拦
#   A  无主语强自持动词（持有/持仓/头寸/自有…）        → 可走机构主语豁免
_FIRST_PERSON_ANCHOR = re.compile(r'^(?:我自己|自己|本人|我)')


def _is_inst_sensitive(match_text: str) -> bool:
    """命中是否属于「机构与个人通用」的无主语自持动词（A 类）。

    第一人称所有格（B1）与第一人称+动词（B2）本身就是个人化表达，不走机构豁免。
    """
    return _FIRST_PERSON_ANCHOR.match(match_text) is None

# ---------------------------------------------------------------------------
# 财报科目豁免：资产负债表/利润表/现金流量表的公开科目不是个人持仓信息
# 例如「货币资金 8888.88 亿元」「经营活动现金流量净额 +100.00 亿元」
#     「综合融资成本 2.50%」「归母净利润」等
# ⚠️ 只做【紧邻前文 ≤24 字符】的局部豁免，不做整行豁免
#    （整行豁免会放过「政策显示再贷款额度扩大。同时我持仓12000股」这类混合行）
# ---------------------------------------------------------------------------
FIN_STATEMENT_SAFE = re.compile(
    r'(?:货币资金|现金及现金等价物|账面现金|经营活动现金流|现金流量净额|现金流净额|经营现金流'
    r'|营业收入|营业总收入|归母净利润|净利润|营业成本|综合成本|融资成本|资金成本|资本成本|负债成本|存款成本'
    r'|总资产|净资产|股东权益|少数股东权益|合同负债|有息负债'
    r'|存货|预收账款|应收账款|短期借款|长期借款|应付债券'
    r'|总市值|流通市值|总股本|流通股本|每股净资产|每股收益'
    r'|毛利率|净利率|资产负债率|分红|股息|贴息|额度'
    r'|拓展金额|拿地|签约|回笼|回款|地价|楼面价|计容'
    r'|减值|拨备|担保|质押|抵押)'
    # 科目名与数字之间允许：Markdown 表格管道符 | 、空格、中文逗号/冒号、括号及其内容
    # 不允许：句号、分号、换行等断句符（否则混合句会被一起放过）
    r'[ |，,：:（(【\[]?[^。；;，,]{0,10}$'
)


def _is_fin_statement(line: str, match_start: int, match_text: str = '') -> bool:
    r"""命中是否属于财报科目披露。

    ⚠️ 关键设计（2026-10-15 三次踩坑后定稿）：
    PII 规则形如 `(?:资金|本金|...)[^\d]{0,8}(\d...)`，匹配起点落在**关键词开头**。
    以「货币资金 8888.88 亿元」为例，匹配串是 `资金 8888.88 亿`，
    match_start=2，于是 line[:2] = "货币" —— 科目名的前半段**留在匹配串之外**。

    因此上下文的正确拼法是：
        line[:match_start]  +  匹配串中"数字之前的部分"
    而非把整个匹配串拼回去（那会把数字也包进窗口，反而挤掉科目名）。

    同时要求科目名与数字之间无断句符，避免
    「货币资金 1338 亿。同时我持仓 12000 股」这类混合句被一起放过。
    """
    # 取匹配串中第一个数字之前的部分（即关键词 + 间隔符）
    m_num = re.search(r'\d', match_text)
    prefix = match_text[:m_num.start()] if m_num else match_text
    # 向前看 30 字符：科目名可能被 Markdown 表格管道符或括号内容隔开
    ctx = (line[:match_start] + prefix)[-30:]
    return FIN_STATEMENT_SAFE.search(ctx) is not None


# ---------------------------------------------------------------------------
# 政策条款豁免：政策原文中的金额/规模数字是公开政策参数，不是个人资金
# 例如「单户上限100万元」「本金年化1个百分点」等
# ⚠️ 只做【紧邻前文 ≤24 字符】的局部豁免，绝不做整行豁免
#    （整行豁免会放过「政策显示再贷款额度扩大。同时我持仓12000股」这类混合行）
# ---------------------------------------------------------------------------
POLICY_TERM_SAFE = re.compile(
    r'(?:单户上限|上限|下限|额度|规模|限额|比例|门槛|首付|贴息'
    r'|增至|提高至|提升至|扩大至|由.{0,12}增至'
    r'|再贷款|逆回购|正回购|降准|降息|基点|pct|个百分点'
    r'|注册资本|资本净额|一级资本|集中度|拨备覆盖率|净融出余额)'
    r'[^\d]{0,12}$'
)


def _is_policy_term(line: str, match_start: int) -> bool:
    """命中处前文 ≤24 字符内是否紧跟政策参数词。只做局部豁免，不做整行豁免。

    ⚠️ 不得跨句（2026-10-30 复核）：政策词与命中必须**在同一句**内。
    否则「基金规模扩大。同时我持有 3 万元」会被上一句的「规模」一起豁免，
    第一人称头寸从政策的缝隙里漏过去。
    """
    head = re.split(r'[。；;！!？?\n]', line[:match_start])[-1]
    head = head[-24:]
    return POLICY_TERM_SAFE.search(head) is not None


def _is_fin_term(line: str, match_start: int, match_end: int) -> bool:
    """判断命中处是否落在金融术语豁免区间内。"""
    for m in FIN_TERM_SAFE.finditer(line):
        if m.start() <= match_start < m.end() or m.start() < match_end <= m.end():
            return True
    return False


def mask_value(v: str) -> str:
    """脱敏显示：前4 + 后2 + 长度。短值全打码。"""
    if v is None:
        return '(null)'
    s = str(v)
    if len(s) <= 8:
        return f'{"*" * len(s)}(len={len(s)})'
    return f'{s[:4]}...{s[-2:]}(len={len(s)})'


def scan_text(text: str, path: str):
    """对单份文本跑 PII 规则。"""
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, sev, rx in PII_RULES:
            for m in rx.finditer(line):
                # 金融术语豁免：公开财务概念不算个人持仓信息
                if _is_fin_term(line, m.start(), m.end()):
                    continue
                # 否定式声明豁免：「不涉及任何资金规模」这类合规声明不算泄漏
                if NEGATION_BEFORE.search(line[:m.start()]):
                    continue
                # 政策条款豁免：政策原文的金额/额度/比例是公开参数，不是个人资金
                if _is_policy_term(line, m.start()):
                    continue
                # 财报科目豁免：资产负债表/利润表/现金流量表公开科目不是个人持仓信息
                if _is_fin_statement(line, m.start(), m.group(0)):
                    continue
                # 机构主语豁免：本基金/机构/公司…持有/资金 N 亿元是公开披露，不是个人头寸。
                # personal_amount 只对 A 类（无主语自持动词）生效；capital_amount 的
                # 资金/本金/仓位/投入…同样机构与个人通用，一并给紧邻机构主语豁免。
                # 只做紧邻局部豁免（≤4 非数字字符），绝不整行豁免。
                if name in ('personal_amount', 'capital_amount'):
                    inst_ok = (name == 'capital_amount'
                               or _is_inst_sensitive(m.group(0)))
                    if inst_ok and INST_SUBJECT_BEFORE.search(line[:m.start()]):
                        continue
                val = m.group(1) if m.groups() else m.group(0)
                hits.append({
                    'rule': name,
                    'severity': sev,
                    'file': path,
                    'line': lineno,
                    'masked': mask_value(val),
                    'context': re.sub(r'\S', '·', line[:120]),
                })
    return hits


def iter_files(target: str):
    if os.path.isfile(target):
        yield target
        return
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fn in files:
            if fn.endswith(('.md', '.txt', '.json', '.csv', '.yaml', '.yml')):
                yield os.path.join(root, fn)


def run_audit_secrets(target: str):
    """调用工作区已有的 audit-secrets.py（若存在）。"""
    ext = os.environ.get('EQUITY_SECRET_SCANNER', '')
    if not ext or not os.path.exists(ext):
        return None, ('skipped: no external scanner (set EQUITY_SECRET_SCANNER; built-in rules still run)')
    try:
        r = subprocess.run(
            [sys.executable, ext, target, '--json'],
            capture_output=True, text=True, timeout=300,
            encoding='utf-8', errors='replace')
    except Exception as e:
        return None, f'failed: {type(e).__name__}: {e}'
    if r.returncode == 2:
        return None, 'skipped(usage): ' + (r.stderr or '')[:200]
    try:
        data = json.loads(r.stdout or '{}')
    except Exception:
        return None, 'unparsable output'
    findings = data.get('findings') or data.get('hits') or []
    if not findings and isinstance(data, list):
        findings = data
    return findings, f'exit={r.returncode}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target', help='要检查的文件或目录')
    ap.add_argument('--strict', action='store_true', help='MEDIUM 也拦截')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--no-external', action='store_true', help='跳过 audit-secrets.py')
    args = ap.parse_args()

    if not os.path.exists(args.target):
        print(f'[usage] target not found: {args.target}', file=sys.stderr)
        return 2

    all_hits = list(scan_text_collect(args.target))

    external = None
    ext_note = 'not run'
    if not args.no_external:
        external, ext_note = run_audit_secrets(args.target)
        if external:
            for f in external:
                all_hits.append({
                    'rule': f.get('rule', 'external'),
                    'severity': f.get('severity', 'CRITICAL'),
                    'file': f.get('file', args.target),
                    'line': f.get('line', '?'),
                    'masked': f.get('masked') or mask_value(f.get('value')),
                    'context': (f.get('context') or '')[:120],
                })

    block_sev = ['CRITICAL', 'HIGH', 'MEDIUM'] if args.strict else ['CRITICAL', 'HIGH']
    blocking = [h for h in all_hits if h['severity'] in block_sev]

    if args.json:
        print(json.dumps({'hits': all_hits, 'blocking': len(blocking),
                          'external_scan': ext_note}, ensure_ascii=False, indent=2))
    else:
        print(f'=== 脱敏门禁：{args.target} ===')
        print(f'external(audit-secrets): {ext_note}')
        print(f'PII 规则命中: {len(all_hits)}   拦截级({"/".join(block_sev)}): {len(blocking)}')
        for h in all_hits:
            print(f'  [{h["severity"]:8}] {h["rule"]:16} '
                  f'{os.path.basename(h["file"])}:{h["line"]}  {h["masked"]}')
            if h.get('context'):
                print(f'      ctx: {h["context"]}')
        print('---')
        if blocking:
            print('❌ BLOCKED —— 发现敏感内容，禁止落盘。请脱敏后重跑。')
        else:
            print('✅ PASS —— 可落盘。')

    return 1 if blocking else 0


def scan_text_collect(target):
    out = []
    for p in iter_files(target):
        try:
            with open(p, 'r', encoding='utf-8', errors='replace') as f:
                txt = f.read()
        except Exception:
            continue
        out.extend(scan_text(txt, p))
    return out


if __name__ == '__main__':
    sys.exit(main())
