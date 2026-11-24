# errsplain

**把天书般的报错翻译成人话。**
**不止告诉你「大概是什么问题」，还去你机器上查「现在到底是什么状态」。**

**命令行 + IDE 剪贴板（VS Code / Visual Studio / PyCharm / IDEA / Rider 等，Windows）** · 纯离线 · 零第三方依赖 · 中英双语 · 可扩展词库

终端里用管道直读；在 VS Code、Visual Studio、PyCharm、IDEA、Rider 等 IDE 里复制报错，也能用 `errsplain watch` / `errsplain clip` 翻译（Windows）。

犯错不可怕，看不懂报错才可怕。`errsplain` 让一段 `fatal: refusing to merge unrelated histories` 变成：

> Git 发现你要合并的两个分支从没有任何一次共同的提交，出于安全考虑拒绝合并。
> **最常见的原因**：本地 `git init` 了一个新仓库，却想直接 pull 一个已有的远端仓库。
> **怎么修**：确认无误后 `git pull origin main --allow-unrelated-histories`……

而 `errsplain diagnose` 更进一步——它会真的去检查你的环境：

```bash
$ errsplain diagnose "ModuleNotFoundError: No module named 'flask'"

  ── 现场诊断（基于你本机的实际状态）──
  模块 flask：❌ 当前解释器里没有这个模块
    → 安装它：python.exe -m pip install flask
  当前 Python：C:\...\python.exe
              版本 3.13.14（可能是系统/全局 Python）
```

不是"可能装到别的环境了"，而是**你的 Python 是哪个、这个模块确实没有、该敲什么命令**。

一切都在本地完成。不联网（除 DNS/证书检查）、不上传你的报错、不需要账号、不需要 API Key。

## 为什么做这个

新手遇到报错的三条路：问 AI（要联网、要把代码贴出去）、搜搜索引擎（前五条全是广告）、问同事（要看人脸色）。

`errsplain` 是第四条：**对着终端一管道，立刻得到中文人话解释和可复制的修复命令**。快、私密、离线可用。部署在内网、飞机上、涉密环境里都没问题。

## 安装

需要 Python 3.9+（几乎每台开发机都有）。

```bash
# 方式一：从 GitHub 直接安装（推荐，需要 Git）
python -m pip install "git+https://github.com/syh-student/errsplain.git"
errsplain "你的报错"

# 方式二：克隆源码后安装
git clone https://github.com/syh-student/errsplain.git
cd errsplain
python -m pip install .
errsplain "你的报错"

# 方式三：直接跑，零安装
git clone https://github.com/syh-student/errsplain.git
cd errsplain
python -m errsplain "你的报错"
```

没有任何第三方运行时依赖。真的没有。

> 自行构建 wheel 并从本地安装（`build` 只用于开发构建，不是运行时依赖）：
> ```bash
> python -m pip install build
> python -m build
> python -m pip install dist/errsplain-0.1.0-py3-none-any.whl
> ```

## 用法

### 直接贴报错

```bash
$ errsplain "fatal: refusing to merge unrelated histories"

找到 1 条相关解释：

  [1] Git 拒绝合并两个没有共同祖先的历史
      分类 git  相关度 高 (90%)

  这是什么意思  Git 发现这两个分支从没有共同提交，出于安全考虑拒绝合并……

  最常见的原因
    1. 本地 git init 了一个新仓库，却想 pull 一个已有远端仓库
  ...
```

### 交互式浏览（真实终端）

想在结果之间切换、按需展开详情，而不是一次滚动一大屏？加 `-i`：

```bash
errsplain -i "ModuleNotFoundError: No module named 'flask'"
errsplain -i --diagnose "你的报错"  # 启动后直接看现场诊断
```

界面采用顶部结果导航、中央解释卡片和底部快捷键栏；摘要只显示最关键的一步，完整内容按需展开。仍只使用 Python 标准库，不引入 curses 或第三方 TUI 依赖。

