# TimeIndex 自动化测试平台

此平台在本机批量评估 TimeIndex 的活动记录、整理、检索和隐私暴露。资源占用测量默认关闭。测试用数据与标准答案、评分代码、环境准备及清理脚本全部保留在本目录，便于论文复现。

## 独立仓库的范围与安装

本仓库只发布测试平台，包含平台代码、虚构样本、自动验收脚本、[论文实验方案](paper_experiment.md)、[平台计划](plan.md)及 `openspec/` 中的设计和实验规格。TimeIndex 本体源码、真实实验记录、数据库、本地模型、环境缓存及联合发布 ZIP 不在本仓库中。

平台需要另行准备 TimeIndex 本体。**本体与测试平台可以放在不同文件夹，无需上下级关系。** 平台会查找并核验本体项目根目录，在每轮实验中复制所选源码；不会改动本体源码或使用其日常数据库。

例如下面的分开放置方式可以使用：

```text
C:/Users/user/Desktop/TimeIndex/     # TimeIndex 本体，含 pyproject.toml 和 src/TimeIndex/
D:/Projects/TimeIndex-Test-Platform/ # 本测试平台
```

仍支持原来的联合发布包布局：

```text
TimeIndex/
├── pyproject.toml          # 另行准备的 TimeIndex 项目
├── src/TimeIndex/          # 另行准备的 TimeIndex 本体
└── test_platform/          # 本仓库
    ├── app.py
    ├── pyproject.toml
    └── paper_experiment.md
```

在任意希望保存测试平台的位置执行：

```powershell
git clone https://github.com/nahanhhan/TimeIndex-Test-Platform.git
cd TimeIndex-Test-Platform
.\start.cmd
```

先安装下文所需的 `uv`，再启动平台。TimeIndex 的版本应与平台调用的核心接口兼容，每轮实验会记录选中的本体绝对路径及本体、平台的源码校验值。下方历史 ZIP 升级说明和 `package.ps1` 针对包含外部本体的联合发布包；打包脚本也使用所选本体目录，其输出位于被忽略的 `dist/`。克隆本仓库不会取得本体文件或联合发布包。

### 本体位置的自动检测与手动指定

启动时优先使用 `-TimeIndexPath` 指定的目录，其次使用 `TIMEINDEX_PROJECT_DIR` 环境变量、已记住的位置和原布局中的上级目录；仍未找到时，检查平台附近的文件夹及当前用户的桌面。自动搜索最多向下两层、检查 600 个目录，跳过环境缓存、实验目录及目录链接，不扫描整个硬盘。发现多个候选时要求明确选择，不随意使用其中一份。

项目根目录必须包含名称为 `timeindex` 的 `pyproject.toml`，以及 `src/TimeIndex` 下的配置、采集、模型处理与存储文件。检测过程不导入本体、不启动采集、不创建本体数据库。目录不完整时给出缺少的文件。

如果本体在另一个位置，可直接指定，例如：

```powershell
.\start.cmd -TimeIndexPath "C:\Users\user\Desktop\TimeIndex"
```

也可以直接启动网页，在“TimeIndex 本体项目文件夹”中填写该路径，再点击“检查并记住本体位置”。找不到本体或有多个候选时，网页仍可打开，但开始实验前必须选定有效目录。位置保存在本机 `.timeindex-project.json`，不会提交到 Git 或进入诊断 ZIP。换电脑后失效的已保存位置会重新查找；显式填写的错误路径不会偷偷换成其他本体。

命令行实验增加 `--timeindex-project "C:\Users\user\Desktop\TimeIndex"`；`doctor` 同样接受该参数。只查看或记住位置可运行：

```powershell
uv run --no-sync python -m platform_core.cli locate
uv run --no-sync python -m platform_core.cli locate --timeindex-project "C:\Users\user\Desktop\TimeIndex" --save
```

## 2026-10-04：论文实验入口

新增 **“论文实验”**：使用独立 `paper-1.0` 数据集，60个虚构窗口场景、6类活动、12个主题；12题按主题找活动、18题找指定记录，另验证6个时间范围。保留核心记录、摘要、整理和时间/标签/语义查询，并增加原始标题关键词对照。论文主结果和章节、精确标签、分组诊断分开显示，当前评分规则为 `4.0`，原有指标公式及旧样本保留。

每轮新增论文主指标、检索分任务、时间查询和隐私阶段四张 CSV，均纳入诊断 ZIP。先用快速检查确认模型可用，再连接真实模型运行论文实验；专用桌面可附加真实软件和黑名单采集。建议同配置重复三轮，分别保留结果。模拟服务结果仅验证平台，不作为论文中的真实模型效果。

