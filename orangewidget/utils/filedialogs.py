import os
import sys
import typing
import warnings
from typing import Tuple, List, Sequence, Optional

from more_itertools import unique_everseen

from AnyQt.QtCore import QFileInfo, Qt, QSettings
from AnyQt.QtGui import QBrush
from AnyQt.QtWidgets import \
    QMessageBox, QFileDialog, QFileIconProvider, QComboBox

from orangecanvas.utils import qualified_name
from orangecanvas.utils.settings import \
    QSettings_writeArray, QSettings_readArray

from orangewidget import settings
from orangewidget.io import Compression
from orangewidget.settings import Setting

if typing.TYPE_CHECKING:
    from orangewidget.widget import OWBaseWidget
    from typing_extensions import Protocol
else:
    from abc import ABC as Protocol


class Format(Protocol):
    """
    An abstract file format description.

    Classes belonging to this type must define:

        PRIORITY: str
        EXTENSIONS: Tuple[str]  # a tuple of filename extensions (including dot)
        DESCRIPTION: str  # A short file type name

    This class is not intended for subclassing.
    """
    PRIORITY = sys.maxsize     # type: int
    EXTENSIONS = ()            # type: Tuple[str, ...]
    DESCRIPTION = ""           # type: str


def fix_extension(ext, format, suggested_ext, suggested_format):
    dlg = QMessageBox(
        QMessageBox.Warning,
        "Mismatching extension",
        "Extension '{}' does not match the chosen file format, {}.\n\n"
        "Would you like to fix this?".format(ext, format))
    role = QMessageBox.AcceptRole
    change_ext = \
        suggested_ext and \
        dlg.addButton("Change extension to " + suggested_ext, role)
    change_format =\
        suggested_format and \
        dlg.addButton("Save as " + suggested_format, role)
    cancel = dlg.addButton("Back", role)
    dlg.setEscapeButton(cancel)
    dlg.exec()
    if dlg.clickedButton() == cancel:
        return fix_extension.CANCEL
    elif dlg.clickedButton() == change_ext:
        return fix_extension.CHANGE_EXT
    elif dlg.clickedButton() == change_format:
        return fix_extension.CHANGE_FORMAT


fix_extension.CHANGE_EXT = 0      # type: ignore
fix_extension.CHANGE_FORMAT = 1   # type: ignore
fix_extension.CANCEL = 2          # type: ignore


def format_filter(writer):
    # type: (Format) -> str
    return '{} (*{})'.format(writer.DESCRIPTION, ' *'.join(writer.EXTENSIONS))


def get_file_name(start_dir, start_filter, file_formats):
    return open_filename_dialog_save(start_dir, start_filter,
                                     sorted(set(file_formats.values()), key=lambda x: x.PRIORITY))


def open_filename_dialog_save(start_dir, start_filter, file_formats):
    """
    The function uses the standard save file dialog with filters from the
    given file formats. Extension is added automatically, if missing. If the
    user enters file extension that does not match the file format, (s)he is
    given a dialog to decide whether to fix the extension or the format.

    Args:
        start_dir (str): initial directory, optionally including the filename
        start_filter (str): initial filter
        file_formats (a list of FileFormat): file formats
    Returns:
        (filename, writer, filter), or `(None, None, None)` on cancel
    """
    while True:
        dialog = QFileDialog.getSaveFileName
        filename, format, filter = \
            open_filename_dialog(start_dir, start_filter, file_formats,
                                 add_all=False, title="Save as...", dialog=dialog)
        if not filename:
            return None, None, None

        base, ext = os.path.splitext(filename)
        if ext in Compression.all:
            base, base_ext = os.path.splitext(base)
            ext = base_ext + ext
        if not ext:
            filename += format.EXTENSIONS[0]
        elif ext not in format.EXTENSIONS:
            suggested_ext = format.EXTENSIONS[0]
            suggested_format = False
            for f in file_formats:  # find the first format
                if ext in f.EXTENSIONS:
                    suggested_format = f
                    break
            res = fix_extension(ext, format.DESCRIPTION, suggested_ext,
                                suggested_format.DESCRIPTION if suggested_format else False)
            if res == fix_extension.CANCEL:
                continue
            if res == fix_extension.CHANGE_EXT:
                filename = base + suggested_ext
            elif res == fix_extension.CHANGE_FORMAT:
                format = suggested_format
                filter = format_filter(format)
        return filename, format, filter


