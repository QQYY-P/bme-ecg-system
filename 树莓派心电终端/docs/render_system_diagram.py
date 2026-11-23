"""Render the current, model-free ECG architecture as a report-ready figure."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, PathPatch
from matplotlib.path import Path as MPath
from matplotlib.font_manager import FontProperties

ROOT = Path(__file__).resolve().parent
FONT = FontProperties(fname=r"C:\Windows\Fonts\msyh.ttc")
BOLD = FontProperties(fname=r"C:\Windows\Fonts\msyhbd.ttc")
plt.rcParams.update({"svg.fonttype": "path", "axes.unicode_minus": False})
fig, ax = plt.subplots(figsize=(18, 12.5), dpi=160)
fig.patch.set_facecolor("white")
ax.set(xlim=(0,18), ylim=(0,12.5))
ax.axis("off")
fig.subplots_adjust(0,0,1,1)

ink="#19324a"; muted="#526579"; edge="#6f879c"
colors={"hw":("#edf5ff","#4282bf"), "sw":("#effaf5","#29957d"),
        "store":("#fff6e9","#c89238"), "aux":("#f4f6f9","#8292a3")}

def txt(x,y,s,size=13,color=ink,bold=False,ha="center",va="center",**kw):
    return ax.text(x,y,s,fontproperties=BOLD if bold else FONT,fontsize=size,
                   color=color,ha=ha,va=va,linespacing=1.55,**kw)

def box(x,y,w,h,title,body="",kind="sw",ts=15,bs=12):
    fill,stroke=colors[kind]
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle="round,pad=0.02,rounding_size=0.12",
                              facecolor=fill,edgecolor=stroke,linewidth=1.5,zorder=3))
    if body:
        txt(x+w/2,y+h-.3,title,ts,bold=True,zorder=4)
        txt(x+w/2,y+(h-.48)/2,body,bs,zorder=4)
    else: txt(x+w/2,y+h/2,title,ts,bold=True,zorder=4)

def arrow(points,dashed=False,color=edge,label=None,label_pos=None):
    path=MPath(points,[MPath.MOVETO]+[MPath.LINETO]*(len(points)-1))
    ax.add_patch(FancyArrowPatch(path=path,arrowstyle="-|>",mutation_scale=14,
                                lw=1.6,color=color,linestyle=(0,(5,4)) if dashed else "-",zorder=2))
    if label: txt(*label_pos,label,11,color=muted,bbox=dict(facecolor="white",edgecolor="none",pad=2),zorder=5)

txt(.55,12.07,"心电采集与树莓派波形显示系统",25,bold=True,ha="left")
txt(17.45,12.07,"当前无模型版  ·  2026-10-27",12,color=muted,ha="right")
txt(.55,11.58,"硬件采集 → 串口传输 → 树莓派处理、记录与本地显示",13,color=muted,ha="left")

box(.55,10.05,2.2,1.15,"心电电极","心电信号输入",kind="hw")
box(3.4,10.05,3.05,1.15,"AD8232 心电模块","模拟信号调理 · 导联检测",kind="hw")
box(7.2,10.05,4.15,1.15,"STM32F103","TIM3 → ADC1 / DMA\n360 Hz · 12 位 ADC",kind="hw",bs=11.5)
box(13.1,10.05,3.0,1.15,"USB-TTL 转换器","UART / USB 串口",kind="hw")
arrow([(2.79,10.63),(3.36,10.63)])
arrow([(6.49,10.63),(7.16,10.63)])
arrow([(11.39,10.63),(13.06,10.63)],label="115200 / 8N1",label_pos=(12.24,11.05))
txt(12.24,10.25,"每点 3 字节",10.5,color=muted)

ax.add_patch(FancyBboxPatch((.55,2.8),14.7,6.55,boxstyle="round,pad=0.025,rounding_size=.16",
                           facecolor="#fbfdfd",edgecolor="#8eb9af",linewidth=1.8,zorder=0))
txt(5.0,8.96,"树莓派 2 Model B  ·  Python 波形终端",16,bold=True,ha="left")
arrow([(14.6,10.01),(14.6,9.65),(2.6,9.65),(2.6,8.29)],label="USB 串口输入",label_pos=(8.6,9.65))

box(1.0,6.75,3.2,1.5,"串口接收与协议解析","三字节帧解码\n序号检查 · 无效点标记")
box(5.0,6.75,3.15,1.5,"采集与会话管理","数据批次 · 连续段\n连接 / 采集 / 停止状态")
arrow([(4.24,7.5),(4.96,7.5)])

box(8.95,7.35,3.05,1.05,"显示支路","50 Hz 陷波 · 保留原始波形",ts=14,bs=10.8)
box(8.95,5.8,3.05,1.05,"分析支路","R 峰检测 → 心率 / RR",ts=14,bs=11.2)
arrow([(8.19,7.85),(8.91,7.85)])
arrow([(8.19,7.12),(8.55,7.12),(8.55,6.33),(8.91,6.33)])

box(12.65,5.85,2.15,2.55,"波形界面","Tkinter\n原始 / 处理后波形\n心率 / RR / 状态\n开始 · 停止 · 暂停",ts=14,bs=10.8)
arrow([(12.04,7.85),(12.61,7.85)])
arrow([(12.04,6.33),(12.61,6.33)])
box(16.0,6.35,1.5,1.8,"外接显示器","本地显示\n心电波形",kind="hw",ts=13,bs=11)
arrow([(14.84,7.25),(15.96,7.25)],label="HDMI",label_pos=(15.4,7.65))
txt(15.4,6.88,"X11 输出",10,color=muted)

box(1.0,3.45,3.2,1.45,"模拟 / 回放数据源","合成模拟波形\n完整 CSV 文件回放",kind="aux",ts=14)
arrow([(4.24,4.18),(4.6,4.18),(4.6,7.0),(4.96,7.0)],dashed=True)
txt(2.6,5.35,"软件测试输入",11,color=muted)

box(5.0,3.45,3.4,1.45,"原始记录与本地存储","数据块 · 原始串口字节\nSQLite 索引 · 事件 / 质量",kind="store",ts=14,bs=11.5)
arrow([(6.57,6.71),(6.57,4.94)],label="独立保存支路",label_pos=(6.57,5.67))
box(9.15,3.45,5.65,1.45,"历史记录与完整 CSV 导出","按会话查询 · 停止后导出全部已保存数据",kind="store",ts=14,bs=11.5)
arrow([(8.44,4.18),(9.11,4.18)])
arrow([(13.7,4.94),(13.7,5.81)])
txt(8.0,3.03,"暂停显示仅冻结画面；原始采集与记录继续运行。",11.5,color=muted)

box(.55,.94,4.55,1.1,"笔记本 · VS Code","代码编辑 / 文件传输 / 日志检查",kind="aux",ts=14,bs=11.5)
arrow([(5.14,1.49),(7.35,1.49),(7.35,2.75)],dashed=True,label="以太网 · SSH",label_pos=(6.65,1.49))
txt(9.05,1.65,"代码已具备采集、显示、分析和记录功能。",12,color=ink,ha="left")
txt(9.05,1.15,"真实串口与外接显示效果仍待现场验证。",12,color="#9b6626",ha="left")
txt(.55,.36,"图示范围：当前软件与硬件链路；不含分类模型、云端或移动 App。实线表示数据流，虚线表示测试输入或开发管理。",11,color=muted,ha="left")

for extension in ("svg","png"):
    output=ROOT / ("系统框图_当前无模型版."+extension)
    fig.savefig(output,dpi=180,facecolor="white")
    print(output)
plt.close(fig)
