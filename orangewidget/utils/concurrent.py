"""
General helper functions and classes for PyQt concurrent programming
"""
# TODO: Rename the module to something that does not conflict with stdlib
# concurrent
from typing import Callable, Any, List, Optional, Union, Type

import threading
import logging
import warnings
from functools import partial, lru_cache
import concurrent.futures
from concurrent.futures import Future, TimeoutError

from AnyQt.QtCore import (
    Qt, QObject, QThreadPool, QThread, QRunnable, QSemaphore,
    QCoreApplication, QEvent,
    pyqtSignal as Signal, pyqtSlot as Slot
)


class FutureRunnable(QRunnable):
    """
    A QRunnable to fulfil a `Future` in a QThreadPool managed thread.

    Parameters
    ----------
    future : concurrent.futures.Future
        Future whose contents will be set with the result of executing
        `func(*args, **kwargs)` after completion
    func : Callable
        Function to invoke in a thread
    args : tuple
        Positional arguments for `func`
    kwargs : dict
        Keyword arguments for `func`

    Example
    -------
    >>> f = concurrent.futures.Future()
    >>> task = FutureRunnable(f, int, (42,), {})
    >>> QThreadPool.globalInstance().start(task)
    >>> f.result()
    42
    """
    def __init__(self, future, func, args, kwargs):
        # type: (Future, Callable, tuple, dict) -> None
        super().__init__()
        self.future = future
        self.task = (func, args, kwargs)

    def run(self):
        """
        Reimplemented from `QRunnable.run`
        """
        try:
            if not self.future.set_running_or_notify_cancel():
                # future was cancelled
                return
            func, args, kwargs = self.task
            try:
                result = func(*args, **kwargs)
            except BaseException as ex: # pylint: disable=broad-except
                self.future.set_exception(ex)
            else:
                self.future.set_result(result)
        except BaseException:  # pylint: disable=broad-except
            log = logging.getLogger(__name__)
            log.critical("Exception in worker thread.", exc_info=True)


