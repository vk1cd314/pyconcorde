""" setup command to build Concorde Cython wrappers.

By default, the setup script will download the QSOpt linear solver, and
download and compile Concorde. If you have either one of these packages
already installed, you can use the installed version by setting the
following environment variables:

QSOPT_DIR: should point to a folder containing qsopt.a and qsopt.h
CONCORDE_DIR: contains concorde.a and concorde.h

Note that for the build process to work correctly, you should either
not set these variables (and rely on the downloaded Concorde) or set
both of them. Setting only one will not work as intended.

"""
from __future__ import print_function

import os
from os.path import exists, join as pjoin
import platform
import shutil
import subprocess

try:
    import urllib.request

    urlretrieve = urllib.request.urlretrieve
except ImportError:  # python 2
    from urllib import urlretrieve

from setuptools import find_packages, setup, Extension
from setuptools.command.build_ext import build_ext as _build_ext

from Cython.Build import cythonize

import numpy as np

QSOPT_LOCATION = {
    "Darwin": {
        "arm64": (
            "https://www.math.uwaterloo.ca/~bico/qsopt/downloads/codes/m1/qsopt.a",
            "https://www.math.uwaterloo.ca/~bico/qsopt/downloads/codes/m1/qsopt.h",
        ),
        "x86_64": (
            "https://www.math.uwaterloo.ca/~bico/qsopt/downloads/codes/mac64/qsopt.a",
            "https://www.math.uwaterloo.ca/~bico/qsopt/downloads/codes/mac64/qsopt.h",
        ),
    },
    "Linux": {
        "x86_64": (
            "https://www.math.uwaterloo.ca/~bico/qsopt/beta/codes/PIC/qsopt.PIC.a",
            "https://www.math.uwaterloo.ca/~bico/qsopt/beta/codes/PIC/qsopt.h",
        ),
    },
}

CONCORDE_SRC = "https://www.math.uwaterloo.ca/tsp/concorde/downloads/codes/src/co031219.tgz"  # noqa


def _safe_makedirs(*paths):
    for path in paths:
        try:
            os.makedirs(path)
        except os.error:
            pass


def download_concorde_qsopt():
    _safe_makedirs("data")
    _safe_makedirs("build")
    qsopt_a_path = pjoin("data", "qsopt.a")
    qsopt_h_path = pjoin("data", "qsopt.h")
    if not exists(qsopt_a_path) or not exists(qsopt_h_path):
        print("qsopt is missing, downloading")
        machine = platform.machine()
        qsopt_a_url, qsopt_h_url = QSOPT_LOCATION[platform.system()][machine]
        urlretrieve(qsopt_a_url, qsopt_a_path)
        urlretrieve(qsopt_h_url, qsopt_h_path)
    concorde_src_path = pjoin("build", "concorde.tgz")
    if not exists(concorde_src_path):
        print("concorde is missing, downloading")
        urlretrieve(CONCORDE_SRC, concorde_src_path)


def _run(cmd, cwd):
    subprocess.check_call(cmd, shell=True, cwd=cwd)


