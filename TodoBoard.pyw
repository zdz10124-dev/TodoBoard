# -*- coding: utf-8 -*-
"""
TodoBoard —— 桌面半透明待办面板

零第三方依赖(Python 标准库 + tkinter)。
双击本文件即可运行(.pyw 后缀不会弹出黑色控制台窗口)。

功能:
  · 深色半透明、无边框、可拖动
  · 自定义分栏(默认「短期目标」「长期目标」)
  · 任务新增 / 编辑 / 删除 / 拖拽排序 / 跨栏拖动
  · 截止时间 + 临近提醒(应用内弹窗 + 提示音)
  · 数据即时落盘,关掉不丢

数据文件:与本脚本同目录的 data.json
"""

import atexit
import collections
import copy
import ctypes
import json
import math
import os
import re
import socket
import subprocess
import sys
import uuid
import winsound
from datetime import date, datetime, time, timedelta

import tkinter as tk
from tkinter import font as tkfont

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SHORTCUT_NAME = "待办面板"

# 开发用:设 TODOBOARD_DEV=<随便一个目录> 就切到独立数据文件 + 不抢单实例锁,
# 这样可以在不打断正在使用的主程序的前提下,另起一个窗口调界面。
DEV = os.environ.get("TODOBOARD_DEV")
DATA_FILE = (os.path.join(DEV, "data.json") if DEV
             else os.path.join(APP_DIR, "data.json"))

# 操作日志。同样是纯文本、UTF-8、追加写,可以直接拿记事本打开看。
LOG_FILE = (os.path.join(DEV, "activity.log") if DEV
            else os.path.join(APP_DIR, "activity.log"))


# --------------------------------------------------------------------------
# DPI —— 必须在创建 Tk 之前设置,否则高分屏下发虚
# --------------------------------------------------------------------------

def _setup_dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _get_dpi():
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return dpi or 96
    except Exception:
        return 96


_setup_dpi()
DPI = _get_dpi()
SCALE = DPI / 96.0


def px(v):
    """逻辑像素 -> 物理像素"""
    return int(round(v * SCALE))


def _row_text_x():
    """一行任务里文字的左边 x:勾选圈右边留 11px。
    「添加任务」那行、以及行的分隔线都对齐到这里 —— 整栏一条竖线。"""
    return px(CARD_PAD) + px(15) + px(11)


def _row_box_cx():
    """勾选圈的中心 x,「添加任务」的 + 也画在这个位置。"""
    return px(CARD_PAD) + px(15) / 2


# --------------------------------------------------------------------------
# 主题(深色玻璃)
# --------------------------------------------------------------------------

# 设计原则(抄 illustro 的作业):
#   1. 只有「一层」表面。旧版把面板/分栏/卡片/胶囊叠了四层近黑色,每层都得
#      靠描边区分,于是到处是框 —— 那是廉价感的根源。现在全部同一个底色,
#      靠留白和 1px 发丝线分区,不再有第二个方块。
#   2. 所有颜色都是中性近黑/近白,不偏蓝。旧版的 #0C0C14 / #21212E 带蓝调,
#      与壁纸混色后发浑。illustro 底面就是纯黑 rgba(0,0,0,140)。
#   3. 只有一个暖色强调色(琥珀,取自 illustro 的 colorBar=235,170,0),
#      且只在「置顶指示」「逾期/临近」两处出现,别处一律中性。
#   4. 文字靠字重/字号/明度分四级层次,不靠加框。
#   5. 栏色只染「玻璃」,不染「内容」。每栏底子带一点自己的色相(见 glass_tint),
#      但文字、发丝线、按钮永远中性黑白灰 —— 染色是背景的事。这样几栏摆在一起
#      是"几块不同的玻璃",而不是"几张彩色卡片"。第 2 条说的"中性"管的是**前景**;
#      底色是唯一允许带色相的地方。
C_PANEL      = "#0B0B0E"   # 面板底 = 分栏底 = 行底(唯一表面)
C_HEADER     = "#131317"   # 标题栏(渐变起点,比正文略亮一点,像被照亮)
C_HEADER_2   = "#0E0E11"   # 标题栏(渐变终点,过渡到面板底)
C_COL_BG     = C_PANEL     # 分栏不再是独立容器
C_ROW_HOV    = "#17171C"   # 任务行的悬停填充(整栏宽,像被选中)
C_CARD       = "#16161A"   # 对话框里的输入框/控件底
C_CARD_HOV   = "#22222A"   # 控件悬停
C_CARD_DRAG  = "#08080A"   # 拖动中的行:比底更暗,像被抠掉
C_ADD_HOV    = C_ROW_HOV
C_TEXT       = "#E9E9ED"   # 一级:标题
C_TEXT_DIM   = "#9C9CA6"   # 二级:分栏名 / 对话框正文
C_TEXT_MUTE  = "#7C7C87"   # 三级:内容预览、普通截止时间
C_TEXT_FAINT = "#55555F"   # 四级:占位、未激活的图标
C_LINE       = "#202024"   # 主发丝线(栏头下、行与行之间)
C_LINE_SOFT  = "#161619"   # 更弱的分隔(分栏之间)
C_RIM        = "#2A2A31"   # 面板顶边的那一道 1px 高光
C_HOVER_BG   = "#1F1F24"   # 图标按钮悬停底
C_PILL       = "#16161A"   # 输入框/按钮底
C_RING       = "#3A3A42"   # 未完成勾选圈

C_OVERDUE    = "#E4655F"
C_SOON       = "#E9A23B"   # = 强调琥珀:时间紧迫和"强调"共用一种暖色
C_DONE       = "#4E4E58"
C_ACCENT     = "#E9A23B"

# 分栏色:降饱和,和琥珀摆在一起才不打架
PALETTE = ["#E9A23B", "#8B9AE8", "#5BB39A", "#D4788F",
           "#9BAE4E", "#B08BD8", "#5FA3D6", "#D98E5F"]

# 字体:不用 Microsoft YaHei UI —— 那是系统界面字体,一眼就"操作系统默认风"。
# Noto Sans SC 的字面和行高更接近印刷品,Light/Medium 有独立字重。
FAMILY      = "Noto Sans SC"
FAMILY_MED  = "Noto Sans SC Medium"
FAMILY_LGT  = "Noto Sans SC Light"

# 逻辑尺寸
HEADER_H     = 48
PAD          = 14      # 面板外边距
COL_GAP      = 1       # 分栏之间只有一条发丝线
COL_PAD      = 14      # 分栏内容左右内边距(发丝线由它让开)
COL_HDR_H    = 44
COL_W_DEF    = 268
CARD_PAD     = 12      # 行内左右边距(勾选圈的位置)
ROW_PAD_Y    = 10      # 行内上下边距
CARD_R       = 8       # 行悬停填充的圆角
CARD_GAP     = 0       # 行与行之间用发丝线,不留空隙
BTN_COL_W    = 40
RESIZE_H     = 6
SNAP_DIST    = 16
COLLAPSED_H  = HEADER_H    # 收起后正好只剩标题栏(含它下面那条发丝线)

# 拆成"每栏一扇窗"之后新增的几个尺寸
COL_GAP_OUT  = 8       # 两扇分栏窗之间留的空档(不再是面板内的发丝线)
EDGE_W       = 5       # 左右/下边缘改尺寸的抓取宽度(贴在窗口最边上)
COL_W_MIN    = 190     # 一栏最窄
COL_H_MIN    = 170     # 一栏最矮(低于这个高度连两行任务都放不下)
TOOL_W       = 18 + 30 * 7 + 14   # 工具条窗的宽度(7 个图标 + 左右留白)
TOOL_PAD     = 16      # 工具条内的左右留白


class F:
    """字体容器,启动后填充。tk 的负字号 = 像素高度。"""
    title = col = task = taskd = meta = small = tiny = ui = btn = None


def _font(family, size, **kw):
    """挑一个真实存在的字体族。Noto Sans SC 的 Medium/Light 是独立族名,
    某些机器上没有,退回到基础族,免得 Tk 静默换成丑的默认字体。"""
    fams = set(tkfont.families())
    fam = family if family in fams else FAMILY
    if fam not in fams:
        fam = "Microsoft YaHei UI"
    return tkfont.Font(family=fam, size=-px(size), **kw)


def build_fonts():
    F.title = _font(FAMILY_MED, 17)                  # 面板标题
    F.col   = _font(FAMILY_MED, 13)                  # 分栏名
    F.task  = _font(FAMILY, 13)                      # 任务标题
    # 完成态的删除线不用字体的 overstrike —— 那是一条 1px 的细线,和小字号
    # 的笔画糊在一起根本看不出来。改成自己沿线画一条,粗细可控。
    F.taskd = _font(FAMILY, 13)
    F.meta  = _font(FAMILY_LGT, 12)                  # 内容预览
    F.small = _font(FAMILY, 12)
    F.tiny  = _font(FAMILY, 11)                      # 截止时间、计数
    F.ui    = _font(FAMILY, 12)
    F.btn   = _font(FAMILY, 12)


# --------------------------------------------------------------------------
# 绘图工具
# --------------------------------------------------------------------------

def round_rect(cv, x1, y1, x2, y2, r, corners=(1, 1, 1, 1), **kw):
    """在 canvas 上画一个精确圆角的矩形,返回 item id。

    corners = (左上, 右上, 右下, 左下),某一位为 0 时那个角是直角
    —— 用来把分栏的标题条/脚条拼成一个完整圆角容器。
    """
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    corners = tuple(bool(c) for c in corners)
    if not any(corners) or r <= 0:
        return cv.create_polygon(x1, y1, x2, y1, x2, y2, x1, y2, **kw)
    # (圆心, 起始角, 该角为直角时用的顶点)
    arcs = ((x1 + r, y1 + r, 180, x1, y1), (x2 - r, y1 + r, 270, x2, y1),
            (x2 - r, y2 - r, 0, x2, y2), (x1 + r, y2 - r, 90, x1, y2))
    pts = []
    for (cx, cy, a0, sx, sy), on in zip(arcs, corners):
        if not on:
            pts.extend((sx, sy))
            continue
        for i in range(9):
            a = math.radians(a0 + 90 * i / 8)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return cv.create_polygon(pts, **kw)


def _hex2rgb(s):
    s = s.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _mix(c1, c2, k):
    """两色线性插值,k=0 取 c1,k=1 取 c2。"""
    r1, g1, b1 = _hex2rgb(c1)
    r2, g2, b2 = _hex2rgb(c2)
    return "#%02X%02X%02X" % (round(r1 + (r2 - r1) * k),
                              round(g1 + (g2 - g1) * k),
                              round(b1 + (b2 - b1) * k))


GLASS_DYE = 0.86           # 栏色混进玻璃底的比例(见 glass_tint)


def glass_tint(color):
    """一栏的"玻璃底色":把栏色压成深色的那一层。

    整块玻璃都染上这一栏的颜色 —— 但不能直接拿栏色去铺,那是荧光板不是玻璃。
    和面板底混到只留一成多的色相:一眼分得出"这栏偏暖、那栏偏冷",又抢不走
    文字的注意力。想让它更浓或更淡,改 GLASS_DYE:小 = 更花哨,大 = 更含蓄
    (1.0 就是原来的纯黑)。0.80 试过,暖色那几栏会发土、显脏。
    """
    return _mix(color, C_PANEL, GLASS_DYE)


# --------------------------------------------------------------------------
# Win11 毛玻璃(亚克力)背景
# --------------------------------------------------------------------------
# 均匀的 -alpha 透明只是"把桌面按比例混进来"。背后是终端、浏览器这种满屏
# 文字时,文字会原样透到面板上,和面板自己的字叠在一起 —— 看着像贴了张半
# 透明纸,不像玻璃。真正的玻璃感来自**模糊**:把背后糊掉再压一层色调。
# Windows 的组合引擎有现成的 API,不用自己截屏做模糊。
#
# 附带好处:换上毛玻璃之后,面板可以做得比现在透得多还依然干净,因为透过来
# 的东西已经不成形了。

class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),   # 0xAABBGGRR
                ("AnimationId", ctypes.c_int)]


class _WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(_ACCENT_POLICY)),
                ("SizeOfData", ctypes.c_size_t)]


ACCENT_DISABLED = 0
ACCENT_BLURBEHIND = 3      # 只模糊,不着色
ACCENT_ACRYLIC = 4         # 模糊 + 噪点,Win11 上就是"云母"那种质感
WCA_ACCENT_POLICY = 19


def set_backdrop(hwnd, state, tint=None, tint_a=0.72):
    """给窗口挂上毛玻璃。返回是否成功(失败就老老实实用均匀透明)。

    `tint` 是挂上去的那层底色,`tint_a` 是它的不透明度 —— 调色板里的"不透明
    度"滑杆,挂上毛玻璃之后调的就是这个数。
    """
    try:
        grad = 0
        if tint:
            r, g, b = _hex2rgb(tint)
            a = max(0, min(255, int(round(tint_a * 255))))
            # DWM 要的是 0xAABBGGRR,蓝绿是反的
            grad = (a << 24) | (b << 16) | (g << 8) | r
        policy = _ACCENT_POLICY(state, 2, grad, 0)
        data = _WINCOMPATTRDATA(WCA_ACCENT_POLICY,
                                ctypes.pointer(policy),
                                ctypes.sizeof(policy))
        fn = ctypes.windll.user32.SetWindowCompositionAttribute
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_WINCOMPATTRDATA)]
        fn.restype = ctypes.c_int
        return bool(fn(ctypes.c_void_p(hwnd), ctypes.byref(data)))
    except Exception:
        return False


def _nearest_palette(hexcol):
    """在新调色板里找色相最接近的一个。

    比"按明度/欧氏距离找最近色"好:欧氏距离会把深蓝配成深绿这种离谱结果,
    而色相最近能保证"蓝的还是蓝的",只是换了同一套色阶。
    """
    import colorsys

    def hue(c):
        r, g, b = (v / 255 for v in _hex2rgb(c))
        return colorsys.rgb_to_hsv(r, g, b)[0]

    try:
        h = hue(hexcol)
    except Exception:
        return PALETTE[0]
    best, bestd = PALETTE[0], 9.0
    for p in PALETTE:
        d = abs(hue(p) - h)
        d = min(d, 1.0 - d)          # 色相是环,0.98 和 0.02 其实只差 0.04
        if d < bestd:
            best, bestd = p, d
    return best


def vgrad(cv, x1, y1, x2, y2, c_top, c_bot, steps=30, tags=()):
    """竖向渐变。tk 没有原生渐变,只能用一串等高色带糊出来。

    色带之间故意多画 1px 重叠,避免非整数像素处露出背景色缝。
    """
    band = (y2 - y1) / steps
    ids = []
    for i in range(steps):
        k = i / (steps - 1) if steps > 1 else 0.0
        top = y1 + i * band
        ids.append(cv.create_rectangle(x1, top, x2, top + band + 1,
                                       fill=_mix(c_top, c_bot, k),
                                       outline="", tags=tags))
    return ids


# --- 手绘图标(不依赖字体符号,任何机器都不会缺字形) ---------------------

def _lw(s):
    return max(2, int(round(s * 0.155)))


def icon_top(cv, cx, cy, s, color, tags=()):
    """置顶:向上实心三角 + 底线"""
    cv.create_polygon(cx, cy - s * 0.50, cx - s * 0.42, cy + s * 0.16,
                      cx + s * 0.42, cy + s * 0.16,
                      fill=color, outline="", tags=tags)
    cv.create_line(cx - s * 0.54, cy + s * 0.52, cx + s * 0.54, cy + s * 0.52,
                   fill=color, width=_lw(s), capstyle="round", tags=tags)


def icon_gear(cv, cx, cy, s, color, tags=()):
    """设置:圆环 + 八根辐条"""
    r1, r2 = s * 0.30, s * 0.50
    cv.create_oval(cx - r1, cy - r1, cx + r1, cy + r1,
                   outline=color, width=_lw(s), tags=tags)
    for i in range(8):
        a = math.radians(i * 45)
        cv.create_line(cx + r1 * math.cos(a), cy + r1 * math.sin(a),
                       cx + r2 * math.cos(a), cy + r2 * math.sin(a),
                       fill=color, width=_lw(s), capstyle="round", tags=tags)


def icon_chevron(cv, cx, cy, s, color, down=True, tags=()):
    """收起/展开:V 形折角"""
    d = 1 if down else -1
    cv.create_line(cx - s * 0.46, cy - d * s * 0.20, cx, cy + d * s * 0.26,
                   cx + s * 0.46, cy - d * s * 0.20,
                   fill=color, width=_lw(s), capstyle="round",
                   joinstyle="round", tags=tags)


def icon_close(cv, cx, cy, s, color, tags=()):
    d = s * 0.40
    for a, b, c_, e in ((cx - d, cy - d, cx + d, cy + d),
                        (cx - d, cy + d, cx + d, cy - d)):
        cv.create_line(a, b, c_, e, fill=color, width=_lw(s),
                       capstyle="round", tags=tags)


def icon_dots(cv, cx, cy, s, color, tags=()):
    """分栏菜单:竖排三点"""
    r = s * 0.105
    for dy in (-s * 0.36, 0.0, s * 0.36):
        cv.create_oval(cx - r, cy + dy - r, cx + r, cy + dy + r,
                       fill=color, outline="", tags=tags)


def icon_plus(cv, cx, cy, s, color, tags=()):
    cv.create_line(cx - s * 0.44, cy, cx + s * 0.44, cy,
                   fill=color, width=_lw(s), capstyle="round", tags=tags)
    cv.create_line(cx, cy - s * 0.44, cx, cy + s * 0.44,
                   fill=color, width=_lw(s), capstyle="round", tags=tags)


def _arc_arrow(cv, cx, cy, s, color, tags, left_side):
    """半个弧 + 端点上一个向下的箭头。left_side 决定箭头落在哪头。

    图标只有十几个像素,画整圈的 ↺ 在这个尺寸下糊成一团;半圆弧加一个明确的
    箭头,一眼就能看出"往回"还是"往前"。
    """
    r = s * 0.46
    cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=0, extent=180,
                  style="arc", outline=color, width=_lw(s), tags=tags)
    x = cx - r if left_side else cx + r
    ln = s * 0.32
    for d in (135, -135):
        b = math.radians(-90 + d)
        cv.create_line(x, cy, x + ln * math.cos(b), cy - ln * math.sin(b),
                       fill=color, width=_lw(s), capstyle="round",
                       joinstyle="round", tags=tags)


def icon_undo(cv, cx, cy, s, color, tags=()):
    """撤回:箭头在左端。"""
    _arc_arrow(cv, cx, cy, s, color, tags, True)


def icon_redo(cv, cx, cy, s, color, tags=()):
    """重做:箭头在右端。"""
    _arc_arrow(cv, cx, cy, s, color, tags, False)


def icon_trash(cv, cx, cy, s, color, tags=()):
    """清空本栏:一个带盖的分类篓。"""
    w2 = s * 0.34
    top = cy - s * 0.28
    bot = cy + s * 0.46
    cv.create_line(cx - s * 0.52, top, cx + s * 0.52, top,
                   fill=color, width=_lw(s), capstyle="round", tags=tags)
    cv.create_line(cx - s * 0.18, top - s * 0.18, cx + s * 0.18, top - s * 0.18,
                   fill=color, width=_lw(s), capstyle="round", tags=tags)
    cv.create_line(cx - w2, top + s * 0.10, cx - w2 * 0.82, bot,
                   fill=color, width=_lw(s), capstyle="round", tags=tags)
    cv.create_line(cx + w2, top + s * 0.10, cx + w2 * 0.82, bot,
                   fill=color, width=_lw(s), capstyle="round", tags=tags)
    cv.create_line(cx - w2 * 0.82, bot, cx + w2 * 0.82, bot,
                   fill=color, width=_lw(s), capstyle="round", tags=tags)


