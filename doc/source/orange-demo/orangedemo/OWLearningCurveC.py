import logging
from functools import reduce, partial
from typing import List, Optional, Sequence

import numpy

from AnyQt.QtWidgets import QTableWidget, QTableWidgetItem
from AnyQt.QtCore import QThread, pyqtSlot

import Orange.data
import Orange.classification
import Orange.evaluation

from Orange.classification import Learner
from Orange.data import Table
from Orange.evaluation.testing import Results

from orangewidget import gui, settings
from orangewidget.utils.widgetpreview import WidgetPreview
from orangewidget.widget import OWBaseWidget, Input, MultiInput

# [start-snippet-1]
import concurrent.futures
from orangewidget.utils.concurrent import (
    FutureWatcher, methodinvoke
)
# [end-snippet-1]


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


# [start-snippet-2]
class Task:
    """
    A class that will hold the state for an learner evaluation.
    """
    #: A concurrent.futures.Future with our (eventual) results.
    #: The OWLearningCurveC class must fill this field
    future = ...       # type: concurrent.futures.Future

    #: FutureWatcher. Likewise this will be filled by OWLearningCurveC
    watcher = ...      # type: FutureWatcher

    #: True if this evaluation has been cancelled. The OWLearningCurveC
    #: will setup the task execution environment in such a way that this
    #: field will be checked periodically in the worker thread and cancel
    #: the computation if so required. In a sense this is the only
    #: communication channel in the direction from the OWLearningCurve to the
    #: worker thread
    cancelled = False  # type: bool

    def cancel(self):
        """
        Cancel the task.

        Set the `cancelled` field to True and block until the future is done.
        """
        # set cancelled state
        self.cancelled = True
        # cancel the future. Note this succeeds only if the execution has
        # not yet started (see `concurrent.futures.Future.cancel`) ..
        self.future.cancel()
        # ... and wait until computation finishes
        concurrent.futures.wait([self.future])
# [end-snippet-2]


class OWLearningCurveC(OWBaseWidget):
    name = "Learning Curve (C)"
    description = ("Takes a dataset and a set of learners and shows a "
                   "learning curve in a table")
    icon = "icons/LearningCurve.svg"
    priority = 1010

    class Inputs:
        data = Input("Data", Table, default=True)
        test_data = Input("Test Data", Table)
        learner = MultiInput("Learner", Learner)

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

        # [start-snippet-3]
        #: The current evaluating task (if any)
        self._task = None   # type: Optional[Task]
        #: An executor we use to submit learner evaluations into a thread pool
        self._executor = concurrent.futures.ThreadPoolExecutor()
        # [end-snippet-3]

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
        # Clear all results/scores
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

# [start-snippet-4]
    def handleNewSignals(self):
        if len(self.learners):
            self.infob.setText("%d learners on input." % len(self.learners))
        else:
            self.infob.setText("No learners.")

        self.commitBtn.setEnabled(len(self.learners))
        self._update()
# [end-snippet-4]

    def _invalidate_curves(self):
        if self.data is not None:
            self._update_curve_points()
        self._update_table()

    def _invalidate_results(self):
        for item in self.learners:
            item.results = None
            item.curve = None
        self._update()

# [start-snippet-5]
    def _update(self):
        if self._task is not None:
            # First make sure any pending tasks are cancelled.
            self.cancel()
        assert self._task is None

        if self.data is None:
            return
        # collect all learners for which results have not yet been computed
        need_update = [(i, item) for (i, item) in enumerate(self.learners)
                       if item.results is None]
        if not need_update:
            self._update_curve_points()
            self._update_table()
            return
# [end-snippet-5]
# [start-snippet-6]
        learners = [item.learner for _, item in need_update]
        # setup the learner evaluations as partial function capturing
        # the necessary arguments.
        if self.testdata is None:
            learning_curve_func = partial(
                learning_curve,
                learners, self.data, folds=self.folds,
                proportions=self.curvePoints,
            )
        else:
            learning_curve_func = partial(
                learning_curve_with_test_data,
                learners, self.data, self.testdata, times=self.folds,
                proportions=self.curvePoints,
            )
# [end-snippet-6]
# [start-snippet-7]
        # setup the task state
        self._task = task = Task()
        # The learning_curve[_with_test_data] also takes a callback function
        # to report the progress. We instrument this callback to both invoke
        # the appropriate slots on this widget for reporting the progress
        # (in a thread safe manner) and to implement cooperative cancellation.
        set_progress = methodinvoke(self, "setProgressValue", (float,))

        def callback(finished):
            # check if the task has been cancelled and raise an exception
            # from within. This 'strategy' can only be used with code that
            # properly cleans up after itself in the case of an exception
            # (does not leave any global locks, opened file descriptors, ...)
            if task.cancelled:
                raise KeyboardInterrupt()
            set_progress(finished * 100)

        # capture the callback in the partial function
        learning_curve_func = partial(learning_curve_func, callback=callback)
# [end-snippet-7]
# [start-snippet-8]
        self.progressBarInit()
        # Submit the evaluation function to the executor and fill in the
        # task with the resultant Future.
        task.future = self._executor.submit(learning_curve_func)
        # Setup the FutureWatcher to notify us of completion
        task.watcher = FutureWatcher(task.future)
        # by using FutureWatcher we ensure `_task_finished` slot will be
        # called from the main GUI thread by the Qt's event loop
        task.watcher.done.connect(self._task_finished)
# [end-snippet-8]

# [start-snippet-progress]
    @pyqtSlot(float)
    def setProgressValue(self, value):
        assert self.thread() is QThread.currentThread()
        self.progressBarSet(value)
# [end-snippet-progress]

# [start-snippet-9]
    @pyqtSlot(concurrent.futures.Future)
    def _task_finished(self, f):
        """
        Parameters
        ----------
        f : Future
            The future instance holding the result of learner evaluation.
        """
        assert self.thread() is QThread.currentThread()
        assert self._task is not None
        assert self._task.future is f
        assert f.done()

        self._task = None
        self.progressBarFinished()

        try:
            results = f.result()  # type: List[Results]
        except Exception as ex:
            # Log the exception with a traceback
            log = logging.getLogger()
            log.exception(__name__, exc_info=True)
            self.error("Exception occurred during evaluation: {!r}"
                       .format(ex))
            # clear all results
            for item in self.learners:
                item.results = None
        else:
            # split the combined result into per learner/model results ...
            results = [list(Results.split_by_model(p_results))
                       for p_results in results]  # type: List[List[Results]]
            assert all(len(r.learners) == 1 for r1 in results for r in r1)
            assert len(results) == len(self.curvePoints)

            learners = [r.learners[0] for r in results[0]]
            # map learner back to LearnerData instance
            data_by_learner = {item.learner: item for item in self.learners}
            # ... and update self.results
            for i, learner in enumerate(learners):
                item = data_by_learner[learner]
                item.results = [p_results[i] for p_results in results]
        # update the display
        self._update_curve_points()
        self._update_table()
# [end-snippet-9]

# [start-snippet-10]
    def cancel(self):
        """
        Cancel the current task (if any).
        """
        if self._task is not None:
            self._task.cancel()
            assert self._task.future.done()
            # disconnect the `_task_finished` slot
            self._task.watcher.done.disconnect(self._task_finished)
            self._task = None
            self.progressBarFinished()
# [end-snippet-10]

# [start-snippet-11]
    def onDeleteWidget(self):
        self.cancel()
        super().onDeleteWidget()
# [end-snippet-11]

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
    WidgetPreview(OWLearningCurveC).run()
