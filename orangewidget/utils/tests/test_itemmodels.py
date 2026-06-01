# Test methods with long descriptive names can omit docstrings
# pylint: disable=missing-docstring

import unittest
from unittest.mock import patch, Mock

import numpy as np

from AnyQt.QtCore import Qt, QModelIndex, QRect
from AnyQt.QtTest import QSignalSpy
from AnyQt.QtGui import QPalette, QFont

from orangewidget.utils.itemmodels import \
    AbstractSortTableModel, PyTableModel, PyListModel, \
    _argsort, _as_contiguous_range, SeparatedListDelegate, LabelledSeparator


class TestArgsort(unittest.TestCase):
    def test_argsort(self):
        self.assertEqual(_argsort("dacb"), [1, 3, 2, 0])
        self.assertEqual(_argsort("dacb", reverse=True), [0, 2, 3, 1])
        self.assertEqual(_argsort([3, -1, 0, 2], key=abs), [2, 1, 3, 0])
        self.assertEqual(
            _argsort([3, -1, 0, 2], key=abs, reverse=True), [0, 3, 1, 2])
        self.assertEqual(
            _argsort([3, -1, 0, 2],
                     cmp=lambda x, y: (abs(x) > abs(y)) - (abs(x) < abs(y))),
            [2, 1, 3, 0])
        self.assertEqual(
            _argsort([3, -1, 0, 2],
                     cmp=lambda x, y: (abs(x) > abs(y)) - (abs(x) < abs(y)),
                     reverse=True),
            [0, 3, 1, 2])

class TestUtils(unittest.TestCase):
    def test_as_contiguous_range(self):
        self.assertEqual(_as_contiguous_range(slice(1, 8), 20), (1, 8, 1))
        self.assertEqual(_as_contiguous_range(slice(1, 8), 6), (1, 6, 1))
        self.assertEqual(_as_contiguous_range(slice(8, 1, -1), 6), (2, 6, 1))
        self.assertEqual(_as_contiguous_range(slice(8), 6), (0, 6, 1))
        self.assertEqual(_as_contiguous_range(slice(8, None, -1), 6), (0, 6, 1))
        self.assertEqual(_as_contiguous_range(slice(7, None, -1), 9), (0, 8, 1))
        self.assertEqual(_as_contiguous_range(slice(None, None, -1), 9),
                         (0, 9, 1))

