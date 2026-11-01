#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
基金数据取数器 —— fund-research 数据源头（Agent1 的唯一取数入口）

设计原则（对齐多智能体分工约定）：
  1. **只输出结构化 JSON**，每条记录字段固定：
     {指标, 值, 单位, 口径, 来源, 数据日期, 取数时间, stale, 类型, 备注}
     类型 ∈ {披露事实, 计算结果, 分析判断, 情景假设}；本脚本只产出前两种。
  2. **取不到填 null / 列入「未取得」**，禁止估算、插值、"大约"。
  3. **自动标 stale**：净值>5 日历日、季度持仓>130 日历日、月度数据>45 天。
  4. **多通道 + 降级**：东财 pingzhongdata（稳定）/ 东财基金页（稳定）/
     东财 f10 持仓归档（间歇可达）/ 腾讯行情（指数·个股日线与快照）。
  5. **披露 ≠ 当前**：季报只披露前十大，半年报/年报更完整；脚本把
     「报告期」「披露类型」写进每条持仓记录，调用方不得当成实时持仓。

用法：
  python fetch_fund.py --code 025490 --out runs/<runid>/fund.json
  python fetch_fund.py --code 025490 --out ... --peer-stocks 10 --benchmark sh000300
  python fetch_fund.py --code 025490 --out ... --rf 1.5      # 才计算夏普比率

退出码：
  0 = 成功（无论多少 null，都能写出合法 JSON）
  1 = 致命错误（全部通道都取不到基金身份与净值，写不出可用事实底稿）
  2 = 用法错误
