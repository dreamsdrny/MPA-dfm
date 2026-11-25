"""Background work with queue delivery; workers never access Tk objects.

All inputs must be captured before submit(). Completion handlers run in the Tk
thread and should validate their input revision before displaying results.
"""
import queue
import gc

from src.infrastructure.tasks import get_executor

_gc_users = 0
_gc_was_enabled = False


class TaskRunner:
    def __init__(self, widget, on_busy=None):
        global _gc_users, _gc_was_enabled
        if not _gc_users:
            _gc_was_enabled = gc.isenabled()
            gc.disable()
        _gc_users += 1
        self.widget = widget
        self.on_busy = on_busy
        self.pending = {}
        self.queue = queue.Queue()
        self.releases = queue.Queue()
        self.alive = True
        self.poll_id = widget.after(50, self._poll)
        widget.bind("<Destroy>", self._destroyed, add="+")

    def submit(self, key, work, done, failed, finished=None):
        if not self.alive or key in self.pending:
            return False
        # Old Tk variable/widget cycles must be collected in the Tk thread,
        # not by automatic GC during an allocation-heavy geometry worker.
        gc.collect()
        self.pending[key] = (done, failed, finished)
        self._notify()

        if not get_executor().submit(key, work, self.queue, self.releases):
            self.pending.pop(key, None)
            self._notify()
            failed("后台任务队列已满，请等待当前任务完成后重试")
            if finished:
                finished()
            return False
        return True

    def _notify(self):
        if self.on_busy:
            self.on_busy(tuple(self.pending))

    def _poll(self):
        if not self.alive:
            return
        self.poll_id = None
        while not self.releases.empty():
            self.releases.get_nowait()
        # Bound work per tick so multiple completions do not starve GUI events.
        for _ in range(8):
            try:
                key, ok, value = self.queue.get_nowait()
            except queue.Empty:
                break
            callbacks = self.pending.pop(key, None)
            if not callbacks:
                continue
            done, failed, finished = callbacks
            try:
                (done if ok else failed)(value)
            except Exception:
                import sys
                self.widget._root().report_callback_exception(*sys.exc_info())
            finally:
                if self.alive:
                    try:
                        if finished:
                            finished()
                        self._notify()
                    except Exception:
                        import sys
                        self.widget._root().report_callback_exception(*sys.exc_info())
            if not self.alive:
                return
        self.poll_id = self.widget.after(50, self._poll)

    def _destroyed(self, event):
        if event.widget is self.widget:
            self.close()

    def close(self):
        global _gc_users
        if not self.alive:
            return
        self.alive = False
        if self.poll_id is not None:
            try:
                self.widget.after_cancel(self.poll_id)
            except Exception:
                pass
            self.poll_id = None
        self.pending.clear()
        _gc_users -= 1
        # Tcl objects in cycles must be finalized in the Tk thread. Do not
        # re-enable cyclic GC while outstanding workers can retain UI closures.
        gc.collect()
        if not _gc_users and not get_executor().jobs.unfinished_tasks and _gc_was_enabled:
            gc.enable()
