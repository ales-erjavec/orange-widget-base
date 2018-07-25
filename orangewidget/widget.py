import sys
import os
import types
import warnings
import textwrap
from operator import attrgetter
from math import log10

from typing import Optional, Union

from AnyQt.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QSizePolicy, QApplication, QStyle,
    QSplitter, QSplitterHandle, QPushButton, QStatusBar,
    QProgressBar, QAction, QFrame, QStyleOption, QHBoxLayout, QMenuBar, QMenu,
    QWIDGETSIZE_MAX
)
from AnyQt.QtCore import (
    Qt, QObject, QEvent, QRect, QMargins, QByteArray, QDataStream, QBuffer,
    QSettings, QUrl, QThread, pyqtSignal as Signal, QSize)
from AnyQt.QtGui import QIcon, QKeySequence, QDesktopServices, QPainter, QColor

from orangewidget import settings, gui
from orangewidget.report import Report
from orangewidget.gui import OWComponent, VerticalScrollArea
from orangewidget.io import ClipboardFormat, ImgFormat
from orangewidget.settings import SettingsHandler
from orangewidget.utils import saveplot, getdeepattr
from orangewidget.utils.progressbar import ProgressBarMixin
from orangewidget.utils.messages import (
    WidgetMessagesMixin, UnboundMsg, MessagesWidget
)
from orangewidget.utils.signals import (
    WidgetSignalsMixin, Input, Output, AttributeList,
    InputSignal, OutputSignal,
    Default, NonDefault, Single, Multiple, Dynamic, Explicit
)  # pylint: disable=unused-import
from orangewidget.utils.overlay import MessageOverlayWidget, OverlayWidget
from orangewidget.utils.buttons import SimpleButton

# Msg is imported and renamed, so widgets can import it from this module rather
# than the one with the mixin (orangewidget.utils.messages).
Msg = UnboundMsg


__all__ = [
    "OWBaseWidget", "Input", "Output", "AttributeList", "Message", "Msg",
    "StateInfo",
]


def _asmappingproxy(mapping):
    if isinstance(mapping, types.MappingProxyType):
        return mapping
    else:
        return types.MappingProxyType(mapping)


class WidgetMetaClass(type(QDialog)):
    """Meta class for widgets. If the class definition does not have a
       specific settings handler, the meta class provides a default one
       that does not handle contexts. Then it scans for any attributes
       of class settings.Setting: the setting is stored in the handler and
       the value of the attribute is replaced with the default."""

    #noinspection PyMethodParameters
    # pylint: disable=bad-classmethod-argument
    def __new__(mcs, name, bases, namespace, openclass=False, **kwargs):
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        if not cls.name: # not a widget
            return cls
        cls.convert_signals()
        cls.settingsHandler = \
            SettingsHandler.create(cls, template=cls.settingsHandler)
        return cls

    @classmethod
    # pylint: disable=bad-classmethod-argument
    def __prepare__(mcs, name, bases, metaclass=None, openclass=False,
                    **kwargs):
        namespace = super().__prepare__(mcs, name, bases, metaclass, **kwargs)
        if not openclass:
            namespace["_final_class"] = True
        return namespace


