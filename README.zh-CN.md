# lenovo-dmi-rescue

刷坏 BIOS 之后，把联想 InsydeH2O 固件里那份被抹掉的机器身份信息找回来——
产品名、MTM、序列号、UUID、Windows OA3 产品密钥。

零依赖，纯标准库 Python 3.10+。这一点是刻意的：被修的那台机器开不了机时，
你可能只能在一支急救 U 盘或者借来的笔记本上跑它，那里未必能 `pip install`。

```
dmi-rescue scan     dump.bin                  这个镜像里有什么？
dmi-rescue diff     backup.bin current.bin    逐字段对比
dmi-rescue splice   backup.bin current.bin -o fixed.bin
dmi-rescue restore  backup.bin current.bin -o usb/
dmi-rescue verify   backup.bin fixed.bin
dmi-rescue journal  dump.bin                  谁在什么时候写了哪个字段
```

## 什么情况用得上

刷坏了 → CH341A 救回来 → 刷了官方 BIOS → 机器能开机了，但 BIOS 里产品名显示
`INVALID`、底板型号没了、Windows 掉激活、`Win32_ComputerSystemProduct.UUID` 空的。

这些数据**不是明文**，`grep` 序列号什么都搜不到。它们存在 `LENV` 块里：XOR 混淆、
带校验和、双份冗余，而且偏移量随 BIOS 版本变化。

但**你刷坏之前那份备份里，这些东西都还在**。

这个工具就是把它搬回去——你想用哪种方式都行。

## 两种做法

| | `splice`（编程器） | `restore`（UEFI 脚本） |
|---|---|---|
| 要拆机 | **要** | 不用 |
| 要 CH341A | 要 | 不用 |
| 结果 | 与备份逐字节一致 | 所有可写字段与备份一致 |
| 内部变量（无 LVAR 标志） | 能恢复 | **恢复不了** |
| 风险 | 写挂变砖 | 写错值 |
| 耗时 | 约 20 分钟 | 约 10 分钟 |

要「一模一样」且本来就要拆机 → 用 `splice`。
机器能开机、不想拆 → 用 `restore`，大多数情况是这个。

两种都基于同一份分析，所以**先跑 `diff`** 看清情况。

## 快速开始

```bash
git clone https://github.com/Emilelu/lenovo-dmi-rescue
cd lenovo-dmi-rescue
PYTHONPATH=src python -m lenovo_dmi_rescue scan dump.bin
```

### 1. 看现状

```
$ dmi-rescue scan current.bin
LDBG   @ 0x75A000   write_offset=0x300   xor=0x43
LENV1  @ 0x75C000   gen=31    entries=16  xor=0x43   checksum=OK   <- active
LENV2  @ 0x75D000   gen=30    entries=15  xor=0x43   checksum=OK
region 0x75A000 - 0x75E000 (16384 bytes)
```

### 2. 和备份对比

```
$ dmi-rescue diff backup.bin current.bin

  type    LVAR        field                  status    donor -> target
  0x0200  /mt         MTM                    WRITE     9999ABCD1 -> (absent)
  0x0500  /uu         UUID                   WRITE     ... -> (absent)
  0x0011  -           internal flag          NO WAY    FE -> FF

  restorable : 11     already ok : 2     impossible : 1
```

`NO WAY` = 备份里有、但**没有任何写入途径**的字段，见下文「已知限制」。

### 3a. 方案 A —— 生成可刷镜像

```
$ dmi-rescue splice backup.bin current.bin -o fixed.bin

  DMI region        : 0x75C000 - 0x75E000  (8192 bytes)
  bytes changed     : 8153
  changes outside region: 0  (clean)
```

最后那行是关键：**只要有一字节改到了 DMI 区之外，工具就会报错并告诉你不许刷**。
你的 NVRAM、Wi-Fi 配对记录、启动历史原样保留——这正是「整片刷旧备份」会搞砸的地方。

刷完用 `dmi-rescue verify backup.bin fixed.bin` 复核。

### 3b. 方案 B —— 生成 U 盘

