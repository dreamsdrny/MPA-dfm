"""执行进度动画：方框内点阵，未执行为灰点，执行中按真实阶段累计填绿点。

填充比例只来自已完成阶段和调用方给出的真实进度，不靠“猜测耗时”自动前进；
当前阶段没有可测进度时只脉冲提示，不会偷偷把点填满，避免界面把等待说成完成。
"""
import time
import tkinter as tk

from src.ui.theme import COLORS

IDLE = "idle"
RUNNING = "running"
COMPLETE = "complete"
PARTIAL = "partial"
ERROR = "error"


class ProgressMatrix(tk.Canvas):
    def __init__(self, master, columns=16, rows=6, width=288, height=104, **kwargs):
        super().__init__(master, width=width, height=height, highlightthickness=0,
                         bg=COLORS["card"], **kwargs)
        self.columns, self.rows = columns, rows
        self.total = columns * rows
        self.state = IDLE
        self.stages = []
        self.active = -1
        self.completed = set()
        self.stage_fraction = None
        self.label = ""
        self._target = 0.0
        self._display = 0.0
        self._pulse = 0.0
        self._started = 0.0
        self._job = None
        self.bind("<Destroy>", self._destroyed, add="+")
        self._draw()

    # ------------------------------------------------------------------ 接口
    def set_stages(self, stages):
        self.stages = [str(row) for row in stages]
        self.completed = set()
        self.active = -1
        self.stage_fraction = None
        self._recompute()
        self._draw()

    def start(self, stages=None, label=""):
        if stages is not None:
            self.set_stages(stages)
        self.state = RUNNING
        self.active = 0
        self.stage_fraction = None
        self.label = label or (self.stages[0] if self.stages else "执行中")
        self._started = time.monotonic()
        self._recompute()
        self._tick()

    def advance(self, stage=None, label=""):
        """标记当前阶段已完成并进入下一阶段；stage 必须在阶段清单内。"""
        if not self.stages:
            self.state = RUNNING
            return
        if stage is None:
            index = min(self.active + 1, len(self.stages) - 1)
        elif stage in self.stages:
            index = self.stages.index(stage)
        else:
            return  # 未知阶段不推进，避免动画比真实工作更“像完成了”
        self.completed.update(self.stages[:index])
        self.active = index
        self.stage_fraction = None
        self.state = RUNNING
        self.label = label or self.stages[index]
        self._recompute()
        self._tick()

    def set_fraction(self, fraction):
        """当前阶段内的真实完成比例（0~1）；None 表示不可测，只做脉冲。"""
        self.stage_fraction = None if fraction is None else max(0.0, min(1.0, float(fraction)))
        self._recompute()
        self._draw()

    def finish(self, state=COMPLETE, label=""):
        self.state = state if state in (COMPLETE, PARTIAL, ERROR) else COMPLETE
        self.completed = set(self.stages)
        self.active = max(len(self.stages) - 1, 0)
        self.stage_fraction = 1.0
        self.label = label or {"complete": "本轮执行完成", "partial": "执行完成，仍有资料缺口",
                               "error": "执行失败"}[self.state]
        self._recompute()
        if self.state == COMPLETE:
            self._display = self._target
        self._stop()  # 结束态不再空转动画
        self._draw()

    def reset(self, label="未执行"):
        self.state = IDLE
        self.completed = set()
        self.active = -1
        self.stage_fraction = None
        self.label = label
        self._target = self._display = 0.0
        self._stop()
        self._draw()

    def snapshot(self):
        return dict(state=self.state, target=round(self._target, 4), display=round(self._display, 4),
                    filled=self._filled(), total=self.total, completed=len(self.completed),
                    stages=len(self.stages), label=self.label)

    # ------------------------------------------------------------------ 内部
    def _recompute(self):
        if self.state == IDLE:
            self._target = 0.0
        elif self.state in (COMPLETE, PARTIAL, ERROR):
            self._target = 1.0 if self.state == COMPLETE else max(0.05, len(self.completed) / max(len(self.stages), 1))
        elif not self.stages:
            self._target = 0.0
        else:
            done = len(self.completed) + (0.0 if self.stage_fraction is None else self.stage_fraction)
            self._target = max(0.0, min(1.0, done / len(self.stages)))

    def _filled(self):
        return int(round(self._display * self.total))

    def _tick(self):
        self._job = None
        if not self.winfo_exists() or self.state != RUNNING:
            return
        if self._display < self._target:
            self._display = min(self._target, self._display + max(.01, (self._target - self._display) * .25))
        self._pulse = (self._pulse + .18) % 6.283
        self._draw()
        self._job = self.after(90, self._tick)

    def _stop(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
        self._job = None

    def _colors(self):
        if self.state == IDLE:
            return COLORS["faint"], COLORS["border"]
        if self.state == ERROR:
            return COLORS["danger"], COLORS["danger"]
        if self.state == COMPLETE:
            return COLORS["success"], COLORS["success"]
        # PARTIAL：已完成部分仍是绿点，未闭合的缺口用黄点，避免把“有缺口”说成“全灰”。
        if self.state == PARTIAL:
            return COLORS["success"], COLORS["warn"]
        return COLORS["success"], COLORS["primary"]

    def _draw(self):
        self.delete("all")
        width, height = self.winfo_width() or int(self.cget("width")), self.winfo_height() or int(self.cget("height"))
        pad, text_space = 10, 22
        self.create_rectangle(2, 2, width - 2, height - 2 - text_space, outline=COLORS["border"], width=2)
        filled = self._filled()
        cell_w = (width - 2 * pad) / self.columns
        cell_h = (height - 2 * pad - text_space) / self.rows
        radius = min(cell_w, cell_h) * .32
        active, edge = self._colors()
        for index in range(self.total):
            row, column = divmod(index, self.columns)
            cx = pad + column * cell_w + cell_w / 2
            cy = pad + row * cell_h + cell_h / 2
            if index < filled:
                color = active
                size = radius
                if self.state == RUNNING and index >= filled - max(1, self.columns // 4):
                    size = radius * (1 + .25 * abs(self._pulse % 3.141 - 1.57))
            else:
                # 未执行=灰；有缺口=黄，让“没做完”和“没开始”在图上一眼可分。
                color, size = (COLORS["warn"] if self.state == PARTIAL else COLORS["faint"], radius * .78)
            self.create_oval(cx - size, cy - size, cx + size, cy + size, fill=color, outline="")
        text = self.label or {"idle": "未执行", "running": "执行中", "complete": "完成",
                              "partial": "有缺口", "error": "失败"}[self.state]
        self.create_text(pad, height - 8, anchor="w", text=text, fill=COLORS["muted"])
        self.create_text(width - pad, height - 8, anchor="e",
                         text=f"{filled}/{self.total} 点", fill=COLORS["faint"])

    def _destroyed(self, event):
        if event.widget is self:
            self._stop()
