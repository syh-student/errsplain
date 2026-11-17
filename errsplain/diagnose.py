"""现场诊断：不止告诉你「大概是什么问题」，还去你机器上查「现在到底是什么状态」。

设计原则：
1. **只读**。绝不修改、创建、删除任何东西，不启动服务，不联网（除 DNS/证书这类必要查询）。
2. **必须有超时**。任何一项检查卡住都不能拖住整体。
3. **失败即跳过**。单个检查出错只标记为「无法检测」，绝不抛异常打断主流程。
4. **说人话**。输出的是「你的 Python 是哪个、flask 装了没、端口被谁占」，不是术语。
"""

from __future__ import annotations

import importlib.util
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = os.name == "nt"
_TIMEOUT = 2.0  # 单次探测超时上限（秒）
_TOOL_TIMEOUT = 6.0  # 调外部工具取版本较慢（如 pip），单独放宽


@dataclass
class Finding:
    """一项检查结果。"""

    title: str
    value: str
    hint: str = ""  # 可选的下一步建议
    ok: bool | None = None  # True 正常 / False 有问题 / None 仅信息


# ---------------------------------------------------------------- 文本抽取

# 从报错里捞出具体对象。宁可漏，不能错。
_MODULE_RE = re.compile(
    r"(?:No module named|ModuleNotFoundError: No module named|"
    r"Cannot find module|Cannot find package|"
    r"could not find (?:module|package))\s*['\"]([\w.@/-]+)['\"]",
    re.IGNORECASE,
)
_PORT_RE = re.compile(
    r"(?:"
    r"port\s+(\d{2,5})"                 # "port 8080"
    r"|:\s*(\d{2,5})\b"                 # ":3000" / ":::3000"
    r"|localhost[:：](\d{2,5})"          # localhost:5432
    r"|127\.0\.0\.1[:：](\d{2,5})"       # 127.0.0.1:5432
    r")",
    re.IGNORECASE,
)
_PATH_RE = re.compile(
    r"(?:^|[\s'\"(\[=:])((?:[A-Za-z]:[\\/]|/)[^\s'\"()\[\],;]{2,})"
)

# 相对路径：出现在 No such file / cannot open 这类上下文里，且带引号或明确后缀
_REL_PATH_RE = re.compile(
    r"(?:No such file or directory|cannot find the path specified|"
    r"cannot open|could not open|does not exist)[^'\"]*['\"]([^'\"\n]{2,})['\"]",
    re.IGNORECASE,
)
_COMMAND_RE = re.compile(
    r"(?:command not found:\s*(\S+)|"
    r"'([\w.-]+)'\s*(?:is not recognized|不是内部或外部命令)|"
    r"([\w.-]+):\s*command not found|"
    r"no\s+'([\w.-]+)'\s+command|"
    r"([\w.-]+)\s*:\s*(?:command not found|not found)|"
    r"'([\w.-]+)'.*(?:无法识别|找不到))",
    re.IGNORECASE,
)
_HOST_RE = re.compile(r"(?:Could not resolve host|UnknownHostException|Name or service not known)[:\s]+([\w.-]+\.[A-Za-z]{2,})")

# 环境变量名：只在明确的「未设置」语境里抽取，避免把普通大写词当变量
_ENV_RE = re.compile(
    r"(?:"
    r"\$(\w+): unbound variable"                                   # bash set -u
    r"|(?:environment variable|env var|ENV)\s+['\"]?([A-Z][A-Z0-9_]{2,})['\"]?"
    r"|process\.env\.([A-Z][A-Z0-9_]{2,})"                          # Node
    r"|(?:EnvironmentError|KeyError)\s*:?\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]"
    r"|\b([A-Z][A-Z0-9_]{2,})\s+(?:is not set|is not defined|is undefined|not configured)"
    r")"
)

