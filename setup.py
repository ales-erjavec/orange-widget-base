#! /usr/bin/env python3

import os
import subprocess
from setuptools import setup, find_packages, Command

NAME = 'orange-widget-base'
VERSION = '4.9.0'
ISRELEASED = False
# full version identifier including a git revision identifier for development
# build/releases (this is filled/updated in `write_version_py`)
FULLVERSION = VERSION

DESCRIPTION = 'Base Widget for Orange Canvas'
README_FILE = os.path.join(os.path.dirname(__file__), 'README.md')
LONG_DESCRIPTION = """
This project implements the base OWBaseWidget class and utilities for use in
Orange Canvas workflows.

Provides:

    * `OWBaseWidget` class
    * `gui` module for building GUI
    * `WidgetsScheme` the workflow execution model/bridge
    * basic configuration for a workflow based application

"""
AUTHOR = 'Bioinformatics Laboratory, FRI UL'
AUTHOR_EMAIL = 'info@biolab.si'
URL = 'http://orange.biolab.si/'
LICENSE = 'GPLv3+'

KEYWORDS = (
    'workflow', 'widget'
)

CLASSIFIERS = (
    'Development Status :: 4 - Beta',
    'Environment :: X11 Applications :: Qt',
    'Environment :: Console',
    'Environment :: Plugins',
    'Programming Language :: Python',
    'License :: OSI Approved :: '
    'GNU General Public License v3 or later (GPLv3+)',
    'Operating System :: POSIX',
    'Operating System :: Microsoft :: Windows',
    'Topic :: Scientific/Engineering :: Artificial Intelligence',
    'Topic :: Scientific/Engineering :: Visualization',
    'Topic :: Software Development :: Libraries :: Python Modules',
    'Intended Audience :: Education',
    'Intended Audience :: Science/Research',
    'Intended Audience :: Developers',
)

INSTALL_REQUIRES = [
    "matplotlib",
    "pyqtgraph",
    "AnyQt",
    "orange-canvas-core>=0.1.13,<0.2a",
    'appnope; sys_platform=="darwin"',
    'more-itertools',
]

EXTRAS_REQUIRE = {
}

ENTRY_POINTS = {
}

DATA_FILES = []


# Return the git revision as a string
def git_version():
    """Return the git revision as a string.

    Copied from numpy setup.py
    """
    def _minimal_ext_cmd(cmd):
        # construct minimal environment
        env = {}
        for k in ['SYSTEMROOT', 'PATH']:
            v = os.environ.get(k)
            if v is not None:
                env[k] = v
        # LANGUAGE is used on win32
        env['LANGUAGE'] = 'C'
        env['LANG'] = 'C'
        env['LC_ALL'] = 'C'
        out = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, env=env)
        return out.stdout

    try:
        out = _minimal_ext_cmd(['git', 'rev-parse', 'HEAD'])
        GIT_REVISION = out.strip().decode('ascii')
    except OSError:
        GIT_REVISION = "Unknown"
    return GIT_REVISION


def write_version_py(filename='orangewidget/version.py'):
    # Copied from numpy setup.py
    cnt = f"""\
# THIS FILE IS GENERATED FROM {NAME.upper()} SETUP.PY
short_version = '%(version)s'
version = '%(version)s'
full_version = '%(full_version)s'
git_revision = '%(git_revision)s'
release = %(isrelease)s

if not release:
    version = full_version
    short_version += ".dev"
"""
    global FULLVERSION
    FULLVERSION = VERSION
    if os.path.exists('.git'):
        GIT_REVISION = git_version()
    elif os.path.exists(filename):
        # must be a source distribution, use existing version file
        import imp
        version = imp.load_source("orangewidget.version", filename)
        GIT_REVISION = version.git_revision
    else:
        GIT_REVISION = "Unknown"

    if not ISRELEASED:
        FULLVERSION += '.dev0+' + GIT_REVISION[:7]

    a = open(filename, 'w')
    try:
        a.write(cnt % {'version': VERSION,
                       'full_version': FULLVERSION,
                       'git_revision': GIT_REVISION,
                       'isrelease': str(ISRELEASED)})
    finally:
        a.close()


PACKAGES = find_packages()

# Extra non .py, .{so,pyd} files that are installed within the package dir
# hierarchy
PACKAGE_DATA = {
    "orangewidget": ["icons/*.png", "icons/*.svg"],
    "orangewidget.report": ["icons/*.svg", "*.html"],
    "orangewidget.utils": ["_webview/*.js"],
}


def setup_package():
    write_version_py()

    setup(
        name=NAME,
        version=FULLVERSION,
        description=DESCRIPTION,
        long_description=LONG_DESCRIPTION,
        long_description_content_type="text/x-rst",
        author=AUTHOR,
        author_email=AUTHOR_EMAIL,
        url=URL,
        license=LICENSE,
        keywords=KEYWORDS,
        classifiers=CLASSIFIERS,
        packages=PACKAGES,
        package_data=PACKAGE_DATA,
        data_files=DATA_FILES,
        install_requires=INSTALL_REQUIRES,
        extras_require=EXTRAS_REQUIRE,
        entry_points=ENTRY_POINTS,
        python_requires=">=3.6",
        zip_safe=False,
    )


if __name__ == '__main__':
    setup_package()
