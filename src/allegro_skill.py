"""生成 Allegro SKILL 提取脚本 (元件坐标 / 孔位坐标)。

.brd 文件本身不含位号坐标和钻孔坐标, 这两类数据只能由 PCB Editor 从设计
数据库里取出。本模块生成一套"自检式"脚本, 在 PCB Editor 里跑一次即可:

    evan_pick.il     SKILL 函数: 导出元件坐标、过孔/通孔坐标、板框范围
    evan_pick.scr    Allegro 脚本: 载入 .il 并调用, 适合 File > Script 直接跑
    README.txt       三步操作说明 + 出错怎么办

导出格式 (供 src/pick.py 读取):
    evan_components.csv   REFDES,X_DBU,Y_DBU,ROTATION,SIDE
    evan_holes.csv        KIND,PADSTACK,X_DBU,Y_DBU,NET,REFDES
    evan_pads.csv         KIND,PADSTACK,X_DBU,Y_DBU,W_DBU,H_DBU,SIDE,NET,REFDES,PIN
                          (每个引脚的焊盘外框尺寸, 用于逐器件统计焊盘大小与引脚间距)
    evan_traces.csv       KIND,LAYER,WIDTH_DBU,X1_DBU,Y1_DBU,X2_DBU,Y2_DBU,NET
                          (每段走线; 供「网络定位」按网络名查坐标/外框长宽)
    evan_pick.log         每一步的条数与错误信息 (SKILL 语法/属性名问题都记在这)

坐标以 DBU 整数原样输出, 首行写明 UNITS,<单位>,<精度>,<每单位DBU>,
换算由 Python 侧做, 避免 SKILL 里的单位换算误差。
"""
import os
import sys
from typing import Dict