完整的 **测试方式、数据内容、结果表及可支持的论文结论** 见 [论文实验方案](paper_experiment.md)。新论文任务与旧版章节查询不同，不能直接把分数变化当作系统性能提升。

2026-10-02 新增 **“一键下载诊断报告（ZIP）”**：实验结束后可一次收集报告与定位问题所需的脱敏证据，失败或取消的实验也支持；命令行入口为 `bundle`。详见下方运行方式。

## 2026-10-02：按核心功能评估，逐次输出可复核

新版沿用 TimeIndex 自己的推理、写库、整理和回忆代码，不更换提示词或模型回答来迎合评分。TimeIndex 目前读取的是窗口标题和进程事件，所以自动评分检查可观察的主题、章节、主要应用、原始记录保留及检索目标，不宣称测到了文档正文理解或键盘操作。

- **快速流程检查（合成）：**8 个场景分布在六类主题，4 个问题，只检查链路和初步效果。没有实际打开场景里的软件。8 条记录中返回前五条，随机命中单个目标的理论机会就有 62.5%，不能把该分数作为长期检索效果。
- **真实软件实验：**在专用测试电脑或虚拟机上，自动打开记事本、PowerShell 和 Edge/Chrome 的本地测试文档，切换、观察、关闭，共三轮、九次软件启动尝试。实际 WMI 采集与入库使用单独数据库；软件缺失、窗口没出现、调用失败均如实报告。没有浏览器时不会偷偷换成模拟窗口。
- **完整实验：**60 条合成输入、30 个问题；确认专用测试桌面后还会执行上述真实软件阶段。整理批次取自 TimeIndex 默认配置（当前 20 条），不再由平台固定为四条。

报告现在有软件清单、实际启动 PID 与时间、窗口标题、逐例输入、初始摘要与标签、整理结果、每次实际模型请求和原始回复、每题查询与返回顺序。网页可下载“逐例明细 JSON（含模型原文）”，也可直接打开详细 HTML。运行完成只说明流程完成，效果必须看主题/章节保留和检索指标；自由标签按线索覆盖评估，固定词表 F1 仅供诊断。原始记录字段的完整性单独检查。

报告默认保留受控虚构窗口内容便于复核，遮罩审计串、非测试窗口标题；访问令牌和请求头不进入取证文件。完整原始接口回复在本地 `evidence/model_calls.json`，完整真实桌面逐例输出在 `evidence/live_cases.json`。原始证据不适合直接公开。旧实验没有保存的原始模型回复无法补造，重算报告会说明缺失。

评分版本为 `3.0`，默认数据集为 `2.0`，不可把不同版本分数直接当成前后改进。采集后调用核心整理函数，以保留整理前后对照；**自动闲时触发尚未验证**。可选资源统计含 TimeIndex 和取证开销，不含本实验打开的业务软件；模型进程单列，磁盘包含合成与真实桌面两个数据库。真实桌面和真实模型效果须在专用测试电脑上验收。

升级方法：关闭旧启动窗口，将新版 ZIP 的整个 `TimeIndex/` 合并覆盖到旧目录，保留已有 `.venv/`、`.python/`、`.uv-cache/` 和 `runs/`，双击 `test_platform/start.cmd`。验证实际采集时选择“真实软件实验”，确认专用测试桌面，LM Studio 须在该电脑上运行。

## 2026-09-30 修复版升级（历史记录）

先关闭旧平台的启动窗口，再把新版压缩包中的整个 `TimeIndex/` 合并覆盖到旧版同名目录，包含 `src/TimeIndex/` 和 `test_platform/`。这次涉及 TimeIndex 的存储和模型提示词，不能只替换 `launch.ps1`。已有 `.venv/`、`.python/`、`.uv-cache/` 和 `runs/` 可保留，然后双击 `start.cmd`。

评分规则版本为 `2.0`：忽略中文旁的排版空格，保留英文单词间隔；整理前后主指标使用同一批样本，缺失或无效的整理结果计为未命中，并另列共同完成样本的比较。报告新增入库完成率、整理完成率、主要应用匹配率和章节编号保留率。主要应用匹配只是按输入应用名称判断，不代表完整的语义正确率。

每轮会保存 `evidence/vector_integrity.json` 和 `evidence/retag_batches.json`，记录整理前后向量检查与批次完成情况。向量缺失、清零或改变时，不计算语义质量分数。旧实验可以用 `replay` 按新规则重算，并核验保存的数据库；原始逐例证据不会被改写。重算不能恢复旧模型丢掉的事实或被清零的向量，验证修复效果需要新跑一轮快速检查。

模型失联或嵌入服务缺失时仍保存原始活动，缺失向量以空值保存并跳过语义检索；故障记录不混入正常质量样本。每轮实验直接从已选择的本体目录复制源码，不复用旧的 TimeIndex 安装缓存。

