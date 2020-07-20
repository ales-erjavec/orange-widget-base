import os
import sys
import tempfile
from collections import OrderedDict

from AnyQt import QtGui, QtCore, QtSvg
from AnyQt.QtCore import QMimeData, QMarginsF
from AnyQt.QtWidgets import (
    QGraphicsScene, QGraphicsView, QWidget, QApplication
)

from orangewidget.utils.matplotlib_export import scene_code

try:
    from orangewidget.utils.webview import WebviewWidget
except ImportError:
    WebviewWidget = None

__all__ = [
    "ImgFormat", "Compression", "PngFormat", "ClipboardFormat", "SvgFormat",
    "MatplotlibPDFFormat", "MatplotlibFormat", "PdfFormat",
]


class Compression:
    """Supported compression extensions"""
    GZIP = '.gz'
    BZIP2 = '.bz2'
    XZ = '.xz'
    all = (GZIP, BZIP2, XZ)


class _Registry(type):
    """Metaclass that registers subtypes."""
    def __new__(mcs, name, bases, attrs):
        cls = type.__new__(mcs, name, bases, attrs)
        if not hasattr(cls, 'registry'):
            cls.registry = OrderedDict()
        else:
            cls.registry[name] = cls
        return cls

    def __iter__(cls):
        return iter(cls.registry)

    def __str__(cls):
        if cls in cls.registry.values():
            return cls.__name__
        return '{}({{{}}})'.format(cls.__name__, ', '.join(cls.registry))


class classproperty(property):
    def __get__(self, instance, class_):
        return self.fget(class_)


class ImgFormat(metaclass=_Registry):
    PRIORITY = sys.maxsize

    @staticmethod
    def _get_buffer(size, filename):
        raise NotImplementedError

    @staticmethod
    def _get_target(scene, painter, buffer, source):
        return QtCore.QRectF(0, 0, source.width(), source.height())

    @staticmethod
    def _save_buffer(buffer, filename):
        raise NotImplementedError

    @staticmethod
    def _get_exporter():
        raise NotImplementedError

    @staticmethod
    def _export(self, exporter, filename):
        raise NotImplementedError

    @classmethod
    def write_image(cls, filename, scene):
        try:
            scene = scene.scene()
            scenerect = scene.sceneRect()   #preserve scene bounding rectangle
            viewrect = scene.views()[0].sceneRect()
            scene.setSceneRect(viewrect)
            backgroundbrush = scene.backgroundBrush()  #preserve scene background brush
            scene.setBackgroundBrush(QtCore.Qt.white)
            exporter = cls._get_exporter()
            cls._export(exporter(scene), filename)
            scene.setBackgroundBrush(backgroundbrush)  # reset scene background brush
            scene.setSceneRect(scenerect)   # reset scene bounding rectangle
        except Exception:
            if isinstance(scene, (QGraphicsScene, QGraphicsView)):
                rect = scene.sceneRect()
            elif isinstance(scene, QWidget):
                rect = scene.rect()
            rect = rect.adjusted(-15, -15, 15, 15)
            buffer = cls._get_buffer(rect.size(), filename)

            painter = QtGui.QPainter()
            painter.begin(buffer)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            if QtCore.QT_VERSION >= 0x050D00:
                painter.setRenderHint(QtGui.QPainter.LosslessImageRendering)

            target = cls._get_target(scene, painter, buffer, rect)
            try:
                scene.render(painter, target, rect)
            except TypeError:
                scene.render(painter)  # QWidget.render() takes different params
            painter.end()
            cls._save_buffer(buffer, filename)

    @classmethod
    def write(cls, filename, scene):
        if type(scene) == dict:
            scene = scene['scene']
        cls.write_image(filename, scene)

    @classproperty
    def img_writers(cls):  # type: () -> Mapping[str, Type[ImgFormat]]
        formats = OrderedDict()
        for format in sorted(cls.registry.values(), key=lambda x: x.PRIORITY):
            for ext in getattr(format, 'EXTENSIONS', []):
                # Only adds if not yet registered
                formats.setdefault(ext, format)
        return formats

    graph_writers = img_writers

    @classproperty
    def formats(cls):
        return cls.registry.values()


class PngFormat(ImgFormat):
    EXTENSIONS = ('.png',)
    DESCRIPTION = 'Portable Network Graphics'
    PRIORITY = 50

    @staticmethod
    def _get_buffer(size, filename):
        return QtGui.QPixmap(int(size.width()), int(size.height()))

    @staticmethod
    def _get_target(scene, painter, buffer, source):
        try:
            brush = scene.backgroundBrush()
            if brush.style() == QtCore.Qt.NoBrush:
                brush = QtGui.QBrush(scene.palette().color(QtGui.QPalette.Base))
        except AttributeError:  # not a QGraphicsView/Scene
            brush = QtGui.QBrush(QtCore.Qt.white)
        painter.fillRect(buffer.rect(), brush)
        return QtCore.QRectF(0, 0, source.width(), source.height())

    @staticmethod
    def _save_buffer(buffer, filename):
        buffer.save(filename, "png")

    @staticmethod
    def _get_exporter():
        from pyqtgraph.exporters.ImageExporter import ImageExporter
        return ImageExporter

    @staticmethod
    def _export(exporter, filename):
        buffer = exporter.export(toBytes=True)
        buffer.save(filename, "png")