_IL_TEMPLATE = r''';;; evan_pick.il - 导出元件坐标 / 孔位坐标 / 焊盘外框, 供 Evan_BRD 生成定位清单与焊盘几何分析
;;; 用法 (PCB Editor 命令窗口):
;;;    skill load("{il_path}")
;;;    evan_pick_all("{out_dir}")
;;; 生成: evan_components.csv / evan_holes.csv / evan_pads.csv / evan_traces.csv / evan_pick.log
;;; 说明: 坐标按 DBU 整数输出, 首行为 UNITS,<单位>,<精度>,<每单位DBU>, 由 Python 换算。

(defun evan_num (v dflt)
  (if (numberp v) v dflt))

(defun evan_log (lg msg)
  (fprintf lg "%s\n" msg))

(defun evan_pick_components (fp d)
  (let ((n 0) (xy nil) (mir nil) (rot nil) (tmp nil))
    (fprintf fp "REFDES,X_DBU,Y_DBU,ROTATION,SIDE\n")
    (foreach c d->components
      (setq xy nil mir nil rot nil)
      (setq tmp (errset (c->xy)))
      (when tmp (setq xy (car tmp)))
      (setq tmp (errset (c->isMirrored)))
      (when tmp (setq mir (car tmp)))
      (when (null mir)
        (setq tmp (errset (c->mirror)))
        (when tmp (setq mir (car tmp))))
      (setq tmp (errset (c->rotation)))
      (when tmp (setq rot (car tmp)))
      (when (and (listp xy) (numberp (car xy)) (numberp (cadr xy)))
        (fprintf fp "%s,%.0f,%.0f,%.1f,%s\n"
                 c->name (car xy) (cadr xy) (evan_num rot 0.0)
                 (if mir "BOTTOM" "TOP"))
        (setq n (add1 n))))
    n))

(defun evan_pick_vias (fp d lg)
  (let ((n 0) (xy nil) (ps nil) (net nil) (tmp nil) (vl nil) (psn nil) (netn nil))
    (setq tmp (errset (d->vias)))
    (when tmp (setq vl (car tmp)))
    (when (null vl)
      (evan_log lg "WARN: d->vias 为空或不可用, 过孔坐标将缺失 (可在 Find 过滤里改用 axlSelect 方案)")
      (setq vl nil))
    (foreach v vl
      (setq xy nil ps nil net nil psn "" netn "")
      (setq tmp (errset (v->xy)))
      (when tmp (setq xy (car tmp)))
      (setq tmp (errset (v->padstack)))
      (when tmp (setq ps (car tmp)))
      (when ps
        (setq tmp (errset (ps->name)))
        (when tmp (setq psn (car tmp))))
      (setq tmp (errset (v->net)))
      (when tmp (setq net (car tmp)))
      (when net
        (setq tmp (errset (net->name)))
        (when tmp (setq netn (car tmp))))
      (when (and (listp xy) (numberp (car xy)) (numberp (cadr xy)))
        (fprintf fp "VIA,%s,%.0f,%.0f,%s,\n" psn (car xy) (cadr xy) netn)
        (setq n (add1 n))))
    (evan_log lg (sprintf "vias: %d" n))
    n))

(defun evan_pick_pins (fp d lg)
  (let ((n 0) (xy nil) (ps nil) (net nil) (comp nil) (tmp nil) (pl nil)
        (psn "") (netn "") (ref ""))
    (setq tmp (errset (d->pins)))
    (when tmp (setq pl (car tmp)))
    (foreach p pl
      (setq xy nil ps nil net nil comp nil psn "" netn "" ref "")
      (setq tmp (errset (p->xy)))
      (when tmp (setq xy (car tmp)))
      (setq tmp (errset (p->padstack)))
      (when tmp (setq ps (car tmp)))
      (when ps
        (setq tmp (errset (ps->name)))
        (when tmp (setq psn (car tmp))))
      (setq tmp (errset (p->net)))
      (when tmp (setq net (car tmp)))
      (when net
        (setq tmp (errset (net->name)))
        (when tmp (setq netn (car tmp))))
      (setq tmp (errset (p->component)))
      (when tmp (setq comp (car tmp)))
      (when comp
        (setq tmp (errset (comp->name)))
        (when tmp (setq ref (car tmp))))
      (when (and (listp xy) (numberp (car xy)) (numberp (cadr xy)))
        (fprintf fp "PIN,%s,%.0f,%.0f,%s,%s\n" psn (car xy) (cadr xy) netn ref)
        (setq n (add1 n))))
    (evan_log lg (sprintf "pins: %d" n))
    n))

(defun evan_pin_box (p)
  ;; 引脚焊盘外框 -> (宽 高) ; 取不到返回 nil
  ;; 不同版本对 bBox 的支持不一样, 所以先用 errset 试, 失败不报错只记日志。
  (let ((b nil) (tmp nil) (w nil) (h nil))
    (setq tmp (errset (p->bBox)))
    (when tmp (setq b (car tmp)))
    (when (and (listp b) (numberp (car b)) (numberp (caddr b)))
      (setq w (abs (- (caddr b) (car b))))
      (setq h (abs (- (cadddr b) (cadr b))))
      (when (and (numberp w) (numberp h) (> w 0) (> h 0))
        (list w h)))))

(defun evan_pick_pads (fp d lg)
  ;; 导出每个引脚的焊盘外框 (W/H), 供 Python 侧算焊盘尺寸与引脚间距
  (let ((n 0) (nobox 0) (pl nil) (tmp nil) (xy nil) (ps nil) (psn "")
        (comp nil) (ref "") (net nil) (netn "") (side "TOP") (bb nil)
        (pin "") (w 0) (h 0))
    (setq tmp (errset (d->pins)))
    (when tmp (setq pl (car tmp)))
    (foreach p pl
      (setq xy nil ps nil psn "" comp nil ref "" net nil netn "" side "TOP"
            bb nil pin "" w 0 h 0)
      (setq tmp (errset (p->xy)))
      (when tmp (setq xy (car tmp)))
      (setq tmp (errset (p->padstack)))
      (when tmp (setq ps (car tmp)))
      (when ps
        (setq tmp (errset (ps->name)))
        (when tmp (setq psn (car tmp))))
      (setq tmp (errset (p->number)))
      (when tmp (setq pin (car tmp)))
      (setq tmp (errset (p->net)))
      (when tmp (setq net (car tmp)))
      (when net
        (setq tmp (errset (net->name)))
        (when tmp (setq netn (car tmp))))
      (setq tmp (errset (p->component)))
      (when tmp (setq comp (car tmp)))
      (when comp
        (setq tmp (errset (comp->name)))
        (when tmp (setq ref (car tmp)))
        (setq tmp (errset (comp->isMirrored)))
        (when tmp
          (when (car tmp) (setq side "BOTTOM"))))
      (setq bb (evan_pin_box p))
      (when (and (listp xy) (numberp (car xy)) (numberp (cadr xy)))
        (if bb
          (progn
            (setq w (car bb))
            (setq h (cadr bb))
            (fprintf fp "PIN,%s,%.0f,%.0f,%.0f,%.0f,%s,%s,%s,%s\n"
                     psn (car xy) (cadr xy) w h side netn ref
                     (if (stringp pin) pin ""))
            (setq n (add1 n)))
          (setq nobox (add1 nobox)))))
    (when (> nobox 0)
      (evan_log lg (sprintf "WARN: %d 个引脚取不到焊盘外框 (p->bBox 不可用), 这些引脚只出现在 evan_holes.csv 里" nobox)))
    (evan_log lg (sprintf "pads: %d" n))
    n))

(defun evan_pick_traces (fp d lg)
  ;; 导出每段走线 (cline segment) 的网络/层/线宽/两端坐标, 供 Python 侧按网络定位
  (let ((n 0) (ncline 0) (cl nil) (tmp nil) (c nil) (netobj nil) (netn "")
        (lyr "") (w 0) (segs nil) (seg nil) (se nil) (p1 nil) (p2 nil))
    (fprintf fp "KIND,LAYER,WIDTH_DBU,X1_DBU,Y1_DBU,X2_DBU,Y2_DBU,NET\n")
    (setq tmp (errset (d->clines)))
    (when tmp (setq cl (car tmp)))
    (when (null cl)
      (evan_log lg "WARN: d->clines 为空或不可用, 走线坐标将缺失 (不影响引脚/过孔)"))
    (foreach c cl
      (setq netobj nil netn "" lyr "" w 0 segs nil)
      (setq tmp (errset (c->net)))
      (when tmp (setq netobj (car tmp)))
      (when netobj
        (setq tmp (errset (netobj->name)))
        (when (and tmp (car tmp)) (setq netn (car tmp))))
      (when (not (stringp netn)) (setq netn ""))
      (setq tmp (errset (c->layer)))
      (when tmp (setq lyr (car tmp)))
      (when (and lyr (not (stringp lyr)))
        (setq tmp (errset (lyr->name)))
        (when (and tmp (car tmp)) (setq lyr (car tmp))))
      (when (not (stringp lyr)) (setq lyr "UNKNOWN"))
      (setq tmp (errset (c->width)))
      (when (and tmp (numberp (car tmp))) (setq w (car tmp)))
      (setq tmp (errset (c->segments)))
      (when tmp (setq segs (car tmp)))
      (foreach seg segs
        (setq se nil)
        (setq tmp (errset (seg->startEnd)))
        (when tmp (setq se (car tmp)))
        (when (and (listp se) (cadr se)
                   (listp (car se)) (listp (cadr se)))
          (setq p1 (car se) p2 (cadr se))
          (fprintf fp "TRACE,%s,%.0f,%.0f,%.0f,%.0f,%.0f,%s\n"
                   lyr w (car p1) (cadr p1) (car p2) (cadr p2) netn)
          (setq n (add1 n))))
      (setq ncline (add1 ncline)))
    (evan_log lg (sprintf "traces: %d segments / %d clines" n ncline))
    n))

(defun evan_pick_all (outDir)
  (let ((dir outDir) (du nil) (unitName "millimeters") (acc 0) (dbu 1000)
        (d nil) (fp nil) (lg nil) (nComp 0) (nHole 0) (tmp nil) (bb nil))
    (setq d (axlDBGetDesign))
    (setq tmp (errset (axlDBGetDesignUnits)))
    (when tmp
      (setq du (car tmp))
      (when (listp du)
        (when (car du) (setq unitName (car du)))
        (when (cadr du) (setq acc (cadr du)))
        (when (caddr du) (setq dbu (caddr du)))))
    (setq lg (outfile (strcat dir "/evan_pick.log") "w"))
    (evan_log lg (strcat "Evan_BRD pick export  units=" unitName
                         " accuracy=" (sprintf "%d" acc)
                         " dbuperuu=" (sprintf "%d" dbu)))
    (when (null d)
      (evan_log lg "ERROR: axlDBGetDesign() 返回空, 请先打开 .brd")
      (close lg)
      (return nil))

    ;; 板框范围 (单独一行, 便于和 .brd 内嵌快照交叉核对)
    (setq tmp (errset (d->bBox)))
    (when tmp
      (setq bb (car tmp))
      (when (listp bb)
        (fprintf lg "bBox: %.0f %.0f %.0f %.0f\n"
                 (car bb) (cadr bb) (caddr bb) (cadddr bb))))

    ;; 元件
    (setq fp (outfile (strcat dir "/evan_components.csv") "w"))
    (fprintf fp "UNITS,%s,%d,%d\n" unitName acc dbu)
    (setq nComp (errset (evan_pick_components fp d)))
    (if nComp (setq nComp (car nComp)) (setq nComp 0))
    (close fp)
    (evan_log lg (sprintf "components: %d" nComp))

    ;; 孔位
    (setq fp (outfile (strcat dir "/evan_holes.csv") "w"))
    (fprintf fp "UNITS,%s,%d,%d\n" unitName acc dbu)
    (fprintf fp "KIND,PADSTACK,X_DBU,Y_DBU,NET,REFDES\n")
    (setq tmp (errset (evan_pick_vias fp d lg)))
    (when tmp (setq nHole (add1 nHole)))
    (setq tmp (errset (evan_pick_pins fp d lg)))
    (when tmp (setq nHole (add1 nHole)))
    (close fp)

    ;; 焊盘外框 (每个引脚的 W/H, 供逐器件统计焊盘大小与引脚间距)
    (setq fp (outfile (strcat dir "/evan_pads.csv") "w"))
    (fprintf fp "UNITS,%s,%d,%d\n" unitName acc dbu)
    (fprintf fp "KIND,PADSTACK,X_DBU,Y_DBU,W_DBU,H_DBU,SIDE,NET,REFDES,PIN\n")
    (setq tmp (errset (evan_pick_pads fp d lg)))
    (close fp)

    ;; 走线 (每段带 NET / 层 / 线宽 / 两端坐标, 供「网络定位」按网络名查位置与长宽)
    (setq fp (outfile (strcat dir "/evan_traces.csv") "w"))
    (fprintf fp "UNITS,%s,%d,%d\n" unitName acc dbu)
    (setq tmp (errset (evan_pick_traces fp d lg)))
    (close fp)

    (evan_log lg "DONE: 如果 components/pins 为 0, 说明属性名与本版本不符, 请把本日志发回")
    (close lg)
    (axlUIWPrint nil (strcat "Evan_BRD: 坐标已导出到 " dir))
    t))
'''