# pylint: disable=too-many-instance-attributes
class OWBaseWidget(QDialog, OWComponent, Report, ProgressBarMixin,
                   WidgetMessagesMixin, WidgetSignalsMixin,
                   metaclass=WidgetMetaClass, openclass=True):
    """
    Base widget class in an orange widget workflow.
    """

    # Global widget count
    widget_id = 0

    # Widget Meta Description
    # -----------------------

    #: Widget name (:class:`str`) as presented in the Canvas
    name = None
    short_name = None
    id = None
    category = None
    version = None
    #: Short widget description (:class:`str` optional), displayed in
    #: canvas help tooltips.
    description = ""
    #: Widget icon path relative to the defining module
    icon = "icons/Unknown.png"
    #: Widget priority used for sorting within a category
    #: (default ``sys.maxsize``).
    priority = sys.maxsize

    help = None
    help_ref = None
    url = None
    keywords = []
    background = None
    replaces = None

    #: A list of published input definitions
    inputs = []
    #: A list of published output definitions
    outputs = []

    # Default widget GUI layout settings
    # ----------------------------------

    #: Should the widget have basic layout
    #: (If this flag is false then the `want_main_area` and
    #: `want_control_area` are ignored).
    want_basic_layout = True
    #: Should the widget construct a `mainArea` (this is a resizable
    #: area to the right of the `controlArea`).
    want_main_area = True
    #: Should the widget construct a `controlArea`.
    want_control_area = True
    #: Is vertical scrolling on the widget's left side, which usually
    #: contains  the `controlArea`, allowed?
    left_side_scrolling = False
    #: Orientation of the buttonsArea box; valid only if
    #: `want_control_area` is `True`. Possible values are Qt.Horizontal,
    #: Qt.Vertical and None for no buttons area
    buttons_area_orientation = Qt.Horizontal
    #: Specify whether the default message bar widget should be created
    #: and placed into the default layout. If False then clients are
    #: responsible for displaying messages within the widget in an
    #: appropriate manner.
    want_message_bar = True
    #: Widget painted by `Save graph` button
    graph_name = None
    graph_writers = [f for f in ImgFormat.formats
                     if getattr(f, 'write_image', None)
                     and getattr(f, "EXTENSIONS", None)]

    save_position = True

    #: If false the widget will receive fixed size constraint
    #: (derived from it's layout). Use for widgets which have simple
    #: static size contents.
    resizing_enabled = True

    blockingStateChanged = Signal(bool)
    processingStateChanged = Signal(int)

    # Signals have to be class attributes and cannot be inherited,
    # say from a mixin. This has something to do with the way PyQt binds them
    progressBarValueChanged = Signal(float)
    messageActivated = Signal(Msg)
    messageDeactivated = Signal(Msg)

    settingsHandler = None
    """:type: SettingsHandler"""

    #: Version of the settings representation
    #: Subclasses should increase this number when they make breaking
    #: changes to settings representation (a settings that used to store
    #: int now stores string) and handle migrations in migrate and
    #: migrate_context settings.
    settings_version = 1

    savedWidgetGeometry = settings.Setting(None)
    controlAreaVisible = settings.Setting(True, schema_only=True)

    #: A list of advice messages (:class:`Message`) to display to the user.
    #: When a widget is first shown a message from this list is selected
    #: for display. If a user accepts (clicks 'Ok. Got it') the choice is
    #: recorded and the message is never shown again (closing the message
    #: will not mark it as seen). Messages can be displayed again by pressing
    #: Shift + F1
    #:
    #: :type: list of :class:`Message`
    UserAdviceMessages = []

    settingsAboutToBePacked = Signal()
    contextAboutToBeOpened = Signal(object)
    contextOpened = Signal()
    contextClosed = Signal()
    openVisualSettingsClicked = Signal()

    __report_action = None  # type: Optional[QAction]
    __save_image_action = None  # type: Optional[QAction]
    __reset_action = None  # type: Optional[QAction]
    __visual_settings_action = None  # type: Optional[QAction]
    __menuBar = None  # type: QMenuBar

    # pylint: disable=protected-access, access-member-before-definition
    def __new__(cls, *args, captionTitle=None, **kwargs):
        self = super().__new__(cls, None, cls.get_flags())
        QDialog.__init__(self, None, self.get_flags())
        OWComponent.__init__(self)
        WidgetMessagesMixin.__init__(self)
        WidgetSignalsMixin.__init__(self)

        stored_settings = kwargs.get('stored_settings', None)
        if self.settingsHandler:
            self.settingsHandler.initialize(self, stored_settings)

        self.signalManager = kwargs.get('signal_manager', None)
        self.__env = _asmappingproxy(kwargs.get("env", {}))

        self.graphButton = None
        self.report_button = None

        OWBaseWidget.widget_id += 1
        self.widget_id = OWBaseWidget.widget_id

        captionTitle = self.name if captionTitle is None else captionTitle

        # must be set without invoking setCaption
        self.captionTitle = captionTitle
        self.setWindowTitle(captionTitle)

        self.setFocusPolicy(Qt.StrongFocus)

        # flag indicating if the widget's position was already restored
        self.__was_restored = False
        # flag indicating the widget is still expecting the first show event.
        self.__was_shown = False

        self.__statusMessage = ""
        self.__info_ns = None  # type: Optional[StateInfo]
        self.__msgwidget = None  # type: Optional[MessageOverlayWidget]
        self.__msgchoice = 0
        self.__statusbar = None  # type: Optional[QStatusBar]
        self.__statusbar_action = None  # type: Optional[QAction]

        # this action is enabled by the canvas framework
        self.__help_action = QAction(
            "Help", self, objectName="action-help", toolTip="Show help",
            enabled=False, shortcut=QKeySequence(Qt.Key_F1)
        )
        self.__report_action = QAction(
            "Report", self, objectName="action-report",
            toolTip="Create and display a report",
            enabled=False, visible=False,
            shortcut=QKeySequence(Qt.AltModifier | Qt.Key_R)
        )
        if hasattr(self, "send_report"):
            self.__report_action.triggered.connect(self.show_report)
            self.__report_action.setEnabled(True)
            self.__report_action.setVisible(True)

        self.__save_image_action = QAction(
            "Save Image", self, objectName="action-save-image",
            toolTip="Save image",
            shortcut=QKeySequence(Qt.AltModifier | Qt.Key_S),
        )
        self.__save_image_action.triggered.connect(self.save_graph)
        self.__save_image_action.setEnabled(bool(self.graph_name))
        self.__save_image_action.setVisible(bool(self.graph_name))

        self.__reset_action = QAction(
            "Reset", self, objectName="action-reset-settings",
            toolTip="Reset settings to default state",
            enabled=False, visible=False,
        )
        if hasattr(self, "reset_settings"):
            self.__reset_action.triggered.connect(self.reset_settings)
            self.__reset_action.setEnabled(True)
            self.__reset_action.setVisible(True)

        self.__visual_settings_action = QAction(
            "Show View Options", self, objectName="action-visual-settings",
            toolTip="Show View Options",
            enabled=False, visible=False,
        )
        self.__visual_settings_action.triggered.connect(
            self.openVisualSettingsClicked)
        if hasattr(self, "set_visual_settings"):
            self.__visual_settings_action.setEnabled(True)
            self.__visual_settings_action.setVisible(True)

        self.addAction(self.__help_action)

        self.__copy_action = QAction(
            "Copy to Clipboard", self, objectName="action-copy-to-clipboard",
            shortcut=QKeySequence.Copy, enabled=False, visible=False
        )
        self.__copy_action.triggered.connect(self.copy_to_clipboard)
        if type(self).copy_to_clipboard != OWBaseWidget.copy_to_clipboard \
                or self.graph_name is not None:
            self.__copy_action.setEnabled(True)
            self.__copy_action.setVisible(True)
            self.__copy_action.setText("Copy Image to Clipboard")

        # macOS Minimize action
        self.__minimize_action = QAction(
            "Minimize", self, shortcut=QKeySequence(Qt.ControlModifier | Qt.Key_M)
        )
        self.__minimize_action.triggered.connect(self.showMinimized)
        # macOS Close window action
        self.__close_action = QAction(
            "Close", self, objectName="action-close-window",
            shortcut=QKeySequence(Qt.ControlModifier | Qt.Key_W)
        )
        self.__close_action.triggered.connect(self.hide)

        self.__menubar = mb = QMenuBar(self)
        fileaction = mb.addMenu(_Menu("&File", mb, objectName="menu-file"))
        fileaction.setVisible(False)
        fileaction.menu().addSeparator()
        fileaction.menu().addAction(self.__report_action)
        fileaction.menu().addAction(self.__save_image_action)
        editaction = mb.addMenu(_Menu("&Edit", mb, objectName="menu-edit"))
        editaction.setVisible(False)

        editaction.menu().addAction(self.__copy_action)
        viewaction = mb.addMenu(_Menu("&View", mb, objectName="menu-view"))
        viewaction.setVisible(False)
        windowaction = mb.addMenu(_Menu("&Window", mb, objectName="menu-window"))
        windowaction.setVisible(False)

        if sys.platform == "darwin":
            windowaction.menu().addAction(self.__close_action)
            windowaction.menu().addAction(self.__minimize_action)
            windowaction.menu().addSeparator()

        helpaction = mb.addMenu(_Menu("&Help", mb, objectName="help-menu"))
        helpaction.menu().addAction(self.__help_action)

        self.left_side = None
        self.controlArea = self.mainArea = self.buttonsArea = None

        self.__splitter = None
        if self.want_basic_layout:
            self.set_basic_layout()
            self.layout().setMenuBar(mb)

        self.__quick_help_action = QAction(
            "Quick Help Tip", self, objectName="action-quick-help-tip",
            shortcut=QKeySequence(Qt.ShiftModifier | Qt.Key_F1)
        )
        self.__quick_help_action.setEnabled(bool(self.UserAdviceMessages))
        self.__quick_help_action.setVisible(bool(self.UserAdviceMessages))
        self.__quick_help_action.triggered.connect(self.__quicktip)
        helpaction.menu().addAction(self.__quick_help_action)

        if self.__splitter is not None and self.__splitter.count() > 1:
            action = QAction(
                "Show Control Area", self, objectName="action-show-control-area",
                shortcut=QKeySequence(Qt.ControlModifier | Qt.ShiftModifier |
                                      Qt.Key_D),
                checkable=True,
            )
            action.setChecked(True)
            action.triggered[bool].connect(self.__setControlAreaVisible)
            self.__splitter.handleClicked.connect(self.__toggleControlArea)
            viewaction.menu().addAction(action)

        if self.controlArea is not None:
            # Otherwise, the first control has focus
            self.controlArea.setFocus(Qt.OtherFocusReason)

        return self

    def menuBar(self) -> QMenuBar:
        return self.__menubar

    # pylint: disable=super-init-not-called
    def __init__(self, *args, **kwargs):
        """__init__s are called in __new__; don't call them from here"""

    def __init_subclass__(cls, **_):
        for base in cls.__bases__:
            if hasattr(base, "_final_class"):
                warnings.warn(
                    "subclassing of widget classes is deprecated and will be "
                    "disabled in the future.\n"
                    f"Extract code from {base.__name__} or explicitly open it.",
                    RuntimeWarning)
                # raise TypeError(f"class {base.__name__} cannot be subclassed")

    @classmethod
    def get_widget_description(cls):
        if not cls.name:
            return None
        properties = {name: getattr(cls, name) for name in
                      ("name", "icon", "description", "priority", "keywords",
                       "help", "help_ref", "url",
                       "version", "background", "replaces", "short_name")}
        properties["id"] = cls.id or cls.__module__
        properties["inputs"] = cls.get_signals("inputs")
        properties["outputs"] = cls.get_signals("outputs")
        properties["qualified_name"] = f"{cls.__module__}.{cls.__name__}"
        return properties

    @classmethod
    def get_flags(cls):
        return (Qt.Window if cls.resizing_enabled
                else Qt.Dialog | Qt.MSWindowsFixedSizeDialogHint)

    class _Splitter(QSplitter):
        handleClicked = Signal()

        def __init__(self, *args, **kwargs):
            super().__init__(*args, *kwargs)
            self.setHandleWidth(8)

        def _adjusted_size(self, size_method):
            size = size_method(super())()
            parent = self.parentWidget()
            if isinstance(parent, OWBaseWidget) \
                    and not parent.controlAreaVisible \
                    and self.count() > 1:
                indices = range(1, self.count())
            else:
                indices = range(0, self.count())

            height = max((size_method(self.widget(i))().height()
                          for i in indices),
                         default=0)
            size.setHeight(height)
            return size

        def sizeHint(self):
            return self._adjusted_size(attrgetter("sizeHint"))

        def minimumSizeHint(self):
            return self._adjusted_size(attrgetter("minimumSizeHint"))

        def setSizes(self, sizes):
            super().setSizes(sizes)
            if len(sizes) == 2:
                self.handle(1).setControlAreaShown(bool(sizes[0]))

        def createHandle(self):
            """Create splitter handle"""
            return self._Handle(
                self.orientation(), self, cursor=Qt.PointingHandCursor)

        class _Handle(QSplitterHandle):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.controlAreaShown = True

            def setControlAreaShown(self, shown):
                self.controlAreaShown = shown
                self.update()

            def paintEvent(self, event):
                super(QSplitterHandle, self).paintEvent(event)
                painter = QPainter(self)
                painter.setPen(QColor(160, 160, 160))
                w = self.width() - 2
                if self.controlAreaShown:
                    x0, x1 = 2, w
                else:
                    x0, x1 = w, 2
                y = self.height() / 2
                h = 0.75 * w
                painter.setRenderHint(painter.Antialiasing)
                painter.drawLine(x0, y - h, x1, y)
                painter.drawLine(x1, y, x0, y + h)

            def mouseReleaseEvent(self, event):
                """Resize on left button"""
                if event.button() == Qt.LeftButton:
                    self.splitter().handleClicked.emit()
                super().mouseReleaseEvent(event)

            def mouseMoveEvent(self, event):
                """Prevent moving; just show/hide"""
                return

    def _insert_splitter(self):
        self.__splitter = self._Splitter(Qt.Horizontal, self)
        self.layout().addWidget(self.__splitter)

    class _HiddenVerticalScrollArea(VerticalScrollArea):

        def __init__(self, parent):
            super().__init__(parent)
            self.setFrameShape(QFrame.NoFrame)

        def sizeHint(self):
            if self.widget():
                return self.widget().sizeHint()
            else:
                return super().sizeHint()

        def minimumSizeHint(self):
            if self.widget():
                min_height = min(self.widget().minimumSizeHint().height(), 300)
                return QSize(self.widget().minimumSizeHint().width(), min_height)
            else:
                return super().minimumSizeHint()

    def _insert_control_area(self):
        if self.left_side_scrolling:
            scroll_area = self._HiddenVerticalScrollArea(self)
            self.__splitter.addWidget(scroll_area)
            self.left_side = gui.vBox(scroll_area, spacing=0)
            scroll_area.setWidget(self.left_side)
        else:
            self.left_side = gui.vBox(self.__splitter, spacing=0)
        self.__splitter.setSizes([1])  # Smallest size allowed by policy
        if self.buttons_area_orientation is not None:
            self.controlArea = gui.vBox(self.left_side, addSpace=0)
            self._insert_buttons_area()
        else:
            self.controlArea = self.left_side
        if self.want_main_area:
            self.controlArea.setSizePolicy(
                QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)
            m = 0
        else:
            m = 4
        self.controlArea.layout().setContentsMargins(m, m, m, m)

    def _insert_buttons_area(self):
        self.buttonsArea = gui.widgetBox(
            self.left_side, addSpace=0, spacing=9,
            orientation=self.buttons_area_orientation)

    def _insert_main_area(self):
        self.mainArea = gui.vBox(
            self.__splitter, margin=4,
            sizePolicy=QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        )
        self.__splitter.addWidget(self.mainArea)
        self.__splitter.setCollapsible(
            self.__splitter.indexOf(self.mainArea), False)
        self.mainArea.layout().setContentsMargins(
            0 if self.want_control_area else 4, 4, 4, 4)

    def _create_default_buttons(self):
        # These buttons are inserted in buttons_area, if it exists
        # Otherwise it is up to the widget to add them to some layout
        if self.graph_name is not None:
            self.graphButton = QPushButton("&Save Image", autoDefault=False)
            self.graphButton.clicked.connect(self.save_graph)
        if hasattr(self, "send_report"):
            self.report_button = QPushButton("&Report", autoDefault=False)
            self.report_button.clicked.connect(self.show_report)

    def set_basic_layout(self):
        """Provide the basic widget layout

        Which parts are created is regulated by class attributes
        `want_main_area`, `want_control_area`, `want_message_bar` and
        `buttons_area_orientation`, the presence of method `send_report`
        and attribute `graph_name`.
        """
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(2, 2, 2, 2)

        if not self.resizing_enabled:
            self.layout().setSizeConstraint(QVBoxLayout.SetFixedSize)

        self.want_main_area = self.want_main_area or self.graph_name
        self._create_default_buttons()

        self._insert_splitter()
        if self.want_control_area:
            self._insert_control_area()
        if self.want_main_area:
            self._insert_main_area()

        if self.want_message_bar:
            # statusBar() handles 'want_message_bar', 'send_report'
            # 'graph_name' ...
            _ = self.statusBar()

    __progressBar = None  # type: Optional[QProgressBar]
    __statusbar = None    # type: Optional[QStatusBar]
    __statusbar_action = None  # type: Optional[QAction]

    def statusBar(self):
        # type: () -> QStatusBar
        """
        Return the widget's status bar.

        The status bar can be hidden/shown (`self.statusBar().setVisible()`).

        Note
        ----
        The status bar takes control of the widget's bottom margin
        (`contentsMargins`) to layout itself in the OWBaseWidget.
        """
        statusbar = self.__statusbar

        if statusbar is None:
            # Use a OverlayWidget for status bar positioning.
            c = OverlayWidget(self, alignment=Qt.AlignBottom)
            c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            c.setWidget(self)
            c.setLayout(QVBoxLayout())
            c.layout().setContentsMargins(0, 0, 0, 0)
            self.__statusbar = statusbar = _StatusBar(
                c, objectName="owwidget-status-bar"
            )
            statusbar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)

            if self.resizing_enabled:
                statusbar.setSizeGripEnabled(True)
            else:
                statusbar.setSizeGripEnabled(False)
                statusbar.setContentsMargins(0, 0, 7, 0)

            statusbar.ensurePolished()
            c.layout().addWidget(statusbar)

            # Reserve the bottom margins for the status bar
            margins = self.contentsMargins()
            margins.setBottom(statusbar.sizeHint().height())
            self.setContentsMargins(margins)
            statusbar.change.connect(self.__updateStatusBarOnChange)

            # Toggle status bar visibility. This action is not visible and
            # enabled by default. Client classes can inspect self.actions
            # and enable it if necessary.
            self.__statusbar_action = statusbar_action = QAction(
                "Show Status Bar", self, objectName="action-show-status-bar",
                toolTip="Show status bar", checkable=True,
                shortcut=QKeySequence(
                    Qt.ShiftModifier | Qt.ControlModifier | Qt.Key_Backslash)
            )
            if self.want_message_bar:
                self.message_bar = MessagesWidget(
                    defaultStyleSheet=(
                        "div.field-text { white-space: pre; }\n"
                        "div.field-detailed-text {\n"
                        "    margin-top: 0.5em; margin-bottom: 0.5em; \n"
                        "    margin-left: 1em; margin-right: 1em;\n"
                        "}"
                    ),
                    elideText=True,
                    sizePolicy=QSizePolicy(QSizePolicy.Preferred,
                                           QSizePolicy.Preferred),
                    visible=False
                )
            self.__progressBar = pb = QProgressBar(
                maximumWidth=120, minimum=0, maximum=100
            )
            pb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Ignored)
            pb.setAttribute(Qt.WA_LayoutUsesWidgetRect)
            pb.setAttribute(Qt.WA_MacMiniSize)
            pb.hide()
            self.processingStateChanged.connect(self.__processingStateChanged)
            self.blockingStateChanged.connect(self.__processingStateChanged)
            self.progressBarValueChanged.connect(lambda v: pb.setValue(int(v)))

            statusbar.addPermanentWidget(pb)
            if self.message_bar is not None:
                statusbar.addPermanentWidget(self.message_bar)

            statusbar_action.toggled[bool].connect(statusbar.setVisible)
            self.addAction(statusbar_action)

            # reserve buttons and in_out_msg areas
            def hlayout(spacing, left=0, right=0, ):
                lay = QHBoxLayout(spacing=spacing)
                lay.setContentsMargins(left, 0, right, 0)
                return lay

            buttons = QWidget(statusbar, objectName="buttons", visible=False)
            buttons.setLayout(hlayout(5, 7))
            buttonsLayout = buttons.layout()
            simple_button = _StatusBar.simple_button
            icon = _status_bar_icon
            if self.__help_action is not None:
                b = simple_button(buttons, self.__help_action, icon("help"))
                buttonsLayout.addWidget(b)
            if self.__save_image_action is not None:
                b = simple_button(buttons, self.__save_image_action, icon("chart"))
                buttonsLayout.addWidget(b)
            if self.__report_action is not None:
                b = simple_button(buttons, self.__report_action, icon("report"))
                buttonsLayout.addWidget(b)
            if self.__reset_action is not None:
                b = simple_button(buttons, self.__reset_action, icon("reset"))
                buttonsLayout.addWidget(b)
            if self.__visual_settings_action is not None:
                b = simple_button(buttons, self.__visual_settings_action, icon("visual-settings"))
                buttonsLayout.addWidget(b)

            if buttonsLayout.count():
                buttons.setVisible(True)

            in_out_msg = QWidget(objectName="in-out-msg", visible=False)
            in_out_msg.setLayout(hlayout(5, left=5))
            statusbar.addWidget(buttons)
            statusbar.addWidget(in_out_msg)

            # Ensure the status bar and the message widget are visible on
            # warning and errors.
            def message_activated(msg):
                # type: (Msg) -> None
                if msg.group.severity >= 1:
                    statusbar.setVisible(True)
            self.messageActivated.connect(message_activated)

            if self.__menubar is not None:
                viewm = self.findChild(QMenu, "menu-view")
                if viewm is not None:
                    viewm.addAction(statusbar_action)

        return statusbar

    def __updateStatusBarOnChange(self):
        statusbar = self.__statusbar
        visible = statusbar.isVisibleTo(self)
        if visible:
            height = statusbar.height()
        else:
            height = 0
        margins = self.contentsMargins()
        margins.setBottom(height)
        self.setContentsMargins(margins)
        self.__statusbar_action.setChecked(visible)

    def __processingStateChanged(self):
        # Update the progress bar in the widget's status bar
        pb = self.__progressBar
        if pb is None:
            return
        pb.setVisible(bool(self.processingState) or self.isBlocking())
        if self.isBlocking() and not self.processingState:
            pb.setRange(0, 0)  # indeterminate pb
        elif self.processingState:
            pb.setRange(0, 100)  # determinate pb

    __info_ns = None  # type: Optional[StateInfo]

    def __info(self):
        # Create and return the StateInfo object
        if self.__info_ns is None:
            self.__info_ns = info = StateInfo(self)
            # default css for IO summary.
            css = textwrap.dedent("""
            /* vertical row header cell */
            tr > th.field-name {
                text-align: right;
                padding-right: 0.2em;
                font-weight: bold;
            }
            dt {
                font-weight: bold;
            }
            """)

            sb = self.statusBar()
            if sb is not None:
                in_out_msg = sb.findChild(QWidget, "in-out-msg")
                assert in_out_msg is not None
                in_out_msg.setVisible(True)
                in_msg = MessagesWidget(
                    objectName="input-summary", visible=False,
                    defaultStyleSheet=css,
                    sizePolicy=QSizePolicy(QSizePolicy.Fixed,
                                           QSizePolicy.Fixed)
                )
                out_msg = MessagesWidget(
                    objectName="output-summary", visible=False,
                    defaultStyleSheet=css,
                    sizePolicy=QSizePolicy(QSizePolicy.Fixed,
                                           QSizePolicy.Fixed)
                )

                # Insert a separator if these are not the first elements
                buttons = sb.findChild(QWidget, "buttons")
                assert buttons is not None
                if buttons.layout().count() != 0:
                    sep = QFrame(frameShape=QFrame.VLine)
                    sep.setContentsMargins(0, 0, 2, 0)
                    in_out_msg.layout().addWidget(sep)

                in_out_msg.layout().addWidget(in_msg)
                in_out_msg.layout().addWidget(out_msg)
                in_out_msg.setVisible(True)

                def set_message(msgwidget, m):
                    # type: (MessagesWidget, StateInfo.Summary) -> None
                    message = MessagesWidget.Message(
                        icon=m.icon, text=m.brief, informativeText=m.details,
                        textFormat=m.format
                    )
                    msgwidget.setMessage(0, message)
                    msgwidget.setVisible(not message.isEmpty())

                info.input_summary_changed.connect(
                    lambda m: set_message(in_msg, m)
                )
                info.output_summary_changed.connect(
                    lambda m: set_message(out_msg, m)
                )
        else:
            info = self.__info_ns
        return info

    @property
    def info(self):
        # type: () -> StateInfo
        """
        A namespace for reporting I/O, state ... related messages.

        .. versionadded:: 3.19

        Returns
        -------
        namespace : StateInfo
        """
        # back-compatibility; subclasses were free to assign self.info =
        # to any value. Preserve this.
        try:
            return self.__dict__["info"]
        except KeyError:
            pass
        return self.__info()

    @info.setter
    def info(self, val):
        warnings.warn(
            "'OWBaseWidget.info' is a property since 3.19 and will be made read "
            "only in v4.0.",
            DeprecationWarning, stacklevel=3
        )
        self.__dict__["info"] = val

    def __toggleControlArea(self):
        if self.__splitter is None or self.__splitter.count() < 2:
            return
        self.__setControlAreaVisible(not self.__splitter.sizes()[0])

    def __setControlAreaVisible(self, visible):
        # type: (bool) -> None
        if self.__splitter is None or self.__splitter.count() < 2:
            return
        self.controlAreaVisible = visible
        action = self.findChild(QAction, "action-show-control-area")
        if action is not None:
            action.setChecked(visible)
        splitter = self.__splitter  # type: QSplitter
        w = splitter.widget(0)
        # Set minimum width to 1 (overrides minimumSizeHint) when control area
        # is not visible to allow the main area to shrink further. Reset the
        # minimum width with a 0 if control area is visible.
        w.setMinimumWidth(int(not visible))

        sizes = splitter.sizes()
        current_size = sizes[0]
        if bool(current_size) == visible:
            return

        current_width = w.width()
        geom = self.geometry()
        frame = self.frameGeometry()
        framemargins = QMargins(
            frame.left() - geom.left(),
            frame.top() - geom.top(),
            frame.right() - geom.right(),
            frame.bottom() - geom.bottom()
        )
        splitter.setSizes([int(visible), QWIDGETSIZE_MAX])
        if not self.isWindow() or \
                self.windowState() not in [Qt.WindowNoState, Qt.WindowActive]:
            # not a window or not in state where we can move move/resize
            return

        # force immediate resize recalculation
        splitter.refresh()
        self.layout().invalidate()
        self.layout().activate()

        if visible:
            # move left and expand by the exposing widget's width
            diffx = -w.width()
            diffw = w.width()
        else:
            # move right and shrink by the collapsing width
            diffx = current_width
            diffw = -current_width
        newgeom = QRect(
            geom.x() + diffx, geom.y(), geom.width() + diffw, geom.height()
        )
        # bound/move by available geometry
        bounds = QApplication.desktop().availableGeometry(self)
        bounds = bounds.adjusted(
            framemargins.left(), framemargins.top(),
            -framemargins.right(), -framemargins.bottom()
        )
        newsize = newgeom.size().boundedTo(bounds.size())
        newgeom = QRect(newgeom.topLeft(), newsize)
        newgeom.moveLeft(max(newgeom.left(), bounds.left()))
        newgeom.moveRight(min(newgeom.right(), bounds.right()))
        self.setGeometry(newgeom)

    def save_graph(self):
        """Save the graph with the name given in class attribute `graph_name`.

        The method is called by the *Save graph* button, which is created
        automatically if the `graph_name` is defined.
        """
        graph_obj = getdeepattr(self, self.graph_name, None)
        if graph_obj is None:
            return
        saveplot.save_plot(graph_obj, self.graph_writers)

    def copy_to_clipboard(self):

        if self.graph_name:
            graph_obj = getdeepattr(self, self.graph_name, None)
            if graph_obj is None:
                return
            ClipboardFormat.write_image(None, graph_obj)

    def __restoreWidgetGeometry(self, geometry):
        # type: (bytes) -> bool
        def _fullscreen_to_maximized(geometry):
            """Don't restore windows into full screen mode because it loses
            decorations and can't be de-fullscreened at least on some platforms.
            Use Maximized state insted."""
            w = QWidget(visible=False)
            w.restoreGeometry(QByteArray(geometry))
            if w.isFullScreen():
                w.setWindowState(
                    w.windowState() & ~Qt.WindowFullScreen | Qt.WindowMaximized)
            return w.saveGeometry()

        restored = False
        if geometry:
            geometry = _fullscreen_to_maximized(geometry)
            restored = self.restoreGeometry(geometry)

        if restored and not self.windowState() & \
                (Qt.WindowMaximized | Qt.WindowFullScreen):
            space = QApplication.desktop().availableGeometry(self)
            frame, geometry = self.frameGeometry(), self.geometry()

            # Fix the widget size to fit inside the available space
            width = space.width() - (frame.width() - geometry.width())
            width = min(width, geometry.width())
            height = space.height() - (frame.height() - geometry.height())
            height = min(height, geometry.height())
            self.resize(width, height)

            # Move the widget to the center of available space if it is
            # currently outside it
            if not space.contains(self.frameGeometry()):
                x = max(0, space.width() / 2 - width / 2)
                y = max(0, space.height() / 2 - height / 2)

                self.move(x, y)
        return restored

    def __updateSavedGeometry(self):
        if self.__was_restored and self.isVisible():
            # Update the saved geometry only between explicit show/hide
            # events (i.e. changes initiated by the user not by Qt's default
            # window management).
            # Note: This should always be stored as bytes and not QByteArray.
            self.savedWidgetGeometry = bytes(self.saveGeometry())

    # when widget is resized, save the new width and height
    def resizeEvent(self, event):
        """Overloaded to save the geometry (width and height) when the widget
        is resized.
        """
        QDialog.resizeEvent(self, event)
        # Don't store geometry if the widget is not visible
        # (the widget receives a resizeEvent (with the default sizeHint)
        # before first showEvent and we must not overwrite the the
        # savedGeometry with it)
        if self.save_position and self.isVisible():
            self.__updateSavedGeometry()

    def moveEvent(self, event):
        """Overloaded to save the geometry when the widget is moved
        """
        QDialog.moveEvent(self, event)
        if self.save_position and self.isVisible():
            self.__updateSavedGeometry()

    def hideEvent(self, event):
        """Overloaded to save the geometry when the widget is hidden
        """
        if self.save_position:
            self.__updateSavedGeometry()
        QDialog.hideEvent(self, event)

    def closeEvent(self, event):
        """Overloaded to save the geometry when the widget is closed
        """
        if self.save_position and self.isVisible():
            self.__updateSavedGeometry()
        QDialog.closeEvent(self, event)

    def mousePressEvent(self, event):
        """ Flash message bar icon on mouse press """
        if self.message_bar is not None:
            self.message_bar.flashIcon()
        event.ignore()

    def setVisible(self, visible):
        # type: (bool) -> None
        """Reimplemented from `QDialog.setVisible`."""
        if visible:
            # Force cached size hint invalidation ... The size hints are
            # sometimes not properly invalidated via the splitter's layout and
            # nested left_part -> controlArea layouts. This causes bad initial
            # size when the widget is first shown.
            if self.controlArea is not None:
                self.controlArea.updateGeometry()
            if self.buttonsArea is not None:
                self.buttonsArea.updateGeometry()
            if self.mainArea is not None:
                self.mainArea.updateGeometry()
        super().setVisible(visible)

    def showEvent(self, event):
        """Overloaded to restore the geometry when the widget is shown
        """
        QDialog.showEvent(self, event)
        if self.save_position and not self.__was_restored:
            # Restore saved geometry on (first) show
            if self.__splitter is not None:
                self.__setControlAreaVisible(self.controlAreaVisible)
            if self.savedWidgetGeometry is not None:
                self.__restoreWidgetGeometry(bytes(self.savedWidgetGeometry))
            self.__was_restored = True

        if not self.__was_shown:
            # Mark as explicitly moved/resized if not already. QDialog would
            # otherwise adjust position/size on subsequent hide/show
            # (move/resize events coming from the window manager do not set
            # these flags).
            self.setAttribute(Qt.WA_Moved, True)
            self.setAttribute(Qt.WA_Resized, True)
            self.__was_shown = True
        self.__quicktipOnce()

    def setCaption(self, caption):
        # save caption title in case progressbar will change it
        self.captionTitle = str(caption)
        self.setWindowTitle(caption)

    def reshow(self):
        """Put the widget on top of all windows
        """
        self.show()
        self.raise_()
        self.activateWindow()

    def openContext(self, *a):
        """Open a new context corresponding to the given data.

        The settings handler first checks the stored context for a
        suitable match. If one is found, it becomes the current contexts and
        the widgets settings are initialized accordingly. If no suitable
        context exists, a new context is created and data is copied from
        the widget's settings into the new context.

        Widgets that have context settings must call this method after
        reinitializing the user interface (e.g. combo boxes) with the new
        data.

        The arguments given to this method are passed to the context handler.
        Their type depends upon the handler. For instance,
        `DomainContextHandler` expects `Orange.data.Table` or
        `Orange.data.Domain`.
        """
        self.contextAboutToBeOpened.emit(a)
        self.settingsHandler.open_context(self, *a)
        self.contextOpened.emit()

    def closeContext(self):
        """Save the current settings and close the current context.

        Widgets that have context settings must call this method before
        reinitializing the user interface (e.g. combo boxes) with the new
        data.
        """
        self.settingsHandler.close_context(self)
        self.contextClosed.emit()

    def retrieveSpecificSettings(self):
        """
        Retrieve data that is not registered as setting.

        This method is called by
        `orangewidget.settings.ContextHandler.settings_to_widget`.
        Widgets may define it to retrieve any data that is not stored in widget
        attributes. See :obj:`Orange.widgets.data.owcolor.OWColor` for an
        example.
        """

    def storeSpecificSettings(self):
        """
        Store data that is not registered as setting.

        This method is called by
        `orangewidget.settings.ContextHandler.settings_from_widget`.
        Widgets may define it to store any data that is not stored in widget
        attributes. See :obj:`Orange.widgets.data.owcolor.OWColor` for an
        example.
        """

    def saveSettings(self):
        """
        Writes widget instance's settings to class defaults. Usually called
        when the widget is deleted.
        """
        self.settingsHandler.update_defaults(self)

    def onDeleteWidget(self):
        """
        Invoked by the canvas to notify the widget it has been deleted
        from the workflow.

        If possible, subclasses should gracefully cancel any currently
        executing tasks.
        """

    def handleNewSignals(self):
        """
        Invoked by the workflow signal propagation manager after all
        signals handlers have been called.

        Reimplement this method in order to coalesce updates from
        multiple updated inputs.
        """

    #: Widget's status message has changed.
    statusMessageChanged = Signal(str)

    def setStatusMessage(self, text):
        """
        Set widget's status message.

        This is a short status string to be displayed inline next to
        the instantiated widget icon in the canvas.
        """
        assert QThread.currentThread() == self.thread()
        if self.__statusMessage != text:
            self.__statusMessage = text
            self.statusMessageChanged.emit(text)

    def statusMessage(self):
        """
        Return the widget's status message.
        """
        return self.__statusMessage

    def keyPressEvent(self, e):
        """Handle default key actions or pass the event to the inherited method
        """
        if (int(e.modifiers()), e.key()) in OWBaseWidget.defaultKeyActions:
            OWBaseWidget.defaultKeyActions[int(e.modifiers()), e.key()](self)
        else:
            QDialog.keyPressEvent(self, e)

    defaultKeyActions = {}

    def setBlocking(self, state=True) -> None:
        """
        Set blocking flag for this widget.

        While this flag is set this widget and all its descendants
        will not receive any new signals from the workflow signal manager.

        .. deprecated:: 4.2.0
            Setting/clearing this flag is equivalent to
            `setInvalidated(True); setReady(False)` and
            `setInvalidated(False); setReady(True)` respectively.
            Use :func:`setInvalidated` and :func:`setReady` in new code.

        .. seealso:: :func:`setInvalidated`, :func:`setReady`

        """
        if state:
            self.__setState(True, False)
        else:
            self.__setState(False, True)

    def isBlocking(self):
        """
        Is this widget blocking signal processing.
        """
        return self.isInvalidated() and not self.isReady()

    widgetStateChanged = Signal()
    blockingStateChanged = Signal(bool)
    invalidatedStateChanged = Signal(bool)
    readyStateChanged = Signal(bool)

    __invalidated = False
    __ready = True

    def setInvalidated(self, state: bool) -> None:
        """
        Set/clear invalidated flag on this widget.

        While this flag is set none of its descendants will receive new
        signals from the workflow execution manager.

        This is useful for instance if the widget does it's work in a
        separate thread or schedules processing from the event queue.
        In this case it can set the invalidated flag when starting a task.
        After the task has completed the widget can clear the flag and
        send the updated outputs.

        .. note::
            Failure to clear this flag will block dependent nodes forever.

        .. seealso:: :func:`~Output.invalidate()` for a more fine grained
           invalidation.
        """
        self.__setState(state, self.__ready)

    def isInvalidated(self) -> bool:
        """
        Return the widget's invalidated flag.
        """
        return self.__invalidated

    def setReady(self, state: bool) -> None:
        """
        Set/clear ready flag on this widget.

        While a ready flag is unset, the widget will not receive any new
        input updates from the workflow execution manager.

        By default this flag is True.
        """
        self.__setState(self.__invalidated, state)

    def isReady(self) -> bool:
        """
        Return the widget's ready state
        """
        return self.__ready

    def __setState(self, invalidated: bool, ready: bool) -> None:
        blocking = self.isBlocking()
        changed = False
        if self.__ready != ready:
            self.__ready = ready
            changed = True
            self.readyStateChanged.emit(ready)
        if self.__invalidated != invalidated:
            self.__invalidated = invalidated
            self.invalidatedStateChanged.emit(invalidated)
            changed = True
        if changed:
            self.widgetStateChanged.emit()
        if blocking != self.isBlocking():
            self.blockingStateChanged.emit(self.isBlocking())

    def workflowEnv(self):
        """
        Return (a view to) the workflow runtime environment.

        Returns
        -------
        env : types.MappingProxyType
        """
        return self.__env

    def workflowEnvChanged(self, key, value, oldvalue):
        """
        A workflow environment variable `key` has changed to value.

        Called by the canvas framework to notify widget of a change
        in the workflow runtime environment.

        The default implementation does nothing.
        """

    def saveGeometryAndLayoutState(self):
        # type: () -> QByteArray
        """
        Save the current geometry and layout state of this widget and
        child windows (if applicable).

        Returns
        -------
        state : QByteArray
            Saved state.
        """
        version = 0x1
        have_spliter = 0
        splitter_state = 0
        if self.__splitter is not None:
            have_spliter = 1
            splitter_state = 1 if self.controlAreaVisible else 0
        data = QByteArray()
        stream = QDataStream(data, QBuffer.WriteOnly)
        stream.writeUInt32(version)
        stream.writeUInt16((have_spliter << 1) | splitter_state)
        stream <<= self.saveGeometry()
        return data

    def restoreGeometryAndLayoutState(self, state):
        # type: (QByteArray) -> bool
        """
        Restore the geometry and layout of this widget to a state previously
        saved with :func:`saveGeometryAndLayoutState`.

        Parameters
        ----------
        state : QByteArray
            Saved state.

        Returns
        -------
        success : bool
            `True` if the state was successfully restored, `False` otherwise.
        """
        version = 0x1
        stream = QDataStream(state, QBuffer.ReadOnly)
        version_ = stream.readUInt32()
        if stream.status() != QDataStream.Ok or version_ != version:
            return False
        splitter_state = stream.readUInt16()
        has_spliter = splitter_state & 0x2
        splitter_state = splitter_state & 0x1
        if has_spliter and self.__splitter is not None:
            self.__setControlAreaVisible(bool(splitter_state))
        geometry = QByteArray()
        stream >>= geometry
        if stream.status() == QDataStream.Ok:
            state = self.__restoreWidgetGeometry(bytes(geometry))
            self.__was_restored = self.__was_restored or state
            return state
        else:
            return False  # pragma: no cover

    def __showMessage(self, message):
        if self.__msgwidget is not None:
            self.__msgwidget.hide()
            self.__msgwidget.deleteLater()
            self.__msgwidget = None

        if message is None:
            return

        buttons = MessageOverlayWidget.Ok | MessageOverlayWidget.Close
        if message.moreurl is not None:
            buttons |= MessageOverlayWidget.Help

        if message.icon is not None:
            icon = message.icon
        else:
            icon = Message.Information

        self.__msgwidget = MessageOverlayWidget(
            parent=self, text=message.text, icon=icon, wordWrap=True,
            standardButtons=buttons)

        btn = self.__msgwidget.button(MessageOverlayWidget.Ok)
        btn.setText("Ok, got it")

        self.__msgwidget.setStyleSheet("""
            MessageOverlayWidget {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 0, y2: 1,
                    stop:0 #666, stop:0.3 #6D6D6D, stop:1 #666)
            }
            MessageOverlayWidget QLabel#text-label {
                color: white;
            }""")

        if message.moreurl is not None:
            helpbutton = self.__msgwidget.button(MessageOverlayWidget.Help)
            helpbutton.setText("Learn more\N{HORIZONTAL ELLIPSIS}")
            self.__msgwidget.helpRequested.connect(
                lambda: QDesktopServices.openUrl(QUrl(message.moreurl)))

        self.__msgwidget.setWidget(self)
        self.__msgwidget.show()

    def __quicktip(self):
        messages = list(self.UserAdviceMessages)
        if messages:
            message = messages[self.__msgchoice % len(messages)]
            self.__msgchoice += 1
            self.__showMessage(message)

    def __quicktipOnce(self):
        dirpath = settings.widget_settings_dir(versioned=False)
        try:
            os.makedirs(dirpath, exist_ok=True)
        except OSError:  # EPERM, EEXISTS, ...
            pass

        filename = os.path.join(settings.widget_settings_dir(versioned=False),
                                "user-session-state.ini")
        namespace = ("user-message-history/{0.__module__}.{0.__qualname__}"
                     .format(type(self)))
        session_hist = QSettings(filename, QSettings.IniFormat)
        session_hist.beginGroup(namespace)
        messages = self.UserAdviceMessages

        def _ispending(msg):
            return not session_hist.value(
                "{}/confirmed".format(msg.persistent_id),
                defaultValue=False, type=bool)
        messages = [msg for msg in messages if _ispending(msg)]

        if not messages:
            return

        message = messages[self.__msgchoice % len(messages)]
        self.__msgchoice += 1

        self.__showMessage(message)

        def _userconfirmed():
            session_hist = QSettings(filename, QSettings.IniFormat)
            session_hist.beginGroup(namespace)
            session_hist.setValue(
                "{}/confirmed".format(message.persistent_id), True)
            session_hist.sync()

        self.__msgwidget.accepted.connect(_userconfirmed)

    @classmethod
    def migrate_settings(cls, settings, version):
        """Fix settings to work with the current version of widgets

        Parameters
        ----------
        settings : dict
            dict of name - value mappings
        version : Optional[int]
            version of the saved settings
            or None if settings were created before migrations
        """

    @classmethod
    def migrate_context(cls, context, version):
        """Fix contexts to work with the current version of widgets

        Parameters
        ----------
        context : Context
            Context object
        version : Optional[int]
            version of the saved context
            or None if context was created before migrations
        """


