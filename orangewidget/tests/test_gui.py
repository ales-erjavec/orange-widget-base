import time
import unittest
from unittest.mock import Mock

from AnyQt.QtCore import Qt, QTimer, QDateTime, QDate, QTime

from orangewidget import gui
from orangewidget.tests.base import GuiTest, WidgetTest
from orangewidget.utils.tests.test_itemdelegates import paint_with_data
from orangewidget.widget import OWBaseWidget


class TestDoubleSpin(GuiTest):
    # make sure that the gui element does not crash when
    # 'checked' parameter is forwarded, ie. is not None
    def test_checked_extension(self):
        widget = OWBaseWidget()
        widget.some_param = 0
        widget.some_option = False
        gui.doubleSpin(widget=widget, master=widget, value="some_param",
                       minv=1, maxv=10, checked="some_option")


class TestFloatSlider(GuiTest):

    def test_set_value(self):
        w = gui.FloatSlider(Qt.Horizontal, 0., 1., 0.5)
        w.setValue(1)
        # Float slider returns value divided by step
        # 1/0.5 = 2
        self.assertEqual(w.value(), 2)
        w = gui.FloatSlider(Qt.Horizontal, 0., 1., 0.05)
        w.setValue(1)
        # 1/0.05 = 20
        self.assertEqual(w.value(), 20)


class TestDelayedNotification(WidgetTest):

    def test_immediate(self):
        dn = gui.DelayedNotification(timeout=5000)
        call = Mock()
        dn.notification.connect(call)
        dn.notify_immediately()
        self.process_events(lambda: call.call_args is not None, timeout=1)

    def test_notify_eventually(self):
        dn = gui.DelayedNotification(timeout=500)
        call = Mock()
        dn.notification.connect(call)
        dn.changed()
        self.process_events(lambda: True, timeout=1)
        self.assertIsNone(call.call_args)  # no immediate notification
        self.process_events(lambda: call.call_args is not None)

    def test_delay_by_change(self):
        dn = gui.DelayedNotification(timeout=500)
        call = Mock()
        dn.notification.connect(call)
        timer = QTimer()
        timer.timeout.connect(dn.changed)
        timer.start(100)
        dn.changed()
        # notification should never be emitted as the input changes too fast
        with self.assertRaises(TimeoutError):
            self.process_events(lambda: call.call_args is not None, timeout=1000)

    def test_no_notification_on_no_change(self):
        dn = gui.DelayedNotification(timeout=500)
        call = Mock()
        dn.notification.connect(call)
        dn.changed(42)
        dn.notify_immediately()  # only for faster test
        self.process_events(lambda: call.call_args is not None)  # wait for the first call
        dn.changed(43)
        dn.changed(42)
        # notification should not be called again
        with self.assertRaises(TimeoutError):
            self.process_events(lambda: len(call.call_args_list) > 1, timeout=1000)


