# v0.6.79

- “远程文件 → 远程主机”导航栏新增“远程电脑”按钮，与左侧“此电脑”形成对称导航。
- 点击“远程电脑”直接返回远程首页，重新显示桌面、下载、文档、图片、音乐、视频以及 C:/D:/E: 等可用磁盘。
- “上一级”继续保留用于逐层返回；位于盘符根目录时继续返回远程首页。
- 仅调整远程文件导航 UI，不修改 WinRM、文件传输、覆盖、备份和目录合并覆盖逻辑。

# v0.6.78

- 修复远程常用目录名称在部分 Windows PowerShell 5.1 / WinRM 编码环境中显示为 `??` 的问题。
- 远程 PowerShell 仅返回 ASCII 稳定标识（Desktop/Downloads/Documents/Pictures/Music/Videos），中文名称统一在本地 Python/Qt 展示层映射，避免远端代码页污染。
- “远程文件 → 远程主机”和“文件分发 → 选择远程目标目录”共用同一稳定中文显示：桌面、下载、文档、图片、音乐、视频。
- 不修改 WinRM 配置、认证、TrustedHosts、分发覆盖/备份策略。

# v0.6.77

- 修复 Windows PowerShell 5.1 读取远程常用目录时 `Argument types do not match`。
- 常用目录读取失败时自动降级到远程磁盘浏览，不再阻断远程文件和目标目录选择。
- 不修改 WinRM 配置、认证或分发覆盖策略。

## v0.6.75 - 2026-09-15

- “远程文件 → 远程主机”根视图增加当前 WinRM 登录用户的桌面、下载、文档、图片、音乐、视频快捷入口，并保留远程磁盘。
- “文件分发 → 浏览远程目录”对话框同步增加相同远程常用目录入口。
- 常用目录由目标 Windows 实际用户配置读取，不硬编码 C:\Users\ADMS，兼容目录重定向/OneDrive。
- 仅扩展远程目录浏览展示与导航；不修改 WinRM 配置、故障检测、分发、备份和覆盖语义。

## v0.6.74 - 2026-09-15

- 正式文件分发改为“用户选择即发布”：目标文件已存在时强制覆盖，不再因大小/SHA256 相同而跳过。
- 启用“覆盖前备份”时，严格执行“备份旧文件并校验成功 → 覆盖新文件 → 最终大小/SHA256 校验”；备份失败则该文件不覆盖。
- 未启用备份时，已存在文件直接通过 WinRM 安全暂存后强制替换，并执行最终校验。
- 新文件仍按新增处理；目录映射内的同名文件遵循同一规则。
- 未修改 WinRM 连接、远程问题检测、休眠/唤醒检测等逻辑。

## v0.6.73 - 2026-09-15

- 远程文件 → 本机 → “此电脑”首页新增 Windows 常用位置：桌面、下载、文档、图片、音乐、视频。
- 常用位置通过 Qt/Windows 标准路径解析，不硬编码用户目录，兼容 OneDrive/系统重定向。
- 保留现有磁盘列表、上一级、选择文件/文件夹、多选和拖拽上传行为。
- 不修改 WinRM 检测、连接、分发、远程问题诊断等逻辑。

## v0.6.72 - 2026-09-15

- 仅调整“文件分发”页面两个主表格的默认可视高度，不修改 WinRM 检测、连接、分发、凭据、远程文件或其他业务逻辑。
- “分发映射”表格默认完整显示 10 行，超过 10 条映射继续使用表格内部滚动。
- “Windows 目标主机”表格由默认约 8 行调整为完整显示 10 行，超过 10 台继续使用表格内部滚动。
- 本版本暂不修改客户端 WinRM 未连接原因诊断/日志逻辑，按现场测试结果后续单独处理。

## v0.6.71 - 2026-09-12

- 远程文件模块优化本机侧浏览：首次无历史目录时直接显示“此电脑”磁盘列表，新增“此电脑”入口；从 C:/D:/E: 根目录点击“上一级”会返回磁盘列表。
- 本机侧“浏览…”调整为“选择…”菜单，明确支持“选择文件（可多选）”和“选择文件夹”；选中后自动定位到父目录并在列表中选中对应项目。
- 本机文件表继续支持文件与文件夹混合选择、Ctrl/Shift 多选、拖拽上传；双击仅进入文件夹/磁盘，普通文件保持可选但不会误导航。
- 远程文件模块仍为独立人工运维工具，不参与文件分发、备份、Dry Run、Version Checker 等任务流程。

## v0.6.70 - 2026-09-12

- 新增独立“远程文件”模块，与文件分发/备份/Dry Run/Version Checker 完全解耦。
- 远程文件模块一次连接 1 台已保存 Windows 主机，复用现有 WinRM 凭据体系，可浏览远程盘符、目录、文件、大小与修改时间。
- 新增双栏文件管理界面：左侧本机、右侧远程；支持上传、下载、新建远程目录、重命名、删除、路径导航和操作日志。
- 支持把 Windows 资源管理器或左侧本机文件表中的文件/目录直接拖放到右侧远程目录进行上传。
- 远程文件上传/下载继续只通过 WinRM，不依赖 SMB 管理共享；目录传输递归处理。
- 远程删除操作明确二次确认，删除不进入远程回收站。

## v0.6.69 - 2026-09-10

- “Windows 目标主机”表格默认完整显示约 8 台主机，更多主机继续使用表格内部滚动。
- 新增独立“打开远程桌面”按钮：一次打开 1 台主机，调用本机 `mstsc.exe`，并使用该主机当前有效凭据通过 Windows `cmdkey` 写入 `TERMSRV/<IP>` 后启动 RDP；密码不写入日志。
- 本地账号在主机名可用时自动转换为 `目标主机名\用户名`，降低 RDP 将裸用户名解释错误的概率。
- 目标主机操作区重新排版为 4 + 4 + 2，脚本下载按钮最后一行各跨两列，避免出现孤立按钮。
- 远程桌面只是人工便利操作，不参与文件分发、备份、Dry Run、前后置命令、ADMS 部署前检查或 Version Checker。

## v0.6.68 - 2026-09-08

- 简化文件/目录与 SFTP 分发映射的目标目录配置：移除“远程盘符 / 刷新盘符”这一层重复选择，仅保留目标目录输入框与“浏览远程目录”；远程浏览器本身从目标机盘符开始浏览。
- 备份模块明确“目标主机本地备份”语义：字段改为“目标主机备份根目录”，增加常驻说明“备份保存在各目标 Windows 主机本地，不回收到当前电脑”。
- 单独备份说明与执行确认同步明确目标主机本地路径；备份根目录不存在时自动创建，已存在时直接使用。
- 仅优化目标目录选择和备份语义/UI，文件分发、WinRM、备份数据流及其他业务逻辑保持不变。

## v0.6.67 - 2026-09-08

- 优化“ADMS 部署前检查”结果窗口尺寸：改为中等宽度的专用结果对话框，不再被详细信息长行横向撑到接近全屏。
- 检查摘要自动换行，主机详细结果直接显示并按窗口宽度换行，无需额外点击 Show Details；业务检查规则保持不变。

## v0.6.66 - 2026-09-08

- “下载 ADMS WinRM 设置脚本”和“下载 ADMS 还原脚本”增加明确悬停提示：下载后需在目标主机上以管理员身份运行。
- 仅调整按钮提示文案，不改变脚本内容、WinRM、文件分发、备份、ADMS 部署前检查或其他业务逻辑。

## v0.6.65 - 2026-09-08

- 将“检查环境”从 Windows 目标主机快速筛选行移出，改名为“ADMS 部署前检查”，并纳入目标主机操作区。
- 目标主机操作区改为 3 列 × 3 行等宽布局：选择/刷新、在线与 WinRM/ADMS 部署前检查、WinRM 脚本与配置向导分层排列，每行铺满可用宽度。
- 新增常驻说明：ADMS 部署前检查只读验证 WinRM、时间偏差（≤2 分钟）、Private/Public 防火墙必须关闭；Domain 防火墙、时区和 Windows Time 仅作为诊断信息。
- 主机管理页同类按钮统一命名为“ADMS 部署前检查”；弹窗、日志和结论文案同步使用 ADMS 部署前置条件语义。
- 保持 v0.6.64 告警规则：时间偏差 >2 分钟，或 Private/Public 任一未关闭，任一条件不满足即告警；不修改目标主机。

## v0.6.64 - 2026-09-08

- 主机环境检查告警逻辑改为独立 OR 判断：时间绝对偏差超过 2 分钟即告警；Private 或 Public 防火墙任一未关闭也立即告警。
- Private 与 Public 必须同时为“关闭”才满足防火墙要求；Domain 防火墙继续展示但不参与告警判定。
- 环境检查弹窗与执行日志同步更新告警规则文案，避免继续显示旧的 5 分钟 AND 关系。

## v0.6.63 - 2026-09-08

