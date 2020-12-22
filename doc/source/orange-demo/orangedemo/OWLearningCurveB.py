from functools import reduce
from typing import List, Optional, Sequence

import numpy

from AnyQt.QtWidgets import QTableWidget, QTableWidgetItem

import Orange.data
import Orange.classification
import Orange.evaluation

from Orange.classification import Learner
from Orange.data import Table
from Orange.evaluation.testing import Results

from orangewidget import gui, settings
from orangewidget.utils.widgetpreview import WidgetPreview
from orangewidget.widget import OWBaseWidget, Input, MultiInput


class LearnerData:
    def __init__(
            self,
            learner: Learner,
            results: Optional[Results] = None,
            curve: Optional[Sequence[float]] = None,
    ) -> None:
        self.learner = learner
        self.results = results
        self.curve = curve


class OWLearningCurveB(OWBaseWidget):
    name = "Learning Curve (B)"
    description = ("Takes a dataset and a set of learners and shows a "
                   "learning curve in a table")
    icon = "icons/LearningCurve.svg"
    priority = 1010

# [start-snippet-1]
    class Inputs:
        data = Input("Data", Table, default=True)
        test_data = Input("Test Data", Table)
        learner = MultiInput("Learner", Learner)
# [end-snippet-1]

    #: cross validation folds
    folds = settings.Setting(5)
    #: points in the learning curve
    steps = settings.Setting(10)
    #: index of the selected scoring function
    scoringF = settings.Setting(0)
    #: compute curve on any change of parameters
    commitOnChange = settings.Setting(True)

    def __init__(self):
        super().__init__()

        # sets self.curvePoints, self.steps equidistant points from
        # 1/self.steps to 1
        self.updateCurvePoints()

        self.scoring = [
            ("Classification Accuracy", Orange.evaluation.scoring.CA),
            ("AUC", Orange.evaluation.scoring.AUC),
            ("Precision", Orange.evaluation.scoring.Precision),
            ("Recall", Orange.evaluation.scoring.Recall)
        ]
        #: Input data on which to construct the learning curve
        self.data = None
        #: Optional test data
        self.testdata = None
        #: LearnerData for each learner input
        self.learners: List[LearnerData] = []

        # GUI
        box = gui.widgetBox(self.controlArea, "Info")
        self.infoa = gui.widgetLabel(box, 'No data on input.')
        self.infob = gui.widgetLabel(box, 'No learners.')

        gui.separator(self.controlArea)

        box = gui.widgetBox(self.controlArea, "Evaluation Scores")
        gui.comboBox(box, self, "scoringF",
                     items=[x[0] for x in self.scoring],
                     callback=self._invalidate_curves)

        gui.separator(self.controlArea)

        box = gui.widgetBox(self.controlArea, "Options")
        gui.spin(box, self, 'folds', 2, 100, step=1,
                 label='Cross validation folds:  ', keyboardTracking=False,
                 callback=lambda:
                 self._invalidate_results() if self.commitOnChange else None)
        gui.spin(box, self, 'steps', 2, 100, step=1,
                 label='Learning curve points:  ', keyboardTracking=False,
                 callback=[self.updateCurvePoints,
                           lambda: self._invalidate_results() if self.commitOnChange else None])
        gui.checkBox(box, self, 'commitOnChange', 'Apply setting on any change')
        self.commitBtn = gui.button(box, self, "Apply Setting",
                                    callback=self._invalidate_results,
                                    disabled=True)

        gui.rubber(self.controlArea)

        # table widget
        self.table = gui.table(self.mainArea,
                               selectionMode=QTableWidget.NoSelection)

    ##########################################################################
    # slots: handle input signals

    @Inputs.data
    def set_dataset(self, data):
        """Set the input train dataset."""
        # Clear all results/scores for all learner inputs
        for item in self.learners:
            item.results = None
            item.curve = None

        self.data = data

        if data is not None:
            self.infoa.setText('%d instances in input dataset' % len(data))
        else:
            self.infoa.setText('No data on input.')

        self.commitBtn.setEnabled(self.data is not None)

    @Inputs.test_data
    def set_testdataset(self, testdata):
        """Set a separate test dataset."""
        # Clear all results/scores for all learner inputs
        for item in self.learners:
            item.results = None
            item.curve = None

        self.testdata = testdata

    @Inputs.learner
    def set_learner(self, index: int, learner):
        """Set the input learner at index"""
        # update/replace a learner on a previously connected link
        item = self.learners[index]
        item.learner = learner
        item.results = None
        item.curve = None

    @Inputs.learner.insert
    def insert_learner(self, index, learner):
        """Insert a learner at index"""
        self.learners.insert(index, LearnerData(learner, None, None))

    @Inputs.learner.remove
    def remove_learner(self, index):
        """"Remove a learner at index"""
        # remove a learner and corresponding results
        del self.learners[index]

    def handleNewSignals(self):
        if len(self.learners):
            self.infob.setText("%d learners on input." % len(self.learners))
        else:
            self.infob.setText("No learners.")

        self.commitBtn.setEnabled(len(self.learners))

        if self.data is not None:
            self._update()
            self._update_curve_points()
        self._update_table()

    def _invalidate_curves(self):
        if self.data is not None:
            self._update_curve_points()
        self._update_table()

    def _invalidate_results(self):
        for item in self.learners:
            item.results = None
            item.curve = None

        if self.data is not None:
            self._update()
            self._update_curve_points()
        self._update_table()

    def _update(self):
        assert self.data is not None
        # collect all learners for which results have not yet been computed
        need_update = [(i, item) for (i, item) in enumerate(self.learners)
                       if item.results is None]
        if not need_update:
            return

        learners = [item.learner for _, item in need_update]

        if self.testdata is None:
            # compute the learning curve result for all learners in one go
            results = learning_curve(
                learners, self.data, folds=self.folds,
                proportions=self.curvePoints,
            )
        else:
            results = learning_curve_with_test_data(
                learners, self.data, self.testdata, times=self.folds,
                proportions=self.curvePoints,
            )
        # split the combined result into per learner/model results
        results = [list(Results.split_by_model(p_results))
                   for p_results in results]

        for i, (_, item) in enumerate(need_update):
            item.results = [p_results[i] for p_results in results]

    def _update_curve_points(self):
        scoref = self.scoring[self.scoringF][1]
        for item in self.learners:
            item.curve = [scoref(x)[0] for x in item.results]

    def _update_table(self):
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.curvePoints))
        self.table.setColumnCount(len(self.learners))

        self.table.setHorizontalHeaderLabels(
            [item.learner.name for item in self.learners])
        self.table.setVerticalHeaderLabels(
            ["{:.2f}".format(p) for p in self.curvePoints])

        if self.data is None:
            return

        for column, item in enumerate(self.learners):
            for row, point in enumerate(item.curve):
                self.table.setItem(
                    row, column, QTableWidgetItem("{:.5f}".format(point)))

        for i in range(len(self.learners)):
            sh = self.table.sizeHintForColumn(i)
            cwidth = self.table.columnWidth(i)
            self.table.setColumnWidth(i, max(sh, cwidth))

    def updateCurvePoints(self):
        self.curvePoints = [(x + 1.)/self.steps for x in range(self.steps)]


