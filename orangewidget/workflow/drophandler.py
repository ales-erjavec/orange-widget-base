"""
Drag/Drop handlers for handling drop events on the canvas.

This is used to create a widget node when a file is dragged onto the canvas.

To define a handler subclass a :class:`OWNodeFromMimeDataDropHandler` or one
of its subclasses (e.g :class:`SingleUrlDropHandler`,
:class:`SingleFileDropHandler`, ...) and register it with the target
application's entry point (the default is
'orangecanvas.document.interactions.DropHandler') in the project's meta data,
e.g.::

    entry_points = {
        ...
        "orange.canvas.drophandler": [
            "The widget = fully.qualified.module:class_name",
            ...
        ],
        ...
"""
import abc
from typing import Type, Dict, Any, Sequence

from AnyQt.QtCore import QMimeData, QUrl

from orangecanvas.document.interactions import NodeFromMimeDataDropHandler
from orangecanvas.document.schemeedit import SchemeEditWidget
from orangecanvas.utils import qualified_name

from orangewidget.widget import OWBaseWidget

__all__ = [
    "OWNodeFromMimeDataDropHandler",
    "SingleUrlDropHandler",
    "UrlsDropHandler",
    "SingleFileDropHandler",
    "FilesDropHandler",
]


class OWNodeFromMimeDataDropHandler(NodeFromMimeDataDropHandler, abc.ABC):
    """
    Canvas drop handler creating a OWBaseWidget nodes.

    This implements a default :meth:`.qualifiedName`
    that is based on :attr:`.WIDGET` class attribute.
    """
    #: Class attribute declaring which OWBaseWidget (sub)class this drop
    #: handler creates. Concrete subclasses **must** assign this attribute.
    WIDGET: Type[OWBaseWidget] = None

    def qualifiedName(self) -> str:
        """Reimplemented."""
        return qualified_name(self.WIDGET)


class SingleUrlDropHandler(OWNodeFromMimeDataDropHandler):
    """
    Canvas drop handler accepting a single url drop.

    Subclasses must define :meth:`canDropUrl` and :meth:`parametersFromUrl`

    Note
    ----
    Use :class:`SingleFileDropHandler` if you only care about local
    filesystem paths.
    """
    def canDropMimeData(self, document: 'SchemeEditWidget', data: 'QMimeData') -> bool:
        """
        Reimplemented.

        Delegate to `canDropFile` method if the `data` has a single local file
        system path.
        """
        urls = data.urls()
        if len(urls) != 1:
            return False
        return self.canDropUrl(urls[0])

    def parametersFromMimeData(self, document: 'SchemeEditWidget',  data: 'QMimeData') -> 'Dict[str, Any]':
        """
        Reimplemented.

        Delegate to :meth:`parametersFromUrl` method.
        """
        return self.parametersFromUrl(data.urls()[0])

    @abc.abstractmethod
    def canDropUrl(self, url: QUrl) -> bool:
        """
        Can the handler create a node from the `url`.

        Subclasses must redefine this method.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def parametersFromUrl(self, url: QUrl) -> 'Dict[str, Any]':
        """
        Return the node parameters from `url`.

        Subclasses must redefine this method.
        """
        raise NotImplementedError


class UrlsDropHandler(OWNodeFromMimeDataDropHandler):
    """
    Canvas drop handler accepting url drops.

    Subclasses must define :meth:`canDropUrls` and :meth:`parametersFromUrls`

    Note
    ----
    Use :class:`FilesDropHandler` if you only care about local filesystem paths.
    """
    def canDropMimeData(self, document: 'SchemeEditWidget', data: 'QMimeData') -> bool:
        """
        Reimplemented.

        Delegate to :meth:`canDropUrls` method.
        """
        urls = data.urls()
        if not bool(urls):
            return False
        return self.canDropUrls(urls)

    def parametersFromMimeData(self, document: 'SchemeEditWidget', data: 'QMimeData') -> 'Dict[str, Any]':
        """
        Reimplemented.

        Delegate to :meth:`parametersFromUrls` method.
        """
        return self.parametersFromUrls(data.urls())

    @abc.abstractmethod
    def canDropUrls(self, urls: Sequence[QUrl]) -> bool:
        """
        Can the handler create a node from the `urls` list.

        Subclasses must redefine this method.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def parametersFromUrls(self, urls: Sequence[QUrl]) -> 'Dict[str, Any]':
        """
        Return the node parameters from `urls`.

        Subclasses must redefine this method.
        """
        raise NotImplementedError


class SingleFileDropHandler(OWNodeFromMimeDataDropHandler):
    """
    Canvas drop handler accepting single local file path.

    Subclasses must define :meth:`canDropFile` and :meth:`parametersFromFile`
    """
    def canDropMimeData(self, document: 'SchemeEditWidget', data: 'QMimeData') -> bool:
        """
        Reimplemented.

        Delegate to :meth:`canDropFile` method if the `data` has a single
        local file system path.
        """
        urls = data.urls()
        if len(urls) != 1 or not urls[0].isLocalFile():
            return False
        path = urls[0].toLocalFile()
        return self.canDropFile(path)

    def parametersFromMimeData(self, document: 'SchemeEditWidget',  data: 'QMimeData') -> 'Dict[str, Any]':
        """
        Reimplemented.

        Delegate to :meth:`parametersFromFile` method.
        """
        path = data.urls()[0].toLocalFile()
        return self.parametersFromFile(path)

    @abc.abstractmethod
    def canDropFile(self, path: str) -> bool:
        """
        Can the handler create a node from the file `path`.

        Subclasses must redefine this method.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def parametersFromFile(self, path: str) -> 'Dict[str, Any]':
        """
        Return the node parameters based on file `path`.

        Subclasses must redefine this method.
        """
        raise NotImplementedError


class FilesDropHandler(OWNodeFromMimeDataDropHandler):
    """
    Canvas drop handler accepting local file paths.

    Subclasses must define :meth:`canDropFiles` and :meth:`parametersFromFiles`
    """
    def canDropMimeData(self, document: 'SchemeEditWidget', data: 'QMimeData') -> bool:
        """
        Reimplemented.

        Delegate to :meth:`canDropFiles` method if the `data` has only local
        filesystem paths.
        """
        urls = data.urls()
        if not urls or not all(url.isLocalFile() for url in urls):
            return False
        paths = [url.toLocalFile() for url in urls]
        return self.canDropFiles(paths)

    def parametersFromMimeData(self, document: 'SchemeEditWidget',  data: 'QMimeData') -> 'Dict[str, Any]':
        """
        Reimplemented.

        Delegate to :meth:`parametersFromFile` method.
        """
        urls = data.urls()
        paths = [url.toLocalFile() for url in urls]
        return self.parametersFromFiles(paths)

    @abc.abstractmethod
    def canDropFiles(self, paths: Sequence[str]) -> bool:
        """
        Can the handler create a node from the `paths`.

        Subclasses must redefine this method.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def parametersFromFiles(self, paths: Sequence[str]) -> 'Dict[str, Any]':
        """
        Return the node parameters based on `paths`.

        Subclasses must redefine this method.
        """
        raise NotImplementedError