class TestPyTableModel(unittest.TestCase):
    def setUp(self):
        self.model = PyTableModel([[1, 4],
                                   [2, 3]])

    def test_init(self):
        self.model = PyTableModel()
        self.assertEqual(self.model.rowCount(), 0)

    def test_rowCount(self):
        self.assertEqual(self.model.rowCount(), 2)
        self.assertEqual(len(self.model), 2)

    def test_columnCount(self):
        self.assertEqual(self.model.columnCount(), 2)

    def test_data(self):
        mi = self.model.index(0, 0)
        self.assertEqual(self.model.data(mi), '1')
        self.assertEqual(self.model.data(mi, Qt.EditRole), 1)

    def test_editable(self):
        editable_model = PyTableModel([[0]], editable=True)
        self.assertFalse(self.model.flags(self.model.index(0, 0)) & Qt.ItemIsEditable)
        self.assertTrue(editable_model.flags(editable_model.index(0, 0)) & Qt.ItemIsEditable)

    def test_sort(self):
        self.model.sort(1)
        self.assertEqual(self.model.index(0, 0).data(Qt.EditRole), 2)

    def test_setHeaderLabels(self):
        self.model.setHorizontalHeaderLabels(['Col 1', 'Col 2'])
        self.assertEqual(self.model.headerData(1, Qt.Horizontal), 'Col 2')
        self.assertEqual(self.model.headerData(1, Qt.Vertical), 2)

    def test_removeRows(self):
        self.model.removeRows(0, 1)
        self.assertEqual(len(self.model), 1)
        self.assertEqual(self.model[0][1], 3)

    def test_removeColumns(self):
        self.model.removeColumns(0, 1)
        self.assertEqual(self.model.columnCount(), 1)
        self.assertEqual(self.model[1][0], 3)

    def test_insertRows(self):
        self.model.insertRows(0, 1)
        self.assertEqual(self.model[1][0], 1)

    def test_insertColumns(self):
        self.model.insertColumns(0, 1)
        self.assertEqual(self.model[0], ['', 1, 4])

    def test_wrap(self):
        self.model.wrap([[0]])
        self.assertEqual(self.model.rowCount(), 1)
        self.assertEqual(self.model.columnCount(), 1)

    def test_init_wrap_empty(self):
        # pylint: disable=protected-access
        t = []
        model = PyTableModel(t)
        self.assertIs(model._table, t)
        t.append([1, 2, 3])
        self.assertEqual(list(model), [[1, 2, 3]])

    def test_clear(self):
        self.model.clear()
        self.assertEqual(self.model.rowCount(), 0)

    def test_append(self):
        self.model.append([5, 6])
        self.assertEqual(self.model[2][1], 6)
        self.assertEqual(self.model.rowCount(), 3)

    def test_extend(self):
        self.model.extend([[5, 6]])
        self.assertEqual(self.model[2][1], 6)
        self.assertEqual(self.model.rowCount(), 3)

    def test_insert(self):
        self.model.insert(0, [5, 6])
        self.assertEqual(self.model[0][1], 6)
        self.assertEqual(self.model.rowCount(), 3)

    def test_remove(self):
        self.model.remove([2, 3])
        self.assertEqual(self.model.rowCount(), 1)

    def test_other_roles(self):
        self.model.append([2, 3])
        self.model.setData(self.model.index(2, 0),
                           Qt.AlignCenter,
                           Qt.TextAlignmentRole)
        del self.model[1]
        self.assertTrue(Qt.AlignCenter &
                        self.model.data(self.model.index(1, 0),
                                        Qt.TextAlignmentRole))

    def test_set_item_signals(self):
        def p(*s):
            return [[x] for x in s]

        def assert_changed(startrow, stoprow, ncolumns):
            start, stop = changed[-1][:2]
            self.assertEqual(start.row(), startrow)
            self.assertEqual(stop.row(), stoprow)
            self.assertEqual(start.column(), 0)
            self.assertEqual(stop.column(), ncolumns)

        self.model.wrap(p(0, 1, 2, 3, 4, 5))
        aboutinserted = QSignalSpy(self.model.rowsAboutToBeInserted)
        inserted = QSignalSpy(self.model.rowsInserted)
        aboutremoved = QSignalSpy(self.model.rowsAboutToBeRemoved)
        removed = QSignalSpy(self.model.rowsRemoved)
        changed = QSignalSpy(self.model.dataChanged)

        # Insert rows
        self.model[2:4] = p(6, 7, 8, 9, 10) + [[11, 2]]
        self.assertEqual(list(self.model), p(0, 1, 6, 7, 8, 9, 10) + [[11, 2]] + p(4, 5))
        self.assertEqual(len(changed), 1)
        assert_changed(2, 3, 1)
        self.assertEqual(aboutinserted[-1][1:], [4, 7])
        self.assertEqual(inserted[-1][1:], [4, 7])
        self.assertEqual(len(aboutremoved), 0)
        self.assertEqual(len(removed), 0)

        # Remove rows
        self.model[2:8] = p(2, 3)
        self.assertEqual(list(self.model), p(0, 1, 2, 3, 4, 5))
        self.assertEqual(len(changed), 2)  # one is from before
        assert_changed(2, 3, 0)
        self.assertEqual(aboutremoved[-1][1:], [4, 7])
        self.assertEqual(removed[-1][1:], [4, 7])
        self.assertEqual(len(inserted), 1)  # from before
        self.assertEqual(len(aboutinserted), 1)  # from before

        # Change rows
        self.model[-5:-3] = p(19, 20)
        self.assertEqual(list(self.model), p(0, 19, 20, 3, 4, 5))
        self.assertEqual(len(changed), 3)  # two are from before
        assert_changed(1, 2, 0)
        self.assertEqual(len(inserted), 1)  # from before
        self.assertEqual(len(aboutinserted), 1)  # from before
        self.assertEqual(len(removed), 1)  # from before
        self.assertEqual(len(aboutremoved), 1)  # from before

        # Insert without change
        self.model[3:3] = p(21, 22)
        self.assertEqual(list(self.model), p(0, 19, 20, 21, 22, 3, 4, 5))
        self.assertEqual(len(changed), 3)  #from before
        self.assertEqual(inserted[-1][1:], [3, 4])
        self.assertEqual(aboutinserted[-1][1:], [3, 4])
        self.assertEqual(len(removed), 1)  # from before
        self.assertEqual(len(aboutremoved), 1)  # from before

        # Remove without change
        self.model[3:5] = []
        self.assertEqual(list(self.model), p(0, 19, 20, 3, 4, 5))
        self.assertEqual(len(changed), 3)  #from before
        self.assertEqual(removed[-1][1:], [3, 4])
        self.assertEqual(aboutremoved[-1][1:], [3, 4])
        self.assertEqual(len(inserted), 2)  # from before
        self.assertEqual(len(aboutinserted), 2)  # from before

        # Remove all
        self.model[:] = []
        self.assertEqual(list(self.model), [])
        self.assertEqual(len(changed), 3)  #from before
        self.assertEqual(removed[-1][1:], [0, 5])
        self.assertEqual(aboutremoved[-1][1:], [0, 5])
        self.assertEqual(len(inserted), 2)  # from before
        self.assertEqual(len(aboutinserted), 2)  # from before

        # Add to empty
        self.model[:] = p(0, 1, 2, 3)
        self.assertEqual(list(self.model), p(0, 1, 2, 3))
        self.assertEqual(len(changed), 3)  #from before
        self.assertEqual(inserted[-1][1:], [0, 3])
        self.assertEqual(inserted[-1][1:], [0, 3])
        self.assertEqual(len(removed), 3)  # from before
        self.assertEqual(len(aboutremoved), 3)  # from before