```
$ dmi-rescue restore backup.bin current.bin -o /media/usb/dmi
```

| 文件 | 用途 |
|---|---|
| `read_first.nsh` | 只读，不动任何东西，显示当前所有值 |
| `restore_dmi.nsh` | 写入所有缺失 / 不符的字段 |
| `msdm.bin` | OA3 数据，只在需要恢复时才生成 |
| `README.txt` | 放在 U 盘里的说明 |

U 盘启动 → 进 UEFI Shell → 跑脚本即可。

生成器处理了两个会**安静地搞坏** `.nsh` 的坑：

* **纯 ASCII + CRLF。** UTF-8 注释在 GBK 控制台下会把 CRLF 的 CR 吃掉，
  两行命令粘成一行。
* **`echo` 行里不能出现 `/xxx`。** Shell 把 `/mfgmode` 当未知 flag，
  **整行输出被丢弃**——提示文字正好在操作者需要它的时候消失。
  生成器会剥掉斜杠，并拒绝输出仍含斜杠的文件。

### 附赠：谁在什么时候写的

`LDBG` 是追加式日志，每条记录带时间戳，能分辨「出厂数据」和「某个修复工具后来写的」：

```
$ dmi-rescue journal current.bin

  2026-09-23 20:56:13  0x02  0x0400  Lenovo SN
  2026-09-23 20:56:17  0x02  0x0200  MTM
  2026-09-23 20:56:20  0x02  0x0000  Product Name
```

五个字段在 15 秒内依次写入——这是工具跑的，不是产线。

## 已知限制

**内部标志 `0x0011` 没有任何软件恢复途径。**

工厂值 `0xFE`，整片重刷后变 `0xFF`。它不映射任何 LVAR 标志、不出现在任何 SMBIOS
字段、日志里也没有引用。在真机上从三个方向确认过：与 MFG 模式变量的生命周期无关、
唯一没测过的候选 flag 在那块主板上根本不存在、24 个 flag 里没有一个指向它。

要还原这一个字节，只能上编程器。

**部分 LVAR 标志在某些机型上不存在。** 某台 Legion R7000 2020 上，24 个 flag 里有
9 个返回 `Not found`。`restore` 会如实报告，而不是生成一条注定失败的命令。

**MFG MODE 不在处理范围内。** BIOS 里安全处理器那一行显示 `MFG MODE` 而不是
Enabled/Disabled，属于固件 provision 问题，用户层面无解。
详见 [`docs/mfg-mode.md`](docs/mfg-mode.md)。

## 实测环境

| 机型 | BIOS | 做了什么 |
|---|---|---|
| Lenovo Legion R7000 2020 (82B6) | EUCN41WW | 完整 DMI 恢复，两种方案都跑过 |

解析器是围绕**结构**写的，不是硬编码偏移，理论上能泛化到其他 InsydeH2O 联想机型。
欢迎提交其他机型的实测结果。

## 安全须知

* **备份都留着。** `splice` 只写新文件、不动输入，但你的原始镜像应当视为不可再生。
* **不要整片刷旧备份。** 只搬 DMI 区。整片刷会把旧 NVRAM、过期的启动变量、
  Wi-Fi 配对记录和崩溃历史一起灌进去。
* **改 UUID 和底板字段会让 Windows 掉激活。** 它们参与微软的激活硬件哈希。
  重新激活只要几秒：

  ```powershell
  $p = Get-CimInstance -Namespace root\cimv2 -ClassName SoftwareLicensingProduct |
       Where-Object { $_.PartialProductKey -eq '3V66T' -and $_.Name -like '*Windows(R), Professional edition*' }
  Invoke-CimMethod -InputObject $p -MethodName Activate
  ```

* **永远不要把固件 dump 提交进仓库。** 里面有序列号、UUID、产品密钥、Wi-Fi 配对。
  `.gitignore` 已经拦掉常见命名，测试套件只用合成镜像。

## 许可

MIT，见 [LICENSE](LICENSE)。与联想无隶属关系，这是给自有硬件的维修工具。
