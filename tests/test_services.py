import pytest
from functools import partial
from kitchen import kitchen
from kitchen.config import KitchenConfig
from redis import Redis
from time import sleep
from xprocess import ProcessStarter
from multiprocessing import Process
from typing import Any, Iterable

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


def service_builder(
    name: str, service_class: Any, args: list[Any] = [], kwargs: dict[str, Any] = {}
) -> tuple[Any, Process]:
    service = service_class(*args, **kwargs)
    configured_service = partial(service, name, redis_host, redis_port)
    process = Process(name=name, target=configured_service)
    return service, process


@pytest.fixture
def logger_service():
    return service_builder("log", kitchen.LoggerService)


class TestLoggerService:
    def test_creation(self):
        service = kitchen.LoggerService()
        assert not service is None

    def test_running(self, logger_service):
        _, process = logger_service
        try:
            process.start()
            sleep(0.1)
            assert process.is_alive()
        finally:
            process.terminate()

    def test_communication(self, logger_service):
        r = Redis(host=redis_host, port=redis_port, db=0)
        p = r.pubsub()

        p.subscribe("log")
        p.get_message(timeout=1)

        _, process = logger_service

        try:
            process.start()

            msg_start_log = p.get_message(timeout=1)
            assert not msg_start_log is None
            assert msg_start_log["type"] == "message"
            assert msg_start_log["channel"] == b"log"
            assert msg_start_log["data"] == b"log: started"

            sleep(0.1)
            subscribers_amount = r.publish("log", "hello mr. logger")
            assert subscribers_amount == 2
        finally:
            process.terminate()
            p.unsubscribe("log")

    def test_canary(self, logger_service):
        r = Redis(host=redis_host, port=redis_port, db=0)
        p = r.pubsub()

        p.subscribe("log")
        p.get_message(timeout=1)

        _, process = logger_service

        try:
            process.start()

            p.get_message(timeout=1)

            canary_period = 10
            sleep(canary_period + 1)

            msg_canary = p.get_message(timeout=1)
            assert not msg_canary is None
            assert msg_canary["type"] == "message"
            assert msg_canary["channel"] == b"log"
            assert msg_canary["data"] == b"log: still alive, nothing happened"

        finally:
            process.terminate()
            p.unsubscribe("log")


class TestQualityCamera:
    names = ["camera"]
    operations = ["calibration", "sleeping"]
    robot_ids = [10, 17, 90]
    order_ids = [1, 2, 3, 4]

    def build_camera(
        self, name_index: int = 0, operation_index: int = 0, robot_id_index: int = 0
    ) -> tuple[Any, Process]:
        return service_builder(
            self.names[name_index],
            kitchen.QualityCamera,
            [self.operations[operation_index], self.robot_ids[robot_id_index]],
        )

    def test_creation(self):
        service, _ = self.build_camera()
        assert not service is None

    def test_running(self):
        _, process = self.build_camera()
        try:
            process.start()
            sleep(0.1)
            assert process.is_alive()
        finally:
            process.terminate()

    def test_communication(self):
        r = Redis(host=redis_host, port=redis_port, db=0)
        p = r.pubsub()
        r.flushdb()

        p.subscribe("log")
        p.get_message(timeout=1)

        name = self.names[0]
        operation = self.operations[0]
        robot_id = self.robot_ids[0]
        order_id = self.order_ids[0]

        _, process = self.build_camera()

        channel_done = f"order.done.{robot_id}.{operation}"
        channel_fail = f"robot.failed.{robot_id}"
        hash_quality = f"order.quality.{order_id}"

        try:
            process.start()

            msg_start_log = p.get_message(timeout=1)
            assert not msg_start_log is None
            assert msg_start_log["type"] == "message"
            assert msg_start_log["channel"] == b"log"
            data = msg_start_log["data"].decode(encoding="utf-8")
            assert data == f"{name}: started"

            # The hash should be clean before we begin:
            assert r.hgetall(hash_quality) == {}

            # Check if the quality is assessed and recorded:
            r.publish(channel_done, str(order_id))
            sleep(0.1)
            # Assession message in log:
            msg_log = p.get_message(timeout=1)
            assert not msg_log is None
            assert msg_log["type"] == "message"
            assert msg_log["channel"] == b"log"
            data = msg_log["data"].decode("utf-8")
            assert data.find(f"quality of order {order_id} is ")
            # Assession results:
            hash_data = r.hgetall(hash_quality)
            assert hash_data != {}
            key = operation.encode(encoding="utf8")
            assert key in hash_data

            # Check if robot failure turns the camera off:
            r.publish(channel_fail, str(order_id))
            sleep(0.1)
            # Log message should appear:
            msg_off_log = p.get_message(timeout=1)
            assert not msg_off_log is None
            assert msg_off_log["type"] == "message"
            assert msg_off_log["channel"] == b"log"
            data = msg_off_log["data"].decode("utf-8")
            assert data == f"{name}: stopped"
            sleep(0.1)
            # Process should terminate:
            assert not process.is_alive()

        finally:
            process.terminate()
            p.unsubscribe("log")