class _StatusBar(QStatusBar):
    #: Emitted on a change of geometry or visibility (explicit hide/show)
    change = Signal()

    def event(self, event):
        # type: (QEvent) ->bool
        if event.type() in {QEvent.Resize, QEvent.ShowToParent,
                            QEvent.HideToParent}:
            self.change.emit()
        return super().event(event)

    def paintEvent(self, event):
        style = self.style()
        opt = QStyleOption()
        opt.initFrom(self)
        painter = QPainter(self)
        # Omit the widget instance from the call (QTBUG-60018)
        style.drawPrimitive(QStyle.PE_PanelStatusBar, opt, painter, None)
        # Do not draw any PE_FrameStatusBarItem frames.
        painter.end()

    @staticmethod
    def simple_button(
            parent: QWidget, action: QAction, icon=QIcon()
    ) -> SimpleButton:
        if icon.isNull():
            icon = action.icon()
        button = SimpleButton(
            parent,
            icon=icon,
            toolTip=action.toolTip(), whatsThis=action.whatsThis(),
            visible=action.isVisible(), enabled=action.isEnabled(), )

        def update():
            button.setVisible(action.isVisible())
            button.setEnabled(action.isEnabled())
            button.setToolTip(action.toolTip())
            button.setWhatsThis(action.whatsThis())

        action.changed.connect(update)
        button.clicked.connect(action.triggered)
        return button


