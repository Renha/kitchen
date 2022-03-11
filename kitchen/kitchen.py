"""
Implements classes needed for the virtual kitchen simulation,
and starts the simulation
"""

from __future__ import annotations

from .config import KitchenConfig

from typing import Optional, Any, List, Callable, Union, Tuple, Iterable
from redis import Redis
from multiprocessing import Process
from datetime import datetime
from time import sleep


import random


class ConnectedServiceBase:
    """
    Base class for connected services, simplifies redis connection
    by creating simple to use attributes.

    Attributes:
        redis: Redis connection instance
        pubsub: initialized Redis.pubsub
        redis_host: redis host to be kept until service launch and to launch
            subservices
        redis_port: redis port to be kept until service launch and to launch
            subservices
        redis: if provided, it is used instead of creating a new connection.
            redis_host and redis_port are left as they are, so use the parameter
            with caution, it has been added for tests.
        name: service name string for logging
    """

    redis: Union[Redis[str], Redis[bytes]]
    pubsub: Any
    redis_host: str
    redis_port: int
    name: str

    def run(self) -> Any:
        pass

    def __call__(
        self,
        name: str,
        redis_host: str,
        redis_port: int,
    ) -> Any:
        self.name = name
        self.redis_host = redis_host
        self.redis_port = redis_port
        # Tests are using their own redis connection:
        self.redis = Redis(host=redis_host, port=redis_port, db=0, encoding="utf-8")
        self.pubsub = self.redis.pubsub(ignore_subscribe_messages=True)
        self.log("started")
        result = self.run()
        self.log("stopped")
        return result

    def log(self, message: str):
        """Publish a message to a logging channel"""
        self.pub("log", f"{self.name}: {message}")

    def pub(self, channel: str, data: Any):
        """
        Syntax sugar to take care of data types convertable to/from strings while pub
        Not quite neccessary, non-pythonic in a way, but left as an indication
        on how we could hide all explicitly Redis operations in ConnectedServiceBase
        class if we decide to do so.

        Arguments:
            channel: channel to publish to
            data: data to be published
        """
        self.redis.publish(channel, str(data))

    def get(self, msg_type: type = str, timeout: float = 0) -> tuple[Optional[str], Optional[Any]]:
        """
        Syntax sugar to take care of data convertable to/from strings while get_message
        Not quite neccessary, non-pythonic in a way, but left as an indication
        on how we could hide all explicitly Redis operations in ConnectedServiceBase
        class if we decide to do so.

        Arguments:
            msg_type: type to convert data to.
            timeout: bypassed to get_message

        Returns:
            (str(channel), msg_type(data)) or (None, None) if no messages
        """
        msg = self.pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
        if msg is None or msg["type"] != "message":
            return (None, None)
        else:
            channel = msg["channel"].decode(encoding="utf8")
            data = msg_type(msg["data"].decode(encoding="utf8"))
            return (channel, data)