- 主机环境检查弹窗扩大，详细区默认提供更大的可视区域，逐台完整显示时间偏差、时区、Windows Time 以及 Domain/Private/Public 三个防火墙 Profile 状态。
- 环境检查日志同步改为详细格式，不再仅显示“全部/部分开启”的汇总。
- 环境告警阈值改为 5 分钟（300 秒）。总告警严格采用 AND 规则：仅当“时间偏差 > 5 分钟”并且“Private/Public 防火墙未全部关闭”同时成立时报警。Domain Profile 与 W32Time 继续只读展示，但不参与该总告警判定。

## v0.6.62 - 2026-09-08

- 修复“Windows 目标主机 → 检查环境”点击即报错：环境检查错误引用了不存在的 `remote_use_https`，现统一使用既有 `remote_https.isChecked()` WinRM HTTPS 配置。
- 全量检查 `app` 源码，不再存在 `remote_use_https` 旧属性引用；不改变环境检查的时间、时区、W32Time、防火墙只读检查逻辑。
- 保留 v0.6.61 的目标主机快速筛选、已选主机数量显示和所有既有分发/备份/Version Checker 逻辑。

## v0.6.61 - 2026-09-08

- Windows 目标主机快速筛选统计新增“已选 N 台”，并在单台勾选、全选、取消全选后即时刷新。
- “环境检查”按钮调整为更明确的“检查环境”；功能仍为只读检查所选主机时间/时区、Windows Time 服务和防火墙状态，不做任何远程修改。

## v0.6.60 - 2026-09-08

- 修复 v0.6.59 启动回归：恢复 `app/version.py` 中的 `APP_NAME = "File Distribution Studio"`，解决 `ImportError: cannot import name 'APP_NAME' from 'app.version'`。
- 保留 v0.6.59 的首次默认配置与“用户修改后优先使用用户配置”逻辑，不改变文件分发、备份、WinRM、Version Checker 等业务行为。

## v0.6.59 - 2026-09-08

- 为首次启动/首次生成设置文件补齐 ADMS 现场默认配置：命令工作目录默认为 `D:\ADMS\bin`，命令执行方式默认为“登录桌面（INTERACTIVE）”，分发前 CMD 默认为 `sys_ctl stop`，分发后 CMD 默认为 `sys_ctl start fast`。
- Version Checker 首次启动默认配置调整为：程序 `D:\ADMS\bin\version_checker.exe`、工作目录 `D:\ADMS\bin`、CSV 生成目录 `D:\ADMS\bin`、Save 按钮 `Save`、等待超时 `120 秒`。
- 默认值只用于没有用户配置的首次启动或新增配置字段；已保存的用户设置继续优先，程序升级不会覆盖用户已经修改过的值。
- 不改变文件分发、备份、WinRM 远程操作、Version Checker 自动化及主机管理业务流程。

## v0.6.58 - 2026-09-08

- 主机管理新增即时“快速筛选”，可按 IP、主机名、分组、在线/WinRM 状态、备注等任意可见字段模糊搜索，并显示“当前可见 / 总主机数”；筛选后“全选/取消全选”只作用于当前可见主机。
- 文件分发 → Windows 目标主机新增同样的快速筛选，可按 IP、名称、分组、凭据、在线状态、WinRM 状态快速定位；刷新主机后保留筛选条件与原有勾选状态。
- 新增只读“环境检查”：通过 WinRM 并发读取目标机 UTC 时间、时区、Windows Time(W32Time) 服务状态和 Windows Defender Firewall 各 Profile 状态；使用本地请求往返时间中点计算时钟偏差，避免时区差异误判，偏差超过 60 秒明确告警。
- 环境检查只读取系统信息，不会修改时间、时区、时间服务或防火墙配置。

## v0.6.57 - 2026-09-08

- 修复组合任务进入 Version Checker 时进度条提前显示 100%：当本次任务包含版本检查时，文件分发阶段最多推进到 90%，Version Checker 使用最后 10%，只有版本检查 completed 后才显示 100%。
- 单独执行 Version Checker 时进度条会从 0% 重新开始，并按已完成主机数动态推进，任务完成前最多显示 99%。
- Version Checker 进度文本会显示 `Version Checker x/y 台完成`，多机并发时可以直观看到实际完成数量。
- 不改变 Version Checker 的 Save → Information/OK → CSV 回收 → 关闭程序流程，也不改变文件分发、备份和 WinRM 前后操作业务逻辑。

## v0.6.56 - 2026-09-07

- 新增 **Dry Run 预演**：正式分发前可只读检查启用映射的源清单、映射冲突、WinRM 身份验证、目标路径、磁盘空间、备份根目录、CMD 工作目录以及 Version Checker 程序/工作目录/CSV 目录；不会上传、覆盖、备份、结束进程、执行 CMD 或启动 Version Checker。
- Dry Run 对“正式任务会自动创建”的目标目录/备份目录使用警告而非直接失败，并在目标主机任务状态列实时显示“预演通过 / 预演警告 / 预演失败”。
- 新增 **重试失败主机**：上一轮正式分发部分失败后，只重新执行失败主机；如果任务在正式计划生成前整体中止，则整批目标主机都会进入重试集合，避免漏分发。
- 新增 **导出任务结果** Excel：导出最近一次正式分发任务的主机状态、文件动作、源/目标路径、文件大小、源 SHA256、目标 SHA256、文件状态与失败原因；认证/预检查早期失败时仍保留主机级结果。
- 执行日志区域新增 Dry Run / 重试 / 结果导出入口，保持正式分发、备份、WinRM 前后操作和 Version Checker 既有业务流程不变。

## v0.6.55 - 2026-09-07

- 分发映射与“文件分发”模块新增智能联动：从 0 条启用映射变为至少 1 条时，自动将文件分发加入本次任务。
- 当最后 1 条启用映射被取消、删除或全部清空时，自动关闭文件分发模块。
- 尊重人工控制：如果用户明确手工关闭文件分发，后续新增/启用映射不会强制重新开启；用户可随时手工再次开启。
- 仅增加任务开关联动，不改变映射内容、分发顺序、备份、WinRM、Version Checker 或文件传输业务逻辑。

## v0.6.54 - 2026-09-07

- “执行与日志”操作区新增“清空日志”按钮，位于“开始执行所选任务 / 取消”旁边。
- “清空日志”仅清除当前文件分发页的日志显示内容，不删除审计日志、分发历史、任务记录或磁盘日志。
- 其他文件分发、备份、WinRM、Version Checker、主机发现与主机管理业务逻辑保持不变。

## v0.6.53 - 2026-09-07

- Windows 目标主机默认凭据区域恢复为原有横向自适应布局：用户名与密码输入框按可用宽度等比例伸展，不再使用 v0.6.50 引入的固定紧凑宽度。
- 仅调整凭据输入区展示布局；凭据保存、Windows Credential Manager、WinRM、主机管理及其他业务逻辑均不变。

## v0.6.52 - 2026-09-07

- 主机管理页新增实时主机总数显示：`当前共 N 台主机`，新增、删除、发现同步或刷新后自动更新。
- 主机数量与内部 SQLite 自增 ID 完全解耦；ID 继续仅供程序内部使用并保持隐藏。

## v0.6.51 - 2026-09-07

- Windows 目标主机操作按钮改为“先铺满整行，再换行”的 4 列等宽网格；同一行自动拉伸占满可用宽度，避免短按钮集中在左侧留下大块空白。
- 保持 8 个常用操作按钮为 2 行 × 4 列，字体、图标与现有样式不变，仅优化空间利用和视觉秩序。

## v0.6.50 - 2026-09-07

- 主机管理隐藏内部 SQLite 自增 ID；该 ID 仅用于内部关联，不再作为用户业务字段展示或导出。
- 默认 Windows 用户和密码输入框改为紧凑自适应宽度，不再随窗口横向无限拉长。
- 分发前/分发后 CMD 明确采用“每行一条命令、从上到下执行”的输入规则，并在界面常驻显示示例与注释规则。

## v0.6.49 - 2026-09-07

- 优化“文件分发 → Windows 目标主机”操作区：按钮宽度改为按当前字体、文字长度和图标自动适配，不再平均拉伸占满整行。
- 目标主机操作按钮保留两行分组，但全部左对齐紧凑排列；短按钮保持短宽度，长按钮按内容自然扩展。
- “凭据管理”按钮同步采用内容自适应宽度，说明文字使用剩余空间，减少无效留白和模块高度。
- 仅调整界面布局与尺寸策略；主机选择、WinRM、凭据、文件分发、备份、Version Checker 等业务逻辑不变。

## v0.6.48 - 2026-09-07

- 主机发现界面回归简洁设计：仅保留用户可直接编辑的“扫描范围”，不再自动拼接网卡/历史网段到扫描任务。
- “扫描内网”现在严格扫描界面中显示并保存的 CIDR；修复旧版自动加入 `169.254.0.0/16` 导致扫描地址数超过上限、点击扫描快速失败/发现 0 台的问题。
- 首次没有扫描范围时仅生成保守的本机私有 IPv4 默认范围，并过滤 APIPA/link-local；用户后续可直接修改并保存。
- 保留 v0.6.46 的扫描后主机库智能对比、新 Windows 主机询问加入、疑似非 Windows 主机询问移除等逻辑不变。