| 按键 | 操作 |
|---|---|
| `↑` / `↓`、`←` / `→` | 切换匹配结果；单条结果时上下滚动 |
| `Enter` / `F` | 展开或收起全部原因、修复和注意事项 |
| `D` / `Tab` | 切换只读现场诊断 |
| `PgUp` / `PgDn`、`Home` / `End` | 滚动正文、跳到开头或末尾 |
| `?` | 打开或关闭键盘帮助 |
| `Q` / `Esc` | 退出并恢复原终端画面 |

交互模式需要 stdin/stdout 都连接真实终端；脚本、管道和 `--json` 仍使用原有非交互输出。

装了 PowerShell hook 后，命令报错后直接输入 `cuowu` 也能进入这个界面，见下文。

### 从管道读（最常用）

把命令的报错直接管道进来——这才是日常的姿势：

```bash
npm install 2>&1 | errsplain
python manage.py runserver 2>&1 | errsplain
git push 2>&1 | errsplain --category git
```

或者直接读日志文件：

```bash
errsplain --file build.log
```

### 其他

```bash
errsplain --list                  # 看看目前收录了哪些报错
errsplain --stats                 # 词库统计
errsplain -c python "找不到模块"   # 限定分类搜索
errsplain --json "Module not found"   # JSON 输出，接脚本用
errsplain --diagnose "你的报错"    # 翻译 + 现场诊断
errsplain --json --diagnose "你的报错"  # JSON 同时包含翻译与诊断
errsplain diagnose "你的报错"      # 只做现场诊断
errsplain -i --clip               # 从剪贴板读报错，直接进交互导航（适合 IDE）
errsplain "你的报错" --full       # 完整卡片（默认精简：意思 + 前 3 条修法）
errsplain --validate              # 校验词库
```

普通 `--json` 返回匹配结果数组；与 `--diagnose` 同用时返回
`{"matches": [...], "diagnosis": [...]}`，诊断字段为 `title / value / hint / ok`。

### 退出码

- `0`：找到匹配
- `1`：没找到
- `2`：词库损坏

方便写进脚本：`npm test 2>&1 | errsplain || echo "未知错误，手动排查"`。

## 现场诊断：去你机器上查真相

报错翻译告诉你"大概是什么问题"，但解决实际问题需要知道"**你现在到底什么状态**"。`diagnose` 会从报错里提取具体对象（模块名、端口、路径、命令、域名），然后**在本机实际执行只读检查**：

```bash
errsplain diagnose "No module named 'flask'"        # 这个 Python 里到底装了没
errsplain diagnose "listen EADDRINUSE :::3000"      # 谁占着 3000 端口
errsplain diagnose "Permission denied: /var/log/app.log"  # 文件真实权限和属主
errsplain diagnose "command not found: yarn"        # PATH 里到底有没有
errsplain diagnose "Could not resolve host: foo.com" # DNS 真的解析不了吗
errsplain diagnose "JAVA_HOME is not set"           # 环境变量设了没（值会脱敏）
```

也可以和翻译一起用（最实用）：

```bash
errsplain "No module named 'flask'" --diagnose   # 解释 + 诊断
errsplain clip --diagnose                        # IDE 复制报错 → 解释 + 诊断
errsplain watch --diagnose                       # 常驻监听，自动带诊断
```

实跑效果（真实输出）：

```
$ errsplain diagnose "listen EADDRINUSE ::: 51555"

  ── 现场诊断（基于你本机的实际状态）──
  端口 51555：✅ 127.0.0.1:51555 有服务在监听并可连接
  占用端口 51555 的进程：PID 24768（python.exe），状态 LISTENING
    → 结束它：taskkill /PID 24768 /F
```

```
$ errsplain diagnose "No such file or directory: 'pakcage.json'"

  ── 现场诊断（基于你本机的实际状态）──
  路径 pakcage.json：❌ 不存在
    → 父目录 C:\...\errsplain 存在；你是不是要：package.json
```

