"""Widget Settings and Settings Handlers

Settings are used to declare widget attributes that persist through sessions.
When widget is removed or saved to a schema file, its settings are packed,
serialized and stored. When a new widget is created, values of attributes
marked as settings are read from disk. When schema is loaded, attribute values
are set to one stored in schema.

Each widget has its own SettingsHandler that takes care of serializing and
storing of settings and SettingProvider that is incharge of reading and
writing the setting values.

All widgets extending from OWBaseWidget use SettingsHandler, unless they
declare otherwise. SettingsHandler ensures that setting attributes
are replaced with default (last used) setting values when the widget is
initialized and stored when the widget is removed.

Widgets with settings whose values depend on the widget inputs use
settings handlers based on ContextHandler. These handlers have two
additional methods, open_context and close_context.

open_context is called when widgets receives new data. It finds a suitable
context and sets the widget attributes to the values stored in context.
If no suitable context exists, a new one is created and values from widget
are copied to it.

close_context stores values that were last used on the widget to the context
so they can be used alter. It should be called before widget starts modifying
(initializing) the value of the setting attributes.
"""

import sys
import copy
import itertools
import os
import logging
import pickle
import pprint
import warnings
from operator import itemgetter
from typing import Any, Optional, Tuple

from orangecanvas.scheme import readwrite
from orangewidget.gui import OWComponent

log = logging.getLogger(__name__)

__all__ = [
    "Setting", "SettingsHandler", "SettingProvider",
    "ContextSetting", "Context", "ContextHandler", "IncompatibleContext",
    "SettingsPrinter", "rename_setting", "widget_settings_dir"
]

_IMMUTABLES = (str, int, bytes, bool, float, tuple)

VERSION_KEY = "__version__"

# protocol v4 is supported since Python 3.4, protocol v5 since Python 3.8
PICKLE_PROTOCOL = 4


__WIDGET_SETTINGS_DIR = None  # type: Optional[Tuple[str, str]]


def set_widget_settings_dir_components(basedir: str, versionstr: str) -> None:
    """
    Set the directory path components where widgets save their settings.

    This overrides the global current application name/version derived paths.

    Note
    ----
    This should be set early in the application startup before any
    `OWBaseWidget` subclasses are imported (because it is needed at class
    definition time).

    Parameters
    ----------
    basedir: str
        The application specific data directory.
    versionstr: str
        The application version string for the versioned path component.

    See Also
    --------
    widget_settings_dir
    """
    global __WIDGET_SETTINGS_DIR
    __WIDGET_SETTINGS_DIR = (basedir, versionstr)


def widget_settings_dir(versioned=True) -> str:
    """
    Return the effective directory where widgets save their settings.

    This is a composed path based on a application specific data directory
    and application version string (if `versioned` is True) with a final
    'widgets' path component added (e.g. `'~/.local/share/MyApp/9.9.9/widgets'`)

    By default `QCoreApplication.applicationName` and
    `QCoreApplication.applicationVersion` are used to derive suitable paths
    (with a fallback if they are not set).

    Note
    ----
    If the application sets the `applicationName`/`applicationVersion`, it
    should do this early in the application startup before any
    `OWBaseWidget` subclasses are imported (because it is needed at class
    definition time).

    Use `set_widget_settings_dir_components` to override the default paths.

    Parameters
    ----------
    versioned: bool
        Should the returned path include the application version component.

    See Also
    --------
    set_widget_settings_dir_components
    """
    if __WIDGET_SETTINGS_DIR is None:
        from orangewidget.workflow.config import data_dir
        return os.path.join(data_dir(versioned), "widgets")
    else:
        base, version = __WIDGET_SETTINGS_DIR
        if versioned:
            return os.path.join(base, version, "widgets")
        else:
            return os.path.join(base, "widgets")


class Setting:
    """Description of a setting.
    """

    # Settings are automatically persisted to disk
    packable = True

    # Setting is only persisted to schema (default value does not change)
    schema_only = False

    def __new__(cls, default, *args, **kwargs):
        """A misleading docstring for providing type hints for Settings

        :type: default: T
        :rtype: T
        """
        return super().__new__(cls)

    def __init__(self, default, **data):
        self.name = None  # Name gets set in widget's meta class
        self.default = default
        self.__dict__.update(data)

    def __str__(self):
        return '{0} "{1}"'.format(self.__class__.__name__, self.name)

    __repr__ = __str__

    def __getnewargs__(self):
        return (self.default, )


