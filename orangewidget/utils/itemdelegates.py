import enum
from datetime import date, datetime
from functools import partial
from itertools import filterfalse
from types import MappingProxyType as MappingProxy
from typing import (
    Sequence, Any, Mapping, Dict, TypeVar, Type, Optional, Container, Tuple,
)
from typing_extensions import Final

import numpy as np

from AnyQt.QtCore import (
    Qt, QObject, QAbstractItemModel, QModelIndex, QPersistentModelIndex, Slot,
    QLocale, QRect, QPointF, QSize, QLineF, QT_VERSION_INFO,
)
from AnyQt.QtGui import (
    QFont, QFontMetrics, QPalette, QColor, QBrush, QIcon, QPixmap, QImage,
    QPainter, QStaticText, QTransform, QPen
)
from AnyQt.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, \
    QApplication, QStyle

from orangewidget.utils.cache import LRUCache
from orangewidget.utils import enum_as_int


A = TypeVar("A")


def item_data(
        index: QModelIndex, roles: Sequence[int]
) -> Dict[int, Any]:
    """Query `index` for all `roles` and return them as a mapping"""
    model = index.model()
    datagetter = partial(model.data, index)
    values = map(datagetter, roles)
    return dict(zip(roles, values))


class ModelItemCache(QObject):
    """
    An item data cache for accessing QAbstractItemModel.data

    >>> cache = ModelItemCache()
    >>> cache.itemData(index, (Qt.DisplayRole, Qt.DecorationRole))
    {0: ...

    """
    # __slots__ = ("__model", "__cache_data")

    def __init__(self, *args, maxsize=100 * 200, **kwargs):
        super().__init__(*args, **kwargs)
        self.__model: Optional[QAbstractItemModel] = None
        self.__cache_data: 'LRUCache[QPersistentModelIndex, Any]' = LRUCache(maxsize)

    def __connect_helper(self, model: QAbstractItemModel) -> None:
        model.dataChanged.connect(self.invalidate)
        model.layoutAboutToBeChanged.connect(self.invalidate)
        model.modelAboutToBeReset.connect(self.invalidate)
        model.rowsAboutToBeInserted.connect(self.invalidate)
        model.rowsAboutToBeRemoved.connect(self.invalidate)
        model.rowsAboutToBeMoved.connect(self.invalidate)
        model.columnsAboutToBeInserted.connect(self.invalidate)
        model.columnsAboutToBeRemoved.connect(self.invalidate)
        model.columnsAboutToBeMoved.connect(self.invalidate)

    def __disconnect_helper(self, model: QAbstractItemModel) -> None:
        model.dataChanged.disconnect(self.invalidate)
        model.layoutAboutToBeChanged.disconnect(self.invalidate)
        model.modelAboutToBeReset.disconnect(self.invalidate)
        model.rowsAboutToBeInserted.disconnect(self.invalidate)
        model.rowsAboutToBeRemoved.disconnect(self.invalidate)
        model.rowsAboutToBeMoved.disconnect(self.invalidate)
        model.columnsAboutToBeInserted.disconnect(self.invalidate)
        model.columnsAboutToBeRemoved.disconnect(self.invalidate)
        model.columnsAboutToBeMoved.disconnect(self.invalidate)

    def setModel(self, model: QAbstractItemModel) -> None:
        if model is self.__model:
            return
        if self.__model is not None:
            self.__disconnect_helper(self.__model)
            self.__model = None
        self.__model = model
        self.__cache_data.clear()
        if model is not None:
            self.__connect_helper(model)

    def model(self) -> Optional[QAbstractItemModel]:
        return self.__model

    @Slot()
    def invalidate(self) -> None:
        """Invalidate all cached data."""
        self.__cache_data.clear()

    def itemData(
            self, index: QModelIndex, roles: Sequence[int]
    ) -> Mapping[int, Any]:
        """
        Return item data from `index` for `roles`.

        The returned mapping is a read only view of *all* data roles accessed
        for the index through this caching interface. It will contain at least
        data for `roles`, but can also contain other ones.
        """
        model = index.model()
        if model is not self.__model:
            self.setModel(model)
        # NOTE: QPersistentModelIndex's hash changes when it is invalidated;
        # it must be purged from __cache_data before that (`__connect_helper`)
        key = QPersistentModelIndex(index)
        try:
            item = self.__cache_data[key]
        except KeyError:
            data = item_data(index, roles)
            view = MappingProxy(data)
            self.__cache_data[key] = data, view
        else:
            data, view = item
            queryroles = tuple(filterfalse(data.__contains__, roles))
            if queryroles:
                data.update(item_data(index, queryroles))
        return view

    def data(self, index: QModelIndex, role: int) -> Any:
        """Return item data for `index` and `role`"""
        model = index.model()
        if model is not self.__model:
            self.setModel(model)
        key = QPersistentModelIndex(index)
        try:
            item = self.__cache_data[key]
        except KeyError:
            data = item_data(index, (role,))
            view = MappingProxy(data)
            self.__cache_data[key] = data, view
        else:
            data, view = item
            if role not in data:
                data[role] = model.data(index, role)
        return data[role]


