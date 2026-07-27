#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convert GitHub file or directory links to raw.githubusercontent.com links.

Run interactively:
    python github_raw_tui.py

Or use one-shot mode:
    python github_raw_tui.py "https://github.com/owner/repo/blob/main/path/image.webp"
    python github_raw_tui.py "https://github.com/owner/repo/tree/main/path" --format raw
    python github_raw_tui.py "https://github.com/owner/repo/tree/main/path" --decode-url
    python github_raw_tui.py "看番/新攻壳机动队/第3集"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


IMAGE_EXTENSIONS = {
    ".apng",
    ".avif",
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}

GITHUB_API = "https://api.github.com"
RAW_HOST = "raw.githubusercontent.com"


def configure_stdio_utf8() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class GitHubLink:
    owner: str
    repo: str
    ref: str
    path: str
    kind: str


class LinkError(ValueError):
    """Raised when a link cannot be parsed or processed."""


@dataclass(frozen=True)
class GitHubRepo:
    owner: str
    repo: str
    ref: str


def script_root() -> Path:
    return Path(__file__).resolve().parent


def run_git(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise LinkError("could not read Git repository info") from exc

    return result.stdout.strip()


def parse_github_remote(remote_url: str) -> tuple[str, str]:
    ssh_match = re.match(r"^(?:git@|ssh://git@)github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$", remote_url)
    if ssh_match:
        return ssh_match.group("owner"), ssh_match.group("repo")

    parsed = urllib.parse.urlparse(remote_url)
    if parsed.netloc.lower() != "github.com":
        raise LinkError("git remote is not a github.com repository")

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise LinkError("git remote is missing owner/repo")

    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return parts[0], repo


def get_local_repo(root: Path) -> GitHubRepo:
    remote_url = run_git(["remote", "get-url", "origin"], root)
    owner, repo = parse_github_remote(remote_url)

    try:
        ref = run_git(["branch", "--show-current"], root)
    except LinkError:
        ref = ""
    if not ref:
        ref = "main"

    return GitHubRepo(owner=owner, repo=repo, ref=ref)


def parse_github_link(link: str) -> GitHubLink:
    text = link.strip()
    if not text:
        raise LinkError("empty link")

    parsed = urllib.parse.urlparse(text)
    host = parsed.netloc.lower()
    parts = [urllib.parse.unquote(part) for part in parsed.path.strip("/").split("/") if part]

    if host == "github.com":
        if len(parts) < 4:
            raise LinkError("GitHub link is missing owner/repo/type/ref parts")

        owner, repo, kind, ref = parts[:4]
        if kind not in {"blob", "tree", "raw"}:
            raise LinkError("GitHub link must contain /blob/, /tree/, or /raw/")

        path = "/".join(parts[4:])
        if not path:
            raise LinkError("GitHub link is missing a file or directory path")

        normalized_kind = "blob" if kind == "raw" else kind
        return GitHubLink(owner=owner, repo=repo, ref=ref, path=path, kind=normalized_kind)

    if host == RAW_HOST:
        if len(parts) < 4:
            raise LinkError("raw.githubusercontent.com link is missing owner/repo/ref/path")

        owner, repo, ref = parts[:3]
        path = "/".join(parts[3:])
        return GitHubLink(owner=owner, repo=repo, ref=ref, path=path, kind="blob")

    raise LinkError("unsupported host; paste a github.com or raw.githubusercontent.com link")


def is_url(text: str) -> bool:
    parsed = urllib.parse.urlparse(text)
    return bool(parsed.scheme and parsed.netloc)

def quote_path(path: str) -> str:
    return "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))


def raw_url(item: GitHubLink | tuple[str, str, str, str]) -> str:
    if isinstance(item, GitHubLink):
        owner, repo, ref, path = item.owner, item.repo, item.ref, item.path
    else:
        owner, repo, ref, path = item

    return f"https://{RAW_HOST}/{owner}/{repo}/{urllib.parse.quote(ref, safe='')}/{quote_path(path)}"


def is_image_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in IMAGE_EXTENSIONS


def github_api_get(url: str) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-image-host-raw-link-tui",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(detail).get("message", detail)
        except json.JSONDecodeError:
            message = detail
        raise LinkError(f"GitHub API returned {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise LinkError(f"network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LinkError("network timeout") from exc


def contents_api_url(link: GitHubLink) -> str:
    encoded_path = quote_path(link.path)
    encoded_ref = urllib.parse.quote(link.ref, safe="")
    return f"{GITHUB_API}/repos/{link.owner}/{link.repo}/contents/{encoded_path}?ref={encoded_ref}"


def collect_directory_images(link: GitHubLink) -> list[str]:
    data = github_api_get(contents_api_url(link))
    results: list[str] = []

    if isinstance(data, dict):
        item_type = data.get("type")
        path = str(data.get("path", ""))
        if item_type == "file" and is_image_path(path):
            results.append(raw_url((link.owner, link.repo, link.ref, path)))
        elif item_type == "dir":
            nested = GitHubLink(link.owner, link.repo, link.ref, path, "tree")
            results.extend(collect_directory_images(nested))
        return results

    if not isinstance(data, list):
        raise LinkError("unexpected GitHub API response")

    for item in data:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        path = str(item.get("path", ""))
        if item_type == "dir":
            nested = GitHubLink(link.owner, link.repo, link.ref, path, "tree")
            results.extend(collect_directory_images(nested))
        elif item_type == "file" and is_image_path(path):
            results.append(raw_url((link.owner, link.repo, link.ref, path)))

    return results


def local_relative_path(text: str) -> str:
    path = urllib.parse.unquote(text.strip().strip('"').strip("'")).replace("\\", "/")
    path = path.lstrip("/")
    if not path:
        raise LinkError("empty path")
    return path


def resolve_local_path(root: Path, relative_path: str) -> Path:
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise LinkError("relative path must stay inside the repository root") from exc
    return target


def local_images_from_path(root: Path, repo: GitHubRepo, relative_path: str) -> list[str]:
    target = resolve_local_path(root, relative_path)
    if not target.exists():
        raise LinkError(f"local path does not exist: {relative_path}")

    if target.is_file():
        normalized = target.relative_to(root).as_posix()
        if not is_image_path(normalized):
            raise LinkError("the local file does not look like an image")
        return [raw_url((repo.owner, repo.repo, repo.ref, normalized))]

    if not target.is_dir():
        raise LinkError("local path must be a file or directory")

    urls: list[str] = []
    for item in sorted(target.rglob("*")):
        if not item.is_file():
            continue
        normalized = item.relative_to(root).as_posix()
        if is_image_path(normalized):
            urls.append(raw_url((repo.owner, repo.repo, repo.ref, normalized)))
    return urls


def convert_link(link: str) -> list[str]:
    if not is_url(link.strip()):
        root = script_root()
        repo = get_local_repo(root)
        return local_images_from_path(root, repo, local_relative_path(link))

    parsed = parse_github_link(link)
    if parsed.kind == "tree":
        return collect_directory_images(parsed)

    if not is_image_path(parsed.path):
        raise LinkError("the file link does not look like an image")

    return [raw_url(parsed)]

def markdown_alt_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    name = urllib.parse.unquote(PurePosixPath(path).name)
    return name.replace("\\", "\\\\").replace("]", "\\]")


def display_url(url: str, decode_url: bool) -> str:
    if decode_url:
        return urllib.parse.unquote(url)
    return url


def format_results(urls: Iterable[str], output_format: str, decode_url: bool = False) -> list[str]:
    displayed_urls = [display_url(url, decode_url) for url in urls]

    if output_format == "raw":
        return displayed_urls

    return [f"![{markdown_alt_from_url(url)}]({url})" for url in displayed_urls]


def write_output(path: str, lines: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines))
        if lines:
            file.write("\n")


def choose_format() -> str:
    while True:
        print("\n输出格式：")
        print("  1. Markdown 图片引用")
        print("  2. 原始 raw 链接")
        choice = input("请选择 [1/2，默认 1]：").strip()
        if choice in {"", "1"}:
            return "markdown"
        if choice == "2":
            return "raw"
        print("请输入 1 或 2。")

def choose_decode_url() -> bool:
    while True:
        choice = input("\n把链接里的 %xx 转义显示成中文？[y/N]：").strip().lower()
        if choice in {"", "n", "no"}:
            return False
        if choice in {"y", "yes"}:
            return True
        print("请输入 y 或 n。")



def interactive_main() -> int:
    print("GitHub Raw 链接生成器")
    print("支持 GitHub 图片/目录链接、raw 链接，以及基于仓库根目录的相对路径。")

    while True:
        link = input("\n粘贴链接或相对路径（直接回车退出）：").strip()
        if not link:
            print("已退出。")
            return 0

        output_format = choose_format()
        decode_url = choose_decode_url()

        try:
            urls = convert_link(link)
        except LinkError as exc:
            print(f"\n处理失败：{exc}")
            continue

        lines = format_results(urls, output_format, decode_url)
        print(f"\n共生成 {len(lines)} 条：")
        for line in lines:
            print(line)



def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert GitHub image file or directory links to raw.githubusercontent.com links.",
    )
    parser.add_argument("link", nargs="?", help="GitHub blob/tree/raw link or repository-relative path to convert")
    parser.add_argument(
        "-f",
        "--format",
        choices=("raw", "markdown"),
        default="markdown",
        help="output format, default: markdown",
    )
    parser.add_argument("-o", "--output", help="write result to a UTF-8 text file")
    parser.add_argument(
        "--decode-url",
        action="store_true",
        help="decode percent escapes in output URLs, for example %%E7%%9C%%8B -> 看",
    )
    return parser


def cli_main(argv: list[str]) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.link:
        return interactive_main()

    try:
        urls = convert_link(args.link)
    except LinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    lines = format_results(urls, args.format, args.decode_url)
    if args.output:
        write_output(args.output, lines)
    else:
        for line in lines:
            print(line)

    return 0


if __name__ == "__main__":
    configure_stdio_utf8()
    raise SystemExit(cli_main(sys.argv[1:]))
