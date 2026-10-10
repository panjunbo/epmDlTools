# epmDlTools 架构设计与实现计划

> 对应需求文档：[require.md](./require.md)
> 参考原工具：`G:\GitHub\PythonCode\9062_download_tools`（扁平结构）
> 目标项目：`g:\GitHub\PythonCode\epmDlTools`（分层 + 子包）

---

## 1. 总体目标与设计原则

| 目标 | 描述 |
| --- | --- |
| 双前端 | 同一业务逻辑同时支持 **CLI**（argparse 子命令）和 **GUI**（Tkinter）。 |
| 可扩展 | 通过 `CommunicationInterface` + `Command` 抽象，新增传输/新业务无需改动主体。 |
| 日志分流 | **常规日志**（下载流程/状态）与 **ID 日志**（设备 ID/产品标识）分开显示；日志串口在“下载”期间被复用为下载串口，下载结束自动还原为日志串口。 |
| 配置可持久化 | 默认值由 `config/config.ini` 提供；CLI/UI 修改后写回磁盘。 |
| 协议可移植 | 协议字节序、CRC、消息帧等业务规则集中在 `protocol/`，不与传输耦合。 |

**设计原则**：分层 + 依赖单向 → `UI / CLI` → `commands` → `protocol` → `communication` → `硬件`。

---

## 2. 目标目录结构（最终态）

```
epmDlTools/
├── epmDlTool.py                    # 入口；解析 --ui/--cli/子命令
├── commands/
│   ├── __init__.py
│   ├── base.py                     # Command 抽象基类
│   ├── ota.py                      # OTA 命令
│   ├── flashload.py                # Flash 擦/写/读
│   └── programmer.py               # 烧录 programmer
├── communication/
│   ├── __init__.py
│   ├── interface.py                # CommunicationInterface（已有雏形）
│   ├── uart_comm.py                # UartComm
│   ├── spi_comm.py                 # SpiComm
│   ├── i2c_comm.py                 # I2cComm
│   └── log_serial.py               # LogSerial（日志串口，单独实现，UI/CLI 共用）
├── protocol/
│   ├── __init__.py
│   ├── frame.py                    # 通用帧编/解码、同步字、checksum
│   ├── flashload_protocol.py       # 原 download_flash.py 主体
│   └── programmer_protocol.py      # 原 download_programmer.py 主体
├── utils/
│   ├── __init__.py
│   ├── config_manager.py           # 读写 config/config.ini
│   ├── crc.py                      # CRC 算法（pycrc）
│   ├── bin_parse.py                # bin 读/写
│   └── logger.py                   # 双通道 Logger：regular / id
├── ui/
│   ├── __init__.py
│   ├── app.py                      # 主窗口
│   ├── log_panel.py                # 常规日志 Text
│   ├── id_log_panel.py             # ID 日志 Text
│   ├── serial_panel.py             # 串口配置 + 打开/关闭
│   └── download_panel.py           # 下载参数 + 触发按钮
├── config/
│   └── config.ini
├── help/
│   └── struct.txt
└── doc/
    ├── require.md
    └── design.md
```

---

## 3. 模块职责与关键接口

### 3.1 `communication.interface`
```python
class CommunicationInterface(ABC):
    def open(self) -> bool: ...
    def close(self): ...
    def send(self, data: bytes) -> int: ...
    def recv(self, timeout_ms: int = 1000) -> bytes: ...
    def flush(self): ...
```
> 在原雏形上补充 `recv_buffered()`（供协议层循环读取）、`is_open` 属性。`LogSerial` 也实现同一接口，使协议层无需区分。

### 3.2 `commands.base.Command`
```python
class Command(ABC):
    name: str
    def __init__(self, args, config: dict, logger: Logger): ...
    @classmethod
    def add_arguments(cls, p: argparse.ArgumentParser): ...
    @abstractmethod
    def execute(self) -> int: ...        # 0=OK, 非0=FAIL
    def _swap_serial_to_download(self):  # 给 UI 调用：切到下载串口
    def _swap_serial_to_log(self):       # 切回日志串口
```
> 关键设计：命令内部使用 `comm_factory.create(comm_type, role='download')` 获取下载串口，使用 `LogSerial` 获取日志串口；UI 在“开始下载”时调用 `_swap_serial_to_download`，完成回调 `_swap_serial_to_log`。

### 3.3 `utils.logger.Logger`
```python
class Logger:
    def __init__(self, regular_sink, id_sink): ...    # sink 可为 GUI Text 或 stdout
    def info(self, msg): self.regular("INFO", msg)
    def warn(self, msg): self.regular("WARN", msg)
    def error(self, msg): self.regular("ERROR", msg)
    def id(self, msg):     # 写入 ID 日志通道；带前缀 "[ID]"
```
> sink 用协议：UI 传 `tk.Text` 回调，CLI 传 `sys.stdout`。这样同一份 `Command` 在两条路径上输出格式一致。