_README_TEMPLATE = '''Evan_BRD 坐标导出说明 (Allegro / OrCAD PCB Editor 17.x)
=========================================================

为什么需要这一步
----------------
.brd 里存的是约束、网络、层和钻孔表, **没有**器件位号坐标, 也没有每个孔的
坐标。这些数据只在 Allegro 的设计数据库里, 必须由 PCB Editor 通过 SKILL 取出来。
本目录下的 evan_pick.il 就是为此生成的脚本: 跑一次, 导出几个 CSV。

三步操作
--------
1. 用 PCB Editor 打开对应的 .brd (Export 需要设计数据库, 不要用只读的 Viewer)。

2. 在命令窗口 (Command >) 依次执行:

       skill load("{il_path}")
       evan_pick_all("{out_dir}")

   或者: File > Script... 选择 evan_pick.scr 直接运行(脚本里已写死本机路径)。

3. 回到 Evan_BRD:
   - 命令行:  dist\\Evan_BRD.exe --pick <brd> --pick-files "{out_dir}\\evan_components.csv" "{out_dir}\\evan_holes.csv" "{out_dir}\\evan_pads.csv"
   - 界面:    DFM 功能分析 > 执行分析 > 坐标定位(选择这几个 CSV)
   - 焊盘几何/间距: dist\\Evan_BRD.exe --analyze <brd> --analyze-only physical.pad_geometry drc.pad_clearance

会生成:
   evan_components.csv   位号 + 坐标(DBU) + 旋转 + 面
   evan_holes.csv        过孔/通孔焊盘: 焊盘栈 + 坐标(DBU) + 网络 + 位号
   evan_pads.csv         每个引脚的焊盘外框 W/H + 位号 + 焊盘栈 (焊盘尺寸与引脚间距)
   evan_traces.csv       每段走线的 NET + 层 + 线宽 + 两端坐标 (网络定位用)
   evan_pick.log         每一步的条数; 出错信息都在这里

按网络名定位 (Evan_DFM > 网络定位)
----------------------------------
.brd 内嵌快照只有图形, 没有网络归属, 所以坐标必须来自带 NET 的导出。
把上面几个 CSV 放在 .brd 同目录 (或 定位清单/allegro_bridge/) 后, 在
Evan_DFM 里点「网络定位」, 选网络即可看到该网络的 X/Y 范围、中心、外框长宽、
走线总长、线宽、层, 以及每个引脚/过孔/走线的坐标。
如果 evan_traces.csv 是空的, 看 evan_pick.log 里的 "traces:" 行和 WARN 提示。

焊盘几何怎么用
--------------
evan_pads.csv 里每个引脚一行: 焊盘外框 W/H 就是**焊盘尺寸**; 同一颗器件的
引脚坐标两两算中心距, 最小值就是**引脚间距 (pitch)**; 有了焊盘位置, 工具会
再去 .brd 的内嵌快照里量「焊盘到铜皮 / 焊盘到走线」的距离 (mm)。
如果日志里出现 "N 个引脚取不到焊盘外框 (p->bBox 不可用)", 说明本版本的
SKILL 不支持 p->bBox, 请把 evan_pick.log 发回来适配。

没有 Allegro 时的替代办法
-------------------------
* 只想要孔位: Manufacture > NC > NC Drill, 导出 Excellon (.drl),
  工具能直接读, 精度到每个孔。
* 只想要元件: File > Export > Placement, 得到 place_txt.txt, 工具也能读。

常见问题
--------
Q: 日志里 components: 0 ?
A: 属性名和你的版本不符。请把 evan_pick.log 发回来; 有时需要先打开设计再执行命令。

Q: 坐标数值很大 / 位置不对?
A: CSV 首行是 UNITS,<单位>,<精度>,<每单位DBU>, 工具会按它换算成 mm,
   不要手工改这一行。

Q: 想按不符合项逐个跳转?
A: 工具的「定位清单」会额外生成 *_locate.scr (zoom center x y),
   在 PCB Editor 里 File > Script 运行即可逐个跳过去。
'''


