"""Switch authenticated PC release files while preserving the original installation."""
import ast
import base64
import contextlib
import csv
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time


APP = Path(os.environ['LOCALAPPDATA']) / 'LuminChannelSwitch'
BUILD = 12450
DATA = Path(__file__).resolve().parent / 'data'
PROFILES = {
    'zhuzi': {
        'manifest': 'manifest.json',
        'pin': '80464c33b04ccf603fe0cc371a2eb8bad05e67d6a17f6af4f4bcf2a4616b9888',
        'payload': '12450',
        'launcher_exe': '26f0e4a83f8407ce60741d90f3c7474c65af31176ca2f1b8aa5b7eda588befb2',
        'launcher_dll': '670e070d6ff9a5d081aa4dce69571b46886db56db223dabd1faf5e3bf92ca439',
    },
    'zhuzi_gf': {
        'manifest': 'manifest-zhuzi_gf.json',
        'pin': '9e80e0544a8e9cf98821dfbd8e7c9c2ea50193a250ba6bbbfd981958b815b3c7',
        'payload': '12450-zhuzi_gf',
        'launcher_exe': 'cc8fb492b52a9b559732708dc2af33ea02f4569e24567f377d3a48133c391cc8',
        'launcher_dll': 'e911ec943ea1905092fded2e545dde1830856049a320be6696fea2e1e1f25d5d',
    },
}
CONFIG = 'Lumin_Data/StreamingAssets/app_config.json'
INFO = 'Lumin_Data/StreamingAssets/app_info'
SDK = 'BLPlatform64'
CHANNELS = ('official', 'bilibili')


class SwitchError(RuntimeError):
    pass


def guarded(path):
    # A junction at any ancestor could redirect an otherwise valid relative path.
    for item in (path, *path.parents):
        try:
            flags = item.lstat().st_file_attributes
        except FileNotFoundError:
            continue
        if flags & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise SwitchError(f'路径含有目录联接或链接，请检查位置：{item}')
    return path


def digest(path):
    guarded(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise SwitchError(f'此位置应当是普通文件：{path}')
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normalized(path):
    return os.path.normcase(os.path.abspath(path))


def local_path(value):
    path = Path(value)
    if not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ':':
        raise SwitchError('请选择本机磁盘上的完整绿梦安装路径。')
    return guarded(Path(os.path.abspath(path)))


def game_folder(value):
    selected = local_path(value)
    if selected.is_file():
        if selected.name.lower() not in {'lumin.exe','lumin.launcher.exe'}:
            raise SwitchError('请选择 Lumin.exe 或 Lumin.Launcher.exe。')
        selected = selected.parent
    for candidate in (selected, selected/'Bin', selected/'Lumin'/'Bin', selected/'app'/'Lumin'/'Bin'):
        if guarded(candidate/'Lumin_Data').is_dir():
            return candidate
    raise SwitchError('找不到 Lumin_Data。请选择绿梦安装目录或 Lumin.exe 所在目录。')


def selected_game():
    settings = guarded(APP/'user-settings.json')
    if settings.is_file():
        data = json.loads(settings.read_bytes())
        return local_path(data['game_root'])
    # Both official publishers' launchers record the user's selected game location.
    low = Path(os.environ['LOCALAPPDATA']).parent/'LocalLow'/'zhuziplay'/'LuminLauncher'
    candidates = set()
    for profile in PROFILES:
        pointer = low/profile/'Prod'/'install_path'
        try:
            raw = guarded(pointer).read_bytes()
            if len(raw) <= 4096:
                candidates.add(game_folder(raw.decode('utf-8-sig').strip()))
        except (OSError, ValueError, SwitchError):
            continue
    if len(candidates)==1:
        return candidates.pop()
    raise SwitchError('请先选择绿梦官服的安装目录。')


def load_manifest(profile):
    meta = PROFILES[profile]
    path = guarded(DATA/meta['manifest'])
    raw = path.read_bytes()
    if sha(raw) != meta['pin']:
        raise SwitchError('内置发行清单未通过校验，请重新解压完整的切服工具。')
    return json.loads(raw)


def legacy_state_for(game):
    """Reuse an earlier local edition only when its pinned installation path matches."""
    folder = guarded(APP/'state'/str(BUILD))
    script = guarded(APP/'lumin_switch.py')
    if not script.is_file() or not folder.is_dir() or script.stat().st_size > 128*1024:
        return None
    try:
        values = {node.targets[0].id:node.value for node in ast.parse(script.read_bytes()).body
                  if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name)}
        path_call = values.get('GAME')
        if (not isinstance(path_call,ast.Call) or not isinstance(path_call.func,ast.Name)
                or path_call.func.id!='Path' or len(path_call.args)!=1
                or not isinstance(path_call.args[0],ast.Constant) or not isinstance(path_call.args[0].value,str)
                or not isinstance(values.get('PIN'),ast.Constant) or values['PIN'].value!=PROFILES['zhuzi']['pin']):
            return None
        if normalized(path_call.args[0].value)==normalized(game):
            return folder
    except (OSError, SyntaxError, UnicodeError, ValueError):
        pass
    return None