# 工具名 -> 版本命令。用于「这个工具到底装没装」。
_TOOL_CMDS = {
    "node": (["node", "--version"], None),
    "npm": (["npm", "--version"], None),
    "python": ([sys.executable, "--version"], None),
    "pip": ([sys.executable, "-m", "pip", "--version"], None),
    "java": (["java", "-version"], True),   # 输出在 stderr
    "go": (["go", "version"], None),
    "docker": (["docker", "--version"], None),
    "kubectl": (["kubectl", "version", "--client", "--output=yaml"], None),
    "git": (["git", "--version"], None),
    "gcc": (["gcc", "--version"], None),
    "rustc": (["rustc", "--version"], None),
}


def extract_module(text: str) -> str | None:
    m = _MODULE_RE.search(text)
    return m.group(1) if m else None


def extract_port(text: str) -> int | None:
    # 排除 "12:30" "10:00" 这类时间写法
    cleaned = re.sub(r"\b([01]?\d|2[0-3]):[0-5]\d\b", " ", text)
    for m in _PORT_RE.finditer(cleaned):
        for g in m.groups():
            if g and g.isdigit():
                p = int(g)
                if 0 < p < 65536:
                    return p
    return None


def extract_path(text: str) -> str | None:
    # 先找绝对路径（最明确）
    for m in _PATH_RE.finditer(text):
        cand = m.group(1).rstrip(".,;:")
        # 过滤误抽：URL 片段、纯符号、看起来像时间/版本的东西
        if cand.startswith(("http", "//", "/dev/")) or len(cand) < 4:
            continue
        if re.fullmatch(r"[A-Za-z]:[\\/]?\d*", cand):  # 如 "C:" 、"C:/1"
            continue
        if not re.search(r"[A-Za-z\u4e00-\u9fff]", cand):  # 必须含字母或中文
            continue
        return cand

    # 再找「文件不存在」上下文里的相对路径
    m = _REL_PATH_RE.search(text)
    if m:
        cand = m.group(1).strip()
        # 排除明显不是路径的（含空格的长句子、URL、纯数字）
        if cand and " " not in cand and not cand.startswith(("http", "//")):
            if re.search(r"[A-Za-z]", cand) or "." in cand:
                return cand
    return None


def extract_command(text: str) -> str | None:
    m = _COMMAND_RE.search(text)
    if not m:
        return None
    for g in m.groups():
        if g:
            return g
    return None


def extract_host(text: str) -> str | None:
    m = _HOST_RE.search(text)
    return m.group(1) if m else None


def extract_env_vars(text: str) -> list[str]:
    """抽取报错里提到的环境变量名（最多 4 个，去重保序）。"""
    found: list[str] = []
    for m in _ENV_RE.finditer(text):
        name = next((g for g in m.groups() if g), None)
        if name and name not in found and len(found) < 4:
            found.append(name)
    return found


def extract_tool(text: str) -> str | None:
    """报错里提到的已知工具（用于判断装没装）。"""
    low = text.lower()
    # 有更强信号时优先用具体工具
    for tool in ("docker", "kubectl", "java", "go", "node", "npm", "pip", "gcc", "rustc"):
        if re.search(rf"\b{re.escape(tool)}\b", low):
            return tool
    return None


# ---------------------------------------------------------------- 具体检查

def check_module(name: str) -> Finding:
    """这个 Python 解释器里能不能导入该模块。"""
    try:
        spec = importlib.util.find_spec(name.split(".")[0])
    except (ImportError, AttributeError, ValueError):
        spec = None
    if spec is not None:
        loc = getattr(spec, "origin", None) or "内置/命名空间包"
        return Finding(
            f"模块 {name}",
            f"当前解释器能找到（{loc}）",
            "如果运行时仍报找不到，说明运行代码的 Python 和当前不是同一个",
            ok=True,
        )
    return Finding(
        f"模块 {name}",
        "❌ 当前解释器里没有这个模块",
        f"安装它：{Path(sys.executable).name} -m pip install {name.split('.')[0]}",
        ok=False,
    )