## 换一台新电脑运行

1. 在旧电脑的测试平台目录运行 `.\package.ps1`，必要时增加 `-TimeIndexPath "本体项目根目录"`。它会在 `dist/` 下生成 `TimeIndex-test-platform-日期时间.zip`，只装入必需的项目源码、平台脚本、样本和依赖锁文件，并校验 ZIP 中每个文件。把这个 ZIP 传到新电脑，解压后得到 `TimeIndex/` 文件夹。压缩包不包含 `.venv/`、Python 下载、依赖缓存、已保存的机器路径、旧实验 `runs/`、旧数据库或论文材料；**新电脑首次安装依赖需要联网**。模型服务和模型文件也不在包内。
2. 若使用独立测试平台仓库，按上方“独立仓库的范围与安装”另行准备本体，两个项目可以分开放置；启动后核对本体位置。若使用联合发布 ZIP，则已包含本体与平台；这两种安装来源要在实验记录中注明。
3. 在新电脑的 Windows 上安装 `uv`（可参照 https://docs.astral.sh/uv/getting-started/installation/），重新打开 PowerShell，用 `uv --version` 确认命令可用。启动脚本会按锁文件安装依赖，并在需要时取得 Python 3.12。
4. 在新电脑上解压整个 ZIP，进入 `TimeIndex/test_platform` 文件夹，**双击 `start.cmd`**。它会按包内 `uv.lock` 指定的版本安装依赖，然后启动网页；浏览器访问 `http://127.0.0.1:8501`。若启动失败，窗口会停住，并在同一文件夹生成 `launch.log`，可据此排查。先点“环境检查”，再运行“快速检查”。模型接口不可达时，普通实验会直接报错；若只想检查无模型的降级流程，须显式勾选“仅检查平台，不运行模型质量测试”。
5. 若 LM Studio 与测试平台在**同一台电脑**，填写本机模型接口，例如 `http://127.0.0.1:1234/v1`。若分别在**两台电脑**，请在运行 LM Studio 的电脑上开启 Developer 页的 **Serve on Local Network**，记下其局域网 IP；在测试电脑填 `http://<LM Studio 电脑的局域网 IP>:1234/v1`，并勾选“允许连接另一台电脑的局域网模型（仅虚构样本）”。`127.0.0.1` 永远指当前运行平台的电脑，不能用来访问另一台电脑；平台会自动给只填到端口的地址补上 `/v1`。LM Studio 网络设置参考 https://lmstudio.ai/docs/developer/core/server/serve-on-network/ 。
6. 点击“环境检查”读取模型列表，**原样使用列表中的完整模型 ID**。例如 2026-09-28 在原电脑实际列出 `liquid/lfm2.5-1.2b` 和 `text-embedding-nomic-embed-text-v1.5`，后一模型返回 768 维；列表可能随已加载模型变化。若 LM Studio 开启了认证，可在网页的密码输入框填访问令牌；令牌不会写入运行清单或导出报告。首次运行建议先做 8 场景快速检查，确认模型真实推理和入库后再做完整实验。

旧版本中显示“完成、已处理 0/8”的记录并没有运行模型，应丢弃，不应作为实验结果。新版会在模型接口不可达时直接报错，只有显式选择无模型自检才会生成诊断记录。若局域网连接失败，先核对两台电脑在同一局域网、LM Studio 已开启网络服务、界面显示的 IP 和端口，以及系统防火墙是否允许该端口。

若测试目标是真实桌面采集，可使用专用测试电脑或 Hyper-V Windows 虚拟机，在交互式桌面运行，并选择“真实软件实验”和专用测试桌面选项；仅运行合成实验时无需勾选。

## 启动

在 Windows 安装 `uv` 后，双击本目录的 `start.cmd`。也可以在已有的 PowerShell 窗口中进入本目录，再执行：

```powershell
.\launch.ps1
```

不要直接双击 `launch.ps1` 或右键选择“使用 PowerShell 运行”，因为 Windows 在脚本退出后可能立即关闭窗口，来不及看报错。若 `start.cmd` 停在报错画面，请将同目录的 `launch.log` 发来。浏览器打开 `http://127.0.0.1:8501`。首次启动会创建本目录自己的 `.venv`，依赖版本由 `uv.lock` 固定。也可运行 `uv run --no-sync python -m platform_core.cli doctor` 检查依赖与本地模型服务。

## 运行方式

