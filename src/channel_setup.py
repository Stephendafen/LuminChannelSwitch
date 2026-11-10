"""Install versioned publisher files in the current user's cache."""
import base64
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen
import uuid
import zipfile

from lumin_switch import APP, CONFIG, Installation, SwitchError, exclusive, guarded

DATA = Path(__file__).resolve().parent / 'data'
LAUNCHER = APP / 'launchers' / 'bilibili'
LAYOUT_PIN = 'b43cce2a8bf5c3d37be4ce7db97c60eb707fafd5c6fc9872ea5fc44eed26ecb7'
BOOTSTRAP = 'https://pkg.biligame.com/games/lmskzsPCb_bilibili_20261014_052628b8bfaf08b884c21982af969e9681096647f84c3a.exe'
BOOTSTRAP_SIZE = 88730064
BOOTSTRAP_PIN = 'f374b48fb68d11cd9811ce37fbb88c6980869d303cd8cc6d7b65fc67bdd6692d'
BASE_URL = 'https://lm-package.zhuziplay.com/Win64/'
ARCHIVES = {
    'zhuzi': ('12055/12055_12450_Prod_zhuzi_20261005_142637.zip', 549890835),
    'zhuzi_gf': ('11970/11970_12450_Prod_zhuzi_gf_20260924_140117.zip', 552439787),
    'bilibili': ('11974/11974_12450_Prod_bilibili_20260924_142327.zip', 635267901),
}