def check_python_env() -> Finding:
    """当前 Python 身份——模块找不到时九成是环境错。"""
    exe = sys.executable
    ver = platform.python_version()
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    tail = "（虚拟环境）" if in_venv else "（可能是系统/全局 Python）"
    return Finding("当前 Python", f"{exe}\n              版本 {ver}{tail}")


def check_command(name: str) -> Finding:
    """命令在不在 PATH 里。"""
    path = shutil.which(name)
    if path:
        return Finding(f"命令 {name}", f"✅ 找到：{path}", ok=True)
    return Finding(
        f"命令 {name}",
        "❌ PATH 里找不到",
        "确认是否已安装、装完是否重开过终端（PATH 需要刷新）",
        ok=False,
    )


def check_path(target: str) -> Finding:
    """路径是否存在、权限如何。"""
    p = Path(os.path.expanduser(target))
    try:
        if not p.exists():
            parent = p.parent
            near: list[str] = []
            if parent.exists():
                try:
                    from difflib import get_close_matches

                    names = [x.name for x in parent.iterdir()]
                    near = get_close_matches(p.name, names, n=3, cutoff=0.5)
                except OSError:
                    pass
            hint = f"父目录 {parent.resolve() if parent.exists() else parent} {'存在' if parent.exists() else '也不存在'}"
            if near:
                hint += f"；你是不是要：{', '.join(near)}"
            return Finding(f"路径 {target}", "❌ 不存在", hint, ok=False)

        st = p.stat()
        bits = []
        if p.is_dir():
            bits.append("目录")
        else:
            bits.append(f"文件 {st.st_size} 字节")
        mode = oct(st.st_mode)[-3:]
        readable = os.access(p, os.R_OK)
        writable = os.access(p, os.W_OK)
        bits.append(f"权限 {mode}")
        bits.append(f"{'可读' if readable else '不可读'}/{'可写' if writable else '不可写'}")
        detail = "，".join(bits)
        problem = not readable
        hint = "如果报 Permission denied，检查属主与权限（Windows 看文件属性→安全）" if problem else ""
        return Finding(f"路径 {target}", f"✅ 存在：{detail}", hint, ok=not problem)
    except OSError as exc:
        return Finding(f"路径 {target}", f"无法检测：{exc}", ok=None)


def check_port(port: int) -> Finding:
    """端口有没有服务在听；能不能连上。"""
    host = "127.0.0.1"
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.8)
    state = ""
    try:
        s.connect((host, port))
        state = f"✅ {host}:{port} 有服务在监听并可连接"
        ok = True
    except ConnectionRefusedError:
        state = f"❌ {host}:{port} 没有服务监听（连接被拒绝）"
        ok = False
    except socket.timeout:
        state = f"⚠️ {host}:{port} 连接超时（可能被防火墙丢包，或服务假死）"
        ok = False
    except OSError as exc:
        state = f"⚠️ {host}:{port} 无法连接：{exc}"
        ok = False
    finally:
        s.close()
    hint = "" if ok else "如果服务本该在跑，检查它是否启动、是否只监听了别的地址"
    return Finding(f"端口 {port}", state, hint, ok=ok)