- **快速流程检查（合成）：**8 个固定合成场景、4 个问题；模型不可达时拒绝启动。显式勾选“仅检查平台”才运行无模型的受控故障场景，状态显示为“无模型自检”，不产生模型质量分数。
- **论文实验：**60 个独立编制场景、30 个分任务问题、6 个时间查询；可附加真实软件阶段，结果与合成实验分开。输出四张专用论文结果表，整理和隐私检查保留。
- **真实软件实验：**自动尝试启动九次真实业务软件，不构造合成记录；必须确认专用测试桌面。
- **完整实验：**60 个合成场景、30 个检索问题。确认专用测试桌面后另执行真实软件实验。
- **自定义样本：**上传符合 `datasets/default.json` 结构的 JSON。场景 ID、事实答案、标签、分组和检索目标必须完整。

真实桌面采集会枚举所在 Windows 会话的可见窗口。请在干净、专用的测试电脑或虚拟机的交互式桌面启动平台；关闭私人文档和账户。若 WMI 进程事件缺失，可用管理员身份运行。真实桌面模型接口须位于测试电脑本机；局域网模型选项只适用于虚构合成样本。另一台电脑模型的 CPU 和内存占用标为未测得。

每轮在 `runs/<运行编号>/` 留下环境清单、逐例原始证据、独立数据库副本与报告。`reports/report.html` 和 `reports/paper_metrics.csv` 默认脱敏；原始证据仅供本地核对，不应直接放入论文或公开仓库。`runs/` 已被 Git 忽略。

实验结束后，选择该轮记录，点击 **“一键下载诊断报告（ZIP）”**，即可一次导出详细 HTML、论文指标 CSV、汇总与明细 JSON、运行清单、样本，以及实际保存的模型请求/回复、逐例记录、查询顺序、整理批次和向量检查。诊断包沿用报告脱敏规则；配置、访问令牌、请求头、日志和数据库不打包。包内 `bundle_manifest.json` 列出文件校验值及缺失材料，旧实验未保存的证据不会补造。以后排查另一台电脑的结果时，直接传这一个 ZIP 即可，无需逐个挑选文件。打包不会重新推理或改写原实验。

失败、部分完成和取消的实验也可下载；即使模型连接失败而尚未生成报告，仍能打包已有运行清单、错误原因和环境检查结果。

## 命令行与复算

```powershell
uv run --no-sync python -m platform_core.cli run --mode quick
uv run --no-sync python -m platform_core.cli run --mode paper --dedicated-desktop
uv run --no-sync python -m platform_core.cli run --mode desktop --dedicated-desktop
uv run --no-sync python -m platform_core.cli list
uv run --no-sync python -m platform_core.cli status <运行编号>
uv run --no-sync python -m platform_core.cli cancel <运行编号>
uv run --no-sync python -m platform_core.cli replay <运行编号>
uv run --no-sync python -m platform_core.cli bundle <运行编号>
uv run --no-sync python -m platform_core.cli cleanup <运行编号>
```

命令行跨电脑连接时，在 `run` 后增加 `--endpoint http://<局域网 IP>:1234/v1 --allow-lan-model`；如果服务开启认证，先在当前 PowerShell 会话设置 `TIMEINDEX_TEST_MODEL_API_KEY` 环境变量。无模型自检可在快速模式增加 `--no-model-self-check`。

`replay` 只读取保存的逐例结果，不重新调用模型或采集桌面。生成新的论文指标表时，报告中应保留样本量、模型、Windows/虚拟机配置和未测原因。摘要事实评分只覆盖数据集预先列明的表达，不代表完整的语义正确率。

## 自动验收与真实实验

安装完成后，可以用下列命令自动检查评分、隔离、无模型降级、缺少嵌入模型、完整模拟批次和取消流程：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tests.integration_smoke
.\.venv\Scripts\python.exe -m tests.integration_missing_embedding
.\.venv\Scripts\python.exe -m tests.integration_lan_mock
.\.venv\Scripts\python.exe -m tests.integration_full_mock
.\.venv\Scripts\python.exe -m tests.integration_paper
.\.venv\Scripts\python.exe -m tests.integration_retag_partial
.\.venv\Scripts\python.exe -m tests.integration_query_embedding
.\.venv\Scripts\python.exe -m tests.integration_desktop_routing
.\.venv\Scripts\python.exe -m tests.integration_cancel
```

这些模拟服务只用于检验平台是否按预期运行，其分数不能作为 TimeIndex 的真实模型效果写入论文。真实模型实验须连接可用模型；真实桌面实验请选择“真实软件实验”，在专用 Windows 测试电脑或虚拟机的交互式会话中运行。模型须通过该桌面的回环地址访问；报告注明资源测量边界及所有未测项目。

`integration_desktop_routing` 注入窗口/进程事件来验证真实软件模式的调度、独立数据库、核心处理和逐例报告；它不启动业务软件，也不验证真实 WMI 或 LM Studio。