### 3.4 `protocol.frame`
- `Sync = 0x55aa5a5a` / `RxSync = 0xb51ababa`（与原代码保持一致）
- `Frame.encode(sync, payload_len, msg_id, data) -> bytes`
- `Frame.feed(rx_buf, on_msg)` 累积式解析（移植自 `download_flash.rx_data_pack_parse`）

### 3.5 串口切换策略（关键需求 #2）
```
[常态]
    LogSerial(读)  -- 显示在 "常规日志" 与 "ID 日志" 面板
[点击"开始下载"]
    1) LogSerial.close()
    2) DownloadSerial.open()  // 用同一物理口，但角色不同
    3) Command.execute() 跑流程，进度写"常规日志"，ID 类信息双写
[下载结束 / 异常]
    1) DownloadSerial.close()
    2) LogSerial.open() 自动重启读线程
    3) UI "下载" 按钮恢复可用
```
> 切换在 `Command` 内部用 `enter_download_mode()` / `exit_download_mode()` 上下文管理器；UI 只触发命令，不直接动串口。

---

## 4. 数据流（以“GUI 点击下载 flashload”为例）

```
User click "下载"
    → DownloadPanel.on_click()
        → cmd = FlashloadCommand(args, cfg, logger)
        → cmd.execute()
            ├─ enter_download_mode()   # 关日志口、开下载口
            ├─ flashload_task()        # 协议层跑状态机
            │     ├─ send Frame → comm.send()
            │     ├─ recv Frame → comm.recv() / Frame.feed()
            │     └─ logger.info() / logger.id()
            └─ exit_download_mode()    # 关下载口、开日志口
```

---

## 5. 配置项（`config/config.ini`）

```ini
[serial]
log_port    = COM5
log_baud    = 115200
download_port = COM5          ; 可与 log 同口（需切角色）
download_baud = 500000

[flashload]
comm_type   = uart
flash_port_num = 0
target_baud = 500000
sync_exit   = true
; ... 与原 download_config 对齐

[programmer]
bin_path    = bin/programmer.bin
bin_addr    = 0x50540000

[ui]
theme       = light
auto_scroll = true
```

---

## 6. 分步实现计划

每一步均落地“能跑 + 有最小验证”，不一次性铺开。

### 阶段 1 · 通信抽象与三种实现
**目标**：把 `comm_uart.py / comm_i2c.py / comm_spi.py` 重写为 `UartComm / I2cComm / SpiComm`，实现 `CommunicationInterface`。
**产物**：
- `communication/interface.py`（补全 `recv_buffered`, `is_open`）
- `communication/uart_comm.py`（基于 pyserial）
- `communication/spi_comm.py`（基于 ControlSPI，保留原签名）
- `communication/i2c_comm.py`（基于 ControlI2C，保留原签名）
- `communication/__init__.py` 暴露 `create(comm_type, port, baud, role)` 工厂
**验证**：单元脚本 `python -c "from communication import create; c=create('uart','COM5',115200); c.open(); c.close()"`

### 阶段 2 · utils 基础设施
**目标**：补齐 `config_manager / crc / bin_parse / logger`。
**产物**：
- `utils/config_manager.py`：读取 `config/config.ini`，提供 `get(section, key)` / `set()` / `save()`
- `utils/crc.py`：包装 pycrc，支持 `width/poly/ref_in/init/ref_out/xor_out` 重配
- `utils/bin_parse.py`：`parse_bin(path) -> bytearray` / `save_bin(path, data)`
- `utils/logger.py`：`Logger(regular_sink, id_sink)`，双通道；sink 协议支持 GUI/CLI
**验证**：CLI 脚本读 config、打印 CRC、对一个 bin 做 read/print/save。

### 阶段 3 · 协议层
**目标**：把 `download_flash.py` / `download_programmer.py` 中**纯协议**部分抽出，与传输解耦。
**产物**：
- `protocol/frame.py`：`Frame` 类（编/解码、累积解析）
- `protocol/flashload_protocol.py`：原 `Flashload_protocol`，构造改为 `__init__(comm, bin_parse, config, logger)`
- `protocol/programmer_protocol.py`：同上
- 原 `download_flash / download_programmer` 中 `print(...)` 全部换成 `logger.info(...)`；ID 类信息（chip id / mac / sn）用 `logger.id(...)`
**验证**：CLI `python epmDlTool.py flashload -h` 仍可用参数解析；不接硬件不崩。