def cast_(type_: Type[A], value: Any) -> Optional[A]:
    # similar but not quite the same as qvariant_cast
    if value is None:
        return value
    if type(value) is type_:  # pylint: disable=unidiomatic-typecheck
        return value
    try:
        return type_(value)
    except Exception:  # pylint: disable=broad-except  # pragma: no cover
        return None


# QStyleOptionViewItem.Feature aliases as python int. Feature.__ior__
# implementation is slower then int.__ior__
_QStyleOptionViewItem_HasDisplay = enum_as_int(QStyleOptionViewItem.HasDisplay)
_QStyleOptionViewItem_HasCheckIndicator = enum_as_int(QStyleOptionViewItem.HasCheckIndicator)
_QStyleOptionViewItem_HasDecoration = enum_as_int(QStyleOptionViewItem.HasDecoration)


class _AlignmentFlagsCache(dict):
    # A cached int -> Qt.Alignment cache. Used to avoid temporary Qt.Alignment
    # flags object (de)allocation.
    def __missing__(self, key: int) -> Qt.AlignmentFlag:
        a = Qt.AlignmentFlag(key)
        self.setdefault(key, a)
        return a


_AlignmentCache: Mapping[int, Qt.Alignment] = _AlignmentFlagsCache()
_AlignmentMask = int(Qt.AlignHorizontal_Mask | Qt.AlignVertical_Mask)


def init_style_option(
        delegate: QStyledItemDelegate,
        option: QStyleOptionViewItem,
        index: QModelIndex,
        data: Mapping[int, Any],
        roles: Optional[Container[int]] = None,
) -> None:
    """
    Like `QStyledItemDelegate.initStyleOption` but fill in the fields from
    `data` mapping. If `roles` is not `None` init the `option` for the
    specified `roles` only.
    """
    # pylint: disable=too-many-branches
    option.styleObject = None
    option.index = index
    if roles is None:
        roles = data
    features = 0
    if Qt.DisplayRole in roles:
        value = data.get(Qt.DisplayRole)
        if value is not None:
            option.text = delegate.displayText(value, option.locale)
            features |= _QStyleOptionViewItem_HasDisplay
    if Qt.FontRole in roles:
        value = data.get(Qt.FontRole)
        font = cast_(QFont, value)
        if font is not None:
            font = font.resolve(option.font)
            option.font = font
            option.fontMetrics = QFontMetrics(option.font)
    if Qt.ForegroundRole in roles:
        value = data.get(Qt.ForegroundRole)
        foreground = cast_(QBrush, value)
        if foreground is not None:
            option.palette.setBrush(QPalette.Text, foreground)
    if Qt.BackgroundRole in roles:
        value = data.get(Qt.BackgroundRole)
        background = cast_(QBrush, value)
        if background is not None:
            option.backgroundBrush = background
    if Qt.TextAlignmentRole in roles:
        value = data.get(Qt.TextAlignmentRole)
        alignment = cast_(int, value)
        if alignment is not None:
            alignment = alignment & _AlignmentMask
            option.displayAlignment = _AlignmentCache[alignment]
    if Qt.CheckStateRole in roles:
        state = data.get(Qt.CheckStateRole)
        if state is not None:
            features |= _QStyleOptionViewItem_HasCheckIndicator
            state = cast_(int, state)
            if state is not None:
                option.checkState = Qt.CheckState(state)
    if Qt.DecorationRole in roles:
        value = data.get(Qt.DecorationRole)
        if value is not None:
            features |= _QStyleOptionViewItem_HasDecoration
        if isinstance(value, QIcon):
            option.icon = value
        elif isinstance(value, QColor):
            pix = QPixmap(option.decorationSize)
            pix.fill(value)
            option.icon = QIcon(pix)
        elif isinstance(value, QPixmap):
            option.icon = QIcon(value)
            option.decorationSize = (value.size() / value.devicePixelRatio()).toSize()
        elif isinstance(value, QImage):
            pix = QPixmap.fromImage(value)
            option.icon = QIcon(value)
            option.decorationSize = (pix.size() / pix.devicePixelRatio()).toSize()
    option.features |= QStyleOptionViewItem.ViewItemFeature(features)


