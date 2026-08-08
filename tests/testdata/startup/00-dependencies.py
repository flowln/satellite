import subprocess

from bluesky import plan_stubs as bps, protocols
from bluesky.plans import count

subprocess.run(["pip", "install", "ophyd", "matplotlib"])

from ophyd.sim import hw

rand = hw().rand
rand.start_simulation()


def simple_plan(readable: protocols.Readable):
    yield from count([readable], num=1)


def this_is_not_a_plan():
    import time

    time.sleep(1.0)


def failing_plan():
    yield from bps.sleep(0.01)
    raise RuntimeError("This test always fails")


def good_stuck_plan():
    while True:
        yield from bps.sleep(0.5)


def bad_stuck_plan():
    yield from bps.sleep(0.01)

    import time

    while True:
        time.sleep(0.5)


def plan_with_various_runs():
    yield from bps.open_run()
    yield from bps.sleep(0.5)
    yield from bps.close_run()

    yield from bps.sleep(0.5)

    yield from bps.open_run()
    yield from bps.sleep(0.5)
    yield from bps.close_run()


from bluesky import RunEngine
from bluesky.callbacks.best_effort import BestEffortCallback

RE = RunEngine()

BEC = BestEffortCallback()
BEC.disable_plots()

RE.subscribe(BEC)
