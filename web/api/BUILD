load("@rules_python//python:defs.bzl", "py_library", "py_test")
load("@pypi//:requirements.bzl", "requirement")

py_library(
    name = "api_lib",
    srcs = ["main.py", "models.py"],
    deps = [
        "//core:game_manager",
        "//core:game_config",
        requirement("fastapi"),
        requirement("pydantic"),
    ],
    visibility = ["//visibility:public"],
)

py_test(
    name = "test_api",
    srcs = ["test_api.py"],
    deps = [
        ":api_lib",
        requirement("pytest"),
        requirement("httpx"),
    ],
)