### 阶段 4 · commands 命令层
**目标**：让 `epmDlTool.py` 能跑起来；命令内部完成“串口切换 + 协议运行 + 日志输出”。
**产物**：
- `commands/base.py`：`Command` 抽象 + 串口切换上下文管理器
- `commands/flashload.py`：`FlashloadCommand(Command)`
- `commands/programmer.py`：`ProgrammerCommand(Command)`
- `commands/ota.py`：`OtaCommand(Command)`（占位，结构与原 `9062` 入口对齐）
**验证**：`python epmDlTool.py flashload --dry-run` 能解析参数、加载 config、打印将要执行的步骤。

### 阶段 5 · CLI 入口打通
**目标**：`epmDlTool.py` 支持子命令 + 公共配置。
**产物**：
- `epmDlTool.py` 重写：argparse + subparsers；`mode` 必选；`--ui` 启动 GUI
- 帮助文本与 `help/struct.txt` 同步
**验证**：所有命令 `-h` 输出正常；无参运行打印帮助并退出 0。

### 阶段 6 · UI 层（核心新增）
**目标**：Tkinter 主窗口，含：常规日志面板、ID 日志面板、串口配置、下载参数、开始/停止按钮。
**产物**：
- `ui/app.py`：`EpmDlApp(tk.Tk)`，菜单 + 主区双 Log + 串口/参数侧栏
- `ui/log_panel.py`：`LogPanel(parent, title, sink_cb)`，封装 `tk.Text` + 滚动、自动追加
- `ui/id_log_panel.py`：同 LogPanel，仅 sink 指向 `Logger.id`
- `ui/serial_panel.py`：端口下拉（`pyserial.tools.list_ports`）、波特率、打开/关闭
- `ui/download_panel.py`：参数表单 + 开始按钮 + 进度条
- 串口切换完全在 `Command.enter_download_mode/exit_download_mode` 完成；UI 只触发命令
**验证**：手动启动 UI → 打开日志串口 → 看到设备日志和 ID → 点“下载” → 日志停 → 协议跑 → 完成 → 日志自动恢复。

### 阶段 7 · 联调、测试、文档
**目标**：冒烟通过 + 文档齐备。
**产物**：
- `doc/design.md`（本文件）
- `doc/usage.md`：CLI/UI 用法
- `help/struct.txt`：保持与目录一致
- `tests/smoke_*.py`：每条命令一个最小冒烟（mock 通信）
**验证**：在无硬件机器上 `pytest tests/` 全绿；在有硬件机器上手动跑一次完整下载流程。

---

## 7. 兼容性策略

| 维度 | 策略 |
| --- | --- |
| CLI 参数 | 与 `9062_download_tools` 的 `-mode / -comm_type / -uart_port / -flashload_cmd / -erase_*` 等参数命名 1:1 兼容；UI 不传 CLI 参数时使用 `config.ini`。 |
| 配置文件 | 复用 `config/config.ini` 段名，新增段加在文件末尾，不删旧 key。 |
| 协议 | 同步字、checksum、状态机与原代码完全一致，保证在线设备固件无需改动。 |
| 第三方 | 保留 `pyserial / pycrc / ControlSPI / ControlI2C`；UI 仅引入标准库 `tkinter`。 |

---

## 8. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 串口同时只能被一个进程打开 | `LogSerial` 和 `DownloadSerial` 严格互斥；切换用上下文管理器保证异常路径也关闭。 |
| UI 阻塞协议循环 | 协议跑在 `threading.Thread`，UI 通过 `queue.Queue + after()` 消费日志。 |
| I2C/SPI 依赖厂商 DLL | 工厂 `create()` 在 DLL 缺失时给出明确错误；不阻断 UART 命令。 |
| Tkinter 在某些环境下样式不一致 | 仅使用 `tk.Frame/Text/Button/ttk.Combobox` 等标准控件，不绑外部主题。 |

---

## 9. 完成定义（DoD）

- [ ] `python epmDlTool.py -h` 输出全部子命令帮助
- [ ] `python epmDlTool.py --ui` 启动窗口，能看到日志 / ID 两栏
- [ ] 点击“开始下载”触发串口切换；下载结束自动恢复
- [ ] 现有 UART 设备固件烧录/擦除流程与原工具结果一致
- [ ] `tests/` 冒烟通过；`doc/design.md`、`doc/usage.md` 齐备

---

## 10. 需求迭代记录（v2）

需求新增的 UI 约束：