目前支持的实际检查：

| 报错里出现 | 它会去查 |
|---|---|
| 模块名 | 当前解释器能否导入、当前 Python 完整路径与版本、是否虚拟环境；Node 项目还查 `node_modules` 与 `package.json` 声明版本 |
| 端口 | 端口是否有服务在听、能否连通、**哪个进程占着**、怎么结束它；有 Docker 时还查是哪个容器映射了它 |
| 路径 | 是否存在、真实权限、大小、类型；不存在时给父目录状态和**最相近的文件名** |
| 命令名 | PATH 里是否真的存在、完整路径、**版本号**（java/go/docker/kubectl/node/npm/pip/gcc/rustc） |
| 环境变量 | 到底设了没（**值一律脱敏**，只显示长度）、代理变量的实际值（URL 里的账号密码打码） |
| 域名 | 是否真的解析不了、解析到哪些 IP；涉及证书时查**有效期还剩几天** |
| Docker | 守护进程是否在跑、版本、正在运行的容器列表 |
| Kubernetes | kubectl 是否存在、当前指向哪个集群和命名空间 |
| 磁盘/内存/句柄 | 余量、使用率、inode 用量、`ulimit` 上限 |
| git 相关 | 当前目录是不是仓库、在哪个分支、是否 detached HEAD |

几个真实输出：

```
$ errsplain diagnose "listen EADDRINUSE ::: 51555"
  端口 51555：✅ 127.0.0.1:51555 有服务在监听并可连接
  占用端口 51555 的进程：PID 24768（python.exe），状态 LISTENING
    → 结束它：taskkill /PID 24768 /F

$ errsplain diagnose "No such file or directory: 'pakcage.json'"
  路径 pakcage.json：❌ 不存在
    → 父目录 C:\...\errsplain 存在；你是不是要：package.json

$ errsplain diagnose "JAVA_HOME is not set and no 'java' command could be found"
  环境变量 JAVA_HOME：❌ 未设置（当前进程读取不到）
    → 设置它：export JAVA_HOME=值（Windows: setx JAVA_HOME "值"，需重开终端）
  命令 java：❌ PATH 里找不到
```

**安全边界**：全部只读——不修改文件、不启动服务、不装包、不改配置。环境变量与代理值**一律脱敏**（只显示是否设置与长度，URL 里的账号密码打码）。每项检查带超时，单项失败只标记「无法检测」，绝不打断主流程。诊断代码里没有任何写操作，并有专门的测试守护"密钥不外泄"。

## 自动翻译（Bash / Zsh）

不想每次都敲管道？装一次 hook，**以后任何命令一失败，翻译就自动出现在报错下面**，成功时完全不打扰。

```bash
errsplain hook install          # 自动检测当前 shell 并安装
# 或指定：errsplain hook install bash / zsh / powershell
errsplain hook status           # 查看装没装
errsplain hook uninstall        # 后悔了，一键卸载
```

装完重开终端生效。之后的效果：

```bash
$ git pull
fatal: refusing to merge unrelated histories
  errsplain → [git] Git 拒绝合并两个没有共同祖先的历史  (高)
    两个分支从没有任何共同提交，出于安全考虑拒绝合并。
    试试：确认无误后 git pull origin main --allow-unrelated-histories

$ npm install          # 成功 —— 什么都不会多显示，和以前一样
added 142 packages in 3s
```

几个贴心细节：

- **只在交互式终端生效**，绝不污染脚本、CI、编辑器调用。
- **Ctrl+C 不算报错**，不会弹翻译。
- **没匹配到时完全静默**——人尽皆知的报错（比如 `ls` 文件不存在）不会刷屏。
- 临时关闭自动翻译：`ERRSPLAIN_HOOK=0`（`cuowu` 仍可用）。
- 安装会自动备份原配置文件；`errsplain hook uninstall` 只移除自己那段，不动你的其他配置。

