"""
Orange Widgets Workflow
=======================

A workflow model for Orange Widgets (OWBaseWidget).

This is a subclass of the :class:`~Scheme`. It is responsible for
the construction and management of OWBaseWidget instances corresponding
to the scheme nodes, as well as delegating the signal propagation to a
companion :class:`WidgetsSignalManager` class.

.. autoclass:: WidgetsScheme
   :bases:

.. autoclass:: WidgetsManager
   :bases:

.. autoclass:: WidgetsSignalManager
   :bases:
"""
import copy
import logging
import enum
import types
import warnings
from functools import singledispatch

from urllib.parse import urlencode
from weakref import finalize

from typing import Optional, Dict, Any, List, overload, Iterable

from AnyQt.QtWidgets import QWidget, QAction
from AnyQt.QtGui import QWhatsThisClickedEvent

from AnyQt.QtCore import Qt, QCoreApplication, QEvent, QByteArray
from AnyQt.QtCore import pyqtSlot as Slot

from orangecanvas.registry import WidgetDescription, OutputSignal, InputSignal

from orangecanvas.scheme.signalmanager import (
    SignalManager, Signal, compress_signals
)
from orangecanvas.scheme import Scheme, SchemeNode
from orangecanvas.scheme.node import UserMessage
from orangecanvas.scheme.widgetmanager import WidgetManager as _WidgetManager
from orangecanvas.utils import name_lookup
from orangecanvas.resources import icon_loader

from orangewidget.widget import OWBaseWidget, Input
from orangewidget.report.owreport import OWReport
from orangewidget.settings import SettingsPrinter

log = logging.getLogger(__name__)


class WidgetsScheme(Scheme):
    """
    A workflow scheme containing Orange Widgets (:class:`OWBaseWidget`).

    Extends the base `Scheme` class to handle the lifetime
    (creation/deletion, etc.) of `OWBaseWidget` instances corresponding to
    the nodes in the scheme. The inter-widget signal propagation is
    delegated to an instance of `WidgetsSignalManager`.
    """
    def __init__(self, parent=None, title=None, description=None, env={},
                 **kwargs):
        super().__init__(parent, title, description, env=env, **kwargs)
        self.widget_manager = WidgetManager()
        self.signal_manager = WidgetsSignalManager(self)
        self.widget_manager.set_scheme(self)
        self.__report_view = None  # type: Optional[OWReport]

    def widget_for_node(self, node):
        """
        Return the OWBaseWidget instance for a `node`.
        """
        return self.widget_manager.widget_for_node(node)

    def node_for_widget(self, widget):
        """
        Return the SchemeNode instance for the `widget`.
        """
        return self.widget_manager.node_for_widget(widget)

    def sync_node_properties(self):
        """
        Sync the widget settings/properties with the SchemeNode.properties.
        Return True if there were any changes in the properties (i.e. if the
        new node.properties differ from the old value) and False otherwise.

        """
        changed = False
        for node in self.nodes:
            settings = self.widget_manager.widget_settings_for_node(node)
            if settings != node.properties:
                node.properties = settings
                changed = True
        log.debug("Scheme node properties sync (changed: %s)", changed)
        return changed

    def show_report_view(self):
        inst = self.report_view()
        inst.show()
        inst.raise_()

    def has_report(self):
        """
        Does this workflow have an associated report

        Returns
        -------
        has_report: bool
        """
        return self.__report_view is not None

    def report_view(self):
        """
        Return a OWReport instance used by the workflow.

        Returns
        -------
        report : OWReport
        """
        if self.__report_view is None:
            parent = self.parent()
            if isinstance(parent, QWidget):
                window = parent.window()  # type: QWidget
            else:
                window = None

            self.__report_view = OWReport()
            if window is not None:
                self.__report_view.setParent(window, Qt.Window)
        return self.__report_view

    def set_report_view(self, view):
        """
        Set the designated OWReport view for this workflow.

        Parameters
        ----------
        view : Optional[OWReport]
        """
        self.__report_view = view

    def dump_settings(self, node: SchemeNode):
        widget = self.widget_for_node(node)

        pp = SettingsPrinter(indent=4)
        pp.pprint(widget.settingsHandler.pack_data(widget))

    def event(self, event):
        if event.type() == QEvent.Close:
            if self.__report_view is not None:
                self.__report_view.close()
            self.signal_manager.stop()
        return super().event(event)


