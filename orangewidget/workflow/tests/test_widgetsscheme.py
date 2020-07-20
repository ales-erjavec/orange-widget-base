import unittest
import unittest.mock
import logging

from types import SimpleNamespace
from typing import Type

from AnyQt.QtCore import QTimer
from AnyQt.QtWidgets import QAction
from AnyQt.QtTest import QSignalSpy

from orangecanvas.registry import WidgetRegistry, WidgetDescription
from orangecanvas.scheme import SchemeNode
from orangewidget.report.owreport import OWReport
from orangewidget.settings import Setting
from orangewidget.workflow.widgetsscheme import OWWidgetManager, WidgetsScheme
from orangewidget import widget
from orangewidget.tests.base import GuiTest


class Number(widget.OWBaseWidget):
    name = "W1"
    value = Setting(0)

    class Outputs:
        out = widget.Output("X", int)


class Adder(widget.OWBaseWidget, openclass=True):
    name = "Adder"

    a = None
    b = None

    class Inputs:
        a = widget.Input("A", int)
        b = widget.Input("B", int)

    class Outputs:
        out = widget.Output("A+B", int)

    @Inputs.a
    def seta(self, a):
        self.a = a

    @Inputs.b
    def setb(self, b):
        self.b = b

    def handleNewSignals(self):
        if self.a is not None and self.b is not None:
            out = self.a + self.b
        else:
            out = None
        self.Outputs.out.send(out)


class AdderAync(Adder):
    def handleNewSignals(self):
        self.setBlocking(True)
        QTimer.singleShot(10, self.do_send)

    def do_send(self):
        if self.a is not None and self.b is not None:
            out = self.a + self.b
        else:
            out = None
        self.setBlocking(False)
        self.Outputs.out.send(out)


class Show(widget.OWBaseWidget):
    name = "Show"

    class Inputs:
        X = widget.Input("X", int)

    x = None

    @Inputs.X
    def set_x(self, x):
        self.x = x

    def handleNewSignals(self):
        print(self.x)


def registry():
    reg = WidgetRegistry()
    reg.register_widget(WidgetDescription(**Number.get_widget_description()))
    reg.register_widget(WidgetDescription(**Adder.get_widget_description()))
    reg.register_widget(WidgetDescription(**AdderAync.get_widget_description()))
    reg.register_widget(WidgetDescription(**Show.get_widget_description()))
    return reg


def widget_description(class_):
    # type: (Type[widget.OWBaseWidget]) -> WidgetDescription
    return WidgetDescription(**class_.get_widget_description())


def create_workflow():
    # reg = registry()
    model = WidgetsScheme()
    w1_node = model.new_node(widget_description(Number))
    w1 = model.widget_for_node(w1_node)
    w2_node = model.new_node(widget_description(Number))
    w2 = model.widget_for_node(w2_node)
    add_node = model.new_node(widget_description(Adder))
    add = model.widget_for_node(add_node)
    show_node = model.new_node(widget_description(Show))
    show = model.widget_for_node(show_node)

    model.new_link(w1_node, "X", add_node, "A")
    model.new_link(w2_node, "X", add_node, "B")
    model.new_link(add_node, "A+B", show_node, "X")

    class Items(SimpleNamespace):
        w1_node: SchemeNode
        w2_node: SchemeNode
        add_node: SchemeNode
        show_node: SchemeNode
        w1: Number
        w2: Number
        add: Adder
        show: Show

    return model, Items(
        w1=w1, w2=w2, add=add, show=show,
        w1_node=w1_node, w2_node=w2_node, add_node=add_node,
        show_node=show_node
    )


class TestWidgetScheme(GuiTest):
    def test_widgetscheme(self):
        model, widgets = create_workflow()
        w1, w2, add = widgets.w1, widgets.w2, widgets.add
        self.assertIs(model.widget_for_node(widgets.w1_node), w1)
        self.assertIs(model.node_for_widget(w1), widgets.w1_node)

        r = OWReport()
        self.assertFalse(model.has_report())
        model.set_report_view(r)
        self.assertTrue(model.has_report())
        self.assertIs(w1._get_designated_report_view(), r)
        self.assertIs(w2._get_designated_report_view(), r)
        self.assertIs(add._get_designated_report_view(), r)
        # 'reset' the report
        model.set_report_view(None)
        # must create model.report_view
        r = w1._get_designated_report_view()
        self.assertIs(model.report_view(), r)
        # all widgets in the same workflow must share the same instance.
        self.assertIs(w2._get_designated_report_view(), r)
        self.assertIs(add._get_designated_report_view(), r)

        with unittest.mock.patch.object(r, "setVisible", return_value=None) as s:
            model.show_report_view()
            s.assert_called_once_with(True)

        model.sync_node_properties()

        model.clear()
        model.set_report_view(None)


