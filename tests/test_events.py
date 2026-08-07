import asyncio
import threading
import unittest

from sbc.core.events import EventEmitter


class EventEmitterTests(unittest.TestCase):
    def test_priority_filter_once_and_off(self):
        emitter = EventEmitter()
        received = []

        @emitter.on("example", priority=1)
        def first(value):
            received.append(("first", value))

        @emitter.once("example", when=lambda value: value > 1)
        def one_shot(value):
            received.append(("once", value))

        def skipped(value):
            received.append(("skipped", value))

        emitter.on("example", skipped, when=lambda _: False)
        emitter.emit("example", 1)
        emitter.emit("example", 2)
        emitter.emit("example", 3)

        self.assertEqual(
            received,
            [("first", 1), ("first", 2), ("once", 2), ("first", 3)],
        )
        self.assertEqual(emitter.off("example", first), 1)
        self.assertEqual(emitter.off("example"), 1)

    def test_async_and_failing_handlers_do_not_block_other_handlers(self):
        emitter = EventEmitter()
        finished = threading.Event()
        received = []

        def fails(value):
            raise RuntimeError("expected test error")

        async def asynchronous(value):
            await asyncio.sleep(0)
            received.append(value)
            finished.set()

        emitter.on("example", fails)
        emitter.on("example", asynchronous)
        emitter.emit("example", "ok")

        self.assertTrue(finished.wait(1))
        self.assertEqual(received, ["ok"])
