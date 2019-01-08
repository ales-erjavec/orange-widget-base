import warnings

from typing import Optional

from AnyQt.QtCore import (
    Qt, QEvent, QObject, QAbstractItemModel, QSortFilterProxyModel,
    QModelIndex, QSize, QRect, QPoint, QMargins, QElapsedTimer,
    QTimer
)
from AnyQt.QtGui import QMouseEvent, QKeyEvent, QPainter, QPalette, QPen
from AnyQt.QtWidgets import (
    QWidget, QComboBox, QLineEdit, QAbstractItemView, QListView,
    QStyleOptionComboBox, QStyleOptionViewItem, QStyle, QStylePainter,
    QStyledItemDelegate, QApplication
)


class ComboBox(QComboBox):
    """
    A QComboBox subclass extended to support bounded contents width hint.

    Prefer to use this class in place of plain QComboBox when the used
    model will possibly contain many items.
    """
    def __init__(self, parent=None, maximumContentsLength=-1, **kwargs):
        # Forward-declared for sizeHint()
        self.__maximumContentsLength = maximumContentsLength
        super().__init__(parent, **kwargs)

        self.__in_mousePressEvent = False
        # Yet Another Mouse Release Ignore Timer
        self.__yamrit = QTimer(self, singleShot=True)

        view = self.view()
        # optimization for displaying large models
        if isinstance(view, QListView):
            view.setUniformItemSizes(True)
        view.viewport().installEventFilter(self)

    def setMaximumContentsLength(self, length):  # type: (int) -> None
        """
        Set the maximum contents length hint.

        The hint specifies the upper bound on the `sizeHint` and
        `minimumSizeHint` width specified in character length.
        Set to 0 or negative value to disable.

        Note
        ----
        This property does not affect the widget's `maximumSize`.
        The widget can still grow depending on its `sizePolicy`.

        Parameters
        ----------
        length : int
            Maximum contents length hint.
        """
        if self.__maximumContentsLength != length:
            self.__maximumContentsLength = length
            self.updateGeometry()

    def maximumContentsLength(self):  # type: () -> int
        """
        Return the maximum contents length hint.
        """
        return self.__maximumContentsLength

    def sizeHint(self):  # type: () -> QSize
        # reimplemented
        sh = super().sizeHint()
        if self.__maximumContentsLength > 0:
            width = (self.fontMetrics().width("X") * self.__maximumContentsLength
                     + self.iconSize().width() + 4)
            sh = sh.boundedTo(QSize(width, sh.height()))
        return sh

    def minimumSizeHint(self):  # type: () -> QSize
        # reimplemented
        sh = super().minimumSizeHint()
        if self.__maximumContentsLength > 0:
            width = (self.fontMetrics().width("X") * self.__maximumContentsLength
                     + self.iconSize().width() + 4)
            sh = sh.boundedTo(QSize(width, sh.height()))
        return sh

    # workaround for QTBUG-67583
    def mousePressEvent(self, event):  # type: (QMouseEvent) -> None
        # reimplemented
        self.__in_mousePressEvent = True
        super().mousePressEvent(event)
        self.__in_mousePressEvent = False

    def showPopup(self):  # type: () -> None
        # reimplemented
        super().showPopup()
        if self.__in_mousePressEvent:
            self.__yamrit.start(QApplication.doubleClickInterval())

    def eventFilter(self, obj, event):
        # type: (QObject, QEvent) -> bool
        if event.type() == QEvent.MouseButtonRelease \
                and event.button() == Qt.LeftButton \
                and obj is self.view().viewport() \
                and self.__yamrit.isActive():
            return True
        else:
            return super().eventFilter(obj, event)


class _ComboBoxListDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        # type: (QPainter, QStyleOptionViewItem, QModelIndex) -> None
        super().paint(painter, option, index)
        if index.data(Qt.AccessibleDescriptionRole) == "separator":
            palette = option.palette  # type: QPalette
            brush = palette.brush(QPalette.Disabled, QPalette.Foreground)
            painter.setPen(QPen(brush, 1.0))
            rect = option.rect  # type: QRect
            y = rect.center().y()
            painter.drawLine(rect.left(), y, rect.left() + rect.width(), y)


