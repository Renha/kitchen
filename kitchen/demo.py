from __future__ import annotations

from .config import KitchenConfig
from .kitchen import Kitchen, KitchenReport

from subprocess import Popen, PIPE
from time import sleep

if __name__ == "__main__":
    redis_host = "localhost"
    redis_port = 7777
    run_time = 80

    # Quick and dirty way to launch redis-server, just enough for a simple demo:
    redis_launched = False
    redis_server = Popen(["redis-server", "--port", str(redis_port)], stdout=PIPE)
    try:
        if not redis_server.stdout is None:
            for line in redis_server.stdout:
                if "Ready to accept connections" in line.decode(encoding="utf8"):
                    redis_launched = True
                    break

        if redis_launched:
            print(f"Redis server started, host = {redis_host}, port = {redis_port}")
            config = KitchenConfig()
            robots_reliability = {
                "sauce": 0.97,
                "cheese": 0.95,
                "slice": 0.87,
                "pack": 0.90,
                "to_oven": 0.99,
                "from_oven": 0.99,
            }
            robots_seconds_per_action: dict[str, float] = {
                "take": 1,
                "sauce": 2,
                "cheese": 3,
                "to_oven": 1,
                "bake": 10,
                "from_oven": 1,
                "slice": 2,
                "pack": 3,
                "put": 1,
            }
            robots_time_variations: tuple[float, float] = (0.9, 1.2)
            manager_commands = [
                ("order", 2),
                ("sleep", 10),
                ("order", 5),
            ]
            kitchen = Kitchen(
                config,
                robots_reliability,
                robots_seconds_per_action,
                robots_time_variations,
                manager_commands,
            )
            try:
                kitchen(name="kitchen init", redis_host=redis_host, redis_port=redis_port)
                sleep(run_time)
            finally:
                kitchen.shutdown()

                report_builder = KitchenReport()
                report_builder("report", redis_host, redis_port)

                # Could be retrieved from report, but that would include orders manager
                # didn't have time to place:
                total_orders_amount = sum((v if c == "order" else 0 for c, v in manager_commands))

                if total_orders_amount:
                    print(f"Total orders amount: {total_orders_amount}. Report:")
                    print(repr(report_builder))
                else:
                    print(f"No orders, nothing to report")
    finally:
        redis_server.kill()
