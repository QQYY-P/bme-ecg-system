"""Show the public phone entry point without sharing device credentials."""
import os
import tkinter as tk
from tkinter import ttk
from urllib.parse import urlparse


def show_mobile(view, sid, owner_name):
    url = os.getenv('ECG_MOBILE_URL', 'https://62.234.150.104/').strip()
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        url = ''
    dialog = tk.Toplevel(view.root)
    dialog.title('手机查看'); view._place_dialog(dialog, 640, 350)
    frame = ttk.Frame(dialog, padding=18); frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='在手机查看本次记录', font=(view.family,16,'bold')).pack(anchor='w')
    ttk.Label(frame, text='1. 在测量总结中点击“上传并分析”，等待上传确认。\n'
        '2. 手机浏览器打开下方网址，用本次记录所属账号登录。\n'
        '3. 点击“刷新记录”，按下方记录编号选择本次测量。',
        wraplength=590, justify='left').pack(anchor='w',pady=14)
    address=tk.StringVar(value=url or '手机网址未正确配置，请联系项目管理员')
    ttk.Entry(frame,textvariable=address,state='readonly').pack(fill='x')
    ttk.Label(frame,text='记录归属：'+owner_name+'\n记录编号：'+sid,
        wraplength=590,justify='left').pack(anchor='w',pady=12)
    ttk.Label(frame,text='未分配的记录须先由管理员指定归属。断网时记录保留在终端，可联网后重试。',
        wraplength=590,style='Muted.TLabel').pack(anchor='w')
    ttk.Button(frame,text='关闭',command=dialog.destroy).pack(side='bottom',anchor='e',pady=(8,0))