def check_port_owner(port: int) -> Finding:
    """谁占着这个端口。"""
    try:
        if IS_WINDOWS:
            r = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True,
                timeout=_TIMEOUT, encoding="utf-8", errors="replace",
            )
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0].upper().startswith("TCP"):
                    if parts[1].rsplit(":", 1)[-1] == str(port) and parts[-1].isdigit():
                        pid = parts[-1]
                        name = _win_proc_name(pid)
                        return Finding(
                            f"占用端口 {port} 的进程",
                            f"PID {pid}{f'（{name}）' if name else ''}，状态 {parts[3]}",
                            f"结束它：taskkill /PID {pid} /F" if parts[3].upper() == "LISTENING" else "",
                        )
            return Finding(f"占用端口 {port} 的进程", "未发现占用")
        # 类 Unix
        for cmd in (["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN"], ["ss", "-tlnp"]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
            except FileNotFoundError:
                continue
            if r.returncode == 0 and r.stdout.strip():
                tail = r.stdout.strip().splitlines()[-1][:140]
                return Finding(f"占用端口 {port} 的进程", tail)
        return Finding(f"占用端口 {port} 的进程", "未发现（或系统缺 lsof/ss）")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return Finding(f"占用端口 {port} 的进程", f"查询失败：{exc}")


def _win_proc_name(pid: str) -> str:
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
        first = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
        return first.split(",")[0].strip('"') if first else ""
    except (subprocess.TimeoutExpired, OSError, IndexError):
        return ""


def check_disk(path: str | None = None) -> Finding:
    """磁盘余量——No space left 的根因。"""
    target = path or (os.getcwd())
    try:
        root = Path(target).anchor or ("C:\\" if IS_WINDOWS else "/")
        usage = shutil.disk_usage(root)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        pct = usage.free / usage.total * 100
        ok = pct > 5
        return Finding(
            f"磁盘 {root}",
            f"剩余 {free_gb:.1f} GB / 共 {total_gb:.1f} GB（{pct:.0f}% 可用）",
            "" if ok else "磁盘快满了，清理后再试（也别忘查 inode：df -i）",
            ok=ok,
        )
    except OSError as exc:
        return Finding("磁盘", f"无法检测：{exc}")


def check_dns(host: str) -> Finding:
    try:
        infos = socket.getaddrinfo(host, None)
        ips = sorted({i[4][0] for i in infos})[:3]
        return Finding(f"域名 {host}", f"✅ 解析为 {', '.join(ips)}", ok=True)
    except socket.gaierror as exc:
        return Finding(
            f"域名 {host}", f"❌ 解析失败：{exc}",
            "检查域名拼写、本机 DNS 配置，或用 IP 直连排除 DNS 问题",
            ok=False,
        )
    except OSError as exc:
        return Finding(f"域名 {host}", f"无法检测：{exc}")


def check_certificate(host: str, port: int = 443) -> Finding:
    """证书有效期——证书过期是「突然全站不通」的经典原因。"""
    import ssl
    from datetime import datetime, timezone

    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        not_after = cert.get("notAfter", "")
        if not not_after:
            return Finding(f"证书 {host}", "已连接，但拿不到有效期信息")
        exp = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days = (exp - datetime.now(timezone.utc)).days
        ok = days > 7
        hint = "" if ok else "证书即将/已经过期，尽快续期（certbot renew）"
        return Finding(f"证书 {host}", f"{'✅' if ok else '❌'} 有效期至 {exp.date()}（剩 {days} 天）", hint, ok=ok)
    except ssl.SSLCertVerificationError as exc:
        return Finding(
            f"证书 {host}", f"❌ 校验失败：{exc.verify_message or exc}",
            "自签名证书需 --cacert 指定 CA；生产环境不要关闭校验", ok=False,
        )
    except (socket.timeout, OSError, ssl.SSLError) as exc:
        return Finding(f"证书 {host}", f"无法检测：{exc}")


def check_memory() -> Finding:
    """内存状况——OOM/Killed 的根因。"""
    try:
        if IS_WINDOWS:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            total = st.ullTotalPhys / (1024 ** 3)
            avail = st.ullAvailPhys / (1024 ** 3)
            ok = st.dwMemoryLoad < 90
            return Finding("内存", f"可用 {avail:.1f} GB / 共 {total:.1f} GB（已用 {st.dwMemoryLoad}%）",
                           "" if ok else "内存吃紧，可能触发 OOM", ok=ok)
        meminfo = Path("/proc/meminfo").read_text()
        vals = {k: int(v.split()[0]) for k, v in
                (line.split(":", 1) for line in meminfo.splitlines() if ":" in line)}
        total = vals.get("MemTotal", 0) / 1024 ** 2
        avail = vals.get("MemAvailable", vals.get("MemFree", 0)) / 1024 ** 2
        ok = avail / total > 0.1 if total else True
        return Finding("内存", f"可用 {avail:.1f} GB / 共 {total:.1f} GB",
                       "" if ok else "内存吃紧，可能触发 OOM", ok=ok)
    except (OSError, KeyError, ValueError, AttributeError) as exc:
        return Finding("内存", f"无法检测：{exc}")


def check_fd_limit() -> Finding:
    """文件描述符上限——Too many open files 的根因。"""
    try:
        if IS_WINDOWS:
            return Finding("文件句柄上限", "Windows 无 ulimit 概念，通常是句柄泄漏而非上限")
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        ok = soft >= 4096
        return Finding(
            "文件句柄上限",
            f"soft={soft} hard={hard}",
            "" if ok else "上限偏低，临时提高：ulimit -n 65535",
            ok=ok,
        )
    except (ImportError, OSError, ValueError) as exc:
        return Finding("文件句柄上限", f"无法检测：{exc}")


def check_git_state() -> Finding:
    """当前目录是不是 git 仓库、在哪个分支。"""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            return Finding("Git 状态", "当前目录不是 Git 仓库（或其上层）")
        branch = r.stdout.strip()
        extra = "，处于 detached HEAD" if branch == "HEAD" else ""
        return Finding("Git 状态", f"分支 {branch}{extra}")
    except (subprocess.TimeoutExpired, OSError):
        return Finding("Git 状态", "无法检测（git 不可用）")


def check_node_modules(pkg: str) -> Finding:
    """Node 项目里这个包到底装了没。"""
    nm = Path("node_modules") / pkg
    if nm.exists():
        return Finding(f"node_modules/{pkg}", f"✅ 已安装：{nm.resolve()}", ok=True)
    pkg_json = Path("package.json")
    if not pkg_json.exists():
        return Finding(f"node_modules/{pkg}", "❌ 未安装，且当前目录没有 package.json（可能不在项目根目录）", ok=False)
    return Finding(f"node_modules/{pkg}", "❌ 未安装", f"安装它：npm install {pkg}", ok=False)


# ---------------------------------------------------------------- 环境与工具链

def _mask(value: str) -> str:
    """脱敏：绝不打印完整密钥/密码。"""
    v = value.strip()
    if not v:
        return "(空字符串)"
    if len(v) <= 4:
        return "***（已设置，长度 %d）" % len(v)
    return f"{v[:2]}***（已设置，长度 {len(v)}）"


def _mask_url_creds(value: str) -> str:
    """把 URL 里的 用户名:密码 打码。"""
    return re.sub(r"//[^/@\s]+@", "//***@", value)


def check_env_var(name: str) -> Finding:
    """这个环境变量到底设了没。值一律脱敏。"""
    val = os.environ.get(name)
    if val is None:
        return Finding(
            f"环境变量 {name}",
            "❌ 未设置（当前进程读取不到）",
            f"设置它：export {name}=值（Windows: setx {name} \"值\"，需重开终端）",
            ok=False,
        )
    return Finding(f"环境变量 {name}", f"✅ 已设置：{_mask(val)}", ok=True)


def check_proxy_env() -> Finding:
    """代理相关环境变量——它经常是「明明有网却连不上」的真凶。"""
    names = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
             "http_proxy", "https_proxy", "all_proxy", "no_proxy")
    set_ones = [(n, os.environ[n]) for n in names if os.environ.get(n)]
    if not set_ones:
        return Finding("代理设置", "未设置任何代理环境变量（走直连）")
    shown = "；".join(f"{n}={_mask_url_creds(v)}" for n, v in set_ones[:3])
    return Finding(
        "代理设置", f"已设置：{shown}",
        "若目标在内网或不需要代理，可临时清掉：unset HTTP_PROXY HTTPS_PROXY（Windows: set HTTP_PROXY=）",
    )