class ProcessingState(enum.IntEnum):
    """OWBaseWidget processing state flags"""
    #: Signal manager is updating/setting the widget's inputs
    InputUpdate = 1
    #:  Widget has entered a blocking state (OWBaseWidget.isBlocking() is True)
    BlockingUpdate = 2
    #: Widget has entered processing state (progressBarInit/Finish)
    ProcessingUpdate = 4
    #: Widget is still in the process of initialization
    Initializing = 8


class Item(types.SimpleNamespace):
    """
    A SchemeNode, OWBaseWidget pair tracked by OWWidgetManager
    """
    def __init__(self, node, widget, state):
        # type: (SchemeNode, Optional[OWBaseWidget], int) -> None
        super().__init__()
        self.node = node
        self.widget = widget
        self.state = state


class OWWidgetManager(_WidgetManager):
    """
    OWBaseWidget instance manager class.

    This class handles the lifetime of OWBaseWidget instances in a
    :class:`WidgetsScheme`.

    """
    InputUpdate, BlockingUpdate, ProcessingUpdate, Initializing = ProcessingState

    #: State mask for widgets that cannot be deleted immediately
    #: (see __try_delete)
    DelayDeleteMask = InputUpdate | BlockingUpdate

    def __init__(self, parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.__scheme = None
        self.__signal_manager = None

        self.__item_for_node = {}  # type: Dict[SchemeNode, Item]

        # Widgets that were 'removed' from the scheme but were at
        # the time in an input update loop and could not be deleted
        # immediately
        self.__delay_delete = {}  # type: Dict[OWBaseWidget, Item]

        # Tracks the widget in the update loop by the SignalManager
        self.__updating_widget = None  # type: Optional[OWBaseWidget]

    def set_scheme(self, scheme):
        """
        Set the :class:`WidgetsScheme` instance to manage.
        """
        self.__scheme = scheme
        self.__signal_manager = scheme.findChild(SignalManager)

        self.__signal_manager.processingStarted[SchemeNode].connect(
            self.__on_processing_started
        )
        self.__signal_manager.processingFinished[SchemeNode].connect(
            self.__on_processing_finished
        )
        scheme.runtime_env_changed.connect(self.__on_env_changed)
        scheme.installEventFilter(self)
        super().set_workflow(scheme)

    def scheme(self):
        """
        Return the scheme instance on which this manager is installed.
        """
        return self.__scheme

    def signal_manager(self):
        """
        Return the signal manager in use on the :func:`scheme`.
        """
        return self.__signal_manager

    def node_for_widget(self, widget):
        # type: (QWidget) -> Optional[SchemeNode]
        """
        Reimplemented.
        """
        node = super().node_for_widget(widget)
        if node is None:
            # the node -> widget mapping requested (by this subclass or
            # WidgetSignalManager) before or after the base class tracks it
            # (in create_widget_for_node or delete_widget_for_node).
            for item in self.__item_for_node.values():
                if item.widget is widget:
                    return item.node
        return node

    def widget_settings_for_node(self, node):
        # type: (SchemeNode) -> Dict[str, Any]
        """
        Return the properties/settings from the widget for node.

        Parameters
        ----------
        node : SchemeNode

        Returns
        -------
        settings : dict
        """
        item = self.__item_for_node.get(node)
        if item is not None and isinstance(item.widget, OWBaseWidget):
            return self.widget_settings(item.widget)
        else:
            return node.properties

    def widget_settings(self, widget):
        # type: (OWBaseWidget) -> Dict[str, Any]
        """
        Return the settings from `OWWidget` instance.

        Parameters
        ----------
        widget : OWBaseWidget

        Returns
        -------
        settings : Dict[str, Any]
        """
        return widget.settingsHandler.pack_data(widget)

    def create_widget_for_node(self, node):
        # type: (SchemeNode) -> QWidget
        """
        Reimplemented.
        """
        widget = self.create_widget_instance(node)
        return widget

    def delete_widget_for_node(self, node, widget):
        # type: (SchemeNode, QWidget) -> None
        """
        Reimplemented.
        """
        assert node not in self.workflow().nodes
        item = self.__item_for_node.get(node)
        if item is not None and isinstance(item.widget, OWBaseWidget):
            assert item.node is node
            if item.state & ProcessingState.Initializing:
                raise RuntimeError(
                    "A widget/node {0} was removed while being initialized. "
                    "This is most likely a result of an explicit "
                    "QApplication.processEvents call from the "
                    "'{1.__module__}.{1.__qualname__}' "
                    "widget's __init__."
                    .format(node.title, type(item.widget))
                )
            # Update the node's stored settings/properties dict before
            # removing the widget.
            # TODO: Update/sync whenever the widget settings change.
            node.properties = self.widget_settings(widget)

            node.title_changed.disconnect(widget.setCaption)
            widget.progressBarValueChanged.disconnect(node.set_progress)

            widget.close()
            # Save settings to user global settings.
            widget.saveSettings()
            # Notify the widget it will be deleted.
            widget.onDeleteWidget()
            # Un befriend the report view
            del widget._Report__report_view

            if log.isEnabledFor(logging.DEBUG):
                finalize(
                    widget, log.debug, "Destroyed namespace for: %s", node.title
                )

            # clear the state ?? (have node.reset()??)
            node.set_progress(0)
            node.set_processing_state(0)
            node.set_status_message("")
            node.set_state(SchemeNode.NoState)
            msgs = [copy.copy(m) for m in node.state_messages()]
            for m in msgs:
                m.contents = ""
                node.set_state_message(m)

            self.__item_for_node.pop(node)
            self.__delete_item(item)

    def __delete_item(self, item):
        item.node = None
        widget = item.widget
        if item.state & WidgetManager.DelayDeleteMask:
            # If the widget is in an update loop and/or blocking we
            # delay the scheduled deletion until the widget is done.
            # The `__on_{processing,blocking}_state_changed` must call
            # __try_delete when the mask is cleared.
            log.debug("Widget %s removed but still in state :%s. "
                      "Deferring deletion.", widget, item.state)
            self.__delay_delete[widget] = item
        else:
            widget.processingStateChanged.disconnect(
                self.__on_widget_state_changed)
            widget.widgetStateChanged.disconnect(
                self.__on_widget_state_changed)
            widget.deleteLater()
            item.widget = None

    def __try_delete(self, item):
        if not item.state & WidgetManager.DelayDeleteMask:
            widget = item.widget
            log.debug("Delayed delete for widget %s", widget)
            widget.widgetStateChanged.disconnect(
                self.__on_widget_state_changed)
            widget.processingStateChanged.disconnect(
                self.__on_widget_state_changed)
            item.widget = None
            widget.deleteLater()
            del self.__delay_delete[widget]

    def create_widget_instance(self, node):
        # type: (SchemeNode) -> OWBaseWidget
        """
        Create a OWBaseWidget instance for the node.
        """
        desc = node.description  # type: WidgetDescription
        signal_manager = self.signal_manager()
        # Setup mapping for possible reentry via signal manager in widget's
        # __init__
        item = Item(node, None, ProcessingState.Initializing)
        self.__item_for_node[node] = item

        try:
            # Lookup implementation class
            klass = name_lookup(desc.qualified_name)
            log.info("WidgetManager: Creating '%s.%s' instance '%s'.",
                     klass.__module__, klass.__name__, node.title)
            item.widget = widget = klass.__new__(
                klass,
                None,
                captionTitle=node.title,
                signal_manager=signal_manager,
                stored_settings=copy.deepcopy(node.properties),
                # NOTE: env is a view of the real env and reflects
                # changes to the environment.
                env=self.scheme().runtime_env()
            )
            widget.__init__()
        except BaseException:
            item.widget = None
            raise
        finally:
            # Clear Initializing flag even in case of error
            item.state &= ~ProcessingState.Initializing

        # bind the OWBaseWidget to the node
        node.title_changed.connect(widget.setCaption)
        # Widget's info/warning/error messages.
        self.__initialize_widget_messages(node, widget)
        widget.messageActivated.connect(self.__on_widget_message_changed)
        widget.messageDeactivated.connect(self.__on_widget_message_changed)

        # Widget's statusMessage
        node.set_status_message(widget.statusMessage())
        widget.statusMessageChanged.connect(node.set_status_message)

        # OWBaseWidget's progress bar state (progressBarInit/Finished,Set)
        widget.progressBarValueChanged.connect(node.set_progress)
        widget.processingStateChanged.connect(
            self.__on_widget_state_changed
        )
        # Advertised state for the workflow execution semantics.
        widget.widgetStateChanged.connect(self.__on_widget_state_changed)

        # Install a help shortcut on the widget
        help_action = widget.findChild(QAction, "action-help")
        if help_action is not None:
            help_action.setEnabled(True)
            help_action.setVisible(True)
            help_action.triggered.connect(self.__on_help_request)

        widget.setWindowIcon(
            icon_loader.from_description(desc).get(desc.icon)
        )
        widget.setCaption(node.title)
        # befriend class Report
        widget._Report__report_view = self.scheme().report_view

        self.__update_item(item)
        return widget

    def node_processing_state(self, node):
        """
        Return the processing state flags for the node.

        Same as `manager.widget_processing_state(manger.widget_for_node(node))`

        """
        if node not in self.__item_for_node:
            if node in self.__scheme.nodes:
                return ProcessingState.Initializing
            else:
                return 0
        return self.__item_for_node[node].state

    def widget_processing_state(self, widget):
        # type: (OWBaseWidget) -> int
        """
        Return the processing state flags for the widget.

        The state is an bitwise of the :class:`ProcessingState` flags.
        """
        node = self.node_for_widget(widget)
        return self.__item_for_node[node].state

    def save_widget_geometry(self, node, widget):
        # type: (SchemeNode, QWidget) -> bytes
        """
        Reimplemented.

        Save and return the current geometry and state for node.
        """
        if isinstance(widget, OWBaseWidget):
            return bytes(widget.saveGeometryAndLayoutState())
        else:
            return super().save_widget_geometry(node, widget)

    def restore_widget_geometry(self, node, widget, state):
        # type: (SchemeNode, QWidget, bytes) -> bool
        """
        Restore the widget geometry state.

        Reimplemented.
        """
        if isinstance(widget, OWBaseWidget):
            return widget.restoreGeometryAndLayoutState(QByteArray(state))
        else:
            return super().restore_widget_geometry(node, widget, state)

    def eventFilter(self, receiver, event):
        if event.type() == QEvent.Close and receiver is self.__scheme:
            # Notify the remaining widget instances (if any).
            for item in list(self.__item_for_node.values()):
                widget = item.widget
                if widget is not None:
                    widget.close()
                    widget.saveSettings()
                    widget.onDeleteWidget()
                    widget.deleteLater()

        return super().eventFilter(receiver, event)

    def __on_help_request(self):
        """
        Help shortcut was pressed. We send a `QWhatsThisClickedEvent` to
        the scheme and hope someone responds to it.

        """
        # Sender is the QShortcut, and parent the OWBaseWidget
        widget = self.sender().parent()
        try:
            node = self.node_for_widget(widget)
        except KeyError:
            pass
        else:
            qualified_name = node.description.qualified_name
            help_url = "help://search?" + urlencode({"id": qualified_name})
            event = QWhatsThisClickedEvent(help_url)
            QCoreApplication.sendEvent(self.scheme(), event)

    def __dump_settings(self):
        sender = self.sender()
        assert isinstance(sender, QAction)
        node = sender.data()
        scheme = self.scheme()
        scheme.dump_settings(node)

    def __initialize_widget_messages(self, node, widget):
        """
        Initialize the tracked info/warning/error message state.
        """
        for message_group in widget.message_groups:
            message = user_message_from_state(message_group)
            if message:
                node.set_state_message(message)

    def __on_widget_message_changed(self, msg):
        """
        The OWBaseWidget info/warning/error state has changed.
        """
        widget = msg.group.widget
        assert widget is not None
        node = self.node_for_widget(widget)
        if node is not None:
            self.__initialize_widget_messages(node, widget)

    def __on_processing_started(self, node):
        """
        Signal manager entered the input update loop for the node.
        """
        assert self.__updating_widget is None, "MUST NOT re-enter"
        # Force widget creation (if not already done)
        _ = self.widget_for_node(node)
        item = self.__item_for_node[node]
        # Remember the widget instance. The node and the node->widget mapping
        # can be removed between this and __on_processing_finished.
        if item.widget is not None:
            self.__updating_widget = item.widget
            item.state |= ProcessingState.InputUpdate
            self.__update_node_processing_state(node)

    def __on_processing_finished(self, node):
        """
        Signal manager exited the input update loop for the node.
        """
        widget = self.__updating_widget
        self.__updating_widget = None
        item = None
        if widget is not None:
            item = self.__item_for_widget(widget)
        if item is None:
            return
        item.state &= ~ProcessingState.InputUpdate
        if item.node is not None:
            self.__update_node_processing_state(node)
        if widget in self.__delay_delete:
            self.__try_delete(item)

    @Slot()
    def __on_widget_state_changed(self):
        """
        OWBaseWidget state has changed.
        """
        widget = self.sender()
        item = None
        if widget is not None:
            item = self.__item_for_widget(widget)
        if item is None:
            warnings.warn(
                "State change for a non-tracked widget {}".format(widget),
                RuntimeWarning,
            )
            return

        if not isinstance(widget, OWBaseWidget):
            return
        self.__update_item(item)

        if item.widget in self.__delay_delete:
            self.__try_delete(item)

    def __item_for_widget(self, widget):
        # type: (OWBaseWidget) -> Optional[Item]
        node = self.node_for_widget(widget)
        if node is not None:
            return self.__item_for_node[node]
        else:
            return self.__delay_delete.get(widget)

    def __update_item(self, item: Item):
        if item.widget is None:
            return
        node, widget = item.node, item.widget
        progress = widget.processingState
        invalidated = widget.isInvalidated()
        ready = widget.isReady()
        initializing = item.state & ProcessingState.Initializing

        def setflag(flags: int, flag: int, on: bool) -> int:
            return flags | flag if on else flags & ~flag

        if node is not None:
            state = node.state()
            state = setflag(state, SchemeNode.Running, progress)
            state = setflag(state, SchemeNode.NotReady, not (ready or initializing))
            state = setflag(state, SchemeNode.Invalidated, invalidated or initializing)
            node.set_state(state)
            if progress:
                node.set_progress(widget.progressBarValue)

        item.state = setflag(
            item.state, ProcessingState.BlockingUpdate, not ready)
        item.state = setflag(
            item.state, ProcessingState.ProcessingUpdate, progress)
        self.signal_manager().post_update_request()

    def __update_node_processing_state(self, node):
        """
        Update the `node.processing_state` to reflect the widget state.
        """
        state = self.node_processing_state(node)
        node.set_processing_state(1 if state else 0)

    def __on_env_changed(self, key, newvalue, oldvalue):
        # Notify widgets of a runtime environment change
        for item in self.__item_for_node.values():
            if item.widget is not None:
                item.widget.workflowEnvChanged(key, newvalue, oldvalue)

    def actions_for_context_menu(self, node):
        # type: (SchemeNode) -> List[QAction]
        """
        Reimplemented from WidgetManager.actions_for_context_menu.

        Parameters
        ----------
        node : SchemeNode

        Returns
        -------
        actions : List[QAction]
        """
        actions = []
        widget = self.widget_for_node(node)
        if widget is not None:
            actions = [a for a in widget.actions()
                       if a.property("ext-workflow-node-menu-action") is True]
            if log.isEnabledFor(logging.DEBUG):
                ac = QAction(
                    self.tr("Show settings"), widget,
                    objectName="show-settings",
                    toolTip=self.tr("Show widget settings"),
                )
                ac.setData(node)
                ac.triggered.connect(self.__dump_settings)
                actions.append(ac)
        return super().actions_for_context_menu(node) + actions


WidgetManager = OWWidgetManager


def user_message_from_state(message_group):
    return UserMessage(
        severity=message_group.severity,
        message_id="{0.__name__}.{0.__qualname__}".format(type(message_group)),
        contents="<br/>".join(msg.formatted
                              for msg in message_group.active) or None,
        data={"content-type": "text/html"})


class WidgetsSignalManager(SignalManager):
    """
    A signal manager for a WidgetsScheme.
    """
    def __init__(self, scheme, **kwargs):
        super().__init__(scheme, **kwargs)

    def send(self, widget, channelname, value, signal_id):
        # type: (OWBaseWidget, str, Any, Any) -> None
        """
        send method compatible with OWBaseWidget.
        """
        scheme = self.scheme()
        node = scheme.widget_manager.node_for_widget(widget)
        if node is None:
            # The Node/Widget was already removed from the scheme.
            log.debug("Node for '%s' (%s.%s) is not in the scheme.",
                      widget.captionTitle,
                      type(widget).__module__, type(widget).__name__)
            return

        try:
            channel = node.output_channel(channelname)
        except ValueError:
            log.error("%r is not valid signal name for %r",
                      channelname, node.description.name)
            return

        # Expand the signal_id with the unique widget id and the
        # channel name. This is needed for OWBaseWidget's input
        # handlers with Multiple flag.
        signal_id = (widget.widget_id, channelname, signal_id)

        super().send(node, channel, value, signal_id)

    @overload
    def invalidate(self, widget: OWBaseWidget, channel: str) -> None: ...

    @overload
    def invalidate(self, node: SchemeNode, channel: OutputSignal) -> None: ...

    def invalidate(self, node, channel):
        """Reimplemented from `SignalManager`"""
        if not isinstance(node, SchemeNode):
            scheme = self.scheme()
            node = scheme.widget_manager.node_for_widget(node)
            channel = node.output_channel(channel)
        super().invalidate(node, channel)

    def is_invalidated(self, node: SchemeNode) -> bool:
        """Reimplemented from `SignalManager`"""
        rval = super().is_invalidated(node)
        state = self.scheme().widget_manager.node_processing_state(node)
        return rval or state & (
                ProcessingState.BlockingUpdate |
                ProcessingState.Initializing
        )

    def is_ready(self, node: SchemeNode) -> bool:
        """Reimplemented from `SignalManager`"""
        rval = super().is_ready(node)
        state = self.scheme().widget_manager.node_processing_state(node)
        return rval and not state & (
            ProcessingState.InputUpdate |
            ProcessingState.Initializing
        )

    def send_to_node(self, node, signals):
        """
        Implementation of `SignalManager.send_to_node`.

        Deliver input signals to an OWBaseWidget instance.
        """
        scheme = self.scheme()
        assert scheme is not None
        widget = scheme.widget_for_node(node)
        if widget is None:
            return
        # `signals` are in the order they were 'enqueued' for delivery.
        # Reorder them to match the order of links in the model.
        _order = {
            l: i for i, l in enumerate(scheme.find_links(sink_node=node))
        }

        def order(signal: Signal) -> int:
            # if link is not in the workflow we are processing the final
            # 'reset' (None) delivery for a removed connection.
            return _order.get(signal.link, -1)
        signals = sorted(signals, key=order)
        self.process_signals_for_widget(node, widget, signals)

    def compress_signals(self, signals):
        """
        Reimplemented from :func:`SignalManager.compress_signals`.
        """
        return compress_signals(signals)

    def process_signals_for_widget(self, node, widget, signals):
        # type: (SchemeNode, OWBaseWidget, List[Signal]) -> None
        """
        Process new signals for the OWBaseWidget.
        """
        workflow = self.workflow()
        process_signals_for_widget(widget, signals, workflow)


def get_input_meta(widget: OWBaseWidget, name: str) -> Optional[Input]:
    def as_input(obj):
        if isinstance(obj, Input):
            return obj
        elif isinstance(obj, InputSignal):
            rval = Input(obj.name, obj.type, obj.id, obj.doc, obj.replaces,
                         multiple=not obj.single, default=obj.default,
                         explicit=obj.explicit)
            rval.handler = obj.handler
            return rval
        elif isinstance(obj, tuple):
            return as_input(InputSignal(*obj))
        else:
            raise TypeError

    inputs: Iterable[Input] = map(as_input, widget.get_signals("inputs"))
    for input_ in inputs:
        if input_.name == name:
            return input_
    return None


@singledispatch
def process_signals_for_widget(widget, signals, workflow):
    # type: (OWBaseWidget, List[Signal], Scheme) -> None
    """
    Process new signals for the OWBaseWidget.
    """
    for signal in signals:
        link = signal.link
        input_meta = get_input_meta(widget, link.sink_channel.name)
        assert input_meta is not None
        value = signal.value
        handler = input_meta.handler
        handler = getattr(widget, handler)

        if link not in workflow.links:
            value = input_meta.closing_sentinel

        if input_meta.single:
            args = (value,)
        else:
            args = (value, signal.id)
        log.debug("Process signals: calling %s.%s (from %s with id:%s)",
                  type(widget).__name__, handler.__name__, link, signal.id)
        try:
            handler(*args)
        except Exception:
            log.exception("Error calling '%s' of '%s'",
                          handler.__name__, widget.captionTitle)
            raise
    try:
        widget.handleNewSignals()
    except Exception:
        log.exception("Error calling 'handleNewSignals()' of '%s'",
                      widget.captionTitle)
        raise

