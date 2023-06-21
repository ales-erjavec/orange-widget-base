from AnyQt.QtWidgets import (
    QPushButton, QAbstractButton, QStyle, QStylePainter, QStyleOptionButton,
    QStyleOption
)
from AnyQt.QtGui import QPalette, QIcon, QLinearGradient, QBrush, QPainter
from AnyQt.QtCore import Qt, QSize, QEvent


class VariableTextPushButton(QPushButton):
    """
    QPushButton subclass with an sizeHint method to better support settable
    variable width button text.

    Use this class instead of the QPushButton when the button will
    switch the text dynamically while displayed.
    """
    def __init__(self, *args, textChoiceList=[], **kwargs):
        super().__init__(*args, **kwargs)
        self.__textChoiceList = list(textChoiceList)

    def setTextChoiceList(self, textList):
        """
        Set the list of all `text` string to use for size hinting.

        Parameters
        ----------
        textList : List[str]
            A list of all different `text` properties that will/can be set on
            the push button. This list is used to derive a suitable sizeHint
            for the widget.
        """
        self.__textChoiceList = textList
        self.updateGeometry()

    def sizeHint(self):
        """
        Reimplemented from `QPushButton.sizeHint`.

        Returns
        -------
        sh : QSize
        """
        sh = super().sizeHint()
        option = QStyleOptionButton()
        self.initStyleOption(option)
        style = self.style()
        fm = option.fontMetrics
        if option.iconSize.isValid():
            icsize = option.iconSize
            icsize.setWidth(icsize.width() + 4)
        else:
            icsize = QSize()

        for text in self.__textChoiceList:
            option.text = text
            size = fm.size(Qt.TextShowMnemonic, text)

            if not icsize.isNull():
                size.setWidth(size.width() + icsize.width())
                size.setHeight(max(size.height(), icsize.height()))

            sh = sh.expandedTo(
                style.sizeFromContents(QStyle.CT_PushButton, option,
                                       size, self))
        return sh


class SimpleButton(QAbstractButton):
    """
    A simple icon button widget.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAttribute(Qt.WA_Hover)

    def sizeHint(self):
        # reimplemented
        self.ensurePolished()
        iconsize = self.iconSize()
        icon = self.icon()
        if not icon.isNull():
            iconsize = icon.actualSize(iconsize)
        margins = self.contentsMargins()
        return QSize(
            iconsize.width() + margins.left() + margins.right(),
            iconsize.height() + margins.top() + margins.bottom()
        )

    def minimumSizeHint(self):
        # reimplemented
        return self.sizeHint()

    def initStyleOption(self, option: QStyleOptionButton):
        option.initFrom(self)
        option.features |= QStyleOptionButton.Flat
        option.iconSize = self.iconSize()
        option.icon = self.icon()
        option.text = ""
        if self.isDown():
            option.state |= QStyle.State_Sunken

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        if self.isDown():
            # Use default pressed flat button look
            painter.drawPrimitive(QStyle.PE_PanelButtonBevel, option)
        elif option.state & (QStyle.State_MouseOver | QStyle.State_HasFocus):
            flat_button_hover_background(painter, option)

        if not option.icon.isNull():
            rect = option.rect.adjusted(1, 1, -1, -1)
            if option.state & QStyle.State_Active:
                mode = (QIcon.Active if option.state & QStyle.State_MouseOver
                        else QIcon.Normal)
            elif option.state & QStyle.State_Enabled:
                mode = QIcon.Normal
            else:
                mode = QIcon.Disabled
            if self.isChecked():
                state = QIcon.On
            else:
                state = QIcon.Off
            option.icon.paint(painter, rect, Qt.AlignCenter, mode, state)


def flat_button_hover_background(
        painter: QPainter, option: QStyleOption
):
    palette = option.palette
    g = QLinearGradient(0, 0, 0, 1)
    g.setCoordinateMode(QLinearGradient.ObjectBoundingMode)
    base = palette.color(QPalette.Window)
    base.setAlpha(200)
    if base.value() < 127:
        base_ = base.lighter(170)
    else:
        base_ = base.darker(130)
    g.setColorAt(0, base_)
    g.setColorAt(0.6, base)
    g.setColorAt(1.0, base_)
    brush = QBrush(base_)
    cg = palette_color_group(option.state)
    if option.state & QStyle.State_HasFocus:
        pen = palette.color(cg, QPalette.Highlight)
    elif option.state & QStyle.State_MouseOver:
        pen = palette.color(cg, QPalette.WindowText)
        pen.setAlpha(50)
    else:
        pen = Qt.NoPen
    painter.save()
    painter.setPen(pen)
    painter.setBrush(brush)
    painter.translate(0.5, 0.5)
    painter.setRenderHints(QPainter.Antialiasing, True)
    painter.drawRoundedRect(option.rect.adjusted(0, 0, -1, -1), 2., 2., )
    painter.restore()


def palette_color_group(state: QStyle.StateFlag) -> QPalette.ColorGroup:
    if not state & QStyle.State_Enabled:
        return QPalette.Disabled
    elif state & QStyle.State_Active:
        return QPalette.Active
    else:
        return QPalette.Inactive
