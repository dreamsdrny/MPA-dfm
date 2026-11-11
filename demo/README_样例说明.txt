这是 tools/make_demo_brd.py 合成的测试夹具, 不是真实板子。
用途: 在没有 Allegro 与真实 .brd 的环境里验证整条分析链路。

DEMO_BOARD.brd        4 层板, 100.16 x 80.01 mm, 49 个器件, 23 个网络
                      故意做了一对间距不足的走线 (TOP 2000/2006.3 mil)
                      另含 U7 (SOIC-8, 焊盘 1.5x0.6mm, 引脚间距 1.27mm)
                      与 R60 (0603) 焊盘, 以及配好的走线/铜皮轮廓
DEMO_BOARD_REV_B.brd  改动版: 少一层走线、约束收紧、板框变宽
place_txt.txt         Allegro Placement 导出样例 (器件坐标)
demo.drl              Excellon 钻孔样例 (16 个孔)
demo_drc.rpt          Allegro DRC 报告样例 (间距/短路/未连接)
pstxnet.dat           第三方网表样例
demo.ipc              IPC-D-356 样例 (4 个测试点)
evan_pads.csv         SKILL 焊盘外框样例 (U7/R60 的焊盘尺寸与引脚间距)