def check_tool(tool: str) -> Finding:
    """工具装没装、什么版本。没装本身就是答案。"""
    spec = _TOOL_CMDS.get(tool)
    if not spec:
        return Finding(f"工具 {tool}", "未知工具")
    cmd, in_stderr = spec
    exe = shutil.which(cmd[0]) if cmd[0] != sys.executable else sys.executable
    if not exe:
        return Finding(
            f"工具 {tool}", f"❌ 未安装（PATH 里没有 {cmd[0]}）",
            f"需要先安装 {tool}，或把它加入 PATH",
            ok=False,
        )
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=_TOOL_TIMEOUT,
                           encoding="utf-8", errors="replace")
        out = (r.stderr if in_stderr else r.stdout) or r.stderr or r.stdout
        first = (out.strip().splitlines() or ["(无输出)"])[0][:100]
        return Finding(f"工具 {tool}", f"✅ {first}", ok=True)
    except subprocess.TimeoutExpired:
        return Finding(f"工具 {tool}", "✅ 已安装（取版本超时，这不影响使用）", ok=True)
    except OSError as exc:
        return Finding(f"工具 {tool}", f"⚠️ 已安装但无法获取版本：{exc}")


def check_docker() -> list[Finding]:
    """Docker 守护进程状态 + 正在运行的容器。"""
    findings: list[Finding] = []
    if not shutil.which("docker"):
        return [Finding("Docker", "❌ docker 命令不存在", "安装 Docker Desktop 或 docker engine", ok=False)]
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                           capture_output=True, text=True, timeout=3,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            findings.append(Finding(
                "Docker 守护进程", "❌ 未运行或无权限访问",
                "启动它：Windows 开 Docker Desktop；Linux: sudo systemctl start docker", ok=False))
            return findings
        findings.append(Finding("Docker 守护进程", f"✅ 正常（server {r.stdout.strip()}）", ok=True))
    except (subprocess.TimeoutExpired, OSError) as exc:
        findings.append(Finding("Docker 守护进程", f"⚠️ 响应异常：{exc}"))
        return findings

    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}\t{{.Status}}"],
                           capture_output=True, text=True, timeout=3,
                           encoding="utf-8", errors="replace")
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        findings.append(Finding("运行中的容器", f"{len(lines)} 个" + (f"：{'; '.join(lines[:3])}" if lines else "")))
    except (subprocess.TimeoutExpired, OSError):
        pass
    return findings