## PowerShell：报错后敲 cuowu

PowerShell 5.1 不会把 `python`、`git`、`npm` 这类原生命令的 stderr 存进 `$Error`，自动弹翻译很容易漏。所以 PowerShell 版只做一件最简单的事：装一个短命令 `cuowu`，报错后下一句敲它就进入交互导航。

```powershell
errsplain hook install powershell   # 写入你的 PowerShell profile
# 重开终端后：
PS> python app.py
Traceback (most recent call last):
  ...
ModuleNotFoundError: No module named 'flask'
PS> cuowu
```

它会当场读取当前终端画面里的报错，不需要复制粘贴、不需要长路径、不改 `prompt`、不常驻后台。`cuowu` 已存在同名命令时不会覆盖。

不想自动装，也可以把 `errsplain hook show powershell` 打印的内容手动粘进 profile。

## IDE 里的报错（VS Code / Visual Studio / PyCharm / IDEA / Rider …）

IDE 点「运行」后的报错画在它自己的控制台里，**不经过 shell**，所以上面的 hook 覆盖不到。但所有 IDE 都有一个共同出口：**报错能选中、能复制**。errsplain 抓住剪贴板，一样能翻译，仍然零依赖（直接调 Windows API，不需要图形库）。

### 方式零：桌面快捷方式（最省事）

给 `errsplain -i --clip` 建一个桌面快捷方式：在 IDE 里复制报错，双击快捷方式，直接进入交互导航，不用记命令。快捷方式目标设为 Python，参数填 `-m errsplain -i --clip`，起始位置设为项目目录即可。

### 方式一：常驻自动监听（推荐，复制即翻译）

```bash
errsplain watch                    # 启动后台监听，Ctrl+C 退出
```

启动后，你在**任意 IDE**（或任何程序）里一复制到像报错的文字，翻译就自动弹窗 + 写回剪贴板，直接 Ctrl+V 就能粘贴译文。复制普通代码、成功日志则完全不打扰。

```bash
errsplain watch --no-notify        # 不弹窗，只在终端打印
errsplain watch --no-write         # 不覆盖剪贴板原文
errsplain watch --interval 0.5     # 调整轮询间隔（默认 0.8 秒）
```

想开机自启？把 `errsplain watch` 放进「启动」文件夹，或用任务计划程序。

### 方式二：按需触发（适合绑快捷键）

```bash
errsplain clip                     # 翻译当前剪贴板的报错，结果写回剪贴板
errsplain clip --no-write          # 只打印，不写回
errsplain clip --force             # 强制翻译（即使看起来不像报错）
```

把它绑到一个全局快捷键（如 `Ctrl+Alt+E`），在 IDE 里选中报错、按快捷键，译文就复制到剪贴板了。Windows 可用 PowerToys / AutoHotkey，macOS 可用系统快捷键绑定「运行 shell 命令」。

> **平台说明**：`clip` / `watch` 目前仅支持 Windows（macOS / Linux 可用管道：`命令 2>&1 | errsplain`，或用剪贴板工具把内容喂给 stdin：`pbpaste | errsplain`）。判断「像不像报错」用关键词 + 错误码（如 `TS2322`、`EACCES`、`ModuleNotFoundError`）识别，复制普通文本不会误触发。

## 收录的报错

目前 **283 条**，覆盖 **66 个分类**，仍在持续增加：

