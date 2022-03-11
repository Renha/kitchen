
Robotic Kitchen
===============

Assumptions
-----------

These things seemed important to note before explaining the solution architecture. Some of those are best to be discussed before starting the implementation in a real world scenario:

- The oven is assumed to be always-on, so that the time in the oven (between pizza placement and pickup) is important.

- On the contrary, the time pizza crust and other ingridients before the baking are assumed to be able to hang out for as much time as they need to, before going to the oven (while we still could try to make this time smaller).

- Robots aren’t conflicting over the freezer access, and in other actions. They are assumed to have non-restricted access to everything they need. The only place for possible conflicts are the ovens.

- The ease of adding new robots could be understood as changing only the amount of robots we already have, or introducing new kinds of robots. We assume that while new kinds of robots could be introduced, but will focus on changing the robots amount. So for example adding a new robot position might require much more alteration than just the amount.

- As the list of the available operations looks to be fully defined, we assume that robots could do only these operations. By the looks of it (even if we'll introduce new kinds of robots) the pizza could not ever travel backwards.

- The obviously failed pizza (even if that is clear on early stages) should anyway go through the oven and both before-the-oven and after-the-oven stages, as there is only one output for the result, good or bad. But we maybe could omit or adopt some stages of preparing for the failed pizza.

- Time in the oven assumed to be significantly longer than the time of crust placement/pickip to/from the oven.

- The ovens, freezer, etc. are considered to be uncontrollable during the normal operation. Still failsafe shutdown of the oven is good to have.

- Any of right-before-the-ovens and right-after-the-ovens robots could access any of the ovens.

- For simplicity one robot toolset is assumed to be associated with exactly one operation and vice versa, so we may focus on operations instead of tools.

- Robots are assumed to be able to fail only physically, i.e. the robot service task and its communication ability keeps working.

- Given cameras amount is based on the current amount of robots and preparing steps; we assume that altering tasks/robots amounts leads to cameras amount change.

- Cameras are checking the operations after those are finished. Changing it to checking at other point in time is trivial for real-world application, that is a simulation limitation, assumed to be fine.

- Cameras are checking quality very fast.

- Robots aren't fixable while the kitchen is operational. While the implementation of robot fixing in simulation seems trivial, the limitation is pretty close to the real world for safety reasons.

- If the pizza is stuck in the oven because of robot failure, we assume that failsafe would safely shut oven down.

- During physical inactivity (for example while baking) robot is unlikely to break down. Receiving robots are inactive while baking for the very same reason.


Brief system description
------------------------

The system consists of multiple agents, running each in its own process. They are interacting with each other via Redis channels, lists, and hashes.

There is a Kitchen class that builds, configures, and runs every other component, and have the ability to stop all of them.

All actual work on the pizza cooking is done by ChiefRobots, they both update the order state and do actual (physical) work. They publish syncronisation messages to QualityCameras attached to them.

KitchenManager object is there to emulate API of orders creation, and KitchenReport allows to eject an extended orders state.


What elements does the system have
----------------------------------

There are multiple kinds of the elements.

Agents:

- ChiefRobot - agent that processes orders both physically and programically. It knows its position and a set of operations it is capable of, and also have ability to break. The breakage is assumed to be possible only physically, so the robot could always signal that it broke down.

- LoggerService - a service that receives and displays execution flow messages from other agents. The implementation have a dead-simple illustration implementation only.

- QualityCamera - An agent which is needed to asses the order quality. It knows to which robot it is attached, and what stage it tracks. Upon receiving the sync signal from its robot is checks the order and writes down the quality value.

- KitchenManager - A simulation of simple ordering api. Have a task list, executes it and stops. Available tasks are: create an order, break a robot (not immediately, at their next physical task), or wait (sleep).

- KitchenReport - An agent that does only one thing: collects current order states. It is supposed to be run when kitchen is stopped. If the kitchen is not stopped, quality data might be out of sync, but order states should be fine.

- Kitchen - builds, configures, and runs all other agent, and have the ability to terminate their processes.

Virtual entities (not represented by python class and/or process):

- order - the pizza during, before, and after the cooking. Have its data fields in Redis. The properties are:

    - id - serves as unique identifier in a system. Physically though if the pizza is stuck in broken ChiefRobot possesion, the id is reused by a new pizza crust in a freezer.

    - state - current state of the order, where it is located or how is it processed. The beginning state of the order is in `freezer`, and the final one is `shelf`. In Redis:

        - Redis HASH `order.state -> id : state` to track the order state for displaying on some screen and/or report builder. Writeable by robots and the manager, readable by reporter.

        - Redis lists `order.waiting.{state} -> id` as queues for robots to take and exchange the orders. Writeable and readable by robots an writeable by manager.

        - Redis channel `order.done.{robot_id}.{operation} -> id` as a signal for cameras to assess the quality of just finished step.

    - quality - quality of the stages the order have already passed. Redis:

        - Redis hashes `order.quality.{id} -> state : quality`. The reporter reads it, and the cameras write.

- oven - an oven. Properties:

    - id - unique identificator.

    - free - the free/occupied state of the oven. Represented in Redis:

        - Redis list `oven.free -> id`. The robots read and write there, the kitchen initializes it.


The interactions between those elements
---------------------------------------

We will group communications by Redis shared data entities:

- Redis HASH `order.state -> id : state`:
    - `KitchenManager` writes there when creating new orders
    - `ChiefRobot` writes there when it starts a new operation on the order, when it resets the order, or finishes the order putting the pizza on the shelf.
    - `KitchenReport` reads the whole table for building a report.

- Redis lists `order.waiting.{state} -> id`:
    - `KitchenManager` adds the new order there when is asked to.
    - `ChiefRobot` writes there when it starts a new operation on the order, when it resets the order, or finishes its part of the operations.

- Redis list `oven.free -> id`:
    - `Kitchen` writes there to initially fill all of the available ovens.
    - `ChiefRobot` writes there when it reserves an oven to bake a pizza, and frees it once it pulls the pizza out.

- Redis channel `log -> message` (could to be another type, but for now let it be):
    - Any agent publishes what it has to say to the world.
    - `LoggerService` receives to put the message to the logging output.

- Redis channels `order.done.{robot_id}.{operation} -> id`:
    - `ChiefRobot` publishes to signal cameras to asses the result of just finished action.
    - `QualityCamera` receives to asses the result of just finished action.

- Redis hashes `order.quality.{id} -> state : quality`:
    - `QualityCamera` writes the result quality check of a finished action.
    - `KitchenReport` reads all the tables for building a report.

- Redis channels `robot.failed.robot_id -> order_id`:
    - `ChiefRobot` publishes to signal that it just broke.
    - `QualityCamera` receives to shut down as they are not needed to run anymore.

- Redis set `robot.break -> robot_id`:
    - `KitchenManager` writes to ask the robot to fail.
    - `ChiefRobot` reads to fail when asked.

Following subsection explains the communication between two robots during the oven negotiation, sorted by a typical communication sequence, as it is blockable and entangled:

- Redis list `robot.oven.queue -> order_id`:
    - `ChiefRobot` before the oven writes when it wants to put an order to an oven.
    - `ChiefRobot` after the oven reads when it is ready to bake an order.

- Redis channel `robot.oven.sync1.{order_id} -> oven_id`:
    - `ChiefRobot` after the oven writes when it is ready to serve a robot from the queue.
    - `ChiefRobot` before the oven reads to unlock and begin a physical placement of the pizza into the oven.

- Redis channel `robot.oven.sync2.{oven_id} -> success`:
    - `ChiefRobot` before the oven writes to signal that the physical pizza placement attempt is finished and also the success of it.
    - `ChiefRobot` after the oven reads to unlock and, depending on the success value, abandon or continue working with the order in question.


Differences between cooking 1 pizza, 2 pizzas, or N pizzas
----------------------------------------------------------

Single pizza is taken from the freezer by one of functioning robots before the oven, being processed by it, then the robot seeks for a robot after the oven which is ready to receive the pizza from the oven it reserved, then process it to the end. If robot processing pizza breaks, the order is recreated.

Two pizzas work the same each, except some robots/ovens could be occupied during the need in them, but with everything working good (no broken robots and/or blocked ovens) there is no additional delays (2 pizzas are done roughly in the same time as a single one).

N pizzas are processed in a way that waiting would be required. Robots before the oven would grab crusts from the freezer and start processing as soon as they could, same for robots after the ovens, they'll reserve an oven as soon as they are ready. The queue before the oven is effectively FIFO.

If 1 robot fails, if 2 robots fail
----------------------------------

If any 1 robot fails, the order it posess is reordered, other than that the kitchen would continue working. If broken robot blocks an oven, it is out of order too, but we have two.

If 2 robots on the different places (before and after the oven) are failing but not blocking both ovens, the kitchen would still work fine.

Any other brokeness cases lead to kitchen eventually stopping, after every still functioning robot owning an order will finish its actions queue (except for putting a pizza into the oven, it is forbidden).


Why is it easy to add new robots to the system
----------------------------------------------

After the physical robot placement, the cameras should be added to watch it. After that the robot should be turned on and the respecting Robot process to be started. Except for the communication configuration (like addresses, network connection, authentication, etc.) and the robot identification (put an ID, tell the robot which tools are available to it, etc.), the robot is ready to serve.

If a new kind of robots is added and it needs to use another transfer point, obviously new transfer queues and syncs should be added. That should be done similarly with the oven queues, but synchronizations might be generalized to do so. Wasn't implemented as we aren't gonna need it in the given project.

What could be done better
-------------------------

Some things aren't done but were at least thought about. Some aren't done to speed things up, some are considered out of scope:

- Visualization service, Interactive control service: it would be awesome to actually see the kitchen functioning at the screen, in a game-like manner, to further inspect the concept. Stopping and fixing robots with mouse clicks, etc.

- Fixing robots and freeing ovens: while it is not safe for the techician to enter the kitchen while operating, some problems could probably be fixed remotely. That would be nice to have implemented.

- Oven time optimization: ovens are not used optimally. When all after the oven robots are busy, no pizza could be placed into the oven. While the decision to do so was rational (simpler design, less chance of order reordering since the passively waiting robot has lesser chance to fail), it would be cool to optimize that at least optionally.

- Better configurability: the kitchen config controls the kitchen configurations, and it seemed not right to add simulation settings there too, so those are a bit scattered.

- Some of the communication patterns could be done better, but are as they are either because focus was not on them, or because of some design tweaks made during the development. Those could be improved. Some variable and channel names could be improved too.

- More tests: unfortunately as time for the task was limited, test suite is limited as well. Also splitting to subtests would be pretty nice.

- Communications via Redis to be hidden under methods, to make managing easier. No string operations with channel names, etc mixed with execution logic. That would be very nice! Could be done with more time.


Technical details
=====================

Kitchen configuration
---------------------

Kitchen could be configured via editing the `kitchen/kitchen.yaml` file, only important section is `kitchen`, others are added for simplier kitchen population. Most important for the project task are altering the machine counts, alter those as you will. The schema for the configuration could be found in `kitchen/config.py`.

There are some non-obvious things:

Operations `sync1` and `sync2` are special actions, they should only be used by robots before the oven, and in their respective order and placement. I.e. `sync2` must be the last action, and all actions that could possibly lock the oven should be placed between those (`to_oven` in the given configuration).

Operation `free` is special to after-the-oven robots, and should be placed only after robot surely stops locking the oven. There is a safeguard mechanism that disallows freeing even when the oven is locked by before-the-oven robot.

Running
-------

To run a simple demonstration, enter Poetry or Nix environment, and from project root directory execute `python -m kitchen.demo`.

To run a test suite, enter Poetry or Nix environment, and from project root directory execute one of:

- `pytest` to run all tests (some of those are pretty long)

- `pytest -m 'not slow_integration_test'` to run only unittests

- `pytest -m 'not slow_integration_test'` to run only integration tests

- `pytest --cov=kitchen tests/` for code coverage report.

Test suite is not complete, mostly because of time limitations, most importantly ChiefRobot is not covered as a unit at all, and integration tests could be more detailed. But the cases from the task are covered, and tests have helped to fix some found errors.


Project dependencies
--------------------

If you use `Nix` package manager, it will manage all dependencies except for itself. Get `Nix` from https://nixos.org/download.html, or if you are using NixOS it should already be installed. Version `2.4` was tested. After install, configuring and updating channels, just enter the project directory and execute `nix-shell` command to get environment with everything project needs. `Nix Environment Selector` (https://marketplace.visualstudio.com/items?itemName=arrterian.nix-env-selector) could be helpful being used with VSCode for better experience.

If you are not willing to use `Nix`, make sure you install `python 3.9`, `poetry 1.1.10`, `redis 6.2.6` in a way you normally do. After that, Python dependencies should be manageable by Poetry as usual, so no need to go deeper. If you are not going to use Poetry either and manage everything by yourself, look at `pyproject.toml` file to get an idea what dependencies you should install.

If `Nix` fails to build a working environment, try to apply `poetry lock`. If you are trying to manage dependencies solely with `Nix` and have no standalone `Poetry` installation, you could get a shell with it by executing `nix-shell -p poetry` command. 


Project file structure
----------------------

Root directory of the project contains the project files for tools like:
- VSCode: `kitchen.code-workspace`
- Poetry: `pyproject.toml`, `poetry.lock`
- Nix: `shell.nix`
- git: `.gitignore`

`kitchen` folder contains main project code as a python module.

`docs` folder contains the assignment statement.

`tests` folder contains tests implementation.

`tools-links` is a generated directory (if you use nix), contains symbolic links to mypy for vscode to find the version managed by nix.

Other directories and files could also be generated during the development and/or usage, most of those are listed in `.gitignore` file.