def check_docker_port(port: int) -> Finding:
    """有没有容器占用这个端口映射。"""
    try:
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
            capture_output=True, text=True, timeout=3,
            encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            return Finding(f"Docker 是否占用 {port}", "无法查询（守护进程不可用）")
        for line in r.stdout.splitlines():
            if f":{port}->" in line or f"0.0.0.0:{port}" in line:
                name = line.split("\t")[0]
                return Finding(
                    f"Docker 是否占用 {port}", f"是：容器 {name}",
                    f"停止它：docker stop {name}", ok=False,
                )
        return Finding(f"Docker 是否占用 {port}", "没有容器占用该端口", ok=True)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return Finding(f"Docker 是否占用 {port}", f"查询失败：{exc}")


def check_pip_package(name: str) -> Finding:
    """pip 里这个包装了没、什么版本。"""
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            v = version(name)
            return Finding(f"Python 包 {name}", f"✅ 已安装 {v}", ok=True)
        except PackageNotFoundError:
            return Finding(
                f"Python 包 {name}", "❌ 当前环境未安装",
                f"安装：{Path(sys.executable).name} -m pip install {name}",
                ok=False,
            )
    except Exception as exc:  # noqa: BLE001 - 诊断不得抛错
        return Finding(f"Python 包 {name}", f"无法检测：{exc}")


