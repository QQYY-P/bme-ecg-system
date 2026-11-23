"""Confirm record ownership before acquisition, without network I/O on Tk."""
import tkinter as tk
from tkinter import ttk


def choose_owner(view, confirmed=None):
    existing = getattr(view, '_owner_dialog', None)
    if existing is not None and existing.winfo_exists(): existing.lift(); return
    dialog = tk.Toplevel(view.root); view._owner_dialog = dialog
    dialog.title('本次采集归属'); dialog.geometry('470x260'); dialog.transient(view.root)
    frame = ttk.Frame(dialog, padding=20); frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='这次为谁采集？', font=(view.family, 16, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='归属随记录保存，后续补传不会更换账号。', wraplength=420).pack(anchor='w', pady=(8,16))
    state = view.cloud.snapshot()
    choices = {'暂不分配（上传后由管理员分配）':None}
    choices.update({u['username']:u['id'] for u in state.get('recipients',[])})
    selected = tk.StringVar(value=next((name for name,uid in choices.items() if uid==view.cloud_owner_id),next(iter(choices))))
    ttk.Combobox(frame,textvariable=selected,values=list(choices),state='readonly').pack(fill='x')
    ttk.Label(frame,text='若离线，使用上次同步的账号列表。未分配记录不向普通账号展示。',wraplength=420,style='Muted.TLabel').pack(anchor='w',pady=12)
    actions=ttk.Frame(frame);actions.pack(side='bottom',fill='x')
    def accept():
        view.cloud_owner_id=choices[selected.get()]
        view.cloud_owner_name=selected.get()
        dialog.destroy()
        if confirmed:confirmed()
    ttk.Button(actions,text='确认并开始' if confirmed else '确认',command=accept,style='Start.TButton').pack(side='right')
    ttk.Button(actions,text='取消',command=dialog.destroy,style='Quiet.TButton').pack(side='right',padx=8)
    dialog.grab_set()