def ellipsize(text, fnt, max_w):
    """按像素宽度截断,放不下就换成省略号。

    栏名以前是原样画的 —— 名字长一点就撞上右边的 ⋯ 了(加上垃圾桶更挤)。
    """
    if fnt.measure(text) <= max_w:
        return text
    ell = "…"
    w_ell = fnt.measure(ell)
    out = ""
    for ch in text:
        if fnt.measure(out + ch) + w_ell > max_w:
            break
        out += ch
    return out + ell


def wrap_text(text, fnt, max_w, max_lines=None):
    """按像素宽度折行(逐字符,适配中英混排),返回行列表。"""
    if not text:
        return []
    out = []
    for para in str(text).split("\n"):
        para = para.rstrip()
        if not para:
            continue
        line = ""
        for ch in para:
            if fnt.measure(line + ch) <= max_w:
                line += ch
            else:
                out.append(line)
                line = ch
        if line:
            out.append(line)
    if max_lines and len(out) > max_lines:
        out = out[:max_lines]
        last = out[-1]
        while last and fnt.measure(last + "…") > max_w:
            last = last[:-1]
        out[-1] = last + "…"
    return out


# --------------------------------------------------------------------------
# 日期工具
# --------------------------------------------------------------------------

_WEEK = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def parse_due_str(s):
    """把用户输入解析成 datetime;空串返回 None;无法识别抛 ValueError。"""
    s = (s or "").strip()
    if not s:
        return None
    s = s.replace("：", ":").replace("/", "-").replace("　", " ")
    now = datetime.now()
    hm = None
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            raise ValueError("时间不合法")
        hm = (h, mi)
        s = (s[:m.start()] + s[m.end():]).strip().strip("-").strip()

    base = None
    if not s:
        if hm is None:
            return None
        cand = datetime.combine(now.date(), time(*hm))
        if cand <= now:
            cand += timedelta(days=1)
        return cand
    if s.startswith("今天"):
        base = now.date()
    elif s.startswith("明天"):
        base = now.date() + timedelta(days=1)
    elif s.startswith("后天"):
        base = now.date() + timedelta(days=2)
    elif s[0] in ("周", "星", "礼"):
        key = s[-1]
        if key not in _WEEK:
            raise ValueError("无法识别的星期")
        delta = (_WEEK[key] - now.weekday()) % 7
        if delta == 0:
            delta = 7
        base = now.date() + timedelta(days=delta)
    else:
        m2 = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
        m3 = re.match(r"^(\d{1,2})-(\d{1,2})$", s)
        try:
            if m2:
                base = date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
            elif m3:
                base = date(now.year, int(m3.group(1)), int(m3.group(2)))
        except ValueError:
            raise ValueError("日期不合法")
        if base is None:
            raise ValueError("看不懂这个时间")

    return datetime.combine(base, time(*(hm or (23, 59))))


