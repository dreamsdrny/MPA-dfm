# COMP_XY 导入、坐标生成和丝印位置检查

## 已验证的输入格式

实际样本：`COMP_XY_S-860-000847_Rev_A.txt`。
最新程序：`dist/Evan_MPA_DFM_COMP_XY_SILK.exe`。
已生成可直接在 Allegro 中执行的脚本：`output/coordinate_scripts/evan_coordinates.scr`。

```text
VERSION = 2.0
UUNITS = MILS
# refdes ! symbol_x ! symbol_y ! rotation ! mirror ! symbol_name ! embedded_layer
R1 ! 1239.66 ! 4255.44 ! 270 ! m ! RESC1608X95N_NTC !
```

- `symbol_x/symbol_y` 是器件放置原点，按 UUNITS 换算，不平移/镜像坐标。
- `mirror=m` 代表 BOTTOM；空白代表 TOP；保留小数角度和封装名。
- 此样本共 4 个器件；R1 原点为 (31.487364,108.088176)mm，对应 (1239.66,4255.44)mil。

界面点 **导入 COMP_XY**，选择 TXT，即可在 **BRD 器件 X/Y** 页看坐标。
也可把 COMP_XY*.txt 放在 BRD 同目录或坐标来源目录；分析自动发现。
丝印坐标不能由器件原点推算，需下一节图形导出。

## 生成脚本

在项目根目录执行：

```powershell
python tools/generate_coordinates.py --brd "D:\PCB\board.brd" --out "D:\PCB\exports"
```

生成：

- `evan_pick.il`：器件/孔/焊盘/走线导出；新增兼容格式 `COMP_XY.txt`。
- `evan_geometry.il`：丝印文字、线段/圆弧、板框坐标导出。
- **`evan_coordinates.scr`**：执行以上两个导出模块的完整脚本。

Allegro 打开相同版本 BRD，在 **File > Script** 运行 `evan_coordinates.scr`。
若希望由脚本打开 Editor，加 `--launch`；可用 `--editor "C:\Cadence\SPB_17.4\tools\bin\allegro.exe"` 指定程序。

GUI 中“生成 Allegro 坐标脚本”也会生成这些文件；“打开 BRD”会自动执行完整导出。
无需另写手动的坐标换算脚本。

## 数据输出与判断

| 文件 | 用途 |
|---|---|
| COMP_XY.txt / evan_components.csv | 器件原点、面、旋转 |
| evan_holes.csv | 实际孔径、孔中心 |
| evan_pads.csv | 引脚外框（不是阻焊开窗） |
| evan_traces.csv | 线宽和端点 |
| evan_silkscreen.csv | 丝印对象中心/原点、层、外框、线宽、端点 |
| evan_outline.csv | 板框直线/圆弧 |

选导出目录后执行 PCB 分析，**问题 X/Y 坐标**页与 XLSX/CSV 报告增加丝印位置。
双击有坐标的记录可由已连接 Allegro 定位。

当前规则：

1. **丝印线宽**：非零实际线宽与所选板厂 min_line_width 比较，低于能力写 FAIL。
2. **丝印到成型线**：真实直线距离扣除丝印半线宽后与 min_to_outline 比较。直线丝印/直线板边为实测 FAIL；文字/圆弧外框是待复核 WARN。
3. **丝印靠近焊盘**：同面丝印外框与引脚外框的候选间隙，按板厂阻焊间隙要求筛查，写 WARN。实际阻焊开窗及旋转字形须 CAM 复核。
4. **器件关联**：给出同面最近器件名称，仅帮助找区域，不把最近器件当作该丝印的所属器件。

实际失败会同步更新制造结论与问题清单；候选待复核也不会直接放行。
圆弧板边未进行完整轮廓布尔运算；没有图形导出时不伪造丝印位置/通过结论。

## 验证记录

- 用户 847 COMP_XY 样本：4 个器件解析成功（角度、镜像、单位均保留）。
- 本机 Allegro 17.4 实际生成 COMP_XY：279 器件。
- 真实板图形导出：4761 个丝印对象、59 段板框。
- 制造检查读入以上导出并输出问题坐标；线宽示例 0.100076mm 对比红版常规 0.1524mm，形成 FAIL。
- pytest：61 项通过；新增测试覆盖样本格式、单位/镜像、线宽/板边、面别隔离、候选复核和结论更新。

生成坐标必须打开真实数据库并完成 Allegro 产品/许可选择。产品选择未完成时 GUI 会等待连接；已有 COMP_XY TXT 可直接导入，不需要重新打开 Editor。
