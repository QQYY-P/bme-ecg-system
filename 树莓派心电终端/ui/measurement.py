"""Guided start and saved-session summary; Tk never performs disk/network I/O."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from measurement import DURATIONS, preflight_checks, summary_lines, owner_label


def prepare_measurement(view):
    if view._closing or view._action_pending or view._last_snapshot.get('running'): return
    previous=getattr(view,'_measurement_dialog',None)
    if previous is not None and previous.winfo_exists():previous.lift();return
    dialog=tk.Toplevel(view.root);view._measurement_dialog=dialog
    dialog.title('开始前确认');view._place_dialog(dialog,730,420);dialog.transient(view.root)
    frame=ttk.Frame(dialog,padding=16);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='准备本次采集',font=(view.family,17,'bold')).pack(anchor='w')
    cloud=view.cloud.snapshot() if view.cloud is not None else {}
    choices={'未分配（仅管理员可分配）':None}
    choices.update({u['username']:u['id'] for u in cloud.get('recipients',[])})
    owner=tk.StringVar(value=next((name for name,uid in choices.items() if uid==view.cloud_owner_id),next(iter(choices))))
    duration=tk.StringVar(value=getattr(view,'_measurement_duration','手动结束'))
    fields=ttk.Frame(frame);fields.pack(fill='x',pady=(12,8))
    ttk.Label(fields,text='使用者').grid(row=0,column=0,padx=(0,8))
    ttk.Combobox(fields,textvariable=owner,values=list(choices),state='readonly',width=30).grid(row=0,column=1,sticky='ew')
    ttk.Label(fields,text='采集时长').grid(row=0,column=2,padx=(16,8))
    ttk.Combobox(fields,textvariable=duration,values=list(DURATIONS),state='readonly',width=12).grid(row=0,column=3)
    fields.columnconfigure(1,weight=1)
    checks=ttk.Frame(frame);checks.pack(fill='both',expand=True)
    labels=[]
    for i in range(5):
        label=ttk.Label(checks,text='正在检查…',wraplength=670,justify='left')
        label.pack(anchor='w',pady=5);labels.append(label)
    hint=ttk.Label(frame,text='按设备标识接好电极与采集线，保持静止；开始后观察信号提示。离线账号来自上次同步；取消不生成记录。',wraplength=670,style='Muted.TLabel')
    hint.pack(anchor='w',pady=6)
    actions=ttk.Frame(frame);actions.pack(fill='x')
    capacity=[None]
    def render(*_):
        if not dialog.winfo_exists() or capacity[0] is None:return
        from ui.main_window import SOURCES
        rows=preflight_checks(source=SOURCES[view.source.get()],port=view.port.get().strip(),storage=capacity[0],
            cloud=view.cloud.snapshot() if view.cloud else {},owner_name=owner.get())
        for label,row in zip(labels,rows):label.configure(text=row['title']+'：'+row['detail'])
        begin.configure(state='disabled' if any(r['blocking'] for r in rows) else 'normal')
    def confirm():
        if view._closing or view._last_snapshot.get('running'):dialog.destroy();return
        if str(begin['state'])=='disabled':return
        view.cloud_owner_id=choices[owner.get()]
        view.cloud_owner_name=owner.get()
        view._measurement_duration=duration.get()
        view._show_next_summary=True
        dialog.destroy()
        view.start(duration=DURATIONS[duration.get()],_owner_checked=True)
    begin=ttk.Button(actions,text='确认并开始',command=confirm,style='Start.TButton',state='disabled');begin.pack(side='right')
    ttk.Button(actions,text='取消',command=dialog.destroy,style='Quiet.TButton').pack(side='right',padx=8)
    def checked(value):capacity[0]=value;render()
    def failed(error):
        if dialog.winfo_exists():labels[2].configure(text='存储检查失败：'+str(error))
    owner.trace_add('write',render)
    view._task(view.controller.storage_status,checked,failed)
    def refresh_status():
        if dialog.winfo_exists() and not view._closed:
            render();view.root.after(500,refresh_status)
    refresh_status()
    dialog.grab_set()


def show_summary(view,sid):
    if not sid:return
    existing=getattr(view,'_summary_dialog',None)
    if existing is not None and existing.winfo_exists():existing.destroy()
    dialog=tk.Toplevel(view.root);view._summary_dialog=dialog
    dialog.title('本次测量总结');view._place_dialog(dialog,750,430)
    frame=ttk.Frame(dialog,padding=12);frame.pack(fill='both',expand=True)
    frame.columnconfigure(0,weight=1);frame.rowconfigure(1,weight=1)
    ttk.Label(frame,text='测量总结',font=(view.family,17,'bold')).grid(row=0,column=0,sticky='w')
    body=ttk.Frame(frame);body.grid(row=1,column=0,sticky='nsew',pady=8)
    table=ttk.Treeview(body,columns=('item','value'),show='headings',height=9)
    table.heading('item',text='项目');table.heading('value',text='本次已保存结果')
    table.column('item',width=140,stretch=False);table.column('value',width=540)
    table.pack(side='left',fill='both',expand=True)
    scroll=ttk.Scrollbar(body,command=table.yview);scroll.pack(side='right',fill='y');table.configure(yscrollcommand=scroll.set)
    status=tk.StringVar(value='正在读取已保存记录…')
    ttk.Label(frame,textvariable=status,wraplength=710,style='Muted.TLabel').grid(row=2,column=0,sticky='w')
    upload_state=tk.StringVar()
    ttk.Label(frame,textvariable=upload_state,wraplength=710).grid(row=3,column=0,sticky='w',pady=4)
    actions=ttk.Frame(frame);actions.grid(row=4,column=0,sticky='ew',pady=(5,0))
    ready=[False];pending=[False];saved_owner=['正在读取…']
    def export():
        if not ready[0] or pending[0]:return
        path=filedialog.asksaveasfilename(parent=dialog,title='保存测量总结',initialdir=view._export_directory(),
            initialfile=sid+'-summary.pdf',defaultextension='.pdf',filetypes=[('PDF 报告','*.pdf')])
        if not path:return
        pending[0]=True;status.set('正在生成 PDF…');pdf.configure(state='disabled')
        def done(value):
            pending[0]=False
            if dialog.winfo_exists():status.set('报告已保存：'+str(value));pdf.configure(state='normal')
        def failed(error):
            pending[0]=False
            if dialog.winfo_exists():status.set('报告未生成：'+str(error));pdf.configure(state='normal')
        view._task(lambda:view.controller.export_measurement_pdf(sid,path),done,failed)
    def upload():
        if not ready[0] or pending[0] or not view.cloud:return
        if view.cloud.snapshot().get('connection')=='unauthorized':return
        pending[0]=True;send.configure(state='disabled')
        def done(_):pending[0]=False
        def failed(error):
            pending[0]=False
            if dialog.winfo_exists():status.set(str(error))
        view._task(lambda:view.cloud.upload(sid),done,failed)
    pdf=ttk.Button(actions,text='导出 PDF',command=export,state='disabled');pdf.pack(side='left')
    send=ttk.Button(actions,text='上传并分析',command=upload,state='disabled',style='Start.TButton');send.pack(side='left',padx=8)
    ttk.Button(actions,text='复核波形',command=lambda:view.show_classification_details(session_id=sid)).pack(side='left')
    from ui.mobile_view import show_mobile
    ttk.Button(actions,text='手机查看',command=lambda:show_mobile(view,sid,saved_owner[0])).pack(side='left',padx=8)
    ttk.Button(actions,text='关闭',command=dialog.destroy,style='Quiet.TButton').pack(side='right')
    def loaded(summary):
        if not dialog.winfo_exists():return
        for key,value in summary_lines(summary):table.insert('','end',values=(key,value))
        saved_owner[0]=summary['owner_name']
        ready[0]=True;pdf.configure(state='normal')
        status.set('N 类不代表临床正常。心率与分类仅来自有效片段；没有结果时不推断为正常。')
    def load_failed(error):
        if dialog.winfo_exists():status.set('无法读取总结：'+str(error))
    view._task(lambda:view.controller.measurement_summary(sid),loaded,load_failed)
    def poll():
        if not dialog.winfo_exists() or view._closed:return
        state=view.cloud.snapshot() if view.cloud else {}
        if state.get('connection')=='unauthorized':
            upload_state.set('云端：设备凭据未配置或已失效，更新后再上传；本地记录保留')
        elif state.get('session_id')==sid:
            upload_state.set('云端：'+state.get('message',''))
        else:upload_state.set('云端：本窗口尚未确认此记录上传结果；可在云端分析窗口查看')
        busy=state.get('phase') in ('waiting','uploading','watching','analyzing')
        send.configure(state='normal' if ready[0] and view.cloud and state.get('connection')!='unauthorized' and not busy and not pending[0] else 'disabled')
        pdf.configure(state='normal' if ready[0] and not pending[0] else 'disabled')
        view.root.after(500,poll)
    poll()