class TestCheckBoxWithDisabledState(GuiTest):
    def test_check_checkbox_disable_false(self):
        widget = OWBaseWidget()
        widget.some_option = False
        cb = gui.checkBox(widget, widget, "some_option", "foo",
                          stateWhenDisabled=False)
        self.assertFalse(cb.isChecked())
        cb.setEnabled(False)
        self.assertFalse(cb.isChecked())
        widget.some_option = True
        self.assertFalse(cb.isChecked())
        cb.setEnabled(True)
        self.assertTrue(cb.isChecked())
        widget.some_option = False
        self.assertFalse(cb.isChecked())

        cb.setDisabled(True)
        self.assertFalse(cb.isChecked())
        widget.some_option = True
        self.assertFalse(cb.isChecked())
        cb.setDisabled(False)
        self.assertTrue(cb.isChecked())
        widget.some_option = False
        self.assertFalse(cb.isChecked())

    def test_check_checkbox_disable_true(self):
        widget = OWBaseWidget()
        widget.some_option = False
        cb = gui.checkBox(widget, widget, "some_option", "foo",
                          stateWhenDisabled=True)
        self.assertFalse(cb.isChecked())
        cb.setEnabled(False)
        self.assertTrue(cb.isChecked())
        widget.some_option = True
        self.assertTrue(cb.isChecked())
        cb.setEnabled(True)
        self.assertTrue(cb.isChecked())
        widget.some_option = False
        self.assertFalse(cb.isChecked())

        cb.setDisabled(True)
        self.assertTrue(cb.isChecked())
        widget.some_option = True
        self.assertTrue(cb.isChecked())
        cb.setDisabled(False)
        self.assertTrue(cb.isChecked())
        widget.some_option = False
        self.assertFalse(cb.isChecked())

    def test_clicks(self):
        widget = OWBaseWidget()
        widget.some_option = False
        cb = gui.checkBox(widget, widget, "some_option", "foo",
                          stateWhenDisabled=False)
        cb.clicked[bool].emit(True)
        cb.setEnabled(False)
        cb.setEnabled(True)
        self.assertTrue(cb.isChecked())

    def test_set_checked(self):
        widget = OWBaseWidget()

        widget.some_option = False
        cb = gui.checkBox(widget, widget, "some_option", "foo",
                          stateWhenDisabled=False)
        self.assertFalse(cb.isChecked())
        cb.setEnabled(False)
        cb.setChecked(True)
        self.assertFalse(cb.isChecked())
        cb.setEnabled(True)
        self.assertTrue(cb.isChecked())

        widget.some_option = True
        cb = gui.checkBox(widget, widget, "some_option", "foo",
                          stateWhenDisabled=True)
        self.assertTrue(cb.isChecked())
        cb.setEnabled(False)
        cb.setChecked(False)
        self.assertTrue(cb.isChecked())
        cb.setEnabled(True)
        self.assertFalse(cb.isChecked())

    def test_set_check_state(self):
        widget = OWBaseWidget()

        widget.some_option = 0
        cb = gui.checkBox(widget, widget, "some_option", "foo",
                          stateWhenDisabled=Qt.Unchecked)
        cb.setCheckState(Qt.Unchecked)
        cb.setEnabled(False)
        self.assertEqual(cb.checkState(), Qt.Unchecked)

        cb.setCheckState(Qt.PartiallyChecked)
        self.assertEqual(cb.checkState(), Qt.Unchecked)
        cb.setEnabled(True)
        self.assertEqual(cb.checkState(), Qt.PartiallyChecked)
        cb.setEnabled(False)
        self.assertEqual(cb.checkState(), Qt.Unchecked)

        cb.setCheckState(Qt.Checked)
        self.assertEqual(cb.checkState(), Qt.Unchecked)
        cb.setEnabled(True)
        self.assertEqual(cb.checkState(), Qt.Checked)
        cb.setEnabled(False)
        self.assertEqual(cb.checkState(), Qt.Unchecked)

        widget.some_option = 2
        cb = gui.checkBox(widget, widget, "some_option", "foo",
                          stateWhenDisabled=Qt.PartiallyChecked)
        cb.setCheckState(Qt.Unchecked)
        cb.setEnabled(False)
        self.assertEqual(cb.checkState(), Qt.PartiallyChecked)

        cb.setCheckState(Qt.Unchecked)
        self.assertEqual(cb.checkState(), Qt.PartiallyChecked)
        cb.setEnabled(True)
        self.assertEqual(cb.checkState(), Qt.Unchecked)
        cb.setEnabled(False)
        self.assertEqual(cb.checkState(), Qt.PartiallyChecked)

        cb.setCheckState(Qt.Checked)
        self.assertEqual(cb.checkState(), Qt.PartiallyChecked)
        cb.setEnabled(True)
        self.assertEqual(cb.checkState(), Qt.Checked)
        cb.setEnabled(False)
        self.assertEqual(cb.checkState(), Qt.PartiallyChecked)


class TestDateTimeEditWCalendarTime(GuiTest):
    def test_set_datetime(self):
        c = gui.DateTimeEditWCalendarTime(None)

        # default time (now)
        c.set_datetime()
        self.assertLessEqual(
            abs(c.dateTime().toSecsSinceEpoch() - time.time()),
            2)

        # some date
        poeh = QDateTime(QDate(1961, 4, 12), QTime(6, 7))
        c.set_datetime(poeh)
        self.assertEqual(c.dateTime(), poeh)

        # set a different time
        ali = QTime(8, 5)
        c.set_datetime(ali)
        poeh.setTime(ali)
        self.assertEqual(c.dateTime(), poeh)