# Pylint ignores type annotations in assignments. For
#
#    x: int = Setting(0)
#
# it ignores `int` and assumes x is of type `Setting`. Annotations in
# comments ( # type: int) also don't work. The only way to annotate x is
#
#    x: int
#    x = Setting(0)
#
# but we don't want to clutter the code with extra lines of annotations. Hence
# we disable checking the type of `Setting` by confusing pylint with an extra
# definition that is never executed.
if 1 == 0:
    class Setting:  # pylint: disable=function-redefined
        pass


def _apply_setting(setting: Setting, instance: OWComponent, value: Any):
    """
    Set `setting` of widget `instance` to the given `value`, in place if
    possible.

    If old and new values are of the same type, and the type is either a list
    or has methods `clear` and `update`, setting is updated in place. Otherwise
    the function calls `setattr`.

    Even if the target is update this way, call setattr (effectively calling
    instance.target = instance.target) to trigger any automatic updates related
    to this attribute).
    """
    target = getattr(instance, setting.name, None)
    if type(target) is type(value):
        if isinstance(value, list):
            target[:] = value
            value = target
        elif hasattr(value, "clear") and hasattr(value, "update"):
            target.clear()
            target.update(value)
            value = target
    setattr(instance, setting.name, value)


class SettingProvider:
    """A hierarchical structure keeping track of settings belonging to
    a class and child setting providers.

    At instantiation, it creates a dict of all Setting and SettingProvider
    members of the class. This dict is used to get/set values of settings
    from/to the instances of the class this provider belongs to.
    """

    def __init__(self, provider_class):
        """ Construct a new instance of SettingProvider.

        Traverse provider_class members and store all instances of
        Setting and SettingProvider.

        Parameters
        ----------
        provider_class : class
            class containing settings definitions
        """
        self.name = ""
        self.provider_class = provider_class
        self.providers = {}
        """:type: dict[str, SettingProvider]"""
        self.settings = {}
        """:type: dict[str, Setting]"""
        self.initialization_data = None

        for name in dir(provider_class):
            value = getattr(provider_class, name, None)
            if isinstance(value, Setting):
                value = copy.deepcopy(value)
                value.name = name
                self.settings[name] = value
            if isinstance(value, SettingProvider):
                value = copy.deepcopy(value)
                value.name = name
                self.providers[name] = value

    def initialize(self, instance, data=None):
        """Initialize instance settings to their default values.

        Mutable values are (shallow) copied before they are assigned to the
        widget. Immutable are used as-is.

        Parameters
        ----------
        instance : OWBaseWidget
            widget instance to initialize
        data : Optional[dict]
            optional data used to override the defaults
            (used when settings are loaded from schema)
        """
        if data is None and self.initialization_data is not None:
            data = self.initialization_data

        self._initialize_settings(instance, data)
        self._initialize_providers(instance, data)

    def reset_to_original(self, instance):
        self._initialize_settings(instance, None)
        self._initialize_providers(instance, None)

    def _initialize_settings(self, instance, data):
        if data is None:
            data = {}
        for name, setting in self.settings.items():
            value = data.get(name, setting.default)
            if isinstance(value, _IMMUTABLES):
                setattr(instance, name, value)
            else:
                setattr(instance, name, copy.copy(value))

    def _initialize_providers(self, instance, data):
        if not data:
            return

        for name, provider in self.providers.items():
            if name not in data:
                continue

            member = getattr(instance, name, None)
            if member is None or isinstance(member, SettingProvider):
                provider.store_initialization_data(data[name])
            else:
                provider.initialize(member, data[name])

    def store_initialization_data(self, initialization_data):
        """Store initialization data for later use.

        Used when settings handler is initialized, but member for this
        provider does not exists yet (because handler.initialize is called in
        __new__, but member will be created in __init__.

        Parameters
        ----------
        initialization_data : dict
            data to be used for initialization when the component is created
        """
        self.initialization_data = initialization_data

    @staticmethod
    def _default_packer(setting, instance):
        """A simple packet that yields setting name and value.

        Parameters
        ----------
        setting : Setting
        instance : OWBaseWidget
        """
        if setting.packable:
            if hasattr(instance, setting.name):
                yield setting.name, getattr(instance, setting.name)
            else:
                warnings.warn("{0} is declared as setting on {1} "
                              "but not present on instance."
                              .format(setting.name, instance))

    def pack(self, instance, packer=None):
        """Pack instance settings in a name:value dict.

        Parameters
        ----------
        instance : OWBaseWidget
            widget instance
        packer: callable (Setting, OWBaseWidget) -> Generator[(str, object)]
            optional packing function
            it will be called with setting and instance parameters and
            should yield (name, value) pairs that will be added to the
            packed_settings.
        """
        if packer is None:
            packer = self._default_packer

        packed_settings = dict(itertools.chain(
            *(packer(setting, instance) for setting in self.settings.values())
        ))

        packed_settings.update({
            name: provider.pack(getattr(instance, name), packer)
            for name, provider in self.providers.items()
            if hasattr(instance, name)
        })
        return packed_settings

    def unpack(self, instance, data):
        """Restore settings from data to the instance.

        Parameters
        ----------
        instance : OWBaseWidget
            instance to restore settings to
        data : dict
            packed data
        """
        for setting, _data, inst in self.traverse_settings(data, instance):
            if setting.name in _data and inst is not None:
                _apply_setting(setting, inst, _data[setting.name])

    def get_provider(self, provider_class):
        """Return provider for provider_class.

        If this provider matches, return it, otherwise pass
        the call to child providers.

        Parameters
        ----------
        provider_class : class
        """
        if issubclass(provider_class, self.provider_class):
            return self

        for subprovider in self.providers.values():
            provider = subprovider.get_provider(provider_class)
            if provider:
                return provider
        return None

    def traverse_settings(self, data=None, instance=None):
        """Generator of tuples (setting, data, instance) for each setting
        in this and child providers..

        Parameters
        ----------
        data : dict
            dictionary with setting values
        instance : OWBaseWidget
            instance matching setting_provider
        """
        data = data if data is not None else {}

        for setting in self.settings.values():
            yield setting, data, instance

        for provider in self.providers.values():
            data_ = data.get(provider.name, {})
            instance_ = getattr(instance, provider.name, None)
            for setting, component_data, component_instance in \
                    provider.traverse_settings(data_, instance_):
                yield setting, component_data, component_instance


