"""Build the portable Windows x64 app and its public release archive."""
import hashlib
from importlib.metadata import distribution
import os
from pathlib import Path
import platform
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'LuminChannelSwitch'


def main():
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64') or sys.maxsize <= 2**32:
        raise SystemExit('Build with 64-bit Python on Windows x64.')
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11 or later is required.')
    subprocess.run(['dotnet','build',str(ROOT/'tools'/'LauncherPreferences'/'LauncherPreferences.csproj'),
                    '-c','Release','-o',str(ROOT/'src'/'data'/'prefs')],cwd=ROOT,check=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onedir', '--windowed', '--noupx', '--name', NAME,
               '--icon', str(ROOT/'assets'/'lumin-switch.ico'),
               '--add-data', f'{ROOT/"src"/"data"}{os.pathsep}data',
               '--add-data', f'{ROOT/"assets"/"lumin-switch.ico"}{os.pathsep}.',
               str(ROOT/'src'/'app.py')]
    subprocess.run(command, cwd=ROOT, check=True)
    binary_dir = ROOT/'dist'/NAME
    release = ROOT/'release'
    release.mkdir(exist_ok=True)
    archive = release/(NAME+'-Windows-x64.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(binary_dir.rglob('*')):
            if f.is_file():
                z.write(f, f'{NAME}/{f.relative_to(binary_dir).as_posix()}')
        z.write(ROOT/'使用说明.txt',f'{NAME}/使用说明.txt')
        runtime = Path(sys.base_prefix)
        licenses = [
            (runtime/'LICENSE.txt','Python.txt'),
            (runtime/'tcl'/'tk8.6'/'license.terms','Tcl-Tk.txt'),
        ]
        pi = distribution('pyinstaller')
        for path in pi.files or []:
            if path.name == 'COPYING.txt' and 'dist-info' in str(path):
                licenses.append((Path(pi.locate_file(path)),'PyInstaller.txt'))
                break
        for path,label in licenses:
            if path.is_file():
                z.write(path,f'{NAME}/licenses/{label}')
    with archive.open('rb') as f:
        digest = hashlib.file_digest(f,'sha256').hexdigest()
    line = f'{digest}  {archive.name}\n'
    (release/'SHA256SUMS.txt').write_bytes(line.encode('ascii'))
    (release/(archive.name+'.sha256')).write_bytes(line.encode('ascii'))
    print(archive)
    print(line, end='')


if __name__ == '__main__':
    main()