class TestAbstractSortTableModel(unittest.TestCase):
    def test_sorting(self):
        assert issubclass(PyTableModel, AbstractSortTableModel)
        model = PyTableModel([[1, 4],
                              [2, 2],
                              [3, 3]])
        model.sort(1, Qt.AscendingOrder)
        # mapToSourceRows
        self.assertSequenceEqual(model.mapToSourceRows(...).tolist(), [1, 2, 0])
        self.assertEqual(model.mapToSourceRows(1).tolist(), 2)
        self.assertSequenceEqual(model.mapToSourceRows([1, 2]).tolist(), [2, 0])
        self.assertSequenceEqual(model.mapToSourceRows([]), [])
        self.assertSequenceEqual(model.mapToSourceRows(np.array([], dtype=int)).tolist(), [])
        self.assertRaises(IndexError, model.mapToSourceRows, np.r_[0.])

        # mapFromSourceRows
        self.assertSequenceEqual(model.mapFromSourceRows(...).tolist(), [2, 0, 1])
        self.assertEqual(model.mapFromSourceRows(1).tolist(), 0)
        self.assertSequenceEqual(model.mapFromSourceRows([1, 2]).tolist(), [0, 1])
        self.assertSequenceEqual(model.mapFromSourceRows([]), [])
        self.assertSequenceEqual(model.mapFromSourceRows(np.array([], dtype=int)).tolist(), [])
        self.assertRaises(IndexError, model.mapFromSourceRows, np.r_[0.])

        model.sort(1, Qt.DescendingOrder)
        self.assertSequenceEqual(model.mapToSourceRows(...).tolist(), [0, 2, 1])
        self.assertSequenceEqual(model.mapFromSourceRows(...).tolist(), [0, 2, 1])

    def test_stable_descending_sort(self):
        def assert_indices_equal(indices):
            self.assertSequenceEqual(model.mapToSourceRows(...).tolist(),
                                     indices)
        model = PyTableModel([[1, 4],
                              [2, 2],
                              [2, 3],
                              [2, 2],
                              [3, 3]])
        model.sort(0, Qt.DescendingOrder)
        assert_indices_equal([4, 1, 2, 3, 0])
        model.sort(1, Qt.DescendingOrder)
        assert_indices_equal([0, 4, 2, 1, 3])

    def test_sorting_fallback(self):
        class TableModel(PyTableModel):
            def sortColumnData(self, column):
                raise NotImplementedError

        model = TableModel([[1, 4],
                            [2, 2],
                            [3, 3]])
        model.sort(1, Qt.DescendingOrder)
        self.assertSequenceEqual(model.mapToSourceRows(...).tolist(), [0, 2, 1])
        model.sort(1, Qt.AscendingOrder)
        self.assertSequenceEqual(model.mapToSourceRows(...).tolist(), [1, 2, 0])

    def test_sorting_2d(self):
        class Model(AbstractSortTableModel):
            def rowCount(self):
                return 3

            def sortColumnData(self, _):
                return np.array([[4, 6, 2],
                                 [3, 3, 3],
                                 [4, 6, 1]])
        model = Model()
        model.sort(0)
        self.assertEqual(model.mapToSourceRows(...).tolist(), [1, 2, 0])

    def test_setSortIndices(self):
        model = AbstractSortTableModel()
        model.rowCount = lambda: 5
        spy_about = QSignalSpy(model.layoutAboutToBeChanged)
        spy_changed = QSignalSpy(model.layoutChanged)

        model.setSortIndices([4, 0, 1, 3, 2])
        self.assertEqual(len(spy_about), 1)
        self.assertEqual(len(spy_changed), 1)
        self.assertEqual(model.mapFromSourceRows(...).tolist(), [1, 2, 4, 3, 0])
        self.assertEqual(model.mapToSourceRows(...).tolist(), [4, 0, 1, 3, 2])

        rows = [0, 1, 2, 3, 4]
        model.setSortIndices(None)
        self.assertEqual(len(spy_about), 2)
        self.assertEqual(len(spy_changed), 2)
        self.assertEqual(model.mapFromSourceRows(...), ...)
        self.assertEqual(model.mapToSourceRows(...), ...)
        self.assertEqual(model.mapFromSourceRows(rows), rows)
        self.assertEqual(model.mapToSourceRows(rows), rows)