class FutureWatcher(QObject):
    """
    An `QObject` watching the state changes of a `concurrent.futures.Future`

    Note
    ----
    The state change notification signals (`done`, `finished`, ...)
    are always emitted when the control flow reaches the event loop
    (even if the future is already completed when set).

    Note
    ----
    An event loop must be running, otherwise the notifier signals will
    not be emitted.

    Parameters
    ----------
    parent : QObject
        Parent object.
    future : Future
        The future instance to watch.

    Example
    -------
    >>> app = QCoreApplication.instance() or QCoreApplication([])
    >>> f = submit(lambda i, j: i ** j, 10, 3)
    >>> watcher = FutureWatcher(f)
    >>> watcher.resultReady.connect(lambda res: print("Result:", res))
    >>> watcher.done.connect(app.quit)
    >>> _ = app.exec()
    Result: 1000
    >>> f.result()
    1000
    """
    #: Signal emitted when the future is done (cancelled or finished)
    done = Signal(Future)

    #: Signal emitted when the future is finished (i.e. returned a result
    #: or raised an exception - but not if cancelled)
    finished = Signal(Future)

    #: Signal emitted when the future was cancelled
    cancelled = Signal(Future)

    #: Signal emitted with the future's result when successfully finished.
    resultReady = Signal(object)

    #: Signal emitted with the future's exception when finished with an
    #: exception.
    exceptionReady = Signal(BaseException)

    def __init__(self, future=None, parent=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.__future = None
        if future is not None:
            self.setFuture(future)

    def setFuture(self, future):
        # type: (Future) -> None
        """
        Set the future to watch.

        Raise a `RuntimeError` if a future is already set.

        Parameters
        ----------
        future : Future
        """
        if self.__future is not None:
            raise RuntimeError("Future already set")

        self.__future = future
        callback = methodinvoke.from_method(
            self.__future_done, (Future,), conntype=Qt.QueuedConnection
        )
        future.add_done_callback(callback)

    def future(self):
        # type: () -> Future
        """
        Return the future instance.
        """
        return self.__future

    def isCancelled(self):
        warnings.warn("isCancelled is deprecated", DeprecationWarning,
                      stacklevel=2)
        return self.__future.cancelled()

    def isDone(self):
        warnings.warn("isDone is deprecated", DeprecationWarning,
                      stacklevel=2)
        return self.__future.done()

    def result(self):
        # type: () -> Any
        """
        Return the future's result.

        Note
        ----
        This method is non-blocking. If the future has not yet completed
        it will raise an error.
        """
        try:
            return self.__future.result(timeout=0)
        except TimeoutError:
            raise RuntimeError("Future is not yet done")

    def exception(self):
        # type: () -> Optional[BaseException]
        """
        Return the future's exception.

        Note
        ----
        This method is non-blocking. If the future has not yet completed
        it will raise an error.
        """
        try:
            return self.__future.exception(timeout=0)
        except TimeoutError:
            raise RuntimeError("Future is not yet done")

    def __emitSignals(self):
        assert self.__future is not None
        assert self.__future.done()
        if self.__future.cancelled():
            self.cancelled.emit(self.__future)
            self.done.emit(self.__future)
        elif self.__future.done():
            self.finished.emit(self.__future)
            self.done.emit(self.__future)
            if self.__future.exception():
                self.exceptionReady.emit(self.__future.exception())
            else:
                self.resultReady.emit(self.__future.result())
        else:
            assert False

    @Slot(Future)
    def __future_done(self, f):
        assert f is self.__future
        self.__emitSignals()


class FutureSetWatcher(QObject):
    """
    An `QObject` watching the state changes of a list of
    `concurrent.futures.Future` instances

    Note
    ----
    The state change notification signals (`doneAt`, `finishedAt`, ...)
    are always emitted when the control flow reaches the event loop
    (even if the future is already completed when set).

    Note
    ----
    An event loop must be running, otherwise the notifier signals will
    not be emitted.

    Parameters
    ----------
    parent : QObject
        Parent object.
    futures : List[Future]
        A list of future instance to watch.

    Example
    -------
    >>> app = QCoreApplication.instance() or QCoreApplication([])
    >>> fs = [submit(lambda i, j: i ** j, 10, 3) for i in range(10)]
    >>> watcher = FutureSetWatcher(fs)
    >>> watcher.resultReadyAt.connect(
    ...     lambda i, res: print("Result at {}: {}".format(i, res))
    ... )
    >>> watcher.doneAll.connect(app.quit)
    >>> _ = app.exec()
    Result at 0: 1000
    ...
    """
    #: Signal emitted when the future at `index` is done (cancelled or
    #: finished)
    doneAt = Signal([int, Future])

    #: Signal emitted when the future at index is finished (i.e. returned
    #: a result)
    finishedAt = Signal([int, Future])

    #: Signal emitted when the future at `index` was cancelled.
    cancelledAt = Signal([int, Future])

    #: Signal emitted with the future's result when successfully
    #: finished.
    resultReadyAt = Signal([int, object])

    #: Signal emitted with the future's exception when finished with an
    #: exception.
    exceptionReadyAt = Signal([int, BaseException])

    #: Signal reporting the current completed count
    progressChanged = Signal([int, int])

    #: Signal emitted when all the futures have completed.
    doneAll = Signal()

    def __init__(self, futures=None, *args, **kwargs):
        # type: (List[Future], Any, Any) -> None
        super().__init__(*args, **kwargs)
        self.__futures = None
        self.__semaphore = None
        self.__countdone = 0
        if futures is not None:
            self.setFutures(futures)

    def setFutures(self, futures):
        # type: (List[Future]) -> None
        """
        Set the future instances to watch.

        Raise a `RuntimeError` if futures are already set.

        Parameters
        ----------
        futures : List[Future]
        """
        if self.__futures is not None:
            raise RuntimeError("already set")
        self.__futures = []
        schedule_emit = methodinvoke(self, "__emitpending", (int, Future))
        # Semaphore counting the number of future that have enqueued
        # done notifications. Used for the `wait` implementation.
        self.__semaphore = semaphore = QSemaphore(0)

        def on_done(index, f):
            try:
                schedule_emit(index, f)
            finally:
                semaphore.release()

        for i, future in enumerate(futures):
            self.__futures.append(future)
            future.add_done_callback(partial(on_done, i))

        if not self.__futures:
            # `futures` was an empty sequence.
            methodinvoke(self, "doneAll", ())()

    @Slot(int, Future)
    def __emitpending(self, index, future):
        # type: (int, Future) -> None
        assert QThread.currentThread() is self.thread()
        assert self.__futures[index] is future
        assert future.done()
        assert self.__countdone < len(self.__futures)
        self.__futures[index] = None
        self.__countdone += 1

        if future.cancelled():
            self.cancelledAt.emit(index, future)
            self.doneAt.emit(index, future)
        elif future.done():
            self.finishedAt.emit(index, future)
            self.doneAt.emit(index, future)
            if future.exception():
                self.exceptionReadyAt.emit(index, future.exception())
            else:
                self.resultReadyAt.emit(index, future.result())
        else:
            assert False

        self.progressChanged.emit(self.__countdone, len(self.__futures))

        if self.__countdone == len(self.__futures):
            self.doneAll.emit()

    def flush(self):
        """
        Flush all pending signal emits currently enqueued.

        Must only ever be called from the thread this object lives in
        (:func:`QObject.thread()`).
        """
        if QThread.currentThread() is not self.thread():
            raise RuntimeError("`flush()` called from a wrong thread.")
        # NOTE: QEvent.MetaCall is the event implementing the
        # `Qt.QueuedConnection` method invocation.
        QCoreApplication.sendPostedEvents(self, QEvent.MetaCall)

    def wait(self):
        """
        Wait for for all the futures to complete and *enqueue* notifications
        to this object, but do not emit any signals.

        Use `flush()` to emit all signals after a `wait()`
        """
        if self.__futures is None:
            raise RuntimeError("Futures were not set.")

        self.__semaphore.acquire(len(self.__futures))
        self.__semaphore.release(len(self.__futures))


class methodinvoke(object):
    """
    A thin wrapper for invoking QObject's method through
    `QMetaObject.invokeMethod`.

    This can be used to invoke the method across thread boundaries (or even
    just for scheduling delayed calls within the same thread).

    Note
    ----
    An event loop MUST be running in the target QObject's thread.

    Parameters
    ----------
    obj : QObject
        A QObject instance.
    method : str
        The method name. This method must be registered with the Qt object
        meta system (e.g. decorated by a Slot decorator).
    arg_types : tuple
        A tuple of positional argument types.
    conntype : Qt.ConnectionType
        The connection/call type. Qt.QueuedConnection (the default) and
        Qt.BlockingConnection are the most interesting.

    See Also
    --------
    QMetaObject.invokeMethod

    Example
    -------
    >>> app = QCoreApplication.instance() or QCoreApplication([])
    >>> quit = methodinvoke(app, "quit", ())
    >>> t = threading.Thread(target=quit)
    >>> t.start()
    >>> app.exec()
    0
    """

    @staticmethod
    def from_method(method, arg_types=(), *, conntype=Qt.QueuedConnection):
        """
        Create and return a `methodinvoke` instance from a bound method.

        Parameters
        ----------
        method : Union[types.MethodType, types.BuiltinMethodType]
            A bound method of a QObject registered with the Qt meta object
            system (e.g. decorated by a Slot decorators)
        arg_types : Tuple[Union[type, str]]
            A tuple of positional argument types.
        conntype: Qt.ConnectionType
            The connection/call type (Qt.QueuedConnection and
            Qt.BlockingConnection are the most interesting)

        Returns
        -------
        invoker : methodinvoke
        """
        obj = method.__self__
        name = method.__name__
        t = emitter_class(arg_types)()
        t.sig.connect(method, conntype)
        mi = methodinvoke.__new__(methodinvoke)
        mi.obj = obj
        mi.method = method
        mi.arg_types = arg_types
        mi.conntype = conntype
        mi.__emitter = t
        return mi
        # return t.sig.emit
        # return methodinvoke(obj, name, arg_types, conntype=conntype)

    def __init__(self, obj, method, arg_types=(), *,
                 conntype=Qt.QueuedConnection):
        self.obj = obj
        self.method = method
        self.arg_types = tuple(arg_types)
        self.conntype = conntype
        emitter_type = emitter_class(*self.arg_types)
        self.__emitter = emitter_type()
        # This used to work using `QMetaObject.invokeMethod` which references
        # to slots by name. 'Private' private methods (`def __blabla(...)` are
        # registered unmangled with Qt object system, so we (try to) do the
        # mangaling here.
        methodname = self.__mangled_method_name(type(obj), method)
        self.__emitter.sig.connect(
            getattr(self.obj, methodname), self.conntype
        )

    @staticmethod
    def __mangled_method_name(klass: type, name: str):
        if hasattr(klass, name):
            return name
        if not name.startswith("__"):
            return name
        for klass_ in klass.mro():
            mangle = f'_{klass_.__name__}{name}'
            if mangle in klass_.__dict__:
                return mangle
        return name

    def __call__(self, *args):
        self.__emitter.sig.emit(*args)


@lru_cache(maxsize=1024)
def emitter_class(*argtypes: Union[type, str]) -> Type[QObject]:
    class Emitter(QObject):
        sig = Signal(*argtypes)
    return Emitter