### 编程语言与框架
| 分类 | 数量 | 典型报错 |
|------|------|----------|
| **go** | 17 | 未使用变量、类型不匹配、import cycle、死锁、nil 指针、并发写 map、模块代理 |
| **python** | 22 | ModuleNotFoundError、SyntaxError、TypeError、pip 全套、venv、Django、pytest、UnicodeDecodeError、JSON 解析 |
| **typescript** | 7 | TS2304/2322/2339/2531/7006、找不到模块、属性无初始化 |
| **cpp** | 7 | undefined reference、段错误、头文件缺失、CMake、make、链接器找不到库 |
| **java** | 7 | ClassNotFound、NPE、堆溢出、Maven、Gradle |
| **spring** | 3 | Bean 创建失败、数据源配置、端口占用 |
| **rust** | 4 | 借用检查、move、trait bound、类型不匹配 |
| **kotlin** | 3 | Unresolved reference、空安全、Gradle 插件版本不兼容 |
| **php** | 3 | Composer 依赖解析、Laravel APP_KEY、目录不可写 |
| **ruby** | 3 | Bundler 找不到 gem、Rails 迁移未执行、GemNotFound |
| **csharp** | 2 | CS0246、NuGet NU1101 |
| **scala / swift / perl / lua / r / elixir / haskell / dart / zig** | 各 1-2 | 各自的编译/依赖/类型错误 |

### 数据库与消息队列
| 分类 | 数量 | 典型报错 |
|------|------|----------|
| **network** | 28 | curl 全套错误码(6/7/28/35/51/56/60)、wget、ssh、DNS、HTTP 4xx/5xx、代理、ECONNRESET |
| **oracle** | 5 | ORA-12541、ORA-12514、ORA-00942、ORA-01017、ORA-28000 |
| **postgresql / mysql / sqlite** | 4/3/1 | 表不存在、唯一键冲突、认证失败、连接拒绝 |
| **sqlserver** | 3 | 登录失败(18456)、连接超时、死锁 |
| **elasticsearch** | 3 | 集群 RED、磁盘水位只读、mapping 冲突 |
| **clickhouse** | 2 | 内存超限(Code 241)、分区片段过多(Code 252) |
| **redis / mongodb / cassandra / neo4j / mariadb** | 1-3 | 连接拒绝、认证失败、副本不足、WRONGTYPE |
| **rabbitmq / rocketmq / kafka** | 1-2 | 连接拒绝、队列积压、NameServer 不通、请求超时 |

### 前端与移动端
| 分类 | 数量 | 典型报错 |
|------|------|----------|
| **frontend** | 11 | CORS、Vite、Webpack、ESLint、React Hooks、Vue、Angular、Next.js 水合 |
| **npm / node** | 7/5 | 找不到模块、EACCES、ERESOLVE、EADDRINUSE、ENOENT、Jest、JS TypeError |
| **android** | 5 | Gradle 同步失败、SDK 未配置、INSTALL_FAILED_*、adb 离线、构建 OOM |
| **ios** | 2 | 代码签名失败、CocoaPods 报错 |
| **mobile** | 2 | React Native Metro 打包、Android 构建失败 |

### 云原生与基础设施
| 分类 | 数量 | 典型报错 |
|------|------|----------|
| **kubernetes** | 7 | ImagePullBackOff、CrashLoopBackOff、Pending、PVC 未绑定、OOMKilled、x509、Helm 发布失败 |
| **docker** | 6 | 守护进程未启动、端口占用、entrypoint、Compose 端口冲突、依赖未就绪 |
| **terraform** | 3 | 状态锁、Provider 下载失败、状态不一致 |
| **aws / ansible** | 3/2 | 凭证缺失、AccessDenied、区域未配置、主机不可达、sudo 密码 |
| **ci** | 4 | GitHub Actions、Jenkins、GitLab CI 配置错误 |
| **nginx / apache / tomcat / iis** | 3/2/1/2 | 端口占用、配置语法、502、403、500.19、应用池崩溃 |