class ClipboardFormat(PngFormat):
    EXTENSIONS = ()
    DESCRIPTION = 'System Clipboard'
    PRIORITY = 50

    @staticmethod
    def _save_buffer(buffer, _):
        QApplication.clipboard().setPixmap(buffer)

    @staticmethod
    def _export(exporter, _):
        buffer = exporter.export(toBytes=True)
        mimedata = QMimeData()
        mimedata.setData("image/png", buffer)
        QApplication.clipboard().setMimeData(mimedata)


class SvgFormat(ImgFormat):
    EXTENSIONS = ('.svg',)
    DESCRIPTION = 'Scalable Vector Graphics'
    PRIORITY = 100

    @staticmethod
    def _get_buffer(size, filename):
        buffer = QtSvg.QSvgGenerator()
        buffer.setResolution(QApplication.desktop().logicalDpiX())
        buffer.setFileName(filename)
        buffer.setViewBox(QtCore.QRectF(0, 0, size.width(), size.height()))
        return buffer

    @staticmethod
    def _save_buffer(buffer, filename):
        dev = buffer.outputDevice()
        if dev is not None:
            dev.flush()
        pass

    @staticmethod
    def _get_exporter():
        from Orange.widgets.utils.SVGExporter import SVGExporter
        return SVGExporter

    @staticmethod
    def _export(exporter, filename):
        exporter.export(filename)

    @classmethod
    def write_image(cls, filename, scene):
        # WebviewWidget exposes its SVG contents more directly;
        # no need to go via QPainter if we can avoid it
        svg = None
        if WebviewWidget is not None and isinstance(scene, WebviewWidget):
            try:
                svg = scene.svg()
            except (ValueError, IOError):
                pass
        if svg is None:
            super().write_image(filename, scene)
            svg = open(filename).read()
        svg = svg.replace(
            "<svg ",
            '<svg style="image-rendering:optimizeSpeed;image-rendering:pixelated" ')
        with open(filename, 'w') as f:
            f.write(svg)


class MatplotlibFormat:
    # not registered as a FileFormat as it only works with scatter plot
    EXTENSIONS = ('.py',)
    DESCRIPTION = 'Python Code (with Matplotlib)'
    PRIORITY = 300

    @classmethod
    def write_image(cls, filename, scene):
        code = scene_code(scene) + "\n\nplt.show()"
        with open(filename, "wt") as f:
            f.write(code)

    @classmethod
    def write(cls, filename, scene):
        if type(scene) == dict:
            scene = scene['scene']
        cls.write_image(filename, scene)


class MatplotlibPDFFormat(MatplotlibFormat):
    EXTENSIONS = ('.pdf',)
    DESCRIPTION = 'Portable Document Format (from Matplotlib)'
    PRIORITY = 200

    @classmethod
    def write_image(cls, filename, scene):
        import matplotlib
        with matplotlib.rc_context({"backend": "pdf"}):
            code = scene_code(scene) + "\n\nplt.savefig({})".format(repr(filename))
            exec(code, {})  # will generate a pdf


if QtCore.QT_VERSION >= 0x050C00:  # Qt 5.12+

    class PdfFormat(ImgFormat):
        EXTENSIONS = ('.pdf', )
        DESCRIPTION = 'Portable Document Format'
        PRIORITY = 110

        @staticmethod
        def _get_buffer(size, filename):
            buffer = QtGui.QPdfWriter(filename)
            dpi = QApplication.desktop().logicalDpiX()
            buffer.setResolution(dpi)
            buffer.setPageMargins(QMarginsF(0, 0, 0, 0))
            buffer.setPageSizeMM(QtCore.QSizeF(size.width(), size.height()) / dpi * 25.4)
            return buffer

        @staticmethod
        def _save_buffer(buffer, filename):
            pass

        @staticmethod
        def _get_exporter():
            from orangewidget.utils.PDFExporter import PDFExporter
            return PDFExporter

        @staticmethod
        def _export(exporter, filename):
            exporter.export(filename)

else:

    # older Qt version have PdfWriter bugs and are handled through SVG

    class PdfFormat(ImgFormat):
        EXTENSIONS = ('.pdf', )
        DESCRIPTION = 'Portable Document Format'
        PRIORITY = 110

        @classmethod
        def write_image(cls, filename, scene):
            # export via svg to temp file then print that
            # NOTE: can't use NamedTemporaryFile with delete = True
            # (see https://bugs.python.org/issue14243)
            fd, tmpname = tempfile.mkstemp(suffix=".svg")
            os.close(fd)
            try:
                SvgFormat.write_image(tmpname, scene)
                with open(tmpname, "rb") as f:
                    svgcontents = f.read()
            finally:
                os.unlink(tmpname)

            svgrend = QtSvg.QSvgRenderer(QtCore.QByteArray(svgcontents))
            vbox = svgrend.viewBox()
            if not vbox.isValid():
                size = svgrend.defaultSize()
            else:
                size = vbox.size()
            writer = QtGui.QPdfWriter(filename)
            writer.setPageSizeMM(QtCore.QSizeF(size) * 0.282)
            painter = QtGui.QPainter(writer)
            svgrend.render(painter)
            painter.end()
            del svgrend
            del painter
