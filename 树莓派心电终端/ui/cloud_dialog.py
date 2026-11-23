"""Cloud results are shown separately from local real-time predictions."""
import tkinter as tk
from tkinter import ttk


def show_cloud(view):
    service = view.cloud
    existing = getattr(view, '_cloud_dialog', None)
    if existing is not None and existing.winfo_exists():
        existing.lift()
        return
    dialog = tk.Toplevel(view.root)
    view._cloud_dialog = dialog
    dialog.title('云端分析')
    dialog.geometry('750x430')
    dialog.minsize(650, 390)
    dialog.configure(background='#0a100d')
    frame = ttk.Frame(dialog, padding=18)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='云端分析', font=(view.family, 18, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='自动连接 · 断线补传 · 本地采集继续运行', style='Muted.TLabel').pack(anchor='w', pady=(4, 14))
    owner = tk.StringVar()
    ttk.Label(frame, textvariable=owner, style='Muted.TLabel').pack(anchor='w', pady=(0,8))
    selected = tk.StringVar()
    choices = ttk.Combobox(frame, textvariable=selected, state='readonly')
    choices.pack(fill='x')
    rows = {}
    owners = {}
    status = tk.StringVar(value='正在检查连接…')
    target = tk.StringVar()
    result = tk.StringVar(value='选择本地记录，上传后返回云端 N / S / V 分类统计。')
    ttk.Label(frame, textvariable=status, wraplength=650).pack(anchor='w', pady=(14, 4))
    ttk.Label(frame, textvariable=target, style='Muted.TLabel').pack(anchor='w')
    progress = ttk.Progressbar(frame, mode='determinate')
    progress.pack(fill='x', pady=10)
    ttk.Label(frame, textvariable=result, wraplength=650, justify='left').pack(anchor='w', fill='x')
    ttk.Label(frame, text='云端结果对应所选整段记录，与本地实时结果分开展示。仅供课程研究。',
              style='Muted.TLabel', wraplength=650).pack(side='bottom', anchor='w', pady=(12, 0))
    actions = ttk.Frame(frame)
    actions.pack(side='bottom', fill='x', pady=(10, 0))

    def loaded(items):
        if not dialog.winfo_exists():
            return
        rows.clear()
        owners.clear()
        for row in items:
            sid = str(row['session_id'])
            from measurement import owner_label
            owners[sid]=owner_label(row)
            caption = f"{sid} · {int(row.get('sample_count') or 0):,} 点 · {owners[sid]}"
            rows[caption] = sid
        choices.configure(values=list(rows))
        current = view._last_snapshot.get('session_id')
        preferred = next((label for label, sid in rows.items() if sid == current), None)
        if selected.get() not in rows:
            selected.set(preferred or next(iter(rows), ''))

    def refresh():
        if commands['refresh']:
            return
        commands['refresh'] = True
        def done(items):
            commands['refresh'] = False
            loaded(items)
        def failed(error):
            commands['refresh'] = False
            if dialog.winfo_exists():
                status.set('读取记录失败：' + str(error))
        view._task(getattr(view.controller, 'list_cloud_sessions', view.controller.list_sessions), done, failed)

    commands = {'pending': False, 'refresh': False}

    def submit(operation):
        if commands['pending']:
            return
        commands['pending'] = True
        send.configure(state='disabled')
        cancel.configure(state='disabled')
        status.set('正在提交操作，波形采集继续运行…')
        def done(_):
            commands['pending'] = False
            last[0] = None
        def failed(error):
            commands['pending'] = False
            last[0] = None
            if dialog.winfo_exists():
                status.set(str(error))
        # Disk validation/fsync and command serialisation belong to a worker,
        # never to a Tk button callback.
        view._task(operation, done, failed)

    def upload():
        if service.snapshot().get('connection')=='unauthorized':
            status.set('请先更新设备凭据并重新连接')
            return
        sid = rows.get(selected.get())
        if not sid:
            status.set('请先采集并选择一条记录')
            return
        submit(lambda: service.upload(sid))

    send = ttk.Button(actions, text='上传并分析', command=upload, style='Start.TButton')
    send.pack(side='left')
    cancel = ttk.Button(actions, text='取消任务', command=lambda: submit(service.cancel))
    cancel.pack(side='left', padx=8)
    ttk.Button(actions, text='刷新记录', command=refresh, style='Quiet.TButton').pack(side='left')
    ttk.Button(actions, text='重新连接', command=service.reconnect, style='Quiet.TButton').pack(side='left')
    ttk.Button(actions, text='关闭', command=dialog.destroy, style='Quiet.TButton').pack(side='right')
    last = [None]

    def poll():
        if not dialog.winfo_exists() or view._closed:
            return
        state = service.snapshot()
        owner.set('此记录归属：' + owners.get(rows.get(selected.get()),'请选择记录') + '；补传沿用记录创建时的归属')
        if state != last[0]:
            last[0] = state
            connected = {'online': '已连接', 'offline': '断线，自动重连中',
                         'connecting': '连接中', 'error': '连接异常','unauthorized':'设备未授权'}.get(state['connection'], '未连接')
            detail=state.get('connection_detail') if state['connection']=='unauthorized' else state['message']
            status.set(f"{connected} · {detail}")
            target.set('当前任务：' + (state['session_id'] or '尚未选择'))
            done, total = state.get('progress') or (0, 0)
            progress.configure(maximum=max(1, total), value=done)
            busy = state['phase'] in ('waiting', 'uploading', 'watching', 'analyzing')
            send.configure(state='disabled' if busy or commands['pending'] or state['connection']=='unauthorized' else 'normal')
            cancel.configure(state='normal' if busy and not commands['pending'] else 'disabled')
            data = state.get('result')
            if data:
                model = data.get('model_result') or {}
                counts = model.get('counts') or {}
                result.set(f"N 常见搏动组 {counts.get('N', 0)}    S 室上性 {counts.get('S', 0)}    V 室性 {counts.get('V', 0)}\n"
                           f"已分类 {model.get('beat_count', 0)} 拍 · 有效样本 {model.get('eligible_samples', 0):,}\n"
                           f"模型：{data.get('model_name', '未提供')} · {data.get('model_version', '')}\n"
                           '完整结果已保存到 data/cloud/results。N 类不代表完全健康。')
            else:
                result.set('等待云端结果；未得到有效结果时不显示分类数值。')
        view.root.after(300, poll)
    refresh()
    poll()