## v0.6.47 - 2026-09-07

- 备份根目录改为幂等使用：目录不存在时自动创建，已经存在时直接复用；组合备份与单独备份均执行该规则并记录日志。
- 文件分发、备份、程序/服务前后操作、Version Checker 四个模块的卡片背景会随“加入本次任务”开关高亮；未加入时恢复普通白色卡片，模块状态比普通参数复选框更醒目。

## v0.6.46 - 2026-09-07

- 重构“主机发现”为智能发现：移除网卡选择、刷新网卡、添加网卡网段等操作，默认自动合并当前私有 IPv4 可路由网段与历史扫描范围，一键“扫描内网”；仅保留低频“调整范围…”入口处理额外路由网段。
- 扫描完成后自动与主机管理比对：新发现 Windows 主机默认建议加入；已保存 IP 若仍可达但 445/3389/5985/5986 均不再呈现 Windows 服务特征，则主动列为“疑似已不是 Windows”并询问是否移除；仅离线/不可达主机只提醒并保留，避免误删关机主机。
- 新增“智能同步主机库”确认窗口，新增为非破坏性默认勾选，移除为破坏性默认不勾选；所有变更继续写入审计日志。
- 优化发现页布局，扫描范围改为摘要式展示，减少大块输入区和重复按钮，降低现场操作复杂度。

## v0.6.45 - 2026-09-07

- 单独备份不再要求手工输入远端路径；新增 WinRM 远程备份浏览器，可读取参考主机可用盘符，并逐层查看文件夹和文件。
- 远程备份浏览器显示名称、类型、文件大小、修改时间和完整路径；支持跨目录累计选择多个文件/文件夹，并将同一组目标路径应用到所有已选主机。
- 单独备份新增目录递归备份能力；选择目录后在各目标机本机递归复制到备份根目录，目标不存在时逐台跳过并记录日志。
- 组合分发备份逻辑保持不变；单独备份仍与分发映射完全解耦。

## v0.6.44 - 2026-09-07

- 修正确认任务窗口：未启用备份时不再显示“备份目录”，避免误导用户认为仍会执行备份。
- 单独执行备份彻底与分发映射解耦：点击“单独执行备份”后直接输入目标机需要备份的文件完整路径（每行一个），不再要求存在或启用分发映射。
- 单独备份确认窗口明确显示目标主机、备份文件清单和备份根目录；仅备份远端实际存在的文件。

## v0.6.43 - 2026-09-07

- 修复 v0.6.42 启动失败：`main_window.py` 新增自适应网格布局后遗漏导入 `QGridLayout`，导致启动时报 `NameError: QGridLayout is not defined`。
- 本版本仅修复该启动级遗漏，不改文件分发、备份、WinRM 前后操作、Version Checker、CSV 回收和主机 Excel 导出业务逻辑。

## v0.6.42 - 2026-09-07

- 修复“关闭主页面横向滚动条后右侧模块仍被裁切”的布局问题：根因是 Windows 目标主机操作按钮和凭据管理动作全部挤在单行，导致页面最小宽度大于滚动视口。
- Windows 目标主机操作区改为 4 列 × 2 行自适应网格；按钮随可用宽度伸缩，不再把整个文件分发页面横向撑宽。
- 凭据管理操作区改为紧凑网格，说明文字单独换行并支持自动折行，进一步降低高 DPI / 较窄窗口下的页面最小宽度。
- 保持正常窗口启动，不强制最大化；主页面仍只保留纵向滚动，底部不出现整页横向滚动条。文件分发、备份、WinRM、Version Checker 和主机 Excel 导出业务逻辑均未改变。

## v0.6.41 - 2026-09-07

- 主机管理新增“导出 Excel”：一次导出全部已保存主机信息为 `.xlsx`，字段与主机管理表一致（不导出密码，仅导出凭据状态）。
- Excel 首行采用表头样式并冻结，启用自动筛选；列宽按内容做受限自适应，便于现场直接查看、筛选和留档。
- 默认文件名包含导出时间，默认保存到当前用户 Downloads；导出结果写入审计日志。

## v0.6.40 - 2026-09-07

- 主窗口恢复正常窗口启动，不再强制最大化。
- 主页面滚动区禁用整页横向滚动条，仅保留纵向滚动；内容随窗口可用宽度自适应，避免底部长期出现横向滑动条。
- 保持 v0.6.39 的主机表格自适应、选择框居中，以及 Version Checker 回收文件“主机名 + IP”命名逻辑不变。

## v0.6.39 - 2026-09-07

- 主窗口启动时按当前显示器可用区域自动最大化，提升不同分辨率 / DPI 下的首屏自适应。
- “主机管理”表格改为按可用宽度自动伸展，默认优先完整显示所有列，减少启动后横向滚动。
- 主机管理、分发目标、映射和发现列表中的“选择/启用”复选框统一居中显示。
- Version Checker 回收结果文件名同时包含主机名和 IP，例如 `JED-PR-Dispatcher-13_172.16.21.113_version_checker_result.csv`，多机结果更易核对且避免同名覆盖。

## v0.6.38 - 2026-09-07