def learning_curve(learners, data, folds=10, proportions=None,
                   random_state=None, callback=None):

    if proportions is None:
        proportions = numpy.linspace(0.0, 1.0, 10 + 1, endpoint=True)[1:]

    def select_proportion_preproc(data, p, rstate=None):
        assert 0 < p <= 1
        rstate = numpy.random.RandomState(None) if rstate is None else rstate
        indices = rstate.permutation(len(data))
        n = int(numpy.ceil(len(data) * p))
        return data[indices[:n]]

    if callback is not None:
        parts_count = len(proportions)
        callback_wrapped = lambda part: \
            lambda value: callback(value / parts_count + part / parts_count)
    else:
        callback_wrapped = lambda part: None

    results = [
        Orange.evaluation.CrossValidation(
            data, learners, k=folds,
            preprocessor=lambda data, p=p: select_proportion_preproc(data, p),
            callback=callback_wrapped(i))
        for i, p in enumerate(proportions)
    ]
    return results


def learning_curve_with_test_data(learners, traindata, testdata, times=10,
                                  proportions=None, random_state=None,
                                  callback=None):
    if proportions is None:
        proportions = numpy.linspace(0.0, 1.0, 10 + 1, endpoint=True)[1:]

    def select_proportion_preproc(data, p, rstate=None):
        assert 0 < p <= 1
        rstate = numpy.random.RandomState(None) if rstate is None else rstate
        indices = rstate.permutation(len(data))
        n = int(numpy.ceil(len(data) * p))
        return data[indices[:n]]

    if callback is not None:
        parts_count = len(proportions) * times
        callback_wrapped = lambda part: \
            lambda value: callback(value / parts_count + part / parts_count)
    else:
        callback_wrapped = lambda part: None

    results = [
        [Orange.evaluation.TestOnTestData(
            traindata, testdata, learners,
            preprocessor=lambda data, p=p: select_proportion_preproc(data, p),
            callback=callback_wrapped(i * times + t))
         for t in range(times)]
        for i, p in enumerate(proportions)]
    results = [reduce(results_add, res, Orange.evaluation.Results())
               for res in results]
    return results


def results_add(x, y):
    def is_empty(res):
        return (getattr(res, "models", None) is None
                and getattr(res, "row_indices", None) is None)

    if is_empty(x):
        return y
    elif is_empty(y):
        return x

    assert x.data is y.data
    assert x.domain is y.domain
    assert x.predicted.shape[0] == y.predicted.shape[0]

    assert len(x.learners) == len(y.learners)
    assert all(xl is yl for xl, yl in zip(x.learners, y.learners))

    row_indices = numpy.hstack((x.row_indices, y.row_indices))
    predicted = numpy.hstack((x.predicted, y.predicted))
    actual = numpy.hstack((x.actual, y.actual))

    xprob = getattr(x, "probabilities", None)
    yprob = getattr(y, "probabilities", None)

    if xprob is None and yprob is None:
        prob = None
    elif xprob is not None and yprob is not None:
        prob = numpy.concatenate((xprob, yprob), axis=1)
    else:
        raise ValueError()

    res = Orange.evaluation.Results()
    res.data = x.data
    res.domain = x.domain
    res.learners = x.learners
    res.row_indices = row_indices
    res.actual = actual
    res.predicted = predicted
    res.folds = None
    if prob is not None:
        res.probabilities = prob

    if x.models is not None and y.models is not None:
        res.models = [xm + ym for xm, ym in zip(x.models, y.models)]

    nmodels = predicted.shape[0]
    xfailed = getattr(x, "failed", None) or [False] * nmodels
    yfailed = getattr(y, "failed", None) or [False] * nmodels
    assert len(xfailed) == len(yfailed)
    res.failed = [xe or ye for xe, ye in zip(xfailed, yfailed)]

    return res


if __name__ == "__main__":
    WidgetPreview(OWLearningCurveB).run()