def open_filename_dialog(start_dir: str, start_filter: str, file_formats,
                         add_all=True, title="Open...", dialog=None):
    """
    Open file dialog with file formats.

    Function also returns the format and filter to cover the case where the
    same extension appears in multiple filters.

    Args:
        start_dir (str): initial directory, optionally including the filename
        start_filter (str): initial filter
        file_formats (a list of FileFormat): file formats
        add_all (bool): add a filter for all supported extensions
        title (str): title of the dialog
        dialog: a function that creates a QT dialog
    Returns:
        (filename, file_format, filter), or `(None, None, None)` on cancel
    """
    file_formats = sorted(set(file_formats), key=lambda w: (w.PRIORITY, w.DESCRIPTION))
    filters = [format_filter(f) for f in file_formats]

    # add all readable files option
    if add_all:
        all_extensions = set()
        for f in file_formats:
            all_extensions.update(f.EXTENSIONS)
        file_formats.insert(0, None)
        filters.insert(0, "All readable files (*{})".format(
            ' *'.join(sorted(all_extensions))))

    if start_filter not in filters:
        start_filter = filters[0]

    if dialog is None:
        dialog = QFileDialog.getOpenFileName
    filename, filter = dialog(
        None, title, start_dir, ';;'.join(filters), start_filter)
    if not filename:
        return None, None, None

    if filter in filters:
        file_format = file_formats[filters.index(filter)]
    else:
        file_format = None
        filter = None

    return filename, file_format, filter


class RecentPath:
    abspath = ''
    prefix = None   #: Option[str]  # BASEDIR | SAMPLE-DATASETS | ...
    relpath = ''  #: Option[str]  # path relative to `prefix`
    title = ''    #: Option[str]  # title of filename (e.g. from URL)
    sheet = ''    #: Option[str]  # sheet
    file_format = None  #: Option[str]  # file format as a string

    def __init__(self, abspath, prefix, relpath, title='', sheet='', file_format=None):
        if os.name == "nt":
            # always use a cross-platform pathname component separator
            abspath = abspath.replace(os.path.sep, "/")
            if relpath is not None:
                relpath = relpath.replace(os.path.sep, "/")
        self.abspath = abspath
        self.prefix = prefix
        self.relpath = relpath
        self.title = title
        self.sheet = sheet
        self.file_format = file_format

    def as_dict(self):
        return {
            "abspath": self.abspath,
            "prefix": self.prefix,
            "relpath": self.relpath,
            "title": self.title,
            "sheet": self.sheet,
            "file_format": self.file_format,
        }

    def __eq__(self, other):
        return (self.abspath == other.abspath or
                (self.prefix is not None and self.relpath is not None and
                 self.prefix == other.prefix and
                 self.relpath == other.relpath))

    @staticmethod
    def create(path, searchpaths, **kwargs):
        """
        Create a RecentPath item inferring a suitable prefix name and relpath.

        Parameters
        ----------
        path : str
            File system path.
        searchpaths : List[Tuple[str, str]]
            A sequence of (NAME, prefix) pairs. The sequence is searched
            for a item such that prefix/relpath == abspath. The NAME is
            recorded in the `prefix` and relpath in `relpath`.
            (note: the first matching prefixed path is chosen).

        """
        def isprefixed(prefix, path):
            """
            Is `path` contained within the directory `prefix`.

            >>> isprefixed("/usr/local/", "/usr/local/shared")
            True
            """
            normalize = lambda path: os.path.normcase(os.path.normpath(path))
            prefix, path = normalize(prefix), normalize(path)
            if not prefix.endswith(os.path.sep):
                prefix = prefix + os.path.sep
            return os.path.commonprefix([prefix, path]) == prefix

        abspath = os.path.normpath(os.path.abspath(path))
        for prefix, base in searchpaths:
            if isprefixed(base, abspath):
                relpath = os.path.relpath(abspath, base)
                return RecentPath(abspath, prefix, relpath, **kwargs)

        return RecentPath(abspath, None, None, **kwargs)

    def search(self, searchpaths):
        """
        Return a file system path, substituting the variable paths if required

        If the self.abspath names an existing path it is returned. Else if
        the `self.prefix` and `self.relpath` are not `None` then the
        `searchpaths` sequence is searched for the matching prefix and
        if found and the {PATH}/self.relpath exists it is returned.

        If all fails return None.

        Parameters
        ----------
        searchpaths : List[Tuple[str, str]]
            A sequence of (NAME, prefixpath) pairs.

        """
        if os.path.exists(self.abspath):
            return os.path.normpath(self.abspath)

        for prefix, base in searchpaths:
            if self.prefix == prefix:
                path = os.path.join(base, self.relpath)
                if os.path.exists(path):
                    return os.path.normpath(path)

    def resolve(self, searchpaths):
        if self.prefix is None and os.path.exists(self.abspath):
            return self
        else:
            for prefix, base in searchpaths:
                path = None
                if self.prefix and self.prefix == prefix:
                    path = os.path.join(base, self.relpath)
                elif not self.prefix and prefix == "basedir":
                    path = os.path.join(base, self.basename)
                if path and os.path.exists(path):
                    return RecentPath(
                        os.path.normpath(path), self.prefix, self.relpath,
                        file_format=self.file_format)
        return None

    @property
    def basename(self):
        return os.path.basename(self.abspath)

    @property
    def icon(self):
        provider = QFileIconProvider()
        return provider.icon(QFileInfo(self.abspath))

    @property
    def dirname(self):
        return os.path.dirname(self.abspath)

    def __repr__(self):
        return ("{0.__class__.__name__}(abspath={0.abspath!r}, "
                "prefix={0.prefix!r}, relpath={0.relpath!r}, "
                "title={0.title!r})").format(self)

    __str__ = __repr__