### 系统、工具与运维
| 分类 | 数量 | 典型报错 |
|------|------|----------|
| **windows** | 7 | 命令找不到、执行策略禁止脚本、拒绝访问、端口占用、文件被占用、DLL 缺失 |
| **linux** | 6 | Permission denied、command not found、shell 语法、SELinux、iptables、NFS、bad interpreter |
| **system** | 5 | Too many open files、OOM Killed、inode 耗尽、Resource unavailable、systemd 失败 |
| **git** | 10 | 无关历史、detached HEAD、合并冲突、SSH 密钥、Git LFS、文件名过长 |
| **security** | 3 | 证书过期、certbot 续期失败、SSH 私钥权限过松 |
| **test** | 6 | JUnit、PHPUnit、RSpec、Cypress、Playwright、Selenium |
| **lint** | 5 | Prettier、Black、Ruff、Flake8、mypy |
| **ml** | 5 | CUDA 显存不足、驱动不匹配、Jupyter 内核崩溃、NumPy 形状、pandas 列名 |
| **version / package** | 3/2 | nvm、pyenv、rbenv、apt/dpkg、Homebrew |
| **editor / email / encoding** | 3/2/1 | Vim swap、tmux 会话、SMTP 认证、BOM 编码 |
| **general** | 6 | 权限不足、连接拒绝、连接超时、SSL 证书、磁盘已满 |

词库是项目的生命线，欢迎 PR 补充——见 [CONTRIBUTING.md](CONTRIBUTING.md)。同一个错误（如 `ECONNREFUSED`）可能匹配到多个工具的词条，结果按相关度排序并标注分类，照着你的实际环境选即可。`general` 分类是**通用兜底**：当报错不带具体工具上下文时，会给出系统级的排查方向。

## 匹配是怎么工作的

三层打分，纯标准库实现（`errsplain/matcher.py`）：

1. **原文命中**（权重最高）— 词条里存的 `patterns` 是否出现在报错里。所以词库存原文片段，命中率最高。支持 `.*` 通配（如 `Swap file .* already exists`），通配段限长 80 字符以防性能问题。
2. **关键词重合** — 去掉 `error`/`failed` 这类到处都是的停用词后，计算关键词 Jaccard 相似度。
3. **模糊比对** — 对路径、时间戳、数字做噪声剥离后，用 `difflib` 做序列相似度兜底。

性能上做了两处优化：词条的归一化/分词结果用 `lru_cache` 预计算；关键词零重合时跳过最贵的序列比对。**283 条词库下单次匹配约 4ms**。

结果按得分排序，附「相关度：高/中/低」和命中理由，方便你判断靠不靠谱，也方便维护者发现词条质量问题。

## 加自己的词条

不想给项目提 PR、只想记公司内部系统的私有报错？在 `~/.errsplain/entries.json` 放一份同格式文件即可，启动时自动加载，同 id 覆盖内置词条。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 开发

```bash
python -m unittest discover -s tests -v    # 共 130 项：默认 129 项运行，真实剪贴板集成测试按需开启
python -m errsplain --validate             # 词库校验
python -m build                            # 构建源码包和 wheel（开发依赖已包含 build）
```

默认测试不会读写真实剪贴板；Windows 真实剪贴板集成测试需显式设置 `ERRSPLAIN_TEST_REAL_CLIPBOARD=1`，并会保存、恢复原内容。

词库质量有自动化测试守护：每条词条必须能命中自己、原始 JSON 的 id 不重复、字段类型正确、不含有害泛化 pattern、没有完全重复的 pattern 集，词条数只增不减。加词时如果引入了坏 pattern 或重复数据，测试会直接失败。

CI 会在 Linux / Windows / macOS × Python 3.9 / 3.11 / 3.13 上跑测试和词库校验，并独立构建 wheel、在全新虚拟环境安装后检查词库 JSON 与 `py.typed` 是否随包安装。

## 路线图

- [ ] 更多词条（欢迎 PR，这是最重要的一条）
- [ ] 上下文感知：结合当前目录的技术栈调整结果排序
- [ ] `--explain`：展开讲讲这个错误背后的原理
- [ ] 交互式模式：一条没命中时，引导用户选择最接近的报错
- [ ] 本地向量检索（仍然离线），提升模糊匹配能力

## 许可

MIT —— 随便用，随便改。
