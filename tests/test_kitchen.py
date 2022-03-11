import pytest
from typing import Any, Iterable
from kitchen import kitchen
from kitchen.config import KitchenConfig
from redis import Redis
from time import sleep
from xprocess import ProcessStarter

redis_host = "localhost"
redis_port = 7880


@pytest.fixture(autouse=True)
def redis(xprocess):
    class Starter(ProcessStarter):
        pattern = "Ready to accept connections"
        max_read_lines = 1000
        terminate_on_interrupt = True
        args = ["redis-server", f"--port {redis_port}"]

    xprocess.ensure("redis", Starter)
    yield
    xprocess.getinfo("redis").terminate()


@pytest.mark.slow_integration_test
class TestKitchen:
    name = "kitchen_test"

    robots_reliability: dict[str, float] = {}
    robots_seconds_per_action: dict[str, float] = {
        "take": 1,
        "sauce": 2,
        "cheese": 2,
        "to_oven": 1,
        "bake": 8,
        "from_oven": 1,
        "slice": 2,
        "pack": 2,
        "put": 1,
    }
    robots_time_variations: tuple[float, float] = (1.0, 1.0)

    def build_service(
        self,
        manager_commands: Iterable[tuple[str, Any]] = [],
    ):
        kitchen_config = KitchenConfig("tests/config_good.yaml")
        service = kitchen.Kitchen(
            kitchen_config,
            self.robots_reliability,
            self.robots_seconds_per_action,
            self.robots_time_variations,
            manager_commands,
        )
        return service

    @property
    def max_busy_time(self):
        return sum(self.robots_seconds_per_action.values()) * self.robots_time_variations[1]

    @property
    def timeout(self):
        return self.max_busy_time * 1.1 + 1

    def test_single_pizza(self):
        pizza_amount = 1
        commands = (("order", pizza_amount),)

        service = self.build_service(manager_commands=commands)

        try:
            service(self.name, redis_host, redis_port)
            sleep(self.timeout)
            service.shutdown()

            report_service = kitchen.KitchenReport()
            report_service("reporter", redis_host, redis_port)

            finished_orders = report_service.map_state_order["shelf"]
            assert len(finished_orders) == pizza_amount

        finally:
            service.shutdown()

    def test_two_pizzas(self):
        pizza_amount = 2
        commands = (("order", pizza_amount),)

        service = self.build_service(manager_commands=commands)

        try:
            service(self.name, redis_host, redis_port)
            sleep(self.timeout)
            service.shutdown()

            report_service = kitchen.KitchenReport()
            report_service("reporter", redis_host, redis_port)

            finished_orders = report_service.map_state_order["shelf"]
            assert len(finished_orders) == pizza_amount

        finally:
            service.shutdown()

    def test_many_pizzas(self):
        pizza_amount = 8
        commands = (("order", pizza_amount),)

        service = self.build_service(manager_commands=commands)

        try:
            service(self.name, redis_host, redis_port)
            sleep(self.timeout)
            service.shutdown()

            report_service = kitchen.KitchenReport()
            report_service("reporter", redis_host, redis_port)

            finished_orders = report_service.map_state_order.get("shelf", {})
            not_started_orders = report_service.map_state_order.get("freezer", {})

            # First pizzas shouldn't be affected by their higher amount:
            assert len(finished_orders) >= 2

            # At least two next two pizzas should have left the freezer:
            assert pizza_amount - len(not_started_orders) >= 4

        finally:
            service.shutdown()

    def test_8_robots(self):
        pizza_amount = 4
        commands = (("order", pizza_amount),)

        kitchen_config = KitchenConfig("tests/config_8_robots.yaml")
        service = kitchen.Kitchen(
            kitchen_config,
            self.robots_reliability,
            self.robots_seconds_per_action,
            self.robots_time_variations,
            commands,
        )

        try:
            service(self.name, redis_host, redis_port)
            sleep(self.timeout)
            service.shutdown()

            report_service = kitchen.KitchenReport()
            report_service("reporter", redis_host, redis_port)

            finished_orders = report_service.map_state_order["shelf"]

            # In the same time, having 2 ovens, 8 robots would still make
            # 2 pizzas
            assert len(finished_orders) == 2

        finally:
            service.shutdown()

    def test_broken_1_before_no_block(self):
        pizza_amount = 2
        commands = (
            ("break", 0),
            ("order", pizza_amount),
        )

        service = self.build_service(manager_commands=commands)

        try:
            service(self.name, redis_host, redis_port)
            sleep(self.timeout)
            service.shutdown()

            report_service = kitchen.KitchenReport()
            report_service("reporter", redis_host, redis_port)

            finished_orders = report_service.map_state_order.get("shelf", {})
            not_started_orders = report_service.map_state_order.get("freezer", {})

            # Exactly one pizza should make it in time, as not affected:
            assert len(finished_orders) == 1

            # At least one next pizza should have left the freezer:
            assert pizza_amount - len(not_started_orders) >= 2

        finally:
            service.shutdown()

    def test_broken_1_after_block(self):
        pizza_amount = 4
        commands = (
            ("break", 3),  # should break at from_oven
            ("order", pizza_amount),
        )

        service = self.build_service(manager_commands=commands)

        try:
            service(self.name, redis_host, redis_port)
            sleep(self.timeout)
            service.shutdown()

            report_service = kitchen.KitchenReport()
            report_service("reporter", redis_host, redis_port)

            baking_orders = report_service.map_state_order.get("baking", {})
            finished_orders = report_service.map_state_order.get("shelf", {})
            not_started_orders = report_service.map_state_order.get("freezer", {})

            # Exactly one pizza should make it in time, as not affected:
            assert len(finished_orders) == 1

            # All pizzas should have left the freezer:
            assert len(not_started_orders) == 0

            # Not more than one pizza should be at the oven.
            assert len(baking_orders) <= 1

        finally:
            service.shutdown()

    def test_broken_2_different_places(self):
        pizza_amount = 2
        commands = (
            ("break", 0),
            ("break", 3),  # should break at from_oven
            ("order", pizza_amount),
        )

        service = self.build_service(manager_commands=commands)

        try:
            service(self.name, redis_host, redis_port)
            sleep(self.timeout * 3)
            service.shutdown()

            report_service = kitchen.KitchenReport()
            report_service("reporter", redis_host, redis_port)

            finished_orders = report_service.map_state_order["shelf"]

            # Both pizzas should make it in triple time:
            assert len(finished_orders) == 2

        finally:
            service.shutdown()

    def test_broken_2_before(self):
        pizza_amount = 2
        commands = (
            ("break", 0),
            ("break", 1),
            ("order", pizza_amount),
        )

        service = self.build_service(manager_commands=commands)

        try:
            service(self.name, redis_host, redis_port)
            sleep(self.timeout * 2)
            service.shutdown()

            report_service = kitchen.KitchenReport()
            report_service("reporter", redis_host, redis_port)

            finished_orders = report_service.map_state_order.get("shelf", {})
            not_started_orders = report_service.map_state_order.get("freezer", {})

            # Not possible to finish any order even in double time:
            assert len(finished_orders) == 0

            # All orders returned to freezer, and no robot could take them:
            assert len(not_started_orders) == 2

        finally:
            service.shutdown()

    def test_broken_2_after(self):
        pizza_amount = 2
        commands = (
            ("break", 2),
            ("break", 3),
            ("order", pizza_amount),
        )

        service = self.build_service(manager_commands=commands)

        try:
            service(self.name, redis_host, redis_port)
            sleep(self.timeout * 2)
            service.shutdown()

            report_service = kitchen.KitchenReport()
            report_service("reporter", redis_host, redis_port)

            finished_orders = report_service.map_state_order.get("shelf", {})
            not_started_orders = report_service.map_state_order.get("freezer", {})

            # Not possible to finish any order even in double time:
            assert len(finished_orders) == 0

            # All orders returned to freezer, then served by active robots:
            assert len(not_started_orders) == 0

        finally:
            service.shutdown()