| 需求 | 实现 |
| --- | --- |
| 下载端口/波特隐藏 | UI 仅显示 `log_port`；`download_port`/`download_baud` 只从 `config.ini [serial]` 读取 |
| 配置自动读/写 + 菜单 | 启动时 `ConfigManager.read()`；关闭时 `_collect_ui_to_cfg()` + `save()`；菜单 → 文件 → 保存配置 / 用文本编辑器打开 / 打开配置文件 |
| 多核 bin 目录选择 | UI 用 `tk.filedialog.askdirectory` 选目录；`ConfigManager.list_cores()` 读 `[bins]` 三核配置；面板自动标记 `[OK]/[--]` 表示文件是否存在 |
| ID 日志无独立界面，按核写入文件 | `Logger.set_id_file(path)`；每核烧录前切换到 `<bin_dir>/<core>_id.log`；启动 `app._run_one_core` 闭环切换 |

**当前配置示例**（`config/config.ini`）：

```ini
[serial]
log_port = COM5
log_baud = 115200
download_port = COM5
download_baud = 500000     ; ← UI 不显示

[bins]
default_dir = ./bin
core_a_name = mcua ; core_a_file = mcua.bin ; core_a_addr = 0x08080000
core_b_name = mcub ; core_b_file = mcub.bin ; core_b_addr = 0x080A0000
core_c_name = mcuc ; core_c_file = mcuc.bin ; core_c_addr = 0x080C0000

[id_log]
core_a_log = mcua_id.log
core_b_log = mcub_id.log
core_c_log = mcuc_id.log
```

**下载流程**（多核顺序烧录）：

```
[点击"开始下载"]
    ├─ core_a:  set_id_file(<dir>/mcua_id.log)  →  run_programmer/flashload
    ├─ core_b:  set_id_file(<dir>/mcub_id.log)  →  run_programmer/flashload
    └─ core_c:  set_id_file(<dir>/mcuc_id.log)  →  run_programmer/flashload
[结束]
    └─ 自动重开日志口
```

---

## 11. 需求迭代记录（v3）

| 需求 | 调整 |
| --- | --- |
| 3.1 下载波特率要在 UI 中显示 | `[serial].download_baud` 不再隐藏；`SerialPanel` 同时显示日志端口/波特与下载端口/波特 |
| 3.5 单 toggle 按钮 | 合并开/关为「打开串口」/「关闭串口」切换；`on_open` 返回 bool，失败时按钮状态自动回退 |
| 3.6 串口助手 | 新增 `ui/serial_assistant.py`，含接收区、发送区、列表发送（Treeview + 增/删/编辑/上移/下移/双击发送）；接收支持 ASCII/HEX/编码/时间戳/自动滚屏；发送支持 ASCII/HEX + `\r/\n` 追加 |
| 预设持久化 | `[presets]` 段存 `name_i/mode_i/data_i`；`ConfigManager.list_presets/save_presets`；UI 启动读入，关闭写回 |

**UI 布局变化**：

```
┌──────────────────────────────────────────────────────────────┐
│ 文件  帮助                                                  │
├──────────────────────────────┬───────────────────────────────┤
│ 串口配置                       │ 下载                          │
├──────────────────────────────┼───────────────────────────────┤
│ 串口助手 (接收/发送/列表发送)   │ 常规日志                       │
└──────────────────────────────┴───────────────────────────────┘
```

---

## 12. 需求迭代记录（v4）

| 需求 | 调整 |
| --- | --- |
| 数据位/校验位/停止位 → 弹窗 | `SerialPanel` 主面板仅显示摘要 `8-N-1`；「配置参数…」按钮弹出 `SerialParamDialog` |
| 下载端口 = 日志端口（共用一个） | 移除 `dl_port` 字段；`port` 同时用作日志端口和下载端口；下载波特率仍独立 |
| 列表发送默认隐藏，按键显示 | `SerialAssistant.presets_visible` 默认为 False；「▶/▼ 列表发送」按钮切换 |
| 日志与串口助手接收区合并 | 移除独立 `log_panel`；`Logger.set_sinks(regular_sink=assistant.feed_log)`；接收区统一显示下载流程日志 + 设备数据 |

**UI 布局变化**（v4）：

```
┌──────────────────────────────────────────────────────────────┐
│ 文件  帮助                                                  │
├──────────────────────────────┬───────────────────────────────┤
│ 串口配置                       │ 下载                          │
├──────────────────────────────┴───────────────────────────────┤
│ 串口助手（接收区 / 日志 = 同一处）                              │
│   接收区 (下载流程 + 设备数据，HEX/ASCII 切换)                 │
│   发送区 (HEX/ASCII, \r/\n, 定时)                            │
│   ▶ 列表发送（默认隐藏，点展开）                               │
└──────────────────────────────────────────────────────────────┘
```