def layout():
    raw = (DATA/'launcher-layout.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != LAYOUT_PIN:
        raise SwitchError('启动器文件索引损坏，请重新解压工具。')
    return json.loads(raw)


def matches(path, record, check_hash=True):
    guarded(path)
    if not path.is_file() or path.stat().st_size != record['size']:
        return False
    if not check_hash:
        return True
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == record['sha256']


def write_verified(dest, source, record):
    guarded(dest).parent.mkdir(parents=True, exist_ok=True)
    temp = guarded(dest.with_name(dest.name+'.setup-part'))
    try:
        size = 0
        digest = hashlib.sha256()
        with temp.open('wb') as out:
            while chunk := source.read(min(4*1024*1024,record['size']-size+1)):
                size += len(chunk)
                if size > record['size']:
                    raise SwitchError(f'文件大小不符：{dest.name}')
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if size != record['size'] or digest.hexdigest() != record['sha256']:
            raise SwitchError(f'发行文件校验失败：{dest.name}')
        os.replace(temp,dest)
    finally:
        temp.unlink(missing_ok=True)


class ArchiveReader(io.RawIOBase):
    """Support ZIP seeks using HTTPS range requests or a single full download."""
    def __init__(self, channel, progress):
        name, self.size = ARCHIVES[channel]
        self.url, self.pos = BASE_URL+name, 0
        self.offset, self.buffer = 0,b''
        self.spool = None
        self.progress = progress

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        pos = (0,self.pos,self.size)[whence]+offset
        if pos < 0:
            raise OSError('ZIP 偏移无效')
        self.pos = pos
        return pos

    def seekable(self):
        return True

    def read(self, n=-1):
        remaining = self.size-self.pos
        n = remaining if n<0 else min(n,remaining)
        if n<=0:
            return b''
        if self.spool is None and not (self.offset <= self.pos and
                                       self.pos+n <= self.offset+len(self.buffer)):
            self._request(min(self.size-1,self.pos+max(n,512*1024)-1))
        if self.spool is not None:
            self.spool.seek(self.pos)
            data = self.spool.read(n)
        else:
            i = self.pos-self.offset
            data = self.buffer[i:i+n]
        self.pos += len(data)
        return data

    def _request(self, last):
        headers = {'Range':f'bytes={self.pos}-{last}', 'Accept-Encoding':'identity'}
        for attempt in range(3):
            try:
                with urlopen(Request(self.url,headers=headers),timeout=65) as res:
                    if not res.geturl().lower().startswith('https://'):
                        raise SwitchError('发行方下载被重定向到非 HTTPS 地址。')
                    if res.status == 206 and res.headers.get('Content-Range') == f'bytes {self.pos}-{last}/{self.size}':
                        block = res.read(last-self.pos+1)
                        if len(block) != last-self.pos+1:
                            raise OSError('下载中断')
                        self.offset,self.buffer = self.pos,block
                        return
                    if res.status != 200:
                        raise SwitchError('下载服务器返回了异常的文件范围。')
                    self.progress('服务器不支持分段读取，正在下载完整基础包…')
                    spool = tempfile.TemporaryFile()
                    try:
                        while block := res.read(2*1024*1024):
                            if spool.tell()+len(block)>self.size:
                                raise SwitchError('基础包大于发行方清单中的大小。')
                            spool.write(block)
                        if spool.tell()!=self.size:
                            raise OSError('下载不完整')
                    except BaseException:
                        spool.close()
                        raise
                    self.spool = spool
                    return
            except OSError:
                if attempt==2:
                    raise
                time.sleep(attempt+1)

    def close(self):
        if self.spool is not None:
            self.spool.close()
        super().close()


def fetch_members(channel, pending, progress):
    if not pending:
        return
    with ArchiveReader(channel,progress) as reader, zipfile.ZipFile(reader) as archive:
        entries = sorted((archive.getinfo(key) for key in pending), key=lambda item:item.header_offset)
        for i, info in enumerate(entries,1):
            dest, rec = pending[info.filename]
            if info.file_size != rec['size']:
                raise SwitchError(f'发行包文件索引不匹配：{info.filename}')
            progress(f'准备渠道文件 {i}/{len(entries)}　{dest.name}')
            with archive.open(info) as source:
                write_verified(dest,source,rec)


def launcher_files_ok(full):
    return all(matches(LAUNCHER/name,rec,full) for name,rec in layout()['files'].items())


def extract_launcher(installer):
    """Decode only files in this release's pinned NSIS solid-stream index."""
    spec = layout()
    with installer.open('rb') as source:
        source.seek(spec['lzma_start'])
        dec = lzma.LZMADecompressor(lzma.FORMAT_RAW,filters=[
            dict(id=lzma.FILTER_LZMA1,dict_size=1<<23,lc=3,lp=0,pb=2)])
        raw = dec.decompress(source.read(spec['lzma_end']-spec['lzma_start']),
                             max_length=spec['uncompressed_size']+1)
    if len(raw)!=spec['uncompressed_size']:
        raise SwitchError('B 服启动器数据长度不符。')
    for name,rec in spec['files'].items():
        dest = LAUNCHER/name
        if matches(dest,rec):
            continue
        start = rec['offset']
        write_verified(dest,io.BytesIO(raw[start:start+rec['size']]),rec)


def setup_launcher(progress):
    if launcher_files_ok(True):
        return
    records = {'size':BOOTSTRAP_SIZE,'sha256':BOOTSTRAP_PIN}
    download = guarded(APP/'setup-cache'/'lumin-bilibili-1.0.2.3.exe')
    if not matches(download,records):
        progress('正在从哔哩哔哩下载正式版启动器…')
        headers = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) LuminChannelSwitch/1.0',
                   'Referer':'https://www.biligame.com/detail/?id=117116','Accept-Encoding':'identity'}
        endpoints = (BOOTSTRAP, BOOTSTRAP.replace('pkg.biligame.com','pkgdl.biligame.net'))
        for i,url in enumerate(endpoints):
            try:
                with urlopen(Request(url,headers=headers),timeout=90) as response:
                    if not response.geturl().lower().startswith('https://'):
                        raise SwitchError('B 站下载地址被重定向到非 HTTPS 地址。')
                    write_verified(download,response,records)
                break
            except (OSError, SwitchError):
                if i == len(endpoints)-1:
                    raise
                progress('正在使用 B 站提供的备用下载地址…')
    progress('正在校验并准备 B 服启动器…')
    extract_launcher(download)
    if not launcher_files_ok(True):
        raise SwitchError('启动器校验失败，请重新准备。')
    download.unlink(missing_ok=True)


def switches(inst):
    official,bili = inst.files['official'],inst.files['bilibili']
    return {name:rec for name,rec in bili.items() if rec != official.get(name)}


def ready_marker(inst):
    return guarded(inst.pkg/'prepared-v1')


def is_ready(game_root=None):
    try:
        inst = Installation(Path(game_root)) if game_root else Installation()
        if not ready_marker(inst).is_file():
            return False
        return (matches(inst.pkg/'official-config.json',inst.files['official'][CONFIG],False)
                and launcher_files_ok(False)
                and all(matches(inst.pkg/'bilibili'/n,rec,False) for n,rec in switches(inst).items()))
    except (OSError,ValueError,SwitchError):
        return False


