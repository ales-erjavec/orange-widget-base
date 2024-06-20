import numpy as np
import pyqtgraph as pg

from orangewidget.tests.base import GuiTest
from orangewidget.utils.matplotlib_export import (
    scatterplot_code, numpy_repr, compress_if_all_same, numpy_repr_int
)


def add_intro(a):
    r = "import matplotlib.pyplot as plt\n" + \
        "from numpy import array\n" + \
        "plt.clf()"
    return r + a


class TestScatterPlot(GuiTest):
    def test_scatterplot_simple(self):
        plotWidget = pg.PlotWidget(background="w")
        scatterplot = pg.ScatterPlotItem()
        scatterplot.setData(
            x=np.array([1., 2, 3]),
            y=np.array([3., 2, 1]),
            size=np.array([1., 1, 1])
        )
        plotWidget.addItem(scatterplot)
        code = scatterplot_code(scatterplot)
        self.assertIn("plt.scatter", code)
        exec(add_intro(code), {})

    def test_utils(self):
        a = np.array([1.5, 2.5])
        self.assertIn("1.5, 2.5", numpy_repr(a))
        a = np.array([1, 1])
        v = compress_if_all_same(a)
        self.assertEqual(v, 1)
        self.assertEqual(repr(v), "1")
        self.assertIs(type(v), int)
        a = np.array([1, 2], dtype=int)
        v = numpy_repr_int(a)
        self.assertIn("1, 2", v)
