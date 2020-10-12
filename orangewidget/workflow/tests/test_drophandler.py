from unittest import mock
from typing import Sequence

from AnyQt.QtCore import Qt, QUrl, QPoint, QMimeData
from AnyQt.QtGui import QDropEvent, QDragEnterEvent
from AnyQt.QtWidgets import QApplication, QWidget

from orangecanvas.registry import WidgetRegistry, WidgetDescription
from orangecanvas.document.interactions import PluginDropHandler, EntryPoint
from orangecanvas.document.schemeedit import SchemeEditWidget

from orangewidget.tests.base import GuiTest
from orangewidget.settings import Setting
from orangewidget.widget import OWBaseWidget
from orangewidget.workflow.widgetsscheme import WidgetsScheme
from orangewidget.workflow.drophandler import (
    SingleFileDropHandler, SingleUrlDropHandler, UrlsDropHandler,
    FilesDropHandler
)


class Widget(OWBaseWidget):
    name = "eman"
    param = Setting("")


class WidgetSingleUrlDropHandler(SingleUrlDropHandler):
    WIDGET = Widget

    def canDropUrl(self, url: QUrl) -> bool:
        return True

    def parametersFromUrl(self, url: QUrl) -> 'Dict[str, Any]':
        return {"param": url.toString()}


class WidgetSingleFileDropHandler(SingleFileDropHandler):
    WIDGET = Widget

    def canDropFile(self, path: str) -> bool:
        return True

    def parametersFromFile(self, path: str) -> 'Dict[str, Any]':
        return {"param": path}


class WidgetUrlsDropHandler(UrlsDropHandler):
    WIDGET = Widget

    def canDropUrls(self, urls: Sequence[QUrl]) -> bool:
        return True

    def parametersFromUrls(self, urls: Sequence[QUrl]) -> 'Dict[str, Any]':
        return {"param": [url.toString() for url in urls]}


class WidgetFilesDropHandler(FilesDropHandler):
    WIDGET = Widget

    def canDropFiles(self, files: Sequence[str]) -> bool:
        return True

    def parametersFromFiles(self, paths: Sequence[str]) -> 'Dict[str, Any]':
        return {"param": list(paths)}


def mock_iter_entry_points(module, attr):
    return mock.patch.object(
        PluginDropHandler, "iterEntryPoints",
        lambda _: [EntryPoint("AA", f"{module}:{attr}", "foo")]
    )


class TestDropHandlers(GuiTest):
    def setUp(self):
        super().setUp()
        reg = WidgetRegistry()
        reg.register_widget(
            WidgetDescription(**Widget.get_widget_description(), category="A")
        )
        self.w = SchemeEditWidget()
        self.w.setRegistry(reg)
        self.w.resize(300, 300)
        self.w.setScheme(WidgetsScheme())
        self.w.setDropHandlers([PluginDropHandler()])

    def tearDown(self):
        self.w.scheme().clear()
        del self.w
        super().tearDown()

    @mock_iter_entry_points(__name__, WidgetSingleUrlDropHandler.__name__)
    def test_single_file_drop(self):
        w = self.w
        workflow = w.scheme()
        view = w.view()
        mime = QMimeData()
        mime.setUrls([QUrl("file:///foo/bar")])
        dragDrop(view.viewport(), mime, QPoint(10, 10))
        self.assertEqual(len(workflow.nodes), 1)
        self.assertEqual(workflow.nodes[0].properties,
                         {"param": "file:///foo/bar"})

    @mock_iter_entry_points(__name__, WidgetSingleFileDropHandler.__name__)
    def test_single_url_drop(self):
        w = self.w
        workflow = w.scheme()
        view = w.view()
        mime = QMimeData()
        mime.setUrls([QUrl("file:///foo/bar")])
        dragDrop(view.viewport(), mime, QPoint(10, 10))
        self.assertEqual(len(workflow.nodes), 1)
        self.assertEqual(workflow.nodes[0].properties,
                         {"param": "/foo/bar"})

    @mock_iter_entry_points(__name__, WidgetUrlsDropHandler.__name__)
    def test_urls_drop(self):
        w = self.w
        workflow = w.scheme()
        mime = QMimeData()
        mime.setUrls([QUrl("file:///foo/bar")] * 2)
        dragDrop(w.view().viewport(), mime, QPoint(10, 10))
        self.assertEqual(len(workflow.nodes), 1)
        self.assertEqual(workflow.nodes[0].properties,
                         {"param": ["file:///foo/bar"] * 2})

    @mock_iter_entry_points(__name__, WidgetFilesDropHandler.__name__)
    def test_files_drop(self):
        w = self.w
        workflow = w.scheme()
        mime = QMimeData()
        mime.setUrls([QUrl("file:///foo/bar")] * 2)
        dragDrop(w.view().viewport(), mime, QPoint(10, 10))
        self.assertEqual(len(workflow.nodes), 1)
        self.assertEqual(workflow.nodes[0].properties,
                         {"param": ["/foo/bar"] * 2})


def dragDrop(
        widget: 'QWidget', mime: QMimeData, pos: QPoint = QPoint(-1, -1),
        action=Qt.CopyAction, buttons=Qt.LeftButton, modifiers=Qt.NoModifier
) -> bool:
    if pos == QPoint(-1, -1):
        pos = widget.rect().center()

    ev = QDragEnterEvent(pos, action, mime, buttons, modifiers)
    ev.setAccepted(False)
    QApplication.sendEvent(widget, ev)
    if not ev.isAccepted():
        return False
    ev = QDropEvent(pos, action, mime, buttons, modifiers)
    ev.setAccepted(False)
    QApplication.sendEvent(widget, ev)
    return ev.isAccepted()
