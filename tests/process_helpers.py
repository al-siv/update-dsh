"""Capture test commands and stop their process groups before timeout cleanup."""

import os
import signal
import subprocess


def stop_process_group(process):
    # The leader may already have exited while a descendant holds its pipes open.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return process.communicate()


def run_command(argv, *, timeout, env=None, cwd=None):
    process = subprocess.Popen(
        argv, env=env, cwd=cwd, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        error.output, error.stderr = stop_process_group(process)
        raise
    except BaseException:
        stop_process_group(process)
        raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