"""

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

_PLUGIN_PKG_NAME = 'dsh-invest-research'
# 单独拷走运行、又找不到本插件 package.json 时的回落值（与 package.json 同步）。
_VERSION_FALLBACK = '0.6.6'


def _resolve_version(start=None):
    """工具版本：向上找本插件根目录的 package.json（避免与插件版本脱节）。

    **插件根 = 同时满足 `name == 'dsh-invest-research'` 且目录下存在 `assets/` 子目录**。
    这样安装路径之上碰巧出现的同名清单不会被误认；脚本可被单独拷走运行，
    找不到就回落字面量 `_VERSION_FALLBACK`。`start` 仅用于测试注入。
    """
    here = start or os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        try:
            if os.path.isdir(os.path.join(here, 'assets')):
                with open(os.path.join(here, 'package.json'), encoding='utf-8') as fh:
                    pkg = json.load(fh)
                if pkg.get('name') == _PLUGIN_PKG_NAME:
                    v = pkg.get('version')
                    if v:
                        return v
        except Exception:
            pass
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return _VERSION_FALLBACK


VERSION = _resolve_version()
TRADING_DAYS = 244

# 时点纪律底座（与 stock-research 共用：../../stock-research/tools/pit.py）
_SHARED_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '..', '..', 'stock-research', 'tools')
if _SHARED_TOOLS not in sys.path:
    sys.path.insert(0, _SHARED_TOOLS)
try:
    import pit as _pit
    build_as_of = _pit.build_as_of
    pit_disclosure_date = _pit.disclosure_date
except Exception:  # pragma: no cover - 底座缺失时降级为「不截断」，不阻断取数
    build_as_of = None
    pit_disclosure_date = None

# 陈旧阈值（日历日）
STALE_NAV_DAYS = 5
STALE_QUARTER_DAYS = 130
STALE_MONTHLY_DAYS = 45

UA = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Referer': 'https://fund.eastmoney.com/',
}
TENCENT_UA = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Referer': 'https://gu.qq.com/',
}

SRC_PZ = 'eastmoney.pingzhongdata'
SRC_PAGE = 'eastmoney.fundpage'
SRC_F10 = 'eastmoney.f10'
SRC_TX = 'tencent.quote'
SRC_CALC = 'derived:computed'

# 常见宽基/风格指数 → 腾讯行情代码（定制指数腾讯不覆盖，见 SKILL.md 环境限制）
BENCHMARK_ALIAS = {
    '沪深300': 'sh000300', '中证500': 'sh000905', '中证800': 'sh000906',
    '中证1000': 'sh000852', '中证2000': 'sh932000', '中证全指': 'sh000985',
    '上证指数': 'sh000001', '上证50': 'sh000016', '科创50': 'sh000688',
    '深证成指': 'sz399001', '创业板指': 'sz399006', '创业板50': 'sz399673',
    '国证2000': 'sz399303', '中证红利': 'sh000922', '中证军工': 'sh399967',
}

WINDOWS = [('近1周', 5), ('近1月', 21), ('近3月', 63), ('近6月', 126), ('近1年', 244)]


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _today():
    return datetime.now().strftime('%Y-%m-%d')


def _num(v):
    """把 '9.06%' / '1,234.5' / '--' / '' 转成 float 或 None。"""
    if v is None:
        return None
    s = str(v).strip().replace(',', '').replace('%', '').replace('元', '')
    if s in ('', '-', '--', '---', 'None', 'null', '暂无', '暂无数据'):
        return None
    try:
        return float(s)
    except Exception:
        return None


def _text(html_fragment):
    """去标签、去实体、压空白。"""
    s = re.sub(r'<[^>]+>', ' ', html_fragment or '')
    s = (s.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<')
          .replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'"))
    return re.sub(r'\s+', ' ', s).strip()


def _days_between(d1, d2):
    try:
        a = datetime.strptime(str(d1)[:10], '%Y-%m-%d')
        b = datetime.strptime(str(d2)[:10], '%Y-%m-%d')
        return (b - a).days
    except Exception:
        return None


def _rec(metric, value, unit, caliber, source, data_date, stale=None, note='',
         kind='披露事实'):
    """构造一条标准记录。stale 为 None 时按数据日期 + 口径自动判定。

    时点纪律：记录一律带 `披露日期`（公众首次可读之日），取不到写「未取得」，
    **不得用数据日期冒充**——重述正是靠这个字段暴露的。
    """
    if stale is None:
        stale = _calc_stale(data_date, caliber)
    return {
        '指标': metric,
        '值': value,
        '单位': unit,
        '口径': caliber,
        '来源': source,
        '数据日期': data_date,
        '披露日期': '未取得',
        '取数时间': _now(),
        'stale': stale,
        '类型': kind,
        '备注': note,
    }


def _rec_pit(metric, value, unit, caliber, source, data_date, asof=None,
             disclose_type=None, stale=None, note='', kind='披露事实'):
    """构造一条带**披露日期**且做过可见性判定的记录。

    - `披露日期` 由「数据日期 + 披露类型」推算（保守上界：监管最晚披露时限）；
      推算值在备注里标明「推算」，不得当作实际披露日期。
    - 披露日期晚于基准日的记录：强制 `stale=true`，备注注明「基准日后披露」。
      `stale` 原本只说明「标签有多旧」，不说明「内容在基准日是否可见」。
    """
    rec = _rec(metric, value, unit, caliber, source, data_date,
               stale=stale, note=note, kind=kind)
    if asof is None or not getattr(asof, 'is_set', False):
        return rec
    disclosed = pit_disclosure_date(data_date, disclose_type)
    rec['披露日期'] = disclosed
    if not asof.visible(data_date, disclose_type):
        rec['stale'] = True
        extra = '；基准日后披露，基准日不可见'
        rec['备注'] = (rec['备注'] + extra) if rec['备注'] else extra.lstrip('；')
    if disclosed != '未取得':
        tag = '；披露日期 %s（推算）' % disclosed
        rec['备注'] = (rec['备注'] + tag) if rec['备注'] else tag.lstrip('；')
    return rec


def _calc_stale(data_date, caliber):
    """按口径判定数据是否陈旧。无法解析日期 → 保守判为 True。"""
    if not data_date:
        return True
    try:
        d = datetime.strptime(str(data_date)[:10], '%Y-%m-%d')
    except Exception:
        return True
    age = (datetime.now() - d).days
    if caliber in ('时点', '净值日'):
        limit = STALE_NAV_DAYS
    elif caliber in ('季度', '披露时点'):
        limit = STALE_QUARTER_DAYS
    elif caliber in ('月度', '累计', '季报', '年报', '半年报'):
        limit = STALE_MONTHLY_DAYS
    else:
        limit = STALE_QUARTER_DAYS
    return age > limit


def _fetch(url, referer=None, decode='utf-8', timeout=20, retries=3, sleep=1.2):
    """带重试的 HTTP 取文本。中间层会间歇性断连（SSL EOF），重试是必需而非装饰。"""
    headers = dict(UA)
    if referer:
        headers['Referer'] = referer
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(decode, errors='replace')
        except Exception as e:  # noqa: BLE001 - 任何网络异常都值得重试
            last = e
            if i < retries - 1:
                time.sleep(sleep * (i + 1))
    raise last


# ---------------------------------------------------------------------------
# 通道 1：pingzhongdata（净值序列 / 规模 / 份额 / 持有人 / 资产配置 / 经理 / 申赎）
# ---------------------------------------------------------------------------

def _js_var(js, name):
    m = re.search(r'var\s+' + re.escape(name) + r'\s*=\s*(.*?);\s*(?:/\*|var\s|$)', js, re.S)
    return m.group(1).strip() if m else None


def _js_json(js, name, default=None):
    raw = _js_var(js, name)
    if raw in (None, ''):
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _ms_to_date(ms):
    """东财 pingzhongdata 的时间戳是「北京时间的当日零点」，需 +8h 后取日期。

    实测（2026-10）：成立日时间戳取 UTC 日期会得到 2025-09-22，而基金成立日是
    2025-09-23；最新净值日会得到 2026-10-15，而披露净值日是 2026-10-16。
    整体错一天会让净值与指数错位对齐，跟踪误差/捕获率全部失真。
    """
    try:
        return datetime.utcfromtimestamp(float(ms) / 1000.0 + 8 * 3600).strftime('%Y-%m-%d')
    except Exception:
        return None


def _qt_time_to_date(qt_time):
    """腾讯行情时间 '20261016161438' 或 '20261016' → '2026-10-16'。"""
    s = (str(qt_time or '').strip())[:8]
    if re.match(r'^\d{8}$', s):
        return '%s-%s-%s' % (s[:4], s[4:6], s[6:8])
    return _today()


def fetch_pingzhongdata(code, channels):
    url = 'https://fund.eastmoney.com/pingzhongdata/%s.js' % code
    try:
        js = _fetch(url, retries=3)
        channels.append(SRC_PZ)
    except Exception as e:
        channels.append('%s:FAILED(%s)' % (SRC_PZ, type(e).__name__))
        return {}

    out = {
        'name': (_js_var(js, 'fS_name') or '').strip('"'),
        'code': (_js_var(js, 'fS_code') or '').strip('"'),
        'source_rate': _num((_js_var(js, 'fund_sourceRate') or '').strip('"')),
        'rate': _num((_js_var(js, 'fund_Rate') or '').strip('"')),
        'min_purchase': _num((_js_var(js, 'fund_minsg') or '').strip('"')),
        'stock_codes': _js_json(js, 'stockCodesNew', _js_json(js, 'stockCodes', [])),
        'bond_codes': _js_json(js, 'zqCodesNew', _js_json(js, 'zqCodes', [])),
        'stage_returns': {},
        'nav': [],
        'ac_nav': [],
        'dividends': [],
        'grand_total': [],
        'rank': [],
        'scale': [],
        'holders': {},
        'asset_alloc': {},
        'managers': [],
        'buy_sedemption': {},
        'evaluation': {},
        'raw_ok': True,
    }

    for label, var in (('近1月', 'syl_1y'), ('近3月', 'syl_3y'), ('近6月', 'syl_6y'),
                       ('近1年', 'syl_1n')):
        out['stage_returns'][label] = _num((_js_var(js, var) or '').strip('"'))

    nav = _js_json(js, 'Data_netWorthTrend', []) or []
    for p in nav:
        d = _ms_to_date(p.get('x'))
        if not d:
            continue
        out['nav'].append((d, _num(p.get('y')), _num(p.get('equityReturn'))))
        money = (p.get('unitMoney') or '').strip()
        if money:
            out['dividends'].append({'日期': d, '说明': money})

    ac = _js_json(js, 'Data_ACWorthTrend', []) or []
    for p in ac:
        if isinstance(p, list) and len(p) >= 2:
            d = _ms_to_date(p[0])
            if d:
                out['ac_nav'].append((d, _num(p[1])))

    # Data_grandTotal 是「累计收益率(%)」序列（本基金 / 同类平均 / 沪深300），
    # 先转成增长因子，后续区间收益与成立以来收益才能用同一套比值口径计算。
    for s in _js_json(js, 'Data_grandTotal', []) or []:
        series = []
        for p in (s.get('data') or []):
            if isinstance(p, list) and len(p) >= 2:
                d = _ms_to_date(p[0])
                pct = _num(p[1])
                if d and pct is not None:
                    series.append((d, 1.0 + pct / 100.0))
        if series:
            out['grand_total'].append({'名称': s.get('name') or '未命名', '序列': series,
                                       '原始口径': '累计收益率(%)，已转为增长因子'})

    for p in _js_json(js, 'Data_rateInSimilarType', []) or []:
        d = _ms_to_date(p.get('x'))
        if d and _num(p.get('y')) is not None:
            out['rank'].append((d, int(_num(p.get('y'))), _num(p.get('sc'))))

    scale = _js_json(js, 'Data_fluctuationScale', {}) or {}
    for i, cat in enumerate(scale.get('categories') or []):
        try:
            out['scale'].append((cat, _num(scale['series'][i].get('y'))))
        except Exception:
            continue

    hold = _js_json(js, 'Data_holderStructure', {}) or {}
    for s in hold.get('series') or []:
        for i, cat in enumerate(hold.get('categories') or []):
            try:
                out['holders'].setdefault(cat, {})[s.get('name')] = _num(s['data'][i])
            except Exception:
                continue

    alloc = _js_json(js, 'Data_assetAllocation', {}) or {}
    for s in alloc.get('series') or []:
        for i, cat in enumerate(alloc.get('categories') or []):
            try:
                out['asset_alloc'].setdefault(cat, {})[s.get('name')] = _num(s['data'][i])
            except Exception:
                continue

    for m in _js_json(js, 'Data_currentFundManager', []) or []:
        rec = {'姓名': m.get('name'), '任职时长': m.get('workTime'),
               '管理规模': m.get('fundSize'), '任职起始': None,
               '任期收益%': None, '同类平均收益%': None, '基准收益%': None,
               '对比基准': None}
        for s in (m.get('profit', {}).get('series') or []):
            cats = m.get('profit', {}).get('categories') or ['任期收益', '同类平均', '沪深300']
            pts = s.get('data') or []
            if not pts:
                continue
            tag = s.get('name') or (cats[0] if len(pts) == 3 else '任期收益')
            if not rec['任职起始']:
                rec['任职起始'] = pts[0].get('name') if len(pts) == 1 else None
            if tag in ('任期收益', '本基金', rec['姓名']):
                rec['任期收益%'] = _num(pts[0].get('y'))
            for c, p in zip(cats, pts):
                key = {'任期收益': '任期收益%', '同类平均': '同类平均收益%',
                       '沪深300': '基准收益%'}
                if c in key and _num(p.get('y')) is not None:
                    rec[key[c]] = _num(p.get('y'))
            if rec['对比基准'] is None and len(pts) >= 3:
                rec['对比基准'] = '沪深300(对比)'
        out['managers'].append(rec)

    for s in ((_js_json(js, 'Data_buySedemption', {}) or {}).get('series') or []):
        out['buy_sedemption'][s.get('name')] = s.get('data')
    out['buy_sedemption']['categories'] = (
        (_js_json(js, 'Data_buySedemption', {}) or {}).get('categories'))

    out['evaluation'] = _js_json(js, 'Data_performanceEvaluation', {}) or {}
    return out


# ---------------------------------------------------------------------------
# 通道 2：基金主页（类型 / 风险等级 / 规模 / 经理 / 成立日 / 管理人 / 评级 /
#           跟踪标的与年化跟踪误差 / 前十大持仓 / 债券持仓 / 交易状态）
# ---------------------------------------------------------------------------

def _first(pattern, text, group=1, flags=0):
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else None


def fetch_fundpage(code, channels):
    url = 'https://fund.eastmoney.com/%s.html' % code
    try:
        html = _fetch(url, retries=3)
        channels.append(SRC_PAGE)
    except Exception as e:
        channels.append('%s:FAILED(%s)' % (SRC_PAGE, type(e).__name__))
        return {}

    out = {'url': url}
    out['name'] = _first(r'class="fix_fname">([^<]+)<', html) or _first(r'<title>([^<_(]+)', html)
    out['kind'] = _first(r'类型：<a[^>]*>([^<]+)</a>', html)
    out['risk_level'] = _first(r'类型：<a[^>]*>[^<]+</a>\s*(?:&nbsp;)*\|\s*(?:&nbsp;)*([^<]+)<', html)
    out['scale'] = _num(_first(r'规模</a>：([\d.]+)亿元', html))
    out['scale_date'] = _first(r'规模</a>：[^（(]*[（(](\d{4}-\d{2}-\d{2})[）)]', html)
    out['manager'] = _first(r'基金经理：<a[^>]*>([^<]+)</a>', html)
    out['inception'] = _first(r'成\s*立\s*日</span>：\s*(\d{4}-\d{2}-\d{2})', html)
    out['company'] = _first(r'管\s*理\s*人</span>：<a[^>]*>([^<]+)</a>', html)
    out['rating'] = _first(r'基金评级</a><span[^>]*>：</span><div class="jjpj">([^<]*)</div>', html)
    out['tracking_target'] = _first(r'跟踪标的：</a>\s*([^|<]{1,60}?)\s*(?:<|\|)', html)
    out['tracking_error'] = _num(_first(r'年化跟踪误差：</a>\s*([\d.\-]+)%', html))
    out['nav_unit'] = _num(_first(r'单位净值</a></span>\s*\(</span>\s*\d{4}-\d{2}-\d{2}\)</p></dt>'
                                 r'<dd class="dataNums"><span[^>]*>([\d.]+)</span>', html))
    out['nav_date'] = _first(r'单位净值</a></span>\s*\(</span>\s*(\d{4}-\d{2}-\d{2})\)', html)
    out['nav_daily_pct'] = _num(_first(r'单位净值</a></span>\s*\(</span>\s*\d{4}-\d{2}-\d{2}\)</p></dt>'
                                       r'<dd class="dataNums"><span[^>]*>[\d.]+</span>'
                                       r'<span[^>]*>([\d.\-]+)%</span>', html))
    out['ac_nav'] = _num(_first(r'累计净值</a></span></span></p></dt>'
                              r'<dd class="dataNums"><span[^>]*>([\d.]+)</span>', html))
    out['trade_status'] = re.findall(r'交易状态：</span>(?:<span[^>]*>)?\s*([^<]{1,12})', html)
    out['nav_recent'] = _parse_nav_table(html)
    top_holdings, holdings_date_hint = _parse_position_block(html, 'position_shares')
    out['top_holdings'] = top_holdings
    out['holdings_date_hint'] = holdings_date_hint
    out['bond_holdings'] = _parse_position_block(html, 'position_bonds')[0]
    out['report_period_hint'] = _first(
        r'([\d]{4}年[1-4]季度|[0-9]{4}年(?:中期|年度))[^<]{0,20}(?:股票|债券)?投资明细', html)
    return out


def _parse_nav_table(html):
    """主页「历史净值」小表：日期 | 单位净值 | 累计净值 | 日增长率。"""
    rows = []
    m = re.search(r'<th class="alignLeft">日期</th>(.*?)</table>', html, re.S)
    if not m:
        return rows
    for tr in re.findall(r'<tr>(.*?)</tr>', m.group(1), re.S):
        cells = [_text(c) for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
        if len(cells) >= 2 and re.match(r'^\d{2}-\d{2}$', cells[0]):
            rows.append({'日期': cells[0], '单位净值': _num(cells[1]),
                         '累计净值': _num(cells[2]) if len(cells) > 2 else None,
                         '日增长率%': _num(cells[3].replace('%', '')) if len(cells) > 3 else None})
    return rows


def _parse_position_block(html, block_id):
    """解析主页 持仓 表格：股票/债券名称 + 代码 + 占净值比例。

    返回 (rows, date_hint)：date_hint 取页面「截至 YYYY-MM-DD」「YYYY年M季度」
    等显式披露日期；主页多数情况没有显式日期，则返回 None（报告期由 f10 或
    最新净值日推断，已在主流程显式标注为「主页最新披露、未取得精确报告期」）。
    """
    i = html.find("id='%s'" % block_id)
    if i < 0:
        i = html.find('id="%s"' % block_id)
    if i < 0:
        return [], None
    head = html[max(0, i - 1500): i]
    date_hint = None
    m = re.search(r'截至\s*(\d{4}-\d{2}-\d{2})', head)
    if m:
        date_hint = m.group(1)
    if not date_hint:
        m = re.search(r'(\d{4})年([1-4])季度', head)
        if m:
            date_hint = '%s-%02d-30' % (m.group(1), int(m.group(2)) * 3)
    seg = html[i:i + 12000]
    end = seg.find('</table>')
    if end > 0:
        seg = seg[:end]
    rows = []
    for tr in re.findall(r'<tr>(.*?)</tr>', seg, re.S):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        if len(cells) < 2:
            continue
        name = _text(cells[0])
        weight = None
        for c in cells[1:]:
            t = _text(c)
            if t.endswith('%'):
                weight = _num(t)
                break
        if not name or weight is None:
            continue
        mkt = re.search(r'/r/(\d)\.(\d{6})', cells[0])
        rows.append({
            '名称': name,
            '代码': mkt.group(2) if mkt else None,
            '市场': ('sh' if mkt and mkt.group(1) == '1' else 'sz') if mkt else None,
            '占净值比%': weight,
        })
    return rows, date_hint


# ---------------------------------------------------------------------------
# 通道 3：f10 持仓归档（多期前十 / 半年报年报全量 / 行业配置 / 债券持仓）
# ---------------------------------------------------------------------------

def fetch_f10(code, channels, kind='jjcc', year=None, topline=100):
    """type=jjcc 股票持仓 | hydb 行业配置 | zqcc 债券持仓。间歇可达，失败即缺口。

    返回 {'periods': [{'label', 'report_date', 'rows'}], 'errors': [...]}。
    报告期优先取页面「截止至：YYYY-MM-DD」，比从「2026年2季度」推算可靠。
    """
    url = ('https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=%s&code=%s'
           '&topline=%d&year=%s&month=' % (kind, code, topline, year or ''))
    try:
        raw = _fetch(url, referer='https://fundf10.eastmoney.com/', retries=4, sleep=1.5)
    except Exception as e:
        channels.append('%s:%s%s:FAILED(%s)' % (SRC_F10, kind, year or '', type(e).__name__))
        return {'periods': [], 'errors': [type(e).__name__]}
    channels.append('%s:%s%s' % (SRC_F10, kind, year or ''))

    unescaped = (raw.replace("\\'", "'").replace('\\"', '"').replace('\\/', '/')
                    .replace('\\r', '').replace('\\n', '').replace('\\t', ''))
    m = re.search(r'content:"(.*?)",arryear', unescaped, re.S)
    body = m.group(1) if m else unescaped

    periods = []
    for block in re.findall(r"<div class='box'>[\s\S]*?(?=<div class='box'>|$)", body):
        labels = re.findall(r'<label[^>]*>(.*?)</label>', block, re.S)
        label = _text(labels[0]) if labels else ''
        report_date = _first(r'截止至：[^<]*<font[^>]*>(\d{4}-\d{2}-\d{2})</font>', block)
        if not report_date:
            report_date, _t = _period_from_label(label)
        rows = []
        for tr in re.findall(r'<tr>(.*?)</tr>', block, re.S):
            cells = [_text(c) for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
            if len(cells) < 3 or not re.match(r'^\d+$', cells[0]):
                continue
            rows.append(cells)
        if rows:
            periods.append({'label': label, 'report_date': report_date, 'rows': rows})
    return {'periods': periods, 'errors': [], 'url': url}


def fetch_f10_multi(code, channels, kind='jjcc', years=2, topline=100):
    """抓最近 `years` 个年度的归档并合并（去重按报告期，新的优先）。

    东财一次只返回一个年度的各期明细；多期对比（季报轮动、半年报全量）需要跨年。
    """
    cur = datetime.now().year
    merged, errors = [], []
    for y in range(cur, cur - max(1, years) - 1, -1):
        payload = fetch_f10(code, channels, kind, year=y, topline=topline)
        errors.extend(payload.get('errors') or [])
        merged.extend(payload.get('periods') or [])
    seen, out = set(), []
    for p in merged:
        key = p.get('report_date') or p.get('label')
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    out.sort(key=lambda p: (p.get('report_date') or ''), reverse=True)
    return {'periods': out, 'errors': errors}


def _period_from_label(label):
    """'2026年2季度股票投资明细' → ('2026-06-30', '季报前十大')。"""
    if not label:
        return None, None
    y = _first(r'(\d{4})年', label)
    q = _first(r'([1-4])季度', label)
    if y and q:
        return '%s-%02d-30' % (y, int(q) * 3), '季报前十大'
    if y and ('中期' in label or '半年' in label):
        return '%s-06-30' % y, '半年报'
    if y and ('年度' in label or '年报' in label):
        return '%s-12-31' % y, '年报'
    return None, None


def parse_f10_holdings(payload):
    """把 f10 的 jjcc 表格变成结构化多期持仓（报告期降序，items 为最新一期）。"""
    out = []
    for p in payload.get('periods', []):
        period = p.get('report_date')
        _p2, dtype = _period_from_label(p.get('label') or '')
        stocks = []
        for cells in p['rows']:
            code = next((c for c in cells if re.match(r'^\d{6}$', c)), None)
            name = next((c for c in cells[1:] if c and not re.match(r'^[\d.,%\-]+$', c)
                         and c != code), None)
            pct = next((_num(c) for c in cells if c.endswith('%')), None)
            nums = [_num(c) for c in cells if re.match(r'^[\d,]+\.?\d*$', c)]
            shares = nums[-2] if len(nums) >= 2 else None
            value = nums[-1] if nums else None
            if name is None and code is None:
                continue
            stocks.append({'代码': code, '名称': name, '占净值比%': pct,
                           '持股数(万股)': shares, '持仓市值(万元)': value})
        if stocks:
            # 行数 > 20 视为半年报/年报全量披露，否则是季报只披露前十大
            full = len(stocks) > 20
            out.append({'报告期': period,
                        '披露类型': ('全量披露(半年报/年报)' if full else (dtype or '季报前十大')),
                        '明细条数': len(stocks), 'label': p.get('label'), '股票': stocks})
    out.sort(key=lambda x: (x.get('报告期') or ''), reverse=True)
    return out


def parse_f10_industry(payload):
    out = []
    for p in payload.get('periods', []):
        period = p.get('report_date') or _period_from_label(p.get('label') or '')[0]
        rows = []
        for cells in p['rows']:
            if len(cells) < 3:
                continue
            name = cells[1] if not re.match(r'^[\d.,%\-]+$', cells[1]) else None
            pct = next((_num(c) for c in cells[2:] if c.endswith('%')), None)
            rows.append({'行业': name, '占净值比%': pct})
        if rows:
            out.append({'报告期': period, 'label': p.get('label'), '行业': rows})
    out.sort(key=lambda x: (x.get('报告期') or ''), reverse=True)
    return out


# ---------------------------------------------------------------------------
# 通道 4：腾讯行情（基准指数/个股日线；个股快照含 PE·PB）
# ---------------------------------------------------------------------------

def fetch_tencent_kline(symbol, days=1300, channels=None):
    url = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq'
           % (symbol, int(days)))
    try:
        txt = _fetch(url, referer='https://gu.qq.com/', retries=3)
    except Exception as e:
        if channels is not None:
            channels.append('%s:kline:%s:FAILED(%s)' % (SRC_TX, symbol, type(e).__name__))
        return []
    try:
        data = json.loads(txt).get('data', {}).get(symbol, {}) or {}
        rows = data.get('qfqday') or data.get('day') or []
    except Exception:
        return []
    series = []
    for r in rows:
        if len(r) >= 3:
            series.append((str(r[0])[:10], _num(r[2])))
    if series and channels is not None:
        channels.append('%s:kline:%s' % (SRC_TX, symbol))
    return [p for p in series if p[1] is not None]


def fetch_tencent_quotes(symbols, channels=None):
    """批量快照（GBK）。字段位：1 名称 3 最新价 31 涨跌 32 涨跌% 38 换手率
    39 PE(TTM) 44 流通市值(亿) 45 总市值(亿) 46 PB 30 时间。"""
    if not symbols:
        return {}
    out = {}
    for i in range(0, len(symbols), 30):
        chunk = symbols[i:i + 30]
        url = 'https://qt.gtimg.cn/q=' + ','.join(chunk)
        try:
            txt = _fetch(url, referer='https://gu.qq.com/', decode='gbk', retries=3)
        except Exception as e:
            if channels is not None:
                channels.append('%s:snapshot:FAILED(%s)' % (SRC_TX, type(e).__name__))
            continue
        for line in txt.split(';'):
            m = re.match(r'\s*v_([a-z]{2}\d{6})="(.*)"', line.strip())
            if not m:
                continue
            parts = m.group(2).split('~')
            if len(parts) < 46:
                continue
            out[m.group(1)] = {
                '名称': parts[1], '代码': parts[2], '最新价': _num(parts[3]),
                '昨收': _num(parts[4]), '涨跌幅%': _num(parts[32]),
                '换手率%': _num(parts[38]), 'PE(TTM)': _num(parts[39]),
                '流通市值(亿)': _num(parts[44]), '总市值(亿)': _num(parts[45]),
                'PB': _num(parts[46]), '行情时间': parts[30],
            }
    if out and channels is not None:
        channels.append('%s:snapshot' % SRC_TX)
    return out


def stock_symbol(code6, market_hint=None):
    if market_hint:
        return market_hint + code6
    if code6[:1] in ('6', '9', '5'):
        return 'sh' + code6
    if code6[:1] in ('0', '3', '1'):
        return 'sz' + code6
    if code6[:1] in ('4', '8'):
        return 'bj' + code6
    return 'sh' + code6


def resolve_benchmark(arg):
    """--benchmark 支持 sh000300 这类代码，或 '沪深300' 这类中文名。"""
    if not arg:
        return None
    a = arg.strip()
    if re.match(r'^(sh|sz|bj)\d{6}$', a):
        return a
    if re.match(r'^\d{6}$', a):
        return ('sh' if a[:1] in ('0', '9') else 'sz') + a
    # Direct lookup first (allow exact match)
    name = re.sub(r'(指数 | 全收益 | 价格)$', '', a)
    for k, v in BENCHMARK_ALIAS.items():
        if k == name or a == k: return v
    return None


# ---------------------------------------------------------------------------
# 衍生指标（纯 stdlib，口径随结果一起输出）
# ---------------------------------------------------------------------------

def _window_return(series, n):
    """series: [(date, value)] 升序。返回 n 个交易日区间收益（小数）或 None。"""
    vals = [v for _, v in series if v is not None]
    if len(vals) < 2:
        return None
    if n >= len(vals):
        n = len(vals) - 1
    if n <= 0 or vals[-1 - n] in (None, 0):
        return None
    return vals[-1] / vals[-1 - n] - 1.0


def _ytd_return(series):
    vals = [(d, v) for d, v in series if v is not None]
    if len(vals) < 2:
        return None
    year = vals[-1][0][:4]
    base = next((v for d, v in vals if d[:4] == year), None)
    if not base:
        return None
    return vals[-1][1] / base - 1.0


def _annualized(series, n=None):
    vals = [v for _, v in series if v is not None]
    if len(vals) < 2:
        return None
    if n is None or n >= len(vals):
        n = len(vals) - 1
    if n <= 0 or vals[-1 - n] in (None, 0):
        return None
    total = vals[-1] / vals[-1 - n]
    if total <= 0:
        return None
    return total ** (TRADING_DAYS / float(n)) - 1.0


def _daily_returns(series, n=None):
    vals = [v for _, v in series if v is not None]
    if n:
        vals = vals[-(n + 1):]
    return [vals[i] / vals[i - 1] - 1.0 for i in range(1, len(vals)) if vals[i - 1]]


def _stdev(xs):
    if len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _volatility(series, n=TRADING_DAYS):
    r = _daily_returns(series, n)
    sd = _stdev(r)
    return None if sd is None else sd * math.sqrt(TRADING_DAYS)


def _max_drawdown(series, n=None):
    pts = [(d, v) for d, v in series if v is not None]
    if n:
        pts = pts[-n:]
    if len(pts) < 2:
        return None
    peak_v, peak_d = pts[0][1], pts[0][0]
    best = {'最大回撤': 0.0}
    for d, v in pts:
        if v > peak_v:
            peak_v, peak_d = v, d
        dd = v / peak_v - 1.0 if peak_v else 0.0
        if dd < best['最大回撤']:
            best = {'最大回撤': dd, '回撤起点日期': peak_d, '回撤谷底日期': d,
                    '回撤起点值': round(peak_v, 6), '回撤谷底值': round(v, 6)}
    best.setdefault('回撤起点日期', peak_d)
    best.setdefault('回撤谷底日期', pts[-1][0])
    best['样本起始日'] = pts[0][0]
    best['样本结束日'] = pts[-1][0]
    best['样本长度'] = len(pts)
    return best


def _percentile(series):
    vals = [v for _, v in series if v is not None]
    if len(vals) < 2:
        return None
    cur = vals[-1]
    return round(sum(1 for v in vals if v < cur) / float(len(vals)) * 100, 2)


def compare_with_benchmark(fund_series, bench_series):
    """按日期对齐后计算：样本数、区间超额、年化跟踪误差、涨跌捕获率。"""
    fmap = {d: v for d, v in fund_series if v is not None}
    bmap = {d: v for d, v in bench_series if v is not None}
    common = sorted(set(fmap) & set(bmap))
    if len(common) < 20:
        return {'可比样本数': len(common), '可用': False,
                '说明': '共同交易日不足 20 个，无法计算跟踪指标'}
    fv = [fmap[d] for d in common]
    bv = [bmap[d] for d in common]
    fr = [fv[i] / fv[i - 1] - 1.0 for i in range(1, len(fv)) if fv[i - 1]]
    br = [bv[i] / bv[i - 1] - 1.0 for i in range(1, len(bv)) if bv[i - 1]]
    diff = [a - b for a, b in zip(fr, br)]
    sd = _stdev(diff)
    excess = (fv[-1] / fv[0] - 1.0) - (bv[-1] / bv[0] - 1.0)
    up = [(a, b) for a, b in zip(fr, br) if b > 0]
    down = [(a, b) for a, b in zip(fr, br) if b < 0]
    up_cap = (sum(a for a, _ in up) / sum(b for _, b in up)) if up and sum(b for _, b in up) else None
    dn_cap = (sum(a for a, _ in down) / sum(b for _, b in down)) if down and sum(b for _, b in down) else None
    return {
        '可用': True,
        '可比样本数': len(common),
        '起始日': common[0], '结束日': common[-1],
        '基金区间收益': round(fv[-1] / fv[0] - 1.0, 6),
        '基准区间收益': round(bv[-1] / bv[0] - 1.0, 6),
        '区间超额收益': round(excess, 6),
        '年化跟踪误差': None if sd is None else round(sd * math.sqrt(TRADING_DAYS), 6),
        '上涨捕获率': None if up_cap is None else round(up_cap, 4),
        '下跌捕获率': None if dn_cap is None else round(dn_cap, 4),
        '口径': '按基金净值日与基准收盘日对齐后计算；基金净值含费用、基准为价格指数',
    }


def infer_share_class(name):
    """从名称推断份额类别（A/C/E/I…）。

    必须排除产品形式缩写的末位字母：'某某ETF' 的 'F'、'某某QDII' 的 'I'、
    '某某FOF' 的 'F' 都不是份额类别。判据：该大写字母前一位若也是大写字母，
    说明它是缩写的一部分 → 返回 None。
    """
    s = (name or '').strip()
    m = re.search(r'([A-Z])(?:类)?$', s)
    if not m:
        return None
    if m.start() > 0 and s[m.start() - 1].isupper():
        return None
    return m.group(1)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build_payload(args):
    code = args.code.strip()
    asof = getattr(args, 'asof', None)
    channels = []
    pz = fetch_pingzhongdata(code, channels)
    page = fetch_fundpage(code, channels)
    f10_stock = fetch_f10_multi(code, channels, 'jjcc', years=args.f10_years,
                                topline=args.topline)
    f10_bond = fetch_f10_multi(code, channels, 'zqcc', years=1, topline=args.topline)
    f10_ind = fetch_f10_multi(code, channels, 'hydb', years=args.f10_years,
                              topline=args.topline)

    fund_name = pz.get('name') or page.get('name') or code
    periods = parse_f10_holdings(f10_stock)
    latest = periods[0] if periods else None
    industry = parse_f10_industry(f10_ind)
    bonds = parse_f10_holdings(f10_bond)

    nav_unit = [(d, v) for d, v, _ in pz.get('nav', [])]
    ac_nav = pz.get('ac_nav') or []
    has_div = bool(pz.get('dividends'))
    nav_series = ac_nav if (has_div and len(ac_nav) >= len(nav_unit) * 0.8) else nav_unit
    nav_caliber = ('累计净值口径（含历史分红派现，非红利再投）' if nav_series is ac_nav
                   else '单位净值口径（区间内无分红派现记录）')
    nav_full_len = len(nav_series)
    # --history-days 默认 0 = 保留全部历史（否则「成立以来」指标其实只是样本区间）
    if args.history_days and len(nav_series) > args.history_days:
        nav_series = nav_series[-args.history_days:]

    # -- 时点纪律：净值序列一律截断到分析基准日 ------------------
    # 基准日之后才发布的净值不得进入任何计算窗口（净值分位/区间收益/回撤都基于它）。
    nav_dropped = 0
    if asof is not None and asof.is_set and nav_series:
        kept, nav_dropped = asof.truncate(nav_series, date_key=0)
        nav_series = kept
    # ------------------------------------------------------------

    inception = page.get('inception')
    # 注意：`or 999` 会把 0（净值首日 == 成立日，正是我们要的正确情况）判成 falsy，
    # 于是「成立以来」被错标成「样本区间」。必须显式判 None。
    gap = _days_between(nav_series[0][0], inception) if (nav_series and inception) else None
    covers_inception = gap is not None and abs(gap) <= 10
    SINCE = '成立以来' if covers_inception else '样本区间'
    if not covers_inception and nav_series:
        note_since = '（样本自 %s 起，未覆盖成立以来%s）' % (
            nav_series[0][0], '' if not inception else '（成立日 %s）' % inception)
    else:
        note_since = ''

    last_nav_date = nav_series[-1][0] if nav_series else None
    last_nav = nav_series[-1][1] if nav_series else None
    records = []
    notes = []
    missing = []

    # ---------- 身份 ----------
    records.append(_rec('基金名称', fund_name, '文本', '时点', SRC_PZ, _today()))
    records.append(_rec('基金代码', code, '文本', '时点', SRC_PZ, _today()))
    records.append(_rec('份额类别', infer_share_class(fund_name), '文本', '时点',
                        SRC_CALC, _today(), kind='计算结果',
                        note='同名基金不同份额费率与净值不同，禁止混用'))
    records.append(_rec('基金类型', page.get('kind'), '文本', '时点', SRC_PAGE, _today()))
    records.append(_rec('风险等级', page.get('risk_level'), '文本', '时点', SRC_PAGE, _today()))
    records.append(_rec('管理人', page.get('company'), '文本', '时点', SRC_PAGE, _today()))
    records.append(_rec('成立日', page.get('inception'), '日期', '时点', SRC_PAGE, _today()))
    records.append(_rec('基金评级', page.get('rating'), '文本', '时点', SRC_PAGE, _today()))
    records.append(_rec('基金经理', page.get('manager'), '文本', '时点', SRC_PAGE, _today()))
    records.append(_rec('跟踪标的', page.get('tracking_target'), '文本', '时点', SRC_PAGE, _today(),
                        note='被动产品必填；主动产品为空属正常'))
    records.append(_rec('年化跟踪误差(披露)', page.get('tracking_error'), '%', '时点', SRC_PAGE,
                        page.get('nav_date') or _today(), note='基金公司披露口径'))
    records.append(_rec('交易状态', ' / '.join(page.get('trade_status') or []) or None, '文本',
                        '时点', SRC_PAGE, _today()))
    records.append(_rec('原申购费率', pz.get('source_rate'), '%', '时点', SRC_PZ, _today()))
    records.append(_rec('现申购费率(平台)', pz.get('rate'), '%', '时点', SRC_PZ, _today(),
                        note='第三方平台折扣价，非基金合同费率'))
    records.append(_rec('最小申购金额', pz.get('min_purchase'), '元', '时点', SRC_PZ, _today()))
    for m in pz.get('managers') or []:
        records.append(_rec('基金经理任职时长', m.get('任职时长'), '文本', '时点', SRC_PZ, _today(),
                            note='%s；管理规模 %s' % (m.get('姓名'), m.get('管理规模'))))
        if m.get('任期收益%') is not None:
            records.append(_rec('基金经理任期收益', m.get('任期收益%'), '%', '累计', SRC_PZ,
                                _today(), kind='计算结果',
                                note='%s 任职起始 %s；对比基准 %s' % (
                                    m.get('姓名'), m.get('任职起始') or '未取得',
                                    m.get('对比基准') or '未取得')))
        if m.get('同类平均收益%') is not None:
            records.append(_rec('基金经理任期同类平均收益', m.get('同类平均收益%'), '%', '累计',
                                SRC_PZ, _today(), note='%s' % m.get('姓名')))
        if m.get('基准收益%') is not None:
            records.append(_rec('基金经理任期基准收益', m.get('基准收益%'), '%', '累计', SRC_PZ,
                                _today(), note='%s；%s' % (m.get('姓名'), m.get('对比基准'))))

    # ---------- 净值与规模 ----------
    records.append(_rec('最新单位净值', page.get('nav_unit') or last_nav, '元', '净值日',
                        SRC_PAGE if page.get('nav_unit') else SRC_PZ,
                        page.get('nav_date') or last_nav_date, note='页面披露值'))
    records.append(_rec('最新累计净值', page.get('ac_nav'), '元', '净值日', SRC_PAGE,
                        page.get('nav_date') or last_nav_date))
    records.append(_rec('最新净值日', last_nav_date, '日期', '净值日', SRC_PZ, last_nav_date))
    records.append(_rec('净值样本数', len(nav_series), '个', '区间', SRC_PZ,
                        last_nav_date, kind='计算结果',
                        note='%s 至 %s' % (nav_series[0][0] if nav_series else '-', last_nav_date)))
    records.append(_rec('基金规模', page.get('scale'), '亿元', '季度', SRC_PAGE,
                        page.get('scale_date') or _today()))
    for date_, yi in (pz.get('scale') or [])[-4:]:
        records.append(_rec('基金规模(份额序列)', yi, '亿元', '季度', SRC_PZ, date_))
    for date_, holders in sorted((pz.get('holders') or {}).items()):
        for k, v in holders.items():
            records.append(_rec(k, v, '%', '半年报', SRC_PZ, date_))
    for date_, alloc in sorted((pz.get('asset_alloc') or {}).items()):
        for k, v in alloc.items():
            records.append(_rec(k, v, '%' if '占净比' in str(k) else '亿元', '季度',
                                SRC_PZ, date_))
    cats = pz.get('buy_sedemption', {}).get('categories') or []
    for k, vals in (pz.get('buy_sedemption') or {}).items():
        if k == 'categories' or not isinstance(vals, list):
            continue
        for i, v in enumerate(vals):
            if i < len(cats) and v is not None:
                records.append(_rec('份额变动:%s' % k, v, '亿份', '季度', SRC_PZ, cats[i]))

    # ---------- 持仓 ----------
    top_holdings = page.get('top_holdings') or []
    known_weight = 0.0
    holding_source = None
    if latest and latest.get('股票'):
        holding_source = 'f10归档'
        stocks = latest['股票']
        for s in stocks:
            if s.get('占净值比%') is not None:
                known_weight += s['占净值比%']
    else:
        holding_source = '基金主页(最新披露)'
        stocks = [{'代码': h.get('代码'), '名称': h.get('名称'), '占净值比%': h.get('占净值比%'),
                   '持股数(万股)': None, '持仓市值(万元)': None} for h in top_holdings]
        known_weight = sum(h['占净值比%'] for h in stocks if h.get('占净值比%') is not None)
    report_period = (latest or {}).get('报告期') or page.get('holdings_date_hint') \
        or page.get('report_period_hint')
    if report_period is None and stocks:
        # 最新一期持仓披露滞后于季度末（监管要求：季报 15 个工作日、半年报 2 个月、
        # 年报 3 个月内披露）。推断规则：只认「季度末 + 25 个日历日 ≤ 最新净值日」的
        # 季度末，取其中最新者 —— 这是最晚被保证已披露的报告期。
        # 实测教训：按「最新净值日所在的最近季度末」正推会得出 2026-09-30，
        # 而 10 月中旬三季报尚未披露，主页持仓实际是 2026-06-30 的。
        nav_last = nav_unit[-1][0] if nav_unit else None
        if nav_last:
            try:
                nav_d = datetime.strptime(nav_last, '%Y-%m-%d')
                y = nav_d.year
                cands = []
                for yy in (y - 1, y):
                    for md in ('03-31', '06-30', '09-30', '12-31'):
                        qd = datetime.strptime('%d-%s' % (yy, md), '%Y-%m-%d')
                        if (nav_d - qd).days >= 25:
                            cands.append((qd, '%d-%s' % (yy, md)))
                if cands:
                    report_period = max(cands)[1]
            except Exception:
                pass
    disclose_type = (latest or {}).get('披露类型') or (
        ('基金主页最新披露%s' % ('，报告期推断' if report_period and not (
            latest or {}).get('报告期') and not page.get('holdings_date_hint') else ''))
        if stocks else None)

    # -- 时点纪律：报告期晚于基准日的持仓不得进入事实底稿 --------------
    # f10 归档给的是「当前可见的最新一期」，不是「基准日当时已公开的一期」。
    # 若不判定，基准日 6 月的报告可能引用 10 月才披露的三季报持仓。
    holdings_visible = True
    holdings_visibility_note = ''
    if asof is not None and asof.is_set and report_period:
        # 披露类型映射到滞后规则：季报前十大 / 全量披露(半年报/年报)
        lag_type = '半年报' if '全量' in str(disclose_type) else '季报'
        holdings_visible = asof.visible(report_period, lag_type)
        if not holdings_visible:
            holdings_visibility_note = (
                '报告期 %s 预计最早公开日 %s，晚于分析基准日 %s —— 该期持仓不得使用，'
                '应改用上一期并在数据缺口写明「最新一期尚未公开」' % (
                    report_period,
                    pit_disclosure_date(report_period, lag_type) if pit_disclosure_date else '未取得',
                    asof.iso))
            # 退回上一期（若存在）
            visible_periods = [p for p in periods
                               if asof.visible(p.get('报告期'),
                                               '半年报' if '全量' in str(p.get('披露类型')) else '季报')]
            if visible_periods:
                fallback = visible_periods[0]
                report_period = fallback.get('报告期')
                disclose_type = fallback.get('披露类型') or disclose_type
                stocks = fallback.get('股票') or []
                holdings_visibility_note += '；已退回使用上一期报告期 %s' % report_period
            else:
                stocks = []
                holdings_visibility_note += '；无可见的历史报告期，持仓明细置空'
    # ------------------------------------------------------------------

    stock_total = None
    stock_total_date = None
    for date_, alloc in sorted((pz.get('asset_alloc') or {}).items()):
        if alloc.get('股票占净比') is not None:
            stock_total, stock_total_date = alloc['股票占净比'], date_
    for s in stocks:
        records.append(_rec_pit('持仓:%s' % (s.get('名称') or s.get('代码')), s.get('占净值比%'),
                                '%', '披露时点', SRC_F10 if holding_source == 'f10归档' else SRC_PAGE,
                                report_period or last_nav_date, asof=asof,
                                disclose_type=('半年报' if '全量' in str(disclose_type) else '季报'),
                                note='%s；%s；报告期 %s%s' % (
                                    holding_source, disclose_type, report_period or '未取得',
                                    ('；' + holdings_visibility_note) if holdings_visibility_note else '')))

    if stocks:
        records.append(_rec('已披露股票合计占净值比', round(known_weight, 4), '%', '披露时点',
                            SRC_CALC, report_period or last_nav_date, kind='计算结果',
                            note='仅覆盖已披露明细，不得视为全组合'))
        records.append(_rec('前十大合计占净值比', round(sum(
            sorted([s.get('占净值比%') or 0 for s in stocks], reverse=True)[:10]), 4), '%',
            '披露时点', SRC_CALC, report_period or last_nav_date, kind='计算结果'))
        if stock_total is not None:
            records.append(_rec('未披露明细的股票仓位(推算)', round(stock_total - known_weight, 4),
                                '%', '披露时点', SRC_CALC, report_period or last_nav_date,
                                kind='计算结果',
                                note='= 股票占净比(%s@%s) - 已披露明细合计；为推算值，'
                                     '不得当作确定性行业暴露' % (stock_total, stock_total_date)))
    # ---------- 联接基金 / FOF 穿透提示（防重复计数） ----------
    structure = ' '.join(filter(None, [page.get('kind') or '', fund_name or '']))
    is_feeder = any(k in structure for k in ('联接', 'FOF', '基金中基金'))
    if is_feeder:
        records.append(_rec(
            '穿透提示:结构', '联接/FOF：f10 股票持仓不含基金投资（目标ETF或子基金）',
            '文本', '时点', SRC_CALC, report_period or last_nav_date, kind='计算结果',
            note='必须单独识别目标 ETF / 子基金持仓；不得把目标 ETF 与其底层股票重复计入；'
                 '本基金股票占净比 %s%% 只是直接持股部分' % stock_total))
        missing.append('目标 ETF / 子基金持仓（联接或 FOF 的基金投资部分，需 read.fetch 定期报告补）')

    for b in (bonds[0]['股票'] if bonds else []):
        records.append(_rec('债券持仓:%s' % (b.get('名称') or b.get('代码')), b.get('占净值比%'),
                            '%', '披露时点', SRC_F10, (bonds[0].get('报告期') or last_nav_date)))
    for row in (industry[0]['行业'] if industry else []):
        records.append(_rec('行业配置:%s' % row.get('行业'), row.get('占净值比%'), '%',
                            '披露时点', SRC_F10, (industry[0].get('报告期') or last_nav_date),
                            note='f10 行业分类口径（申万/中证以页面标注为准）'))

    # ---------- 区间收益 / 风险指标 ----------
    if nav_series:
        for label, n in WINDOWS:
            r = _window_return(nav_series, n)
            records.append(_rec('区间收益:%s' % label, None if r is None else round(r * 100, 4),
                                '%', '区间', SRC_CALC, last_nav_date, kind='计算结果',
                                note=nav_caliber))
        ytd = _ytd_return(nav_series)
        records.append(_rec('区间收益:今年以来', None if ytd is None else round(ytd * 100, 4),
                            '%', '区间', SRC_CALC, last_nav_date, kind='计算结果', note=nav_caliber))
        since = nav_series[-1][1] / nav_series[0][1] - 1.0 if nav_series[0][1] else None
        records.append(_rec('区间收益:%s' % SINCE, None if since is None else round(since * 100, 4),
                            '%', '区间', SRC_CALC, last_nav_date, kind='计算结果',
                            note='%s；%s%s' % (nav_caliber, nav_series[0][0], note_since)))
        ann = _annualized(nav_series)
        records.append(_rec('年化收益:%s' % SINCE, None if ann is None else round(ann * 100, 4),
                            '%', '区间', SRC_CALC, last_nav_date, kind='计算结果',
                            note='按 244 交易日/年折算；%s%s' % (nav_caliber, note_since)))
        ann1 = _annualized(nav_series, 244)
        records.append(_rec('年化收益:近1年', None if ann1 is None else round(ann1 * 100, 4),
                            '%', '区间', SRC_CALC, last_nav_date, kind='计算结果',
                            note='按 244 交易日/年折算；%s' % nav_caliber))
        vol = _volatility(nav_series, TRADING_DAYS)
        records.append(_rec('年化波动率:近1年', None if vol is None else round(vol * 100, 4),
                            '%', '区间', SRC_CALC, last_nav_date, kind='计算结果',
                            note='日收益样本标准差×sqrt(244)；%s' % nav_caliber))
        mdd = _max_drawdown(nav_series)
        if mdd:
            records.append(_rec('最大回撤:%s' % SINCE, round(mdd['最大回撤'] * 100, 4), '%', '区间',
                                SRC_CALC, mdd['回撤谷底日期'], kind='计算结果',
                                note='%s→%s；%s%s' % (mdd['回撤起点日期'], mdd['回撤谷底日期'],
                                                   nav_caliber, note_since)))
        mdd1 = _max_drawdown(nav_series, TRADING_DAYS)
        if mdd1:
            records.append(_rec('最大回撤:近1年', round(mdd1['最大回撤'] * 100, 4), '%', '区间',
                                SRC_CALC, mdd1['回撤谷底日期'], kind='计算结果', note=nav_caliber))
        pct = _percentile(nav_series)
        # 净值分位的样本窗口终点必须写出来：窗口终点若不披露，
        # 「基准日」与「最新可得净值日」的差异就不可见（分位口径也就无法复核）。
        nav_window_note = '样本窗口 %s → %s（%d 个净值点）' % (
            nav_series[0][0] if nav_series else '-',
            last_nav_date or '-', len(nav_series))
        if asof is not None and asof.is_set:
            lag = _days_between(last_nav_date, asof.iso) if last_nav_date else None
            nav_window_note += '；分析基准日 %s' % asof.iso
            if lag:
                nav_window_note += ('；基准日净值已披露' if lag <= 0 else
                                    '；基准日净值未披露，最新可得净值日滞后 %d 天' % lag)
        records.append(_rec('净值分位:%s' % SINCE, pct, '%', '区间', SRC_CALC, last_nav_date,
                            kind='计算结果',
                            note='严格小于当前净值的样本占比（与 stock-research 的 '
                                 'compute_percentile 同口径）；≠估值分位%s；%s'
                                 % (note_since, nav_window_note)))
        if args.rf is not None and ann1 is not None and vol:
            sharpe = (ann1 * 100 - args.rf) / (vol * 100)
            records.append(_rec('夏普比率:近1年', round(sharpe, 4), '比率', '区间', SRC_CALC,
                                last_nav_date, kind='计算结果',
                                note='无风险利率取 %.2f%%（用户传入假设值）；%s' % (args.rf, nav_caliber)))
        else:
            notes.append('夏普比率未计算：未提供 --rf 无风险利率假设（禁止用默认值冒充）')
        for date_, rank, total in (pz.get('rank') or [])[-1:]:
            records.append(_rec('同类排名', '%s/%s' % (int(rank), int(total) if total else '?'),
                                '文本', '时点', SRC_PZ, date_, note='天天基金同类排名口径'))

    for g in pz.get('grand_total') or []:
        for label, n in WINDOWS + [('成立以来', None)]:
            if n is None:
                r = (g['序列'][-1][1] - 1.0) if g['序列'] else None
            else:
                r = _window_return(g['序列'], n)
            if r is None:
                continue
            records.append(_rec('对比序列:%s:%s' % (g['名称'], label), round(r * 100, 4), '%',
                                '区间', SRC_PZ, g['序列'][-1][0],
                                note='天天基金对比序列（累计收益口径，非同类排名）'))

    # ---------- 基准与跟踪 ----------
    benchmark = resolve_benchmark(args.benchmark) if args.benchmark else None
    bench_series = fetch_tencent_kline(benchmark, args.benchmark_days, channels) if benchmark else []
    bench_block = None
    cmp_block = None
    if benchmark:
        if bench_series:
            bm = _max_drawdown(bench_series)
            records.append(_rec('基准指数最新收盘', bench_series[-1][1], '点', '时点', SRC_TX,
                                bench_series[-1][0]))
            records.append(_rec('基准指数近1年收益',
                                None if _window_return(bench_series, 244) is None else round(
                                    _window_return(bench_series, 244) * 100, 4),
                                '%', '区间', SRC_CALC, bench_series[-1][0], kind='计算结果'))
            if bm:
                records.append(_rec('基准指数最大回撤(样本区间)', round(bm['最大回撤'] * 100, 4), '%',
                                    '区间', SRC_CALC, bm['回撤谷底日期'], kind='计算结果',
                                    note='%s→%s' % (bm['回撤起点日期'], bm['回撤谷底日期'])))
            records.append(_rec('基准指数分位(样本区间)', _percentile(bench_series), '%', '区间',
                                SRC_CALC, bench_series[-1][0], kind='计算结果'))
            bench_block = {'名称': args.benchmark, '代码': benchmark,
                           '起始日': bench_series[0][0], '结束日': bench_series[-1][0],
                           '样本数': len(bench_series),
                           '序列': [[d, v] for d, v in bench_series]}
            if nav_series:
                cmp_block = compare_with_benchmark(nav_series, bench_series)
                if cmp_block.get('可用'):
                    records.append(_rec('区间超额收益(vs基准)', round(
                        cmp_block['区间超额收益'] * 100, 4), '%', '区间', SRC_CALC,
                        cmp_block['结束日'], kind='计算结果',
                        note='共同交易日 %d 个；%s' % (cmp_block['可比样本数'], cmp_block['口径'])))
                    if cmp_block.get('年化跟踪误差') is not None:
                        records.append(_rec('年化跟踪误差(计算)', round(
                            cmp_block['年化跟踪误差'] * 100, 4), '%', '区间', SRC_CALC,
                            cmp_block['结束日'], kind='计算结果',
                            note='日超额收益标准差×sqrt(244)；与披露值口径可能不同，须并列'))
                    for k in ('上涨捕获率', '下跌捕获率'):
                        if cmp_block.get(k) is not None:
                            records.append(_rec(k, cmp_block[k], '比率', '区间', SRC_CALC,
                                                cmp_block['结束日'], kind='计算结果'))
                else:
                    notes.append('跟踪指标不可用：%s' % cmp_block.get('说明'))
        else:
            notes.append('基准指数 %s 行情未取得（腾讯通道不覆盖中证/国证定制指数，'
                         'push2 通道不可达）→ 记为数据缺口，可用基金净值近似并标注'
                         % args.benchmark)

    # ---------- 重仓股快照 ----------
    peers = []
    if args.peer_stocks and stocks:
        symbols = []
        by_symbol = {}
        for s in stocks[:args.peer_stocks]:
            c = (s.get('代码') or '').strip()
            if not re.match(r'^\d{6}$', c):
                continue
            sym = stock_symbol(c)
            symbols.append(sym)
            by_symbol[sym] = s
        quotes = fetch_tencent_quotes(symbols, channels)
        for sym in symbols:
            q = quotes.get(sym)
            if not q:
                continue
            rec = {'代码': q.get('代码'), '名称': q.get('名称'),
                   '占净值比%': (by_symbol[sym] or {}).get('占净值比%'),
                   '最新价': q.get('最新价'), '涨跌幅%': q.get('涨跌幅%'),
                   'PE(TTM)': q.get('PE(TTM)'), 'PB': q.get('PB'),
                   '总市值(亿)': q.get('总市值(亿)'), '行情时间': q.get('行情时间'),
                   '行情日期': _qt_time_to_date(q.get('行情时间'))}
            kl = fetch_tencent_kline(sym, 250, channels)
            if kl:
                rec['近1年收益%'] = None if _window_return(kl, 244) is None else round(
                    _window_return(kl, 244) * 100, 2)
                rec['近1年最大回撤%'] = None if not _max_drawdown(kl, 244) else round(
                    _max_drawdown(kl, 244)['最大回撤'] * 100, 2)
                rec['价格分位(250日)%'] = _percentile(kl)
                rec['K线数据日期'] = kl[-1][0]
            peers.append(rec)
        for p in peers:
            for k, unit in (('PE(TTM)', '倍'), ('PB', '倍'), ('总市值(亿)', '亿元'),
                            ('近1年收益%', '%'), ('近1年最大回撤%', '%'),
                            ('价格分位(250日)%', '%')):
                if p.get(k) is not None:
                    records.append(_rec('重仓股:%s:%s' % (p['名称'], k), p[k], unit, '时点',
                                        SRC_TX, p.get('行情日期') or _today(),
                                        note='权重 %s%%（%s）' % (p.get('占净值比%'),
                                                                 report_period or '未取得')))

    # ---------- 缺口清单 ----------
    if not nav_series:
        missing.append('基金净值序列（pingzhongdata 通道失败）')
    if not stocks:
        missing.append('持仓明细（基金主页与 f10 通道均未取到）')
    if not industry:
        missing.append('行业配置（f10 hydb 通道间歇不可达或该基金未披露）')
    if not bonds and (pz.get('bond_codes') or []):
        missing.append('债券持仓明细')
    if args.benchmark and not bench_series:
        missing.append('基准指数行情')
    if not pz.get('rank'):
        missing.append('同类排名序列')
    if not (pz.get('holders') or {}):
        missing.append('持有人结构')
    if not f10_stock.get('periods'):
        missing.append('多期持仓归档（f10 FundArchivesDatas 本次不可达，仅用主页最新披露）')

    payload = {
        '工具': 'fetch_fund.py',
        '工具版本': VERSION,
        '标的': code,
        '基金名称': fund_name,
        '分析基准日': (asof.iso if asof is not None and asof.is_set else _today()),
        '基准日时区': 'Asia/Shanghai (东八区)',
        '时点截断': bool(asof is not None and asof.is_set),
        '基准日后剔除净值点数': nav_dropped,
        '取数时间': _now(),
        '通道': channels,
        '记录数': len(records),
        '空值数': sum(1 for r in records if r['值'] is None),
        'stale数': sum(1 for r in records if r.get('stale')),
        '数据': records,
        '持仓': {
            '最新报告期': report_period,
            '披露类型': disclose_type,
            '数据来源': holding_source,
            '股票': stocks,
            '多期股票持仓': [{'报告期': p['报告期'], '披露类型': p['披露类型'], '股票': p['股票']}
                              for p in periods],
            '债券': (bonds[0]['股票'] if bonds else []),
            '行业配置': (industry[0]['行业'] if industry else []),
            '已披露股票合计占净值比': round(known_weight, 4) if stocks else None,
            '股票占净比': stock_total,
            '口径说明': '报告期为持仓时点，不等于当前持仓；季报仅前十大，'
                        '半年报/年报更完整；未披露部分不得推断',
        },
        '净值序列': {
            '口径': nav_caliber,
            '起始日': nav_series[0][0] if nav_series else None,
            '结束日': last_nav_date,
            '样本数': len(nav_series),
            '序列': [[d, v] for d, v in nav_series],
        },
        '基准序列': bench_block,
        '基准对比': cmp_block,
        '同类对比序列': [{'名称': g['名称'], '序列': [[d, v] for d, v in g['序列']]}
                         for g in (pz.get('grand_total') or [])],
        '基金经理': pz.get('managers') or [],
        '分红记录': pz.get('dividends') or [],
        '重仓股快照': peers,
        '未取得的指标': missing,
        '口径与局限': [
            '区间收益/年化/波动率/回撤/分位均由本脚本按净值序列计算，口径见各记录「口径」「备注」',
            '基金净值含申赎费用影响之外的管理费与托管费（净值已扣费）；基准指数为价格指数，'
            '与全收益口径存在差异',
            '指数定制指数（中证/国证 9xxxxx）历史行情在可达通道中取不到，'
            '如需跟踪误差请并列披露基金公司披露值',
            '本 JSON 为唯一事实源：所有角色必须引用同一份文件与同一分析基准日',
        ] + ([
            '时点纪律：已按分析基准日 %s 截断序列，基准日后剔除净值点 %d 个；'
            '披露日期为「报告期 + 监管最晚披露时限」的推算上界，实际披露通常更早，'
            '推算值不得当作实际披露日期' % (asof.iso, nav_dropped),
            '报告期 %s 的持仓若在基准日尚未公开，已退回上一可见报告期或置空，'
            '调用方须在「数据缺口」写明' % (report_period or '未取得'),
        ] if asof is not None and asof.is_set else []),
        '免责': '仅供研究参考，不构成投资建议；全程只读公开数据。',
    }
    return payload


def main():
    ap = argparse.ArgumentParser(description='fund-research 基金取数器')
    ap.add_argument('--code', required=True, help='6 位基金代码，如 025490')
    ap.add_argument('--out', required=True, help='输出 JSON 路径')
    ap.add_argument('--history-days', type=int, default=0,
                    help='净值回溯交易日数；0（默认）= 全部历史，'
                         '否则「成立以来」类指标会退化为样本区间')
    ap.add_argument('--f10-years', type=int, default=2,
                    help='抓取持仓归档的年度数（多期对比用，默认最近 2 年）')
    ap.add_argument('--peer-stocks', type=int, default=0, help='为重仓前 N 只个股取快照与分位')
    ap.add_argument('--benchmark-days', type=int, default=1300,
                    help='基准指数行情回溯交易日数')
    ap.add_argument('--benchmark', default=None, help='基准：sh000300 或 "沪深300"')
    ap.add_argument('--rf', type=float, default=None, help='无风险利率假设（%%/年）；给了才计算夏普')
    ap.add_argument('--topline', type=int, default=100, help='f10 持仓条数上限')
    ap.add_argument('--as-of', dest='as_of', default=None,
                    help='分析基准日 YYYY-MM-DD：净值/指数/个股序列一律截断到该日，'
                         '且报告期晚于基准日的持仓不得进入事实底稿（时点纪律）')
    args = ap.parse_args()

    if not re.match(r'^\d{6}$', args.code.strip()):
        print('[usage] --code 必须是 6 位数字基金代码', file=sys.stderr)
        return 2

    if args.as_of and build_as_of is not None:
        asof = build_as_of(args.as_of)
        if not asof.is_set:
            print('[WARN] --as-of 格式无法解析（需 YYYY-MM-DD），本次不做时点截断。',
                  file=sys.stderr)
    else:
        asof = None
    args.asof = asof

    payload = build_payload(args)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    summary = {k: v for k, v in payload.items()
               if k not in ('数据', '净值序列', '基准序列', '同类对比序列', '重仓股快照',
                            '多期股票持仓')}
    summary['净值序列'] = {k: v for k, v in payload['净值序列'].items() if k != '序列'}
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    fatal = not payload['净值序列']['样本数'] and not payload['持仓']['股票']
    if fatal:
        print('[FATAL] 基金身份/净值/持仓全部通道取数失败——不得用估算填补，'
              '请在报告「数据缺口」中如实列出并终止。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
