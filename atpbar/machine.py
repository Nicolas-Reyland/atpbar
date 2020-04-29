# Tai Sakuma <tai.sakuma@gmail.com>
import threading
import multiprocessing

from .reporter import ProgressReporter
from .pickup import ProgressReportPickup
from .presentation.create import create_presentation
from .misc import in_main_thread

##__________________________________________________________________||
class StateMachine:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = Initial()

    def find_reporter(self):
        with self.lock:
            self.state = self.state.prepare_reporter()
        return self.state.reporter

    def register_reporter(self, reporter):
        self.state = self.state.register_reporter(reporter)

    def flush(self):
        with self.lock:
            self.state = self.state.flush()

    def disable(self):
        self.state = self.state.disable()

    def shutdown(self):
        with self.lock:
            self.state = self.state.shutdown()

    def fetch_reporter(self):
        with self.lock:
            self.state = self.state.prepare_reporter()
        yield from self.state.fetch_reporter(lock=self.lock)

##__________________________________________________________________||
class State:
    """The base class of the states
    """
    def prepare_reporter(self):
        return self

    def register_reporter(self, reporter):
        return Registered(reporter=reporter)

    def disable(self):
        return Disabled()

    def fetch_reporter(self, lock):
        yield None

    def flush(self):
        return self

    def shutdown(self):
        return self

##__________________________________________________________________||
class Initial(State):
    """Initial state

    The pickup is not running.
    """

    def __init__(self, reporter=None, queue=None):
        self.reporter = reporter
        self.queue = queue

    def prepare_reporter(self):
        return Started(reporter=self.reporter, queue=self.queue)

    def fetch_reporter(self, lock):
        yield self.reporter

    def flush(self):
        return Started(reporter=self.reporter, queue=self.queue)

class Started(State):
    """Started state

    The pickup started and is running, typically, in the main process
    """
    def __init__(self, reporter=None, queue=None):
        self.reporter = reporter
        self.queue = queue
        self.pickup = None
        self.reporter_yielded = False
        self.to_restart_pickup = True

        if self.reporter is None:
            if self.queue is None:
                self.queue = multiprocessing.Queue()
            self.reporter = ProgressReporter(queue=self.queue)

        self._start_pickup()

    def _start_pickup(self):
        presentation = create_presentation()
        self.pickup = ProgressReportPickup(self.queue, presentation, self.detach)
        self.pickup.start()

    def _end_pickup(self):
        self.queue.put(None)
        self.pickup.join()
        self.pickup = None

    def _restart_pickup(self):
        self._end_pickup()
        self._start_pickup()

    def fetch_reporter(self, lock):

        if not in_main_thread():
            yield self.reporter
            return

        if self.reporter_yielded:
            # called from an inner loop
            yield self.reporter
            return

        self.reporter_yielded = True
        self.to_restart_pickup = True

        try:
            yield self.reporter
        finally:
            self.reporter_yielded = False
            if not self.to_restart_pickup:
                return
            with lock:
                self._restart_pickup()

    def detach(self):
        """detach the pickup

        This method is given to the pickup. The pickup calls this method when
        the pickup receives a report from a sub-thread or a sub-process.

        The method fetch_reporter() yields the reporter. While yielding the
        reporter from the main thread of the main process if fetch_reporter()
        is called again from the main thread of the main process, atpbar is
        used in nested loops. The fetch_reporter() restarts the pickup when the
        outermost loop has ended.

        If this method is called when yielding the reporter from the main
        thread of the main process, atpbar is used in a sub-thread or a
        sub-process. The fetch_reporter() doesn't restart the pickup because it
        cannot tell which loop ends last.
        """

        self.to_restart_pickup = False

    def flush(self):
        self._restart_pickup()
        return self

    def shutdown(self):
        self._end_pickup()
        return Initial(reporter=self.reporter, queue=self.queue)

##__________________________________________________________________||
class Registered(State):
    """Registered state

    Typically, in a sub-process. The reporter, which has been created
    in the main process, is registered in the sub-process
    """

    def __init__(self, reporter):
        self.reporter = reporter

    def fetch_reporter(self, lock):
        yield self.reporter

class Disabled(State):
    """Disabled state
    """
    def __init__(self):
        self.reporter = None

##__________________________________________________________________||
