import copy
import itertools
import warnings
from typing import Optional, Iterable

from orangecanvas.registry.description import (
    InputSignal, OutputSignal, Single, Multiple, Default, NonDefault,
    Explicit, Dynamic
)

from orangewidget.utils import getmembers

# increasing counter for ensuring the order of Input/Output definitions
# is preserved when going through the unordered class namespace of
# WidgetSignalsMixin.Inputs/Outputs.
_counter = itertools.count()


Closed = type(
    "Closed", (object,), {
        "__doc__": "Explicit connection closing sentinel.",
        "__repr__": lambda self: "Closed",
        "__str__": lambda self: "Closed",
    }
)()


class _Signal:
    @staticmethod
    def get_flags(multiple, default, explicit, dynamic):
        """Compute flags from arguments"""

        return (Multiple if multiple else Single) | \
                (Default if default else NonDefault) | \
                (explicit and Explicit) | \
                (dynamic and Dynamic)

    def bound_signal(self, widget):
        """
        Return a copy of the signal bound to a widget.

        Called from `WidgetSignalsMixin.__init__`
        """
        new_signal = copy.copy(self)
        new_signal.widget = widget
        return new_signal


class Input(InputSignal, _Signal):
    """
    Description of an input signal.

    The class is used to declare input signals for a widget as follows
    (the example is taken from the widget Test & Score)::

        class Inputs:
            train_data = Input("Data", Table, default=True)
            test_data = Input("Test Data", Table)
            learner = Input("Learner", Learner, multiple=True)
            preprocessor = Input("Preprocessor", Preprocess)

    Every input signal must be used to decorate exactly one method that
    serves as the input handler, for instance::

        @Inputs.train_data
        def set_train_data(self, data):
            ...

    Parameters
    ----------
    name (str):
        signal name
    type (type):
        signal type
    id (str):
        a unique id of the signal
    doc (str, optional):
        signal documentation
    replaces (list of str):
        a list with names of signals replaced by this signal
    multiple (bool, optional):
        if set, multiple signals can be connected to this output
        (default: `False`)
    default (bool, optional):
        when the widget accepts multiple signals of the same type, one of them
        can set this flag to act as the default (default: `False`)
    explicit (bool, optional):
        if set, this signal is only used when it is the only option or when
        explicitly connected in the dialog (default: `False`)
    """
    Closed = Closed

    def __init__(self, name, type, id=None, doc=None, replaces=None, *,
                 multiple=False, default=False, explicit=False,
                 closing_sentinel=None):
        flags = self.get_flags(multiple, default, explicit, False)
        super().__init__(name, type, "", flags, id, doc, replaces or [])
        self._seq_id = next(_counter)
        self.closing_sentinel = closing_sentinel

    def __call__(self, method):
        """
        Decorator that stores decorated method's name in the signal's
        `handler` attribute. The method is returned unchanged.
        """
        if self.handler:
            raise ValueError("Input {} is already bound to method {}".
                             format(self.name, self.handler))
        self.handler = method.__name__
        return method


class MultiInput(Input):
    """
    A special multiple input descriptor.

    This type of input has explicit set/insert/remove interface to maintain
    fully ordered sequence input. This should be preferred to the
    plain `Input(..., multiple=True)` descriptor.

    This input type must register three methods in the widget implementation
    class corresponding to the insert, set/update and remove input commands::

        class Inputs:
            values = MultiInput("Values", object)
        ...
        @Inputs.values
        def set_value(self, index: int, value: object):
            "Set/update the value at index"
            ...
        @Inputs.values.insert
        def insert_value(self, index: int, value: object):
            "Insert value at specified index"
            ...
        @Inputs.values.remove
        def remove_value(self, index: int):
            "Remove value at index"
            ...

    Parameters
    ----------
    filter_none: bool
        If `True` any `None` values sent by workflow execution
        are implicitly converted to 'remove' notifications. When the value
        again changes to non-None the input is re-inserted into its proper
        position.


    .. versionadded:: 4.12.0
    """
    insert_handler: str = None
    remove_handler: str = None

    def __init__(self, *args, filter_none=False, **kwargs):
        multiple = kwargs.pop("multiple", True)
        if not multiple:
            raise ValueError("multiple cannot be set to False")
        super().__init__(*args, multiple=True, **kwargs)
        self.filter_none = filter_none
        self.closing_sentinel = Closed

    def insert(self, method):
        """Register the method as the insert handler"""
        self.insert_handler = method.__name__
        return method

    def remove(self, method):
        """"Register the method as the remove handler"""
        self.remove_handler = method.__name__
        return method

    def bound_signal(self, widget):
        if self.insert_handler is None:
            raise RuntimeError('insert_handler is not set')
        if self.remove_handler is None:
            raise RuntimeError('remove_handler is not set')
        return super().bound_signal(widget)