class ChiefRobot(ConnectedServiceBase):
    """
    Chief Robot service implements order preparation with simulation

    Arguments:
        id: robot id
        operations: operations robot is capable of, ordered by execution need
        border_state: for robot befire the oven, a state where it expects the
            order to apper for starting preparing, for robot after the oven it
            is a state at which robot places the order after finishing its job
        reset_state: a state to which robot sends order when it fails, so that
            it would not be lost (other robot will restart it if possible).
        after_oven: robot placement. True for being placed after the oven,
            False otherwise.
        reliability: robot reliability (1 minus failure chance) mapped to
            operation name. If the operation is not among keys, it is
            considered flawless.
        seconds_per_action: time in seconds every preparation step takes
        time_variations: time of seconds_per_action would be multiplied to a
            float in these limits.

    Attributes:
        id: argument id is kept there
        operations: argument operations is kept there
        border_state: argument border_state is kept there
        reset_state: argument reset_state is kept there
        after_oven: argument after_oven is kept there
        reliability: argument reliability is kept there
        seconds_per_action: argument seconds_per_action is kept there
        time_variations: argument time_variations is kept there
        oven_id: oven id is kept there temporarily while it is actual,
            at other times is ignored and may have not actual data
        order_id: order id is kept there temporarily while it is actual,
            at other times is ignored and may have not actual data
        failure: failure state of the robot, failed robot will shut down.

    Redis communications:
        lpush: order.waiting.{reset_state}=order_id to reset an order state when failed
        if before oven:
            lpop: order.waiting.{border_state}=order_id to take order to start making a pizza
        if after oven:
            rpush: order.waiting.{border_state}=order_id to declare that order is done

        pub: order.done.{robot_id}.{operation}=order_id to publically announce that the
            operation has been done, for example for cameras to check how good it is done

        lpop: oven.free=oven_id if we are after the oven, when we are ready to accept a new
            order passed from the before the oven, we reserve an oven for ourselves this way.
        rpush: oven.free=self.oven_id to free the occupied oven when pull the pizza out.

        pub: robot.failed.{robot_id}=str(int(order_id)) to announce the failure if it happens
        sismember: robot.break=robot_id to simulate a failure if the manager asks to

        delete: order.quality.{order_id} when resets an order

        hset: order.state=str(int(order_id))->operation|border_state|reset_state to update
            order state

        Syncronisation between before-the-oven and after-the-oven when passing the pizza:
            before:
                [enters `sync1` action]
                rpush: robot.oven.queue=order_id -- take a place at the queue
            after
                [enters __get_order_id method]
                [reserves an oven for itself]
                lpop: robot.oven.queue=order_id -- pop a waiting robot from queue
                pub: robot.oven.sync1.{order_id}=oven_id -- tell the other robot that it
                    is waited, also sending the oven id (where it is waited)
            before:
                get: robot.oven.sync1.{order_id}=oven_id -- takes a note of the oven id
                [leaves `sync1` action]
                [performs put-to-oven actions]
                [enters either the `sync2` action or a failure procedure]
                pub: robot.oven.sync2.{oven_id}=success
            after:
                get: robot.oven.sync2.{oven_id}=success
                if success:
                    [frees the oven]
                    [continues to bake action]
                if not success:
                    [returns back to the beginning of the __get_order_id]
            before:
                [leaves either the `sync2` action or a failure procedure]
                if success:
                    [takes a new order]
                if not success:
                    [turns off]
    """

    default_seconds_per_action: dict[str, float] = {
        "take": 3,
        "sauce": 4,
        "cheese": 4,
        "to_oven": 3,
        "bake": 30,
        "from_oven": 3,
        "slice": 8,
        "pack": 6,
        "put": 3,
    }

    def __init__(
        self,
        id: int,
        operations: list[str],
        border_state: str,
        reset_state: str,
        after_oven: bool,
        reliability: dict[str, float] = {},
        seconds_per_action: dict[str, float] = {},
        time_variations: tuple[float, float] = (0.9, 1.2),
    ):
        self.id = id
        self.operations = operations
        self.border_state = border_state
        self.reset_state = reset_state
        self.after_oven = after_oven
        self.reliability = reliability
        self.seconds_per_action = dict(self.default_seconds_per_action)
        self.seconds_per_action.update(seconds_per_action)
        self.time_variations = time_variations
        self.oven_id: Optional[int] = None
        self.order_id: Optional[int] = None
        self.failure: bool = False

        random.seed()

    def execute_action(self, action_name: str) -> bool:
        """
        Simulate real work done by the robot. Only special actions are 'sync'
        and 'free' which are not a simulation, but a real communication
        procedures. Implemented with locking for simplicity sake.
        """

        # Service actions:
        if action_name == "sync1":
            # Subscribe to oven_id and a signal to continue:
            sync_channel = f"robot.oven.sync1.{self.order_id}"
            self.pubsub.subscribe(sync_channel)

            # Take a place in before-the-oven queue
            self.redis.rpush(f"robot.oven.queue", str(self.order_id))

            # Wait for oven_id and a signal to continue:
            while True:
                channel, data = self.get(int, timeout=1)
                if not data is None and channel == sync_channel:
                    self.pubsub.unsubscribe(sync_channel)
                    self.oven_id = data
                    break

            return True

        if action_name == "sync2":
            self.pub(f"robot.oven.sync2.{self.oven_id}", 1)
            self.oven_id = None
            return True

        elif action_name == "free":
            assert not self.oven_id is None
            self.redis.rpush("oven.free", self.oven_id)
            return True

        # Physical actions:
        else:
            # Could be done much better, but for fast solution needed for tests,
            # should be sufficient:
            commanded_to_break = self.redis.sismember(f"robot.break", f"{self.id}")
            if commanded_to_break:
                return False

            base_time = self.seconds_per_action.get(action_name, 0)
            sleep(random.uniform(*(base_time * v for v in self.time_variations)))

            reliability = self.reliability.get(action_name, 1)
            failure_chance = 1 - reliability

            success = (
                random.choices([True, False], [reliability, failure_chance])[0]
                if failure_chance
                else True
            )

            return success

    def __get_order_id(self) -> int:
        """Returns order ID for robot to work on. May block forever."""
        if self.after_oven:
            order_id = None
            while order_id is None:
                # Wait for and get free oven:
                free_msg = self.redis.blpop(["oven.free"], timeout=0)
                assert not free_msg is None
                self.oven_id = int(free_msg[1])
                # Get the first of before-the-oven oven waiters:
                queue_msg = self.redis.blpop(["robot.oven.queue"], timeout=0)
                assert not queue_msg is None
                order_id = int(queue_msg[1])
                # Subscribe for oven filling result:
                sync2_channel = f"robot.oven.sync2.{self.oven_id}"
                self.pubsub.subscribe(sync2_channel)
                # Give them an oven id and signal that we are waiting for
                # them to either put a pizza to oven or to fail doing that:
                sync1_channel = f"robot.oven.sync1.{order_id}"
                self.pub(sync1_channel, self.oven_id)
                # Wait for the unlock message with filling success info:
                while True:
                    channel, data = self.get(int, timeout=1)
                    if not data is None and channel == sync2_channel:
                        self.pubsub.unsubscribe(sync2_channel)
                        success = bool(data)
                        if success:
                            self.log(f"order {order_id} has been put into oven")
                            break
                        else:
                            self.log(f"order {order_id} failed to be put into oven {self.oven_id}")
                            # Leave oven locked, wait for another order
                            order_id = None

        else:
            pending_order = self.redis.blpop([f"order.waiting.{self.border_state}"], timeout=0)
            assert not pending_order is None
            order_id = int(pending_order[1])

        self.order_id = order_id
        return order_id

    def __order_reset(self, order_id: int):
        self.failure = True
        self.log("failure")
        self.pub("robot.failed", self.id)
        # In case if we have locked a robot after the oven:
        if (not self.after_oven) and (not self.oven_id is None):
            self.pub(f"robot.oven.sync2.{self.oven_id}", 0)
        self.redis.hset(f"order.state", str(order_id), str(self.reset_state))
        self.redis.delete(f"order.quality.{order_id}")
        self.redis.lpush(f"order.waiting.{self.reset_state}", order_id)

    def run(self):
        while not self.failure:
            order_id = self.__get_order_id()
            result = False
            for operation in self.operations:
                self.log(f"start `{operation}` order {order_id}")
                self.redis.hset(f"order.state", str(order_id), operation)
                result = self.execute_action(operation)
                if not result:
                    self.__order_reset(order_id)
                    break
                else:
                    self.log(f"done `{operation}` order {order_id}")
                    self.pub(f"order.done.{self.id}.{operation}", order_id)
            if result and self.after_oven:
                self.redis.rpush(f"order.waiting.{self.border_state}", order_id)
                self.redis.hset(f"order.state", str(order_id), str(self.border_state))