class ComboBoxSearch(QComboBox):
    """
    A drop down list combo box with filter/search.

    The popup list view is filtered by text entered in the filter field.

    Note
    ----
    `popup`, `lineEdit` and `completer` from the base QComboBox class are
    unused. Setting/modifying them will have no effect.
    """
    # NOTE: Setting editable + QComboBox.NoInsert policy + ... did not achieve
    # the same results.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__searchline = QLineEdit(self, visible=False, frame=False)
        self.__searchline.setAttribute(Qt.WA_MacShowFocusRect, False)
        self.__searchline.setFocusProxy(self)
        self.__popup = None  # type: Optional[QAbstractItemModel]
        self.__proxy = None  # type: Optional[QSortFilterProxyModel]
        self.__popupTimer = QElapsedTimer()
        self.setFocusPolicy(Qt.ClickFocus | Qt.TabFocus)

    def showPopup(self):
        # type: () -> None
        """
        Reimplemented from QComboBox.showPopup

        Popup up a customized view and filter edit line.

        Note
        ----
        The .popup(), .lineEdit(), .completer() of the base class are not used.
        """
        if self.__popup is not None:
            # We have user entered state that cannot be disturbed
            # (entered filter text, scroll offset, ...)
            return  # pragma: no cover

        if self.count() == 0:
            return

        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        popup = QListView(
            uniformItemSizes=True,
            horizontalScrollBarPolicy=Qt.ScrollBarAlwaysOff,
            verticalScrollBarPolicy=Qt.ScrollBarAsNeeded,
            iconSize=self.iconSize(),
        )
        popup.setFocusProxy(self.__searchline)
        popup.setParent(self, Qt.Popup | Qt.FramelessWindowHint)
        popup.setItemDelegate(_ComboBoxListDelegate(popup))
        proxy = QSortFilterProxyModel(
            popup, filterCaseSensitivity=Qt.CaseInsensitive
        )
        proxy.setFilterKeyColumn(self.modelColumn())
        proxy.setSourceModel(self.model())
        popup.setModel(proxy)
        root = proxy.mapFromSource(self.rootModelIndex())
        popup.setRootIndex(root)

        self.__popup = popup
        self.__proxy = proxy
        self.__searchline.setText("")
        self.__searchline.setPlaceholderText("Filter...")
        self.__searchline.setVisible(True)
        self.__searchline.textEdited.connect(proxy.setFilterFixedString)

        style = self.style()  # type: QStyle

        popuprect_origin = style.subControlRect(
            QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxListBoxPopup, self
        )  # type: QRect
        popuprect_origin = QRect(
            self.mapToGlobal(popuprect_origin.topLeft()),
            popuprect_origin.size()
        )
        editrect = style.subControlRect(
            QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxEditField, self
        )  # type: QRect
        self.__searchline.setGeometry(editrect)
        desktop = QApplication.desktop()
        screenrect = desktop.availableGeometry(self)  # type: QRect

        # get the height for the view
        listrect = QRect()
        for i in range(min(proxy.rowCount(root), self.maxVisibleItems())):
            index = proxy.index(i, self.modelColumn(), root)
            if index.isValid():
                listrect = listrect.united(popup.visualRect(index))
            if listrect.height() >= screenrect.height():
                break
        window = popup.window()  # type: QWidget
        window.ensurePolished()
        if window.layout() is not None:
            window.layout().activate()
        else:
            QApplication.sendEvent(window, QEvent(QEvent.LayoutRequest))

        margins = qwidget_margin_within(popup.viewport(), window)
        height = (listrect.height() + 2 * popup.spacing() +
                  margins.top() + margins.bottom())

        popup_size = (QSize(popuprect_origin.width(), height)
                      .expandedTo(window.minimumSize())
                      .boundedTo(window.maximumSize())
                      .boundedTo(screenrect.size()))
        popuprect = dropdown_popup_geometry(
            popup_size, popuprect_origin, screenrect)
        popup.setGeometry(popuprect)

        current = proxy.mapFromSource(
            self.model().index(self.currentIndex(), self.modelColumn(),
                               self.rootModelIndex()))
        popup.setCurrentIndex(current)
        popup.scrollTo(current, QAbstractItemView.EnsureVisible)
        popup.show()
        popup.setFocus(Qt.PopupFocusReason)
        popup.installEventFilter(self)
        popup.viewport().installEventFilter(self)
        popup.viewport().setMouseTracking(True)
        self.update()
        self.__popupTimer.restart()

    def hidePopup(self):
        """Reimplemented"""
        if self.__popup is not None:
            popup = self.__popup
            self.__popup = self.__proxy = None
            popup.setFocusProxy(None)
            popup.hide()
            popup.deleteLater()
            popup.removeEventFilter(self)
            popup.viewport().removeEventFilter(self)

        # need to call base hidePopup even though the base showPopup was not
        # called (update internal state wrt. 'pressed' arrow, ...)
        super().hidePopup()
        self.__searchline.hide()
        self.update()

    def initStyleOption(self, option):
        # type: (QStyleOptionComboBox) -> None
        super().initStyleOption(option)
        option.editable = True

    def __updateGeometries(self):
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        editarea = self.style().subControlRect(
            QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxEditField, self)
        self.__searchline.setGeometry(editarea)

    def resizeEvent(self, event):
        """Reimplemented."""
        super().resizeEvent(event)
        self.__updateGeometries()

    def paintEvent(self, event):
        """Reimplemented."""
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.CC_ComboBox, opt)
        if not self.__searchline.isVisibleTo(self):
            opt.editable = False
            painter.drawControl(QStyle.CE_ComboBoxLabel, opt)

    def eventFilter(self, obj, event):  # pylint: disable=too-many-branches
        # type: (QObject, QEvent) -> bool
        """Reimplemented."""
        etype = event.type()
        if etype == QEvent.FocusOut and self.__popup is not None:
            self.hidePopup()
            return True
        if etype == QEvent.Hide and self.__popup is not None:
            self.hidePopup()
            return False

        if etype == QEvent.KeyPress or etype == QEvent.KeyRelease or \
                etype == QEvent.ShortcutOverride and obj is self.__popup:
            event = event  # type: QKeyEvent
            key, modifiers = event.key(), event.modifiers()
            if key in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Select):
                current = self.__popup.currentIndex()
                if current.isValid():
                    self.__activateProxyIndex(current)
            elif key in (Qt.Key_Up, Qt.Key_Down,
                         Qt.Key_PageUp, Qt.Key_PageDown):
                return False  #
            elif key in (Qt.Key_Tab, Qt.Key_Backtab):
                pass
            elif key == Qt.Key_Escape or \
                    (key == Qt.Key_F4 and modifiers & Qt.AltModifier):
                self.__popup.hide()
                return True
            else:
                # pass the input events to the filter edit line (no propagation
                # up the parent chain).
                self.__searchline.event(event)
                if event.isAccepted():
                    return True
        if etype == QEvent.MouseButtonRelease and self.__popup is not None \
                and obj is self.__popup.viewport() \
                and self.__popupTimer.elapsed() >= \
                    QApplication.doubleClickInterval():
            event = event  # type: QMouseEvent
            index = self.__popup.indexAt(event.pos())
            if index.isValid():
                self.__activateProxyIndex(index)

        if etype == QEvent.MouseMove and self.__popup is not None \
                and obj is self.__popup.viewport():
            event = event  # type: QMouseEvent
            opt = QStyleOptionComboBox()
            self.initStyleOption(opt)
            style = self.style()  # type: QStyle
            if style.styleHint(QStyle.SH_ComboBox_ListMouseTracking, opt, self):
                index = self.__popup.indexAt(event.pos())
                if index.isValid() and \
                        index.flags() & (Qt.ItemIsEnabled | Qt.ItemIsSelectable):
                    self.__popup.setCurrentIndex(index)

        if etype == QEvent.MouseButtonPress and self.__popup is obj:
            # Popup border or out of window mouse button press/release.
            # At least on windows this needs to be handled.
            style = self.style()
            opt = QStyleOptionComboBox()
            self.initStyleOption(opt)
            opt.subControls = QStyle.SC_All
            opt.activeSubControls = QStyle.SC_ComboBoxArrow
            pos = self.mapFromGlobal(event.globalPos())
            sc = style.hitTestComplexControl(QStyle.CC_ComboBox, opt, pos, self)
            if sc != QStyle.SC_None:
                self.__popup.setAttribute(Qt.WA_NoMouseReplay)
            self.hidePopup()

        return super().eventFilter(obj, event)

    def __activateProxyIndex(self, index):
        # type: (QModelIndex) -> None
        # Set current and activate the source index corresponding to the proxy
        # index in the popup's model.
        if self.__popup is not None and index.isValid():
            proxy = self.__popup.model()
            assert index.model() is proxy
            index = proxy.mapToSource(index)
            assert index.model() is self.model()
            if index.isValid() and \
                    index.flags() & (Qt.ItemIsEnabled | Qt.ItemIsSelectable):
                self.hidePopup()
                text = self.itemText(index.row())
                self.setCurrentIndex(index.row())
                self.activated[int].emit(index.row())
                self.activated[str].emit(text)