class Output(OutputSignal, _Signal):
    """
    Description of an output signal.

    The class is used to declare output signals for a widget as follows
    (the example is taken from the widget Test & Score)::

        class Outputs:
            predictions = Output("Predictions", Table)
            evaluations_results = Output("Evaluation Results", Results)

    The signal is then transmitted by, for instance::

        self.Outputs.predictions.send(predictions)

    Parameters
    ----------
    name (str):
        signal name
    type (type):
        signal type
    id (str):
        a unique id of the signal
    doc (str, optional):
        signal documentation
    replaces (list of str):
        a list with names of signals replaced by this signal
    default (bool, optional):
        when the widget accepts multiple signals of the same type, one of them
        can set this flag to act as the default (default: `False`)
    explicit (bool, optional):
        if set, this signal is only used when it is the only option or when
        explicitly connected in the dialog (default: `False`)
    dynamic (bool, optional):
        Specifies that the instances on the output will in general be subtypes
        of the declared type and that the output can be connected to any input
        signal which can accept a subtype of the declared output type
        (default: `True`)
    """
    def __init__(self, name, type, id=None, doc=None, replaces=None, *,
                 default=False, explicit=False, dynamic=True):
        flags = self.get_flags(False, default, explicit, dynamic)
        super().__init__(name, type, flags, id, doc, replaces or [])
        self.widget = None
        self._seq_id = next(_counter)

    def send(self, value, *args, **kwargs):
        """Emit the signal through signal manager."""
        assert self.widget is not None

        def _id_(id):
            return id
        extra_args = ()
        try:
            id = _id_(*args, **kwargs)
        except TypeError:
            pass
        else:
            extra_args += (id,)
            warnings.warn(
                "`id` parameter is deprecated and will be removed in the "
                "future", FutureWarning, stacklevel=2,
            )

        signal_manager = self.widget.signalManager
        if signal_manager is not None:
            signal_manager.send(self.widget, self.name, value, *extra_args)

    def invalidate(self):
        """Invalidate the current output value on the signal"""
        assert self.widget is not None
        signal_manager = self.widget.signalManager
        if signal_manager is not None:
            signal_manager.invalidate(self.widget, self.name)


class WidgetSignalsMixin:
    """Mixin for managing widget's input and output signals"""
    class Inputs:
        pass

    class Outputs:
        pass

    def __init__(self):
        self._bind_signals()

    def _bind_signals(self):
        for direction, signal_type in (("Inputs", Input), ("Outputs", Output)):
            bound_cls = getattr(self, direction)()
            for name, signal in getmembers(bound_cls, signal_type):
                setattr(bound_cls, name, signal.bound_signal(self))
            setattr(self, direction, bound_cls)

    def send(self, signalName, value, *args, **kwargs):
        """
        Send a `value` on the `signalName` widget output.

        An output with `signalName` must be defined in the class ``outputs``
        list.
        """
        def _id_(id=None):
            return id
        id_args = ()
        try:
            id = _id_(*args, **kwargs)
        except TypeError:
            pass
        else:
            id_args += (id,)
            warnings.warn(
                "`id` parameter is deprecated and will be removed in the "
                "future.", FutureWarning, stacklevel=2
            )

        if not any(s.name == signalName for s in self.outputs):
            raise ValueError('{} is not a valid output signal for widget {}'.format(
                signalName, self.name))

        if self.signalManager is not None:
            self.signalManager.send(self, signalName, value, *id_args)

    def handleNewSignals(self):
        """
        Invoked by the workflow signal propagation manager after all
        signals handlers have been called.
        Reimplement this method in order to coalesce updates from
        multiple updated inputs.
        """
        pass

    # Methods used by the meta class
    @classmethod
    def convert_signals(cls):
        """
        Convert tuple descriptions into old-style signals for backwards
        compatibility, and check the input handlers exist.
        The method is called from the meta-class.
        """
        def signal_from_args(args, signal_type):
            if isinstance(args, tuple):
                return signal_type(*args)
            elif isinstance(args, signal_type):
                return copy.copy(args)

        if hasattr(cls, "inputs") and cls.inputs:
            cls.inputs = [signal_from_args(input_, InputSignal)
                          for input_ in cls.inputs]
        if hasattr(cls, "outputs") and cls.outputs:
            cls.outputs = [signal_from_args(output, OutputSignal)
                           for output in cls.outputs]

        cls._check_input_handlers()

    @classmethod
    def _check_input_handlers(cls):
        unbound = [signal.name
                   for _, signal in getmembers(cls.Inputs, Input)
                   if not signal.handler]
        if unbound:
            raise ValueError("unbound signal(s) in {}: {}".
                             format(cls.__name__, ", ".join(unbound)))

        missing_handlers = [signal.handler for signal in cls.inputs
                            if not hasattr(cls, signal.handler)]
        if missing_handlers:
            raise ValueError("missing handlers in {}: {}".
                             format(cls.__name__, ", ".join(missing_handlers)))

    @classmethod
    def get_signals(cls, direction, ignore_old_style=False):
        """
        Return a list of `InputSignal` or `OutputSignal` needed for the
        widget description. For old-style signals, the method returns the
        original list. New-style signals are collected into a list.

        Parameters
        ----------
        direction (str): `"inputs"` or `"outputs"`

        Returns
        -------
        list of `InputSignal` or `OutputSignal`
        """
        old_style = cls.__dict__.get(direction, None)
        if old_style and not ignore_old_style:
            return old_style

        signal_class = getattr(cls, direction.title())
        signals = [signal for _, signal in getmembers(signal_class, _Signal)]
        return list(sorted(signals, key=lambda s: s._seq_id))


def get_input_meta(widget: WidgetSignalsMixin, name: str) -> Optional[Input]:
    """
    Return the named input meta description from widget (if it exists).
    """
    def as_input(obj):
        if isinstance(obj, Input):
            return obj
        elif isinstance(obj, InputSignal):
            rval = Input(obj.name, obj.type, obj.id, obj.doc, obj.replaces,
                         multiple=not obj.single, default=obj.default,
                         explicit=obj.explicit)
            rval.handler = obj.handler
            return rval
        elif isinstance(obj, tuple):
            return as_input(InputSignal(*obj))
        else:
            raise TypeError

    inputs: Iterable[Input] = map(as_input, widget.get_signals("inputs"))
    for input_ in inputs:
        if input_.name == name:
            return input_
    return None


class AttributeList(list):
    """Signal type for lists of attributes (variables)"""