class CachedDataItemDelegate(QStyledItemDelegate):
    """
    An QStyledItemDelegate with item model data caching.

    Parameters
    ----------
    roles: Sequence[int]
        A set of roles to query the model and fill the `QStyleOptionItemView`
        with. By specifying only a subset of the roles here the delegate can
        be speed up (e.g. if you know the model does not provide the relevant
        roles or you just want to ignore some of them).
    """
    # __slots__ = ("roles", "__cache",)

    #: The default roles that are filled in initStyleOption
    DefaultRoles = (
        Qt.DisplayRole, Qt.TextAlignmentRole, Qt.FontRole, Qt.ForegroundRole,
        Qt.BackgroundRole, Qt.CheckStateRole, Qt.DecorationRole
    )

    def __init__(
            self, *args, roles: Sequence[int] = None, **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        if roles is None:
            roles = self.DefaultRoles
        self.roles = tuple(roles)
        self.__cache = ModelItemCache(self)

    def cachedItemData(
            self, index: QModelIndex, roles: Sequence[int]
    ) -> Mapping[int, Any]:
        """
        Return a mapping of all roles for the index.

        .. note::
           The returned mapping contains at least `roles`, but will also
           contain all cached roles that were queried previously.
        """
        return self.__cache.itemData(index, roles)

    def cachedData(self, index: QModelIndex, role: int) -> Any:
        """Return the data for role from `index`."""
        return self.__cache.data(index, role)

    def initStyleOption(
            self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        """
        Reimplemented.

        Use caching to query the model data. Also limit the roles queried
        from the model and filled in `option` to `self.roles`.
        """
        data = self.cachedItemData(index, self.roles)
        init_style_option(self, option, index, data, self.roles)


_Real = (float, np.floating)
_Integral = (int, np.integer)
_Number = _Integral + _Real
_String = (str, np.str_)
_DateTime = (date, datetime, np.datetime64)
_TypesAlignRight = _Number + _DateTime


class StyledItemDelegate(QStyledItemDelegate):
    """
    A `QStyledItemDelegate` subclass supporting a broader range of python
    and numpy types for display.

    E.g. supports `np.float*`, `np.(u)int`, `datetime.date`,
    `datetime.datetime`
    """
    #: Types that are displayed as real (decimal)
    RealTypes: Final[Tuple[type, ...]] = _Real
    #: Types that are displayed as integers
    IntegralTypes: Final[Tuple[type, ...]] = _Integral
    #: RealTypes and IntegralTypes combined
    NumberTypes: Final[Tuple[type, ...]] = _Number
    #: Date time types
    DateTimeTypes: Final[Tuple[type, ...]] = _DateTime

    def displayText(self, value: Any, locale: QLocale) -> str:
        """
        Reimplemented.
        """
        # NOTE: Maybe replace the if,elif with a dispatch a table
        if value is None:
            return ""
        elif type(value) is str:  # pylint: disable=unidiomatic-typecheck
            return value  # avoid copies
        elif isinstance(value, _Integral):
            return super().displayText(int(value), locale)
        elif isinstance(value, _Real):
            return super().displayText(float(value), locale)
        elif isinstance(value, _String):
            return str(value)
        elif isinstance(value, datetime):
            return value.isoformat(sep=" ")
        elif isinstance(value, date):
            return value.isoformat()
        elif isinstance(value, np.datetime64):
            return self.displayText(value.astype(datetime), locale)
        return super().displayText(value, locale)


_Qt_AlignRight = enum_as_int(Qt.AlignRight)
_Qt_AlignLeft = enum_as_int(Qt.AlignLeft)
_Qt_AlignHCenter = enum_as_int(Qt.AlignHCenter)
_Qt_AlignTop = enum_as_int(Qt.AlignTop)
_Qt_AlignBottom = enum_as_int(Qt.AlignBottom)
_Qt_AlignVCenter = enum_as_int(Qt.AlignVCenter)

_StaticTextKey = Tuple[str, QFont, Qt.TextElideMode, int]
_PenKey = Tuple[str, int]
_State_Mask = enum_as_int(
    QStyle.State_Selected | QStyle.State_Enabled | QStyle.State_Active
)


class DataDelegate(CachedDataItemDelegate, StyledItemDelegate):
    """
    A QStyledItemDelegate optimized for displaying fixed tabular data.

    This delegate will automatically display numeric and date/time values
    aligned to the right.

    Note
    ----
    Does not support text wrapping
    """
    # __slots__ = (
    #     "__static_text_lru_cache", "__pen_lru_cache", "__style"
    # )
    #: Types that are right aligned by default (when Qt.TextAlignmentRole
    #: is not defined by the model or is excluded from self.roles)
    TypesAlignRight: Final[Tuple[type, ...]] = _TypesAlignRight

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__static_text_lru_cache: LRUCache[_StaticTextKey, QStaticText]
        self.__static_text_lru_cache = LRUCache(100 * 200)
        self.__pen_lru_cache: LRUCache[_PenKey, QPen] = LRUCache(100)
        self.__style = None

    def initStyleOption(
            self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        data = self.cachedItemData(index, self.roles)
        init_style_option(self, option, index, data, self.roles)
        if data.get(Qt.TextAlignmentRole) is None \
                and Qt.TextAlignmentRole in self.roles \
                and isinstance(data.get(Qt.DisplayRole), _TypesAlignRight):
            option.displayAlignment = \
                (option.displayAlignment & ~Qt.AlignHorizontal_Mask) | \
                Qt.AlignRight

    def paint(
            self, painter: QPainter, option: QStyleOptionViewItem,
            index: QModelIndex
    ) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = QApplication.style() if widget is None else widget.style()
        # Keep ref to style wrapper. This is ugly, wrong but the wrapping of
        # C++ QStyle instance takes ~5% unless the wrapper already exists.
        self.__style = style
        text = opt.text
        opt.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)
        trect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, widget)
        opt.text = text
        self.drawViewItemText(style, painter, opt, trect)

    def drawViewItemText(
            self, style: QStyle, painter: QPainter,
            option: QStyleOptionViewItem, rect: QRect
    ) -> None:
        """
        Draw view item text in `rect` using `style` and `painter`.
        """
        margin = style.pixelMetric(
            QStyle.PM_FocusFrameHMargin, None, option.widget) + 1
        rect = rect.adjusted(margin, 0, -margin, 0)
        font = option.font
        st = self.__static_text_elided_cache(
            option.text, font, option.fontMetrics, option.textElideMode,
            rect.width()
        )
        tsize = st.size()
        textalign = enum_as_int(option.displayAlignment)
        text_pos_x = text_pos_y = 0.0

        if textalign & _Qt_AlignLeft:
            text_pos_x = rect.left()
        elif textalign & _Qt_AlignRight:
            text_pos_x = rect.x() + rect.width() - tsize.width()
        elif textalign & _Qt_AlignHCenter:
            text_pos_x = rect.x() + rect.width() / 2 - tsize.width() / 2

        if textalign & _Qt_AlignVCenter:
            text_pos_y = rect.y() + rect.height() / 2 - tsize.height() / 2
        elif textalign & _Qt_AlignTop:
            text_pos_y = rect.top()
        elif textalign & _Qt_AlignBottom:
            text_pos_y = rect.top() + rect.height() - tsize.height()

        painter.setPen(self.__pen_cache(option.palette, option.state))
        painter.setFont(font)
        painter.drawStaticText(QPointF(text_pos_x, text_pos_y), st)

    def __static_text_elided_cache(
            self, text: str, font: QFont, fontMetrics: QFontMetrics,
            elideMode: Qt.TextElideMode, width: int
    ) -> QStaticText:
        """
        Return a `QStaticText` instance for depicting the text with the `font`
        """
        fd = _font_def(font)
        try:
            return self.__static_text_lru_cache[text, fd, elideMode, width]
        except KeyError:
            st = QStaticText(fontMetrics.elidedText(text, elideMode, width))
            st.prepare(QTransform(), font)
            key = text, fd, elideMode, width
            self.__static_text_lru_cache[key] = st
            return st

    def __pen_cache(self, palette: QPalette, state: QStyle.State) -> QPen:
        """Return a QPen from the `palette` for `state`."""
        # NOTE: This method exists mostly to avoid QPen, QColor (de)allocations.
        key = palette.cacheKey(), enum_as_int(state) & _State_Mask
        try:
            return self.__pen_lru_cache[key]
        except KeyError:
            pen = QPen(text_color_for_state(palette, state))
            self.__pen_lru_cache[key] = pen
            return pen


if QT_VERSION_INFO >= (5, 13):
    def _font_def(font: QFont):
        return (font.pointSizeF(), font.pixelSize(),
                font.weight(), font.style(), font.stretch(), font.styleHint(),
                font.styleStrategy(),
                # font.ignorePitch(),
                font.fixedPitch(), tuple(font.families()), font.styleName(),
                font.hintingPreference())
else:
    def _font_def(font: QFont):
        return (font.pointSizeF(), font.pixelSize(),
                font.weight(), font.style(), font.stretch(), font.styleHint(),
                font.styleStrategy(),
                # font.ignorePitch(),
                font.fixedPitch(), font.family(), font.styleName(),
                font.hintingPreference())


def text_color_for_state(palette: QPalette, state: QStyle.State) -> QColor:
    """Return the appropriate `palette` text color for the `state`."""
    cgroup = QPalette.Normal if state & QStyle.State_Active else QPalette.Inactive
    cgroup = cgroup if state & QStyle.State_Enabled else QPalette.Disabled
    role = QPalette.HighlightedText if state & QStyle.State_Selected else QPalette.Text
    return palette.color(cgroup, role)


class BarItemDataDelegate(DataDelegate):
    """
    An delegate drawing a horizontal bar below its text.

    Can be used to visualise numerical column distribution.

    Parameters
    ----------
    parent: Optional[QObject]
        Parent object
    color: QColor
        The default color for the bar. If not set then the palette's
        foreground role is used.
    penWidth: int
        The bar pen width.
    barFillRatioRole: int
        The item model role used to query the bar fill ratio (see
        :method:`barFillRatioData`)
    barColorRole: int
        The item model role used to query the bar color.
    """
    # __slots__ = (
    #     "color", "penWidth", "barFillRatioRole", "barColorRole",
    #     "__line", "__pen"
    # )

    def __init__(
            self, parent: Optional[QObject] = None, color=QColor(), penWidth=5,
            barFillRatioRole=Qt.UserRole + 1, barColorRole=Qt.UserRole + 2,
            **kwargs
    ):
        super().__init__(parent, **kwargs)
        self.color = color
        self.penWidth = penWidth
        self.barFillRatioRole = barFillRatioRole
        self.barColorRole = barColorRole
        # Line and pen instances reused
        self.__line = QLineF()
        self.__pen = QPen(color, penWidth, Qt.SolidLine, Qt.RoundCap)

    def barFillRatioData(self, index: QModelIndex) -> Optional[float]:
        """
        Return a number between 0.0 and 1.0 indicating the bar fill ratio.

        The default implementation queries the model for `barFillRatioRole`
        """
        return cast_(float, self.cachedData(index, self.barFillRatioRole))

    def barColorData(self, index: QModelIndex) -> Optional[QColor]:
        """
        Return the color for the bar.

        The default implementation queries the model for `barColorRole`
        """
        return cast_(QColor, self.cachedData(index, self.barColorRole))

    def sizeHint(
            self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        sh = super().sizeHint(option, index)
        pw, vmargin = self.penWidth, 1
        sh.setHeight(sh.height() + pw + vmargin)
        return sh

    def paint(
            self, painter: QPainter, option: QStyleOptionViewItem,
            index: QModelIndex
    ) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = QApplication.style() if widget is None else widget.style()
        self.__style = style
        text = opt.text
        opt.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        textrect = style.subElementRect(
            QStyle.SE_ItemViewItemText, opt, widget)

        ratio = self.barFillRatioData(index)
        if ratio is not None and 0. <= ratio <= 1.:
            color = self.barColorData(index)
            if color is None:
                color = self.color
            if not color.isValid():
                color = opt.palette.color(QPalette.WindowText)
            rect = option.rect
            pw = self.penWidth
            hmargin = 3 + pw / 2  # + half pen width for the round line cap
            vmargin = 1
            textoffset = pw + vmargin * 2
            baseline = rect.bottom() - textoffset / 2
            width = (rect.width() - 2 * hmargin) * ratio
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            pen = self.__pen
            pen.setColor(color)
            pen.setWidth(pw)
            painter.setPen(pen)
            line = self.__line
            left = rect.left() + hmargin
            line.setLine(left, baseline, left + width, baseline)
            painter.drawLine(line)
            painter.restore()
            textrect.adjust(0, 0, 0, -textoffset)

        opt.text = text
        self.drawViewItemText(style, painter, opt, textrect)
