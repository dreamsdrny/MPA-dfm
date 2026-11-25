"""Bounded daemon workers. No Tk imports or callbacks executed in the UI thread."""
import queue
import threading


class TaskExecutor:
    def __init__(self, max_workers=4, max_pending=16):
        if max_workers < 1 or max_pending < 1:
            raise ValueError("任务资源上限必须为正数")
        self.max_workers = max_workers
        self.jobs = queue.Queue(maxsize=max_pending)
        self.lock = threading.Lock()
        self.started = False

    def submit(self, key, work, delivery, release_delivery=None):
        with self.lock:
            if not self.started:
                for index in range(self.max_workers):
                    threading.Thread(target=self._run, name=f"DFM-worker-{index}", daemon=True).start()
                self.started = True
            try:
                self.jobs.put_nowait((key, work, delivery, release_delivery))
            except queue.Full:
                return False
        return True

    def _run(self):
        while True:
            key, work, delivery, release_delivery = self.jobs.get()
            try:
                value = work()
            except BaseException as exc:  # noqa: BLE001  将 worker 异常转换为队列消息
                from src.diagnostics import log_exception
                log_exception("background task " + key, type(exc), exc, exc.__traceback__)
                delivery.put((key, False, str(exc)))
            else:
                delivery.put((key, True, value))
            finally:
                self.jobs.task_done()
                # Do not retain UI-bound closures while the worker blocks for
                # its next job. Ownership returns via the completion queue.
                if release_delivery is not None:
                    release_delivery.put(work)
                del work, delivery
                del release_delivery
                if "value" in locals():
                    del value


_executor = TaskExecutor()


def get_executor():
    return _executor


def configure_executor(max_workers, max_pending):
    global _executor
    if _executor.started:
        if (max_workers, max_pending) != (_executor.max_workers, _executor.jobs.maxsize):
            raise RuntimeError("已有后台任务，不能在运行中修改资源上限")
        return
    _executor = TaskExecutor(max_workers, max_pending)