class SettingsHandler:
    """Reads widget setting files and passes them to appropriate providers."""

    def __init__(self):
        """Create a setting handler template.

        Used in class definition. Bound instance will be created
        when SettingsHandler.create is called.
        """
        self.widget_class = None
        self.provider = None
        """:type: SettingProvider"""
        self.defaults = {}
        self.known_settings = {}

    @staticmethod
    def create(widget_class, template=None):
        """Create a new settings handler based on the template and bind it to
        widget_class.

        Parameters
        ----------
        widget_class : class
        template : SettingsHandler
            SettingsHandler to copy setup from

        Returns
        -------
        SettingsHandler
        """

        if template is None:
            template = SettingsHandler()

        setting_handler = copy.copy(template)
        setting_handler.defaults = {}
        setting_handler.bind(widget_class)
        return setting_handler

    def bind(self, widget_class):
        """Bind settings handler instance to widget_class.

        Parameters
        ----------
        widget_class : class
        """
        self.widget_class = widget_class
        self.provider = SettingProvider(widget_class)
        self.known_settings = {}
        self.analyze_settings(self.provider, "")
        self.read_defaults()

    def analyze_settings(self, provider, prefix):
        """Traverse through all settings known to the provider
        and analyze each of them.

        Parameters
        ----------
        provider : SettingProvider
        prefix : str
            prefix the provider is registered to handle
        """
        for setting in provider.settings.values():
            self.analyze_setting(prefix, setting)

        for name, sub_provider in provider.providers.items():
            new_prefix = '{0}{1}.'.format(prefix or '', name)
            self.analyze_settings(sub_provider, new_prefix)

    def analyze_setting(self, prefix, setting):
        """Perform any initialization task related to setting.

        Parameters
        ----------
        prefix : str
        setting : Setting
        """
        self.known_settings[prefix + setting.name] = setting

    def read_defaults(self):
        """Read (global) defaults for this widget class from a file.
        Opens a file and calls :obj:`read_defaults_file`. Derived classes
        should overload the latter."""
        filename = self._get_settings_filename()
        if os.path.isfile(filename):
            settings_file = open(filename, "rb")
            try:
                self.read_defaults_file(settings_file)
            # Unpickling exceptions can be of any type
            # pylint: disable=broad-except
            except Exception as ex:
                warnings.warn("Could not read defaults for widget {0}\n"
                              "The following error occurred:\n\n{1}"
                              .format(self.widget_class, ex))
            finally:
                settings_file.close()

    def read_defaults_file(self, settings_file):
        """Read (global) defaults for this widget class from a file.

        Parameters
        ----------
        settings_file : file-like object
        """
        defaults = pickle.load(settings_file)
        self.defaults = {
            key: value
            for key, value in defaults.items()
            if not isinstance(value, Setting)
        }
        self._migrate_settings(self.defaults)
        # remove schema_only settings introduced by the migration
        self._remove_schema_only(self.defaults)

    def write_defaults(self):
        """Write (global) defaults for this widget class to a file.
        Opens a file and calls :obj:`write_defaults_file`. Derived classes
        should overload the latter."""
        filename = self._get_settings_filename()
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        try:
            settings_file = open(filename, "wb")
            try:
                self.write_defaults_file(settings_file)
            except (EOFError, IOError, pickle.PicklingError) as ex:
                log.error("Could not write default settings for %s (%s).",
                          self.widget_class, ex)
                settings_file.close()
                os.remove(filename)
            else:
                settings_file.close()
        except PermissionError as ex:
            log.error("Could not write default settings for %s (%s).",
                      self.widget_class, type(ex).__name__)

    def write_defaults_file(self, settings_file):
        """Write defaults for this widget class to a file

        Parameters
        ----------
        settings_file : file-like object
        """
        defaults = dict(self.defaults)
        defaults[VERSION_KEY] = self.widget_class.settings_version
        pickle.dump(defaults, settings_file, protocol=PICKLE_PROTOCOL)

    def _get_settings_filename(self):
        """Return the name of the file with default settings for the widget"""
        return os.path.join(widget_settings_dir(),
                            "{0.__module__}.{0.__qualname__}.pickle"
                            .format(self.widget_class))

    def initialize(self, instance, data=None):
        """
        Initialize widget's settings.

        Replace all instance settings with their default values.

        Parameters
        ----------
        instance : OWBaseWidget
        data : dict or bytes that unpickle into a dict
            values used to override the defaults
        """
        provider = self._select_provider(instance)

        if isinstance(data, bytes):
            data = pickle.loads(data)
        self._migrate_settings(data)

        if provider is self.provider:
            data = self._add_defaults(data)

        provider.initialize(instance, data)

    def reset_to_original(self, instance):
        provider = self._select_provider(instance)
        provider.reset_to_original(instance)

    def _migrate_settings(self, settings):
        """Ask widget to migrate settings to the latest version."""
        if settings:
            try:
                self.widget_class.migrate_settings(
                    settings, settings.pop(VERSION_KEY, 0))
            except Exception:  # pylint: disable=broad-except
                sys.excepthook(*sys.exc_info())
                settings.clear()

    def _select_provider(self, instance):
        provider = self.provider.get_provider(instance.__class__)
        if provider is None:
            message = "{0} has not been declared as setting provider in {1}. " \
                      "Settings will not be saved/loaded properly. Defaults will be used instead." \
                      .format(instance.__class__, self.widget_class)
            warnings.warn(message)
            provider = SettingProvider(instance.__class__)
        return provider

    def _add_defaults(self, data):
        if data is None:
            return self.defaults

        new_data = self.defaults.copy()
        new_data.update(data)
        return new_data

    def _remove_schema_only(self, settings_dict):
        for setting, data, _ in self.provider.traverse_settings(data=settings_dict):
            if setting.schema_only:
                data.pop(setting.name, None)

    def _prepare_defaults(self, widget):
        self.defaults = self.provider.pack(widget)
        self._remove_schema_only(self.defaults)

    def pack_data(self, widget):
        """
        Pack the settings for the given widget. This method is used when
        saving schema, so that when the schema is reloaded the widget is
        initialized with its proper data and not the class-based defaults.
        See :obj:`SettingsHandler.initialize` for detailed explanation of its
        use.

        Inherited classes add other data, in particular widget-specific
        local contexts.

        Parameters
        ----------
        widget : OWBaseWidget
        """
        widget.settingsAboutToBePacked.emit()
        packed_settings = self.provider.pack(widget)
        packed_settings[VERSION_KEY] = self.widget_class.settings_version
        return packed_settings

    def update_defaults(self, widget):
        """
        Writes widget instance's settings to class defaults. Called when the
        widget is deleted.

        Parameters
        ----------
        widget : OWBaseWidget
        """
        widget.settingsAboutToBePacked.emit()
        self._prepare_defaults(widget)
        self.write_defaults()

    def fast_save(self, widget, name, value):
        """Store the (changed) widget's setting immediately to the context.

        Parameters
        ----------
        widget : OWBaseWidget
        name : str
        value : object

        """
        if name in self.known_settings:
            setting = self.known_settings[name]
            if not setting.schema_only:
                setting.default = value

    def reset_settings(self, instance):
        """Reset widget settings to defaults

        Parameters
        ----------
        instance : OWBaseWidget
        """
        for setting, _, inst in self.provider.traverse_settings(instance=instance):
            if setting.packable:
                _apply_setting(setting, inst, setting.default)


