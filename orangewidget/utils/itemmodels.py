import collections
from collections import defaultdict
from typing import Sequence
from math import isnan, isinf
from numbers import Number, Integral

import operator

from contextlib import contextmanager
from warnings import warn

from AnyQt.QtCore import (
    Qt, QObject, QAbstractListModel, QAbstractTableModel, QModelIndex,
    QItemSelectionModel, QMimeData
)
from AnyQt.QtCore import pyqtSignal as Signal
from AnyQt.QtWidgets import (
    QWidget, QBoxLayout, QToolButton, QAbstractButton, QAction
)

import numpy


class _store(dict):
    pass


def _argsort(seq, cmp=None, key=None, reverse=False):
    indices = range(len(seq))
    if key is not None:
        return sorted(indices, key=lambda i: key(seq[i]), reverse=reverse)
    elif cmp is not None:
        from functools import cmp_to_key
        return sorted(indices, key=cmp_to_key(lambda a, b: cmp(seq[a], seq[b])),
                      reverse=reverse)
    else:
        return sorted(indices, key=lambda i: seq[i], reverse=reverse)


@contextmanager
def signal_blocking(obj):
    blocked = obj.signalsBlocked()
    obj.blockSignals(True)
    try:
        yield
    finally:
        obj.blockSignals(blocked)


def _as_contiguous_range(the_slice, length):
    start, stop, step = the_slice.indices(length)
    if step == -1:
        # Equivalent range with positive step
        start, stop, step = stop + 1, start + 1, 1
    elif not (step == 1 or step is None):
        raise IndexError("Non-contiguous range.")
    return start, stop, step