def check_npm_package(name: str) -> Finding:
    """Node 包：已装版本 vs package.json 声明版本。"""
    installed = None
    pkg_json = Path("node_modules") / name / "package.json"
    if pkg_json.exists():
        try:
            import json as _json

            installed = _json.loads(pkg_json.read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError):
            installed = None

    declared = None
    root = Path("package.json")
    if root.exists():
        try:
            import json as _json

            data = _json.loads(root.read_text(encoding="utf-8"))
            declared = (data.get("dependencies", {}).get(name)
                        or data.get("devDependencies", {}).get(name))
        except (OSError, ValueError):
            declared = None

    if installed and declared:
        return Finding(f"Node 包 {name}", f"已装 {installed}，package.json 要求 {declared}", ok=True)
    if installed:
        return Finding(f"Node 包 {name}", f"已装 {installed}（package.json 未声明）", ok=True)
    if declared:
        return Finding(
            f"Node 包 {name}", f"❌ 未安装（package.json 要求 {declared}）",
            f"安装：npm install", ok=False,
        )
    return Finding(
        f"Node 包 {name}", "❌ 未安装，且 package.json 里也没声明",
        f"安装：npm install {name}", ok=False,
    )


def check_kube_context() -> list[Finding]:
    """kubectl 当前指向哪个集群、命名空间。"""
    findings: list[Finding] = []
    if not shutil.which("kubectl"):
        return [Finding("kubectl", "❌ 未安装", "安装 kubectl 并配置 kubeconfig", ok=False)]
    try:
        ctx = subprocess.run(["kubectl", "config", "current-context"],
                             capture_output=True, text=True, timeout=3,
                             encoding="utf-8", errors="replace")
        context = ctx.stdout.strip() or "(未设置)"
        findings.append(Finding("kubectl 当前上下文", context, ok=bool(ctx.stdout.strip())))
        ns = subprocess.run(["kubectl", "config", "view", "--minify",
                             "--output", "jsonpath={.contexts[0].context.namespace}"],
                            capture_output=True, text=True, timeout=3,
                            encoding="utf-8", errors="replace")
        if ns.stdout.strip():
            findings.append(Finding("kubectl 默认命名空间", ns.stdout.strip()))
        else:
            findings.append(Finding("kubectl 默认命名空间", "default（未显式设置）"))
    except (subprocess.TimeoutExpired, OSError) as exc:
        findings.append(Finding("kubectl", f"查询失败：{exc}"))
    return findings


def check_inode(path: str | None = None) -> Finding:
    """inode 使用率——「有空间却写不进去」的元凶。"""
    if IS_WINDOWS:
        return Finding("inode 使用率", "Windows 无 inode 概念，可忽略此因素")
    try:
        target = Path(path or os.getcwd()).anchor or "/"
        st = os.statvfs(target)
        total = st.f_files
        free = st.f_ffree
        if not total:
            return Finding("inode 使用率", "无法读取")
        used_pct = (1 - free / total) * 100
        ok = used_pct < 90
        return Finding(
            f"inode 使用率 {target}",
            f"{'✅' if ok else '❌'} 已用 {used_pct:.0f}%（剩余 {free}）",
            "" if ok else "inode 耗尽：删小文件多的目录（缓存/session/日志碎片）",
            ok=ok,
        )
    except (OSError, AttributeError) as exc:
        return Finding("inode 使用率", f"无法检测：{exc}")


# ---------------------------------------------------------------- 调度