def retry(action):
    for attempt in range(16):
        try:
            return action()
        except PermissionError:
            if attempt == 15:
                raise
            time.sleep(.4)


def atomic_bytes(path, data):
    guarded(path).parent.mkdir(parents=True, exist_ok=True)
    temp = guarded(path.with_suffix(path.suffix + '.pending'))
    with temp.open('wb') as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    retry(lambda: os.replace(temp, path))


@contextlib.contextmanager
def exclusive(state):
    guarded(state).mkdir(parents=True, exist_ok=True)
    with guarded(state/'switch.lock').open('a+b') as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise SwitchError('另一个窗口正在操作此安装。') from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def require_closed():
    exe = Path(os.environ['SystemRoot'])/'System32'/'tasklist.exe'
    result = subprocess.run([str(exe), '/fo', 'csv', '/nh'], capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=15)
    running = {row[0].lower() for row in csv.reader(result.stdout.decode('mbcs','replace').splitlines()) if row}
    if running & {'lumin.exe','lumin.launcher.exe','lumin-launcher-upd.exe','lumin-installsvc.exe'}:
        raise SwitchError('请先退出绿梦和绿梦启动器（包括托盘里的启动器），再切换。')


class Installation:
    def __init__(self, game=None, state=None, packages=None):
        self.game = local_path(game) if game is not None else selected_game()
        self.install_id = sha(normalized(self.game).encode('utf-8'))[:24]
        self.state = guarded(Path(state) if state is not None else APP/'installations'/self.install_id/str(BUILD))
        self.cache = guarded(self.game.parent/('.lumin-channel-switch-12450-' + self.install_id))
        records = {profile:load_manifest(profile) for profile in PROFILES}
        info = digest(self.game/INFO)
        legacy = legacy_state_for(self.game) if state is None else None
        self.legacy = legacy is not None and (guarded(legacy/'transaction.json').exists() or
            info in {records['zhuzi'][ch][INFO]['sha256'] for ch in CHANNELS})
        if self.legacy:
            self.state = legacy
            self.cache = guarded(self.game.parent/'.lumin-channel-switch-12450')
        self.journal = self.state/'transaction.json'
        self.origin = self.state/'origin.json'
        if guarded(self.journal).is_file():
            # app_info may already have been changed when a switch was interrupted.
            self.profile = self.read_origin()
        else:
            self.profile = next((p for p,r in records.items() if r['official'][INFO]['sha256']==info),None)
            if self.profile is None:
                if info != records['zhuzi']['bilibili'][INFO]['sha256']:
                    raise SwitchError('无法识别此安装。当前支持 PC 构建 12450 的 zhuzi 和 zhuzi_gf 官服。')
                self.profile = self.read_origin()
        self.files = records[self.profile]
        meta = PROFILES[self.profile]
        self.pkg = guarded(Path(packages) if packages is not None else APP/'payloads'/meta['payload'])
        self.backups = self.state if self.legacy else self.state/'backups'/self.profile
        self.manifest_path = DATA/meta['manifest']
        official, bili = (self.files[ch] for ch in CHANNELS)
        self.diff = sorted(name for name in official.keys() | bili.keys()
                           if official.get(name) != bili.get(name))
        self.tree = [n for n in self.diff if n.startswith(SDK+'/')]
        self.single = [n for n in self.diff if n not in self.tree and n != INFO] + [INFO]

    def read_origin(self):
        if not guarded(self.origin).is_file():
            if self.legacy and guarded(self.state/'official-profile.json').is_file():
                return 'zhuzi'
            raise SwitchError('此安装缺少官服原件记录。首次使用请先恢复到原本的官服，再选择目录。')
        data = json.loads(self.origin.read_bytes())
        if normalized(data.get('game_root','')) != normalized(self.game) or data.get('profile') not in PROFILES:
            raise SwitchError('官服备份与所选游戏位置不一致。')
        return data['profile']

    def packaged(self, channel, name):
        if channel == 'bilibili':
            return self.pkg/'bilibili'/name
        if name == CONFIG:
            return self.backups/'official-profile.json'
        return self.backups/'original'/name

    def reference_hash(self, channel, name):
        return self.files[channel].get(name, {}).get('sha256')

    def valid_config(self, channel, data):
        file = self.pkg/'official-config.json' if channel=='official' else self.pkg/'bilibili'/CONFIG
        standard = guarded(file).read_bytes()
        if sha(standard) != self.reference_hash(channel,CONFIG):
            raise SwitchError('缓存中的渠道配置校验失败。')
        def fields(raw):
            if len(raw)>65536:
                raise SwitchError('渠道配置过大。')
            obj = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(obj,dict):
                raise SwitchError('无法识别游戏的渠道配置。')
            obj.pop('cps',None)
            return obj
        if fields(data) != fields(standard):
            raise SwitchError('本机渠道配置与当前版本不符，请先核对版本。')
        return data

    def status(self):
        pending = guarded(self.journal).exists()
        info = digest(self.game/INFO)
        ch = next((c for c in CHANNELS if self.reference_hash(c,INFO)==info),'unknown')
        if ch != 'unknown' and digest(self.game/'Lumin.exe') != self.reference_hash(ch,'Lumin.exe'):
            ch = 'unknown'
        return {'channel':'unknown' if pending else ch,'build_id':BUILD if ch!='unknown' else None,
                'game_root':str(self.game),'recovery':pending,'profile':self.profile}

    def save_originals(self):
        # Copy only the channel-dependent original files. Preserve this installation's CPS bytes.
        for name in self.diff:
            expected = self.reference_hash('official',name)
            if expected is None:
                continue
            src, backup = guarded(self.game/name), self.packaged('official',name)
            if name == CONFIG:
                data = self.valid_config('official',src.read_bytes())
                atomic_bytes(backup,data)
                continue
            if digest(src) != expected:
                raise SwitchError(f'官服文件已变化，无法建立本版本备份：{name}')
            if digest(backup) == expected:
                continue
            guarded(backup).parent.mkdir(parents=True,exist_ok=True)
            tmp = guarded(backup.with_name(backup.name+'.pending'))
            shutil.copyfile(src,tmp)
            if digest(tmp)!=expected:
                raise SwitchError(f'备份校验失败：{name}')
            retry(lambda: os.replace(tmp,backup))

    def verified_cfg(self, ch):
        return self.valid_config(ch, guarded(self.packaged(ch,CONFIG)).read_bytes())

    def hashes(self, ch, config):
        result = {n:self.reference_hash(ch,n) for n in self.diff}
        result[CONFIG] = sha(self.valid_config(ch,config))
        return result

    def validate_cache(self, ch):
        for n in self.diff:
            h = self.reference_hash(ch,n)
            if h and n!=CONFIG and digest(self.packaged(ch,n)) != h:
                raise SwitchError(f'渠道备份或安装缓存不完整：{n}')
        self.verified_cfg(ch)

    def check_live(self, hashes):
        for n in self.diff:
            if digest(self.game/n) != hashes[n]:
                raise SwitchError(f'现场文件与当前版本不一致：{n}。已保留现场。')

    def sdk_paths(self):
        live, parked = guarded(self.game/SDK), guarded(self.cache/SDK)
        if live.exists() and parked.exists():
            raise SwitchError('两处同时存在 B 站组件目录，请保留现场检查。')
        return live,parked

    def move_sdk(self, ch):
        live, parked = self.sdk_paths()
        if ch=='official' and not live.exists():
            return
        self.cache.mkdir(parents=True,exist_ok=True)
        if not live.exists() and not parked.exists():
            # An interrupted first cache build can be continued before any directory is activated.
            building = guarded(self.cache/'sdk-building')
            for n in self.tree:
                dest = guarded(building/Path(n).relative_to(SDK))
                dest.parent.mkdir(parents=True,exist_ok=True)
                if digest(dest)!=self.reference_hash('bilibili',n):
                    shutil.copyfile(guarded(self.packaged('bilibili',n)),dest)
            self.verify_sdk(building)
            retry(lambda: os.rename(building,parked))
        source = live if live.exists() else parked
        self.verify_sdk(source)
        target = live if ch=='bilibili' else parked
        if source!=target:
            retry(lambda: os.rename(source,target))

    def verify_sdk(self, root):
        # Preserve any additional runtime files when parking the whole Bilibili directory.
        for n in self.tree:
            if digest(root/Path(n).relative_to(SDK)) != self.reference_hash('bilibili',n):
                raise SwitchError(f'B 站组件发生变化或已损坏：{n}')
        for parent,dirs,files in os.walk(root,followlinks=False):
            for child in dirs+files:
                guarded(Path(parent)/child)

    def apply(self, target, target_cfg, previous, progress):
        wanted = self.hashes(target,target_cfg)
        self.move_sdk(target)
        self.cache.mkdir(parents=True,exist_ok=True)
        temp = guarded(self.cache/'file.pending')
        for i,name in enumerate(self.single,1):
            dest = guarded(self.game/name)
            found, desired = digest(dest), wanted[name]
            if found == desired:
                continue
            if found != previous[name]:
                raise SwitchError(f'切换期间文件发生变化，已保留：{name}')
            progress(f'正在切换 {i}/{len(self.single)}　{name.rsplit("/",1)[-1]}')
            if desired is None:
                retry(dest.unlink)
                continue
            dest.parent.mkdir(exist_ok=True,parents=True)
            if name == CONFIG:
                with temp.open('wb') as file:
                    file.write(target_cfg)
                    file.flush()
                    os.fsync(file.fileno())
            else:
                with guarded(self.packaged(target,name)).open('rb') as src, temp.open('wb') as out:
                    shutil.copyfileobj(src,out,4*1024*1024)
                    out.flush()
                    os.fsync(out.fileno())
            if digest(temp)!=desired:
                raise SwitchError(f'文件写入校验失败：{name}')
            retry(lambda: os.replace(temp,dest))
        self.check_live(wanted)

    def rollback(self,progress):
        tx = json.loads(guarded(self.journal).read_bytes())
        src,dst = tx['source'],tx['target']
        if ({src,dst} != set(CHANNELS) or normalized(tx['root'])!=normalized(self.game)
                or tx['build']!=BUILD or tx.get('profile','zhuzi' if self.legacy else '') != self.profile):
            raise SwitchError('恢复记录与安装位置不匹配。')
        source_cfg = base64.b64decode(tx['source_config'],validate=True)
        dest_cfg = base64.b64decode(tx['target_config'],validate=True)
        self.validate_cache(src)
        self.apply(src,source_cfg,self.hashes(dst,dest_cfg),progress)
        self.journal.unlink()

    def switch(self,target,progress):
        if target not in CHANNELS:
            raise SwitchError('未知的渠道。')
        with exclusive(self.cache), exclusive(self.state):
            require_closed()
            status = self.status()
            if status['recovery']:
                raise SwitchError('请先恢复上一次操作。')
            source = status['channel']
            if source=='unknown':
                raise SwitchError('当前游戏的版本或渠道无法识别，仅支持构建 12450。')
            if source==target:
                return
            source_cfg = self.valid_config(source,guarded(self.game/CONFIG).read_bytes())
            expected = self.hashes(source,source_cfg)
            progress('正在核对当前游戏文件与还原备份…')
            self.check_live(expected)
            live,parked = self.sdk_paths()
            if (source=='official') == live.exists():
                raise SwitchError('游戏渠道与 B 站组件目录不一致。')
            if source == 'official':
                self.save_originals()
            for ch in CHANNELS:
                self.validate_cache(ch)
            target_cfg = self.verified_cfg(target)
            tx = dict(source=source,target=target,root=str(self.game),build=BUILD,profile=self.profile,
                      source_config=base64.b64encode(source_cfg).decode(),
                      target_config=base64.b64encode(target_cfg).decode())
            require_closed()
            if source == 'official':
                atomic_bytes(self.origin, json.dumps({'game_root':str(self.game),'profile':self.profile}).encode())
            atomic_bytes(self.journal,json.dumps(tx).encode())
            try:
                self.apply(target,target_cfg,expected,progress)
            except Exception as exc:
                try:
                    progress('正在还原切换前的文件…')
                    require_closed()
                    self.rollback(progress)
                except Exception as rollback_exc:
                    raise SwitchError(f'{exc}\n请关闭游戏后点击“恢复上次操作”。\n{rollback_exc}') from exc
                raise SwitchError(f'切换未完成，已还原原渠道。\n{exc}') from exc
            self.journal.unlink()


