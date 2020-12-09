"""
Testing framework for OWWidgets
"""
import os
import sys
import pickle
import tempfile

import time
import unittest
from contextlib import contextmanager, ExitStack
from unittest.mock import Mock, patch
from typing import List, Optional, TypeVar, Type

import sip

from AnyQt.QtCore import Qt, QObject, pyqtSignal, QElapsedTimer, pyqtSlot
from AnyQt.QtTest import QTest, QSignalSpy
from AnyQt.QtWidgets import (
    QApplication, QComboBox, QSpinBox, QDoubleSpinBox, QSlider
)

from orangewidget.report.owreport import OWReport
from orangewidget.settings import SettingsHandler
from orangewidget.utils.signals import get_input_meta, notify_input_helper
from orangewidget.widget import OWBaseWidget

sip.setdestroyonexit(False)

app = None

DEFAULT_TIMEOUT = 5000

# pylint: disable=invalid-name
T = TypeVar("T")


@contextmanager
def named_file(content, encoding=None, suffix=''):
    file = tempfile.NamedTemporaryFile("wt", delete=False,
                                       encoding=encoding, suffix=suffix)
    file.write(content)
    name = file.name
    file.close()
    try:
        yield name
    finally:
        os.remove(name)


class _Invalidated(QObject):
    completed = pyqtSignal(object)


class _FinishedMonitor(QObject):
    finished = pyqtSignal()

    def __init__(self, widget: 'OWBaseWidget') -> None:
        super().__init__(None)
        self.widget = widget
        self.sm = widget.signalManager  # type: DummySignalManager
        widget.widgetStateChanged.connect(self._changed)
        self._finished = self.is_finished()
        self.invalidated_outputs = self.sm.invalidated_outputs(widget)
        for output in self.invalidated_outputs:
            output.completed.connect(self._completed, Qt.UniqueConnection)

    def is_finished(self) -> bool:
        finished = not (self.widget.isInvalidated() or
                        self.sm.has_invalidated_outputs(self.widget))
        return finished

    @pyqtSlot()
    def _changed(self):
        fin = self.is_finished()
        self.invalidated_outputs = self.sm.invalidated_outputs(self.widget)
        for output in self.invalidated_outputs:
            try:
                output.completed.connect(self._completed, Qt.UniqueConnection)
            except TypeError:  # connection already exists
                pass
        if fin and fin != self._finished:
            self.finished.emit()

    @pyqtSlot()
    def _completed(self):
        self._changed()


class DummySignalManager:
    def __init__(self):
        self.outputs = {}

    def clear(self):
        self.outputs.clear()

    def send(self, widget, signal_name, value, *args, **kwargs):
        if not isinstance(signal_name, str):
            signal_name = signal_name.name
        current = self.outputs.get((widget, signal_name), None)
        self.outputs[(widget, signal_name)] = value
        if isinstance(current, _Invalidated):
            current.completed.emit(value)

    def invalidate(self, widget, signal_name):
        if not isinstance(signal_name, str):
            signal_name = signal_name.name
        self.outputs[(widget, signal_name)] = _Invalidated()

    def wait_for_outputs(self, widget, timeout=DEFAULT_TIMEOUT):
        st = _Invalidated()
        invalidated = self.invalidated_outputs(widget)
        for val in invalidated:
            val.completed.connect(st.completed)
        if invalidated:
            return QSignalSpy(st.completed).wait(timeout)
        else:
            return True

    def has_invalidated_outputs(self, widget):
        invalidated = self.invalidated_outputs(widget)
        return bool(invalidated)

    def invalidated_outputs(self, widget):
        return [value for (w, name), value in self.outputs.items()
                if w is widget and isinstance(value, _Invalidated)]

    def get_output(self, widget, signal_name, timeout=DEFAULT_TIMEOUT):
        if not isinstance(signal_name, str):
            signal_name = signal_name.name
        elapsed = QElapsedTimer()
        if widget.isInvalidated():
            elapsed.start()
            spy = QSignalSpy(widget.invalidatedStateChanged)
            assert spy.wait(timeout)
            timeout = timeout - elapsed.elapsed()
        value = self.outputs.get((widget, signal_name))
        if isinstance(value, _Invalidated) and timeout >= 0:
            spy = QSignalSpy(value.completed)
            assert spy.wait(timeout), "Failed to get output in the specified timeout"
            assert len(spy) == 1
            value = spy[0][0]
        return value

    def wait_for_finished(
            self, widget: 'OWBaseWidget', timeout=DEFAULT_TIMEOUT) -> bool:
        monitor = _FinishedMonitor(widget)
        if monitor.is_finished():
            return True
        else:
            spy = QSignalSpy(monitor.finished)
            return spy.wait(timeout)


