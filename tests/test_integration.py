from kimmdy.parsing import (
    read_topol,
    read_plumed,
)
from kimmdy.cmd import kimmdy_run
from kimmdy.utils import run_shell_cmd

import os
import shutil
import logging
from pathlib import Path
import pytest


def setup_testdir(tmp_path, dirname) -> Path:
    try:
        filedir = Path(__file__).parent / "test_files" / "test_integration" / dirname
        assetsdir = Path(__file__).parent / "test_files" / "assets"
    except NameError:
        filedir = Path("./tests/test_files" / "test_integration" / dirname)
        assetsdir = Path("./tests/test_files" / "assets")
    testdir = tmp_path / "test_integration" / dirname
    shutil.copytree(filedir, testdir)
    shutil.copy2(assetsdir / "amber99sb_patches.xml", testdir)
    Path(testdir / "amber99sb-star-ildnp.ff").symlink_to(
        assetsdir / "amber99sb-star-ildnp.ff",
        target_is_directory=True,
    )
    os.chdir(testdir.resolve())
    return testdir


def test_integration_emptyrun(tmp_path, caplog):
    testdir = setup_testdir(tmp_path, "emptyrun")
    caplog.set_level(logging.INFO)
    (testdir / "emptyrun.txt").touch()

    kimmdy_run()
    for record in caplog.records:
        # assert record.levelname != "WARNING"
        assert record.levelname != "CRITICAL"
    assert set(["Finished", "running", "tasks,"]).issubset(
        set(caplog.records[-1].message.split(sep=" "))
    )


def test_integration_hat_reaction(tmp_path, caplog):
    testdir = setup_testdir(tmp_path, "HAT_reaction")

    kimmdy_run()
    for record in caplog.records:
        # assert record.levelname != "WARNING"
        assert record.levelname != "CRITICAL"
    assert set(["Finished", "running", "tasks,"]).issubset(
        set(caplog.records[-1].message.split(sep=" "))
    )


def test_integration_homolysis_reaction(tmp_path, caplog):
    testdir = setup_testdir(tmp_path, "homolysis")

    kimmdy_run()

    for record in caplog.records:
        # assert record.levelname != "WARNING"
        assert record.levelname != "CRITICAL"
    assert set(["Finished", "running", "tasks,"]).issubset(
        set(caplog.records[-1].message.split(sep=" "))
    )


def test_integration_whole_run(tmp_path, caplog):
    testdir = setup_testdir(tmp_path, "whole_run")

    kimmdy_run()

    for record in caplog.records:
        # assert record.levelname != "WARNING"
        assert record.levelname != "CRITICAL"
    assert set(["Finished", "running", "tasks,"]).issubset(
        set(caplog.records[-1].message.split(sep=" "))
    )