_RecentPathSchema = {
    "abspath": str,
    "prefix": (str, ""),
    "relpath": (str, ""),
    "title": (str, ""),
    "sheet": (str, ""),
    "file_format": (str, ""),
}


def read_recent_items(settings: QSettings) -> List[RecentPath]:
    def make(*, prefix="", relpath="", file_format="", **kwargs) -> RecentPath:
        return RecentPath(
            prefix=prefix or None, relpath=relpath or None,
            file_format=file_format or None, **kwargs)
    items = QSettings_readArray(settings, "recent_path_items", _RecentPathSchema)
    return [make(**item) for item in items]


def write_recent_items(settings: QSettings, items: List[RecentPath]):
    QSettings_writeArray(settings, "recent_path_items",
                         [item.as_dict() for item in items])


def prepend_recent_item(settings: QSettings, recent: RecentPath, max_count: int = None):
    items = read_recent_items(settings)
    if recent in items:
        items.remove(recent)
    items.insert(0, recent)
    if max_count is not None:
        items = items[:max_count]
    write_recent_items(settings, items)


def _relocate(
        recent: RecentPath, search_paths: Sequence[Tuple[str, str]]
) -> RecentPath:
    kwargs = dict(title=recent.title, sheet=recent.sheet,
                  file_format=recent.file_format)
    resolved = recent.resolve(search_paths)
    if resolved is not None:
        recent = RecentPath.create(resolved.abspath, search_paths, **kwargs)
    elif True:
        path = recent.search(search_paths)
        if path is not None:
            recent = RecentPath.create(path, search_paths, **kwargs),
    return recent