def prepare_files(game_root, progress):
    inst = Installation(Path(game_root))
    with exclusive(APP/'setup-lock'):
        marker = ready_marker(inst)
        marker.parent.mkdir(parents=True,exist_ok=True)
        marker.unlink(missing_ok=True)
        data = guarded(inst.manifest_path).read_bytes()
        manifest_rec = {'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        write_verified(inst.pkg/'manifest.json',io.BytesIO(data),manifest_rec)
        progress('正在核对已缓存的发行文件…')
        config = inst.pkg/'official-config.json'
        base = inst.files['official'][CONFIG]
        if not matches(config,base):
            fetch_members(inst.profile,{CONFIG:(config,base)},progress)
        changed = switches(inst)
        missing = {name:(inst.pkg/'bilibili'/name,rec) for name,rec in changed.items()
                   if not matches(inst.pkg/'bilibili'/name,rec)}
        fetch_members('bilibili',missing,progress)
        setup_launcher(progress)
        marker.write_bytes(b'1\n')
    progress('渠道文件准备完成。')


def dotnet_executable():
    # The game launcher already uses the .NET 8 x64 desktop runtime.
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\dotnet\Setup\InstalledVersions\x64',
                            0,winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as key:
            base = Path(winreg.QueryValueEx(key,'InstallLocation')[0])
    except OSError:
        base = Path(os.environ['ProgramFiles'])/'dotnet'
    exe = guarded(base/'dotnet.exe')
    if not exe.is_file():
        raise SwitchError('请选择微软的 .NET 8 桌面运行时（Windows x64）安装后重试。')
    return exe


def low_profile():
    import ctypes
    from ctypes import wintypes
    guid = (ctypes.c_ubyte*16).from_buffer_copy(uuid.UUID('A520A1A4-1780-4FF6-BD18-167343C5AF16').bytes_le)
    value = wintypes.LPWSTR()
    result = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid),0,None,ctypes.byref(value))
    if result!=0 or not value.value:
        raise SwitchError('无法获取 Windows 用户的 LocalLow 目录。')
    try:
        return guarded(Path(value.value)/'zhuziplay/LuminLauncher/bilibili/Prod')
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(value,ctypes.c_void_p))


def write_blob(path,data):
    write_verified(path,io.BytesIO(data),{'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})


def bind_launcher(game_root):
    profile = low_profile()
    profile.mkdir(parents=True,exist_ok=True)
    conf,marker = (guarded(profile/n) for n in ('config.lmc','install_path'))
    guarded(profile/'cache.lmc')
    if any(p.suffix.lower()=='.lck' for p in profile.iterdir()):
        raise SwitchError('请退出 B 服启动器再绑定游戏目录。')
    root = guarded(Path(game_root)).parent
    helper = DATA/'prefs'/'LauncherPreferences.dll'
    if not helper.is_file():
        raise SwitchError('配置助手缺失，请重新解压发行包。')
    # The helper uses the publisher's methods to edit its settings format. It
    # receives no login or account data and only returns the proposed encrypted bytes.
    proc = subprocess.run([str(dotnet_executable()),str(helper),str(LAUNCHER/'Lumin.Launcher.dll'),
                           str(profile),str(root)],capture_output=True,timeout=20,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode==3:
        raise SwitchError('B 服启动器有未完成的任务，请先完成或取消。')
    if proc.returncode:
        raise SwitchError('无法读取 B 服启动器配置，请确认已安装 .NET 8 桌面运行时并关闭启动器。')
    prior = {p:(p.read_bytes() if p.is_file() else None) for p in (conf,marker)}
    updates = {}
    if proc.stdout.strip():
        updates[conf] = base64.b64decode(proc.stdout.strip(),validate=True)
    if (prior[marker] or b'').decode('utf-8-sig') != str(root):
        updates[marker] = str(root).encode('utf-8')
    if not updates:
        return
    backup = guarded(APP/'backups'/('bilibili-prefs-'+uuid.uuid4().hex))
    backup.mkdir(parents=True)
    for p,raw in prior.items():
        if raw is not None:
            write_blob(backup/p.name,raw)
    committed = []
    try:
        for p,raw in updates.items():
            write_blob(p,raw)
            committed.append(p)
    except BaseException:
        for p in reversed(committed):
            if prior[p] is not None:
                write_blob(p,prior[p])
            else:
                p.unlink(missing_ok=True)
        raise


def prepare_bilibili_launch(game_root):
    with exclusive(APP/'setup-lock'):
        if not launcher_files_ok(True):
            raise SwitchError('B 服启动器未通过校验，请先重新准备渠道文件。')
        bind_launcher(game_root)
    return guarded(LAUNCHER/'Lumin.Launcher.exe')