class TestKitchenManager:
    name = "kitchen_manager"

    def build_manager(
        self, commands: kitchen.KitchenManager.CommandsTypeAlias
    ) -> tuple[Any, Process]:
        return service_builder(self.name, kitchen.KitchenManager, [commands])

    def test_creation(self):
        service, _ = self.build_manager([])
        assert not service is None

    def test_communication(self):
        r = Redis(host=redis_host, port=redis_port, db=0)
        p = r.pubsub()
        r.flushdb()

        list_orders = "order.waiting.freezer"
        hash_state = "order.state"
        set_break = "robot.break"

        p.subscribe("log")
        p.get_message(timeout=1)

        commands = [("sleep", 2), ("order", 2), ("sleep", 2), ("break", 2), ("sleep", 40)]

        _, process = self.build_manager(commands)

        try:
            process.start()

            sleep(1)

            # Manager should still be sleeping:
            assert r.lpop(list_orders) is None
            assert r.hget(hash_state, "2") is None

            # With blocking we should get an order:
            order_msg = r.blpop(list_orders, timeout=2)
            assert not order_msg is None
            assert order_msg[1] == b"0"

            # And the second order right after:
            order_msg = r.blpop(list_orders, timeout=1)
            assert not order_msg is None
            assert order_msg[1] == b"1"

            sleep(1)

            # Hash should be updated by now for sure:
            assert r.hget(hash_state, "0") == b"freezer"
            assert r.hget(hash_state, "1") == b"freezer"

            # No braking should be made so far:
            assert not r.sismember(set_break, "2")

            sleep(2)

            # Now breaking should already be set:
            assert r.sismember(set_break, "2")

        finally:
            process.terminate()
            p.unsubscribe("log")


# KitchenReport is a service used for testing, so have less priority


class TestKitchenReport:
    pass


# ChiefRobot is one of the most needed component of testing, but I had not enough time to cover it yet.


class TestChiefRobot:
    pass


class TestKitchenService:
    name = "kitchen_test"

    def build_service(
        self,
        robots_reliability: dict[str, float] = {},
        manager_commands: Iterable[tuple[str, Any]] = [],
    ):
        kitchen_config = KitchenConfig("tests/config_good.yaml")
        service = kitchen.Kitchen(kitchen_config, robots_reliability, manager_commands)
        return service

    def test_creation(self):
        service = self.build_service()
        # Check attributes:
        assert isinstance(service.logger, kitchen.LoggerService)
        assert isinstance(service.manager, kitchen.KitchenManager)
        assert service.camera_operations == {"sauce", "cheese", "from_oven", "slice", "pack"}
        assert len(service.robots) == 4
        cameras_amount = len(service.camera_operations) * len(service.robots)
        assert len(service.cameras) == cameras_amount
        assert service.ovens_amount == 2
        assert service.processes == []

    def test_running(self):
        r = Redis(host=redis_host, port=redis_port, db=0)
        p = r.pubsub()

        service = self.build_service()

        # Redis database should be flushed at launch, so unflush now
        r.sadd("unflushed", "test")

        service(self.name, redis_host, redis_port)

        # Check that database was flushed:
        assert not r.sismember("unflushed", "test")

        # Check processes:
        services_amount = sum(
            (
                len(service.robots),
                len(service.cameras),
                1,  # logger
                1,  # manager
            )
        )
        assert len(service.processes) == services_amount
        sleep(1)
        # All processes except for the manager should be running
        running_processes = [p for p in service.processes if p.is_alive()]
        assert len(running_processes) == services_amount - 1

    def test_shutdown(self):
        service = self.build_service()
        service(self.name, redis_host, redis_port)
        sleep(0.1)
        service.shutdown()
        sleep(0.1)
        running_processes = [p for p in service.processes if p.is_alive()]
        assert len(running_processes) == 0