class RecentPathsWidgetMixin:
    """
    Provide a setting with recent paths and relocation capabilities

    The mixin provides methods `add_path` to add paths to the top of the list,
    and `last_path` to retrieve the most recent path. The widget must also call
    `select_file(n)` to push the n-th file to the top when the user selects it
    in the combo. The recommended usage is to connect the combo box signal
    to `select_file`::

        self.file_combo.activated[int].connect(self.select_file)

    and overload the method `select_file`, for instance like this

        def select_file(self, n):
            super().select_file(n)
            self.open_file()

    The mixin works by adding a `recent_path` setting storing a list of
    instances of :obj:`RecentPath` (not pure strings). The widget can also
    manipulate the settings directly when `add_path` and `last_path` do not
    suffice.

    If the widget has a simple combo box with file names, use
    :obj:`RecentPathsWComboMixin`, which also manages the combo box.

    Since this is a mixin, make sure to explicitly call its constructor by
    `RecentPathsWidgetMixin.__init__(self)`.
    """

    #: list with search paths; overload to add, say, documentation datasets dir
    SEARCH_PATHS = []
    MAX_RECENT_ITEMS = 20

    recent_paths_default: Sequence[RecentPath] = ()
    recent_paths: List[RecentPath] = []
    current_path: Optional[RecentPath] = None

    current_path_data: 'Optional[RecentPathData]' = Setting(None)

    _init_called = False

    def __init__(self: 'OWBaseWidget'):
        super().__init__()
        if isinstance(type(self).recent_paths, Setting):
            warnings.warn(
                "`RecentPathsWidgetMixin.recent_paths` is no longer "
                "a Setting. This use is deprecated and will raise error in "
                "the future ", FutureWarning, stacklevel=3,
            )
        self._init_called = True
        self.__restore_state()
        self._relocate_recent_files()
        self.settingsAboutToBePacked.connect(self.__on_settingsAboutToBePacked)

    def _local_settings(self) -> QSettings:
        """Return a QSettings instance with local persistent settings."""
        filename = "{}.ini".format(qualified_name(type(self)))
        fname = os.path.join(settings.widget_settings_dir(), filename)
        return QSettings(fname, QSettings.IniFormat)

    def __restore_state(self):
        settings = self._local_settings()
        # preserve existing recent_paths if (still) defined;
        # recent_paths were in the past 'Setting', subclasses could/did
        # redefine them again (as Settings) so they are out of our control
        recent_paths = self.recent_paths
        recent_defaults = list(self.recent_paths_default)
        search_paths = self._search_paths()
        if self.current_path_data is not None:
            current_path = _relocate(
                RecentPath(**self.current_path_data), search_paths)
            session_items = [current_path]
        else:
            current_path = None
            session_items = []
        recent_items = (session_items + recent_paths +
                        read_recent_items(settings) + recent_defaults)
        recent_items = [_relocate(item, search_paths) for item in recent_items]
        recent_items = list(unique_everseen(
            recent_items,
            key=lambda item: (item.abspath, item.prefix, item.relpath)
        ))
        if current_path is not None:
            self.current_path = current_path
        self.recent_paths = recent_items

    def __on_settingsAboutToBePacked(self):
        if self.current_path is not None:
            self.current_path_data = self.current_path.as_dict()
        else:
            self.current_path_data = None

    def _check_init(self):
        if not self._init_called:
            raise RuntimeError("RecentPathsWidgetMixin.__init__ was not called")

    def _search_paths(self: 'OWBaseWidget'):
        basedir = self.workflowEnv().get("basedir", None)
        if basedir is None:
            return self.SEARCH_PATHS
        return self.SEARCH_PATHS + [("basedir", basedir)]

    def _relocate_recent_files(self):  # TODO: Remove this
        self._check_init()
        search_paths = self._search_paths()
        rec = []
        for recent in self.recent_paths:
            rec.append(_relocate(recent, search_paths))
        # change the list in-place for the case the widgets wraps this list
        # in some model (untested!)
        self.recent_paths[:] = rec

        if self.current_path is not None:
            self.current_path = _relocate(self.current_path, search_paths)

    def _store_recent_path(self, recent: RecentPath):
        # Store the `recent` to user pref list.
        settings = self._local_settings()
        prepend_recent_item(settings, recent, self.MAX_RECENT_ITEMS)

    def add_path(self, filename):
        """Add (or move) a file name to the top of recent paths"""
        self._check_init()
        recent = RecentPath.create(filename, self._search_paths())
        if recent in self.recent_paths:
            self.recent_paths.remove(recent)
        self.recent_paths.insert(0, recent)
        self._store_recent_path(recent)
        self.current_path = recent

    def select_file(self, n):
        """Move the n-th file to the top of the list"""
        recent = self.recent_paths[n]
        del self.recent_paths[n]
        self.recent_paths.insert(0, recent)
        self._store_recent_path(recent)

    def last_path(self):
        """Return the most recent absolute path or `None` if there is none"""
        return self.current_path.abspath if self.current_path is not None else None
        # return self.recent_paths[0].abspath if self.recent_paths else None

    @staticmethod
    def recent_paths_mixin_settings_migration_helper(settings):
        paths = settings.pop("recent_paths", [])
        if paths:
            # Used to save all items, now only the 'last' in
            # `current_path_data`.
            last = paths[0]
            current = last.as_dict()
        else:
            current = None
        settings['current_path_data'] = current


class RecentPathsWComboMixin(RecentPathsWidgetMixin):
    """
    Adds file combo handling to :obj:`RecentPathsWidgetMixin`.

    The mixin constructs a combo box `self.file_combo` and provides a method
    `set_file_list` for updating its content. The mixin also overloads the
    inherited `add_path` and `select_file` to call `set_file_list`.
    """

    def __init__(self):
        super().__init__()
        self.file_combo = \
            QComboBox(self, sizeAdjustPolicy=QComboBox.AdjustToContents)

    def add_path(self, filename):
        """Add (or move) a file name to the top of recent paths"""
        super().add_path(filename)
        self.set_file_list()

    def select_file(self, n):
        """Move the n-th file to the top of the list"""
        super().select_file(n)
        self.set_file_list()

    def set_file_list(self):
        """
        Sets the items in the file list combo
        """
        self._check_init()
        self.file_combo.clear()
        if not self.recent_paths:
            self.file_combo.addItem("(none)")
            self.file_combo.model().item(0).setEnabled(False)
        else:
            for i, recent in enumerate(self.recent_paths):
                self.file_combo.addItem(recent.basename)
                self.file_combo.model().item(i).setToolTip(recent.abspath)
                if not os.path.exists(recent.abspath):
                    self.file_combo.setItemData(i, QBrush(Qt.red),
                                                Qt.TextColorRole)

    def update_file_list(self, key, value, oldvalue):
        if key == "basedir":
            self._relocate_recent_files()
            self.set_file_list()
