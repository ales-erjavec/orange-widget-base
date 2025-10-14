import ntpath
import pathlib

import os
import unittest
from unittest.mock import Mock
from tempfile import NamedTemporaryFile

from orangewidget.utils.filedialogs import RecentPath, open_filename_dialog, \
    unambiguous_paths


class TestUtils(unittest.TestCase):
    def test_unambiguous_paths(self):
        paths = [
            "asd.txt",
            "abc/def/ghi.txt",
            "abc/def/jkl.txt",
            "abc/xyz/jkl.txt",
            "abc/xyz/rty/qwe.txt",
            "abd/xyz/rty/qwe.txt",
            "abe/xyz/rty/qwe.txt",
        ]

        paths = [t.replace("/", os.path.sep, 1) for t in paths]

        def test(exp, **kwargs):
            self.assertEqual(unambiguous_paths(paths, **kwargs),
                             [t.replace("/", os.path.sep) for t in exp])

        test(["asd.txt",
              "ghi.txt",
              "def/jkl.txt",
              "xyz/jkl.txt",
              "abc/xyz/rty/qwe.txt",
              "abd/xyz/rty/qwe.txt",
              "abe/xyz/rty/qwe.txt"])

        test(["asd.txt",
              "def/ghi.txt",
              "def/jkl.txt",
              "xyz/jkl.txt",
              "abc/xyz/rty/qwe.txt",
              "abd/xyz/rty/qwe.txt",
              "abe/xyz/rty/qwe.txt"], minlevel=2)

        test(["asd.txt",
              "abc/def/ghi.txt",
              "abc/def/jkl.txt",
              "abc/xyz/jkl.txt",
              "abc/xyz/rty/qwe.txt",
              "abd/xyz/rty/qwe.txt",
              "abe/xyz/rty/qwe.txt"], minlevel=3)

        test(["asd.txt",
              "abc/def/ghi.txt",
              "abc/def/jkl.txt",
              "abc/xyz/jkl.txt",
              "abc/xyz/rty/qwe.txt",
              "abd/xyz/rty/qwe.txt",
              "abe/xyz/rty/qwe.txt"], minlevel=4)

        # For simplicity, omit this test on Windows; if it works on Posix paths,
        # it works on Windows, too.
        if os.path.sep == "/":
            t = ["abc/def/ghi.txt", "abc/def/ghi.txt"]
            self.assertEqual(unambiguous_paths(t), t)

    @unittest.mock.patch("pathlib.Path", pathlib.PureWindowsPath)
    @unittest.mock.patch("os.path.join", ntpath.join)
    def test_unambiguous_paths_windows(self):
        paths = ["C:\\Documents/Newsletters\\Summer2018.pdf",
                 "D:\\Documents/Newsletters\\Summer2018.pdf"]
        self.assertEqual(unambiguous_paths(paths),
                         ["C:\\Documents\\Newsletters\\Summer2018.pdf",
                          "D:\\Documents\\Newsletters\\Summer2018.pdf"]
                         )

        paths = ["C:\\abc\\def\\Summer2018.pdf",
                 "C:\\abc\\deg\\Summer2018.pdf"]
        self.assertEqual(unambiguous_paths(paths),
                         ["def\\Summer2018.pdf",
                          "deg\\Summer2018.pdf", ]
                         )

        paths = ["C:\\deg\\Summer2018.pdf",
                 "D:\\deg\\Summer2018.pdf"]
        self.assertEqual(unambiguous_paths(paths),
                         ["C:\\deg\\Summer2018.pdf",
                          "D:\\deg\\Summer2018.pdf", ]
                         )


class TestRecentPath(unittest.TestCase):
    def test_resolve(self):
        temp_file = NamedTemporaryFile(dir=os.getcwd(), delete=False)
        file_name = temp_file.name
        temp_file.close()
        base_name = os.path.basename(file_name)
        try:
            recent_path = RecentPath(
                os.path.join("temp/datasets", base_name), "",
                os.path.join("datasets", base_name)
            )
            search_paths = [("basedir", os.getcwd())]
            self.assertIsNotNone(recent_path.resolve(search_paths))
        finally:
            os.remove(file_name)


class TestOpenFilenameDialog(unittest.TestCase):
    def test_empty_filter(self):
        class ABCFormat:
            EXTENSIONS = ('.abc', '.jkl')
            DESCRIPTION = 'abc file'
            PRIORITY = 30

        name, file_format, file_filter = open_filename_dialog(
            ".", "", [ABCFormat],
            dialog=Mock(return_value=("foo.xyz", "")))
        self.assertEqual(name, "foo.xyz")
        self.assertEqual(file_format, None)
        self.assertEqual(file_filter, None)

    def test_add_all(self):
        class ABCFormat:
            EXTENSIONS = ('.abc', '.jkl')
            DESCRIPTION = 'abc file'
            PRIORITY = 30

        class DEFFormat:
            EXTENSIONS = ('.def', )
            DESCRIPTION = 'def file'
            PRIORITY = 40

        # Add all known extensions
        dialog = Mock(return_value=("foo.abc", ""))
        open_filename_dialog(".", "", [ABCFormat, DEFFormat], dialog=dialog)
        self.assertIn("(*.abc *.def *.jkl)", dialog.call_args[0][3])
        self.assertIn("(*.abc *.jkl)", dialog.call_args[0][3])
        self.assertIn("(*.def)", dialog.call_args[0][3])

        # Add all extensions (*.*)
        dialog = Mock(return_value=("foo.abc", ""))
        open_filename_dialog(".", "", [ABCFormat, DEFFormat],
                             add_all="*", dialog=dialog)
        self.assertIn("(*.*)", dialog.call_args[0][3])
        self.assertIn("(*.abc *.jkl)", dialog.call_args[0][3])
        self.assertIn("(*.def)", dialog.call_args[0][3])

        # Don't add any extensions
        dialog = Mock(return_value=("foo.abc", ""))
        open_filename_dialog(".", "", [ABCFormat, DEFFormat],
                             add_all=False, dialog=dialog)
        self.assertNotIn("(*.abc *.def *.jkl)", dialog.call_args[0][3])
        self.assertIn("(*.abc *.jkl)", dialog.call_args[0][3])
        self.assertIn("(*.def)", dialog.call_args[0][3])

        # With a single format, add all known extensions is ignored
        dialog = Mock(return_value=("foo.abc", ""))
        open_filename_dialog(".", "", [ABCFormat],
                             dialog=dialog)
        self.assertNotIn("(*.*)", dialog.call_args[0][3])
        self.assertEqual(dialog.call_args[0][3].count("(*.abc *.jkl)"), 1)

        # With a single format, add all extensions (*.*) still applies
        dialog = Mock(return_value=("foo.abc", ""))
        open_filename_dialog(".", "", [ABCFormat],
                             add_all="*", dialog=dialog)
        self.assertIn("(*.*)", dialog.call_args[0][3])
        self.assertIn("(*.abc *.jkl)", dialog.call_args[0][3])


if __name__ == "__main__":
    unittest.main()