# Tests test _is_index_valid and access model._other_data. The latter tests
# implementation, but it would be cumbersome and less readable to test function
# pylint: disable=protected-access
class TestPyListModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = PyListModel([1, 2, 3, 4])

    def test_wrap(self):
        model = PyListModel()
        s = [1, 2]
        model.wrap(s)
        self.assertSequenceEqual(model, [1, 2])
        model.append(3)
        self.assertEqual(s, [1, 2, 3])
        self.assertEqual(len(model._other_data), 3)

        s.append(5)
        self.assertRaises(RuntimeError, model._is_index_valid, 0)

    def test_is_index_valid(self):
        self.assertTrue(self.model._is_index_valid(0))
        self.assertTrue(self.model._is_index_valid(2))
        self.assertTrue(self.model._is_index_valid(-1))
        self.assertTrue(self.model._is_index_valid(-4))

        self.assertFalse(self.model._is_index_valid(-5))
        self.assertFalse(self.model._is_index_valid(5))

    def test_index(self):
        index = self.model.index(2, 0)
        self.assertTrue(index.isValid())
        self.assertEqual(index.row(), 2)
        self.assertEqual(index.column(), 0)

        self.assertFalse(self.model.index(5, 0).isValid())
        self.assertFalse(self.model.index(-5, 0).isValid())
        self.assertFalse(self.model.index(0, 1).isValid())

    def test_headerData(self):
        self.assertEqual(self.model.headerData(3, Qt.Vertical), "3")

    def test_rowCount(self):
        self.assertEqual(self.model.rowCount(), len(self.model))
        self.assertEqual(self.model.rowCount(self.model.index(2, 0)), 0)

    def test_columnCount(self):
        self.assertEqual(self.model.columnCount(), 1)
        self.assertEqual(self.model.columnCount(self.model.index(2, 0)), 0)

    def test_indexOf(self):
        self.assertEqual(self.model.indexOf(3), 2)

    def test_data(self):
        mi = self.model.index(2)
        self.assertEqual(self.model.data(mi), 3)
        self.assertEqual(self.model.data(mi, Qt.EditRole), 3)

        self.assertIsNone(self.model.data(self.model.index(5)))

    def test_itemData(self):
        model = PyListModel([1, 2, 3, 4])
        mi = model.index(2)
        model.setItemData(mi, {Qt.ToolTipRole: "foo"})
        self.assertEqual(model.itemData(mi)[Qt.ToolTipRole], "foo")

        self.assertEqual(model.itemData(model.index(5)), {})

    def test_mimeData(self):
        model = PyListModel([1, 2])
        model._other_data[:] = [{Qt.UserRole: "a"}, {}]
        mime = model.mimeData([model.index(0), model.index(1)])
        self.assertTrue(mime.hasFormat(PyListModel.MIME_TYPE))

    def test_dropMimeData(self):
        model = PyListModel([1, 2])
        model.setData(model.index(0), "a", Qt.UserRole)
        mime = model.mimeData([model.index(0)])
        self.assertTrue(
            model.dropMimeData(mime, Qt.CopyAction, 2, -1, model.index(-1, -1))
        )
        self.assertEqual(len(model), 3)
        self.assertEqual(
            model.itemData(model.index(2)),
            {Qt.DisplayRole: 1, Qt.EditRole: 1, Qt.UserRole: "a"}
        )

    def test_parent(self):
        self.assertFalse(self.model.parent(self.model.index(2)).isValid())

    def test_set_data(self):
        model = PyListModel([1, 2, 3, 4])
        model.setData(model.index(0), None, Qt.EditRole)
        self.assertIs(model.data(model.index(0), Qt.EditRole), None)

        model.setData(model.index(1), "This is two", Qt.ToolTipRole)
        self.assertEqual(model.data(model.index(1), Qt.ToolTipRole),
                         "This is two",)

        self.assertFalse(model.setData(model.index(5), "foo"))

    def test_setitem(self):
        model = PyListModel([1, 2, 3, 4])
        model[1] = 42
        self.assertSequenceEqual(model, [1, 42, 3, 4])
        model[-1] = 42
        self.assertSequenceEqual(model, [1, 42, 3, 42])

        with self.assertRaises(IndexError):
            model[4]  # pylint: disable=pointless-statement

        with self.assertRaises(IndexError):
            model[-5]  # pylint: disable=pointless-statement

        model = PyListModel([1, 2, 3, 4])
        model[0:0] = [-1, 0]
        self.assertSequenceEqual(model, [-1, 0, 1, 2, 3, 4])

        model = PyListModel([1, 2, 3, 4])
        model[len(model):len(model)] = [5, 6]
        self.assertSequenceEqual(model, [1, 2, 3, 4, 5, 6])

        model = PyListModel([1, 2, 3, 4])
        model[0:2] = (-1, -2)
        self.assertSequenceEqual(model, [-1, -2, 3, 4])

        model = PyListModel([1, 2, 3, 4])
        model[-2:] = [-3, -4]
        self.assertSequenceEqual(model, [1, 2, -3, -4])

        model = PyListModel([1, 2, 3, 4])
        with self.assertRaises(IndexError):
            # non unit strides currently not supported
            model[0:-1:2] = [3, 3]

    def test_getitem(self):
        self.assertEqual(self.model[0], 1)
        self.assertEqual(self.model[2], 3)
        self.assertEqual(self.model[-1], 4)
        self.assertEqual(self.model[-4], 1)

        with self.assertRaises(IndexError):
            self.model[4]    # pylint: disable=pointless-statement

        with self.assertRaises(IndexError):
            self.model[-5]  # pylint: disable=pointless-statement

    def test_delitem(self):
        model = PyListModel([1, 2, 3, 4])
        model._other_data = list("abcd")
        del model[1]
        self.assertSequenceEqual(model, [1, 3, 4])
        self.assertSequenceEqual(model._other_data, "acd")

        model = PyListModel([1, 2, 3, 4])
        model._other_data = list("abcd")
        del model[1:3]

        self.assertSequenceEqual(model, [1, 4])
        self.assertSequenceEqual(model._other_data, "ad")

        model = PyListModel([1, 2, 3, 4])
        model._other_data = list("abcd")
        del model[:]
        self.assertSequenceEqual(model, [])
        self.assertEqual(len(model._other_data), 0)

        model = PyListModel([1, 2, 3, 4])
        with self.assertRaises(IndexError):
            # non unit strides currently not supported
            del model[0:-1:2]
        self.assertEqual(len(model), len(model._other_data))

    def test_add(self):
        model2 = self.model + [5, 6]
        self.assertSequenceEqual(model2, [1, 2, 3, 4, 5, 6])
        self.assertEqual(len(model2), len(model2._other_data))

    def test_iadd(self):
        model = PyListModel([1, 2, 3, 4])
        model += [5, 6]
        self.assertSequenceEqual(model, [1, 2, 3, 4, 5, 6])
        self.assertEqual(len(model), len(model._other_data))

    def test_list_specials(self):
        # Essentially tested in other tests, but let's do it explicitly, too
        # __len__
        self.assertEqual(len(self.model), 4)

        # __contains__
        self.assertTrue(2 in self.model)
        self.assertFalse(5 in self.model)

        # __iter__
        self.assertSequenceEqual(self.model, [1, 2, 3, 4])

        # __bool__
        self.assertTrue(bool(self.model))
        self.assertFalse(bool(PyListModel()))

    def test_insert_delete_rows(self):
        model = PyListModel([1, 2, 3, 4])
        success = model.insertRows(0, 3)

        self.assertIs(success, True)
        self.assertSequenceEqual(model, [None, None, None, 1, 2, 3, 4])

        success = model.removeRows(3, 4)
        self.assertIs(success, True)
        self.assertSequenceEqual(model, [None, None, None])

        self.assertFalse(model.insertRows(0, 1, model.index(0)))
        self.assertFalse(model.removeRows(0, 1, model.index(0)))

    def test_extend(self):
        model = PyListModel([])
        model.extend([1, 2, 3, 4])
        self.assertSequenceEqual(model, [1, 2, 3, 4])

        model.extend([5, 6])
        self.assertSequenceEqual(model, [1, 2, 3, 4, 5, 6])

        self.assertEqual(len(model), len(model._other_data))

    def test_append(self):
        model = PyListModel([])
        model.append(1)
        self.assertSequenceEqual(model, [1])

        model.append(2)
        self.assertSequenceEqual(model, [1, 2])

        self.assertEqual(len(model), len(model._other_data))

    def test_insert(self):
        model = PyListModel()
        model.insert(0, 1)
        self.assertSequenceEqual(model, [1])
        self.assertEqual(len(model._other_data), 1)
        model._other_data = ["a"]

        model.insert(0, 2)
        self.assertSequenceEqual(model, [2, 1])
        self.assertEqual(model._other_data[1], "a")
        self.assertNotEqual(model._other_data[0], "a")
        model._other_data[0] = "b"

        model.insert(1, 3)
        self.assertSequenceEqual(model, [2, 3, 1])
        self.assertEqual(model._other_data[0], "b")
        self.assertEqual(model._other_data[2], "a")
        self.assertNotEqual(model._other_data[1], "b")
        self.assertNotEqual(model._other_data[1], "a")
        model._other_data[1] = "c"

        model.insert(3, 4)
        self.assertSequenceEqual(model, [2, 3, 1, 4])
        self.assertSequenceEqual(model._other_data[:3], ["b", "c", "a"])
        model._other_data[3] = "d"

        model.insert(-1, 5)
        self.assertSequenceEqual(model, [2, 3, 1, 5, 4])
        self.assertSequenceEqual(model._other_data[:3], ["b", "c", "a"])
        self.assertEqual(model._other_data[4], "d")
        self.assertEqual(len(model), len(model._other_data))

    def test_remove(self):
        model = PyListModel([1, 2, 3, 2, 4])
        model._other_data = list("abcde")
        model.remove(2)
        self.assertSequenceEqual(model, [1, 3, 2, 4])
        self.assertSequenceEqual(model._other_data, "acde")

    def test_pop(self):
        model = PyListModel([1, 2, 3, 2, 4])
        model._other_data = list("abcde")
        model.pop(1)
        self.assertSequenceEqual(model, [1, 3, 2, 4])
        self.assertSequenceEqual(model._other_data, "acde")

    def test_clear(self):
        model = PyListModel([1, 2, 3, 2, 4])
        model.clear()
        self.assertSequenceEqual(model, [])
        self.assertEqual(len(model), len(model._other_data))

        model.clear()
        self.assertSequenceEqual(model, [])
        self.assertEqual(len(model), len(model._other_data))

    def test_reverse(self):
        model = PyListModel([1, 2, 3, 4])
        model._other_data = list("abcd")
        model.reverse()
        self.assertSequenceEqual(model, [4, 3, 2, 1])
        self.assertSequenceEqual(model._other_data, "dcba")

    def test_sort(self):
        model = PyListModel([3, 1, 4, 2])
        model._other_data = list("abcd")
        model.sort()
        self.assertSequenceEqual(model, [1, 2, 3, 4])
        self.assertSequenceEqual(model._other_data, "bdac")

    def test_moveRows(self):
        model = PyListModel([1, 2, 3, 4])
        for i in range(model.rowCount()):
            model.setData(model.index(i), str(i + 1), Qt.UserRole)

        def modeldata(role):
            return [model.index(i).data(role)
                    for i in range(model.rowCount())]

        def userdata():
            return modeldata(Qt.UserRole)

        def editdata():
            return modeldata(Qt.EditRole)

        r = model.moveRows(QModelIndex(), 1, 1, QModelIndex(), 0)
        self.assertIs(r, True)
        self.assertSequenceEqual(editdata(), [2, 1, 3, 4])
        self.assertSequenceEqual(userdata(), ["2", "1", "3", "4"])
        r = model.moveRows(QModelIndex(), 1, 2, QModelIndex(), 4)
        self.assertIs(r, True)
        self.assertSequenceEqual(editdata(), [2, 4, 1, 3])
        self.assertSequenceEqual(userdata(), ["2", "4", "1", "3"])
        r = model.moveRows(QModelIndex(), 3, 1, QModelIndex(), 0)
        self.assertIs(r, True)
        self.assertSequenceEqual(editdata(), [3, 2, 4, 1])
        self.assertSequenceEqual(userdata(), ["3", "2", "4", "1"])
        r = model.moveRows(QModelIndex(), 2, 1, QModelIndex(), 2)
        self.assertIs(r, False)
        model = PyListModel([])
        r = model.moveRows(QModelIndex(), 0, 0, QModelIndex(), 0)
        self.assertIs(r, False)

    def test_separator(self):
        model = PyListModel([1, PyListModel.Separator, 2])
        model.append(model.Separator)
        model += [1, model.Separator]
        model.extend([1, model.Separator])
        for i in range(len(model)):
            if i % 2:
                midx = model.index(i)
                self.assertIs(midx.data(Qt.DisplayRole), None)
                self.assertIs(midx.data(Qt.EditRole), model.Separator)
                self.assertEqual(midx.data(Qt.AccessibleDescriptionRole), "separator")
                self.assertEqual(midx.flags(), Qt.NoItemFlags)


class TestSeparatedListDelegate(unittest.TestCase):
    @patch("AnyQt.QtWidgets.QStyledItemDelegate.paint")
    def test_paint(self, _):
        delegate = SeparatedListDelegate()
        painter = Mock()
        font = QFont()
        font.setPointSizeF(10)
        painter.font = lambda: font
        option = Mock()
        option.palette = QPalette()
        option.rect = QRect(10, 20, 50, 5)
        index = Mock()

        index.data = Mock(return_value="foo")
        delegate.paint(painter, option, index)
        painter.drawText.assert_not_called()
        painter.drawLine.assert_not_called()

        index.data = Mock(return_value=LabelledSeparator())
        delegate.paint(painter, option, index)
        painter.drawLine.assert_called_with(10, 22, 60, 22)
        painter.drawLine.reset_mock()
        painter.drawText.assert_not_called()

        index.data = Mock(return_value=LabelledSeparator("bar"))
        delegate.paint(painter, option, index)
        painter.drawLine.assert_called()
        painter.drawText.assert_called_with(option.rect, Qt.AlignCenter, "bar")


if __name__ == "__main__":
    unittest.main()