class AbstractSortTableModel(QAbstractTableModel):
    """
    A sorting proxy table model that sorts its rows in fast numpy,
    avoiding potentially thousands of calls into
    ``QSortFilterProxyModel.lessThan()`` or any potentially costly
    reordering of original data.

    Override ``sortColumnData()``, adapting it to your underlying model.

    Make sure to use ``mapToSourceRows()``/``mapFromSourceRows()``
    whenever fetching or manipulating table data, such as in ``data()``.

    When updating the model (inserting, removing rows), the sort order
    needs to be accounted for (e.g. reset and re-applied).
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.__sortInd = None     #: Indices sorting the source table
        self.__sortIndInv = None  #: The inverse of __sortInd
        self.__sortColumn = -1    #: Sort key column, or -1
        self.__sortOrder = Qt.AscendingOrder

    def sortColumnData(self, column):
        """Return raw, sortable data for column"""
        raise NotImplementedError

    def _sortColumnData(self, column):
        try:
            # Call the overridden implementation if available
            data = numpy.asarray(self.sortColumnData(column))
        except NotImplementedError:
            # Fallback to slow implementation
            data = numpy.array([self.index(row, column).data()
                                for row in range(self.rowCount())])

        assert data.ndim in (1, 2), 'Data should be 1- or 2-dimensional'
        data = data[self.mapToSourceRows(Ellipsis)]
        return data

    def sortColumn(self):
        """The column currently used for sorting (-1 if no sorting is applied)"""
        return self.__sortColumn

    def sortOrder(self):
        """The current sort order"""
        return self.__sortOrder

    def mapToSourceRows(self, rows):
        """Return array of row indices in the source table for given model rows

        Parameters
        ----------
        rows : int or list of int or numpy.ndarray of dtype=int or Ellipsis
            View (sorted) rows.

        Returns
        -------
        numpy.ndarray
            Source rows matching input rows. If they are the same,
            simply input `rows` is returned.
        """
        # self.__sortInd[rows] fails if `rows` is an empty list or array
        if self.__sortInd is not None \
                and (isinstance(rows, (Integral, type(Ellipsis)))
                     or len(rows)):
            new_rows = self.__sortInd[rows]
            if rows is Ellipsis:
                new_rows.setflags(write=False)
            rows = new_rows
        return rows

    def mapFromSourceRows(self, rows):
        """Return array of row indices in the model for given source table rows

        Parameters
        ----------
        rows : int or list of int or numpy.ndarray of dtype=int or Ellipsis
            Source model rows.

        Returns
        -------
        numpy.ndarray
            ModelIndex (sorted) rows matching input source rows.
            If they are the same, simply input `rows` is returned.
        """
        # self.__sortInd[rows] fails if `rows` is an empty list or array
        if self.__sortIndInv is not None \
                and (isinstance(rows, (Integral, type(Ellipsis)))
                     or len(rows)):
            new_rows = self.__sortIndInv[rows]
            if rows is Ellipsis:
                new_rows.setflags(write=False)
            rows = new_rows
        return rows

    def resetSorting(self):
        """Invalidates the current sorting"""
        return self.sort(-1)

    def _argsortData(self, data: numpy.ndarray, order):
        """
        Return indices of sorted data. May be reimplemented to handle
        sorting in a certain way, e.g. to sort NaN values last.
        """
        if data.ndim == 1:
            indices = numpy.argsort(data, kind="mergesort")
        else:
            indices = numpy.lexsort(data.T[::-1])
        if order == Qt.DescendingOrder:
            indices = indices[::-1]
        return indices

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder):
        """
        Sort the data by `column` into `order`.

        To reset the order, pass column=-1.

        Reimplemented from QAbstractItemModel.sort().

        Notes
        -----
        This only affects the model's data presentation. The underlying
        data table is left unmodified. Use mapToSourceRows()/mapFromSourceRows()
        when accessing data by row indexes.
        """
        indices = self._sort(column, order)
        self.__sortColumn = -1 if column < 0 else column
        self.__sortOrder = order
        self.setSortIndices(indices)

    def setSortIndices(self, indices):
        layoutChangeParams = ([], QAbstractTableModel.VerticalSortHint)
        try:
            self.layoutAboutToBeChanged.emit(*layoutChangeParams)
        except TypeError:
            layoutChangeParams = ()
            self.layoutAboutToBeChanged.emit()

        # Store persistent indices as well as their (actual) rows in the
        # source data table.
        persistent = self.persistentIndexList()
        persistent_rows = self.mapToSourceRows([i.row() for i in persistent])

        if indices is not None:
            self.__sortInd = numpy.asarray(indices)
            self.__sortIndInv = numpy.argsort(indices)
        else:
            self.__sortInd = None
            self.__sortIndInv = None

        persistent_rows = self.mapFromSourceRows(persistent_rows)

        self.changePersistentIndexList(
            persistent,
            [self.index(row, pind.column())
             for row, pind in zip(persistent_rows, persistent)])
        self.layoutChanged.emit(*layoutChangeParams)

    def _sort(self, column, order):
        indices = None
        if column >= 0:
            # - _sortColumnData returns data in its currently shown order
            # - _argSortData thus returns an array a, in which a[i] is the row
            #   number (in the current view) to put to line i
            # - mapToSourceRows maps these indices back to original data.
            # This contrived procedure (instead of _sortColumnData returning
            # the original data, saving us from double mapping) ensures stable
            # sort order on consecutive calls
            data = numpy.asarray(self._sortColumnData(column))
            if data is None:
                data = numpy.arange(self.rowCount())
            elif data.dtype == object:
                data = data.astype(str)
            indices = self.mapToSourceRows(self._argsortData(data, order))
        return indices


class PyTableModel(AbstractSortTableModel):
    """ A model for displaying python tables (sequences of sequences) in
    QTableView objects.

    Parameters
    ----------
    sequence : list
        The initial list to wrap.
    parent : QObject
        Parent QObject.
    editable: bool or sequence
        If True, all items are flagged editable. If sequence, the True-ish
        fields mark their respective columns editable.

    Notes
    -----
    The model rounds numbers to human readable precision, e.g.:
    1.23e-04, 1.234, 1234.5, 12345, 1.234e06.

    To set additional item roles, use setData().
    """

    @staticmethod
    def _RoleData():
        return defaultdict(lambda: defaultdict(dict))

    def __init__(self, sequence=None, parent=None, editable=False):
        super().__init__(parent)
        self._headers = {}
        self._editable = editable
        self._table = None
        self._roleData = None
        if sequence is None:
            sequence = []
        self.wrap(sequence)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else max(map(len, self._table), default=0)

    def flags(self, index):
        flags = super().flags(index)
        if not self._editable or not index.isValid():
            return flags
        if isinstance(self._editable, Sequence):
            return flags | Qt.ItemIsEditable if self._editable[index.column()] else flags
        return flags | Qt.ItemIsEditable

    def setData(self, index, value, role=Qt.EditRole):
        row = self.mapFromSourceRows(index.row())
        if role == Qt.EditRole:
            self[row][index.column()] = value
            self.dataChanged.emit(index, index)
        else:
            self._roleData[row][index.column()][role] = value
        return True

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return

        row, column = self.mapToSourceRows(index.row()), index.column()

        role_value = self._roleData.get(row, {}).get(column, {}).get(role)
        if role_value is not None:
            return role_value

        try:
            value = self[row][column]
        except IndexError:
            return
        if role == Qt.EditRole:
            return value
        # if role == Qt.DecorationRole and isinstance(value, Variable):
        #     return gui.attributeIconDict[value]
        if role == Qt.DisplayRole:
            if (isinstance(value, Number) and
                    not (isnan(value) or isinf(value) or isinstance(value, Integral))):
                absval = abs(value)
                strlen = len(str(int(absval)))
                value = '{:.{}{}}'.format(value,
                                          2 if absval < .001 else
                                          3 if strlen < 2 else
                                          1 if strlen < 5 else
                                          0 if strlen < 6 else
                                          3,
                                          'f' if (absval == 0 or
                                                  absval >= .001 and
                                                  strlen < 6)
                                          else 'e')
            return str(value)
        if role == Qt.TextAlignmentRole and isinstance(value, Number):
            return Qt.AlignRight | Qt.AlignVCenter
        if role == Qt.ToolTipRole:
            return str(value)

    def sortColumnData(self, column):
        return [row[column] for row in self._table]

    def setHorizontalHeaderLabels(self, labels):
        """
        Parameters
        ----------
        labels : list of str
        """
        self._headers[Qt.Horizontal] = tuple(labels)

    def setVerticalHeaderLabels(self, labels):
        """
        Parameters
        ----------
        labels : list of str
        """
        self._headers[Qt.Vertical] = tuple(labels)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        headers = self._headers.get(orientation)

        if headers and section < len(headers):
            section = self.mapToSourceRows(section) if orientation == Qt.Vertical else section
            value = headers[section]
            if role == Qt.ToolTipRole:
                role = Qt.DisplayRole
            if role == Qt.DisplayRole:
                return value

        # Use QAbstractItemModel default for non-existent header/sections
        return super().headerData(section, orientation, role)

    def removeRows(self, row, count, parent=QModelIndex()):
        if not parent.isValid():
            del self[row:row + count]
            for rowidx in range(row, row + count):
                self._roleData.pop(rowidx, None)
            return True
        return False

    def removeColumns(self, column, count, parent=QModelIndex()):
        self.beginRemoveColumns(parent, column, column + count - 1)
        for row in self._table:
            del row[column:column + count]
        for cols in self._roleData.values():
            for col in range(column, column + count):
                cols.pop(col, None)
        del self._headers.get(Qt.Horizontal, [])[column:column + count]
        self.endRemoveColumns()
        return True

    def insertRows(self, row, count, parent=QModelIndex()):
        self.beginInsertRows(parent, row, row + count - 1)
        self._table[row:row] = [[''] * self.columnCount() for _ in range(count)]
        self.endInsertRows()
        return True

    def insertColumns(self, column, count, parent=QModelIndex()):
        self.beginInsertColumns(parent, column, column + count - 1)
        for row in self._table:
            row[column:column] = [''] * count
        self.endInsertColumns()
        return True

    def __len__(self):
        return len(self._table)

    def __bool__(self):
        return len(self) != 0

    def __iter__(self):
        return iter(self._table)

    def __getitem__(self, item):
        return self._table[item]

    def __delitem__(self, i):
        if isinstance(i, slice):
            start, stop, _ = _as_contiguous_range(i, len(self))
            stop -= 1
        else:
            start = stop = i = i if i >= 0 else len(self) + i
        self._check_sort_order()
        self.beginRemoveRows(QModelIndex(), start, stop)
        del self._table[i]
        self.endRemoveRows()

    def __setitem__(self, i, value):
        self._check_sort_order()

        if isinstance(i, slice):
            start, stop, _ = _as_contiguous_range(i, len(self))
            if not isinstance(value, collections.abc.Sized):
                value = tuple(value)
            newstop = start + len(value)

            # Signal changes
            parent = QModelIndex()
            if newstop > stop:
                self.rowsAboutToBeInserted.emit(parent, stop, newstop - 1)
            elif newstop < stop:
                self.rowsAboutToBeRemoved.emit(parent, newstop, stop - 1)

            # Make changes
            self._table[i] = value

            # Signal change were made
            if start != min(stop, newstop):
                self.dataChanged.emit(
                    self.index(start, 0),
                    self.index(min(stop, newstop) - 1, self.columnCount() - 1))
            if newstop > stop:
                self.rowsInserted.emit(parent, stop, newstop - 1)
            elif newstop < stop:
                self.rowsRemoved.emit(parent, newstop, stop - 1)
        else:
            self._table[i] = value
            i %= len(self)
            self.dataChanged.emit(self.index(i, 0),
                                  self.index(i, self.columnCount() - 1))

    def _check_sort_order(self):
        if self.mapToSourceRows(Ellipsis) is not Ellipsis:
            warn("Can't modify PyTableModel when it's sorted",
                 RuntimeWarning, stacklevel=3)
            raise RuntimeError("Can't modify PyTableModel when it's sorted")

    def wrap(self, table):
        self.beginResetModel()
        self._table = table
        self._roleData = self._RoleData()
        self.resetSorting()
        self.endResetModel()

    def tolist(self):
        return self._table

    def clear(self):
        self.beginResetModel()
        self._table.clear()
        self.resetSorting()
        self._roleData.clear()
        self.endResetModel()

    def append(self, row):
        self.extend([row])

    def _insertColumns(self, rows):
        n_max = max(map(len, rows))
        if self.columnCount() < n_max:
            self.insertColumns(self.columnCount(), n_max - self.columnCount())

    def extend(self, rows):
        i, rows = len(self), list(rows)
        self.insertRows(i, len(rows))
        self._insertColumns(rows)
        self[i:] = rows

    def insert(self, i, row):
        self.insertRows(i, 1)
        self._insertColumns((row,))
        self[i] = row

    def remove(self, val):
        del self[self._table.index(val)]


class PyListModel(QAbstractListModel):
    """ A model for displaying python list like objects in Qt item view classes
    """
    MIME_TYPE = "application/x-Orange-PyListModelData"
    Separator = object()
    removed = Signal()

    def __init__(self, iterable=None, parent=None,
                 flags=Qt.ItemIsSelectable | Qt.ItemIsEnabled,
                 list_item_role=Qt.DisplayRole,
                 enable_dnd=False,
                 supportedDropActions=Qt.MoveAction):
        super().__init__(parent)
        self._list = []
        self._other_data = []
        if enable_dnd:
            flags |= Qt.ItemIsDragEnabled
        self._flags = flags
        self.list_item_role = list_item_role

        self._supportedDropActions = supportedDropActions
        if iterable is not None:
            self.extend(iterable)

    def _is_index_valid(self, index):
        # This error would happen if one wraps a list into a model and then
        # modifies a list instead of a model
        if len(self) != len(self._other_data):
            raise RuntimeError("Mismatched length of model and its _other_data")
        if isinstance(index, QModelIndex) and index.isValid():
            row, column = index.row(), index.column()
            return 0 <= row < len(self) and column == 0
        elif isinstance(index, int):
            return -len(self) <= index < len(self)
        else:
            return False

    def wrap(self, lst):
        """ Wrap the list with this model. All changes to the model
        are done in place on the passed list
        """
        self.beginResetModel()
        self._list = lst
        self._other_data = [_store() for _ in lst]
        self.endResetModel()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return str(section)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._list)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 1

    def data(self, index, role=Qt.DisplayRole):
        row = index.row()
        if role in [self.list_item_role, Qt.EditRole] \
                and self._is_index_valid(index):
            return self[row]
        elif self._is_index_valid(row):
            return self._other_data[row].get(role, None)

    def itemData(self, index):
        mapping = QAbstractListModel.itemData(self, index)
        if self._is_index_valid(index):
            items = list(self._other_data[index.row()].items())
        else:
            items = []
        for key, value in items:
            mapping[key] = value
        return mapping

    def parent(self, index=QModelIndex()):
        return QModelIndex()

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.EditRole:
            if self._is_index_valid(index):
                self._list[index.row()] = value
                self.dataChanged.emit(index, index)
                return True
        elif self._is_index_valid(index):
            self._other_data[index.row()][role] = value
            self.dataChanged.emit(index, index)
            return True
        return False

    def setItemData(self, index, data):
        data = dict(data)
        if not data:
            return True  # pragma: no cover

        with signal_blocking(self):
            for role, value in data.items():
                if role == Qt.EditRole and \
                        self._is_index_valid(index):
                    self._list[index.row()] = value
                elif self._is_index_valid(index):
                    self._other_data[index.row()][role] = value

        self.dataChanged.emit(index, index)
        return True

    def flags(self, index):
        if self._is_index_valid(index):
            return self._other_data[index.row()].get("flags", self._flags)
        else:
            return self._flags | Qt.ItemIsDropEnabled

    def insertRows(self, row, count, parent=QModelIndex()):
        """ Insert ``count`` rows at ``row``, the list fill be filled
        with ``None``
        """
        if not parent.isValid():
            self[row:row] = [None] * count
            return True
        else:
            return False

    def removeRows(self, row, count, parent=QModelIndex()):
        """Remove ``count`` rows starting at ``row``
        """
        if not parent.isValid():
            del self[row:row + count]
            self.removed.emit()
            return True
        else:
            return False

    def moveRows(self, sourceParent, sourceRow, count,
                 destinationParent, destinationChild):
        # type: (QModelIndex, int, int, QModelIndex, int) -> bool
        """
        Move `count` rows starting at `sourceRow` to `destinationChild`.

        Reimplemented from QAbstractItemModel.moveRows
        """
        if not self.beginMoveRows(sourceParent, sourceRow, sourceRow + count - 1,
                                  destinationParent, destinationChild):
            return False
        take_slice = slice(sourceRow, sourceRow + count)
        insert_at = destinationChild
        if insert_at > sourceRow:
            insert_at -= count
        items, other = self._list[take_slice], self._other_data[take_slice]
        del self._list[take_slice], self._other_data[take_slice]
        self._list[insert_at:insert_at] = items
        self._other_data[insert_at: insert_at] = other
        self.endMoveRows()
        return True

    def extend(self, iterable):
        list_ = list(iterable)
        count = len(list_)
        if count == 0:
            return
        self.beginInsertRows(QModelIndex(),
                             len(self), len(self) + count - 1)
        self._list.extend(list_)
        self._other_data.extend([_store() for _ in list_])
        self.endInsertRows()

    def append(self, item):
        self.extend([item])

    def insert(self, i, val):
        self.beginInsertRows(QModelIndex(), i, i)
        self._list.insert(i, val)
        self._other_data.insert(i, _store())
        self.endInsertRows()

    def remove(self, val):
        i = self._list.index(val)
        self.__delitem__(i)

    def pop(self, i):
        item = self._list[i]
        self.__delitem__(i)
        return item

    def indexOf(self, value):
        return self._list.index(value)

    def clear(self):
        del self[:]

    def __len__(self):
        return len(self._list)

    def __contains__(self, value):
        return value in self._list

    def __iter__(self):
        return iter(self._list)

    def __getitem__(self, i):
        return self._list[i]

    def __add__(self, iterable):
        new_list = PyListModel(list(self._list),
                               # method parent is overloaded in Model
                               QObject.parent(self),
                               flags=self._flags,
                               list_item_role=self.list_item_role,
                               supportedDropActions=self.supportedDropActions())
        # pylint: disable=protected-access
        new_list._other_data = list(self._other_data)
        new_list.extend(iterable)
        return new_list

    def __iadd__(self, iterable):
        self.extend(iterable)
        return self

    def __delitem__(self, s):
        if isinstance(s, slice):
            start, stop, _ = _as_contiguous_range(s, len(self))
            if not len(self) or start == stop:
                return
            self.beginRemoveRows(QModelIndex(), start, stop - 1)
        else:
            s = operator.index(s)
            s = len(self) + s if s < 0 else s
            self.beginRemoveRows(QModelIndex(), s, s)
        del self._list[s]
        del self._other_data[s]
        self.endRemoveRows()

    def __setitem__(self, s, value):
        if isinstance(s, slice):
            start, stop, step = _as_contiguous_range(s, len(self))
            self.__delitem__(slice(start, stop, step))

            if not isinstance(value, list):
                value = list(value)
            if len(value) == 0:
                return
            separators = [start + i for i, v in enumerate(value) if v is self.Separator]
            self.beginInsertRows(QModelIndex(), start, start + len(value) - 1)
            self._list[start:start] = value
            self._other_data[start:start] = (_store() for _ in value)
            for idx in separators:
                self._other_data[idx]['flags'] = Qt.NoItemFlags
                self._other_data[idx][Qt.AccessibleDescriptionRole] = 'separator'
            self.endInsertRows()
        else:
            s = operator.index(s)
            s = len(self) + s if s < 0 else s
            self._list[s] = value
            self._other_data[s] = _store()
            self.dataChanged.emit(self.index(s), self.index(s))

    def reverse(self):
        self._list.reverse()
        self._other_data.reverse()
        self.dataChanged.emit(self.index(0), self.index(len(self) - 1))

    def sort(self, *args, **kwargs):
        indices = _argsort(self._list, *args, **kwargs)
        lst = [self._list[i] for i in indices]
        other = [self._other_data[i] for i in indices]
        for i, (new_l, new_o) in enumerate(zip(lst, other)):
            self._list[i] = new_l
            self._other_data[i] = new_o
        self.dataChanged.emit(self.index(0), self.index(len(self) - 1))

    def __repr__(self):
        return "PyListModel(%s)" % repr(self._list)

    def __bool__(self):
        return len(self) != 0

    def emitDataChanged(self, indexList):
        if isinstance(indexList, int):
            indexList = [indexList]

        #TODO: group indexes into ranges
        for ind in indexList:
            self.dataChanged.emit(self.index(ind), self.index(ind))

    ###########
    # Drag/drop
    ###########

    def supportedDropActions(self):
        return self._supportedDropActions

    def mimeTypes(self):
        return [self.MIME_TYPE]

    def mimeData(self, indexlist):
        if len(indexlist) <= 0:
            return None

        def itemData(row):
            # type: (int) -> Dict[int, Any]
            if row < len(self._other_data):
                return {key: val for key, val in self._other_data[row].items()
                        if isinstance(key, (int, Qt.ItemDataRole))}
            else:
                return {}  # pragma: no cover

        items = [self[i.row()] for i in indexlist]
        itemdata = [itemData(i.row()) for i in indexlist]
        mime = QMimeData()
        mime.setData(self.MIME_TYPE, b'see properties: _items, _itemdata')
        mime.setProperty('_items', items)
        mime.setProperty('_itemdata', itemdata)
        return mime

    def dropMimeData(self, mime, action, row, column, parent):
        if action == Qt.IgnoreAction:
            return True  # pragma: no cover

        if not mime.hasFormat(self.MIME_TYPE):
            return False  # pragma: no cover

        items = mime.property('_items')
        itemdata = mime.property('_itemdata')

        if not items:
            return False  # pragma: no cover

        if row == -1:
            row = len(self)  # pragma: no cover

        self[row:row] = items
        for i, data in enumerate(itemdata):
            self.setItemData(self.index(row + i), data)
        return True


class ListSingleSelectionModel(QItemSelectionModel):
    """ Item selection model for list item models with single selection.

    Defines signal:
        - selectedIndexChanged(QModelIndex)

    """
    selectedIndexChanged = Signal(QModelIndex)

    def __init__(self, model, parent=None):
        super().__init__(model, parent)
        self.selectionChanged.connect(self.onSelectionChanged)

    def onSelectionChanged(self, new, _):
        index = list(new.indexes())
        if index:
            index = index.pop()
        else:
            index = QModelIndex()

        self.selectedIndexChanged.emit(index)

    def selectedRow(self):
        """ Return QModelIndex of the selected row or invalid if no selection.
        """
        rows = self.selectedRows()
        if rows:
            return rows[0]
        else:
            return QModelIndex()

    def select(self, index, flags=QItemSelectionModel.ClearAndSelect):
        if isinstance(index, int):
            index = self.model().index(index)
        return super().select(self, index, flags)


def select_row(view, row):
    """
    Select a `row` in an item view.
    """
    selmodel = view.selectionModel()
    selmodel.select(view.model().index(row, 0),
                    QItemSelectionModel.ClearAndSelect |
                    QItemSelectionModel.Rows)


class ModelActionsWidget(QWidget):
    def __init__(self, actions=None, parent=None,
                 direction=QBoxLayout.LeftToRight):
        super().__init__(parent)
        self.actions = []
        self.buttons = []
        layout = QBoxLayout(direction)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)
        if actions is not None:
            for action in actions:
                self.addAction(action)
        self.setLayout(layout)

    def actionButton(self, action):
        if isinstance(action, QAction):
            button = QToolButton(self)
            button.setDefaultAction(action)
            return button
        elif isinstance(action, QAbstractButton):
            return action

    def insertAction(self, ind, action, *args):
        button = self.actionButton(action)
        self.layout().insertWidget(ind, button, *args)
        self.buttons.insert(ind, button)
        self.actions.insert(ind, action)
        return button

    def addAction(self, action, *args):
        return self.insertAction(-1, action, *args)