def _status_bar_icon(name):
    icon = QIcon(gui.resource_filename("icons/{}.svg".format(name)))
    icon.addFile(gui.resource_filename("icons/{}-hover.svg".format(name)),
                 mode=QIcon.Active)
    return icon


class _Menu(QMenu):
    """
    A QMenu managing self-visibility in a parent menu or menu bar.

    The menu is visible if it has at least one visible action.
    """
    def actionEvent(self, event):
        super().actionEvent(event)
        ma = self.menuAction()
        if ma is not None:
            ma.setVisible(
                any(ac.isVisible() and not ac.isSeparator()
                    for ac in self.actions())
            )


class Message:
    """
    A user message.

    :param str text: Message text
    :param str persistent_id:
        A persistent message id.
    :param icon: Message icon
    :type icon: QIcon or QStyle.StandardPixmap
    :param str moreurl:
        An url to open when a user clicks a 'Learn more' button.

    .. seealso:: :const:`OWBaseWidget.UserAdviceMessages`
    """
    #: QStyle.SP_MessageBox* pixmap enums repeated for easier access
    Question = QStyle.SP_MessageBoxQuestion
    Information = QStyle.SP_MessageBoxInformation
    Warning = QStyle.SP_MessageBoxWarning
    Critical = QStyle.SP_MessageBoxCritical

    def __init__(self, text, persistent_id, icon=None, moreurl=None):
        assert isinstance(text, str)
        assert isinstance(icon, (type(None), QIcon, QStyle.StandardPixmap))
        assert persistent_id is not None
        self.text = text
        self.icon = icon
        self.moreurl = moreurl
        self.persistent_id = persistent_id