- Version Checker CSV 回收改为直接写入用户选择的“本机结果目录”根目录，不再隐藏到 `VERCHK-任务ID\主机名\` 两层子目录。
- 多主机结果自动使用 `<主机名>_version_checker_result.csv` 命名，避免相互覆盖且便于现场直接查找。
- WinRM 分块回收完成后新增本机落盘确认与文件大小校验；远端/本机大小不一致会明确判定任务失败。
- 完成提示中的“结果目录”现在直接指向用户选择的本机结果目录。

## v0.6.37 - 2026-09-07

- 修复 v0.6.36 启动即报 `NameError: name 'background' is not defined`：`APP_QSS` 为 Python f-string，新增 `TaskToggle` QSS 规则中的 CSS 花括号现已正确转义为 `{{` / `}}`。
- 仅修复模块级任务 Toggle 的样式字符串解析问题；文件分发、备份、WinRM 前后操作、Version Checker 及其业务逻辑保持不变。

## v0.6.36 - 2026-09-07

- UI 语义优化：文件分发、备份、程序/服务前后操作、Version Checker 四个“是否加入本次任务”的模块级开关改为独立 Toggle 按钮，不再与 SHA256、预检查、失败恢复等普通参数复选框混用。
- 模块级 Toggle 明确显示“● 已加入本次任务 / ○ 未加入本次任务”，启用时使用独立绿色状态样式；普通 QCheckBox 继续只表达模块内部选项。
- 保持四个模块原有执行逻辑、独立执行按钮、任务链顺序和 Version Checker 自动化逻辑不变。

## v0.6.35 - 2026-09-07

- 修复 Version Checker 已成功打开但无法点击 Save：不再只依赖 `Process.MainWindowHandle`，改为枚举当前交互桌面可见顶层窗口并按 `version_checker` 标题与窗口面积锁定真实主窗口。
- Save 自动化改为“点击即验证”：依次尝试 UI Automation、Win32 子控件文本、窗口相对坐标；每种方式后立即检测 `Information` 对话框，未出现则自动继续下一种方式。
- `Information` 对话框改为按当前桌面可见窗口标题查找，避免其窗口句柄/进程归属与主进程不一致时漏检。
- 保持既定流程：Save → OK → `version_checker_result.csv` 稳定写入 → 可选回收 CSV → 可选关闭 Version Checker。

## v0.6.34 - 2026-09-07

- 修复 Version Checker 自动化脚本部署路径中的 Python `\v` 转义问题：`FileDistributionStudio\version_checker` 之前被解释成垂直制表符 `0x0B`，导致目标机 PowerShell 报 `Illegal characters in path`。
- 部署命令改为 raw f-string，确保远端目录固定为 `C:\ProgramData\FileDistributionStudio\version_checker`。
- Version Checker 后续 Save → Information/OK → CSV 回收/关闭流程保持不变。

## v0.6.33 - 2026-09-07

- Version Checker 自动化按现场实际流程固定为：启动程序 → Save → Information/OK → 等待 `version_checker_result.csv` 写入稳定 → 可选回收到本机 → 可选关闭程序。
- Save 控件识别增强为多级策略：UI Automation → Win32 子控件文本 → 主窗口相对位置点击兜底，兼容当前 Version Checker 无法通过标准 UIA Name=Save 定位的情况。
- Information 对话框的 OK 同样支持 UIA / Win32 文本匹配，并以 Enter 作为最终兜底。
- Version Checker 输出类型由错误的 Excel 假设修正为 CSV；界面、日志和回收文件名统一使用 `version_checker_result.csv`。
- 保留 v0.6.31 的 PowerShell 5.1 UTF-8 BOM 兼容处理以及 v0.6.32 的任务组件解耦。

## v0.6.32 - 2026-09-07

- 文件分发、备份、WinRM 程序/服务前后操作、Version Checker 改为独立任务组件；未勾选的模块不参与本次执行。
- 移除“仅文件分发”快捷模式，改为显式“启用文件分发”；只勾选文件分发即为纯分发，避免隐藏联动。
- 备份从分发策略中解耦为独立卡片：可勾选“分发前备份”参与组合任务，也可使用“单独执行备份”。单独备份按当前启用映射生成目标文件清单，只备份远端已存在的对应文件。
- WinRM 前/后操作模块顶部开关现在真正控制整块配置；关闭后配置区变灰且不会参与执行。
- Version Checker 改为“加入本次任务”语义，默认不因分发成功自动执行；仍保留“单独执行版本检查”。
- 底部主按钮改为“开始执行所选任务”，并实时显示本次执行流程：备份 → 前置操作 → 文件分发 → 后置操作 → Version Checker。
- 保留 v0.6.31 的 Windows PowerShell 5.1 UTF-8 BOM 兼容修复。

## v0.6.31 - 2026-09-07

- 修复 Version Checker 交互任务在 Windows PowerShell 5.1 下解析临时 `run_*.ps1` 失败的问题。根因是 v0.6.30 将包含中文提示的脚本写成 UTF-8 无 BOM，PowerShell 5.1 可能按本地 ANSI 代码页读取，造成中文字符串乱码并破坏 PowerShell 语法。
- Version Checker 临时 PowerShell 脚本现在强制写入 UTF-8 BOM，确保中文字符串在中文/英文 Windows 环境中均按 UTF-8 正确解析。
- 同时整理 Version Checker UI Automation 脚本中的 PowerShell 运算符和条件表达式间距，增强 Windows PowerShell 5.1 兼容性；WinRM、交互桌面计划任务、Save 控件查找、Excel 检测和回收业务流程保持不变。

## v0.6.30 - 2026-09-07

- “分发策略”新增 **仅文件分发** 快捷模式：启用后自动关闭并禁用“覆盖前备份”和“分发前 / 后操作”，SHA256、分发前预检查、失败重试、主机并发数保持可用。
- 版本检查与普通分发完全解耦：新增 **版本检查（独立可选）** 区域；默认不执行，可选择“分发完成后执行 Version Checker”，也可点击“单独执行版本检查”对当前勾选主机直接并发执行。
- 新增 Version Checker GUI 自动化：通过 WinRM 调度 Windows 交互式计划任务，在目标机当前登录桌面启动 `version_checker.exe`，使用 Windows UI Automation 按按钮名称查找并调用 `Save`，不依赖固定鼠标坐标。
- 新增 Excel 结果检测与回收：监视指定目标目录中新建/更新的 `.xlsx/.xls`，文件稳定后通过 WinRM 分块下载到本机，并按任务 ID / 主机分别归档。
- Version Checker 支持配置程序路径、工作目录、Excel 生成目录、Save 按钮名称、超时、本机结果目录、是否回收 Excel、是否完成后关闭程序。
- “分发完成后版本检查”仅作用于本次文件分发成功的主机；未勾选时分发完成即结束，不会自动运行版本检查。

## v0.6.29 - 2026-09-07

- 修复 `TARGET_PREP_ADMS_WINRM.cmd` 中 WinRM 初始化后脚本不继续执行的问题：`winrm` 在 Windows 中由 `winrm.cmd` 提供，批处理内改为 `call winrm quickconfig -quiet`，确保控制流返回并继续执行后续 `sc` 和 `reg add`。
- WinRM 设置脚本增加分步状态输出，并对 WinRM 服务运行状态和 `LocalAccountTokenFilterPolicy=1` 做最终验证。
- WinRM 恢复脚本增加分步状态输出；注册表值不存在时视为无需处理，停止 WinRM 后验证服务状态。
- 两个脚本末尾统一显示 `SUCCESS` / `FAILED` 总结，并使用 `pause` 保持窗口不退出。
- 不新增账号、用户组或 RDP 权限修改逻辑。

## v0.6.28 - 2026-09-07

- 简化“使用帮助 → ADMS 账号与管理员组”命令：Administrators 查询/加入/复查均直接使用内置 SID `S-1-5-32-544`，不再定义 `$g` 变量。
- `TARGET_PREP_ADMS_WINRM.cmd` 保持原有极简业务动作不变；`LocalAccountTokenFilterPolicy=1` 使用单行 `reg add ... /d 1 /f` 执行，避免 CMD 多行续行复制带来的歧义。
- 设置脚本末尾仅增加完成提示和 `pause`，不新增检查、账号、用户组、RDP 或其他修改逻辑。

## v0.6.27-zh-CN - 2026-09-07

- 简化“使用帮助 → ADMS 账号与管理员组（手工）”模块。
- 移除 Remote Desktop Users（SID `S-1-5-32-555`）查询和加入命令，避免把 RDP 组权限与 WinRM 管理权限混在一起。
- 帮助区只保留 3 个步骤：查看 ADMS、查看本地 Administrators、必要时手工加入 Administrators。
- 明确说明：ADMS 已属于 Administrators 时，通常无需再加入 Remote Desktop Users；账号/用户组命令仍只用于手工操作，一键脚本不会执行。
- 缩短帮助文本框高度，减少页面占用。
- 正式文件分发、WinRM、Remote UAC、主机管理、审计及其他业务逻辑均未修改。

## v0.6.26-zh-CN - 2026-09-07

- 修正 `TARGET_PREP_ADMS_WINRM.cmd` 中 `LocalAccountTokenFilterPolicy` 的值：正式 ADMS 本地管理员 WinRM 模式应设置为 `1`，不是 `0`。
- ADMS 设置脚本继续保持极简：仅 `winrm quickconfig -quiet`、WinRM 自动启动/启动服务、`LocalAccountTokenFilterPolicy=1`；不读取、不创建、不启用/禁用、不加组、不移组，也不修改 RDP。
- ADMS 还原脚本保持不变：仅删除 `LocalAccountTokenFilterPolicy` 并停止 WinRM。
- 主界面/配置向导/当前文档同步修正为 `LocalAccountTokenFilterPolicy=1`，避免与帮助中的正确管理员令牌说明冲突。
- 保留 v0.6.25 的多目标主机远程目录浏览/路径范围提示与其他业务逻辑，不做额外业务改动。

## v0.6.24

- “使用帮助”新增 **ADMS 账号检查与管理员组（手工操作）**，包含：`net user ADMS`、按 SID 查看本地 Administrators、手工将 ADMS 加入 Administrators、查看/可选加入 Remote Desktop Users。
- 明确区分“帮助中的手工账号命令”和“一键 WinRM 脚本”：应用脚本不会执行任何账号、用户组或 RDP 变更。
- `TARGET_PREP_ADMS_WINRM.cmd` 简化为仅执行 WinRM 初始化/启动和 `LocalAccountTokenFilterPolicy=0`。
- `TARGET_RESTORE_ADMS_WINRM.cmd` 简化为仅删除 `LocalAccountTokenFilterPolicy` 并停止 WinRM。
- 其余文件分发、WinRM 远程操作、凭据、选择记忆、进度、审计等业务逻辑不变。

# v0.6.23

- 按现场要求极简化 ADMS WinRM 设置/还原脚本；不再读取、检查、启用、禁用或修改 ADMS 账号，也不修改任何用户组、RDP 权限或其他账号策略。
- `TARGET_PREP_ADMS_WINRM.cmd` 现在只执行 WinRM 启用/启动，并显式设置 `LocalAccountTokenFilterPolicy=0`。
- `TARGET_RESTORE_ADMS_WINRM.cmd` 现在只删除 `LocalAccountTokenFilterPolicy` 并停止 WinRM；不会修改 WinRM 启动类型，也不会修改任何 ADMS 相关状态。
- 主程序其他业务逻辑保持 v0.6.22 不变。

# v0.6.22-zh-CN

- 修复 ADMS 初始化脚本可能间接改变 RDP 授权行为的风险：新版 `TARGET_PREP_ADMS_WINRM.cmd` 不再自动启用/禁用 ADMS，也不再自动把 ADMS 加入本地 Administrators；只验证 ADMS 已启用且已是本地管理员，然后配置 WinRM HTTP 5985 与 `LocalAccountTokenFilterPolicy=1`。
- 准备脚本明确不修改 Remote Desktop Users 或任何 RDP 登录策略。
- 新版 `TARGET_RESTORE_ADMS_WINRM.cmd` 继续删除 `LocalAccountTokenFilterPolicy` 恢复 Windows 默认 Remote UAC，并兼容读取旧版 `ADMS_WinRM.before.txt`：若旧脚本曾改变 ADMS 启用状态或 Administrators 成员关系，可按旧快照恢复。
- 使用 v2 状态文件 `ADMS_WinRM.before.v2.txt` 记录新版 Remote UAC 基线，避免复用陈旧旧版快照。
- 其余 WinRM-only 分发、进程/CMD、凭据、选择记忆、进度与审计逻辑保持不变。

# v0.6.21-zh-CN

- 简化“Windows 目标主机”和“WinRM 连接与远程操作”的用户说明：主界面只保留必要信息，默认显示 `WinRM：HTTP 5985`；HTTPS/自定义端口移到“高级连接…”中。
- “Windows 目标主机”区域新增“下载 ADMS 设置脚本”和“下载 ADMS 还原脚本”，无需先进入配置向导即可直接保存两个 CMD。
- 修复 `TARGET_RESTORE_ADMS_WINRM.cmd`：还原脚本现在会恢复记录的 ADMS 启用/管理员成员状态，并明确把 `LocalAccountTokenFilterPolicy` 删除，恢复 Windows 默认 Remote UAC 行为；随后再次查询验证该值确实不存在，否则报错。
- 还原成功后删除旧的 `ADMS_WinRM.before.txt` 状态文件，避免下一次重新准备时继续复用陈旧备份。WinRM 服务保持启用，仅重启一次以应用新会话策略。
- 配置向导文字同步精简；设置脚本仍不会创建 ADMS 或修改密码。
- 其余 v0.6.20 的 WinRM-only 文件分发、目标主机选择记忆、进程/CMD、凭据、进度、审计和 Git 行为不变。

# v0.6.20-zh-CN

- Windows 目标主机复选框现在会跨程序启动记住上一次选择。第一次初始化且已有主机时默认全部勾选；此后重新打开程序按主机/IP精确恢复上次的勾选/取消状态。
- 首次初始化时如果主机列表为空，不会提前消费“默认全选”；首次发现/添加主机后仍默认全部勾选。
- 已经存在选择历史后，新发现或新增的主机默认不勾选，避免用户未确认的新机器自动进入正式分发范围。
- “全选 / 取消全选”和单机勾选都会立即保存选择状态；普通刷新、在线测试、WinRM 测试、主机名验证等刷新不会改变选择。
- 选择记录只保存主机/IP与布尔勾选状态到 `settings.json`，不涉及密码或其他敏感凭据。
- 其余 WinRM-only 分发、进程/CMD、凭据、审计、进度与 ADMS 一键准备逻辑保持 v0.6.19 不变。

# v0.6.19-zh-CN

- 修正分发总进度语义：文件全部上传/校验完成时不再提前显示 100%；文件阶段最多推进到 90%，每台主机完成分发后 CMD/收尾后最多到 99%，只有整个任务 completed 信号到达、审计收尾完成时才显示 100% 并弹出最终结果。失败/取消也只在工作流真正结束时显示 100%，同时在进度文字中标明最终状态。
- WinRM 配置向导新增默认“ADMS 一键准备（推荐）”模式，并新增 `TARGET_PREP_ADMS_WINRM.cmd`：只针对目标机**已存在**的本地 ADMS 账号，不创建账号、不修改密码；会记录原状态，必要时启用 ADMS、加入本地 Administrators、执行 `winrm quickconfig`、设置 WinRM 自动启动/启动服务、设置 `LocalAccountTokenFilterPolicy=1`，最后检查 Listener/5985。
- 新增 `TARGET_RESTORE_ADMS_WINRM.cmd`：按首次准备前记录的状态恢复 ADMS 是否启用、是否属于 Administrators 以及 `LocalAccountTokenFilterPolicy`；WinRM 服务保持启用，避免误中断正在使用的远程管理通道。
- 配置向导导出 `.cmd` 时改为直接复制经过验证的资源字节，确保仍为 ASCII + CRLF + 无 BOM，不再由 GUI 重新写成带 BOM 的 UTF-8。
- 启动 Splash 不再显示个人作者信息；保留 NARI、国际业务部 / International Business Division 和动态版本号。
- 不加入此前暂缓的“远程 GUI 自动点击/结果文件回收”需求，正式目标侧业务仍为 WinRM-only。

# v0.6.18-zh-CN

- 修复 Windows `cmd.exe` 执行 `setup.bat` 时出现大量“不是内部或外部命令”的问题：根因是仓库中的 `.bat/.cmd` 使用 LF 行尾并包含 UTF-8 中文文本，Windows 批处理解析在部分环境下会把字节/行错误拆成命令。
- 所有 `.bat/.cmd` 入口脚本改为纯 ASCII 控制文本并强制 CRLF、无 BOM，避免系统代码页影响批处理语法。应用 GUI 和文档仍保持简体中文。
- `setup.bat` 继续支持 `--ensure / --verify / --recreate`，并改进 Python Launcher 查找，优先明确尝试 3.14→3.10 的 64 位解释器。
- PowerShell `.ps1` 脚本统一为 CRLF + UTF-8 BOM，兼容 Windows PowerShell 5.1 的 Unicode 读取。
- 新增 `.gitattributes` 与 `.editorconfig`，确保 Git clone/checkout 后 Windows 脚本仍为 CRLF，不会因开发机或 Git `core.autocrlf` 再次破坏。
- 不修改 v0.6.17 的 WinRM 文件分发、远程命令、进程、凭据、审计等业务行为。

# CHANGELOG


## v0.6.17-zh-CN - 2026-09-07

- Git 仓库整理版本：新增根目录 `.gitignore`。
- 默认忽略 `.venv`、Python 缓存、IDE 本地配置、PyInstaller `build/dist/release` 输出、EXE/ZIP 等生成物。
- 默认忽略运行时数据库、`settings.json`、日志、审计 JSONL、缓存/暂存目录和 `.fds_tmp`。
- 默认忽略 `.env`、证书/私钥、凭据/secret 文件和真实主机清单；仓库继续保留 `sample_hosts.csv`。
- 不包含“远程 GUI 自动点击/结果文件回收”需求；该需求暂不进入当前代码。
- 不改变 v0.6.16 的 WinRM 分发、交互桌面命令、进程选择、凭据、备份、校验和审计业务逻辑。

## v0.6.16-zh-CN - 2026-09-07

- 修复“登录桌面”模式错误判断无人登录：新增基于 explorer.exe 所有者 + SessionId 的交互桌面识别，并优先匹配当前 WinRM 用户；保留 Win32_ComputerSystem.UserName 兼容回退。
- 交互式任务编排统一返回 JSON 错误，抑制 PowerShell Progress/CLIXML 噪音，错误提示更直接。
- 远程操作固定顺序改为：预检查 → 结束目标进程 → 分发前 CMD → 文件分发 → 分发后 CMD。
- 长页面 QScrollArea 垂直滚轮步长提升至 64，提高右侧页面滚动速度；输入控件滚轮保护规则不变。
- 当远程操作已经开始而后续失败时，若启用“失败恢复”，仍会尝试执行分发后 CMD。

# Changelog

## v0.6.15-zh-CN - 2026-09-06

- 修复“登录桌面”交互式 CMD 在任务开始阶段报 `The command line is too long`。根因是 v0.6.14 将完整的任务计划编排 PowerShell 脚本交给 `pywinrm.Session.run_ps()`，脚本会被整体编码到 PowerShell `-EncodedCommand`，可能超过 Windows 远程命令行长度限制。
- 新增 WinRM/WinRS stdin 脚本流执行器：远端命令行只包含固定的小型 PowerShell bootstrap，真正的编排脚本以 UTF-8 数据通过 WSMan stdin 分块发送，不再进入命令行。
- “登录桌面”仍使用 WinRM 作为唯一控制通道，并继续通过临时 Scheduled Task 使用当前已登录桌面用户的 Interactive token 执行 `sys_ctl stop` / `sys_ctl start fast` 等命令。
- 不改变文件 WinRM 二进制流上传、进程结束、目录/备份/SHA256、凭据和审计策略。

## v0.6.14-zh-CN - 2026-09-06

- 记住上次远程操作设置：分发前 CMD、分发后 CMD、结束进程镜像名、命令工作目录、失败恢复开关、启用状态与命令执行方式会保存到本地设置；密码仍只按原有 Windows 凭据管理器策略保存。
- 新增“命令执行方式”：`登录桌面（推荐，最接近本机执行）` / `WinRM 后台（无界面）`。
- 登录桌面模式仍以 WinRM 为唯一控制通道，但通过目标 Windows 内置任务计划程序，以当前已登录桌面用户的 `Interactive` 令牌临时执行命令；不需要安装 Agent，也不会把密码写入任务或命令行。
- 解决 `sys_ctl start fast`、GUI/厂商 launcher 在 WinRM 非交互会话中与本机执行结果不同的问题；同时保留 `命令工作目录`。
- 交互任务执行完后会自动删除临时任务和 `%ProgramData%\FileDistributionStudio\interactive` 下的临时脚本/输出文件；目标机无人登录时明确报错，不静默退回。

# 更新日志

## v0.6.13-zh-CN - 2026-09-06

- 远程操作流程进一步简化：移除“停止服务”输入和“分发完成后重新启动服务”选项，不再要求用户提供 Windows Service 名称。
- 正式执行顺序调整为：`WinRM 认证/目标预检查 → ① 分发前 CMD → ② 结束目标进程 → ③ 文件分发 → ④ 分发后 CMD`。
- “② 结束目标进程”继续支持 WinRM 远程进程选择器，最终只按用户选中的真实 EXE 镜像名执行 `taskkill /F /T /IM <image.exe>`。
- 失败恢复语义收敛为“文件分发失败时仍尝试执行分发后 CMD”，不再包含服务启动恢复。
- 后端正式分发工作流已移除停止/启动服务步骤与对应计划字段，避免 UI 与真实行为不一致。
- 其余 WinRM-only 文件分发、远程目录浏览、远程盘符、分层凭据、Windows 凭据管理器、二进制流上传、CMD 工作目录、备份/SHA256/审计、品牌与启动页保持不变。

## v0.6.12-zh-CN - 2026-09-06

- 新增 WinRM 远程进程选择器：显示友好描述、镜像名、实例数、PID、可执行路径并支持搜索。
- 进程查询为只读 WinRM 操作；正式结束仍严格发生在固定流水线第③步。
- 选择语义与后端一致：按镜像名聚合，避免用户误以为只会结束某一个 PID。
- 多台目标主机时明确选择一个参考主机读取列表；最终同一组镜像名应用到所有勾选主机。
- 已配置但当前未运行的进程镜像名会继续保留在选择器中。

## v0.6.11-zh-CN - 2026-09-06

- 启动 Splash 重新设计：在首次打开 App 的启动页面中清晰展示 `NARI`、`国际业务部 / International Business Division`。
- 启动页新增作者信息：`作者：罗戈石布`。
- 清理旧 Splash 中遗留的硬编码旧版本 `v0.4.2`，版本号改为运行时动态绘制，后续升级不会再出现新旧版本叠字。
- 启动页技术标识调整为 `本地 · SFTP 源 · WinRM · 审计`，与当前正式业务全部通过 WinRM 的架构保持一致。
- 仍使用文字式 NARI / 国际业务部标识；未捆绑未经提供的官方图形 Logo 资产。后续如提供官方 PNG/SVG，可直接替换。
- 其余 v0.6.10 的 WinRM-only 分发、远程目录浏览、分层凭据、CMD 工作目录、执行流水线、备份/SHA256/审计等行为不变。

## v0.6.10-zh-CN - 2026-09-06

- 品牌展示：在主界面侧栏与顶部加入 `NARI`、`国际业务部 / International Business Division` 品牌标识；启动 Splash 同步显示 `NARI · 国际业务部`。未引入外部官方图形文件，避免把非官方图形误作正式 Logo；如后续提供官方 PNG/SVG，可直接替换为正式资产。
- 主机管理职责收敛：`WinRM 凭据`列改为纯状态展示，主机管理表禁止直接单元格编辑；账号/密码仍统一从“文件分发 → Windows 目标主机 → 凭据管理”配置。删除旧的“连接模式”展示列，业务通道固定为 WinRM。
- 远程操作界面重排为明确流水线，并与真实执行顺序一致：`WinRM 认证/目标预检查 → ① 分发前 CMD → ② 停止服务 → ③ 结束残留进程 → ④ 文件分发 → ⑤ 启动服务 → ⑥ 分发后 CMD`。
- `CMD 工作目录`放在命令流水线顶部，继续适配 `sys_ctl start fast` 等依赖当前目录的厂商启动器。
- 分发确认框补充完整执行顺序，避免用户误解“停止服务/结束进程/分发前 CMD”的先后关系。
- 保持 v0.6.9 WinRM 远程目录浏览器、v0.6.8 CMD 工作目录、v0.6.7 远程盘符、v0.6.5 分层凭据与 Windows 凭据管理器、WinRM-only 正式业务、二进制流上传、备份/SHA256/审计逻辑不变。


## v0.6.9-zh-CN - 2026-09-06

- 分发映射目标目录新增“浏览远程目录”，在 File Distribution Studio 内部通过 WinRM 只读浏览远程 Windows 盘符和目录树。
- 目录树采用懒加载：展开某个盘符/目录时只读取下一层子目录，不递归扫描整盘，降低大目录和多盘环境的延迟。
- 多主机场景支持选择“参考主机”进行浏览；选中的真实 Windows 路径仍会在正式分发前对所有勾选主机逐台执行写入与空间预检查。
- 浏览器显示盘符卷标和容量，点击目录即可回填 `D:\ADMS\bin` 等目标路径；也保留手工路径输入。
- 远程目录读取使用每台主机的有效 WinRM 凭据，只读、不创建目录、不使用 SMB/C$/D$/E$。
- 无权限/不存在目录只在浏览器中提示读取失败，不会修改远端。
- 保留 v0.6.8 的 CMD 工作目录能力以及此前 WinRM-only 文件分发、流式上传、凭据管理、盘符读取和审计能力。

## v0.6.8-zh-CN - 2026-09-06

- 新增“CMD 工作目录”，用于 WinRM 分发前/后自定义命令。
- `sys_ctl start fast` 等依赖当前目录的厂商启动器可设置例如 `D:\ADMS\bin`，程序会执行等价于 `cd /d "D:\ADMS\bin" && sys_ctl start fast` 的命令。
- 工作目录会保存在本地 `settings.json`，不包含密码；审计日志记录工作目录和命令，便于追溯。
- 使用帮助新增 WinRM 非交互式会话说明：GUI/桌面程序与 Windows 服务/后台控制程序的执行环境不同。
- 文件分发、目录、备份、校验、服务、进程仍全部使用 WinRM。

# Changelog

## v0.6.7-zh-CN - 2026-09-06

- 分发映射目标目录新增“远程盘符”自动读取与选择。
- 盘符、卷标、总容量和剩余空间全部通过 WinRM + `.NET DriveInfo` 读取，不依赖 SMB 管理共享。
- 单主机直接列出该机可用盘符；多主机显示每个盘符的覆盖数量，`✓` 表示所有勾选主机均存在，`⚠` 表示仅部分主机存在。
- 选择远程盘符会自动更新真实 Windows 目标路径前缀，同时保留已有子目录。
- 本地文件、目录以及 SFTP 源映射都支持相同的远程盘符选择能力。
- 读取盘符时按每台主机解析“默认凭据/自定义凭据”，密码仍不进入日志或配置文件。

## v0.6.6-zh-CN - 2026-09-06

- 主机管理表新增“选择”复选框列，并新增“全选 / 取消全选”；批量在线测试、主机名验证、删除等操作优先使用勾选主机，同时继续兼容旧的高亮行操作。刷新状态时保留主机管理复选框状态。
- 新增侧栏“使用帮助”页面，内置 WinRM quickconfig、服务查询/启停、Listener、5985、`winrm id`、启动类型，以及 `LocalAccountTokenFilterPolicy` 查询/开启/设 0/删除/再次确认的完整 CMD 命令和风险说明。
- 帮助页提供“复制 WinRM 命令”“复制 Remote UAC 命令”和“打开 WinRM 配置向导”。
- 解释并修复 `.fds_tmp` 残留：它是 WinRM 安全暂存目录，用于临时上传、大小/SHA256 校验和正式提交，避免直接覆盖损坏正式文件。
- 任务结束后现在无论成功或失败都会尽力清理映射级临时目录；随后仅在为空时删除任务级目录和 `.fds_tmp` 根目录，避免影响并发任务。若断网或强制退出导致无法清理，会写入审计提示。
- 正式目标侧业务仍为 WinRM-only；SMB/Ping/RDP/DNS/NetBIOS 继续只用于发现、识别和辅助诊断。

## v0.6.5-zh-CN - 2026-09-06

- 新增“默认 WinRM 凭据 + 每主机自定义凭据”模型：大多数主机直接使用顶部默认账号/密码，少数特殊主机可以单独覆盖。
- 目标主机表新增“凭据”列，显示“默认凭据 / 自定义凭据 / 缺少密码 / 未设置”，绝不显示密码。
- 新增“设置选中凭据”“批量设置凭据”“恢复默认凭据”。Ctrl/Shift 可高亮多行；批量按钮可直接对当前勾选的分发目标应用同一套自定义凭据。
- “测试 WinRM”、正式分发前认证、目标目录预检查、文件上传、备份、SHA256、服务/进程操作和前后命令全部使用每台主机解析后的同一套有效凭据，避免测试与执行凭据不一致。
- 默认账号/密码现在可以跨启动记住。用户名保存到 settings.json；密码仅写入当前 Windows 用户的 Windows 凭据管理器（Generic Credential），不进入 settings.json、SQLite、JSONL、审计日志或命令行。
- 自定义主机凭据也支持“安全记住”；主机自定义用户名保存在设置中，密码仍只进入 Windows 凭据管理器。若不勾选安全记住，密码仅保留到本次程序退出。
- 删除主机或恢复默认凭据时，会同步清理该主机在 Windows 凭据管理器中的 FDS 自定义凭据。
- 保留 v0.6.4 WinRM/WinRS stdin 二进制流上传及 WinRM-only 正式业务通道。

## v0.6.4-zh-CN - 2026-09-06

- 修复 WinRM 文件上传报 `The command line is too long`：旧实现把每个文件块先 Base64，再嵌入 PowerShell `run_ps` 命令，pywinrm 还会再次将整个脚本编码为 `-EncodedCommand`，因此几 KiB 文件也可能触发 Windows 命令行长度上限。
- 文件上传改为 **WinRM/WinRS stdin 二进制流**：远端只启动一个小型 PowerShell 接收器，文件正文通过 WSMan 标准输入流发送，不再进入命令行。
- 默认流式块大小 64 KiB，并限制在 16–64 KiB 的保守范围，避免 WSMan Envelope 过大。
- 上传结束必须收到远端 `FDS_UPLOAD_OK` 完成标记；异常时仍清理临时文件、WinRM command 和 shell。
- 正式业务仍保持 WinRM-only，不恢复 SMB/C$/D$/E$ 分发。

## v0.6.3-zh-CN - 2026-09-06

## v0.6.2-zh-CN - 2026-09-06
- 修复 WinRM 本地账号认证方式：简单用户名（如 `ADMS`）现在按输入原样发送，不再自动拼接主机发现得到的 Computer Name。现场已验证 `Get-Credential -UserName "ADMS" + Invoke-Command -Authentication Negotiate` 可成功，因此应用行为与该方式对齐。
- 避免长 Windows 主机名 / DNS 名称与 NetBIOS 本地账号 authority 不一致时，把有效的 `ADMS` 错误改写成不可认证的 `COMPUTER\ADMS`。
- “测试 WinRM”现在同时执行 `hostname`、`whoami` 和目录写入探针；成功消息会显示远端实际主机和登录身份。
- 新增“WinRM 配置向导”：可从应用内保存/复制目标机准备脚本。
- 新增 `TARGET_PREP_WINRM_LOCAL_ADMIN.cmd`：在用户明确确认后，为 ADMS 等本地管理员设置 `LocalAccountTokenFilterPolicy=1`；执行前保存原始状态。
- 新增 `TARGET_RESTORE_WINRM_LOCAL_ADMIN.cmd`：按保存的原始状态恢复该策略。
- `TARGET_PREP_WINRM.cmd` 继续只执行 `winrm quickconfig`、WinRM 服务、Listener、5985、`winrm id` 等标准准备，不修改注册表/UAC。
- PyInstaller 包现在包含 `resources/scripts`，发布后的 EXE 同样可从配置向导导出脚本。
- 正式业务写入路径进一步收紧为 WinRM-only：移除未使用的 SMB 写入兼容模块及管理员共享路径转换帮助函数。SMB/445 仅保留在主机发现、Windows 身份识别和辅助诊断。
- 主机编辑中的目标连接模式固定显示 WinRM，避免普通用户误选已不参与正式业务的旧 SMB/UNC 模式。

## v0.6.1-zh-CN
- 明确业务传输边界：文件上传、目录创建、备份、SHA256、停/启服务、杀进程、前后 CMD/PowerShell 全部只使用 WinRM。
- Ping、TCP 445/3389、DNS、NetBIOS、SMB/NTLM/WKSSVC 仅用于主机发现、Windows 身份识别和辅助诊断，不参与正式分发。
- 正式任务新增 WinRM 前置认证闸门：所有勾选主机必须先通过 WinRM 身份验证，才开始读取 SFTP/本地源、生成清单和 SHA256；避免认证已失败仍继续准备任务。
- WinRM 401/credentials rejected 中文诊断不再简单等同于“密码错误”，会明确提示本地非内置管理员可能受到 Windows WinRM/UAC 远程限制，并显示实际认证账号。
- 主机管理主界面移除“测试 SMB”操作入口，降低普通用户对 SMB 管理共享的依赖和认知负担；SMB 兼容代码仍保留用于识别/诊断。
- WinRM 正式目标路径限制为目标机本地绝对盘符路径（C:\\、D:\\、E:\\、F:\\...），避免 NTLM WinRM 访问集中 UNC 备份时触发 double-hop 问题。
- TARGET_PREP_WINRM.ps1 / .cmd 仅执行标准 WinRM quickconfig、服务、Listener 与端口检查，不修改注册表、TrustedHosts、UAC 或账号权限。


## v0.6.0 - 2026-09-06

- **目标 Windows 文件分发主通道改为 WinRM**：不再要求文件目标目录通过 `C$ / D$ / E$` 管理共享访问。
- 用户在映射中继续直接填写目标机真实路径，例如 `C:\Program Files\NARI`、`D:\ADMS\bin`、`E:\temp`、`F:\Update`。
- 文件上传、目录创建、写入探针、备份、临时文件、正式替换、文件大小和 SHA256 校验全部通过 WinRM / PowerShell 完成。
- 分发前 CMD、停止服务、结束进程、启动服务、分发后 CMD 与文件传输复用同一 WinRM 连接模型。
- 分发页移除“测试 SMB”作为前置步骤，改为 **测试 WinRM**：同时验证身份认证以及当前所有目标目录的实际写入能力。
- 新增主机 `winrm_status` 持久化状态；分发目标表直接显示“WinRM 状态 / 可分发”。
- 在线检测优先显示 `在线（WinRM 可达）`，仍保留 445/3389 等端口作为辅助诊断。
- 本地账号可以只输入用户名（例如 `ADMS`）；当主机库已有真实 Computer Name 时，程序自动使用 `COMPUTER\ADMS` 进行 WinRM NTLM 身份认证。域账号 `DOMAIN\user` / UPN 保持原样。
- 目标路径不再转换为 UNC 管理共享；支持任意 Windows 盘符的绝对路径以及显式 UNC 路径（是否可访问由远端 WinRM 账号权限决定）。
- 保留多源→多目标映射、SFTP 来源拉取、本地审计、覆盖前备份、失败重试、SHA256、目标冲突检查和刷新后勾选状态保持。
- 密码仍只存在当前进程内，不写入 SQLite、JSONL、任务审计或命令行参数。
- 兼容代码仍保留 SMB 诊断/旧主机信息，但 v0.6.0 正式分发流程不依赖 SMB。

## v0.5.2 - 2026-09-06

- 修复“测试在线状态”完成后目标主机全部被重新勾选的问题。
- 修复根因：`refresh_hosts()` 不再在每次刷新时无条件 `setChecked(True)`。
- 分发页主机勾选状态现在按 Host/IP 保存并在在线测试、SMB 测试、主机名验证、主机编辑/刷新后恢复。
- 首次加载仍保持默认全选；已经存在用户选择状态后，新出现的主机默认不自动勾选，防止意外加入分发。
- 刷新主机数据时保留分发主机表和主机管理表的滚动位置。

## v0.5.0 - 2026-09-06

- 将文件分发模型从“单一来源 + 单一目标目录”升级为“多源 → 多目标目录分发映射”。
- 一次任务可同时添加多个本地文件、多个本地目录和多条 SFTP 映射，每条映射拥有独立目标目录。
- 目录映射支持“仅复制目录内容”和“复制目录本身”两种模式。
- 所有勾选 Windows 主机会执行同一套映射计划，支持一次任务跨 D:/E: 等不同盘符目录。
- 主机管理取消“默认目标目录”的业务含义；旧数据库字段仅保留兼容，不再在 UI 中要求用户配置。
- 新增 task_mappings 表；文件审计新增 mapping_id、source_path、target_path。
- 分发前新增目标文件冲突检查：多个源最终映射到同一个 Windows 目标文件时直接阻止任务。
- 自定义备份根目录在多目标场景中按任务 ID + 原盘符 + 原目录结构保存，避免同名文件备份冲突。
- 新增“测试在线状态”：Ping + TCP 445/3389/5985/5986 组合判断，不把 Ping 失败简单视为离线。
- 在线测试同时加入文件分发页和主机管理页；测试结果持久化到 hosts 表。
- 主机管理新增在线状态、Ping、SMB、RDP、WinRM、SMB 写权限状态和最后测试时间。
- “测试 SMB”升级为对当前全部目标目录逐一执行认证和写入探针。
- 正式分发仍保持安全顺序：所有 SMB/目录/备份/空间预检查通过后才执行 WinRM 停服务/结束进程。
- 保留 v0.4.5 原生 Unicode SMB MPR API、错误码中文诊断、完整审计、SHA256、多阶段备份校验、主机名识别和输入滚轮保护。

## v0.4.5 - 2026-09-06

- 修复 SMB 错误信息乱码：不再用 UTF-8 解码中文 Windows `net use` 输出。
- SMB 会话改用 Windows 原生 Unicode MPR API（`WNetAddConnection2W` / `WNetCancelConnection2W`）。
- 显式 SMB 密码不再暴露在子进程命令行参数中。
- 新增“测试 SMB”：批量验证认证、管理共享访问、目标目录读写能力。
- Windows 错误 5/53/67/86/1219/1326/1330/1909 增加中文原因与操作建议。
- 调整部署安全顺序：先完成 SMB / 写权限 / 磁盘空间预检查，确认可分发后才执行 WinRM 停服务和结束进程。
- Splash 版本号改为运行时动态绘制。
- 保留 v0.4.4 输入控件滚轮保护与页面正常滚动。

## v0.4.4 - 2026-09-06

- 修正 v0.4.3 全局滚轮保护范围过大的问题。
- 现在只禁止输入控件使用鼠标滚轮修改值。
- 不再拦截 `QAbstractSlider` / `QScrollBar`。
- 主页面右侧滚动条、表格滚动条、列表滚动、只读日志滚动全部恢复正常。
- `QSpinBox` / `QDoubleSpinBox` / 日期时间输入、`QComboBox`、`QLineEdit` 和可编辑多行输入仍保持滚轮保护。

## v0.4.3 - 2026-09-06

- 全局禁用所有“输入控件”的鼠标滚轮操作，避免滚动页面时误改配置。
- 覆盖 QSpinBox / QDoubleSpinBox / 日期时间类 SpinBox、QComboBox、QSlider/QDial 类、QLineEdit。
- 可编辑 QTextEdit / QPlainTextEdit 同样不响应滚轮；只读执行日志仍保留滚轮浏览能力。
- 采用 QApplication 级事件过滤器统一实现，主窗口、弹窗、动态创建输入控件以及后续新增控件无需逐个修改。
- 重点修复 WinRM 端口、SFTP 端口、失败重试、主机并发数、扫描线程数、磁盘余量等数值框因滚轮误触导致参数变化的问题。
- 保留 v0.4.2 的跨网段主机名增强识别，以及 v0.4.0+ 的完整本地审计、可配置备份、多阶段校验和远程部署流程。

## v0.4.2 - 2026-09-06

- 修复跨网段 Windows 主机“445/3389 可达但计算机名仍未识别”的核心问题。
- 快速发现新增 **Windows 工作站 API + SMB/NTLM 计算机身份指纹**：当 TCP 445 可达时，先使用 `NetWkstaGetInfo(100)` 获取目标 Computer Name；若不可用，再从 SMB/NTLM Challenge 的 TargetInfo 中读取 DNS/NetBIOS 计算机身份。两个快速步骤都不需要 WinRM，也不提供密码。
- 快速名称识别优先级调整为：Windows NetWkstaGetInfo(100) → SMB/NTLM → DNS PTR → NetBIOS (`nbtstat -A`) → Windows 名称解析 (`ping -a`) → 未识别。
- 新增 **SMB/WKSSVC 深度验证**：使用一次性 Windows 凭据通过 TCP 445 调用 Workstation Service 获取 Windows Computer Name，不要求目标机开启 5985/5986 WinRM。
- “深度验证主机名”新增自动模式：优先 SMB/WKSSVC，失败后才回退 WinRM `hostname`。
- 主机名来源新增 `SMB/NTLM 指纹` 与 `SMB/WKSSVC 验证`，并继续持久化名称来源、验证状态和说明。
- 主机名验证账号/密码只驻留当前进程，不写入 SQLite、JSONL、任务日志或审计详情。
- 新增 `impacket` 依赖；升级后运行一次 `setup.bat --ensure` / `run.bat` 会自动补齐依赖。
- 保留 v0.4.1 的多 CIDR 扫描，以及 v0.4.0 的完整本地审计、可配置备份、多阶段大小/SHA256 校验和 WinRM 部署流程。

## v0.4.1 - 2026-09-06

- 增强 Windows 主机名识别，不再只依赖 `socket.gethostbyaddr()` / DNS PTR。
- 快速发现新增 DNS PTR → NetBIOS (`nbtstat -A`) → Windows 名称解析 (`ping -a`) 多级名称提示。
- 新增 WinRM 主机名深度验证：用户明确触发后在目标 Windows 主机执行 `hostname` 获取实际 Computer Name。
- 主机发现新增“名称来源”“名称状态”“名称说明”列。
- 主机管理新增“名称来源”“名称状态”“名称说明”，并支持多选后批量 WinRM 验证名称。
- 同一扫描中多个 IP 返回相同主机名时提示“名称重复，建议验证”。
- 对 NetBIOS 15 字符限制进行显式提示，避免把截断名称误认为完整 Windows 主机名。
- 主机数据库新增 `hostname_source`、`hostname_verified`、`hostname_note` 字段，并兼容旧 `fds.db` 自动迁移。
- WinRM 验证成功/失败均写入本地操作审计；密码不保存、不进入审计。
- 完整保留 v0.4.0 的本地全链路日志、可配置备份目录、多阶段 SHA256/大小校验、WinRM 部署工作流。

## v0.4.0 - 2026-09-06

- 新增本地全链路审计：SQLite + app.log + 每日 operations JSONL + 每任务独立审计目录。
- 新增 audit_events、task_backups、task_verifications 数据表，并兼容旧 fds.db 自动迁移。
- 每条操作审计自动记录操作用户、操作计算机和软件版本。
- 新增“审计日志”主页面，可按分类 / 状态 / 关键词查询并导出 CSV。
- 分发历史详情新增“备份记录”“校验记录”“完整审计”。
- 备份根目录可由用户指定：支持目标机路径（例如 E:\FDS_Backup）和集中 UNC 共享；集中共享按任务/主机隔离；留空继续使用目标目录\.fds_backup。
- 修正备份重试安全性：原始文件只备份一次，重试不会覆盖真正的回滚备份。
- 新增 Manifest SHA256 总体指纹。
- 新增分发前预检查：本地审计链、目标/备份写权限和磁盘空间；审计目录无法建立时不执行远程变更。
- 文件大小校验始终执行；SHA256 可执行源、现有目标、备份、临时文件、正式文件多阶段强校验。
- 审计序列化自动脱敏 password / token / secret 等常见敏感字段。
- 系统设置新增本地审计目录、默认远程备份根目录、磁盘空间安全余量。

## v0.3.2-zh-CN - 2026-09-06

- 修复中文主界面残留英文：`Windows Targets` 改为“Windows 目标主机”。
- 对应英文说明改为中文。
- `Application Settings` 改为“系统设置”，说明文字同步中文化。
- 文件清单、SFTP 下载、目标路径错误等运行日志改为中文。
- WinRM 自动生成的状态文本和退出码提示进一步中文化。
- TARGET_PREP_WINRM.ps1 的用户提示信息改为中文。
- 保留 Windows、SFTP、SMB、WinRM、CIDR、SHA256、UNC、RDP 等标准技术名词。


## v0.3.1 - 2026-09-06

- 默认语言调整为简体中文，程序启动后第一屏即为中文界面
- 中文化左侧导航、顶部说明、分发页、主机管理、主机发现、历史、设置和任务详情
- 中文化主要提示框、执行日志、扫描状态及环境初始化脚本
- 来源类型改为中文显示，但内部仍使用 LOCAL / SFTP 稳定代码值，保持业务逻辑不变
- History / Task Detail 表头改为中文，并在显示层翻译任务状态、文件动作、远程操作阶段
- 新主机默认分组使用“默认”，扫描加入的主机使用“自动发现”
- 默认 Windows 字体优先 Microsoft YaHei UI
- Splash 启动画面重新制作成中文版本
- 保留 v0.3.0 多 CIDR 扫描，可同时扫描 172.16.22.0/24、172.16.21.0/24 等多个网段
- 保留 v0.3.0 自动创建 / 修复 .venv 的 setup.bat --ensure 机制


## v0.3.0 - 2026-09-06

- Rebuilt the desktop shell with a professional sidebar/topbar/card UI.
- Added original File Distribution Studio branding and complete Windows icon assets.
- Added splash screen and stable Windows AppUserModelID/runtime icon handling.
- Fixed environment bootstrap: `run.bat` now auto-ensures `.venv` and dependencies.
- Removed mandatory `pip install --upgrade pip`, which could interrupt setup before dependencies were installed.
- Added `setup.bat --ensure`, `--verify`, and `--recreate`.
- Added Python 3.10 x64 support to match existing field workstations.
- Added multi-CIDR Discovery with persistent scan ranges and combined progress.
- Supports scanning `172.16.22.0/24` and `172.16.21.0/24` in one job.
- Added formal PyInstaller spec/build/release workflow with embedded ICO and SHA256 release file.
- Preserved v0.2.0 WinRM pre/post CMD, service stop/start, process kill, SMB transfer, SHA256 verification, backup, retry and SQLite audit behavior.

## v0.5.2-zh-CN - 2026-09-06

- 所有密码输入框新增“小眼睛”显示/隐藏按钮，包括：Windows SMB/WinRM 密码、SFTP 密码、SMB 测试密码、主机名深度验证密码。
- 密码显示切换只影响当前界面显示，不写入 SQLite、JSONL 或任务审计文件。
- 强化 Windows 错误 5（拒绝访问）诊断：明确区分“主机/445 可达”与“管理共享授权成功”，并针对未限定账号（如 `ADMS`）、D$/E$ 管理共享、Remote UAC、共享/NTFS 权限给出中文建议。
- 文件分发页增加管理共享提示：D$/E$ 需要远程管理员令牌，生产环境优先使用域部署账号或专用 SMB 共享。