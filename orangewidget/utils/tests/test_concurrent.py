import unittest
import unittest.mock
import threading
import random
import weakref

from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace
from typing import Iterable, Set

import AnyQt
from AnyQt.QtCore import (
    Qt, QObject, QCoreApplication, QThread, QEventLoop, QTimer, pyqtSlot,
    pyqtSignal
)
from AnyQt.QtTest import QSignalSpy

from orangewidget.utils.concurrent import (
    FutureWatcher, FutureSetWatcher, methodinvoke, PyOwned
)


class CoreAppTestCase(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication.instance()
        if self.app is None:
            self.app = QCoreApplication([])

    def tearDown(self):
        self.app.processEvents()
        del self.app


@unittest.skipIf(lambda: AnyQt.USED_API == "PySide2", "PySide2 in use")
class TestMethodinvoke(CoreAppTestCase):
    def test_methodinvoke(self):
        executor = ThreadPoolExecutor()
        state = [None, None]

        class StateSetter(QObject):
            @pyqtSlot(object)
            def set_state(self, value):
                state[0] = value
                state[1] = QThread.currentThread()

        def func(callback):
            callback(QThread.currentThread())

        obj = StateSetter()
        f1 = executor.submit(func, methodinvoke(obj, "set_state", (object,)))
        f1.result()
        # So invoked method can be called from the event loop
        self.app.processEvents()

        self.assertIs(state[1], QThread.currentThread(),
                      "set_state was called from the wrong thread")

        self.assertIsNot(state[0], QThread.currentThread(),
                         "set_state was invoked in the main thread")

        executor.shutdown(wait=True)


@unittest.skipIf(lambda: AnyQt.USED_API == "PySide2", "PySide2 in use")
class TestFutureWatcher(CoreAppTestCase):
    def test_watcher(self):
        executor = ThreadPoolExecutor(max_workers=1)
        f = executor.submit(lambda: 42)
        w = FutureWatcher(f)

        def spies(w):
            return SimpleNamespace(
                done=QSignalSpy(w.done),
                finished=QSignalSpy(w.finished),
                result=QSignalSpy(w.resultReady),
                error=QSignalSpy(w.exceptionReady),
                cancelled=QSignalSpy(w.cancelled)
            )

        spy = spies(w)
        self.assertTrue(spy.done.wait())

        self.assertEqual(list(spy.done), [[f]])
        self.assertEqual(list(spy.finished), [[f]])
        self.assertEqual(list(spy.result), [[42]])
        self.assertEqual(list(spy.error), [])
        self.assertEqual(list(spy.cancelled), [])

        f = executor.submit(lambda: 1/0)
        w = FutureWatcher(f)
        spy = spies(w)

        self.assertTrue(spy.done.wait())

        self.assertEqual(list(spy.done), [[f]])
        self.assertEqual(list(spy.finished), [[f]])
        self.assertEqual(len(spy.error), 1)
        self.assertIsInstance(spy.error[0][0], ZeroDivisionError)
        self.assertEqual(list(spy.result), [])
        self.assertEqual(list(spy.cancelled), [])

        ev = threading.Event()
        # block the executor to test cancellation
        executor.submit(lambda: ev.wait())
        f = executor.submit(lambda: 0)
        w = FutureWatcher(f)
        self.assertTrue(f.cancel())
        ev.set()

        spy = spies(w)

        self.assertTrue(spy.done.wait())

        self.assertEqual(list(spy.done), [[f]])
        self.assertEqual(list(spy.finished), [])
        self.assertEqual(list(spy.error), [])
        self.assertEqual(list(spy.result), [])
        self.assertEqual(list(spy.cancelled), [[f]])


@unittest.skipIf(lambda: AnyQt.USED_API == "PySide2", "PySide2 in use")
class TestFutureSetWatcher(CoreAppTestCase):
    def test_watcher(self):
        def spies(w):
            # type: (FutureSetWatcher) -> SimpleNamespace
            return SimpleNamespace(
                doneAt=QSignalSpy(w.doneAt),
                finishedAt=QSignalSpy(w.finishedAt),
                cancelledAt=QSignalSpy(w.cancelledAt),
                resultAt=QSignalSpy(w.resultReadyAt),
                exceptionAt=QSignalSpy(w.exceptionReadyAt),
                doneAll=QSignalSpy(w.doneAll),
            )

        executor = ThreadPoolExecutor(max_workers=5)
        fs = [executor.submit(lambda i: "Hello {}".format(i), i)
              for i in range(10)]
        w = FutureSetWatcher(fs)
        spy = spies(w)

        def as_set(seq):
            # type: (Iterable[list]) -> Set[tuple]
            seq = list(map(tuple, seq))
            set_ = set(seq)
            assert len(set_) == len(seq)
            return set_

        self.assertTrue(spy.doneAll.wait())
        expected = {(i, "Hello {}".format(i)) for i in range(10)}
        self.assertSetEqual(as_set(spy.doneAt), set(enumerate(fs)))
        self.assertSetEqual(as_set(spy.finishedAt), set(enumerate(fs)))
        self.assertSetEqual(as_set(spy.cancelledAt), set())
        self.assertSetEqual(as_set(spy.resultAt), expected)
        self.assertSetEqual(as_set(spy.exceptionAt), set())

        rseq = [random.randrange(0, 10) for _ in range(10)]
        fs = [executor.submit(lambda i: 1 / (i % 3), i) for i in rseq]
        w = FutureSetWatcher(fs)
        spy = spies(w)

        self.assertTrue(spy.doneAll.wait())
        self.assertSetEqual(as_set(spy.doneAt), set(enumerate(fs)))
        self.assertSetEqual(as_set(spy.finishedAt), set(enumerate(fs)))
        self.assertSetEqual(as_set(spy.cancelledAt), set())
        results = {(i, f.result())
                   for i, f in enumerate(fs) if not f.exception()}
        exceptions = {(i, f.exception())
                      for i, f in enumerate(fs) if f.exception()}
        assert len(results | exceptions) == len(fs)
        self.assertSetEqual(as_set(spy.resultAt), results)
        self.assertSetEqual(as_set(spy.exceptionAt), exceptions)

        executor = ThreadPoolExecutor(max_workers=1)
        ev = threading.Event()
        # Block the single worker thread to ensure successful cancel for f2
        f1 = executor.submit(lambda: ev.wait())
        f2 = executor.submit(lambda: 42)
        w = FutureSetWatcher([f1, f2])
        self.assertTrue(f2.cancel())
        # Unblock the worker
        ev.set()

        spy = spies(w)
        self.assertTrue(spy.doneAll.wait())
        self.assertSetEqual(as_set(spy.doneAt), {(0, f1), (1, f2)})
        self.assertSetEqual(as_set(spy.finishedAt), {(0, f1)})
        self.assertSetEqual(as_set(spy.cancelledAt), {(1, f2)})
        self.assertSetEqual(as_set(spy.resultAt), {(0, True)})
        self.assertSetEqual(as_set(spy.exceptionAt), set())

        # doneAll must always be emitted after the doneAt signals.
        executor = ThreadPoolExecutor(max_workers=2)
        futures = [executor.submit(pow, 1000, 1000) for _ in range(100)]
        watcher = FutureSetWatcher(futures)
        emithistory = []
        watcher.doneAt.connect(lambda i, f: emithistory.append(("doneAt", i, f)))
        watcher.doneAll.connect(lambda: emithistory.append(("doneAll", )))

        spy = spies(watcher)
        watcher.wait()
        self.assertEqual(len(spy.doneAll), 0)
        self.assertEqual(len(spy.doneAt), 0)
        watcher.flush()
        self.assertEqual(len(spy.doneAt), 100)
        self.assertEqual(list(spy.doneAll), [[]])
        self.assertSetEqual(set(emithistory[:-1]),
                            {("doneAt", i, f) for i, f in enumerate(futures)})
        self.assertEqual(emithistory[-1], ("doneAll",))

        # doneAll must be emitted even when on an empty futures list
        watcher = FutureSetWatcher()
        watcher.setFutures([])
        spy = spies(watcher)
        self.assertTrue(spy.doneAll.wait())

        watcher = FutureSetWatcher()
        watcher.setFutures([])
        watcher.wait()

        watcher = FutureSetWatcher()
        with self.assertRaises(RuntimeError):
            watcher.wait()

        with unittest.mock.patch.object(watcher, "thread", lambda: 42), \
                self.assertRaises(RuntimeError):
            watcher.flush()

@unittest.skipIf(lambda: AnyQt.USED_API == "PySide2", "PySide2 in use")
class TestPyOwned(CoreAppTestCase):
    def test_py_owned(self):
        class Obj(QObject, PyOwned):
            pass

        executor = ThreadPoolExecutor()
        ref = SimpleNamespace(obj=Obj())
        wref = weakref.ref(ref.obj)
        event = threading.Event()
        event.clear()

        def clear_ref():
            del ref.obj
            event.set()

        executor.submit(clear_ref)
        event.wait()
        self.assertIsNotNone(wref())
        self.assertIn(wref(), PyOwned._PyOwned__delete_later_set)
        loop = QEventLoop()
        QTimer.singleShot(0, loop.quit)
        loop.exec()
        self.assertIsNone(wref())

    def test_py_owned_enqueued(self):
        # https://www.riverbankcomputing.com/pipermail/pyqt/2020-April/042734.html
        class Emitter(QObject, PyOwned):
            signal = pyqtSignal()
            _p_signal = pyqtSignal()

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                # queued signal -> signal connection
                self._p_signal.connect(self.signal, Qt.QueuedConnection)

            def schedule_emit(self):
                """Schedule `signal` emit"""
                self._p_signal.emit()

        executor = ThreadPoolExecutor(max_workers=4)

        def test_one():
            ref = SimpleNamespace()  # hold single reference to Emitter obj
            ref.obj = Emitter()
            # enqueue 200 meta call events to the obj
            for i in range(200):
                ref.obj.schedule_emit()

            # event signaling the event loop is about to be entered
            event = threading.Event()

            def clear_obj(ref=ref):
                # wait for main thread to signal it is about to enter the event loop
                event.wait()
                del ref.obj  # clear the last/single ref to obj

            executor.submit(clear_obj)

            loop = QEventLoop()
            QTimer.singleShot(0, loop.quit)
            # bytecode optimizations, reduce the time between event.set and
            # exec to minimum
            set = event.set
            exec = loop.exec

            set()  # signal/unblock the worker;
            exec()  # enter event loop to process the queued meta calls

        for i in range(10):
            test_one()