def diagnose(text: str) -> list[Finding]:
    """根据报错内容，跑一组相关的只读检查，返回确凿的现状。"""
    findings: list[Finding] = []
    low = text.lower()

    module = extract_module(text)
    port = extract_port(text)
    path = extract_path(text)
    command = extract_command(text)
    host = extract_host(text)
    env_names = extract_env_vars(text)
    tool = extract_tool(text)

    # 模块相关
    if module:
        findings.append(check_module(module))
        findings.append(check_python_env())
        if "cannot find module" in low or "cannot find package" in low:
            findings.append(check_node_modules(module))
        # pip / npm 依赖版本
        if "pip" in low or "install" in low:
            findings.append(check_pip_package(module))
        if "npm" in low or "node" in low or "cannot find module" in low:
            findings.append(check_npm_package(module))

    # 环境变量
    for name in env_names:
        findings.append(check_env_var(name))

    # 工具装没装（命令找不到、或报错明显来自某个工具）
    if command:
        found = check_command(command)
        findings.append(found)
        # 已找到才值得再查版本；找不到时上面那条已经说清楚了
        if found.ok and command in _TOOL_CMDS:
            findings.append(check_tool(command))
    elif tool and any(k in low for k in ("not found", "command not found", "不是内部或外部命令",
                                        "no such file or directory", "无法识别")):
        findings.append(check_tool(tool))

    # 路径相关
    if path:
        findings.append(check_path(path))

    # 端口相关
    if port:
        findings.append(check_port(port))
        findings.append(check_port_owner(port))
        if shutil.which("docker"):
            findings.append(check_docker_port(port))

    # 域名 / 证书
    if host:
        findings.append(check_dns(host))
        if "certificate" in low or "ssl" in low or "tls" in low:
            findings.append(check_certificate(host))

    # 代理——「有网却连不上」的高频真凶
    if any(k in low for k in (
        "timeout", "timed out", "could not resolve", "connection refused",
        "econnrefused", "econnreset", "enotfound", "getaddrinfo",
        "unreachable", "proxy", "ssl", "certificate", "handshake", "network is down",
    )):
        findings.append(check_proxy_env())

    # Docker：只在「非命令缺失」语境下查守护进程（命令缺失时上面已经报过了）
    docker_cmd_missing = bool(command == "docker") or "docker: command not found" in low
    if not docker_cmd_missing and (
        "docker" in low or "container" in low or "port is already allocated" in low
    ):
        findings.extend(check_docker())

    # Kubernetes
    kube_cmd_missing = bool(command == "kubectl") or "kubectl: command not found" in low
    if not kube_cmd_missing and any(
        k in low for k in ("kubectl", "kubernetes", "pod", "helm", "crashloop", "imagepull")
    ):
        findings.extend(check_kube_context())

    # 资源相关
    if any(k in low for k in ("no space", "enospc", "disk full", "磁盘")):
        findings.append(check_disk(path))
        findings.append(check_inode(path))
    if any(k in low for k in ("out of memory", "oom", "killed", "cannot allocate", "memoryerror")):
        findings.append(check_memory())
    if "too many open files" in low or "emfile" in low:
        findings.append(check_fd_limit())

    # git 相关
    if "git" in low:
        findings.append(check_git_state())

    # 去重（同标题只留第一次）
    seen: set[str] = set()
    uniq: list[Finding] = []
    for f in findings:
        if f.title in seen:
            continue
        seen.add(f.title)
        uniq.append(f)
    return uniq


def has_checkable(text: str) -> bool:
    """这段报错里有没有可以做实地检查的具体对象。"""
    return any((
        extract_module(text), extract_port(text), extract_path(text),
        extract_command(text), extract_host(text), extract_env_vars(text),
    ))


def render(findings: list[Finding], palette=None) -> str:
    """把检测结果渲染成终端友好的文本（按显示宽度折行、悬挂缩进）。

    传入 palette 时按状态着色：成功绿、失败红、未知黄。
    """
    if not findings:
        return ""
    from .layout import wrap

    cols = shutil.get_terminal_size(fallback=(100, 24)).columns
    cols = max(cols, 46)
    # 用 palette.paint：颜色关闭时它不会硬塞一个 \033[0m 进管道输出
    paint = palette.paint if palette else (lambda t, c: t)
    ok_color = {True: palette.green if palette else "", False: palette.red if palette else ""}
    lines = ["  " + paint("── 现场诊断（基于你本机的实际状态）──", palette.bold + palette.cyan if palette else "")]
    for f in findings:
        color = "" if f.ok is None else (ok_color.get(f.ok, palette.yellow if palette else ""))
        for i, ln in enumerate(wrap(f"{f.title}：{f.value}", cols - 4)):
            indent = "  " if i == 0 else "      "
            lines.append(indent + (paint(ln, color) if i == 0 else ln))
        if f.hint:
            for i, ln in enumerate(wrap(f"→ {f.hint}", cols - 6)):
                lines.append(("    " if i == 0 else "        ") + ln)
    return "\n".join(lines)
