import unittest
from unittest import mock
import test_device_mode


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


class MeasurementUiTests(unittest.TestCase):
    setUp=test_device_mode.DeviceUiTests.setUp
    tearDown=test_device_mode.DeviceUiTests.tearDown

    def prepare(self):
        self.controller.storage_status.return_value={'state':'ok','free_bytes':2**30}
        self.controller.list_ports.return_value=[dict(device='COM4',usb=True)]
        self.controller.start.return_value='a'*32
        self.view.refresh_ports()
        self.view.start_button.invoke()
        self.root.update_idletasks()
        return self.view._measurement_dialog

    def test_cancel_never_starts_or_creates_record(self):
        dialog=self.prepare()
        self.controller.start.assert_not_called()
        next(w for w in widgets(dialog) if w.winfo_class()=='TButton' and w['text']=='取消').invoke()
        self.controller.start.assert_not_called()

    def test_dialog_timer_does_not_break_application_shutdown(self):
        import tkinter as tk
        self.prepare()
        for cb in self.root.tk.call('after','info'):self.root.after_cancel(cb)
        self.root.destroy()
        self.assertIsNone(tk._default_root)

    def test_confirm_passes_selected_duration_and_arms_summary(self):
        dialog=self.prepare()
        for w in widgets(dialog):
            if w.winfo_class()=='TCombobox' and '3 分钟' in w['values']:w.set('3 分钟')
        next(w for w in widgets(dialog) if w.winfo_class()=='TButton' and w['text']=='确认并开始').invoke()
        self.assertEqual(self.controller.start.call_args.kwargs['duration'],180)
        self.assertEqual(self.view._summary_session,'a'*32)

    def test_critical_storage_disables_confirm(self):
        self.controller.storage_status.return_value={'state':'critical','free_bytes':1}
        self.view.prepare_measurement()
        dialog=self.view._measurement_dialog
        button=next(w for w in widgets(dialog) if w.winfo_class()=='TButton' and w['text']=='确认并开始')
        self.assertEqual(str(button['state']),'disabled')
        button.invoke();self.controller.start.assert_not_called()

    def test_summary_actions_remain_visible_on_small_display(self):
        from ui.measurement import show_summary
        self.controller.measurement_summary.return_value={'owner_name':'alice'}
        with mock.patch('ui.measurement.summary_lines',return_value=[('记录','已保存')]*15):
            show_summary(self.view,'saved')
        dialog=self.view._summary_dialog
        dialog.geometry('750x430')
        self.root.update()
        for button in widgets(dialog):
            if button.winfo_class()=='TButton':
                self.assertTrue(button.winfo_ismapped(),(button['text'],dialog.winfo_geometry(),button.master.winfo_geometry(),button.master.master.grid_size()))
                bottom=button.winfo_rooty()-dialog.winfo_rooty()+button.winfo_height()
                self.assertLessEqual(bottom,dialog.winfo_height(),button['text'])

    def test_unauthorized_cloud_disables_upload_in_both_dialogs(self):
        from ui.cloud_dialog import show_cloud
        from ui.measurement import show_summary
        service=mock.Mock()
        service.snapshot.return_value=dict(connection='unauthorized',connection_detail='设备凭据失效',phase='idle',
            session_id=None,message='未授权',progress=None,result=None)
        self.view.cloud=service
        self.controller.measurement_summary.return_value={'owner_name':'alice'}
        self.controller.list_cloud_sessions.return_value=[{'session_id':'saved','sample_count':360}]
        show_cloud(self.view)
        send=next(w for w in widgets(self.view._cloud_dialog) if w.winfo_class()=='TButton' and w['text']=='上传并分析')
        self.assertEqual(str(send['state']),'disabled')
        with mock.patch('ui.measurement.summary_lines',return_value=[]):show_summary(self.view,'saved')
        send=next(w for w in widgets(self.view._summary_dialog) if w.winfo_class()=='TButton' and w['text']=='上传并分析')
        self.assertEqual(str(send['state']),'disabled')
        send.invoke();service.upload.assert_not_called()
        # The application cancels callbacks centrally before destroying children.
        import tkinter as tk
        for cb in self.root.tk.call('after','info'):self.root.after_cancel(cb)
        self.root.destroy()
        self.assertIsNone(tk._default_root)


if __name__=='__main__':unittest.main()