class GuiTest(unittest.TestCase):
    """Base class for tests that require a QApplication instance

    GuiTest ensures that a QApplication exists before tests are run an
    """
    tear_down_stack: ExitStack

    @classmethod
    def setUpClass(cls):
        """Prepare for test execution.

        Ensure that a (single copy of) QApplication has been created
        """
        global app
        if app is None:
            app = QApplication.instance()
        if app is None:
            app = QApplication(["-", "-widgetcount"])

        # Disable App Nap on macOS (see
        # https://codereview.qt-project.org/c/qt/qtbase/+/202515 for more)
        if sys.platform == "darwin":
            try:
                import appnope
            except ImportError:
                pass
            else:
                appnope.nope()
        cls.tear_down_stack = ExitStack()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        if "pyqtgraph" in sys.modules:
            import pyqtgraph
            pyqtgraph.setConfigOption("exitCleanup", False)
        cls.tear_down_stack.close()
        super().tearDownClass()
        QTest.qWait(0)


class WidgetTest(GuiTest):
    """Base class for widget tests

    Contains helper methods widget creation and working with signals.

    All widgets should be created by the create_widget method, as this
    will ensure they are created correctly.
    """

    widgets = []  # type: List[OWBaseWidget]

    def __init_subclass__(cls, **kwargs):

        def test_minimum_size(self):
            widget = getattr(self, "widget", None)
            if widget is None:
                self.skipTest("minimum size not tested as .widget was not set")
            self.check_minimum_size(widget)

        def test_msg_base_class(self):
            widget = getattr(self, "widget", None)
            if widget is None:
                self.skipTest("msg base class not tested as .widget was not set")
            self.check_msg_base_class(widget)

        if not hasattr(cls, "test_minimum_size"):
            cls.test_minimum_size = test_minimum_size
        if not hasattr(cls, "test_msg_base_class"):
            cls.test_msg_base_class = test_msg_base_class

    @classmethod
    def setUpClass(cls):
        """Prepare environment for test execution

        Construct a dummy signal manager and monkey patch
        OWReport.get_instance to return a manually created instance.
        """
        super().setUpClass()

        cls.widgets = []

        cls.signal_manager = DummySignalManager()

        report = None

        def get_instance():
            nonlocal report
            if report is None:
                report = OWReport()
                if not (os.environ.get("TRAVIS") or os.environ.get("APPVEYOR")):
                    report.show = Mock()
                cls.widgets.append(report)
            return report

        cls.tear_down_stack.enter_context(
            patch.object(OWReport, "get_instance", get_instance)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.signal_manager.clear()
        del cls.signal_manager
        widgets = cls.widgets[:]
        cls.widgets.clear()
        while widgets:
            w = widgets.pop(-1)
            if not w.__dict__.get("_Cls__didCallOnDeleteWidget", False):
                w.onDeleteWidget()
            if not sip.isdeleted(w):
                w.deleteLater()
            w.signalManager = None
        super().tearDownClass()

    def tearDown(self):
        """Process any pending events before the next test is executed."""
        self.signal_manager.clear()
        QTest.qWait(0)
        super().tearDown()

    def create_widget(self, cls, stored_settings=None, reset_default_settings=True):
        # type: (Type[T], Optional[dict], bool) -> T
        """Create a widget instance using mock signal_manager.

        When used with default parameters, it also overrides settings stored
        on disk with default defined in class.

        After widget is created, QApplication.process_events is called to
        allow any singleShot timers defined in __init__ to execute.

        Parameters
        ----------
        cls : WidgetMetaClass
            Widget class to instantiate
        stored_settings : dict
            Default values for settings
        reset_default_settings : bool
            If set, widget will start with default values for settings,
            if not, values accumulated through the session will be used

        Returns
        -------
        Widget instance : cls
        """
        # Use a substitute subclass to mark calls to onDeleteWidget; Some tests
        # call this on their own (this used to be done in tearDownClass, then
        # it was not, so tests did it themself, now it is done again).
        with open_widget_classes():
            class Cls(cls):
                def onDeleteWidget(self):
                    self.__didCallOnDeleteWidget = True
                    super(Cls, self).onDeleteWidget()
                __didCallOnDeleteWidget = False

            Cls.__name__ = cls.__name__
            Cls.__qualname__ = cls.__qualname__
            Cls.__module__ = cls.__module__

        if reset_default_settings:
            self.reset_default_settings(Cls)
        widget = Cls.__new__(Cls, signal_manager=self.signal_manager,
                             stored_settings=stored_settings)
        widget.__init__()
        self.process_events()
        self.widgets.append(widget)
        return widget

    @staticmethod
    def reset_default_settings(widget):
        """Reset default setting values for widget

        Discards settings read from disk and changes stored by fast_save

        Parameters
        ----------
        widget : OWBaseWidget
            widget to reset settings for
        """
        settings_handler = getattr(widget, "settingsHandler", None)
        if settings_handler:
            # Rebind settings handler to get fresh copies of settings
            # in known_settings
            settings_handler.bind(widget)
            # Reset defaults read from disk
            settings_handler.defaults = {}
            # Reset context settings
            settings_handler.global_contexts = []

    def process_events(self, until: callable = None, timeout=DEFAULT_TIMEOUT):
        """Process Qt events, optionally until `until` returns
        something True-ish.

        Needs to be called manually as QApplication.exec is never called.

        Parameters
        ----------
        until: callable or None
            If callable, the events are processed until the function returns
            something True-ish.
        timeout: int
            If until condition is not satisfied within timeout milliseconds,
            a TimeoutError is raised.

        Returns
        -------
        If until is not None, the True-ish result of its call.
        """
        if until is None:
            until = lambda: True

        started = time.perf_counter()
        while True:
            app.processEvents()
            try:
                result = until()
                if result:
                    return result
            except Exception:  # until can fail with anything; pylint: disable=broad-except
                pass
            if (time.perf_counter() - started) * 1000 > timeout:
                raise TimeoutError()
            time.sleep(.05)

    def show(self, widget=None):
        """Show widget in interactive mode.

        Useful for debugging tests, as widget can be inspected manually.
        """
        widget = widget or self.widget
        widget.show()
        app.exec()

    def send_signal(self, input, value, *args, widget=None, wait=-1):
        """ Send signal to widget by calling appropriate triggers.

        Parameters
        ----------
        input : str
        value : Object
        id : int
            channel id, used for inputs with flag Multiple
        widget : Optional[OWBaseWidget]
            widget to send signal to. If not set, self.widget is used
        wait : int
            The amount of time to wait for the widget to complete.
        """
        return self.send_signals([(input, value)], *args,
                                 widget=widget, wait=wait)

    def send_signals(self, signals, *args, widget=None, wait=-1):
        """ Send signals to widget by calling appropriate triggers.
        After all the signals are send, widget's handleNewSignals() in invoked.

        Parameters
        ----------
        signals : list of (str, Object)
        widget : Optional[OWBaseWidget]
            widget to send signals to. If not set, self.widget is used
        wait : int
            The amount of time to wait for the widget to complete.
        """
        if widget is None:
            widgets = {signal.widget for signal, _ in signals
                       if hasattr(signal, "widget")}
            if not widgets:
                widget = self.widget
            elif len(widgets) == 1:
                widget = widgets.pop()
            else:
                raise ValueError("Signals are bound to different widgets")

        for input, value in signals:
            self._send_signal(widget, input, value, *args)
        widget.handleNewSignals()
        if wait >= 0:
            self.wait_until_finished(widget, timeout=wait)

    @staticmethod
    def _send_signal(widget, input, value, *args, **kwargs):

        if isinstance(input, str):
            input = get_input_meta(widget, input)
            if input is None:
                raise ValueError("'{}' is not an input name for widget {}"
                                 .format(input, type(widget).__name__))
        if not widget.isReady():
            raise RuntimeError("'send_signal' called but the widget is not "
                               "in ready state and does not accept inputs.")
        # Assert sent input is of correct class
        assert isinstance(value, (input.type, type(None), type(input.closing_sentinel))), \
            '{} should be {}'.format(value.__class__.__mro__, input.type)
        notify_input_helper(input, widget, value, *args, **kwargs)

    def wait_until_stop_blocking(self, widget=None, wait=DEFAULT_TIMEOUT):
        """Wait until the widget stops blocking i.e. finishes computation.

        Parameters
        ----------
        widget : Optional[OWBaseWidget]
            widget to send signal to. If not set, self.widget is used
        wait : int
            The amount of time to wait for the widget to complete.

        """
        if widget is None:
            widget = self.widget

        if widget.isBlocking():
            spy = QSignalSpy(widget.blockingStateChanged)
            self.assertTrue(spy.wait(timeout=wait))

    def wait_until_finished(
            self, widget: Optional[OWBaseWidget] = None,
            timeout=DEFAULT_TIMEOUT) -> None:
        """Wait until the widget finishes computation.

        The widget is considered finished once all its outputs are valid.

        Parameters
        ----------
        widget : Optional[OWBaseWidget]
            widget to send signal to. If not set, self.widget is used
        timeout : int
            The amount of time to wait for the widget to complete.
        """
        if widget is None:
            widget = self.widget
        self.assertTrue(
            self.signal_manager.wait_for_finished(widget, timeout),
            f"Did not finish in the specified {timeout}ms timeout"
        )

    def commit_and_wait(self, widget=None, wait=DEFAULT_TIMEOUT):
        """Unconditional commit and wait until finished.

        Parameters
        ----------
        widget : Optional[OWBaseWidget]
            widget to send signal to. If not set, self.widget is used
        wait : int
            The amount of time to wait for the widget to complete.

        """
        if widget is None:
            widget = self.widget

        widget.unconditional_commit()
        self.wait_until_finished(widget=widget, timeout=wait)

    def get_output(self, output, widget=None, wait=DEFAULT_TIMEOUT):
        """Return the last output that has been sent from the widget.

        Parameters
        ----------
        output_name : str
        widget : Optional[OWBaseWidget]
            widget whose output is returned. If not set, self.widget is used
        wait : int
            The amount of time (in milliseconds) to wait for widget to complete.

        Returns
        -------
        The last sent value of given output or None if nothing has been sent.
        """
        if widget is None:
            # `output` may be an unbound signal with `widget` set to `None`
            # In this case, we use `self.widget`.
            widget = getattr(output, "widget", self.widget) or self.widget

        if not isinstance(output, str):
            output = output.name
        # widget.outputs are old-style signals; if empty, use new style
        outputs = widget.outputs or widget.Outputs.__dict__.values()
        assert output in (out.name for out in outputs), \
            "widget {} has no output {}".format(widget.name, output)
        return widget.signalManager.get_output(widget, output, wait)

    @contextmanager
    def modifiers(self, modifiers):
        """
        Context that simulates pressed modifiers

        Since QTest.keypress requries pressing some key, we simulate
        pressing "BassBoost" that looks exotic enough to not meddle with
        anything.
        """
        old_modifiers = QApplication.keyboardModifiers()
        try:
            QTest.keyPress(self.widget, Qt.Key_BassBoost, modifiers)
            yield
        finally:
            QTest.keyRelease(self.widget, Qt.Key_BassBoost, old_modifiers)

    def check_minimum_size(self, widget):

        def invalidate_cached_size_hint(w):
            # as in OWBaseWidget.setVisible
            if w.controlArea is not None:
                w.controlArea.updateGeometry()
            if w.buttonsArea is not None:
                w.buttonsArea.updateGeometry()
            if w.mainArea is not None:
                w.mainArea.updateGeometry()

        invalidate_cached_size_hint(widget)
        min_size = widget.minimumSizeHint()
        self.assertLess(min_size.width(), 800)
        self.assertLess(min_size.height(), 700)

    def check_msg_base_class(self, widget):
        """ Test whether widget error, warning and info messages are derived
        from its (direct) parent message classes. """
        def inspect(msg):
            msg_cls = getattr(widget, msg).__class__
            msg_base_cls = getattr(widget.__class__.__bases__[0], msg)
            self.assertTrue(issubclass(msg_cls, msg_base_cls))

        inspect("Error")
        inspect("Warning")
        inspect("Information")


class TestWidgetTest(WidgetTest):
    """Meta tests for widget test helpers"""

    def test_process_events_handles_timeouts(self):
        with self.assertRaises(TimeoutError):
            self.process_events(until=lambda: False, timeout=0)

    def test_minimum_size(self):
        return  # skip this test

    def test_check_msg_base_class(self):
        class A(OWBaseWidget, openclass=True):
            pass

        class B(A):
            class Error(A.Error):
                pass

        class C(A, openclass=True):
            class Error(OWBaseWidget.Error):
                pass

        class D(C):
            class Error(A.Error):
                pass

        self.check_msg_base_class(B())
        self.check_msg_base_class(C())  # It is unfortunate that this passes...
        self.assertRaises(AssertionError, self.check_msg_base_class, D())


class BaseParameterMapping:
    """Base class for mapping between gui components and learner's parameters
    when testing learner widgets.

    Parameters
    ----------
    name : str
        Name of learner's parameter.

    gui_element : QWidget
        Gui component who's corresponding parameter is to be tested.

    values: list
        List of values to be tested.

    getter: function
        It gets component's value.

    setter: function
        It sets component's value.
    """

    def __init__(self, name, gui_element, values, getter, setter,
                 problem_type="both"):
        self.name = name
        self.gui_element = gui_element
        self.values = values
        self.get_value = getter
        self.set_value = setter
        self.problem_type = problem_type

    def __str__(self):
        if self.problem_type == "both":
            return self.name
        else:
            return "%s (%s)" % (self.name, self.problem_type)


class DefaultParameterMapping(BaseParameterMapping):
    """Class for mapping between gui components and learner's parameters
    when testing unchecked properties and therefore default parameters
    should be used.

    Parameters
    ----------
    name : str
        Name of learner's parameter.

    default_value: str, int,
        Value that should be used by default.
    """

    def __init__(self, name, default_value):
        super().__init__(name, None, [default_value],
                         lambda: default_value, lambda x: None)


class ParameterMapping(BaseParameterMapping):
    """Class for mapping between gui components and learner parameters
    when testing learner widgets

    Parameters
    ----------
    name : str
        Name of learner's parameter.

    gui_element : QWidget
        Gui component who's corresponding parameter is to be tested.

    values: list, mandatory for ComboBox, optional otherwise
        List of values to be tested. When None, it is set according to
        component's type.

    getter: function, optional
        It gets component's value. When None, it is set according to
        component's type.

    setter: function, optional
        It sets component's value. When None, it is set according to
        component's type.
    """

    def __init__(self, name, gui_element, values=None,
                 getter=None, setter=None, **kwargs):
        super().__init__(
            name, gui_element,
            values or self._default_values(gui_element),
            getter or self._default_get_value(gui_element, values),
            setter or self._default_set_value(gui_element, values),
            **kwargs)

    @staticmethod
    def get_gui_element(widget, attribute):
        return widget.controlled_attributes[attribute][0].control

    @classmethod
    def from_attribute(cls, widget, attribute, parameter=None):
        return cls(parameter or attribute, cls.get_gui_element(widget, attribute))

    @staticmethod
    def _default_values(gui_element):
        if isinstance(gui_element, (QSpinBox, QDoubleSpinBox, QSlider)):
            return [gui_element.minimum(), gui_element.maximum()]
        else:
            raise TypeError("{} is not supported".format(gui_element))

    @staticmethod
    def _default_get_value(gui_element, values):
        if isinstance(gui_element, (QSpinBox, QDoubleSpinBox, QSlider)):
            return lambda: gui_element.value()
        elif isinstance(gui_element, QComboBox):
            return lambda: values[gui_element.currentIndex()]
        else:
            raise TypeError("{} is not supported".format(gui_element))

    @staticmethod
    def _default_set_value(gui_element, values):
        if isinstance(gui_element, (QSpinBox, QDoubleSpinBox, QSlider)):
            return lambda val: gui_element.setValue(val)
        elif isinstance(gui_element, QComboBox):
            def fun(val):
                value = values.index(val)
                gui_element.activated.emit(value)
                gui_element.setCurrentIndex(value)

            return fun
        else:
            raise TypeError("{} is not supported".format(gui_element))


@contextmanager
def open_widget_classes():
    with patch.object(OWBaseWidget, "__init_subclass__"):
        yield


@contextmanager
def override_default_settings(widget, defaults=None, handler=None):
    if defaults is None:
        defaults = {}

    h = (handler or SettingsHandler)()
    h.widget_class = widget
    h.defaults = defaults
    filename = h._get_settings_filename()

    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "wb") as f:
        pickle.dump(defaults, f)

    yield

    if os.path.isfile(filename):
        os.remove(filename)


if __name__ == "__main__":
    unittest.main()