class LoggerService(ConnectedServiceBase):
    """
    Logger service. Simpliest implementation with printing to the stdout,
    instead of more complete using logging module, for simplicity. For the
    same reason pubsub is used instead of queues, streams, pubsub was just
    the quickest to implement. Also if needed conversion should be trivial.

    Redis communications:
        sub: log=str(log message) to print the message out
        pub: log=str(alive message) to give a sign that logging is still
            active, when no new messages appear for `canary_period`
    """

    def run(self):
        canary_period = 10
        self.pubsub.subscribe("log")
        while True:
            channel, message = self.get(str, timeout=canary_period)
            if not message is None:
                time_str: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{time_str}] {channel}: {message}")
            else:
                self.log("still alive, nothing happened")


class QualityCamera(ConnectedServiceBase):
    """
    Quality Camera service implements checking the quality of the product.

    Arguments:
        operation: an operation name for its result to be checked
        robot_id: each camera is assumed to be attached to a specific robot

    Attributes:
        operation: argument operation is kept there
        robot_id: argument robot_id is kept there

    Redis communications:
        sub: order.done.{robot_id}.{operation}=order_id to know when to assess
        hset: order.quality.{order_id}=->operation=str(float(quality))
        sub: robot.failed.{robot_id}=order_id to know when robot fails, for
            shutting itself down
    """

    def __init__(self, operation: str, robot_id: int):
        self.operation = operation
        self.robot_id = robot_id
        random.seed()

    def assess_quality(self) -> float:
        """
        Simulate real quality checkup

        Returns:
            number between 0.0 for awful quality and 1.0 for a perfect one
        """
        randresult = random.normalvariate(1.0, 0.1)
        quality = max(0.0, min(1.0, randresult))
        return quality

    def run(self):
        time_period = 100

        order_done_channel = f"order.done.{self.robot_id}.{self.operation}"
        robot_failed_channel = f"robot.failed.{self.robot_id}"

        self.pubsub.subscribe((order_done_channel, robot_failed_channel))
        while True:
            channel, data = self.get(timeout=time_period)
            if not data is None:
                if channel == robot_failed_channel:
                    break
                elif channel == order_done_channel:
                    order_id = int(data)
                    quality = self.assess_quality()
                    self.log(f"quality of order {order_id} is {quality:.2f}")
                    self.redis.hset(f"order.quality.{order_id}", self.operation, str(quality))


