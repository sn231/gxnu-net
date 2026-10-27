"""托盘回归测试：真实 Windows 消息循环，模拟网络及系统通知。

运行：python -m unittest discover -s tests -p test_tray.py -v
"""

import threading
import time
import unittest
from unittest.mock import Mock, patch

from gxnu_net.config import Settings
from gxnu_net.drcom import LoginResult
from gxnu_net import tray


@unittest.skipIf(tray.win32gui is None, "需要 Windows 和 pywin32")
class TrayTests(unittest.TestCase):
    def setUp(self):
        self.log = Mock()
        self.app = tray.TrayApp(Settings(username="test-user", password="test-password"), self.log)
        self.app.st.interval = 0.01
        self.app.watchdog.interval = 0.01
        self.app.watchdog.recheck_delay = 0.01
        self.notify_threads = []
        self.notify = Mock(side_effect=lambda *args: self.notify_threads.append(threading.get_ident()))
        self.patch = patch.object(tray.win32gui, "Shell_NotifyIcon", self.notify)
        self.patch.start()
        self.app._create_window()
        self.app._add_tray()
        self.main_thread = threading.get_ident()
        self.worker = None

    def tearDown(self):
        self.app._stop_event.set()
        if self.worker:
            self.worker.join(timeout=2)
            self.assertFalse(self.worker.is_alive())
        self.app.quit()
        tray.win32gui.PumpWaitingMessages()
        for icon in self.app._icons.values():
            tray.win32gui.DestroyIcon(icon)
        tray.win32gui.UnregisterClass("GxnuNetTray", tray.win32api.GetModuleHandle(None))
        self.patch.stop()

    def pump_until(self, condition):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            tray.win32gui.PumpWaitingMessages()
            if condition():
                return
            time.sleep(0.005)
        self.fail("托盘消息未在时限内处理")

    def modifications(self):
        return [call.args[1] for call in self.notify.call_args_list
                if call.args[0] == tray.win32gui.NIM_MODIFY]

    def start_monitor(self):
        self.worker = threading.Thread(target=self.app._monitor_loop)
        self.worker.start()

    def test_online_updates_icon_and_tooltip_on_window_thread(self):
        self.app.watchdog.is_online = Mock(return_value=True)
        self.start_monitor()
        self.pump_until(lambda: len(self.modifications()) >= 2)
        self.assertTrue(self.worker.is_alive(), "首次刷新后后台必须继续检测")
        self.assertGreaterEqual(self.app.watchdog.is_online.call_count, 2)
        for nid in self.modifications():
            self.assertEqual(nid[4], self.app._icons[tray.STATE_ONLINE])
            self.assertIn("校园网已连接", nid[5])
        self.assertEqual(set(self.notify_threads), {self.main_thread})

    def test_offline_threshold_and_successful_reconnect_update_icon(self):
        self.app.watchdog.is_online = Mock(return_value=False)
        self.app.watchdog.try_login = Mock(return_value=LoginResult(True, "ok", "登录成功"))
        self.app._monitor_once()
        tray.win32gui.PumpWaitingMessages()
        self.assertEqual(self.modifications()[-1][4], self.app._icons[tray.STATE_OFFLINE])
        self.app.watchdog.try_login.assert_not_called()
        self.app._monitor_once()
        tray.win32gui.PumpWaitingMessages()
        self.app.watchdog.try_login.assert_called_once()
        self.assertEqual(self.modifications()[-1][4], self.app._icons[tray.STATE_ONLINE])

    def test_monitor_logs_exception_then_recovers(self):
        def check():
            if self.app.watchdog.is_online.call_count == 1:
                raise RuntimeError("simulated network failure")
            return True
        self.app.watchdog.is_online = Mock(side_effect=check)
        self.start_monitor()
        self.pump_until(lambda: self.modifications()
                        and self.modifications()[-1][4] == self.app._icons[tray.STATE_ONLINE])
        self.log.exception.assert_called_once()
        self.assertTrue(self.worker.is_alive())
        self.assertGreaterEqual(self.app.watchdog.is_online.call_count, 2)

    def test_notification_failure_is_logged_and_next_update_succeeds(self):
        self.notify.side_effect = [RuntimeError("simulated shell failure"), None]
        self.app._set_state(tray.STATE_ONLINE, "connected")
        tray.win32gui.PumpWaitingMessages()
        self.log.exception.assert_called_once()
        self.app._set_state(tray.STATE_ONLINE, "still connected")
        tray.win32gui.PumpWaitingMessages()
        self.assertEqual(len(self.modifications()), 2)
        self.assertIn("校园网已连接", self.modifications()[-1][5])

    def test_refresh_finishes_with_connected_tooltip(self):
        self.app.watchdog.is_online = Mock(return_value=True)
        self.app._on_refresh()
        self.pump_until(lambda: self.modifications()
                        and self.modifications()[-1][4] == self.app._icons[tray.STATE_ONLINE])
        self.assertIn("校园网已连接", self.modifications()[-1][5])

    def test_explorer_restart_restores_current_icon(self):
        self.app._set_state(tray.STATE_ONLINE, "connected")
        tray.win32gui.PumpWaitingMessages()
        tray.win32gui.SendMessage(self.app._hwnd, self.app._taskbar_created, 0, 0)
        action, nid = self.notify.call_args.args
        self.assertEqual(action, tray.win32gui.NIM_ADD)
        self.assertEqual(nid[4], self.app._icons[tray.STATE_ONLINE])
        self.assertIn("校园网已连接", nid[5])

    def test_quit_deletes_icon_once_without_recursive_shutdown(self):
        self.app.quit()
        self.app.quit()
        deletions = [call for call in self.notify.call_args_list
                     if call.args[0] == tray.win32gui.NIM_DELETE]
        self.assertEqual(len(deletions), 1)
        self.assertFalse(tray.win32gui.IsWindow(self.app._hwnd))


if __name__ == "__main__":
    unittest.main()