def fmt_due(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def humanize(td):
    secs = int(td.total_seconds())
    if secs < 0:
        secs = -secs
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    mi = rem // 60
    if d:
        return f"{d} 天"
    if h:
        return f"{h} 小时"
    if mi:
        return f"{mi} 分钟"
    return "不到 1 分钟"


def due_label(due_iso, done):
    """返回 (文字, 颜色);无截止时间返回 (None, None)。"""
    if not due_iso:
        return None, None
    try:
        due = datetime.fromisoformat(due_iso)
    except ValueError:
        return None, None
    now = datetime.now()
    if done:
        return due.strftime("%m-%d %H:%M"), C_DONE
    delta = due - now
    if delta.total_seconds() < 0:
        return f"已逾期 {humanize(delta)}", C_OVERDUE
    d = due.date()
    today = now.date()
    if d == today:
        txt = due.strftime("今天 %H:%M")
    elif d == today + timedelta(days=1):
        txt = due.strftime("明天 %H:%M")
    elif 0 <= (d - today).days <= 6:
        txt = due.strftime("%m-%d %H:%M")
    else:
        txt = due.strftime("%Y-%m-%d")
    if delta.total_seconds() <= 3600:
        return txt, C_SOON
    return txt, C_TEXT_MUTE


RING_URGENT_PCT = 0.05     # 剩余不到 5% 算"快结束了"
RING_URGENT_SEC = 60       # 或者剩余不到 1 分钟
RING_MIN_DEG = 12          # 弧再短也得画这么多,否则小进度等于没画
SHAKE_MS = 90              # 快结束时环抖一拍要多久(毫秒)
SHAKE_PX = 1.5             # 抖的幅度(逻辑像素)。再大就不是"轻轻"了


def ring_progress(t, now=None):
    """勾选圈里那圈进度该画成什么样。

    返回 (已过比例, 颜色, 是不是快结束了);这条任务不该画环就返回 None。

    比例量的是"从建这条任务起、到截止为止"这一段里已经过去的部分 —— 环从
    十二点顺时针长过去,长满就是到点。用 created 当起点而不是固定的时间窗,
    是因为它不用猜:任务活多久,这个环就走多久。
    """
    if t.get("done") or not t.get("due"):
        return None
    try:
        due = datetime.fromisoformat(t["due"])
        made = datetime.fromisoformat(t["created"])
    except (ValueError, TypeError, KeyError):
        return None
    total = (due - made).total_seconds()
    if total <= 0:
        return None
    now = now or datetime.now()
    left = (due - now).total_seconds()
    if left <= 0:
        # 逾期:环满着、红着,但**不抖**。抖是"快到点了"这一句话,说到点上
        # 就该停;挂着一个逾期任务让它抖到天荒地老,那是噪音不是提醒。
        return 1.0, C_OVERDUE, False
    p = 1.0 - left / total
    urgent = left <= RING_URGENT_SEC or (left / total) <= RING_URGENT_PCT
    if urgent or left <= 3600:
        color = C_SOON
    else:
        color = C_TEXT_MUTE
    return p, color, urgent


# --------------------------------------------------------------------------
# 数据层
# --------------------------------------------------------------------------

def new_id():
    return uuid.uuid4().hex[:12]


def default_data():
    now = datetime.now()
    tomorrow = datetime.combine(now.date() + timedelta(days=1), time(18, 0))
    later = datetime.combine(now.date() + timedelta(days=30), time(23, 59))
    c1, c2 = new_id(), new_id()
    return {
        "version": 1,
        "settings": {
            # x/y/height/column_width 是"新建一栏时的默认值",不再是面板本身的
            # 几何 —— 每栏的位置和大小都存在自己的 columns 条目里了
            "x": None, "y": None, "height": 560,
            "opacity": 0.88, "topmost": True, "sound": True,
            "hide_done": False, "default_remind_minutes": -1,
            "column_width": COL_W_DEF,
            "tool_x": None, "tool_y": None,
        },
        "columns": [
            {"id": c1, "name": "短期目标", "color": PALETTE[0]},
            {"id": c2, "name": "长期目标", "color": PALETTE[1]},
        ],
        "tasks": [
            {"id": new_id(), "column_id": c1, "title": "交周报",
             "content": "把本周进展整理成一页发给组里",
             "due": fmt_due(tomorrow), "remind_minutes": 60,
             "done": False, "created": now.isoformat(timespec="minutes"),
             "notified": False, "snooze_until": None},
            {"id": new_id(), "column_id": c1, "title": "预约牙医",
             "content": "", "due": None, "remind_minutes": -1,
             "done": False, "created": now.isoformat(timespec="minutes"),
             "notified": False, "snooze_until": None},
            {"id": new_id(), "column_id": c2, "title": "学英语",
             "content": "每天 30 分钟,别断",
             "due": None, "remind_minutes": -1,
             "done": False, "created": now.isoformat(timespec="minutes"),
             "notified": False, "snooze_until": None},
            {"id": new_id(), "column_id": c2, "title": "读完《人月神话》",
             "content": "一个月内啃完,做读书笔记",
             "due": fmt_due(later), "remind_minutes": 1440,
             "done": False, "created": now.isoformat(timespec="minutes"),
             "notified": False, "snooze_until": None},
        ],
    }


class Store:
    def __init__(self, path):
        self.path = path
        self.data = None
        self.fresh = False   # 本次是否为首次创建
        self._dirty = False  # 迁移改动了数据,载入后要立刻落盘

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self._backup_broken()
                self.data = default_data()
                self.fresh = True
        else:
            self.data = default_data()
            self.fresh = True
        self._normalize()
        if self._dirty:
            self.save()      # 迁移只在第一次生效,落盘后就不再触发
        return self.data

    def _backup_broken(self):
        try:
            os.replace(self.path, self.path + ".broken")
        except Exception:
            pass

    def _normalize(self):
        d = self.data
        d.setdefault("version", 1)
        st = d.setdefault("settings", {})
        for k, v in default_data()["settings"].items():
            st.setdefault(k, v)
        d.setdefault("columns", [])
        d.setdefault("tasks", [])
        if not d["columns"]:
            d["columns"] = default_data()["columns"]
        self._migrate_colors(d["columns"])
        self._migrate_geometry(d["columns"], st)
        valid = {c["id"] for c in d["columns"]}
        for t in d["tasks"]:
            if t.get("column_id") not in valid:
                t["column_id"] = d["columns"][0]["id"]
            t.setdefault("content", "")
            t.setdefault("due", None)
            t.setdefault("done", False)
            t.setdefault("notified", False)
            t.setdefault("snooze_until", None)
            t.setdefault("title", "未命名")

    def _migrate_colors(self, cols):
        """把旧版的糖果色分栏归拢到新调色板。

        旧版每栏一个高饱和蓝/紫/青,叠在近黑底上像三条霓虹灯 —— 这是"廉价"
        的一大来源。新版按"色相就近"映射:蓝栏还是蓝栏,只是降了饱和、提了
        明度对齐到同一套色阶,用户对"哪栏是哪栏"的记忆不会断。

        只认调色板里没有的颜色,所以跑过一次之后就是空操作。
        """
        try:
            for c in cols:
                cur = str(c.get("color", "")).upper()
                if not cur or cur in PALETTE:
                    continue
                c["color"] = _nearest_palette(cur)
                self._dirty = True
        except Exception:
            pass

    def _migrate_geometry(self, cols, st):
        """把"一整块面板"时代的几何拆到每一栏头上。

        旧版只有一个窗口:x/y 是整块面板的左上角,height 是公共高度,
        column_width 是公共栏宽,每栏自己没有位置。新版每栏一扇窗,所以按旧的
        分栏顺序从左往右依次排开,起点对齐旧面板左上角、再让开面板外边距 ——
        视觉上就是"原来那块面板原地展开成几栏",不会突然跳到屏幕别处去。

        只认还没有 x 的条目,所以跑过一次之后就是空操作。
        """
        try:
            if all(c.get("x") is not None and c.get("y") is not None for c in cols):
                return
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            x0 = st.get("x")
            y0 = st.get("y")
            if x0 is None:
                x0 = sw - px(24) - sum(
                    px(int(c.get("w") or st.get("column_width", COL_W_DEF)))
                    + px(COL_GAP_OUT) for c in cols)
            y0 = 0 if y0 is None else int(y0)
            for i, c in enumerate(cols):
                c.setdefault("w", int(st.get("column_width", COL_W_DEF)))
                c.setdefault("h", int(st.get("height", 560)))
                if c.get("x") is None or c.get("y") is None:
                    c["x"] = int(x0) + px(PAD) + sum(
                        px(int(cc["w"])) + px(COL_GAP_OUT) for cc in cols[:i])
                    c["y"] = y0
            self._dirty = True
        except Exception:
            pass

    def save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # -- 便捷访问 --------------------------------------------------------
    @property
    def settings(self):
        return self.data["settings"]

    @property
    def columns(self):
        return self.data["columns"]

    @property
    def tasks(self):
        return self.data["tasks"]

    def task(self, tid):
        for t in self.tasks:
            if t["id"] == tid:
                return t
        return None

    def column(self, cid):
        for c in self.columns:
            if c["id"] == cid:
                return c
        return None


# --------------------------------------------------------------------------
# 操作日志 + 撤回/重做
# --------------------------------------------------------------------------

class Journal:
    """一次操作,两处记账。

    每一次"动了数据"的操作都做两件事:
      · 往 activity.log 追加一行 —— 给人看的。纯文本、带秒级时间戳,
        随时拿记事本打开就能看出"这条是谁什么时候删的"。
      · 把操作**之前**的一份快照压进撤回栈 —— 给按钮用的。

    撤回/重做走整份快照,而不是给每种操作配一个"逆操作"。因为操作之间互相
    牵扯得厉害:删一个分栏要把栏里的任务全并到第一栏、跨栏拖动要同时改
    column_id 和顺序、清空一栏要删一批 —— 逆操作就得对着每一种组合分别
    写对,漏一种就是"撤回之后数据不对,还不如不撤"。数据一共才几 KB,
    存快照便宜得多,而且**永远是对的**。

    栈只在内存里,不落盘:重启之后"撤回上一步"本来也说不通(那可能是昨天
    的事)。落盘的那份是给人读的日志,不是给程序读的。
    """

    MAX_BYTES = 1024 * 1024   # 日志过 1MB 就把旧的挪成 activity.log.1
    MAX_STEPS = 200           # 撤回栈的深度上限

    def __init__(self, app):
        self.app = app
        self.undo_stack = []      # [(标签, 快照)]  快照 = 改**之前**的样子
        self.redo_stack = []

    # -- 写日志 ----------------------------------------------------------
    def log(self, text):
        line = "%s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text)
        try:
            self._rotate()
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass      # 记不上账不能影响用

    def _rotate(self):
        try:
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > self.MAX_BYTES:
                os.replace(LOG_FILE, LOG_FILE + ".1")
        except Exception:
            pass

    # -- 记账 ------------------------------------------------------------
    def snapshot(self):
        d = self.app.store.data
        return {"columns": copy.deepcopy(d["columns"]),
                "tasks": copy.deepcopy(d["tasks"])}

    def note(self, label):
        """**动手改数据之前**调一次。改完再叫就晚了 —— 快照拍到的是改后的样子。"""
        self.commit(label, self.snapshot())

    def commit(self, label, snap):
        """记下一笔,但用调用方**先前**拍好的快照。

        新建任务用得着:任务在弹编辑窗之前就已经加进数据了(不然编辑窗取消
        的时候还得记得删),但那会儿标题还是空的 —— 日志里记成"新建任务「」"
        没用。所以进 add_task 时先拍一张,等标题填完了再把这张拿来记账。
        """
        if snap is None:
            return
        self.undo_stack.append((label, snap))
        if len(self.undo_stack) > self.MAX_STEPS:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.log(label)

    def can_undo(self):
        return bool(self.undo_stack)

    def can_redo(self):
        return bool(self.redo_stack)

    def undo(self):
        if not self.undo_stack:
            return None
        label, snap = self.undo_stack.pop()
        self.redo_stack.append((label, self.snapshot()))
        self._restore(snap)
        self.log("撤回:" + label)
        return label

    def redo(self):
        if not self.redo_stack:
            return None
        label, snap = self.redo_stack.pop()
        self.undo_stack.append((label, self.snapshot()))
        self._restore(snap)
        self.log("重做:" + label)
        return label

    def _restore(self, snap):
        d = self.app.store.data
        d["columns"][:] = snap["columns"]
        d["tasks"][:] = snap["tasks"]
        self.app.store.save()
        self.app.apply_history()


# --------------------------------------------------------------------------
# 主应用
# --------------------------------------------------------------------------

class TodoBoard:
    """控制器 + 每栏一扇自己的窗。

    这里**没有"整块面板"了**。每个分栏就是一扇独立的无边框窗:自己的圆角、
    自己的毛玻璃、自己的宽高和位置,爱拖到哪儿拖到哪儿,互相之间只是"碰巧
    挨着"。最右边另有一小条工具窗(＋新建分栏 / 置顶 / 收起 / 设置 / 退出)。

    之所以走"多窗"而不是"一块面板里放几个可拖动的栏":后者做不出"栏跑到
    屏幕另一头"这件事,那是这次要的东西。代价是"一整块玻璃"变成了"几块
    玻璃",但每块都有自己的顶边高光,看起来是几片干净的玻璃,不是几个盒子。

    宿主 root 全程 withdraw,只当一个不露面的"窗口工厂":它管 bind_all、
    after 定时器,以及所有子窗的生命周期。子窗都挂在它下面。
    """

    def __init__(self, root, store):
        self.root = root
        self.store = store
        st = store.settings

        self.opacity = float(st.get("opacity", 0.88))
        self.acrylic = False       # 毛玻璃是否真的挂上了,见 _apply_backdrop
        self.topmost = bool(st.get("topmost", True))
        self.hide_done = bool(st.get("hide_done", False))
        self.col_w = int(st.get("column_width", COL_W_DEF))   # 新分栏的默认宽度

        self.columns = []          # [{"id","win","hdr","canvas","data",...}]
        self.toolwin = None
        self.toolcv = None
        self.card_geo = {}         # tid -> (y_top, height)
        self.card_rect = {}        # tid -> 悬停填充的 item id
        self.card_canvas = {}      # tid -> 所属 canvas
        self.card_hair = {}        # tid -> 该行下方的发丝线 item id
        self.card_check = {}       # tid -> 勾选圈那块"命中的底"的 item id
        self.card_del = {}         # tid -> 行尾叉号那块"命中的底"的 item id
        self._hov_tid = None       # 鼠标正停在谁头上(给叉号的回退色用)
        self.card_check_geo = {}   # tid -> 勾选圈可点范围的 (x0,y0,x1,y1)
        self.card_ring = {}        # tid -> (进度环 item id, 它该在的方框)
        self._shake_tids = set()   # 快到点的那些任务,环在抖
        self._shake_phase = 0      # 抖到第几拍了
        self._shake_job = None     # 抖动的 after 句柄;没在抖就是 None
        self.card_del_geo = {}     # tid -> 行尾那个叉号可点范围的 (x0,y0,x1,y1)
        self.add_geo = {}          # 栏 id -> 「添加任务」那行的 (y0,y1)
        self._press_serial = None  # 上一次按下的序号,用来去重(见 _col_press)
        self._pending = None       # 拖拽中的任务行
        self._win_drag = None      # 拖拽中的分栏窗
        self._tool_drag = None
        self._rs = None            # 正在缩放边缘
        self._indicator = None
        self._collapsed = False
        self._expanded = {}        # cid -> 收起前的高度(物理像素)
        self._reminders = []
        self._icons = {}
        self._hdr_bg = C_HEADER
        self.hist = Journal(self)  # 操作日志 + 撤回/重做,见 Journal
        self._new_snap = None      # 新建任务时先拍的底,标题填完再记账
        self._cur_toast = None     # 屏幕上正浮着的那条提示,换新的先把旧的收掉
        self._hidden = False       # 面板被整个藏起来了(托盘菜单里切的那项)
        self.tray = None           # 任务栏折叠区里那个图标,见 TrayIcon

        # 提醒卡片和提示条要有个"面板在哪儿"的参照,取第一栏(见 _sync_anchor)
        self.win_x = self.win_y = 0
        self.win_w = px(COL_W_DEF)
        self.win_h = px(400)

        self.root.withdraw()
        self._build_columns()
        self._build_tool()
        self._apply_backdrop()
        self.render_all()
        self.root.bind_all("<MouseWheel>", self._on_wheel)
        self.root.after(600, self._check_reminders)
        self.tray = TrayIcon(self)
        self.tray.start()
        self.root.after(TRAY_POLL_MS, self._tray_pump)

        if os.environ.get("TODOBOARD_DEBUG"):
            print(f"[TodoBoard] dpi={DPI} scale={SCALE:.2f} "
                  f"screen={self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()} "
                  f"cols={len(self.columns)} tasks={len(self.store.tasks)} "
                  f"acrylic={self.acrylic}", flush=True)

    # -- 窗口基础设施 ----------------------------------------------------
    @property
    def dlg_parent(self):
        """对话框挂在谁下面。**不能用 root** —— 它是 withdraw 的,拿一个不可见
        的窗口当 owner,弹窗有可能压根不显示;而且 _center 是照父窗定位的,
        藏起来的 root 会把每个对话框都算到屏幕左上角去。"""
        return self.toolwin or self.root

    def _win_hwnd(self, win):
        """窗口真正的顶层句柄。overrideredirect 的 Tk 窗外面还裹着一层系统
        框架,圆角和毛玻璃都得挂在那上面,挂错对象就静默无效。"""
        return ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()

    def _round_corners(self, win):
        """Win11 圆角;失败无副作用。"""
        try:
            win.update_idletasks()
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                self._win_hwnd(win), 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:
            pass

    def _style_window(self, win):
        """无边框窗的公共装束:圆角 + 重新映射时补挂毛玻璃。"""
        win.overrideredirect(True)
        win.configure(bg=C_PANEL)
        win.attributes("-topmost", self.topmost)
        win.update_idletasks()
        self._round_corners(win)
        # 必须判断 e.widget is win:子控件的 <Map> 也会走到挂在顶层窗上的这个
        # 绑定(顶层的路径在子控件的 bindtags 里),不筛的话建界面那一下会触发
        # 几十次。锁屏解锁、切远程桌面之后 DWM 会把这个属性丢掉,窗口变回一块
        # 不透明的黑,重新 Map 一次正好补回来。
        win.bind("<Map>", lambda e, w=win: self._apply_one(w)
                 if e.widget is w else None, add="+")
        win.bind("<Alt-F4>", lambda e: self.quit_app())

    def _app_windows(self):
        out = [c["win"] for c in self.columns]
        if self.toolwin is not None:
            out.append(self.toolwin)
        return out

    def _apply_one(self, win):
        """一扇窗挂上当前该有的透明方式。"""
        try:
            if self.acrylic:
                set_backdrop(self._win_hwnd(win), ACCENT_ACRYLIC,
                             self._tint_for(win), self.opacity)
                win.attributes("-alpha", 1.0)
            else:
                set_backdrop(self._win_hwnd(win), ACCENT_DISABLED)
                win.attributes("-alpha", self.opacity)
        except tk.TclError:
            pass

    def _tint_of(self, col):
        return glass_tint(col["data"].get("color", C_ACCENT))

    def _tint_for(self, win):
        """这扇窗该用哪块底色。栏目窗用自己的栏色,工具条那些是中性黑。"""
        for c in self.columns:
            if c["win"] is win:
                return c.get("_tint") or self._tint_of(c)
        return C_PANEL

    def _apply_col_tint(self, col):
        """把这一栏整块玻璃刷成它自己的颜色。

        两条路都得刷:挂上毛玻璃的机器,透出来的是 DWM 那层 tint;挂不上的
        (旧系统、远程桌面、驱动不给面子),看到的就是 canvas 自己的背景色。
        哪条路生效由机器决定,这边两边都摆好,用户不用知道自己是哪一种。
        """
        want = self._tint_of(col)
        if col.get("_tint") == want:
            return
        col["_tint"] = want
        for w in (col["win"], col["hdr"], col["canvas"],
                  col["rz_l"], col["rz_r"], col["rz_b"]):
            try:
                w.configure(bg=want)
            except tk.TclError:
                pass
        if self.acrylic:
            try:
                set_backdrop(self._win_hwnd(col["win"]), ACCENT_ACRYLIC,
                             want, self.opacity)
            except tk.TclError:
                pass

    def _apply_backdrop(self):
        """决定这一整套窗到底是毛玻璃还是均匀半透明。

        毛玻璃是加分项不是必需品:API 调不通(旧系统、远程桌面、某些驱动)就
        原样退回均匀透明,-alpha 那条路一直是通的,不会因为一个装饰性的效果把
        整个程序搞黑或者搞没。

        探一次就够 —— 同一台机器上所有窗是一起成功或一起失败的,没必要每扇
        都试一遍再各自为政,那样反而会出现"这一栏糊了那一栏没糊"的怪状。
        """
        want = (bool(self.store.settings.get("acrylic", True))
                and not os.environ.get("TODOBOARD_NO_ACRYLIC"))
        wins = self._app_windows()
        if not wins:
            return False
        self.acrylic = bool(want) and bool(set_backdrop(
            self._win_hwnd(wins[0]), ACCENT_ACRYLIC, C_PANEL, self.opacity))
        for w in wins:
            self._apply_one(w)
        # 挂不上不写回设置:万一是这次启动的偶发失败(远程桌面、驱动还在初始化),
        # 写回去等于永久关掉。真要关由用户在设置里关(见 SettingsDialog._on_acrylic)。
        return self.acrylic

    def apply_opacity(self):
        """「不透明度」变了。挂上毛玻璃时改的是那层底色的浓度,没挂上时改的是
        整扇窗的 alpha —— 同一条滑杆,两种走法,用户不用关心区别。"""
        for w in self._app_windows():
            self._apply_one(w)

    def reapply_backdrop(self):
        """重新挂钩毛玻璃。锁屏解锁、改分辨率、切远程桌面之后 DWM 会把这个
        属性丢掉,窗口就变回一块不透明的黑 —— 重新挂一次就好了。"""
        if self.acrylic or self.store.settings.get("acrylic", True):
            self._apply_backdrop()

    # -- 分栏窗 ----------------------------------------------------------
    def _col_w_logical(self, col):
        return int(col.get("w") or self.col_w)

    def _col_h_logical(self, col):
        return int(col.get("h") or self.store.settings.get("height", 560))

    def _col_geom(self, col):
        """算出这一栏窗口该在的位置和大小,一律夹进屏幕内。

        不管坐标是刚算出来的还是上次存下的,都在这里过一遍 —— 换了显示器、
        拔了外接屏之后,上回的位置很可能已经在屏幕外面了。
        """
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = max(px(COL_W_MIN), min(px(self._col_w_logical(col)), sw - px(16)))
        h = max(px(COL_H_MIN), min(px(self._col_h_logical(col)), sh - px(16)))
        x = int(col.get("x") if col.get("x") is not None else 0)
        y = int(col.get("y") if col.get("y") is not None else 0)
        x = max(0, min(x, max(0, sw - w)))
        y = max(0, min(y, max(0, sh - h)))
        return x, y, w, h

    def _place_defaults(self):
        """给还没有坐标的分栏排位:贴着屏幕右上角、从右往左铺开。

        和旧版面板默认出生的位置一致 —— 升级上来的人不会觉得窗口"自己跑了"。
        """
        cols = self.store.columns
        sw = self.root.winfo_screenwidth()
        gap = px(COL_GAP_OUT)
        n = len(cols)
        for i, col in enumerate(cols):
            if col.get("x") is not None and col.get("y") is not None:
                continue
            w = px(self._col_w_logical(col))
            col["x"] = int(sw - (n - i) * (w + gap))
            col["y"] = 0

    def _build_columns(self):
        """按数据重建所有分栏窗。数量或顺序一变就走这里。"""
        for c in self.columns:
            try:
                c["win"].destroy()
            except Exception:
                pass
        self.columns = []
        self._place_defaults()
        for col in self.store.columns:
            self._make_column(col)
        self._apply_backdrop()
        if self._collapsed:
            self._apply_collapse()
        self._sync_anchor()

    def _make_column(self, col):
        x, y, w, h = self._col_geom(col)
        # 整块玻璃的底色 —— 建窗口时就带上,免得先黑一下再变色
        tint = glass_tint(col.get("color", C_ACCENT))
        win = tk.Toplevel(self.root)
        self._style_window(win)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.configure(bg=tint)

        hdr = tk.Canvas(win, bg=tint, highlightthickness=0, bd=0)
        cv = tk.Canvas(win, bg=tint, highlightthickness=0, bd=0,
                       yscrollincrement=px(24))
        entry = {"id": col["id"], "win": win, "hdr": hdr, "canvas": cv,
                 "data": col, "_tint": tint,
                 "rz_l": tk.Frame(win, bg=tint, cursor="sb_h_double_arrow"),
                 "rz_r": tk.Frame(win, bg=tint, cursor="sb_h_double_arrow"),
                 "rz_b": tk.Frame(win, bg=tint, cursor="sb_v_double_arrow")}
        self.columns.append(entry)
        self._layout_column(entry, w, h)

        # 点在**某个 item 上**:走 tag_bind,而且必须返回 "break"。
        #
        # 为什么非得用 all 这个标签,而不是画布自己的 <Button-1>:Tk 画布把
        # 一次 Button-1 分两路派发 —— 先按 item 的标签派发一遍,再走画布自身
        # 的绑定。而第二路**会被第一路顶掉**:当指针底下那个 item 的标签里
        # 有一条挂了 Button-1 绑定(本栏的「添加任务」就是),画布自身的绑定
        # 就不再触发了。实测:任务未完成时,圆心底下是那个实心的"命中的底",
        # 它没有 Button-1 绑定,画布的绑定照跑;一旦任务完成,圆心底下多了
        # 一条斜的勾(包围盒罩住圆心),它上面的 check 标签改变了两路派发的
        # 归属,画布的绑定整个哑掉 —— 于是"完成态点圈没反应"。
        #
        # all 是每个 item 都带的隐含标签,谁在上面都逃不掉,和形状无关。
        cv.tag_bind("all", "<Button-1>",
                    lambda e, c=entry: self._col_item_press(e, c))
        # 点在**空白处**:底下没有 item,上面那路不会触发,靠这一条兜住。
        cv.bind("<Button-1>", lambda e, c=entry: self._col_press(e, c))
        cv.bind("<B1-Motion>", lambda e, c=entry: self._col_motion(e, c))
        cv.bind("<ButtonRelease-1>", lambda e, c=entry: self._col_release(e, c))
        cv.bind("<Double-Button-1>", lambda e, c=entry: self._col_dblclick(e, c))
        cv.bind("<Button-3>", lambda e, c=entry: self._col_right(e, c))

        hdr.bind("<Button-1>", lambda e, c=entry: self._hdr_press(e, c))
        hdr.bind("<B1-Motion>", self._win_move)
        hdr.bind("<ButtonRelease-1>", lambda e: self._save_geom())

        for side in ("rz_l", "rz_r", "rz_b"):
            f = entry[side]
            f.bind("<Button-1>",
                   lambda e, c=entry, s=side: self._rsz_press(e, c, s))
            f.bind("<B1-Motion>", self._rsz_move)
            f.bind("<ButtonRelease-1>", lambda e: self._rsz_done())
        return entry

    def _layout_column(self, e, w=None, h=None):
        """摆好一栏窗里的四块东西,并把窗口调到 w×h(物理像素)。

        用 place 而不是 pack:左/右/下三条改尺寸的热区要**压在内容上面**,
        pack 做不到重叠,place 一行就够。热区只有 EDGE_W 逻辑像素宽,不挡
        正常操作,但鼠标贴到边上就能拖出光标。
        """
        win = e["win"]
        if w is None or h is None:
            _, _, dw, dh = self._col_geom(e["data"])
            w = w or dw
            h = h or dh
        win.geometry(f"{w}x{h}")
        hh, rh = px(COL_HDR_H), px(EDGE_W)
        e["hdr"].place(x=0, y=0, width=w, height=hh)
        e["canvas"].place(x=0, y=hh, width=w,
                          height=max(px(40), h - hh - rh))
        e["rz_l"].place(x=0, y=0, width=rh, height=h)
        e["rz_r"].place(x=max(0, w - rh), y=0, width=rh, height=max(0, h - rh))
        e["rz_b"].place(x=0, y=max(0, h - rh), width=w, height=rh)

    def _sync_anchor(self):
        """给提醒卡片和提示条留一个"面板在哪"的参照 —— 取第一栏。"""
        if not self.columns:
            return
        win = self.columns[0]["win"]
        try:
            win.update_idletasks()
            self.win_x, self.win_y = win.winfo_x(), win.winfo_y()
            self.win_w, self.win_h = win.winfo_width(), win.winfo_height()
        except tk.TclError:
            pass

    # -- 工具条 ----------------------------------------------------------
    def _build_tool(self):
        st = self.store.settings
        w = px(TOOL_W)
        h = px(COL_HDR_H)
        sw = self.root.winfo_screenwidth()
        x, y = st.get("tool_x"), st.get("tool_y")
        if x is None or y is None:
            if self.columns:
                first = self.columns[0]["win"]
                last = self.columns[-1]["win"]
                # 优先挨着最后一栏的右边;右边放不下就改放第一栏左边。
                #
                # 这里**不能**直接 clamp 回屏幕内:屏幕右边放不下时,clamp 出来
                # 的位置正好落在最后一栏的头上,把它标题条上的「⋯」菜单盖掉
                # (实机截图里就是这样)。宁可换到另一头,也不能压在栏身上。
                x = last.winfo_x() + last.winfo_width() + px(COL_GAP_OUT)
                y = last.winfo_y()
                if x + w > sw:
                    x = first.winfo_x() - px(COL_GAP_OUT) - w
                if x < 0:
                    # 两头都挤不下(屏幕上栏太多),退回屏幕右下角 ——
                    # 宁可离得远,也不叠在栏身上
                    x, y = sw - w - px(24), 0
            else:
                x, y = sw - w - px(24), 0
        x = max(0, min(int(x), max(0, sw - w)))
        y = max(0, min(int(y), self.root.winfo_screenheight() - h))

        win = tk.Toplevel(self.root)
        self._style_window(win)
        win.geometry(f"{w}x{h}+{x}+{y}")
        cv = tk.Canvas(win, bg=C_PANEL, width=w, height=h,
                       highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True)
        self.toolwin, self.toolcv = win, cv
        cv.bind("<Button-1>", self._tool_press)
        cv.bind("<B1-Motion>", self._tool_move)
        cv.bind("<ButtonRelease-1>", lambda e: self._save_tool_geom())
        self._render_tool()

    def _render_tool(self):
        cv = self.toolcv
        if cv is None:
            return
        cv.delete("all")
        w = max(cv.winfo_width(), px(TOOL_W))
        h = px(COL_HDR_H)
        cy = h / 2

        # 和旧标题栏同一套语言:一条极轻的竖向渐变 + 顶边 1px 高光。
        # 那条高光就是"这是一块玻璃"的全部证据,别省。
        vgrad(cv, 0, 0, w, h, C_HEADER, C_HEADER_2, 16)
        self._hdr_bg = _mix(C_HEADER, C_HEADER_2, cy / h)
        rim = px(8)
        cv.create_rectangle(rim, 0, w - rim, px(1), fill=C_RIM, outline="")

        # 从左到右:新建分栏 / 撤回 / 重做 / 置顶(唯一的彩色,它是状态灯)/
        # 收起 / 设置 / 退出。整个程序就这一个条挂全局操作。
        #
        # 撤回和重做没得撤的时候压到最暗那一级(看起来就是"灰着的"),但不真的
        # 禁掉点击 —— 点一下会说"没有可以撤回的操作了",比一个死活按不动的
        # 按钮更容易明白。另外**没有键盘快捷键**:这是桌面角落的一小块面板,
        # 抢 Ctrl+Z 会跟当前正在用的程序打架。
        top_on = bool(self.topmost)
        undo_on = self.hist.can_undo()
        redo_on = self.hist.can_redo()
        specs = [
            ("add", icon_plus, self.add_column,
             C_TEXT_MUTE, C_TEXT, C_HOVER_BG),
            ("undo", icon_undo, self.undo,
             C_TEXT_MUTE if undo_on else C_TEXT_FAINT, C_TEXT, C_HOVER_BG),
            ("redo", icon_redo, self.redo,
             C_TEXT_MUTE if redo_on else C_TEXT_FAINT, C_TEXT, C_HOVER_BG),
            ("top", icon_top, self.toggle_top,
             C_ACCENT if top_on else C_TEXT_MUTE, C_ACCENT, "#22190D"),
            ("collapse", lambda c, x, y, k, col, tg: icon_chevron(
                c, x, y, k, col, down=not self._collapsed, tags=tg),
             self.toggle_collapse, C_TEXT_MUTE, C_TEXT, C_HOVER_BG),
            ("gear", lambda c, x, y, k, col, tg: icon_gear(c, x, y, k, col, tg),
             self.open_settings, C_TEXT_MUTE, C_TEXT, C_HOVER_BG),
            ("close", lambda c, x, y, k, col, tg: icon_close(c, x, y, k, col, tg),
             self.quit_app, C_TEXT_MUTE, C_OVERDUE, "#241517"),
        ]
        self._icons = {}
        step = px(30)
        cx = px(TOOL_PAD) + step / 2
        for name, draw, cmd, base, hov, hovbg in specs:
            tag = f"tb:{name}"
            half = px(13)
            bg = round_rect(cv, cx - half, cy - half, cx + half, cy + half,
                            px(7), fill=self._hdr_bg, outline="", tags=(tag,))
            draw(cv, cx, cy, px(14), base, (tag,))
            self._icons[name] = {"bg": bg, "tag": tag, "hovbg": hovbg,
                                 "base": base, "hov": hov}
            cv.tag_bind(tag, "<Enter>",
                        lambda e, n=name: self._icon_hover(n, True))
            cv.tag_bind(tag, "<Leave>",
                        lambda e, n=name: self._icon_hover(n, False))
            cv.tag_bind(tag, "<Button-1>",
                        lambda e, c=cmd: self._icon_click(c))
            cx += step

    def _icon_hover(self, name, on):
        d = self._icons.get(name)
        cv = self.toolcv
        if not d or cv is None:
            return
        try:
            cv.itemconfig(d["bg"], fill=d["hovbg"] if on else self._hdr_bg)
            col = d["hov"] if on else d["base"]
            for it in cv.find_withtag(d["tag"]):
                if it == d["bg"]:
                    continue
                # 齿轮的外圈用的是 outline,不是 fill,得分别设
                if cv.type(it) in ("oval", "polygon", "rectangle"):
                    cv.itemconfig(it, outline=col)
                cv.itemconfig(it, fill=col)
        except tk.TclError:
            pass

    def _icon_click(self, cmd):
        cmd()
        return "break"   # 别把点击再传给工具条的拖动处理

    def _tool_press(self, e):
        self._tool_drag = (e.x_root - self.toolwin.winfo_x(),
                           e.y_root - self.toolwin.winfo_y())

    def _tool_move(self, e):
        if not self._tool_drag:
            return
        dx, dy = self._tool_drag
        x, y = self._snap_win(self.toolwin, e.x_root - dx, e.y_root - dy)
        self.toolwin.geometry(f"+{int(x)}+{int(y)}")

    def _save_tool_geom(self):
        self._tool_drag = None
        if self.toolwin is None:
            return
        self.store.settings["tool_x"] = self.toolwin.winfo_x()
        self.store.settings["tool_y"] = self.toolwin.winfo_y()
        self.store.save()

    # -- 渲染 ------------------------------------------------------------
    def render_all(self):
        self.card_geo.clear()
        self.card_rect.clear()
        self.card_canvas.clear()
        self.card_hair.clear()
        self.card_check.clear()
        self.card_check_geo.clear()
        self.card_ring.clear()
        self._shake_tids.clear()
        self.card_del.clear()
        self.card_del_geo.clear()
        self._hov_tid = None
        self.add_geo.clear()
        for c in self.columns:
            self._apply_col_tint(c)
            self.render_column(c)
        self._render_tool()
        # 重画之后环的位置全变了,抖动的对象也重新数过一遍
        self._shake_sync()

    # -- 进度环:跟着时间走、快到了就抖 --------------------------------
    def _shake_dx(self):
        """这一拍环该偏多少。

        0 → 右 → 0 → 左,四个相位一个来回。用正弦而不是随机数:随机看着像
        卡顿,来回摆才像"在震"。相位一次只推一格,所以中途重画也还在同一个
        节奏上,不会跳。
        """
        return px(SHAKE_PX) * math.sin(self._shake_phase * math.pi / 2)

    def _ring_place(self, tid, dx):
        """把一个环按"基准方框 + 偏移"摆好。

        用 coords 绝对定位,不用 canvas.move —— move 是累加的,每拍再挪一次
        dx,两秒钟这环就飞出栏外了。
        """
        got = self.card_ring.get(tid)
        if not got:
            return
        item, cv, (x0, y0, x1, y1) = got
        try:
            cv.coords(item, x0 + dx, y0, x1 + dx, y1)
        except tk.TclError:
            pass

    def _shake_tick(self):
        """抖一拍,再约下一拍。"""
        self._shake_job = None
        if not self._shake_tids:
            self._shake_phase = 0
            return
        self._shake_phase = (self._shake_phase + 1) % 4
        dx = self._shake_dx()
        for tid in list(self._shake_tids):
            self._ring_place(tid, dx)
        self._shake_job = self.root.after(SHAKE_MS, self._shake_tick)

    def _shake_sync(self):
        """该抖的让它抖着,没有了就停。

        没有任务快结束时,面板连一个定时器都不留 —— 常驻桌面的东西,不该
        在那儿空转。停下来时把环摆回正中,免得它歪着留在屏幕上。
        """
        if self._shake_tids:
            if self._shake_job is None:
                self._shake_job = self.root.after(SHAKE_MS, self._shake_tick)
            return
        if self._shake_job is not None:
            try:
                self.root.after_cancel(self._shake_job)
            except tk.TclError:
                pass
            self._shake_job = None
        if self._shake_phase:
            self._shake_phase = 0
            for tid in list(self.card_ring):
                self._ring_place(tid, 0)

    def _refresh_rings(self):
        """时间往前走,环也得跟着长 —— 每半分钟跟提醒一起扫一遍。

        不整栏重画:重画会把悬停和滚动都抖一下,而这里变的只是几个弧的角度。
        顺手把"快结束了"的名单也更新了:刚够着线的开始抖,缓过来的停下。
        """
        urgent = set()
        for tid, (item, cv, box) in list(self.card_ring.items()):
            t = self.store.task(tid)
            rp = ring_progress(t) if t else None
            if not rp:
                continue
            p, color, is_urgent = rp
            try:
                cv.itemconfigure(item,
                                 extent=-max(RING_MIN_DEG, 359.9 * p),
                                 outline=color)
            except tk.TclError:
                continue
            if is_urgent:
                urgent.add(tid)
        if urgent != self._shake_tids:
            self._shake_tids = urgent
            for tid in list(self.card_ring):
                self._ring_place(tid, 0)
            self._shake_sync()

    def visible_tasks(self, cid):
        out = []
        for t in self.store.tasks:
            if t["column_id"] != cid:
                continue
            if self.hide_done and t["done"]:
                continue
            out.append(t)
        return out

    def render_column(self, col):
        cv = col["canvas"]
        # 重画前先记住滚到哪儿了。勾一条任务就会整栏重画,不记住的话列表会
        # 立刻弹回顶部 —— 长列表里改一条就得重新滚下去,很烦。
        try:
            frac = cv.yview()[0]
        except tk.TclError:
            frac = 0.0
        cv.delete("all")
        w = px(self._col_w_logical(col["data"]))

        self._render_col_header(col)

        tasks = self.visible_tasks(col["id"])
        y = 0
        for t in tasks:
            h = self._draw_card(cv, t, y, w)
            self.card_geo[t["id"]] = (y, h)
            y += h        # 行高已含发丝线,不再额外留空隙

        if not tasks:
            # 空态:一句很轻的提示,不画虚线框(框 = 又多一个方块)
            cv.create_text(w / 2, px(40), text="双击这里添加任务",
                           font=F.tiny, fill=C_TEXT_FAINT, anchor="n")
            y = px(88)

        y = self._draw_add_row(cv, col, y, w)
        cv.configure(scrollregion=(0, 0, w, y + px(14)))
        cv.yview_moveto(frac)

    def _render_col_header(self, col):
        hdr = col["hdr"]
        hdr.delete("all")
        w = px(self._col_w_logical(col["data"]))
        h = px(COL_HDR_H)
        cy = h / 2
        data = col["data"]
        color = data.get("color", C_ACCENT)

        # 每一栏都是一块独立的玻璃,都得有自己那道顶边高光 —— 少了它,这栏
        # 就是一块平涂的黑,和"玻璃"没关系。两端让开圆角半径。
        rim = px(10)
        hdr.create_rectangle(rim, 0, w - rim, px(1), fill=C_RIM, outline="")

        # 色条:小小的 3px 竖条,是这一栏的"身份色"
        round_rect(hdr, px(COL_PAD), cy - px(7), px(COL_PAD) + px(3), cy + px(7),
                   px(1.5), fill=color, outline="")

        # 标题条右边的两个按钮:先清空(垃圾桶),再菜单(⋯)。
        # 名字长了就截断 —— 以前是原样画的,栏名一长就钻到 ⋯ 底下去了。
        bx, mx, _ = self._hdr_btn_xy(col)
        nx = px(COL_PAD) + px(11)
        avail = bx - px(13) - nx - px(20)     # 20 留给后面那个计数
        name = ellipsize(data["name"], F.col, avail)
        hdr.create_text(nx, cy, text=name, font=F.col,
                        fill=C_TEXT_DIM, anchor="w")

        n = len([t for t in self.store.tasks
                 if t["column_id"] == col["id"] and not t["done"]])
        if n:
            # 计数就是个淡数字,不给它胶囊底
            cx = nx + F.col.measure(name) + px(7)
            hdr.create_text(cx, cy + px(1), text=str(n), font=F.tiny,
                            fill=C_TEXT_MUTE, anchor="w")

        # 清空本栏
        btag = f"cdel:{col['id']}"
        brect = round_rect(hdr, bx - px(12), cy - px(12), bx + px(12), cy + px(12),
                           px(6), fill=C_PANEL, outline="", tags=(btag,))
        icon_trash(hdr, bx, cy, px(13), C_TEXT_MUTE, tags=(btag,))
        hdr.tag_bind(btag, "<Enter>", lambda e, r=brect, t=btag:
                     self._hdr_btn(hdr, r, t, True, True))
        hdr.tag_bind(btag, "<Leave>", lambda e, r=brect, t=btag:
                     self._hdr_btn(hdr, r, t, False, True))
        hdr.tag_bind(btag, "<Button-1>",
                     lambda e, c=col: self.clear_column(c))

        # ⋯ 分栏菜单
        tag = f"cmenu:{col['id']}"
        mrect = round_rect(hdr, mx - px(12), cy - px(12), mx + px(12), cy + px(12),
                           px(6), fill=C_PANEL, outline="", tags=(tag,))
        icon_dots(hdr, mx, cy, px(13), C_TEXT_MUTE, tags=(tag,))
        hdr.tag_bind(tag, "<Enter>", lambda e, r=mrect, t=tag:
                     self._hdr_btn(hdr, r, t, True, False))
        hdr.tag_bind(tag, "<Leave>", lambda e, r=mrect, t=tag:
                     self._hdr_btn(hdr, r, t, False, False))
        hdr.tag_bind(tag, "<Button-1>",
                     lambda e, c=col: self._col_menu(e, c))

        # 底边这条发丝线走这一栏的颜色。
        #
        # 以前"这一栏的颜色"只体现在左边那个 3×12 的小竖条上 —— 换颜色其实是
        # 换了的,但盯着看半天也看不出区别,反馈回来就成了"更换颜色没反应"。
        # 让它顺着整条底线走一遍:一眼看得见,又没往界面上多加任何东西(这条
        # 线本来就在,只是从灰的变成有色的)。压暗三成,免得比栏名还抢眼。
        hdr.create_line(0, h - 1, w, h - 1, fill=_mix(color, C_PANEL, 0.28))

    def _hdr_btn_xy(self, col):
        """标题条上那两个小按钮:圆心 x(先垃圾桶、后 ⋯)和共同的高度中线。

        画的地方和判定"点没点到"的地方共用这一个算法 —— 两边各算一份的话,
        哪天挪了按钮,点中的范围就悄悄跟画的位置错开了。
        """
        w = px(self._col_w_logical(col["data"]))
        cy = px(COL_HDR_H) / 2
        mx = w - px(COL_PAD) - px(10)
        return mx - px(26), mx, cy

    def _hdr_on_btn(self, col, x, y):
        """这一下是不是落在那两个小按钮上。

        判定范围就是画出来的 24×24 —— 不多不少。往外多放一圈看着"更好点",
        其实会做出死区:那圈上没有按钮的画,点下去菜单不开、窗也不拖,用户
        只觉得"这儿没反应"。
        """
        bx, mx, cy = self._hdr_btn_xy(col)
        if abs(y - cy) > px(12):
            return False
        r = px(12)
        return abs(x - bx) <= r or abs(x - mx) <= r

    def _hdr_press(self, e, col):
        """标题条上按下 = 拖整栏,但按在那两个小按钮上不算。

        一开始是想让按钮的 tag_bind 返回 "break" 把这一下吃掉,结果 Tk 的
        canvas 压根不认:item 绑定和 widget 绑定是两路,都会跑到,"break"
        只断得掉 item 自己那一串(拿最小 tkinter 试过)。所以只能在这儿自己
        判一次 —— 不判的话,点 ⋯ 会顺手把这一栏"抓"起来,而且菜单把后面的
        ButtonRelease 吃了,那个 _win_drag 再也没人来清,这一栏就一直黏在
        鼠标上,直到下一次随手点一下才掉。
        """
        if self._hdr_on_btn(col, e.x, e.y):
            return
        self._win_press(e, col)

    def _hdr_btn(self, hdr, rect, tag, on, danger):
        """标题条上小按钮的悬停。danger=True 的(垃圾桶)悬停变红,别的只是提亮。"""
        try:
            hdr.itemconfig(rect, fill=C_HOVER_BG if on else C_PANEL)
            col = (C_OVERDUE if danger else C_TEXT) if on else C_TEXT_MUTE
            for it in hdr.find_withtag(tag):
                if it == rect:
                    continue
                hdr.itemconfig(it, fill=col)
        except tk.TclError:
            pass

    def _draw_add_row(self, cv, col, y, w):
        """底部「添加任务」。做成和任务行同构的一行,只是整体压暗两三档,
        这样它属于列表的一部分,而不是列表外面挂的一个按钮。"""
        h = px(38)
        tag = f"add:{col['id']}"
        tags = ("addbtn", tag)
        box = cv.create_rectangle(0, y, w, y + h, fill="", outline="", tags=tags)
        cy = y + h / 2
        icon_plus(cv, _row_box_cx(), cy, px(13), C_TEXT_FAINT, tags=tags)
        cv.create_text(_row_text_x(), cy, text="添加任务", font=F.small,
                       fill=C_TEXT_FAINT, anchor="w", tags=tags)

        def hov(on):
            try:
                cv.itemconfig(box, fill=C_ADD_HOV if on else "")
                for it in cv.find_withtag(tag):
                    if it != box:
                        cv.itemconfig(it, fill=C_TEXT_MUTE if on else C_TEXT_FAINT)
            except tk.TclError:
                pass

        cv.tag_bind("addbtn", "<Enter>", lambda e: hov(True))
        cv.tag_bind("addbtn", "<Leave>", lambda e: hov(False))
        # 「添加任务」的点击不在这里接 —— 交给 _col_press 按几何判定。
        # 在这里再挂一个 Button-1,真人点一下会触发两遍(画布的 item 绑定和
        # 窗口绑定都会跑),凭空多出一条任务。
        cv.tag_bind(tag, "<Button-3>", lambda e, c=col: self._col_right(e, c))
        self.add_geo[col["id"]] = (y, y + h)
        return y + h

    def _draw_card(self, cv, t, y, w):
        """画一行任务。

        x 从 0 到 w(整栏宽),悬停填充才能顶满两侧 —— 像邮件列表那样。
        旧版把每一行做成居中的圆角卡片,四边都要留缝,于是整块面板被切成
        一堆小方块,这是"廉价"最直观的来源。
        """
        done = t["done"]
        tid = t["id"]
        tags = ("card", f"task:{tid}")

        cb = px(15)
        cbx = px(CARD_PAD)
        tx = _row_text_x()
        tw = w - px(CARD_PAD) - tx

        title_font = F.taskd if done else F.task
        dtext, dcolor = due_label(t.get("due"), done)

        # 标题行右边留出叉号的位置。只有标题这一行让 —— 内容行照旧铺到边,
        # 叉号只占第一行的行高,内容那一行空出一块反而显得栏被啃掉一口。
        del_w = px(18)
        due_right = w - px(CARD_PAD) - del_w

        # 截止时间右对齐挂在标题第一行上(邮件列表那种排法):省掉一整行,
        # 同一屏能多放三成任务。标题在"剩下的宽度"里换行,所以永远不会撞上。
        due_w = (F.tiny.measure(dtext) + px(12)) if dtext else 0
        tlines = wrap_text(t.get("title") or "未命名", title_font,
                           tw - del_w - due_w, 2)
        clines = wrap_text(t.get("content") or "", F.meta, tw, 2)

        # 行高按字体真实行高算,换字体/换 DPI 都不会挤在一起
        lh_t = title_font.metrics("linespace") + px(2)
        lh_c = F.meta.metrics("linespace") + px(1)
        hgt = px(ROW_PAD_Y) * 2 + len(tlines) * lh_t
        if clines:
            hgt += px(1) + len(clines) * lh_c
        hgt -= px(1)

        # 悬停填充:平时完全透明,所以整栏看起来是纯文字,没有任何框
        rect = cv.create_rectangle(0, y, w, y + hgt, fill="", outline="",
                                   tags=tags)
        self.card_rect[tid] = rect
        self.card_canvas[tid] = cv

        # 勾选圈,垂直对齐第一行标题
        cby = y + px(ROW_PAD_Y) + (lh_t - px(4)) / 2 - cb / 2
        # 命中的"底":一个填成本行底色的圆,比圈本身大一圈。
        # Tk 里 fill="" 的圆**只有描边能被点到**,圆心是空的 —— 点圆心会穿到
        # 下面那一行矩形上,于是"点圈"变成了"打开编辑页"。填一个和行底同色的
        # 圆把这块补实:平时看不见,但点得到;悬停时跟着行底一起变色。
        hp = px(4)
        hit = cv.create_oval(cbx - hp, cby - hp, cbx + cb + hp, cby + cb + hp,
                             fill=C_PANEL, outline="", tags=tags + ("check",))
        self.card_check[tid] = hit
        # 这一圈可点范围,点圈时靠它判定(为什么不用 Tk 的 "current",见
        # _hit_test 的说明)。比画出来的圈略大一点点,手指落偏一点也认。
        pad = px(5)
        self.card_check_geo[tid] = (cbx - pad, cby - pad,
                                    cbx + cb + pad, cby + cb + pad)
        cv.create_oval(cbx, cby, cbx + cb, cby + cb,
                       outline=C_RING, width=px(1.4), fill="",
                       tags=tags + ("check",))
        # 有截止时间的,这一圈兼当进度环:从十二点顺时针长过去,长满就到点。
        # 底下那圈灰的当轨道,弧叠在上面。快结束了会轻轻抖(见 _shake_tick)——
        # 那是整块面板上唯一会动的东西,别处一律安静,不然就成迪厅了。
        rp = ring_progress(t)
        if rp:
            p, rcolor, urgent = rp
            arc = cv.create_arc(cbx, cby, cbx + cb, cby + cb,
                                start=90,
                                extent=-max(RING_MIN_DEG, 359.9 * p),
                                style="arc", outline=rcolor, width=px(1.8),
                                tags=tags + ("check",))
            self.card_ring[tid] = (arc, cv, (cbx, cby, cbx + cb, cby + cb))
            if urgent:
                self._shake_tids.add(tid)
                self._ring_place(tid, self._shake_dx())
        if done:
            # 完成态 = 暗圈 + 暗勾,不填色。做完的事不该比没做完的更亮,
            # 这一行的标题已经有删除线了,足够了。
            #
            # 带上 check 标签是给悬停和调试看的,不再是"点得动"的前提 ——
            # 点圈的判定已经改成按几何算(_hit_test)。这条勾斜跨圆心上方,
            # 包围盒罩住整个圆心,靠 Tk 的 current 去认它必然出错。
            cv.create_line(cbx + cb * 0.27, cby + cb * 0.52,
                           cbx + cb * 0.44, cby + cb * 0.69,
                           cbx + cb * 0.74, cby + cb * 0.32,
                           fill=C_DONE, width=px(1.6),
                           capstyle="round", joinstyle="round",
                           tags=tags + ("check",))

        ty = y + px(ROW_PAD_Y)

        # 行尾的叉号 —— 一下点掉,不弹确认。

        # 平时它退到最暗那一级,鼠标扫过这一行才提亮;扫到叉号本身变红(和
        # 工具条的 ✕ 同一套语言)。整列挂着排暗淡的叉号会显得脏,但完全藏起
        # 来又找不着 —— 静态压到最低、有反馈时再出来,是这两者的折中。

        # 底下垫一个和行底同色的圆:icon_close 画的是两条斜线,线中间是空的,
        # 鼠标落在两条线当中反而不算"落在叉号上"。和勾选圈同样的处理。
        dcx = w - px(CARD_PAD) - del_w / 2
        dcy = ty + lh_t / 2
        dhp = px(9)
        dhit = cv.create_oval(dcx - dhp, dcy - dhp, dcx + dhp, dcy + dhp,
                              fill=C_PANEL, outline="", tags=tags + ("delhit",))
        self.card_del[tid] = dhit
        self.card_del_geo[tid] = (dcx - dhp, dcy - dhp, dcx + dhp, dcy + dhp)
        dtag = f"del:{tid}"
        icon_close(cv, dcx, dcy, px(11), C_TEXT_FAINT,
                   tags=(dtag, "card", f"task:{tid}"))
        cv.tag_bind(dtag, "<Enter>",
                    lambda e, t=tid: self._del_hover(t, True))
        cv.tag_bind(dtag, "<Leave>",
                    lambda e, t=tid: self._del_hover(t, False))

        if dtext:
            # 让两种字号"坐在同一条基线"上:各字体的 ascent 不一样,
            # 都按顶端对齐的话小字会浮起来一点。
            dy = ty + title_font.metrics("ascent") - F.tiny.metrics("ascent")
            cv.create_text(due_right, dy, text=dtext, font=F.tiny,
                           fill=C_DONE if done else dcolor,
                           anchor="ne", tags=tags)
        for ln in tlines:
            cv.create_text(tx, ty, text=ln, font=title_font,
                           fill=C_DONE if done else C_TEXT, anchor="nw", tags=tags)
            if done:
                # 划在字面中央(不是行框中央),视觉上才是"划掉"
                my = ty + title_font.metrics("ascent") * 0.56
                cv.create_line(tx, my, tx + title_font.measure(ln), my,
                               fill=C_DONE, width=px(1.2), tags=tags)
            ty += lh_t
        if clines:
            ty += px(1)
            for ln in clines:
                cv.create_text(tx, ty, text=ln, font=F.meta,
                               fill=C_DONE if done else C_TEXT_MUTE,
                               anchor="nw", tags=tags)
                ty += lh_c

        # 发丝线:从左内边距下方起,和文字左边缘对齐(iOS 列表那种缩进分隔线)
        hair = cv.create_line(tx, y + hgt - 1, w - px(CARD_PAD), y + hgt - 1,
                              fill=C_LINE, tags=tags)
        self.card_hair[tid] = hair

        cv.tag_bind(f"task:{tid}", "<Enter>",
                    lambda e, t=tid: self._card_hover(t, True))
        cv.tag_bind(f"task:{tid}", "<Leave>",
                    lambda e, t=tid: self._card_hover(t, False))
        return hgt

    def _card_hover(self, tid, on):
        if self._pending and self._pending.get("active"):
            return
        cv = self.card_canvas.get(tid)
        rect = self.card_rect.get(tid)
        if cv is None or rect is None:
            return
        self._hov_tid = tid if on else None
        try:
            cv.itemconfig(rect, fill=C_ROW_HOV if on else "")
            hair = self.card_hair.get(tid)
            if hair is not None:
                cv.itemconfig(hair, fill=C_CARD_HOV if on else C_LINE)
            # 勾选圈那个"命中的底"也得跟着行底一起变,否则悬停时会露出一块
            # 比行底更暗的方形补丁
            hit = self.card_check.get(tid)
            if hit is not None:
                cv.itemconfig(hit, fill=C_ROW_HOV if on else C_PANEL)
            # 叉号也一样:底跟着行底走,图标本身平时压到最暗,扫过这行才提亮
            dh = self.card_del.get(tid)
            if dh is not None:
                cv.itemconfig(dh, fill=C_ROW_HOV if on else C_PANEL)
                for it in cv.find_withtag(f"del:{tid}"):
                    cv.itemconfig(it, fill=C_TEXT_MUTE if on else C_TEXT_FAINT)
        except tk.TclError:
            pass

    def _del_hover(self, tid, on):
        """鼠标扫到叉号上就变红(和工具条的 ✕ 一个语言)。

        离开时不能一律回到最暗那级:鼠标多半只是挪回了同一行里,那行还亮着,
        叉号突然沉下去很跳。所以看这一行是不是还悬停着。
        """
        cv = self.card_canvas.get(tid)
        if cv is None:
            return
        col = C_OVERDUE if on else (
            C_TEXT_MUTE if self._hov_tid == tid else C_TEXT_FAINT)
        try:
            for it in cv.find_withtag(f"del:{tid}"):
                cv.itemconfig(it, fill=col)
        except tk.TclError:
            pass

    # -- 交互:窗口拖动 / 缩放 --------------------------------------------
    def _win_press(self, e, entry):
        try:
            self._win_drag = (entry, e.x_root - entry["win"].winfo_x(),
                              e.y_root - entry["win"].winfo_y())
        except tk.TclError:
            self._win_drag = None

    def _win_move(self, e):
        if not self._win_drag:
            return
        entry, dx, dy = self._win_drag
        win = entry["win"]
        x, y = self._snap_win(win, e.x_root - dx, e.y_root - dy)
        win.geometry(f"+{int(x)}+{int(y)}")
        self._sync_anchor()

    def _snap_win(self, win, x, y):
        """吸附:屏幕四条边 + 其他窗口的边。

        拆成独立窗口之后"对齐"这件事反而更要紧 —— 几块玻璃各歪各的,比一整
        块歪着更难看。所以除了屏幕边缘,还会吸到旁边那几扇窗的左/右/中线和
        顶/底/中线,几栏随手一拖就自然摆成一条线。
        """
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = max(1, win.winfo_width())
        h = max(1, win.winfo_height())
        tol = px(SNAP_DIST)
        xs = [0, sw - w]
        ys = [0, sh - h]
        for other in self._app_windows():
            if other is win:
                continue
            try:
                ox, oy = other.winfo_x(), other.winfo_y()
                ow, oh = other.winfo_width(), other.winfo_height()
            except tk.TclError:
                continue
            xs += [ox, ox + ow - w, ox + (ow - w) / 2]
            ys += [oy, oy + oh - h, oy + (oh - h) / 2]
        for c in xs:
            if abs(x - c) <= tol:
                x = c
                break
        for c in ys:
            if abs(y - c) <= tol:
                y = c
                break
        return (max(0, min(int(x), max(0, sw - w))),
                max(0, min(int(y), max(0, sh - h))))

    def _save_geom(self):
        """拖完松手:把每扇窗的位置落盘。

        只存位置,不存宽高 —— 收起状态下窗口高度是"折叠"出来的,存下去下次
        启动就永久变矮了。宽高由 _rsz_done 单独负责。
        """
        self._win_drag = None
        for e in self.columns:
            try:
                e["data"]["x"], e["data"]["y"] = e["win"].winfo_x(), e["win"].winfo_y()
            except tk.TclError:
                pass
        self.store.save()
        self._sync_anchor()

    # -- 交互:改宽窄 / 高低 ----------------------------------------------
    def _rsz_press(self, e, entry, side):
        win = entry["win"]
        entry.pop("_rsz_cur", None)
        self._rs = (entry, side, e.x_root, e.y_root,
                    win.winfo_x(), win.winfo_y(),
                    win.winfo_width(), win.winfo_height())

    def _rsz_move(self, e):
        if not self._rs:
            return
        entry, side, x0, y0, wx, wy, ww, wh = self._rs
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        dx, dy = e.x_root - x0, e.y_root - y0
        h = wh
        if side == "rz_r":
            # 右边缘:宽跟着走,左边缘不动。上限是"屏幕右边缘 - 窗口左边",
            # 但那个值本身可能小于最窄宽度(窗口贴到屏幕最右边了),所以下限
            # 也要兜一层 —— 否则 max/min 一夹会得出一个比最窄还小的宽度。
            w = max(px(COL_W_MIN),
                    min(ww + dx, max(px(COL_W_MIN), sw - wx)))
        elif side == "rz_l":
            # 左边缘:右边钉住,只让左边界来回 —— 手感才对,不然窗口会"跑"
            w = max(px(COL_W_MIN), min(ww - dx, wx + ww))
            wx = wx + ww - w
        else:
            # 下边缘:只改高
            w = ww
            h = max(px(COL_H_MIN), min(wh + dy, sh - wy))
        self._layout_column(entry, w, h)
        entry["win"].geometry(f"{w}x{h}+{wx}+{wy}")
        if side != "rz_b":
            entry["data"]["x"] = wx
        # 松手时直接拿这份尺寸,不去问 winfo_width() —— 它要等 Tk 跑完一次
        # 重绘才更新,而 ButtonRelease 是当场处理的,读到的会是旧值。
        entry["_rsz_cur"] = (w, h, wx, wy)

    def _rsz_done(self):
        if not self._rs:
            return
        entry = self._rs[0]
        self._rs = None
        cur = entry.pop("_rsz_cur", None)
        if not cur:
            return
        w, h, wx, wy = cur
        col = entry["data"]
        col["w"] = max(COL_W_MIN, int(round(w / SCALE)))
        col["h"] = max(COL_H_MIN, int(round(h / SCALE)))
        col["x"], col["y"] = wx, wy
        self.store.save()
        self.render_column(entry)     # 宽度变了,文字要按新宽度重新折行
        self._sync_anchor()

    # -- 交互:分栏头 ----------------------------------------------------
    def _col_menu(self, e, col):
        m = tk.Menu(self.dlg_parent, tearoff=0, bg="#24243A", fg=C_TEXT,
                    activebackground=C_ACCENT, activeforeground="#FFFFFF",
                    bd=0, relief="flat")
        m.add_command(label="添加任务", command=lambda: self.add_task(col["id"]))
        m.add_separator()
        m.add_command(label="重命名分栏", command=lambda: self.rename_column(col))
        m.add_command(label="更换颜色", command=lambda: self.recolor_column(col))
        m.add_separator()
        m.add_command(label="清空本栏任务", command=lambda: self.clear_column(col))
        m.add_command(label="删除分栏", command=lambda: self.delete_column(col))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _col_right(self, e, col):
        items = col["canvas"].find_withtag("current")
        tid = None
        for it in items:
            for tag in col["canvas"].gettags(it):
                if tag.startswith("task:"):
                    tid = tag[5:]
        if not tid:
            self._col_menu(e, col)
            return
        t = self.store.task(tid)
        m = tk.Menu(self.dlg_parent, tearoff=0, bg="#24243A", fg=C_TEXT,
                    activebackground=C_ACCENT, activeforeground="#FFFFFF",
                    bd=0, relief="flat")
        m.add_command(label="编辑", command=lambda: self.edit_task(tid))
        m.add_command(label="标记为未完成" if t["done"] else "标记为已完成",
                      command=lambda: self.toggle_done(tid))
        m.add_command(label="复制一条", command=lambda: self.duplicate_task(tid))
        m.add_separator()
        m.add_command(label="删除",
                      command=lambda: self.delete_task(tid, undoable=True))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    # -- 交互:卡片 ------------------------------------------------------
    def _hit_test(self, col, x, y):
        """按画的时候记下来的几何判断这一下点在哪儿。

        这里**故意不用** Tk 的 find_withtag("current")。谁是 current 由 Tk
        按 item 的**包围盒**和叠放次序决定 —— 完成态那条勾斜跨圆心上方,它的
        包围盒正好罩住圆心,于是它替底下的圈接走了点击。上一轮给这条勾也补上
        check 标签,只是碰运气:圈当 current 时点得动,线当 current 时连 canvas
        自己的 Button-1 都不触发了(实测),点圈在完成态彻底失效。

        「点圈 = 完成」这件事不该由 z 序决定。谁画在上面、画成什么形状,都不
        该改变这一下点在哪儿 —— 直接拿几何算,结果永远一样。

        y 要用 canvasy 换算:栏内容滚动过之后,画布坐标和窗口坐标不是一个东西。
        """
        cv = col["canvas"]
        cy = cv.canvasy(y)
        for tid, (x0, y0, x1, y1) in self.card_check_geo.items():
            if self.card_canvas.get(tid) is not cv:
                continue
            if x0 <= x <= x1 and y0 <= cy <= y1:
                return ("check", tid)
        for tid, (x0, y0, x1, y1) in self.card_del_geo.items():
            if self.card_canvas.get(tid) is not cv:
                continue
            if x0 <= x <= x1 and y0 <= cy <= y1:
                return ("del", tid)
        for tid, (y0, h) in self.card_geo.items():
            if self.card_canvas.get(tid) is not cv:
                continue
            if y0 <= cy < y0 + h:
                return ("card", tid)
        box = self.add_geo.get(col["id"])
        if box and box[0] <= cy < box[1]:
            return ("add", col["id"])
        return (None, None)

    def _col_item_press(self, e, col):
        """点在某个 item 上。"""
        self._press_serial = getattr(e, "serial", None)
        self._claim_press(e, col)
        return "break"

    def _col_press(self, e, col):
        """画布自己的 Button-1。

        一次按下会被派发两遍:先按 item 的标签走一遍,再走画布自身这一遍。
        两遍都办事的话,点任务会同时"打开编辑页"和"拖窗口"。

        挡第二遍不用 break —— 是 break 让位的时候不巧:勾选那条路会整栏重画
        (delete("all")),item 在半路就没了,break 跟着丢,第二遍照样跑(实测)。
        改用事件序号认:同一个事件序号只办一次。序号是 Tk 自己发的,每次按下
        都不一样,两条路看到的又是同一个序号,拿它去重是精确的。
        """
        if getattr(e, "serial", None) == self._press_serial:
            return
        self._win_press(e, col)      # 空白处 = 拖这一扇窗

    def _claim_press(self, e, col):
        kind, val = self._hit_test(col, e.x, e.y)
        if kind == "add":
            self.add_task(val)
            return
        if kind == "check":
            self.toggle_done(val)
            return
        if kind == "del":
            self.delete_task(val, undoable=True)
            return
        if kind == "card":
            self._pending = {"tid": val, "col": col,
                             "x": e.x_root, "y": e.y_root, "active": False}
            return
        # 落在某个 item 上,但不在任何一行里(比如空态那句提示)
        self._win_press(e, col)

    def _col_motion(self, e, col):
        if self._win_drag:
            self._win_move(e)
            return
        p = self._pending
        if not p:
            return
        if not p["active"]:
            if abs(e.x_root - p["x"]) + abs(e.y_root - p["y"]) < px(6):
                return
            p["active"] = True
            r = self.card_rect.get(p["tid"])
            if r:
                p["col"]["canvas"].itemconfig(r, fill=C_CARD_DRAG)
            try:
                col["win"].configure(cursor="fleur")
            except tk.TclError:
                pass
        self._update_indicator(e)

    def _col_release(self, e, col):
        if self._win_drag:
            self._save_geom()
            return
        p = self._pending
        self._pending = None
        try:
            col["win"].configure(cursor="")
        except tk.TclError:
            pass
        if not p:
            return
        if p.get("active"):
            tgt = self._compute_drop(e)
            self._clear_indicator()
            if tgt:
                cid, idx = tgt
                self.move_task(p["tid"], cid, idx)
            self.render_all()
        else:
            self.edit_task(p["tid"])

    def _col_dblclick(self, e, col):
        kind, _ = self._hit_test(col, e.x, e.y)
        if kind in ("card", "check", "add"):
            # 双击任务行 = 编辑,双击勾选圈 = 打勾,双击添加行 = 添加 ——
            # 都由单击那条路处理,这里不要再叠一次
            return
        self.add_task(col["id"])

    def _compute_drop(self, e):
        """返回 (目标分栏 id, 插入位置) 或 None。

        先按"指针落在哪扇窗里"认栏 —— 拆成独立窗口之后横坐标不再连续,只靠
        x 判断会在两栏之间的空隙里猜错。外面的判定放宽 px(20),贴着边缘丢
        也算数,不用精确投进框里。
        """
        pad = px(20)
        for col in self.columns:
            win = col["win"]
            try:
                x0, y0 = win.winfo_rootx(), win.winfo_rooty()
                ww, wh = win.winfo_width(), win.winfo_height()
            except tk.TclError:
                continue
            if not (x0 - pad <= e.x_root <= x0 + ww + pad
                    and y0 - pad <= e.y_root <= y0 + wh + pad):
                continue
            cv = col["canvas"]
            cy = e.y_root - cv.winfo_rooty() + cv.canvasy(0)
            idx = 0
            for i, t in enumerate(self.visible_tasks(col["id"])):
                geo = self.card_geo.get(t["id"])
                if not geo:
                    continue
                top, hgt = geo
                if cy > top + hgt / 2:
                    idx = i + 1
            return col["id"], idx
        return None

    def _update_indicator(self, e):
        tgt = self._compute_drop(e)
        self._clear_indicator()
        if not tgt:
            return
        cid, idx = tgt
        col = next((c for c in self.columns if c["id"] == cid), None)
        if not col:
            return
        cv = col["canvas"]
        w = px(self._col_w_logical(col["data"]))
        vis = self.visible_tasks(cid)
        if idx < len(vis):
            y = self.card_geo[vis[idx]["id"]][0] - px(CARD_GAP // 2)
        elif vis:
            top, hgt = self.card_geo[vis[-1]["id"]]
            y = top + hgt + px(CARD_GAP // 2)
        else:
            y = px(6)
        self._indicator = (cv, cv.create_line(px(6), y, w - px(6), y,
                                              fill=C_ACCENT, width=px(2),
                                              capstyle="round"))

    def _clear_indicator(self):
        if self._indicator:
            cv, iid = self._indicator
            try:
                cv.delete(iid)
            except Exception:
                pass
            self._indicator = None

    # -- 滚轮 ------------------------------------------------------------
    def _on_wheel(self, e):
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return
        cvs = [c["canvas"] for c in self.columns]
        cv = None
        while w is not None:
            if isinstance(w, tk.Canvas) and w in cvs:
                cv = w
                break
            w = getattr(w, "master", None)
        if cv is None:
            return
        cv.yview_scroll(int(-e.delta / 120), "units")
        return "break"

    # -- 撤回 / 重做 -----------------------------------------------------
    def apply_history(self):
        """撤回/重做之后把界面拉回和数据一致。

        分栏是**窗**,不是画布上的方块:栏的数量或 id 一变,窗就得重建。
        只是任务变了的话重画就够 —— 重建会让所有栏闪一下,能不重建就不重建。
        """
        want = [c["id"] for c in self.store.columns]
        have = [c["id"] for c in self.columns]
        if want != have:
            self._build_columns()
        else:
            for c in self.columns:
                # 还原换掉的是整个 dict,窗上挂着的那份引用得重新指过去
                c["data"] = self.store.column(c["id"])
                self._layout_column(c)
        self.render_all()

    def undo(self):
        if not self.hist.can_undo():
            self._toast("没有可以撤回的操作了")
            return
        label = self.hist.undo()
        self._toast("已撤回:%s" % label, action="重做", on_action=self.redo)

    def redo(self):
        if not self.hist.can_redo():
            self._toast("没有可以重做的操作了")
            return
        label = self.hist.redo()
        self._toast("已重做:%s" % label, action="撤回", on_action=self.undo)

    def _col_name(self, cid):
        c = self.store.column(cid)
        return c["name"] if c else "?"

    # -- 任务操作 --------------------------------------------------------
    def add_task(self, cid):
        # 先拍底再动手:这条任务在弹编辑窗**之前**就已经进了数据(不然编辑窗
        # 取消时还得记得删回去),可那会儿标题还是空的 —— 等标题填完再回头看
        # 就晚了,快照拍到的是改完的样子。所以在这里先拍一张,交给编辑窗去记账。
        self._new_snap = self.hist.snapshot()
        t = {"id": new_id(), "column_id": cid, "title": "", "content": "",
             "due": None, "remind_minutes": self.store.settings["default_remind_minutes"],
             "done": False, "created": datetime.now().isoformat(timespec="minutes"),
             "notified": False, "snooze_until": None}
        self.store.tasks.append(t)
        self.store.save()
        self.render_all()
        self.edit_task(t["id"], is_new=True)

    def duplicate_task(self, tid):
        src = self.store.task(tid)
        if not src:
            return
        cp = dict(src)
        cp["id"] = new_id()
        cp["done"] = False
        cp["notified"] = False
        cp["title"] = src["title"] + " (副本)"
        idx = self.store.tasks.index(src)
        self.hist.note("复制任务「%s」→ %s"
                       % (src.get("title") or "未命名", self._col_name(cp["column_id"])))
        self.store.tasks.insert(idx + 1, cp)
        self.store.save()
        self.render_all()

    def edit_task(self, tid, is_new=False):
        t = self.store.task(tid)
        if not t:
            return
        dlg = TaskEditor(self, t, is_new=is_new)
        self.root.wait_window(dlg.win)
        self.render_all()

    def toggle_done(self, tid):
        t = self.store.task(tid)
        if not t:
            return
        title = t.get("title") or "未命名"
        t["done"] = not t["done"]
        if not t["done"]:
            t["notified"] = False
            t["snooze_until"] = None
        # 打勾是最常做的动作,日志里攒得最多 —— 也正因为多,回头翻的时候
        # 能看出"哪天在集中清哪一摊事"。
        self.hist.note(("完成" if t["done"] else "取消完成") + "「%s」" % title)
        self.store.save()
        self.render_all()

    def delete_task(self, tid, undoable=False):
        t = self.store.task(tid)
        if not t:
            return
        title = t.get("title") or "未命名"
        self.hist.note("删除任务「%s」→ %s" % (title, self._col_name(t["column_id"])))
        self.store.tasks.remove(t)
        self.store.save()
        self.render_all()
        if undoable:
            # 直接删,不弹确认 —— 后悔药就是这条提示上的「撤销」
            self._sync_anchor()
            self._toast("已删除「%s」" % title, action="撤销", on_action=self.undo)

    def move_task(self, tid, target_cid, index):
        t = self.store.task(tid)
        if not t:
            return
        # 拍照要在挪之前 —— 拍在挪之后的话,记下的是"挪完之后"的样子,
        # 撤回回去就成了原地不动
        self.hist.note("移动任务「%s」→ %s"
                       % (t.get("title") or "未命名", self._col_name(target_cid)))
        others = [x for x in self.store.tasks if x["id"] != tid]
        vis = [x for x in others
               if x["column_id"] == target_cid
               and not (self.hide_done and x["done"])]

        # index 是拖拽时按「含被拖卡片」的目标栏可见顺序算出来的
        # (见 _compute_drop)。卡片原本就在目标栏里的话,把它摘掉后
        # 后面的元素整体前移一位,index 要相应补偿,否则会多落一格。
        old_pos = None
        if t["column_id"] == target_cid:
            for i, x in enumerate(self.visible_tasks(target_cid)):
                if x["id"] == tid:
                    old_pos = i
                    break

        t["column_id"] = target_cid
        full_len = len(vis) + (1 if old_pos is not None else 0)
        if index >= full_len:
            same = [x for x in others if x["column_id"] == target_cid]
            pos = others.index(same[-1]) + 1 if same else len(others)
        else:
            if old_pos is not None and index > old_pos:
                index -= 1
            pos = others.index(vis[index])
        others.insert(pos, t)
        self.store.tasks[:] = others
        self.store.save()

    # -- 分栏操作 --------------------------------------------------------
    def add_column(self):
        name = SimplerPrompt(self.dlg_parent, "新建分栏", "分栏名称：", "新分栏").result
        if not name:
            return
        used = {c.get("color") for c in self.store.columns}
        color = next((c for c in PALETTE if c not in used),
                     PALETTE[len(self.store.columns) % len(PALETTE)])
        new = {"id": new_id(), "name": name, "color": color,
               "w": self.col_w, "h": self.store.settings.get("height", 560)}
        # 新栏就摆在最后一栏右边,摆不下就换到下一行 —— 建出来必须看得见,
        # 这是上次"新建分栏点了没反应"的教训(栏建好了,却在屏幕外面)
        if self.columns:
            last = self.columns[-1]["win"]
            nx = last.winfo_x() + last.winfo_width() + px(COL_GAP_OUT)
            ny = last.winfo_y()
            if nx + px(self.col_w) > self.root.winfo_screenwidth():
                nx = self.columns[0]["win"].winfo_x()
                ny = last.winfo_y() + last.winfo_height() + px(COL_GAP_OUT)
            new["x"], new["y"] = max(0, nx), max(0, ny)
        self.hist.note("新建分栏「%s」" % name)
        self.store.columns.append(new)
        self.store.save()
        self._build_columns()
        self.render_all()

    def rename_column(self, col):
        cur = self.store.column(col["id"])
        name = SimplerPrompt(self.dlg_parent, "重命名分栏", "新名称：", cur["name"]).result
        if not name:
            return
        self.hist.note("分栏「%s」改名为「%s」" % (cur["name"], name))
        cur["name"] = name
        self.store.save()
        self.render_all()

    def recolor_column(self, col):
        cur = self.store.column(col["id"])
        old = cur["color"]
        picker = ColorPicker(self.dlg_parent, cur["color"])
        self.root.wait_window(picker.win)
        if picker.result and picker.result != old:
            self.hist.note("分栏「%s」换了颜色" % cur["name"])
            cur["color"] = picker.result
            self.store.save()
            self.render_all()

    def delete_column(self, col):
        if len(self.store.columns) <= 1:
            self._toast("至少要保留一个分栏")
            return
        cur = self.store.column(col["id"])
        n = len([t for t in self.store.tasks if t["column_id"] == col["id"]])
        if not Confirm(self.dlg_parent, "删除分栏",
                       f"确定删除「{cur['name']}」吗？" +
                       (f"\n\n里面的 {n} 条任务会移到第一个分栏。" if n else "")).result:
            return
        fallback = next(c["id"] for c in self.store.columns if c["id"] != col["id"])
        self.hist.note("删除分栏「%s」%s"
                       % (cur["name"], "(内含 %d 条,已并入「%s」)"
                          % (n, self._col_name(fallback)) if n else ""))
        for t in self.store.tasks:
            if t["column_id"] == col["id"]:
                t["column_id"] = fallback
        self.store.columns[:] = [c for c in self.store.columns if c["id"] != col["id"]]
        self.store.save()
        self._build_columns()
        self.render_all()

    def clear_column(self, col):
        """把这一栏里的任务一次删光,栏本身留着。

        确认框问得比删分栏还慎重,因为删分栏是"任务挪走",这个是真删。虽然
        提示条上还挂着「撤销」,但撤回栈只在内存里 —— 关掉程序就没了,所以
        这一下还是要拦一道。
        """
        cur = self.store.column(col["id"])
        n = len([t for t in self.store.tasks if t["column_id"] == col["id"]])
        if not n:
            self._toast("「%s」已经是空的" % cur["name"])
            return
        if not Confirm(self.dlg_parent, "清空分栏",
                       "确定删掉「%s」里的全部 %d 条任务吗?\n\n"
                       "分栏本身留着;删掉的任务可以点提示条上的「撤销」找回。"
                       % (cur["name"], n)).result:
            return
        self.hist.note("清空分栏「%s」(%d 条)" % (cur["name"], n))
        self.store.tasks[:] = [t for t in self.store.tasks
                               if t["column_id"] != col["id"]]
        self.store.save()
        self.render_all()
        self._sync_anchor()
        self._toast("已清空「%s」· %d 条" % (cur["name"], n),
                    action="撤销", on_action=self.undo)

    def relayout(self):
        """把所有分栏重新排成一排,贴回屏幕右上角。

        拆成独立窗口之后这一项比以前重要多了:几栏拖散之后想一键归位。
        """
        sw = self.root.winfo_screenwidth()
        gap = px(COL_GAP_OUT)
        x = sw - px(24)
        for e in reversed(self.columns):
            try:
                w = e["win"].winfo_width()
                x -= w
                e["win"].geometry(f"+{int(max(0, x))}+0")
                x -= gap
            except tk.TclError:
                continue
        self._save_geom()
        self.render_all()

    # -- 顶部按钮 --------------------------------------------------------
    def toggle_top(self):
        self.topmost = not self.topmost
        for w in self._app_windows():
            try:
                w.attributes("-topmost", self.topmost)
                if self.topmost:
                    w.lift()
            except tk.TclError:
                pass
        self.store.settings["topmost"] = self.topmost
        self.store.save()
        self._render_tool()

    def _apply_collapse(self):
        for e in self.columns:
            try:
                if self._collapsed:
                    self._expanded[e["id"]] = e["win"].winfo_height()
                    self._layout_column(e, h=px(COL_HDR_H) + px(EDGE_W))
                else:
                    h = self._expanded.get(
                        e["id"], px(self._col_h_logical(e["data"])))
                    self._layout_column(e, h=h)
            except tk.TclError:
                pass
        self._render_tool()

    def toggle_collapse(self):
        """收起 = 每栏只留自己那条标题。独立窗口之后这一项正好用来"暂时
        让开屏幕":几栏同时压成一条条,不占地方,又都还在原位找得回来。"""
        self._collapsed = not self._collapsed
        self._apply_collapse()

    def open_settings(self):
        dlg = SettingsDialog(self)
        self.root.wait_window(dlg.win)
        self.render_all()

    def _toast(self, msg, action=None, on_action=None):
        # 同一时刻只留一条:连点撤回时不会在屏幕上摞成一堆
        old = getattr(self, "_cur_toast", None)
        if old is not None:
            old.close()
        self._cur_toast = Toast(self.root, msg, self.win_x, self.win_y, self.win_w,
                                action=action, on_action=on_action)

    # -- 提醒 ------------------------------------------------------------
    def _check_reminders(self):
        now = datetime.now()
        changed = False
        for t in self.store.tasks:
            if t["done"] or not t.get("due"):
                continue
            try:
                due = datetime.fromisoformat(t["due"])
            except ValueError:
                continue
            if t.get("notified"):
                continue
            su = t.get("snooze_until")
            if su:
                try:
                    if now < datetime.fromisoformat(su):
                        continue
                except ValueError:
                    pass
            rm = t.get("remind_minutes")
            if rm is None:
                rm = self.store.settings["default_remind_minutes"]
            if rm is None or rm < 0:
                continue
            if now >= due - timedelta(minutes=rm):
                t["notified"] = True
                changed = True
                self._show_reminder(t)
        if changed:
            self.store.save()
            self.render_all()
        # 环要跟着时间长大。跟提醒共用这半分钟一次的节拍 —— 再开一个定时器
        # 只是让这面板在你看不见的地方多转一圈,没这个必要。
        self._refresh_rings()
        self.root.after(30000, self._check_reminders)

    def _show_reminder(self, t):
        if self.store.settings.get("sound"):
            try:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass
        self._reminders = [r for r in self._reminders if r.alive]
        pop = ReminderPopup(self, t, index=len(self._reminders))
        self._reminders.append(pop)
        self.root.after(0, pop.fade_in)

    def schedule_position(self):
        for i, r in enumerate(self._reminders):
            if r.alive:
                r.place(i)

    def fade_in(self, step=0):
        """启动淡入。

        挂了毛玻璃就必须整段跳过:给窗口设分层 alpha,Windows 会**顺手把 DWM
        的模糊关掉**(模糊和"整窗半透明"在合成器里是互斥的两条路)。这个坑很
        隐蔽 —— 毛玻璃明明挂上了,却被一句看似无害的淡入覆盖掉,窗口看起来还是
        老的半透明。有毛玻璃就直接出现。
        """
        if self.acrylic:
            return
        a = min(self.opacity, 0.15 + step * 0.12)
        for w in self._app_windows():
            try:
                w.attributes("-alpha", a)
            except tk.TclError:
                continue
        if a < self.opacity:
            self.root.after(16, lambda: self.fade_in(step + 1))

    # -- 托盘那边递过来的活儿 ---------------------------------------------
    def _tray_pump(self):
        """替托盘的窗口过程收网,见 TrayIcon.pump。"""
        if self.tray is not None:
            self.tray.pump()
        self.root.after(TRAY_POLL_MS, self._tray_pump)

    # -- 从屏幕上整个收起来 ----------------------------------------------
    def _set_visible(self, show):
        """把所有的栏(和工具条)一起藏起来,或者一起放回来。

        用 withdraw 而不是 iconify:这些窗全是 overrideredirect 的,iconify
        之后任务栏上不会长出一个按钮来点,真就找不回来了。withdraw 只是不
        显示 —— 位置、尺寸、滚到哪儿,什么都没动,deiconify 回来还是原样。
        """
        self._hidden = not show
        for w in self._app_windows():
            try:
                w.deiconify() if show else w.withdraw()
            except tk.TclError:
                pass

    def toggle_visible(self):
        self._set_visible(self._hidden)

    # -- 退出 ------------------------------------------------------------
    def quit_app(self):
        self._save_geom()
        self._save_tool_geom()
        self.store.save()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()


# --------------------------------------------------------------------------
# 任务栏托盘图标
# --------------------------------------------------------------------------

WM_TRAY = 0x8000 + 1            # 图标回调消息,自己定的(WM_APP + 1)
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x10, 0x40
IDI_APPLICATION = 32512
HWND_MESSAGE = -3               # "只收消息、不显示"的父窗口
TRAY_ID = 1
TRAY_POLL_MS = 150              # 主线程多久去收一次网(见 TrayIcon.pump)


class _NOTIFYICONDATAW(ctypes.Structure):
    """Shell_NotifyIconW 要的那个结构。

    字段一个都不能少:cbSize 填的是整个结构的大小,系统拿它判断"你用的是
    哪一版",少填一截就会被当成老版本,图标静悄悄加不上,还不报错。
    """
    _fields_ = [("cbSize", ctypes.c_uint32),
                ("hWnd", ctypes.c_void_p),
                ("uID", ctypes.c_uint32),
                ("uFlags", ctypes.c_uint32),
                ("uCallbackMessage", ctypes.c_uint32),
                ("hIcon", ctypes.c_void_p),
                ("szTip", ctypes.c_wchar * 128),
                ("dwState", ctypes.c_uint32),
                ("dwStateMask", ctypes.c_uint32),
                ("szInfo", ctypes.c_wchar * 256),
                ("uVersion", ctypes.c_uint32),
                ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", ctypes.c_uint32),
                ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", ctypes.c_void_p)]


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint32),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# 窗口过程:LRESULT WINAPI WndProc(HWND, UINT, WPARAM, LPARAM)
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint32,
                              ctypes.c_size_t, ctypes.c_ssize_t)

_tray_seq = 0


def _tray_class_name():
    """窗口类名要唯一 —— 同一个进程里开第二个实例(测试就这么干)、或者程序
    重载一次,同名类会让 RegisterClassW 直接失败,图标就没了。"""
    global _tray_seq
    _tray_seq += 1
    return f"TodoBoardTray_{os.getpid()}_{_tray_seq}"


class TrayIcon:
    """任务栏右下角折叠区里那个小图标。

    走的是 Windows 的老路子:自建一扇"只收消息、永远不显示"的窗,把它的
    句柄交给 Shell_NotifyIcon,鼠标事件就从那扇窗的窗口过程进来。

    为什么不用 pywin32 之类的库?这程序的态度是"双击就能跑、不装任何东西",
    为了一个图标去拉一个第三方包不值。

    为什么不用线程去泵消息?也没必要 —— 这扇窗和 Tk 开在同一个线程里,而
    Tk 的 mainloop 本来就在泵这个线程的消息队列,消息自然会送到这儿来,
    连 after 轮询都不用加。
    """

    def __init__(self, app):
        self.app = app
        self.hwnd = None
        self._proc = None       # 必须留着引用,被 GC 掉 Windows 就会调到野指针上
        self._cls = None
        self._hicon = None
        self._added = False
        self._taskbar_created = 0
        # 窗口过程只管往这儿丢记号,主线程来取 —— 为什么必须这么绕,见
        # _on_message 的说明
        self._events = collections.deque()

    # -- 起 / 停 ----------------------------------------------------------
    def start(self):
        """把图标挂上去。成不成都不抛异常 —— 见 stop() 的说明。"""
        # 进程以任何方式结束都要摘掉图标 —— 托盘图标是 explorer 替我们拿着
        # 的,进程一死它不会自己消失,会变成任务栏上一个点不动的死图标,直
        # 到鼠标从上面扫过去。正常退出走 quit_app,这条是兜底。
        atexit.register(self.stop)
        try:
            self._create_window()
            self._load_icon()
            self._add()
            return True
        except Exception:
            self.stop()
            return False

    def stop(self):
        """把图标摘掉、把消息窗拆了。

        全程吞异常:它只是个入口,挂不上或者摘不掉都不该影响面板本身。
        退出前一定要摘 —— 留着的话任务栏上会挂一个点不动的死图标,直到
        鼠标从它上面划过,那很吓人。
        """
        if self._added:
            try:
                nid = _NOTIFYICONDATAW()
                nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
                nid.hWnd = self.hwnd
                nid.uID = TRAY_ID
                ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE,
                                                        ctypes.byref(nid))
            except Exception:
                pass
        self._added = False
        if self.hwnd:
            try:
                ctypes.windll.user32.DestroyWindow(ctypes.c_void_p(self.hwnd))
            except Exception:
                pass
        self.hwnd = None

    # -- 内部 -------------------------------------------------------------
    def _create_window(self):
        user32 = ctypes.windll.user32
        kern32 = ctypes.windll.kernel32
        kern32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]   # 它在 kernel32,不在 user32
        kern32.GetModuleHandleW.restype = ctypes.c_void_p
        user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        user32.RegisterClassW.restype = ctypes.c_uint16
        user32.CreateWindowExW.argtypes = [
            ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                          ctypes.c_size_t, ctypes.c_ssize_t]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        user32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
        user32.RegisterWindowMessageW.restype = ctypes.c_uint32

        hinst = kern32.GetModuleHandleW(None)
        self._proc = _WNDPROC(self._on_message)
        self._cls = _WNDCLASSW()
        self._cls.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        self._cls.hInstance = hinst
        self._cls.lpszClassName = _tray_class_name()
        if not user32.RegisterClassW(ctypes.byref(self._cls)):
            raise OSError("RegisterClassW 失败")
        # 父窗口给 HWND_MESSAGE:这扇窗不进桌面、不进任务栏、Alt+Tab 里也
        # 没有它,除了收消息什么都不干
        self.hwnd = user32.CreateWindowExW(
            0, self._cls.lpszClassName, "TodoBoard", 0, 0, 0, 0, 0,
            ctypes.c_void_p(HWND_MESSAGE), None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW 失败")
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

    def _load_icon(self):
        """优先用同目录的 icon.ico,找不到就借系统的默认图标。

        兜底不是多余的:用户完全可能把 .pyw 单独拷到别的目录去用。图标丑
        一点,也好过托盘上一块空白。
        """
        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                      ctypes.c_uint32, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_uint32]
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadIconW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.LoadIconW.restype = ctypes.c_void_p
        ico = os.path.join(APP_DIR, "icon.ico")
        if os.path.exists(ico):
            self._hicon = user32.LoadImageW(None, ico, IMAGE_ICON, 0, 0,
                                            LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not self._hicon:
            self._hicon = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

    def _nid(self):
        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = TRAY_ID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._hicon
        nid.szTip = "待办面板"
        return nid

    def _add(self):
        fn = ctypes.windll.shell32.Shell_NotifyIconW
        fn.argtypes = [ctypes.c_uint32, ctypes.POINTER(_NOTIFYICONDATAW)]
        fn.restype = ctypes.c_int
        nid = self._nid()
        if not fn(NIM_ADD, ctypes.byref(nid)):
            raise OSError("Shell_NotifyIcon 失败")
        self._added = True

    def _on_message(self, hwnd, msg, wp, lp):
        """托盘的事件从这儿进来 —— Windows 直接调,而且是在 Tk 正忙着的时候
        (它这会儿已经放开 GIL、蹲在消息循环里等消息)。

        所以这里**一个 Tk 调用都不能有**,连 root.after 都不行:那等于在 Tcl
        自己执行到一半的时候再挤进去调 Tcl,线程状态会被搅坏(实测:轻则
        卡住,重则 interpreter 直接崩在 update 里)。这里只往队列里丢一个
        记号就返回,活儿全交给主线程那边轮询的 pump() 去办。
        """
        try:
            if msg == WM_TRAY:
                if lp in (WM_LBUTTONUP, WM_RBUTTONUP):
                    self._events.append("menu")
            elif self._taskbar_created and msg == self._taskbar_created:
                # explorer 重启过,托盘被清空了。不重加的话图标从此消失,
                # 用户只能去任务管理器结束进程 —— 每个托盘程序都要挨这一下。
                self._events.append("readd")
        except Exception:
            pass
        return ctypes.windll.user32.DefWindowProcW(
            ctypes.c_void_p(hwnd), msg, wp, lp)

    def pump(self):
        """主线程这边收网:把窗口过程记下的记号一件件办掉。

        这不是"图省事的轮询" —— 消息只能从窗口过程进来,而那儿不能碰 Tk,
        两边中间总得有一处搭这座桥。150ms 一次,队列空的时候就是一次判空
        加一个 after,约等于不花钱。
        """
        while self._events:
            what = self._events.popleft()
            if what == "menu":
                self._popup()
            elif what == "readd":
                self._added = False
                try:
                    self._add()
                except Exception:
                    pass

    def _popup(self):
        """在光标那儿弹出托盘菜单。

        菜单挂 dlg_parent(那根能看见的工具条),不是挂 root:root 是
        withdraw 的,拿一个不可见窗口当菜单的主人,弹出来的东西收不到点击。
        Windows 上还有一个前台锁 —— 点托盘图标那一下,前台是任务栏(explorer
        的),我们只是个蹲在后面的进程,"啪"地弹菜单会被拦下来,表现就是点了
        没反应。先把自己的窗口抬成前台,菜单才有资格抢到鼠标。

        tk_popup 会一直转到菜单被选掉或者点没了才返回。
        """
        m = tk.Menu(self.app.dlg_parent, tearoff=0, bg="#24243A", fg=C_TEXT,
                    activebackground=C_ACCENT, activeforeground="#FFFFFF",
                    bd=0, relief="flat")
        m.add_command(label="显示待办面板" if self.app._hidden else "隐藏待办面板",
                      command=self.app.toggle_visible)
        m.add_command(label="展开全部栏" if self.app._collapsed else "收起全部栏",
                      command=self.app.toggle_collapse)
        m.add_separator()
        m.add_command(label="设置…", command=self.app.open_settings)
        m.add_command(label="退出", command=self.app.quit_app)
        x, y = self._cursor_pos()
        try:
            user32 = ctypes.windll.user32
            user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            user32.SetForegroundWindow(
                ctypes.c_void_p(self.app._win_hwnd(self.app.dlg_parent)))
        except Exception:
            pass
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    @staticmethod
    def _cursor_pos():
        """菜单弹在鼠标那儿 —— 图标在屏幕角落,菜单自然要贴着它,而不是贴着
        面板(面板早被拖到别处去了)。

        GetCursorPos 给的是物理像素,这程序启动时已经声明了 DPI 感知,和 Tk
        的 x_root 是同一套坐标,可以直接用。
        """
        p = _POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y


# --------------------------------------------------------------------------
# 小对话框
# --------------------------------------------------------------------------

def dark_titlebar(win):
    """把系统标题栏也刷成黑的。

    深色程序配一条白标题栏是最扎眼的"没做完"信号之一 —— 这个洞是
    Windows 自己在画的,只能用 DWM 属性告诉它。

    必须在窗口"映射之后"设。映射前设,DWM 调用照样返回 S_OK,但系统创建
    窗口框架时会把它覆盖掉,现象就是"返回值 0、标题栏还是白的"。这个坑
    排查了很久,别再改回映射前调用。
    """
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        if not hwnd:
            return
        val = ctypes.c_int(1)
        for attr in (20, 19):        # 20 = 新版, 19 = 21H1 之前
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


def _dark_later(win):
    """映射后补两拍。第一拍是主力,第二拍兜底 —— 有的机型要等窗口框架
    完全建好才认这个属性。窗口销毁后 after 会被 tk 自动取消,不用手动清。"""
    for ms in (0, 120):
        win.after(ms, lambda w=win: dark_titlebar(w))


def prepare_dialog(win):
    win.configure(bg=C_PANEL)
    win.attributes("-topmost", True)
    win.resizable(False, False)
    try:
        win.iconbitmap(default="")
    except Exception:
        pass
    # 收起再展开不会重新 Map,所以 <Map> 之外的定时兜底是必要的
    win.bind("<Map>", lambda e: dark_titlebar(win), add="+")
    _dark_later(win)


class SimplerPrompt:
    """单行输入弹窗"""
    def __init__(self, parent, title, label, initial=""):
        self.result = None
        win = tk.Toplevel(parent)
        self.win = win
        prepare_dialog(win)
        win.title(title)
        win.transient(parent)
        win.attributes("-alpha", 1.0)

        body = tk.Frame(win, bg=C_PANEL, padx=px(18), pady=px(16))
        body.pack(fill="both", expand=True)
        tk.Label(body, text=label, font=F.ui, fg=C_TEXT_DIM, bg=C_PANEL).pack(anchor="w")
        var = tk.StringVar(value=initial)
        ent = tk.Entry(body, textvariable=var, font=F.ui, bg=C_CARD, fg=C_TEXT,
                       insertbackground=C_TEXT, relief="flat", width=26,
                       highlightthickness=1, highlightbackground=C_LINE,
                       highlightcolor=C_ACCENT)
        ent.pack(fill="x", pady=(px(8), px(14)), ipady=px(5))
        ent.focus_set()
        ent.select_range(0, "end")

        row = tk.Frame(body, bg=C_PANEL)
        row.pack(fill="x")

        def ok(_=None):
            v = var.get().strip()
            self.result = v or None
            win.destroy()

        def cancel(_=None):
            self.result = None
            win.destroy()

        _dlg_btn(row, "取消", cancel).pack(side="right", padx=(px(8), 0))
        _dlg_btn(row, "确定", ok, primary=True).pack(side="right")
        ent.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        _center(win, parent)
        win.grab_set()


class Confirm:
    def __init__(self, parent, title, message):
        self.result = False
        win = tk.Toplevel(parent)
        self.win = win
        prepare_dialog(win)
        win.title(title)
        win.transient(parent)
        win.attributes("-alpha", 1.0)

        body = tk.Frame(win, bg=C_PANEL, padx=px(20), pady=px(18))
        body.pack(fill="both", expand=True)
        tk.Label(body, text=message, font=F.ui, fg=C_TEXT, bg=C_PANEL,
                 justify="left").pack(anchor="w")
        row = tk.Frame(body, bg=C_PANEL)
        row.pack(fill="x", pady=(px(16), 0))

        def yes():
            self.result = True
            win.destroy()

        def no():
            self.result = False
            win.destroy()

        _dlg_btn(row, "取消", no).pack(side="right", padx=(px(8), 0))
        _dlg_btn(row, "确定", yes, primary=True).pack(side="right")
        win.bind("<Escape>", lambda e: no())
        _center(win, parent)
        win.grab_set()


class ColorPicker:
    """从调色板里选一个颜色"""
    def __init__(self, parent, current):
        self.result = None
        win = tk.Toplevel(parent)
        self.win = win
        prepare_dialog(win)
        win.title("选择颜色")
        win.transient(parent)
        win.attributes("-alpha", 1.0)

        body = tk.Frame(win, bg=C_PANEL, padx=px(18), pady=px(16))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="挑一个分栏颜色", font=F.ui, fg=C_TEXT_DIM,
                 bg=C_PANEL).pack(anchor="w", pady=(0, px(10)))
        grid = tk.Frame(body, bg=C_PANEL)
        grid.pack()

        def choose(c):
            self.result = c
            win.destroy()

        for i, c in enumerate(PALETTE):
            sw = tk.Frame(grid, bg=c, width=px(36), height=px(30),
                          cursor="hand2",
                          highlightthickness=px(2) if c == current else 0,
                          highlightbackground=C_TEXT)
            sw.grid(row=i // 4, column=i % 4, padx=px(5), pady=px(5))
            sw.pack_propagate(False)
            sw.bind("<Button-1>", lambda e, c=c: choose(c))

        _dlg_btn(body, "取消", lambda: win.destroy()).pack(pady=(px(12), 0))
        win.bind("<Escape>", lambda e: win.destroy())
        _center(win, parent)
        win.grab_set()


def _dlg_btn(parent, text, cmd, primary=False):
    # 主按钮用琥珀底 + 近黑的字。白字压在琥珀上对比度只有 2:1,是"廉价感"
    # 最典型的来源之一。
    bg = C_ACCENT if primary else C_CARD
    fg = "#1A1206" if primary else C_TEXT
    hov = "#F2B455" if primary else C_CARD_HOV
    lbl = tk.Label(parent, text=text, font=F.ui, fg=fg, bg=bg,
                   padx=px(16), pady=px(6), cursor="hand2")
    lbl.bind("<Enter>", lambda e: lbl.configure(bg=hov))
    lbl.bind("<Leave>", lambda e: lbl.configure(bg=bg))
    lbl.bind("<Button-1>", lambda e: cmd())
    return lbl


class CanvasSlider(tk.Canvas):
    """自绘滑杆。

    tk 自带的 Scale 在深色底上**滑块是隐形的** —— 只看得见一条槽,该往哪儿
    拖全靠猜;它那套浅色像素画法也和这块面板格格不入。所以干脆自己画:一条
    细槽、一段琥珀色的已选量、一个亮圆点。和面板里其他东西同一套语言。
    """

    H = 22          # 逻辑高度

    def __init__(self, parent, lo, hi, value, step, command, width=230):
        # 属性名带下划线前缀:Tk 的 Misc 是个很宽的基类,别去赌某个短名没被占用
        self._cw = px(width)
        self._ch = px(self.H)
        super().__init__(parent, width=self._cw, height=self._ch, bg=C_PANEL,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.lo, self.hi, self.step = lo, hi, step
        self.command = command
        self.value = value
        self.pad = px(9)
        self.bind("<Button-1>", self._on_drag)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<Enter>", lambda e: self._draw(hot=True))
        self.bind("<Leave>", lambda e: self._draw(hot=False))
        self._draw()

    # -- 坐标换算 --------------------------------------------------------
    def _x_of(self, v):
        k = (v - self.lo) / float(self.hi - self.lo)
        return self.pad + k * (self._cw - 2 * self.pad)

    def _val_of(self, x):
        span = max(1.0, self._cw - 2 * self.pad)
        k = min(1.0, max(0.0, (x - self.pad) / span))
        v = self.lo + k * (self.hi - self.lo)
        return round(v / self.step) * self.step

    def set(self, v):
        self.value = v
        self._draw()

    def _on_drag(self, e):
        v = self._val_of(e.x)
        if abs(v - self.value) < 1e-9:
            return
        self.value = v
        self._draw(hot=True)
        self.command(v)

    def _draw(self, hot=False):
        self.delete("all")
        ty = self._ch / 2.0
        th = px(4)
        r = th / 2.0
        round_rect(self, self.pad, ty - r, self._cw - self.pad, ty + r, r,
                   fill=C_CARD, outline="")
        x = self._x_of(self.value)
        if x > self.pad + r:
            round_rect(self, self.pad, ty - r, x, ty + r, r,
                       fill=C_ACCENT, outline="")
        # 滑块:平时是亮灰,悬停/拖动时变大一圈并亮成纯白 —— 一眼看得出"能拖"
        kr = px(7.5) if hot else px(6.5)
        self.create_oval(x - kr, ty - kr, x + kr, ty + kr,
                         fill="#FFFFFF" if hot else C_TEXT, outline="")


def _center(win, parent):
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    px_, py_ = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    x = px_ + max(0, (pw - w) // 2)
    y = py_ + max(0, (ph - h) // 3)
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    x = max(0, min(x, sw - w))
    y = max(0, min(y, sh - h))
    win.geometry(f"+{x}+{y}")


# --------------------------------------------------------------------------
# 任务编辑弹窗
# --------------------------------------------------------------------------

REMIND_CHOICES = [
    ("不提醒", -1), ("到点提醒", 0), ("提前 10 分钟", 10),
    ("提前 30 分钟", 30), ("提前 1 小时", 60), ("提前 2 小时", 120),
    ("提前 1 天", 1440), ("自定义…", None),
]


class TaskEditor:
    def __init__(self, app, task, is_new=False):
        self.app = app
        self.store = app.store
        self.task = task
        self.is_new = is_new

        win = tk.Toplevel(app.dlg_parent)
        self.win = win
        prepare_dialog(win)
        win.title("新建任务" if is_new else "编辑任务")
        win.transient(app.dlg_parent)
        win.attributes("-alpha", 1.0)

        body = tk.Frame(win, bg=C_PANEL, padx=px(20), pady=px(18))
        body.pack(fill="both", expand=True)

        # 标题
        self.title_var = tk.StringVar(value=task.get("title") or "")
        self._label(body, "标题")
        ent = self._entry(body, self.title_var)
        ent.pack(fill="x", pady=(px(4), px(12)), ipady=px(5))
        ent.focus_set()

        # 内容
        self._label(body, "内容(可选)")
        # width=1 让 Text 只作最小请求宽度,实际宽度交给 pack(fill="x") 撑开,
        # 否则 tk.Text 默认请求 80 字符宽,会把整个弹窗顶得很宽
        txt = tk.Text(body, height=4, width=1, font=F.ui, bg=C_CARD, fg=C_TEXT,
                      insertbackground=C_TEXT, relief="flat", wrap="word",
                      highlightthickness=1, highlightbackground=C_LINE,
                      highlightcolor=C_ACCENT, padx=px(8), pady=px(6))
        txt.pack(fill="x", pady=(px(4), px(12)))
        txt.insert("1.0", task.get("content") or "")
        self.txt = txt

        # 分栏 + 提醒
        row = tk.Frame(body, bg=C_PANEL)
        row.pack(fill="x", pady=(0, px(12)))

        left = tk.Frame(row, bg=C_PANEL)
        left.pack(side="left", fill="x", expand=True, padx=(0, px(8)))
        self._label(left, "所属分栏")
        names = [c["name"] for c in self.store.columns]
        cur = self.store.column(task["column_id"])
        self.col_var = tk.StringVar(value=cur["name"] if cur else names[0])
        self._optionmenu(left, self.col_var, names).pack(fill="x", pady=(px(4), 0))

        right = tk.Frame(row, bg=C_PANEL)
        right.pack(side="left", fill="x", expand=True)
        self._label(right, "提醒")
        cur_rm = task.get("remind_minutes")
        if cur_rm is None:
            cur_rm = self.store.settings["default_remind_minutes"]
        label = next((n for n, v in REMIND_CHOICES if v == cur_rm), "自定义…")
        self.remind_var = tk.StringVar(value=label)
        self._optionmenu(right, self.remind_var,
                         [n for n, _ in REMIND_CHOICES]).pack(fill="x", pady=(px(4), 0))

        # 截止时间
        self._label(body, "截止时间")
        due_row = tk.Frame(body, bg=C_PANEL)
        due_row.pack(fill="x", pady=(px(4), px(6)))
        self.due_var = tk.StringVar()
        if task.get("due"):
            try:
                self.due_var.set(fmt_due(datetime.fromisoformat(task["due"])))
            except ValueError:
                pass
        due_ent = self._entry(due_row, self.due_var)
        due_ent.pack(fill="x", side="left", expand=True, ipady=px(5))
        _dlg_btn(due_row, "清除", lambda: self.due_var.set("")).pack(side="left", padx=(px(6), 0))

        quick = tk.Frame(body, bg=C_PANEL)
        quick.pack(fill="x", pady=(0, px(4)))
        for txt_, val in (("今天", "today"), ("明天", "tomorrow"),
                          ("本周日", "sunday"), ("下周", "nextweek")):
            _small_btn(quick, txt_, lambda v=val: self._quick_date(v)).pack(side="left", padx=(0, px(5)))
        tk.Label(quick, text="│", fg=C_LINE, bg=C_PANEL).pack(side="left", padx=px(3))
        for hh in ("09:00", "12:00", "18:00", "21:00"):
            _small_btn(quick, hh, lambda v=hh: self._quick_time(v)).pack(side="left", padx=(0, px(5)))

        tk.Label(body, text="可写:2026-10-09 18:00 / 10-09 / 明天 9:30 / 周日 / 18:00",
                 font=F.tiny, fg=C_TEXT_MUTE, bg=C_PANEL).pack(anchor="w", pady=(px(4), px(14)))

        # 完成
        self.done_var = tk.BooleanVar(value=bool(task.get("done")))
        chk = tk.Checkbutton(body, text="已完成", variable=self.done_var,
                             font=F.ui, fg=C_TEXT_DIM, bg=C_PANEL,
                             activebackground=C_PANEL, activeforeground=C_TEXT,
                             selectcolor=C_CARD, bd=0, highlightthickness=0)
        chk.pack(anchor="w", pady=(0, px(14)))

        # 底部按钮
        btns = tk.Frame(body, bg=C_PANEL)
        btns.pack(fill="x")
        _dlg_btn(btns, "保存", self.save, primary=True).pack(side="right")
        _dlg_btn(btns, "取消", self.cancel).pack(side="right", padx=(0, px(8)))
        if not is_new:
            del_btn = _dlg_btn(btns, "删除", self.delete)
            del_btn.configure(fg="#FF9C9C", bg=C_PANEL)
            del_btn.pack(side="left")

        win.bind("<Escape>", lambda e: self.cancel())
        win.bind("<Control-Return>", lambda e: self.save())
        # 右上角那个 ✕ 也要走 cancel()。不接这个协议的话,✕ 是 Tk 默认的
        # "直接销毁窗口",cancel() 压根不执行 —— 而新建任务时,任务在弹这个
        # 窗**之前**就已经进数据了(见 add_task),于是双击空白又随手关掉,
        # 就白留下一条「未命名」。现在 ✕ / Esc / 「取消」三条路走到同一个地方。
        win.protocol("WM_DELETE_WINDOW", self.cancel)
        _center(win, app.dlg_parent)
        win.grab_set()

    # -- 控件助手 --------------------------------------------------------
    def _label(self, parent, text):
        tk.Label(parent, text=text, font=F.small, fg=C_TEXT_DIM,
                 bg=C_PANEL).pack(anchor="w")

    def _entry(self, parent, var):
        return tk.Entry(parent, textvariable=var, font=F.ui, bg=C_CARD,
                        fg=C_TEXT, insertbackground=C_TEXT, relief="flat",
                        highlightthickness=1, highlightbackground=C_LINE,
                        highlightcolor=C_ACCENT)

    def _optionmenu(self, parent, var, values):
        om = tk.OptionMenu(parent, var, *values)
        om.configure(bg=C_CARD, fg=C_TEXT, font=F.ui, relief="flat",
                     bd=0, highlightthickness=0, activebackground=C_CARD_HOV,
                     activeforeground=C_TEXT, anchor="w", padx=px(8), pady=px(3))
        om["menu"].configure(bg="#24243A", fg=C_TEXT, font=F.ui, bd=0,
                             activebackground=C_ACCENT, activeforeground="#FFFFFF")
        return om

    def _quick_date(self, kind):
        now = datetime.now()
        if kind == "today":
            d = now.date()
        elif kind == "tomorrow":
            d = now.date() + timedelta(days=1)
        elif kind == "sunday":
            delta = (6 - now.weekday()) % 7 or 7
            d = now.date() + timedelta(days=delta)
        else:
            d = now.date() + timedelta(days=7)
        old = self.due_var.get()
        m = re.search(r"(\d{1,2}):(\d{2})", old)
        hm = m.group(0) if m else "23:59"
        self.due_var.set(f"{d.strftime('%Y-%m-%d')} {hm}")

    def _quick_time(self, hhmm):
        cur = self.due_var.get().strip()
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", cur)
        d = m.group(1) if m else datetime.now().strftime("%Y-%m-%d")
        self.due_var.set(f"{d} {hhmm}")

    # -- 动作 ------------------------------------------------------------
    def save(self):
        title = self.title_var.get().strip()
        if not title:
            title = "未命名"
        due_txt = self.due_var.get().strip()
        try:
            due = parse_due_str(due_txt)
        except ValueError as ex:
            Confirm(self.win, "时间格式不对",
                    f"没看懂「{due_txt}」。\n\n{ex}\n\n"
                    "可以写成:\n2026-10-09 18:00\n10-09\n明天 9:30\n周日\n18:00")
            return

        t = self.task
        name = self.col_var.get()
        col = next((c for c in self.store.columns if c["name"] == name), None)

        # 记账必须在写数据**之前** —— 快照拍的得是改之前的样子。
        # 新建这条用的是进 add_task 时先拍好的那张底:任务那会儿就在数据里了,
        # 只是标题还空着,当场再拍就拍成了"新建之后"。
        old_title = t.get("title") or "未命名"
        if self.is_new:
            self.app.hist.commit(
                "新建任务「%s」→ %s"
                % (title, self.app._col_name(col["id"] if col else t["column_id"])),
                self.app._new_snap)
        elif title != old_title:
            self.app.hist.note("编辑任务「%s」→「%s」" % (old_title, title))
        else:
            self.app.hist.note("编辑任务「%s」" % old_title)
        self.app._new_snap = None

        t["title"] = title
        t["content"] = self.txt.get("1.0", "end").strip()
        t["due"] = fmt_due(due) if due else None
        t["done"] = bool(self.done_var.get())
        if col:
            t["column_id"] = col["id"]

        rlabel = self.remind_var.get()
        if rlabel == "自定义…":
            v = SimplerPrompt(self.win, "自定义提醒",
                              "提前多少分钟提醒?(0 = 到点)", "30").result
            try:
                t["remind_minutes"] = max(0, int(v)) if v else 0
            except (TypeError, ValueError):
                t["remind_minutes"] = -1     # 输入没法解析就当没设提醒
        else:
            t["remind_minutes"] = next(v for n, v in REMIND_CHOICES if n == rlabel)

        t["notified"] = False
        t["snooze_until"] = None
        self.store.save()
        self.win.destroy()

    def cancel(self):
        if self.is_new:
            lst = self.store.tasks
            if self.task in lst:
                lst.remove(self.task)
                self.store.save()
            # 新建没成,那张底就没用了 —— 留着的话,下一次"编辑任务"会拿着
            # 一份老早以前的快照去记账,撤回回去就删错东西
            self.app._new_snap = None
        self.win.destroy()

    def delete(self):
        title = self.task.get("title") or "未命名"
        if not Confirm(self.win, "删除任务", f"确定删除「{title}」吗?").result:
            return
        if self.is_new:
            # 还没记账过(标题都还没填),直接扔掉,别往撤回栈里塞一条空的
            self.app._new_snap = None
        else:
            self.app.hist.note("删除任务「%s」→ %s"
                               % (title, self.app._col_name(self.task["column_id"])))
        lst = self.store.tasks
        if self.task in lst:
            lst.remove(self.task)
            self.store.save()
        self.win.destroy()


def _small_btn(parent, text, cmd):
    lbl = tk.Label(parent, text=text, font=F.tiny, fg=C_TEXT_DIM, bg=C_CARD,
                   padx=px(9), pady=px(4), cursor="hand2")
    lbl.bind("<Enter>", lambda e: lbl.configure(bg=C_CARD_HOV, fg=C_TEXT))
    lbl.bind("<Leave>", lambda e: lbl.configure(bg=C_CARD, fg=C_TEXT_DIM))
    lbl.bind("<Button-1>", lambda e: cmd())
    return lbl


# --------------------------------------------------------------------------
# 设置面板
# --------------------------------------------------------------------------

class SettingsDialog:
    def __init__(self, app):
        self.app = app
        st = app.store.settings
        win = tk.Toplevel(app.dlg_parent)
        self.win = win
        prepare_dialog(win)
        win.title("设置")
        win.transient(app.dlg_parent)
        win.attributes("-alpha", 1.0)

        body = tk.Frame(win, bg=C_PANEL, padx=px(20), pady=px(18))
        body.pack(fill="both", expand=True)

        tk.Label(body, text="设置", font=F.title, fg=C_TEXT, bg=C_PANEL).pack(anchor="w", pady=(0, px(14)))

        # 透明度。数值摆在标题行右端,而不是像 tk 的 Scale 那样浮在槽上方
        self.op_lab = self._row(body, "面板不透明度",
                                "%.2f" % app.opacity)
        CanvasSlider(body, 0.40, 1.00, app.opacity, 0.01,
                     self._on_opacity).pack(fill="x", pady=(0, px(12)))

        # 新分栏的默认宽度。已经在的栏想改宽度,直接拖它自己的左右边缘 ——
        # 这里只是"以后再建一栏"时用哪个宽度。
        self.cw_lab = self._row(body, "新分栏的默认宽度", str(app.col_w))
        CanvasSlider(body, 200, 560, app.col_w, 4,
                     self._on_colw).pack(fill="x", pady=(0, px(12)))

        # 默认提醒
        self._row(body, "新任务默认提前多久提醒")
        rlabel = next((n for n, v in REMIND_CHOICES
                       if v == st.get("default_remind_minutes")), "不提醒")
        self.rm_var = tk.StringVar(value=rlabel)
        om = tk.OptionMenu(body, self.rm_var, *[n for n, _ in REMIND_CHOICES if n != "自定义…"])
        # indicatoron=False:关掉那个默认下拉箭头 —— 它在深色底上是一枚缺字符
        # 的方框,比没有箭头难看得多。整个控件就当成一个"当前值"的按钮。
        om.configure(bg=C_CARD, fg=C_TEXT, font=F.ui, relief="flat", bd=0,
                     indicatoron=False, highlightthickness=0,
                     activebackground=C_CARD_HOV,
                     activeforeground=C_TEXT, anchor="w", padx=px(10), pady=px(6))
        om["menu"].configure(bg=C_CARD_HOV, fg=C_TEXT, font=F.ui, bd=0,
                             activebackground=C_ACCENT, activeforeground="#1A1206")
        om.pack(fill="x", pady=(0, px(10)))
        self.rm_var.trace_add("write", lambda *a: self._on_remind())

        # 开关
        self.sound_var = tk.BooleanVar(value=bool(st.get("sound")))
        self.hide_var = tk.BooleanVar(value=bool(app.hide_done))
        self.auto_var = tk.BooleanVar(value=autostart_enabled())
        self.acr_var = tk.BooleanVar(value=bool(app.acrylic))
        for var, text, cmd in (
            (self.acr_var, "毛玻璃背景（把背后的窗口模糊掉）", self._on_acrylic),
            (self.sound_var, "提醒时播放提示音", self._on_sound),
            (self.hide_var, "隐藏已完成的任务", self._on_hide),
            (self.auto_var, "开机自动启动", self._on_auto),
        ):
            chk = tk.Checkbutton(body, text=text, variable=var, command=cmd,
                                 font=F.ui, fg=C_TEXT_DIM, bg=C_PANEL,
                                 activebackground=C_PANEL, activeforeground=C_TEXT,
                                 selectcolor=C_CARD, bd=0, highlightthickness=0)
            chk.pack(anchor="w", pady=px(2))

        tk.Frame(body, bg=C_LINE, height=1).pack(fill="x", pady=px(14))

        row = tk.Frame(body, bg=C_PANEL)
        row.pack(fill="x")
        _dlg_btn(row, "重新排列分栏", self._reset_pos).pack(side="left")
        _dlg_btn(row, "完成", win.destroy, primary=True).pack(side="right")

        tk.Label(body, text=f"数据文件:{DATA_FILE}", font=F.tiny,
                 fg=C_TEXT_MUTE, bg=C_PANEL, wraplength=px(240),
                 justify="left").pack(anchor="w", pady=(px(12), 0))

        win.bind("<Escape>", lambda e: win.destroy())
        _center(win, app.dlg_parent)
        win.grab_set()

    def _row(self, parent, text, value=None):
        """一行标题。给了 value 就在右端放一个可更新的数值 —— 滑杆的当前值
        摆在这里,比 tk 默认那种浮在槽上方的数字干净,也不会顶开布局。"""
        row = tk.Frame(parent, bg=C_PANEL)
        row.pack(fill="x")
        tk.Label(row, text=text, font=F.small, fg=C_TEXT_DIM,
                 bg=C_PANEL).pack(side="left")
        if value is None:
            return row
        lab = tk.Label(row, text=value, font=F.small, fg=C_TEXT, bg=C_PANEL)
        lab.pack(side="right")
        return lab

    def _on_opacity(self, v):
        val = round(float(v), 2)
        self.app.opacity = val
        self.op_lab.configure(text="%.2f" % val)
        self.app.store.settings["opacity"] = val
        self.app.store.save()
        self.app.apply_opacity()

    def _on_acrylic(self):
        on = bool(self.acr_var.get())
        self.app.store.settings["acrylic"] = on
        self.app.store.save()
        if not self.app._apply_backdrop() and on:
            # 这台机器上挂不上,别让勾留在"已开启"骗人
            self.acr_var.set(False)
            self.app.store.settings["acrylic"] = False
            self.app.store.save()

    def _on_colw(self, v):
        """只改"以后新建分栏用多宽"。已经在的那些栏各有各的宽度,不该被这条
        滑杆一起改掉 —— 那也是它们能各自拖边缘的意义所在。"""
        val = int(float(v))
        if val == self.app.col_w:
            return
        self.app.col_w = val
        self.cw_lab.configure(text=str(val))
        self.app.store.settings["column_width"] = val
        self.app.store.save()

    def _on_remind(self):
        label = self.rm_var.get()
        v = next((v for n, v in REMIND_CHOICES if n == label), -1)
        self.app.store.settings["default_remind_minutes"] = v
        self.app.store.save()

    def _on_sound(self):
        self.app.store.settings["sound"] = bool(self.sound_var.get())
        self.app.store.save()

    def _on_hide(self):
        self.app.hide_done = bool(self.hide_var.get())
        self.app.store.settings["hide_done"] = self.app.hide_done
        self.app.store.save()
        self.app.render_all()

    def _on_auto(self):
        set_autostart(bool(self.auto_var.get()))

    def _reset_pos(self):
        """把拖散的分栏全部收回屏幕右上角、排成一排。

        旧版叫「重置窗口位置」,救的是"整块面板被拖出了屏幕"。拆成独立窗口
        之后每栏都可能各自跑丢,这个按钮正好升级成"一键归位"。
        """
        self.app.relayout()


# --------------------------------------------------------------------------
# 提醒弹窗
# --------------------------------------------------------------------------

class ReminderPopup:
    def __init__(self, app, task, index=0):
        self.app = app
        self.task = task
        self.alive = True
        self.index = index

        win = tk.Toplevel(app.root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        win.configure(bg=C_ACCENT)

        outer = tk.Frame(win, bg=C_ACCENT)
        outer.pack(fill="both", expand=True)
        # 1px 琥珀描边 = "这是通知"的信号,里面是一层比面板略亮的表面
        body = tk.Frame(outer, bg=C_CARD)
        body.pack(fill="both", expand=True, padx=px(1), pady=px(1))

        inner = tk.Frame(body, bg=C_CARD, padx=px(16), pady=px(14))
        inner.pack(fill="both", expand=True)

        head = tk.Frame(inner, bg=C_CARD)
        head.pack(fill="x")
        tk.Label(head, text="待办提醒", font=F.small, fg=C_ACCENT,
                 bg=C_CARD).pack(side="left")
        tk.Label(head, text="✕", font=F.ui, fg=C_TEXT_MUTE, bg=C_CARD,
                 cursor="hand2").pack(side="right")

        tk.Label(inner, text=task.get("title") or "未命名", font=F.task,
                 fg=C_TEXT, bg=C_CARD, wraplength=px(280),
                 justify="left", anchor="w").pack(fill="x", pady=(px(8), 0))

        content = (task.get("content") or "").strip()
        if content:
            tk.Label(inner, text=content, font=F.meta, fg=C_TEXT_DIM,
                     bg=C_CARD, wraplength=px(280), justify="left",
                     anchor="w").pack(fill="x", pady=(px(4), 0))

        due = task.get("due")
        txt, col = due_label(due, False)
        if txt:
            tk.Label(inner, text=txt, font=F.small, fg=col, bg=C_CARD,
                     anchor="w").pack(fill="x", pady=(px(8), 0))

        row = tk.Frame(inner, bg=C_CARD)
        row.pack(fill="x", pady=(px(12), 0))
        _dlg_btn(row, "打开任务", self.open_task, primary=True).pack(side="right")
        _dlg_btn(row, "稍后 10 分钟", self.snooze).pack(side="right", padx=(0, px(6)))
        _dlg_btn(row, "知道了", self.close).pack(side="right", padx=(0, px(6)))

        win.update_idletasks()
        self.place(index)
        app.root.after(0, app.schedule_position)

    def place(self, index):
        try:
            self.win.update_idletasks()
            w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            x = sw - w - px(24)
            y = sh - px(90) - (index + 1) * (h + px(12))
            self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
            self.index = index
        except Exception:
            pass

    def fade_in(self, step=0):
        if not self.alive:
            return
        a = min(0.96, 0.08 + step * 0.08)
        try:
            self.win.attributes("-alpha", a)
        except Exception:
            return
        if a < 0.96:
            self.win.after(18, lambda: self.fade_in(step + 1))

    def open_task(self):
        tid = self.task["id"]
        self.close()
        self.app.root.after(50, lambda: self.app.edit_task(tid))

    def snooze(self):
        self.task["snooze_until"] = (datetime.now() + timedelta(minutes=10)).isoformat(timespec="minutes")
        self.task["notified"] = False
        self.app.store.save()
        self.close()
        Toast(self.app.root, "已推迟 10 分钟提醒", self.app.win_x,
              self.app.win_y, self.app.win_w)

    def close(self):
        self.alive = False
        try:
            self.win.destroy()
        except Exception:
            pass
        self.app.root.after(60, self.app.schedule_position)


class Toast:
    """在栏顶上浮一下的小提示条。

    action / on_action 是给「已删除 · 撤销」这类用的:右边挂一个能点的动作。
    带动作的时候多停一会儿(5 秒),够人反应过来要点;不带动作的 1.6 秒就淡走,
    免得挡着看东西。

    「撤销」这件事本来可以做成编辑框里的确认,但确认框会打断手头的动作 ——
    删一条任务要多按一次回车,删十条就是十次。这里反过来:先删掉(反正有
    撤回),给你一次后悔的机会。手快的人根本不会看到这条提示。
    """

    def __init__(self, parent, msg, x, y, w, action=None, on_action=None):
        win = tk.Toplevel(parent)
        self.win = win
        self.on_action = on_action
        self.done = False
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        box = tk.Frame(win, bg="#22222A", padx=px(18), pady=px(11))
        box.pack()
        tk.Label(box, text=msg, font=F.ui, fg=C_TEXT,
                 bg="#22222A").pack(side="left")
        if action:
            self.act = tk.Label(box, text=action, font=F.ui, fg=C_ACCENT,
                                bg="#22222A", padx=px(6), cursor="hand2")
            self.act.pack(side="left", padx=(px(14), 0))
            self.act.bind("<Enter>", lambda e: self.act.configure(fg="#FFFFFF"))
            self.act.bind("<Leave>", lambda e: self.act.configure(fg=C_ACCENT))
            self.act.bind("<Button-1>", lambda e: self._fire())
        win.update_idletasks()
        tw, th = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry(f"+{x + max(0, (w - tw) // 2)}+{y + px(14)}")
        self.fade(0, True, err=False)

    def _fire(self):
        """点了动作按钮。

        先把自己收掉再办事 —— 回调(比如 app.undo)自己会再弹一条新提示,
        留着这条旧的在上面会叠成一摞。
        """
        if self.done:
            return
        cb = self.on_action
        self.close()
        if cb:
            cb()

    def close(self):
        self.done = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def fade(self, step, up, err=False):
        if self.done:
            return
        goals = 8
        hold = 5000 if self.on_action else 1600
        try:
            if up and step <= goals:
                self.win.attributes("-alpha",
                                    min(0.95, step / goals * 0.95 + 0.05))
                self.win.after(16, lambda: self.fade(step + 1, True, err))
            elif up:
                self.win.after(hold, lambda: self.fade(goals, False, err))
            else:
                a = max(0.0, step / goals * 0.95)
                self.win.attributes("-alpha", a)
                if step <= 0:
                    self.close()
                else:
                    self.win.after(16, lambda: self.fade(step - 1, False, err))
        except tk.TclError:
            self.done = True


# --------------------------------------------------------------------------
# 开机自启(快捷方式)
# --------------------------------------------------------------------------

def _startup_dir():
    return os.path.join(os.environ.get("APPDATA", ""),
                        r"Microsoft\Windows\Start Menu\Programs\Startup")


def _lnk_path():
    return os.path.join(_startup_dir(), SHORTCUT_NAME + ".lnk")


def autostart_enabled():
    return os.path.exists(_lnk_path())


def make_shortcut(lnk, pythonw, script, appdir, desc):
    ps = (
        "$W = New-Object -ComObject WScript.Shell; "
        f"$S = $W.CreateShortcut('{lnk}'); "
        f"$S.TargetPath = '{pythonw}'; "
        f"$S.Arguments = '\"{script}\"'; "
        f"$S.WorkingDirectory = '{appdir}'; "
        f"$S.Description = '{desc}'; "
        "$S.WindowStyle = 7; "
        "$S.Save()"
    )
    flags = 0x08000000  # CREATE_NO_WINDOW
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-Command", ps], creationflags=flags, timeout=30)


def pythonw_path():
    p = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(p):
        return p
    return sys.executable


def set_autostart(enable):
    lnk = _lnk_path()
    try:
        if enable:
            os.makedirs(_startup_dir(), exist_ok=True)
            make_shortcut(lnk, pythonw_path(), os.path.abspath(__file__),
                          APP_DIR, "桌面待办面板")
        else:
            if os.path.exists(lnk):
                os.remove(lnk)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# 单实例锁
# --------------------------------------------------------------------------

def single_instance():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 50937))
        s.listen(1)
        return s
    except OSError:
        return None


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    lock = None if DEV else single_instance()
    if lock is None and not DEV:
        # 已经有一个在跑了,静默退出(不弹框,免得重复双击时被打扰)
        return
    if DEV:
        os.makedirs(DEV, exist_ok=True)

    root = tk.Tk()
    root.title("待办面板")
    build_fonts()

    store = Store(DATA_FILE)
    store.load()
    if store.fresh:
        store.save()   # 首次运行把示例数据落盘,免得每次启动都重新生成

    app = TodoBoard(root, store)

    # 淡入(挂了毛玻璃会自动跳过,原因见 TodoBoard.fade_in)
    app.fade_in()

    # 启动补挂:窗口建好的那一刻 DWM 偶尔还没准备好,毛玻璃会静默失败。
    # 稳定之后再挂一次,失败也不影响运行(退化成普通的半透明)。
    root.after(400, app.reapply_backdrop)

    root.mainloop()


if __name__ == "__main__":
    main()
