load("@rules_python//python:defs.bzl", "py_binary", "py_library")
load("@pypi//:requirements.bzl", "requirement")

package(default_visibility = ["//visibility:public"])

py_library(
    name = "game_config",
    srcs = ["game_config.py"],
)

py_library(
    name = "math_engine",
    srcs = ["math_engine.py"],
    deps = [":game_config"],
)

py_library(
    name = "math_playbook",
    srcs = ["math_playbook.py"],
    deps = [":math_engine"],
)

py_library(
    name = "game_entities",
    srcs = ["game_entities.py"],
    deps = [":game_config"],
)

py_library(
    name = "ai_agent",
    srcs = ["ai_agent.py"],
    deps = [
        ":game_entities",
        ":game_config",
    ],
)

py_library(
    name = "game_manager",
    srcs = ["game_manager.py"],
    deps = [
        ":game_entities",
        ":math_engine",
        ":game_config",
        ":math_playbook",
    ],
)

py_binary(
    name = "equadium",
    srcs = ["equadium.py"],
    deps = [
        ":game_manager",
        ":game_entities",
        ":ai_agent",
        ":math_playbook",
        requirement("sympy"),
    ],
)

py_binary(
    name = "equadium_optimization",
    srcs = ["equadium_optimization.py"],
    deps = [
        ":game_manager",
        ":game_entities",
        ":ai_agent",
        ":math_playbook",
        requirement("sympy"),
    ],
)

py_binary(
    name = "build_playbook",
    srcs = ["build_playbook.py"],
    deps = [
        ":game_config",
        ":math_playbook",
    ],
)

