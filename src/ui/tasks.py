"""Background work with queue delivery; workers never access Tk objects.

All inputs must be captured before submit(). Completion handlers run in the Tk
thread and should validate their input revision before displaying results.
"""
import queue
import threading


class TaskRunner:
    def __init__(self, widget, on_busy=None):
        self.widget = widget
        self.on_busy = on_busy
        self.pending = {}
        self.queue = queue.Queue()
        self.alive = True
        self.poll_id = widget.after(50, self._poll)
        widget.bind("<Destroy>", self._destroyed, add="+")

    def submit(self, key, work, done, failed, finished=None):
        if not self.alive or key in self.pending:
            return False
        self.pending[key] = (done, failed, finished)
        self._notify()

        def execute():
            try:
                value = work()
            except Exception as exc:
                self.queue.put((key, False, str(exc)))
            else:
                self.queue.put((key, True, value))

        threading.Thread(target=execute, name=f"DFM-{key}", daemon=True).start()
        return True

    def _notify(self):
        if self.on_busy:
            self.on_busy(tuple(self.pending))

    def _poll(self):
        if not self.alive:
            return
        self.poll_id = None
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
        self.alive = False
        if self.poll_id is not None:
            try:
                self.widget.after_cancel(self.poll_id)
            except Exception:
                pass
            self.poll_id = None
        self.pending.clear()
