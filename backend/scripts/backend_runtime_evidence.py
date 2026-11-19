"""Pure final-image ABI/attack-surface evidence, no device/provider/socket calls."""
import glob
import importlib.util
import json
import os
import shutil
import sqlite3
import ssl
import sys
import pyexpat
import rasterio
import pyproj
import psycopg

assert sys.version_info[:2] == (3, 12)
assert os.getuid() == os.getgid() == 10001
assert importlib.util.find_spec('pip') is None
assert not any(shutil.which(tool) for tool in ('gcc', 'cc', 'g++', 'make'))
print(json.dumps({
    'python': sys.version, 'uid': os.getuid(), 'gid': os.getgid(),
    'openssl': ssl.OPENSSL_VERSION, 'sqlite': sqlite3.sqlite_version,
    'python_expat': pyexpat.EXPAT_VERSION, 'gdal': rasterio.__gdal_version__,
    'proj': pyproj.proj_version_str, 'psycopg': psycopg.__version__,
    'runtime_pip': False, 'compiler': False,
    'shell': shutil.which('sh'),
    'shell_reason': 'Required existing seed/migrate launcher; retained, not a shell-free image claim',
    'systemd_homed_binary': shutil.which('systemd-homed'),
    'perl_archive_tar_files': glob.glob('/usr/share/perl*/**/Archive/Tar.pm', recursive=True),
    'scope': 'Final immutable runtime image, nonroot/read-only/no capabilities/network none; no exploitability waiver',
}, sort_keys=True))
