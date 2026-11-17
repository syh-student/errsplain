"""自动翻译 hook：生成 / 安装 / 卸载各 shell 的配置片段。

安装后，命令一旦失败（退出码非 0），shell 会把捕获到的报错喂给
``errsplain --hook``，后者以静默精简模式输出翻译；成功命令不打扰。

设计要点：
- 所有脚本块都用 BEGIN/END 标记包裹，install 幂等、uninstall 干净。
- install 前备份原启动文件（若非首次安装）。
- bash/zsh 只在交互式 shell 生效，绝不污染脚本与 CI。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

BEGIN = "# >>> errsplain hook (managed) >>>"
END = "# <<< errsplain hook (managed) <<<"

# ---------------------------------------------------------------- bash / zsh

_BASH_SCRIPT = r'''# >>> errsplain hook (managed) >>>
# errsplain 自动翻译：命令失败时把报错翻译成人话。
# 由 `errsplain hook install` 写入；`errsplain hook uninstall` 移除。

# 只在交互式 shell 生效——脚本、CI、编辑器调用一律不启用
case $- in
  *i*) ;;
  *) return 2>/dev/null || exit 0 ;;
esac

_ERRSPLAIN_BUF="$(mktemp -t errsplain.XXXXXX)"

# 把 stderr 镜像一份到缓冲区，原样仍输出到终端
exec 3>&2 2> >(tee "$_ERRSPLAIN_BUF" >&3)

_errsplain_auto() {
  local rc=$?
  : > "$_ERRSPLAIN_BUF.tmp"
  if [ "$rc" -eq 0 ] || [ "$rc" -eq 130 ]; then
    : > "$_ERRSPLAIN_BUF"
    return
  fi
  [ "${ERRSPLAIN_HOOK:-1}" = "1" ] || { : > "$_ERRSPLAIN_BUF"; return; }
  sleep 0.1
  if [ -s "$_ERRSPLAIN_BUF" ]; then
    errsplain --hook < "$_ERRSPLAIN_BUF"
    : > "$_ERRSPLAIN_BUF"
  fi
}

# 放在最前，确保拿到的是上一条命令的退出码
PROMPT_COMMAND="_errsplain_auto${PROMPT_COMMAND:+;$PROMPT_COMMAND}"

# 临时开关：ERRSPLAIN_HOOK=0 关闭，=1 打开
# <<< errsplain hook (managed) <<<'''

_ZSH_SCRIPT = r'''# >>> errsplain hook (managed) >>>
# errsplain 自动翻译：命令失败时把报错翻译成人话。
[[ -o interactive ]] || return 2>/dev/null || exit 0

_ERRSPLAIN_BUF="$(mktemp -t errsplain.XXXXXX)"
exec 3>&2 2> >(tee "$_ERRSPLAIN_BUF" >&3)

_errsplain_auto() {
  local rc=$?
  if [ "$rc" -eq 0 ] || [ "$rc" -eq 130 ]; then
    : > "$_ERRSPLAIN_BUF"
    return
  fi
  [ "${ERRSPLAIN_HOOK:-1}" = "1" ] || { : > "$_ERRSPLAIN_BUF"; return; }
  sleep 0.1
  if [ -s "$_ERRSPLAIN_BUF" ]; then
    errsplain --hook < "$_ERRSPLAIN_BUF"
    : > "$_ERRSPLAIN_BUF"
  fi
}

autoload -Uz add-zsh-hook 2>/dev/null \
  && add-zsh-hook precmd _errsplain_auto \
  || precmd_functions+=(_errsplain_auto)
# <<< errsplain hook (managed) <<<'''

# ------------------------------------------------------------- powershell

_POWERSHELL_SCRIPT = r'''# >>> errsplain hook (managed) >>>
# errsplain 自动翻译：命令失败时把报错翻译成人话（尽力方案，PowerShell 7 效果最佳）
$global:ErrsplainHookEnabled = $true

function global:prompt {
    $code = $LASTEXITCODE
    if ($global:ErrsplainHookEnabled -and $code -gt 0 -and $code -ne 130) {
        try {
            $err = $Error | Select-Object -First 1
            if ($err) {
                $text = $err.ToString()
                if ($text -and $text.Length -le 8000) { $text | errsplain --hook }
            }
        } catch { }
    }
    "PS $($executionContext.SessionState.Path.CurrentLocation)$('>' * ($nestedPromptLevel + 1)) "
}
# <<< errsplain hook (managed) <<<'''

SCRIPTS: dict[str, str] = {
    "bash": _BASH_SCRIPT,
    "zsh": _ZSH_SCRIPT,
    "powershell": _POWERSHELL_SCRIPT,
}


def detect_shell() -> str:
    """尽最大努力检测当前 shell。"""
    if os.environ.get("ZSH_VERSION"):
        return "zsh"
    shell = os.environ.get("SHELL", "").lower()
    if "zsh" in shell:
        return "zsh"
    if "bash" in shell:
        return "bash"
    if os.name == "nt":
        return "powershell"
    return "bash"


def profile_path(shell: str) -> Path:
    """返回该 shell 的启动文件路径。"""
    home = Path.home()
    if shell == "bash":
        return home / ".bashrc"
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "powershell":
        for name in ("PowerShell", "WindowsPowerShell"):
            p = home / "Documents" / name / "Microsoft.PowerShell_profile.ps1"
            if p.exists():
                return p
        return home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
    raise ValueError(f"不支持的 shell: {shell}")


def _replace_block(existing: str, script: str) -> str:
    """若已有托管块则替换，否则追加到末尾。"""
    if BEGIN in existing and END in existing:
        head = existing[: existing.index(BEGIN)].rstrip()
        tail = existing[existing.index(END) + len(END) :].lstrip("\r\n")
        parts = [p for p in (head, script, tail) if p.strip()]
        return "\n\n".join(parts) + "\n"
    if not existing.strip():
        return script + "\n"
    return existing.rstrip() + "\n\n" + script + "\n"


def _backup(path: Path) -> Path | None:
    """首次安装时备份原文件；返回备份路径（若生成）。"""
    if not path.exists():
        return None
    backup = path.with_name(
        f"{path.name}.{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak"
    )
    backup.write_bytes(path.read_bytes())
    return backup


def install(shell: str, *, target: Path | None = None) -> str:
    shell = shell.lower()
    if shell not in SCRIPTS:
        raise ValueError(f"不支持的 shell: {shell}（可选: {', '.join(SCRIPTS)}）")
    path = Path(target) if target else profile_path(shell)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    first_time = BEGIN not in existing
    backup = _backup(path) if first_time else None
    path.write_text(_replace_block(existing, SCRIPTS[shell]), encoding="utf-8")

    lines = [f"已安装自动翻译 hook → {path}"]
    if backup:
        lines.append(f"原文件已备份 → {backup}")
    lines.append("重新打开终端（或执行 source 对应配置文件）后生效。")
    if shell == "powershell":
        lines.append("提示：PowerShell 5.1 对原生命令报错捕获有限，7+ 效果最佳。")
    return "\n".join(lines)


def uninstall(shell: str, *, target: Path | None = None) -> str:
    shell = shell.lower()
    path = Path(target) if target else profile_path(shell)
    if not path.exists():
        return f"未找到配置文件：{path}，无需卸载。"
    existing = path.read_text(encoding="utf-8", errors="replace")
    if BEGIN not in existing or END not in existing:
        return f"{path} 中没有找到 errsplain hook 块。"
    head = existing[: existing.index(BEGIN)].rstrip()
    tail = existing[existing.index(END) + len(END) :].lstrip("\r\n")
    parts = [p for p in (head, tail) if p.strip()]
    path.write_text(("\n\n".join(parts) + "\n") if parts else "", encoding="utf-8")
    return f"已从 {path} 移除 errsplain hook。"


def status(shell: str, *, target: Path | None = None) -> str:
    shell = shell.lower()
    path = Path(target) if target else profile_path(shell)
    if not path.exists():
        return f"[{shell}] 配置文件不存在：{path}"
    if BEGIN in path.read_text(encoding="utf-8", errors="replace"):
        return f"[{shell}] 已安装：{path}"
    return f"[{shell}] 未安装：{path}"


def handle_hook_command(args: list[str]) -> int:
    """处理 `errsplain hook ...` 子命令，返回退出码。"""
    usage = (
        "用法：errsplain hook <命令> [shell]\n"
        "  install [bash|zsh|powershell]   自动检测并安装（可指定 shell）\n"
        "  uninstall [shell]               卸载\n"
        "  status [shell]                  查看安装状态\n"
        "  show [shell]                    打印配置片段，供手动粘贴\n"
        "\n不指定 shell 时自动检测当前环境。"
    )
    if not args or args[0] in ("-h", "--help", "help"):
        print(usage)
        return 0

    action = args[0].lower()
    shell = args[1].lower() if len(args) > 1 else detect_shell()

    try:
        if action == "install":
            print(install(shell))
            return 0
        if action == "uninstall":
            print(uninstall(shell))
            return 0
        if action == "status":
            if shell not in SCRIPTS:
                shell = detect_shell()
            for s in SCRIPTS:
                print(status(s, target=profile_path(s) if s != shell else None))
            return 0
        if action == "show":
            if shell not in SCRIPTS:
                print(f"不支持的 shell: {shell}", file=__import__("sys").stderr)
                return 2
            print(SCRIPTS[shell])
            return 0
    except ValueError as exc:
        print(f"错误：{exc}", file=__import__("sys").stderr)
        return 2

    print(f"未知 hook 命令：{action}\n\n{usage}")
    return 2