class KitchenManager(ConnectedServiceBase):
    """
    Service that simulates api and management for that simple implementation.
    It has a fixed set of simple commands to work with, and after all of them
    are done, shuts down.

    Arguments:
        commands: list of tuples (command_name, parameter), supported commands:
            ('order', amount) -- order `amount` pizzas,
            ('sleep', seconds) -- wait `seconds` seconds,
            ('break', robot_id) -- ask robot `robot_id` to break at the beginning
                of its current or next physical action

    Attributes:
        commands: argument commands is stored there
        next_order_id: incrementing counter to never create orders with same id

    Redis communications:
        create a new order:
            hset: order.state=str(int(order_id))->'freezer'
            rpush: order.waiting.freezer=order_id
        break a robot:
            sadd: robot.break=robot_id
    """

    CommandsTypeAlias = Iterable[Tuple[str, Union[int, float]]]

    def __init__(self, commands: CommandsTypeAlias):
        self.commands = commands
        self.next_order_id = 0

    def __order_create(self):
        order_id = self.next_order_id
        self.redis.hset(f"order.state", str(order_id), "freezer")
        self.redis.rpush(f"order.waiting.freezer", self.next_order_id)
        self.log(f"created a new order {order_id}")
        self.next_order_id += 1

    def run(self):
        for command, parameter in self.commands:
            if command == "sleep":
                sleep(parameter)
            elif command == "order":
                for _ in range(int(parameter)):
                    self.__order_create()
            elif command == "break":
                robot_id = parameter
                self.redis.sadd(f"robot.break", robot_id)


class KitchenReport(ConnectedServiceBase):
    """
    Report builder, when started, immediately collects orders states.
    If the kitchen is not stopped, quality data might be out of sync.

    Attributes:
        map_state_order: orders by state
        map_order_state: state by order
        map_order_state_quality: quality by order by state

    Redis communications:
        hgetall: order.state to build report
        hgetall: order.quality.{order_id}=->operation=str(float(quality))
    """

    def __init__(self):
        self.map_state_order: dict[str, list[int]] = {}
        self.map_order_state: dict[int, str] = {}
        self.map_order_state_quality: dict[int, dict[str, str]] = {}

    def __repr__(self):
        repr_map_order_state_quality = "\n  " + (
            "\n  ".join(
                (f"{order}: {quality}" for order, quality in self.map_order_state_quality.items())
            )
        )
        return f"""
orders by state: {self.map_state_order}
state by order: {self.map_order_state}
quality by order by state: {repr_map_order_state_quality}
        """

    def run(self):
        order_state_raw: Union[dict[str, str], dict[bytes, bytes]]
        order_state_raw = self.redis.hgetall("order.state")
        for order_raw, state_raw in order_state_raw.items():
            assert isinstance(order_raw, bytes)
            assert isinstance(state_raw, bytes)
            order = int(order_raw.decode(encoding="utf-8"))
            state = str(state_raw.decode(encoding="utf-8"))
            self.map_order_state[order] = state
            if state in self.map_state_order:
                self.map_state_order[state].append(order)
            else:
                self.map_state_order[state] = [order]
            self.map_order_state[order] = state

            state_quality_raw: Union[dict[str, str], dict[bytes, bytes]]
            state_quality_raw = self.redis.hgetall(f"order.quality.{order}")
            self.map_order_state_quality[order] = {}
            for quality_state_raw, quality_value_raw in state_quality_raw.items():
                assert isinstance(quality_state_raw, bytes)
                assert isinstance(quality_value_raw, bytes)
                quality_state = str(quality_state_raw.decode(encoding="utf-8"))
                quality_value = float(quality_value_raw.decode(encoding="utf-8"))
                self.map_order_state_quality[order][quality_state] = f"{quality_value:.2f}"