class ContextSetting(Setting):
    """Description of a context dependent setting"""

    OPTIONAL = 0
    REQUIRED = 2

    # Context settings are not persisted, but are stored in context instead.
    packable = False

    # These flags are not general - they assume that the setting has to do
    # something with the attributes. Large majority does, so this greatly
    # simplifies the declaration of settings in widget at no (visible)
    # cost to those settings that don't need it
    def __init__(self, default, *, required=2,
                 exclude_attributes=False, exclude_metas=False, **data):
        super().__init__(default, **data)
        self.exclude_attributes = exclude_attributes
        self.exclude_metas = exclude_metas
        self.required = required


@readwrite.RestrictedUnpickler.register_pickle_safe
class Context:
    """Class for data that defines context and
    values that should be applied to widget if given context
    is encountered."""
    def __init__(self, **argkw):
        self.values = {}
        self.__dict__.update(argkw)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__


class ContextHandler(SettingsHandler):
    """Base class for setting handlers that can handle contexts.

    Classes deriving from it need to implement method `match`.
    """

    NO_MATCH = 0
    MATCH = 1
    PERFECT_MATCH = 2

    MAX_SAVED_CONTEXTS = 50

    def __init__(self):
        super().__init__()
        self.global_contexts = []
        self.known_settings = {}

    def initialize(self, instance, data=None):
        """Initialize the widget: call the inherited initialization and
        add an attribute 'context_settings' to the widget. This method
        does not open a context."""
        instance.current_context = None
        super().initialize(instance, data)
        if data and "context_settings" in data:
            instance.context_settings = data["context_settings"]
            self._migrate_contexts(instance.context_settings)
        else:
            instance.context_settings = []

    def read_defaults_file(self, settings_file):
        """Call the inherited method, then read global context from the
           pickle."""
        super().read_defaults_file(settings_file)
        self.global_contexts = pickle.load(settings_file)
        self._migrate_contexts(self.global_contexts)
        # remove schema_only settings introduced by the migration
        for context in self.global_contexts:
            self._remove_schema_only(context.values)

    def _migrate_contexts(self, contexts):
        i = 0
        while i < len(contexts):
            context = contexts[i]
            try:
                self.widget_class.migrate_context(
                    context, context.values.pop(VERSION_KEY, 0))
            except IncompatibleContext:
                del contexts[i]
            except Exception:  # pylint: disable=broad-except
                sys.excepthook(*sys.exc_info())
                del contexts[i]
            else:
                i += 1

    def write_defaults_file(self, settings_file):
        """Call the inherited method, then add global context to the pickle."""
        super().write_defaults_file(settings_file)

        def add_version(context):
            context = copy.copy(context)
            context.values = dict(context.values)
            context.values[VERSION_KEY] = self.widget_class.settings_version
            return context

        pickle.dump([add_version(context) for context in self.global_contexts],
                    settings_file, protocol=PICKLE_PROTOCOL)

    def pack_data(self, widget):
        """Call the inherited method, then add local contexts to the dict."""
        data = super().pack_data(widget)
        self.settings_from_widget(widget)
        context_settings = [copy.copy(context) for context in
                            widget.context_settings]
        for context in context_settings:
            context.values[VERSION_KEY] = self.widget_class.settings_version
        data["context_settings"] = context_settings
        return data

    def update_defaults(self, widget):
        """
        Reimplemented from SettingsHandler

        Merge the widgets local contexts into the global contexts and persist
        the settings (including the contexts) to disk.
        """
        widget.settingsAboutToBePacked.emit()
        self.settings_from_widget(widget)
        globs = self.global_contexts
        assert widget.context_settings is not globs
        new_contexts = []
        for context in widget.context_settings:
            context = copy.deepcopy(context)
            self._remove_schema_only(context.values)
            if context not in globs:
                new_contexts.append(context)
        globs[:0] = reversed(new_contexts)
        del globs[self.MAX_SAVED_CONTEXTS:]

        # Save non-context settings. Do not call super().update_defaults, so that
        # settingsAboutToBePacked is emitted once.
        self._prepare_defaults(widget)
        self.write_defaults()

    def new_context(self, *args):
        """Create a new context."""
        return Context()

    def open_context(self, widget, *args):
        """Open a context by finding one and setting the widget data or
        creating one and fill with the data from the widget."""
        widget.current_context, is_new = \
            self.find_or_create_context(widget, *args)
        if is_new:
            self.settings_from_widget(widget, *args)
        else:
            self.settings_to_widget(widget, *args)

    def match(self, context, *args):
        """Return the degree to which the stored `context` matches the data
        passed in additional arguments).
        When match returns 0 (ContextHandler.NO_MATCH), the context will not
        be used. When it returns ContextHandler.PERFECT_MATCH, the context
        is a perfect match so no further search is necessary.

        If imperfect matching is not desired, match should only
        return ContextHandler.NO_MATCH or ContextHandler.PERFECT_MATCH.

        Derived classes must overload this method.
        """
        raise NotImplementedError

    def find_or_create_context(self, widget, *args):
        """Find the best matching context or create a new one if nothing
        useful is found. The returned context is moved to or added to the top
        of the context list."""

        # First search the contexts that were already used in this widget instance
        best_context, best_score = self.find_context(widget.context_settings, args, move_up=True)
        # If the exact data was used, reuse the context
        if best_score == self.PERFECT_MATCH:
            return best_context, False

        # Otherwise check if a better match is available in global_contexts
        best_context, best_score = self.find_context(self.global_contexts, args,
                                                     best_score, best_context)
        if best_context:
            context = self.clone_context(best_context, *args)
        else:
            context = self.new_context(*args)
        # Store context in widget instance. It will be pushed to global_contexts
        # when (if) update defaults is called.
        self.add_context(widget.context_settings, context)
        return context, best_context is None

    def find_context(self, known_contexts, args, best_score=0, best_context=None, move_up=False):
        """Search the given list of contexts and return the context
         which best matches the given args.

        best_score and best_context can be used to provide base_values.
        """

        best_idx = None
        for i, context in enumerate(known_contexts):
            score = self.match(context, *args)
            if score > best_score:  # NO_MATCH is not OK!
                best_context, best_score, best_idx = context, score, i
                if score == self.PERFECT_MATCH:
                    break
        if best_idx is not None and move_up:
            self.move_context_up(known_contexts, best_idx)
        return best_context, best_score

    @staticmethod
    def move_context_up(contexts, index):
        """Move the context to the top of the list"""
        contexts.insert(0, contexts.pop(index))

    def add_context(self, contexts, setting):
        """Add the context to the top of the list."""
        contexts.insert(0, setting)
        del contexts[self.MAX_SAVED_CONTEXTS:]

    def clone_context(self, old_context, *args):
        """Construct a copy of the context settings suitable for the context
        described by additional arguments. The method is called by
        find_or_create_context with the same arguments. A class that overloads
        :obj:`match` to accept additional arguments must also overload
        :obj:`clone_context`."""
        context = self.new_context(*args)
        context.values = copy.deepcopy(old_context.values)

        traverse = self.provider.traverse_settings
        for setting, data, _ in traverse(data=context.values):
            if not isinstance(setting, ContextSetting):
                continue

            self.filter_value(setting, data, *args)
        return context

    @staticmethod
    def filter_value(setting, data, *args):
        """Remove values related to setting that are invalid given args."""

    def close_context(self, widget):
        """Close the context by calling :obj:`settings_from_widget` to write
        any relevant widget settings to the context."""
        if widget.current_context is None:
            return

        self.settings_from_widget(widget)
        widget.current_context = None

    def settings_to_widget(self, widget, *args):
        """Apply context settings stored in currently opened context
        to the widget.
        """
        context = widget.current_context
        if context is None:
            return

        widget.retrieveSpecificSettings()

        for setting, data, instance in \
                self.provider.traverse_settings(data=context.values, instance=widget):
            if not isinstance(setting, ContextSetting) or setting.name not in data:
                continue
            value = self.decode_setting(setting, data[setting.name], *args)
            _apply_setting(setting, instance, value)

    def settings_from_widget(self, widget, *args):
        """Update the current context with the setting values from the widget.
        """

        context = widget.current_context
        if context is None:
            return

        widget.storeSpecificSettings()

        def packer(setting, instance):
            if isinstance(setting, ContextSetting) and hasattr(instance, setting.name):
                value = getattr(instance, setting.name)
                yield setting.name, self.encode_setting(context, setting, value)

        context.values = self.provider.pack(widget, packer=packer)

    def fast_save(self, widget, name, value):
        """Update value of `name` setting in the current context to `value`
        """
        setting = self.known_settings.get(name)
        if isinstance(setting, ContextSetting):
            context = widget.current_context
            if setting.schema_only or context is None:
                return

            value = self.encode_setting(context, setting, value)
            self.update_packed_data(context.values, name, value)
        else:
            super().fast_save(widget, name, value)

    @staticmethod
    def update_packed_data(data, name, value):
        """Updates setting value stored in data dict"""

        *prefixes, name = name.split('.')
        for prefix in prefixes:
            data = data.setdefault(prefix, {})
        data[name] = value

    def encode_setting(self, context, setting, value):
        """Encode value to be stored in settings dict"""
        return copy.copy(value)

    def decode_setting(self, setting, value, *args):
        """Decode settings value from the setting dict format"""
        return value


class IncompatibleContext(Exception):
    """Raised when a required variable in context is not available in data."""


class SettingsPrinter(pprint.PrettyPrinter):
    """Pretty Printer that knows how to properly format Contexts."""

    def _format(self, obj, stream, indent, allowance, context, level):
        if not isinstance(obj, Context):
            super()._format(obj, stream, indent, allowance, context, level)
            return

        stream.write("Context(")
        for key, value in sorted(obj.__dict__.items(), key=itemgetter(0)):
            if key == "values":
                continue
            stream.write(key)
            stream.write("=")
            stream.write(self._repr(value, context, level + 1))
            stream.write(",\n")
            stream.write(" " * (indent + 8))
        stream.write("values=")
        stream.write(" ")
        self._format(obj.values, stream, indent+15,
                     allowance+1, context, level + 1)
        stream.write(")")


def rename_setting(settings, old_name, new_name):
    """
    Rename setting from `old_name` to `new_name`. Used in migrations.

    The argument `settings` can be `dict` or `Context`.
    """
    if isinstance(settings, Context):
        rename_setting(settings.values, old_name, new_name)
    else:
        settings[new_name] = settings.pop(old_name)