class TestWidgetManager(GuiTest):
    def test_state_tracking(self):
        model, widgets = create_workflow()
        wm = model.widget_manager
        sm = model.signal_manager
        w1, w1_node = widgets.w1, widgets.w1_node
        w1.setBlocking(True)
        self.assertFalse(sm.is_ready(w1_node))
        self.assertTrue(sm.is_invalidated(w1_node))
        w1.setBlocking(False)
        self.assertTrue(sm.is_ready(w1_node))
        self.assertFalse(sm.is_invalidated(w1_node))
        w1.setReady(False)
        self.assertFalse(sm.is_ready(w1_node))
        w1.setReady(True)
        self.assertTrue(sm.is_ready(w1_node))
        w1.setInvalidated(True)
        self.assertTrue(sm.is_invalidated(w1_node))
        w1.setInvalidated(False)
        self.assertFalse(sm.is_invalidated(w1_node))
        w1.Outputs.out.invalidate()
        self.assertTrue(sm.has_invalidated_inputs(widgets.add_node))
        w1.Outputs.out.send(1)
        self.assertFalse(sm.has_invalidated_inputs(widgets.add_node))

        w1.setStatusMessage("$%^#")
        self.assertEqual(w1_node.status_message(), "$%^#")
        w1.setStatusMessage("")
        self.assertEqual(w1_node.status_message(), "")
        w1.progressBarInit()
        self.assertEqual(w1_node.processing_state, 1)
        w1.progressBarSet(42)
        self.assertEqual(w1_node.progress, 42)
        w1.progressBarFinished()
        self.assertEqual(w1_node.processing_state, 0)
        w1.information("We want information.")
        self.assertTrue(
            any(m.contents == "We want information."
                for m in w1_node.state_messages())
        )

    def test_state_init(self):
        def __init__(self, *args, **kwargs):
            super(widget.OWBaseWidget, self).__init__(*args, **kwargs)
            self.setReady(False)
            self.setInvalidated(True)
            self.progressBarInit()
            self.setStatusMessage("Aa")

        with unittest.mock.patch.object(Adder, "__init__", __init__):
            model, widgets = create_workflow()
            sm = model.signal_manager
            node = widgets.add_node
            self.assertFalse(sm.is_ready(node))
            self.assertTrue(sm.is_invalidated(node))
            self.assertTrue(sm.is_active(node))
            self.assertEqual(node.status_message(), "Aa")

    def test_remove_blocking(self):
        model, widgets = create_workflow()
        wm = model.widget_manager
        add = widgets.add

        add.setBlocking(True)
        add.progressBarInit()
        with unittest.mock.patch.object(add, "deleteLater") as delete:
            model.clear()
            delete.assert_not_called()
            add.progressBarFinished()
            add.setBlocking(False)
            delete.assert_called_once()

    def test_env_dispatch(self):
        model, widgets = create_workflow()
        with unittest.mock.patch.object(widgets.w1, "workflowEnvChanged") as c:
            model.set_runtime_env("workdir", "/a/b/c/d")
            c.assert_called_once_with("workdir", "/a/b/c/d", None)
            model.set_runtime_env("workdir", "/a/b/c")
            c.assert_called_with("workdir", "/a/b/c", "/a/b/c/d")

    def test_extra_actions(self):
        model, widgets = create_workflow()
        wm = model.widget_manager
        # set debug level - implicit 'Show properties' action
        log = logging.getLogger("orangewidget.workflow.widgetsscheme")
        level = log.level
        try:
            log.setLevel(logging.DEBUG)
            actions = wm.actions_for_context_menu(widgets.w1_node)
        finally:
            log.setLevel(level)
        self.assertTrue(any(a.objectName() == "show-settings" for a in actions))
        a = QAction("A", widgets.w1, objectName="-extra-action")
        a.setProperty("ext-workflow-node-menu-action", True)
        widgets.w1.addAction(a)
        actions = wm.actions_for_context_menu(widgets.w1_node)
        self.assertIn(a, actions)


class TestSignalManager(GuiTest):
    def test_signalmanager(self):
        model, widgets = create_workflow()
        sm = model.signal_manager
        widgets.w1.Outputs.out.send(42)
        widgets.w2.Outputs.out.send(-42)
        self.assertSequenceEqual(
            sm.node_update_front(), [widgets.add_node]
        )

        sm.process_queued()
        self.assertEqual(widgets.add.a, 42)
        self.assertEqual(widgets.add.b, -42)
        link = model.find_links(widgets.add_node, sink_node=widgets.show_node)
        link = link[0]
        contents = sm.link_contents(link)
        self.assertEqual(next(iter(contents.values())), 0)

        self.assertSequenceEqual(
            sm.node_update_front(), [widgets.show_node]
        )

    def test_state_ready(self):
        model, widgets = create_workflow()
        sm = model.signal_manager
        widgets.w1.Outputs.out.send(42)
        widgets.w2.Outputs.out.send(-42)
        widgets.add.setReady(False)
        self.assertFalse(sm.is_ready(widgets.add_node))
        spy = QSignalSpy(sm.processingStarted[SchemeNode])
        sm.process_next()
        self.assertEqual(len(spy), 0)  # must not have processed the node
        widgets.add.setReady(True)
        self.assertTrue(sm.is_ready(widgets.add_node))
        assert spy.wait()
        self.assertSequenceEqual(spy, [[widgets.add_node]])

    def test_state_invalidated(self):
        model, widgets = create_workflow()
        sm = model.signal_manager
        widgets.w1.Outputs.out.send(42)
        widgets.w2.Outputs.out.send(-42)

        self.assertIn(widgets.add_node, sm.node_update_front())
        widgets.w1.setInvalidated(True)
        self.assertTrue(sm.is_invalidated(widgets.w1_node))
        self.assertSequenceEqual(sm.node_update_front(), [])
        widgets.w1.setInvalidated(False)
        self.assertFalse(sm.is_invalidated(widgets.w1_node))
        self.assertIn(widgets.add_node, sm.node_update_front())

        spy = QSignalSpy(sm.processingStarted[SchemeNode])
        assert spy.wait()
        self.assertSequenceEqual(spy, [[widgets.add_node]])