class Kitchen(ConnectedServiceBase):
    """
    Main kitchen class, runs all the kitchen services, has the ability
    to shut those down afterwards.

    Arguments:
        config: kitchen configuration
        robots_reliability: robots reliability (1 minus failure chance) mapped
            to operation name. If the operation is not among keys, it is
            considered flawless.
        robots_seconds_per_action: time in seconds every preparation step takes
        robots_time_variations: time of seconds_per_action would be multiplied to a
            float in these limits.
        manager_commands: list of tuples (command_name, parameter), supported commands:
            ('order', amount) -- order `amount` pizzas,
            ('sleep', seconds) -- wait `seconds` seconds,
            ('break', robot_id) -- ask robot `robot_id` to break at the beginning
                of its current or next physical action

    Attributes:
        logger: logger service object is saved there after creation
        manager: kitchen manager service object is saved there after creation
        camera_operations: a set of operations that should be watched by the
        cameras: camera services are kept there
        robots: robots services are kept there
        ovens_amount: amount of ovens is kept there
        launch_delay: a time delay between services launch
        processes: there all runnicg subservices are stored
    """

    def __init__(
        self,
        config: KitchenConfig,
        robots_reliability: dict[str, float] = {},
        robots_seconds_per_action: dict[str, float] = {},
        robots_time_variations: tuple[float, float] = (0.9, 1.2),
        manager_commands: KitchenManager.CommandsTypeAlias = [],
    ):
        self.logger = LoggerService()
        self.manager = KitchenManager(manager_commands)
        self.camera_operations: set[str] = set()
        self.cameras: set[QualityCamera] = set()
        # Robots are kept in list instead of set for id -> position dependance
        #   which could be useful for testing:
        self.robots: list[ChiefRobot] = []
        self.ovens_amount = 0

        self.launch_delay = 0.1

        robot_id = 0

        kitchen_config: KitchenConfig.TypeAlias = config.data["kitchen"]
        for machine in kitchen_config:
            count = machine.get("count", 1)
            assert isinstance(count, int)
            if machine["kind"] == "robot":
                for _ in range(count):
                    assert isinstance(machine["operations"], List)
                    assert isinstance(machine["border_state"], str)
                    assert isinstance(machine["reset_state"], str)
                    assert isinstance(machine["after_oven"], bool)
                    robot = ChiefRobot(
                        robot_id,
                        machine["operations"],
                        machine["border_state"],
                        machine["reset_state"],
                        machine["after_oven"],
                        robots_reliability,
                        robots_seconds_per_action,
                        robots_time_variations,
                    )
                    self.robots.append(robot)
                    robot_id += 1
            elif machine["kind"] == "oven":
                self.ovens_amount += count
            elif machine["kind"] == "camera-system":
                assert isinstance(machine["operations"], List)
                self.camera_operations = set(machine["operations"])

        if self.camera_operations and self.robots:
            for robot_id in (r.id for r in self.robots):
                for operation in self.camera_operations:
                    camera = QualityCamera(operation, robot_id)
                    self.cameras.add(camera)

        self.processes: list[Process] = []

    def launch_service(self, service: Callable[..., Any], name: str):
        process = Process(
            target=service,
            kwargs={"name": name, "redis_host": self.redis_host, "redis_port": self.redis_port},
        )
        process.start()
        self.processes.append(process)
        sleep(self.launch_delay)

    def run(self):
        """
        Start the execution of all the kitchen-located services,
        flushes database for clean run.
        """

        self.redis.flushdb()

        for oven_id in range(self.ovens_amount):
            self.redis.rpush("oven.free", oven_id)

        self.launch_service(self.logger, "logger")

        for camera in self.cameras:
            self.launch_service(camera, f"camera `{camera.operation}` robot {camera.robot_id}")

        for robot in self.robots:
            self.launch_service(robot, f"robot {robot.id}")

        self.launch_service(self.manager, "manager")

    def shutdown(self):
        for process in self.processes:
            process.terminate()