#: Input/Output flags (deprecated).
#: --------------------------------
#:
#: The input/output is the default for its type.
#: When there are multiple IO signals with the same type the
#: one with the default flag takes precedence when adding a new
#: link in the canvas.
Default = Default
NonDefault = NonDefault
#: Single input signal (default)
Single = Single
#: Multiple outputs can be linked to this signal.
#: Signal handlers with this flag have (object, id: object) -> None signature.
Multiple = Multiple
#: Applies to user interaction only.
#: Only connected if specifically requested (in a dedicated "Links" dialog)
#: or it is the only possible connection.
Explicit = Explicit
#: Dynamic output type.
#: Specifies that the instances on the output will in general be
#: subtypes of the declared type and that the output can be connected
#: to any input signal which can accept a subtype of the declared output
#: type.
Dynamic = Dynamic


metric_suffix = ['', 'k', 'M', 'G', 'T', 'P']


class StateInfo(QObject):
    """
    A namespace for OWBaseWidget's detailed input/output/state summary reporting.

    See Also
    --------
    OWBaseWidget.info
    """
    class Summary:
        """
        Input/output summary description.

        This class is used to hold and report detailed I/O summaries.

        Attributes
        ----------
        brief: str
            A brief (inline) description.
        details: str
            A richer detailed description.
        icon: QIcon
            An custom icon. If not set a default set will be used to indicate
            special states (i.e. empty input ...)
        format: Qt.TextFormat
            Qt.PlainText if `brief` and `details` are to be rendered as plain
            text or Qt.RichText if they are HTML.

        See also
        --------
        :func:`StateInfo.set_input_summary`,
        :func:`StateInfo.set_output_summary`,
        :class:`StateInfo.Empty`,
        :class:`StateInfo.Partial`,
        `Supported HTML Subset`_

        .. _`Supported HTML Subset`:
            http://doc.qt.io/qt-5/richtext-html-subset.html

        """
        def __init__(self, brief="", details="", icon=QIcon(),
                     format=Qt.PlainText):
            # type: (str, str, QIcon, Qt.TextFormat) -> None
            super().__init__()
            self.__brief = brief
            self.__details = details
            self.__icon = QIcon(icon)
            self.__format = format

        @property
        def brief(self) -> str:
            return self.__brief

        @property
        def details(self) -> str:
            return self.__details

        @property
        def icon(self) -> QIcon:
            return QIcon(self.__icon)

        @property
        def format(self) -> Qt.TextFormat:
            return self.__format

        def __eq__(self, other):
            return (isinstance(other, StateInfo.Summary) and
                    self.brief == other.brief and
                    self.details == other.details and
                    self.icon.cacheKey() == other.icon.cacheKey() and
                    self.format == other.format)

        def as_dict(self):
            return dict(brief=self.brief, details=self.details, icon=self.icon,
                        format=self.format)

        def updated(self, **kwargs):
            state = self.as_dict()
            state.update(**kwargs)
            return type(self)(**state)

        @classmethod
        def default_icon(cls, role):
            # type: (str) -> QIcon
            """
            Return a default icon for input/output role.

            Parameters
            ----------
            role: str
                "input" or "output"

            Returns
            -------
            icon: QIcon
            """
            return QIcon(gui.resource_filename("icons/{}.svg".format(role)))

    class Empty(Summary):
        """
        Input/output summary description indicating empty I/O state.
        """
        @classmethod
        def default_icon(cls, role):
            return QIcon(gui.resource_filename("icons/{}-empty.svg".format(role)))

    class Partial(Summary):
        """
        Input summary indicating partial input.

        This state indicates that some inputs are present but more are needed
        in order for the widget to proceed.
        """
        @classmethod
        def default_icon(cls, role):
            return QIcon(gui.resource_filename("icons/{}-partial.svg".format(role)))

    #: Signal emitted when the input summary changes
    input_summary_changed = Signal(Summary)
    #: Signal emitted when the output summary changes
    output_summary_changed = Signal(Summary)

    #: A default message displayed to indicate no inputs.
    NoInput = Empty()

    #: A default message displayed to indicate no output.
    NoOutput = Empty()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__input_summary = StateInfo.Summary()   # type: StateInfo.Summary
        self.__output_summary = StateInfo.Summary()  # type: StateInfo.Summary

    def set_input_summary(self, summary, details="", icon=QIcon(),
                          format=Qt.PlainText):
        # type: (Union[StateInfo.Summary, str, int, None], str, QIcon, Qt.TextFormat) -> None
        """
        Set the input summary description.

        This method has two overloads

        .. function:: set_input_summary(summary: Optional[StateInfo.Summary]])

        .. function:: set_input_summary(summary:str, detailed:str="", icon:QIcon)

        Note
        ----
        `set_input_summary(None)` clears/resets the current summary. Use
        `set_input_summary(StateInfo.NoInput)` to indicate no input state.
        `set_input_summary(int)` to have automatically formatted summary

        Parameters
        ----------
        summary : Union[Optional[StateInfo.Message], str, int]
            A populated `StateInfo.Message` instance or a short text
            description (should not exceed 16 characters) or an integer.
        details : str
            A detailed description (only applicable if summary is a string).
        icon : QIcon
            An icon. If not specified a default icon will be used (only
            applicable if `summary` is a string).
        format : Qt.TextFormat
            Specify how the `short` and `details` text should be interpreted.
            Can be `Qt.PlainText` or `Qt.RichText` (only applicable if
            `summary` is a string or an integer).
        """
        def assert_single_arg():
            if not (details == "" and icon.isNull() and format == Qt.PlainText):
                raise TypeError("No extra arguments expected when `summary` "
                                "is `None` or `Message`")

        if summary is None:
            assert_single_arg()
            summary = StateInfo.Summary()
        elif isinstance(summary, StateInfo.Summary):
            assert_single_arg()
            if isinstance(summary, StateInfo.Empty):
                summary = summary.updated(details="No data on input")
            if summary.icon.isNull():
                summary = summary.updated(icon=summary.default_icon("input"))
        elif isinstance(summary, str):
            summary = StateInfo.Summary(summary, details, icon, format=format)
            if summary.icon.isNull():
                summary = summary.updated(icon=summary.default_icon("input"))
        elif isinstance(summary, int):
            summary = StateInfo.Summary(self.format_number(summary),
                                        details or str(summary),
                                        StateInfo.Summary.default_icon("input"),
                                        format=format)
        else:
            raise TypeError("'None', 'str' or 'Message' instance expected, "
                            "got '{}'" .format(type(summary).__name__))

        if self.__input_summary != summary:
            self.__input_summary = summary
            self.input_summary_changed.emit(summary)

    def set_output_summary(self, summary, details="", icon=QIcon(),
                           format=Qt.PlainText):
        # type: (Union[StateInfo.Summary, str, int, None], str, QIcon, Qt.TextFormat) -> None
        """
        Set the output summary description.

        Note
        ----
        `set_output_summary(None)` clears/resets the current summary. Use
        `set_output_summary(StateInfo.NoOutput)` to indicate no output state.
        `set_output_summary(int)` to have automatically formatted summary

        Parameters
        ----------
        summary : Union[StateInfo.Summary, str, int, None]
            A populated `StateInfo.Summary` instance or a short text
            description (should not exceed 16 characters) or an integer.
        details : str
            A detailed description (only applicable if `summary` is a string).
        icon : QIcon
            An icon. If not specified a default icon will be used
            (only applicable if `summary` is a string)
        format : Qt.TextFormat
            Specify how the `summary` and `details` text should be interpreted.
            Can be `Qt.PlainText` or `Qt.RichText` (only applicable if
            `summary` is a string or an integer).
        """
        def assert_single_arg():
            if not (details == "" and icon.isNull() and format == Qt.PlainText):
                raise TypeError("No extra arguments expected when `summary` "
                                "is `None` or `Message`")
        if summary is None:
            assert_single_arg()
            summary = StateInfo.Summary()
        elif isinstance(summary, StateInfo.Summary):
            assert_single_arg()
            if isinstance(summary, StateInfo.Empty):
                summary = summary.updated(details="No data on output")
            if summary.icon.isNull():
                summary = summary.updated(icon=summary.default_icon("output"))
        elif isinstance(summary, str):
            summary = StateInfo.Summary(summary, details, icon, format=format)
            if summary.icon.isNull():
                summary = summary.updated(icon=summary.default_icon("output"))
        elif isinstance(summary, int):
            summary = StateInfo.Summary(self.format_number(summary),
                                        details or str(summary),
                                        StateInfo.Summary.default_icon("output"),
                                        format=format)
        else:
            raise TypeError("'None', 'str' or 'Message' instance expected, "
                            "got '{}'" .format(type(summary).__name__))

        if self.__output_summary != summary:
            self.__output_summary = summary
            self.output_summary_changed.emit(summary)

    @staticmethod
    def format_number(n: int) -> str:
        """
        Format integers larger then 9999 with metric suffix and at most 3 digits.

        Example:
            >>> StateInfo.format_number(12_345)
            '12.3k'
        """
        if n < 10_000:
            return str(n)
        mag = int(log10(n) // 3)
        n = n / 10 ** (mag * 3)
        if n >= 999.5:
            # rounding to higher order
            n = 1
            mag += 1
        return f"{n:.3g}{metric_suffix[mag]}"

# pylint: disable=too-many-lines
