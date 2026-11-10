"""Look up stable project releases and verify explicitly requested downloads."""

import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from version import REPOSITORY, VERSION


ZIP_NAME = "LuminChannelSwitch-Windows-x64.zip"
CHECKSUM_NAME = ZIP_NAME + ".sha256"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"
_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
_HOSTS = {"api.github.com", "github.com", "release-assets.githubusercontent.com",
          "objects.githubusercontent.com", "github-releases.githubusercontent.com"}


class ReleaseError(RuntimeError):
    pass


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if parts.scheme != "https" or parts.netloc.lower() not in _HOSTS:
            raise ReleaseError("更新服务返回了无法验证的下载地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(GitHubRedirect())


def _request(url):
    request = urllib.request.Request(url, headers={"User-Agent": f"LuminChannelSwitch/{VERSION}",
                                                "Accept": "application/vnd.github+json" if url == _API else "*/*"})
    try:
        return _opener.open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise ReleaseError("GitHub 暂时限制了更新请求，可从发布页下载。") from exc
        if exc.code == 404:
            raise ReleaseError("GitHub 上暂未找到正式版更新包。") from exc
        raise ReleaseError(f"GitHub 更新服务返回 HTTP {exc.code}。") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ReleaseError("连接 GitHub 失败，请稍后重试或打开发布页。") from exc


def _read(url, limit):
    with _request(url) as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ReleaseError("更新服务返回的数据超出预期大小。")
    return body


def _version_tuple(value):
    match = re.fullmatch(r"v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})", str(value))
    if not match:
        raise ReleaseError("无法识别此正式版使用的版本号。")
    return tuple(int(part) for part in match.groups())


@dataclass(frozen=True)
class Release:
    tag: str
    size: int

    def url(self, name):
        return f"{RELEASES_PAGE}/download/{self.tag}/{name}"


def latest_update():
    """Return a newer release, or None when this version is up to date."""
    meta = json.loads(_read(_API, 2*1024*1024))
    tag = meta.get("tag_name")
    if meta.get("draft") or meta.get("prerelease"):
        raise ReleaseError("未找到可更新的正式版。")
    if _version_tuple(tag) <= _version_tuple(VERSION):
        return None
    assets = {item.get("name"): item for item in meta.get("assets", [])}
    if not all(n in assets for n in (ZIP_NAME, CHECKSUM_NAME)):
        raise ReleaseError("新版的 Windows 压缩包和校验文件尚未全部发布。")
    size = assets[ZIP_NAME].get("size")
    if not isinstance(size, int) or size <= 0:
        raise ReleaseError("新版下载包大小无效。")
    return Release(tag, size)


def download(release, destination, progress):
    lines = _read(release.url(CHECKSUM_NAME), 4096).decode("utf-8-sig").splitlines()
    matches = [m.group(1).lower() for line in lines if
               (m := re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?" + re.escape(ZIP_NAME), line.strip()))]
    if len(matches) != 1:
        raise ReleaseError("发布页的 SHA-256 校验文件无法识别。")
    destination = Path(destination).absolute()
    received, last_pct = 0, -1
    checksum = hashlib.sha256()
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".lumin-update-", suffix=".part",
                                     delete=False) as handle:
        temp = Path(handle.name)
    try:
        with _request(release.url(ZIP_NAME)) as response, temp.open("wb") as output:
            while block := response.read(512*1024):
                received += len(block)
                if received > release.size:
                    raise ReleaseError("下载的数据超过发行包大小。")
                output.write(block)
                checksum.update(block)
                percent = received * 100 // release.size
                if percent != last_pct:
                    progress(f"正在下载 {release.tag}　{percent}%")
                    last_pct = percent
            output.flush()
            os.fsync(output.fileno())
        if received != release.size or checksum.hexdigest() != matches[0]:
            raise ReleaseError("更新包未通过 SHA-256 或文件大小校验，请重新下载。")
        os.replace(temp, destination)
        return destination
    finally:
        temp.unlink(missing_ok=True)