def _short_path(path: str) -> str:
    """Windows 8.3 短路径 (纯 ASCII), 供 Allegro 脚本使用。

    带中文/空格的目录在 .scr 里用 UTF-8 路径时, 老版本 Allegro 会读错甚至报错;
    短路径是纯 ASCII, 兼容性最好。取不到时原样返回。
    """
    if sys.platform != "win32":
        return path
    try:
        import ctypes

        win_path = path.replace("/", "\\")
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(win_path, buf, 1024)
        win = buf.value if n and buf.value else win_path
        return win.replace("\\", "/")
    except Exception:  # noqa: BLE001
        return path


def write_bundle(out_dir: str) -> Dict[str, str]:
    """在 out_dir 写出 .il / .scr / README.txt, 返回各文件路径。"""
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    il_path = os.path.join(out_dir, "evan_pick.il")

    il_fwd = il_path.replace("\\", "/")
    out_fwd = os.path.abspath(out_dir).replace("\\", "/")

    paths: Dict[str, str] = {}
    with open(il_path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(_IL_TEMPLATE.format(il_path=il_fwd, out_dir=out_fwd))
    paths["il"] = il_path

    scr = os.path.join(out_dir, "evan_pick.scr")
    scr_text = (f'skill load("{_short_path(il_fwd)}")\n'
                f'skill evan_pick_all("{_short_path(out_fwd)}")\n'
                "exit\n")
    try:
        scr_text.encode("ascii")
        encoding = "ascii"
    except UnicodeEncodeError:
        # 短路径也拿不到时退回系统 ANSI (中文 Windows 是 GBK), 避免直接崩在写文件上
        encoding = "mbcs" if sys.platform == "win32" else "utf-8"
    with open(scr, "w", encoding=encoding, errors="replace", newline="\r\n") as f:
        f.write(scr_text)
    paths["scr"] = scr

    readme = os.path.join(out_dir, "README_坐标导出.txt")
    with open(readme, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(_README_TEMPLATE.format(il_path=il_fwd, out_dir=out_fwd))
    paths["readme"] = readme
    return paths