class TestDeferred(GuiTest):
    def test_deferred(self) -> None:
        class Widget(OWBaseWidget):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.option = False
                self.autocommit = False

                self.checkbox = gui.checkBox(self, self, "option", "foo",
                                             callback=self.apply.deferred)

                self.commit_button = gui.auto_commit(
                    self, self, 'autocommit', 'Commit', commit=self.apply)

            real_apply = Mock()
            # Unlike real functions, mocks don't have names
            real_apply.__name__ = "apply"
            apply = gui.deferred(real_apply)

        w = Widget()

        # clicked, but no autocommit
        w.checkbox.click()
        w.real_apply.assert_not_called()

        # manual commit
        w.commit_button.button.click()
        w.real_apply.assert_called()
        w.real_apply.reset_mock()

        # enable auto commit - this should not trigger commit
        w.commit_button.checkbox.click()
        w.real_apply.assert_not_called()

        # clicking control should auto commit
        w.checkbox.click()
        w.real_apply.assert_called()
        w.real_apply.reset_mock()

        # disabling and reenable auto commit without chenging the control
        # should not trigger commit
        w.commit_button.checkbox.click()
        w.real_apply.assert_not_called()

        # calling now should always call the apply
        w.apply.now()
        w.real_apply.assert_called_with(w)
        w.real_apply.reset_mock()

        # calling decorated method without `now` or `deferred` raises an expception
        self.assertRaises(RuntimeError, w.apply)

        w2 = Widget()
        w.apply.now()
        w.real_apply.assert_called_with(w)
        w.real_apply.reset_mock()

    def test_warn_to_defer(self):
        class Widget(OWBaseWidget):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.autocommit = False
                self.commit_button = gui.auto_commit(
                    self, self, 'autocommit', 'Commit')

            def commit(self):
                pass

        with self.assertWarns(UserWarning):
            _ = Widget()

    def test_override(self):
        class Widget(OWBaseWidget, openclass=True):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.autocommit = False
                self.commit_button = gui.auto_commit(
                    self, self, 'autocommit', 'Commit')

            m = Mock()
            n = Mock()

            @gui.deferred
            def commit(self):
                self.m()

        class Widget2(Widget):
            @gui.deferred
            def commit(self):
                super().commit()
                self.n()

        w = Widget2()
        w.commit.now()
        w.m.assert_called_once()
        w.n.assert_called_once()
        w.m.reset_mock()
        w.n.reset_mock()

        class Widget3(Widget):
            @gui.deferred
            def commit(self):
                self.n()

        w = Widget3()
        w.commit.now()
        w.m.assert_not_called()
        w.n.assert_called_once()
        w.m.reset_mock()
        w.n.reset_mock()

        # This tests that exception is raised if derived method is undecorated
        class Widget4(Widget):
            def commit(self):
                self.n()

        self.assertRaises(RuntimeError, Widget4)

    def test_override_and_decorate(self):
        class Widget(OWBaseWidget, openclass=True):
            m = Mock()
            n = Mock()

            def commit(self):
                self.m()

        class Widget2(Widget):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.autocommit = False
                self.commit_button = gui.auto_commit(
                    self, self, 'autocommit', 'Commit')

            @gui.deferred
            def commit(self):
                super().commit()
                self.n()

        w = Widget2()
        w.commit.deferred()
        w.m.assert_not_called()
        w.n.assert_not_called()

        w.autocommit = True
        w.commit.deferred()
        w.m.assert_called_once()
        w.n.assert_called_once()

    def test_two_autocommits(self):
        class Widget(OWBaseWidget, openclass=True):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.autocommit = False
                self.automagog = False
                self.commit_button = gui.auto_commit(
                    self, self, 'autocommit', 'Commit', commit=self.commit)
                self.magog_button = gui.auto_commit(
                    self, self, 'automagog', 'Magog', commit=self.magog)

            real_commit = Mock()
            real_magog = Mock()

            @gui.deferred
            def commit(self):
                self.real_commit()

            @gui.deferred
            def magog(self):
                self.real_magog()

        w = Widget()

        # Make a deffered call to commit; nothing should be called
        w.commit.deferred()
        w.real_commit.assert_not_called()
        w.real_magog.assert_not_called()

        # enable check boxes, but only commit is dirty
        w.commit_button.checkbox.click()
        w.magog_button.checkbox.click()
        w.real_commit.assert_called()
        w.real_magog.assert_not_called()
        w.real_commit.reset_mock()

        # disable, enable, disable; nothing is dirty => shouldn't call anything
        w.commit_button.checkbox.click()
        w.magog_button.checkbox.click()
        w.commit_button.checkbox.click()
        w.magog_button.checkbox.click()
        w.commit_button.checkbox.click()
        w.magog_button.checkbox.click()

        # Make a deffered call to magog; nothing should be called
        w.magog.deferred()
        w.real_commit.assert_not_called()
        w.real_magog.assert_not_called()

        # enable check boxes, but only magog is dirty
        w.commit_button.checkbox.click()
        w.magog_button.checkbox.click()
        w.real_commit.assert_not_called()
        w.real_magog.assert_called()
        w.real_magog.reset_mock()

        # disable, enable; nothing is dirty => shouldn't call anything
        w.commit_button.checkbox.click()
        w.magog_button.checkbox.click()
        w.commit_button.checkbox.click()
        w.magog_button.checkbox.click()


class TestBarItemDelegate(GuiTest):
    def test(self):
        delegate = gui.BarItemDelegate(None)
        paint_with_data(delegate, {Qt.DisplayRole: 0.5})


class TestIndicatorItemDelegate(GuiTest):
    def test(self):
        delegate = gui.IndicatorItemDelegate(None)
        paint_with_data(delegate, {Qt.DisplayRole: True})


class TestColoredBarItemDelegate(GuiTest):
    def test(self):
        delegagte = gui.ColoredBarItemDelegate()
        paint_with_data(delegagte, {Qt.DisplayRole: 1.0, gui.BarRatioRole: 0.5})
        nan = float("nan")
        paint_with_data(delegagte, {Qt.DisplayRole: nan, gui.BarRatioRole: nan})


if __name__ == "__main__":
    unittest.main()
