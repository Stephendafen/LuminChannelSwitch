"""Windows desktop entry point for Lumin Channel Switch."""
import ctypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

import channel_setup
import lumin_switch as core
import updater
from version import VERSION


CHANNELS = {"official": "官服", "bilibili": "B 服"}
TASKS = {
    "inspect": "读取游戏状态", "configure": "识别安装目录", "launcher": "选择官服启动器",
    "prepare": "准备 B 服组件",
    "switch": "切换渠道", "recover": "恢复上次操作", "launch": "启动当前渠道",
    "check": "检查工具更新", "download": "下载更新包",
}


class ChannelWindow:
    def __init__(self, root, *, start_inspection=True):
        self.root = root
        self.state = {}
        self.busy = None
        self.events = queue.SimpleQueue()
        root.title(f"绿梦切服　v{VERSION}")
        icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))/"lumin-switch.ico"
        if icon.is_file():
            root.iconbitmap(str(icon))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(9, 5))
        style.configure("Channel.TLabel", font=("Microsoft YaHei UI", 20, "bold"))

        menu = tk.Menu(root)
        self.settings = tk.Menu(menu, tearoff=False)
        self.settings.add_command(label="选择官服启动器…", command=self.choose_launcher)
        menu.add_cascade(label="设置", menu=self.settings)
        tools = tk.Menu(menu, tearoff=False)
        tools.add_command(label="检查工具更新…", command=self.check_update)
        tools.add_command(label="打开 GitHub 发布页", command=lambda: webbrowser.open(updater.RELEASES_PAGE))
        menu.add_cascade(label="更新", menu=tools)
        root.configure(menu=menu)

        panel = ttk.Frame(root, padding=(22, 18))
        panel.grid(row=0, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        self.header = tk.StringVar(value="正在识别…")
        self.build = tk.StringVar()
        self.location = tk.StringVar()
        self.note = tk.StringVar()
        head = ttk.Frame(panel)
        head.grid(row=0,column=0, sticky="ew", pady=(0, 12))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="当前渠道").grid(row=0, column=0, sticky="w")
        ttk.Label(head, textvariable=self.header, style="Channel.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Label(head, textvariable=self.build).grid(row=1, column=1, sticky="e")

        ttk.Label(panel, text="绿梦 PC 安装目录").grid(row=1, column=0, sticky="w")
        picker = ttk.Frame(panel)
        picker.grid(row=2, column=0, sticky="ew", pady=(4, 10))
        picker.columnconfigure(0, weight=1)
        ttk.Entry(picker, textvariable=self.location, state="readonly", width=44).grid(row=0, column=0, sticky="ew")
        self.choose = ttk.Button(picker, text="选择目录…", command=self.choose_game)
        self.choose.grid(row=0,column=1,padx=(7, 0))
        self.prepare = ttk.Button(panel, text="准备 B 服组件", command=self.prepare_files)
        self.prepare.grid(row=3,column=0,sticky="ew",pady=(0, 10))

        buttons = ttk.Frame(panel)
        buttons.grid(row=4,column=0,sticky="ew")
        for col in (0, 1):
            buttons.columnconfigure(col, weight=1, uniform="channels")
        self.official = ttk.Button(buttons, text="切换到官服", command=lambda: self.switch("official"))
        self.bili = ttk.Button(buttons, text="切换到 B 服", command=lambda: self.switch("bilibili"))
        self.official.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.bili.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.launch = ttk.Button(panel, text="启动当前渠道", command=self.launch_game)
        self.launch.grid(row=5,column=0,sticky="ew",pady=(8, 14))

        self.activity = ttk.Progressbar(panel,mode="indeterminate")
        self.activity.grid(row=6,column=0,sticky="ew")
        label = ttk.Label(panel,textvariable=self.note,wraplength=460,justify="left")
        label.grid(row=7,column=0,sticky="w",pady=(8,6))
        panel.bind("<Configure>",lambda e: label.configure(wraplength=max(260,e.width-46)))

        bottom = ttk.Frame(panel)
        bottom.grid(row=8,column=0,sticky="ew")
        bottom.columnconfigure(1,weight=1)
        self.recovery = ttk.Button(bottom, text="恢复上次操作", command=self.recover)
        self.recovery.grid(row=0, column=0, sticky="w")
        self.update = ttk.Button(bottom, text="检查更新", command=self.check_update)
        self.update.grid(row=0,column=2,padx=(0,5))
        self.refresh = ttk.Button(bottom, text="刷新", command=self.refresh_status)
        self.refresh.grid(row=0,column=3)
        self.draw()
        root.update_idletasks()
        root.minsize(root.winfo_reqwidth(),root.winfo_reqheight())
        if start_inspection:
            root.after(0,self.refresh_status)
            root.after(60,self.drain)

    def draw(self):
        idle = self.busy is None
        channel = self.state.get("channel")
        pending = bool(self.state.get("recovery"))
        prepared = bool(self.state.get("prepared"))
        folder = self.state.get("game_root") or ""
        known = channel in CHANNELS
        self.location.set(folder)
        self.header.set("等待恢复" if pending else CHANNELS.get(channel, "待识别" if folder else "选择游戏目录"))
        build = self.state.get("build_id")
        self.build.set(f"构建 {build}" if build else "")
        self.prepare.configure(text="校验 B 服组件" if prepared else "准备 B 服组件")
        for widget, enabled in (
            (self.choose, not pending), (self.prepare, bool(folder) and (known or pending)),
            (self.official, prepared and not pending and channel == "bilibili"),
            (self.bili, prepared and not pending and channel == "official"),
            (self.launch,known and not pending and (channel=="official" or prepared)),
            (self.recovery,pending), (self.refresh,True), (self.update,True),
        ):
            widget.state(["!disabled"] if enabled and idle else ["disabled"])
        if pending:
            self.recovery.grid()
        else:
            self.recovery.grid_remove()
        self.settings.entryconfigure(0,state="normal" if idle and folder and known and not pending else "disabled")

    def start(self, action, work, refresh=False):
        if self.busy:
            return
        self.busy = action
        self.note.set(f"正在{TASKS[action]}…")
        self.activity.start(15)
        self.draw()

        def worker():
            def progress(text):
                self.events.put(("progress",str(text)))
            result, updated, error = None, None, ""
            try:
                result = work(progress)
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            if refresh:
                try:
                    updated = core.inspect_status()
                except Exception as exc:
                    updated = {}
                    error += ("\n" if error else "") + f"无法读取游戏状态：{exc}"
            self.events.put(("done",(action,result,updated,error)))
        threading.Thread(target=worker,daemon=True).start()

    def drain(self):
        latest = None
        for _ in range(512):
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "progress":
                latest = payload
            else:
                latest = None
                self.finish(*payload)
        if latest is not None:
            self.note.set(latest)
        self.root.after(60,self.drain)

    def finish(self, action, result, status, error):
        self.busy = None
        self.activity.stop()
        if status is not None:
            self.state = status
        self.draw()
        if error:
            self.note.set(error)
            if action in ("download","check"):
                if messagebox.askyesno(TASKS[action],error+"\n\n打开 GitHub 发布页？",parent=self.root):
                    webbrowser.open(updater.RELEASES_PAGE)
            else:
                messagebox.showerror(TASKS[action],error,parent=self.root)
            return
        if action == "check":
            if result is None:
                self.note.set(f"当前已是最新正式版 v{VERSION}。")
            else:
                self.offer_update(result)
        elif action == "download":
            self.note.set(f"更新包已通过 SHA-256 校验，保存至 {result}")
            if messagebox.askyesno("更新已下载", "完整解压到新文件夹，运行其中的 LuminChannelSwitch.exe 即可使用新版。\n\n打开压缩包所在文件夹？",parent=self.root):
                os.startfile(str(result.parent))
        elif self.state.get("recovery"):
            self.note.set("上次切换未完成。请退出游戏和启动器，点击“恢复上次操作”。")
        elif not self.state.get("game_root"):
            self.note.set("先选择绿梦的安装目录，或包含 Lumin.exe 的 Bin 目录。")
        elif self.state.get("channel") not in CHANNELS:
            self.note.set(self.state.get("reason") or "此目录尚未识别出受支持的游戏版本。")
        elif action == "switch":
            self.note.set(f"已切换到{CHANNELS[self.state['channel']]}。点击“启动当前渠道”进入游戏。")
        elif action == "launch":
            self.note.set("已请求打开当前渠道的启动器。")
        elif action == "recover":
            self.note.set("原渠道已恢复。")
        elif action == "launcher":
            self.note.set("官服启动器位置已保存。")
        elif self.state.get("prepared"):
            self.note.set("切换前请退出游戏和托盘里的启动器。切换后请通过此工具启动。")
        else:
            self.note.set("点击“准备 B 服组件”完成首次下载。")

    def refresh_status(self):
        self.start("inspect",lambda progress: None,refresh=True)

    def choose_game(self):
        folder = filedialog.askdirectory(title="选择绿梦的安装目录或 Bin 目录",parent=self.root,mustexist=True)
        if folder:
            self.start("configure",lambda progress:core.configure_game(Path(folder)),refresh=True)

    def choose_launcher(self):
        filename = filedialog.askopenfilename(
            title="选择此游戏的官服启动器", parent=self.root,
            filetypes=[("绿梦启动器","Lumin.Launcher.exe"),("Windows 程序","*.exe")])
        if filename:
            self.start("launcher",lambda progress:core.configure_official_launcher(Path(filename)),refresh=True)

    def prepare_files(self):
        if not self.state.get("prepared"):
            confirmed = messagebox.askokcancel(
                "准备 B 服组件", "将从发行方下载适用于当前版本的渠道文件和 B 服启动器。首次准备需要数百 MB 流量。",parent=self.root)
            if not confirmed:
                return
        folder = Path(self.state["game_root"])
        self.start("prepare",lambda progress:channel_setup.prepare_files(folder,progress),refresh=True)

    def switch(self,channel):
        self.start("switch",lambda progress:core.switch_channel(channel,progress),refresh=True)

    def launch_game(self):
        channel = self.state["channel"]
        self.start("launch",lambda progress:core.launch(channel),refresh=True)

    def recover(self):
        self.start("recover",lambda progress:core.recover(progress),refresh=True)

    def check_update(self):
        self.start("check",lambda progress:updater.latest_update())

    def offer_update(self,release):
        self.note.set(f"GitHub 已发布 {release.tag}。")
        size = release.size/(1024*1024)
        if not messagebox.askyesno("发现新版",f"下载 {release.tag} 的 Windows 压缩包（{size:.1f} MiB）？",parent=self.root):
            return
        path = filedialog.asksaveasfilename(title="保存更新包",parent=self.root,
                                           initialfile=f"LuminChannelSwitch-{release.tag}-Windows-x64.zip",
                                           filetypes=[("ZIP 压缩包","*.zip")],defaultextension=".zip")
        if path:
            self.start("download",lambda progress:updater.download(release,path,progress))

    def close(self):
        if self.busy in ("configure","launcher","prepare","switch","recover","download","launch"):
            messagebox.showinfo("绿梦切服","正在处理文件，请等操作完成后再关闭窗口。",parent=self.root)
            return
        self.root.destroy()


def main():
    args = sys.argv[1:]
    if args not in ([], ["--self-check"]):
        return 2
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError,OSError):
        pass
    root = tk.Tk()
    if args:
        root.withdraw()
        try:
            data_dir = Path(__file__).resolve().parent/"data"
            for name in ("manifest.json", "manifest-zhuzi_gf.json"):
                manifest = json.loads((data_dir/name).read_bytes())
                if not all("Lumin.exe" in manifest[c] for c in ("official","bilibili")):
                    raise RuntimeError("安装包渠道清单不完整。")
            layout = json.loads((data_dir/"launcher-layout.json").read_bytes())
            if not layout.get("files"):
                raise RuntimeError("安装包启动器清单不完整。")
            methods = (core.inspect_status, core.configure_game, core.switch_channel, core.recover,
                       core.configure_official_launcher,core.launch,channel_setup.prepare_files,channel_setup.is_ready)
            if not all(callable(method) for method in methods):
                raise RuntimeError("安装包缺少切换或准备功能。")
            ChannelWindow(root,start_inspection=False)
        finally:
            root.destroy()
        return 0
    ChannelWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