def profile_for(game_root):
    return Installation(game_root).profile


def inspect_status():
    import channel_setup
    root = None
    try:
        root = selected_game()
        install = Installation(root)
        status = install.status()
        status['prepared'] = channel_setup.is_ready(root)
        return status
    except (KeyError, OSError, ValueError, SwitchError) as exc:
        return {'channel':'unknown','build_id':None,'game_root':str(root or ''),
                'recovery':False,'prepared':False,'reason':str(exc)}


def configure_game(path):
    game = game_folder(path)
    instance = Installation(game)
    status = instance.status()
    if not status['recovery'] and status['channel']=='unknown':
        raise SwitchError('游戏主程序与发行清单不一致，请先核对所选目录。')
    with exclusive(APP/'preferences'):
        atomic_bytes(APP/'user-settings.json',json.dumps({'game_root':str(game)}).encode('utf-8'))
    return inspect_status()


def switch_channel(target, progress_callback):
    Installation().switch(target, progress_callback)


def recover(progress_callback):
    install = Installation()
    with exclusive(install.cache), exclusive(install.state):
        require_closed()
        if guarded(install.journal).exists():
            install.rollback(progress_callback)


def checked_official_launcher(install, path):
    path = local_path(path)
    profile = PROFILES[install.profile]
    if (path.name.lower() != 'lumin.launcher.exe' or digest(path)!=profile['launcher_exe']
            or digest(path.with_suffix('.dll')) != profile['launcher_dll']):
        raise SwitchError(f'所选启动器与已验证的 {install.profile} 官服启动器版本不符。请从官服启动器自行启动，或选择匹配的 Lumin.Launcher.exe。')
    return path