def qwidget_margin_within(widget, ancestor):
    # type: (QWidget, QWidget) -> QMargins
    """
    Return the 'margins' of widget within its 'ancestor'

    Ancestor must be within the widget's parent hierarchy and both widgets must
    share the same top level window.

    Parameters
    ----------
    widget : QWidget
    ancestor : QWidget

    Returns
    -------
    margins: QMargins
    """
    assert ancestor.isAncestorOf(widget)
    assert ancestor.window() is widget.window()
    r1 = widget.geometry()
    r2 = ancestor.geometry()
    topleft = r1.topLeft()
    bottomright = r1.bottomRight()
    topleft = widget.mapTo(ancestor, topleft)
    bottomright = widget.mapTo(ancestor, bottomright)
    return QMargins(topleft.x(), topleft.y(),
                    r2.right() - bottomright.x(),
                    r2.bottom() - bottomright.y())


def dropdown_popup_geometry(size, origin, screen, preferred_direction="down"):
    # type: (QRect, QRect, QRect, str) -> QRect
    """
    Move/constrain the geometry for a drop down popup.

    Parameters
    ----------
    size : QRect
        The base popup size if not constrained.
    origin : QRect
        The origin rect from which the popup extends (in screen coords.).
    screen : QRect
        The available screen geometry into which the popup must fit.
    preferred_direction : str
        'up' or 'down'

    Returns
    -------
    geometry: QRect
        Constrained drop down list geometry to fit into screen
    """
    if isinstance(size, QRect):
        warnings.warn(
            "Deprecated, pass QSize ",
            DeprecationWarning, stacklevel=2
        )
        size = size.size()

    if preferred_direction == "down":
        # if the popup  geometry extends bellow the screen and there is more
        # room above the popup origin ...
        geometry = QRect(origin.bottomLeft() + QPoint(0, 1), size)
        if geometry.bottom() > screen.bottom() \
                and origin.center().y() > screen.center().y():
            # ...flip the rect about the origin so it extends upwards
            geometry.moveBottom(origin.top() - 1)
    elif preferred_direction == "up":
        geometry = QRect(origin.topLeft() - QPoint(0, 1 + size.height()), size)
        if geometry.top() < screen.top() \
                and origin.center().y() < screen.center().y():
            # ... flip, extend down
            geometry.moveTop(origin.bottom() - 1)

    # fixup horizontal position if it extends outside the screen
    if geometry.left() < screen.left():
        geometry.moveLeft(screen.left())
    if geometry.right() > screen.right():
        geometry.moveRight(screen.right())

    # bounded by screen geometry
    return geometry.intersected(screen)