def build_concorde():
    if not exists("data/concorde.h") or not exists("data/concorde.a"):
        print("building concorde")
        _run("tar xzvf concorde.tgz", "build")

        # Replace Concorde's prehistoric Autotools helper scripts with
        # modern ones so that the build recognises contemporary 64-bit
        # Linux hosts such as x86_64-unknown-linux-gnu.
        try:
            autotools_dir = "/usr/share/misc"  # Debian/Ubuntu (autotools-dev)
            for helper in ("config.guess", "config.sub"):
                helper_path = os.path.join(autotools_dir, helper)
                if os.path.exists(helper_path):
                    shutil.copy(helper_path, f"build/concorde/{helper}")
        except Exception:
            # Best-effort; if this fails we fall back to the originals.
            pass

        # Enable default (glibc) feature macros so legacy networking code
        # sees e.g. `h_addr` in <netdb.h>, and ensure we build position-
        # independent code with optimisation and symbols.
        cflags = "-fPIC -O2 -g -D_DEFAULT_SOURCE"

        if platform.system().startswith("Darwin"):
            flags = "--host=darwin"
        else:
            # Let configure decide the canonical host/build; now that we
            # ship modern config.guess/config.sub this will succeed and we
            # avoid cross-compile mode (which had disabled feature tests and
            # produced wrong prototypes like gethostname).
            flags = ""

        datadir = os.path.abspath("data")
        cwd = (
            'CC="gcc" CFLAGS="{cflags}" ./configure --prefix {data} '
            "--with-qsopt={data} {flags}"
        ).format(cflags=cflags, data=datadir, flags=flags).strip()

        _run(cwd, "build/concorde")
        # Work around obsolete typedef macros that conflict with modern
        # system headers.  The configure script defines `#define u_char
        # unsigned char` if it does not detect the BSD typedef from
        # <sys/types.h>.  On current glibc systems that typedef exists but
        # configure (running in strict ANSI mode) does not see it, so the
        # macro redefinition causes a hard error.  We comment it out.
        cfg_hdr = "build/concorde/INCLUDE/config.h"
        try:
            with open(cfg_hdr, "r", encoding="utf-8") as f:
                cfg_lines = f.readlines()
            with open(cfg_hdr, "w", encoding="utf-8") as f:
                for ln in cfg_lines:
                    if ln.strip().startswith("#define u_char"):
                        f.write("/* " + ln.strip() + " (removed by setup.py) */\n")
                    else:
                        f.write(ln)
        except Exception:
            pass

        # Build Concorde library with the flags already recorded in the
        # generated Makefiles (they now include -D_DEFAULT_SOURCE plus the
        # required -I paths).  Do not override them here.
        _run('make', "build/concorde")

        shutil.copyfile("build/concorde/concorde.a", "data/concorde.a")
        shutil.copyfile("build/concorde/concorde.h", "data/concorde.h")

        # The 20-year-old Autotools probe can mis-detect the gethostname
        # prototype with modern glibc (expects `int`, gets `size_t`) and
        # therefore asks concorde.h to declare its own obsolete version.
        # That clashes later when we compile the Python extension.  Remove
        # the stale macro so the duplicate prototype is not emitted.
        hdr_path = "data/concorde.h"
        try:
            with open(hdr_path, "r", encoding="utf-8") as h:
                lines = h.readlines()
            with open(hdr_path, "w", encoding="utf-8") as h:
                for line in lines:
                    if "#define CC_PROTO_GETHOSTNAME" in line:
                        h.write("/* " + line.strip() + " (disabled by setup.py) */\n")
                    else:
                        h.write(line)
        except Exception:
            pass


class build_ext(_build_ext, object):
    """Build command that downloads and installs Concorde, if not found."""

    def run(self):
        if not self.has_external_concorde:
            download_concorde_qsopt()
            build_concorde()
        else:
            print("Using external Concorde/QSOpt")

        super(build_ext, self).run()

    @property
    def has_external_concorde(self):
        qsopt_dir = os.environ.get("QSOPT_DIR")
        concorde_dir = os.environ.get("CONCORDE_DIR")
        return bool(qsopt_dir) and bool(concorde_dir)


class ConcordeExtension(Extension, object):
    """Extension that sets Concorde/QSOpt lib/include args."""

    def __init__(self, *args, **kwargs):
        super(ConcordeExtension, self).__init__(*args, **kwargs)
        qsopt_dir = os.environ.get("QSOPT_DIR", "data")
        concorde_dir = os.environ.get("CONCORDE_DIR", "data")
        self.include_dirs.append(concorde_dir)
        self.extra_objects.extend(
            [pjoin(qsopt_dir, "qsopt.a"), pjoin(concorde_dir, "concorde.a")]
        )


setup(
    name="pyconcorde",
    ext_modules=cythonize(
        [
            ConcordeExtension(
                "concorde._concorde",
                sources=["concorde/_concorde.pyx"],
                include_dirs=[np.get_include()],
            )
        ]
    ),
    version="0.1.0",
    install_requires=[
        "cython>=0.22.0",
        "numpy>=1.21.0",
        "tsplib95",
    ],
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    license="BSD",
    author="Joris Vankerschaver",
    author_email="joris.vankerschaver@gmail.com",
    url="https://github.com/jvkersch/pyconcorde",
    description="Cython wrappers for the Concorde TSP library",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "License :: OSI Approved :: BSD License",
    ],
    cmdclass={
        "build_ext": build_ext,
    },
)