def configure_official_launcher(path):
    install = Installation()
    exe = checked_official_launcher(install, path)
    with exclusive(install.state):
        atomic_bytes(install.backups/'launcher.json',json.dumps({'path':str(exe)}).encode('utf-8'))


def launch(channel):
    install = Installation()
    # Launcher preferences are per channel, independent of the active game directory.
    with exclusive(APP/'launcher-operation'), exclusive(install.cache), exclusive(install.state):
        if channel not in CHANNELS or install.status()['channel'] != channel:
            raise SwitchError('请刷新并确认当前渠道。')
        if channel=='bilibili':
            import channel_setup
            require_closed()
            exe = guarded(Path(channel_setup.prepare_bilibili_launch(install.game)))
        else:
            binding = guarded(install.backups/'launcher.json')
            if binding.is_file():
                selected = json.loads(binding.read_bytes())['path']
            else:
                selected = install.game.parents[1]/'Lumin.Launcher.exe'
            exe = checked_official_launcher(install,selected)
            pointer = (Path(os.environ['LOCALAPPDATA']).parent/'LocalLow'/'zhuziplay'/'LuminLauncher'
                       /install.profile/'Prod'/'install_path')
            if guarded(pointer).is_file():
                actual = pointer.read_text(encoding='utf-8-sig').strip()
                if actual and normalized(Path(actual)/'Bin')!=normalized(install.game):
                    raise SwitchError('该官服启动器指向另一份游戏。请先在官服启动器中关联所选目录。')
        os.startfile(str(exe), cwd=str(exe.parent))
