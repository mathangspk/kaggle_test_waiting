"""Standalone stdlib Linux fault checks; no browser, auth, Docker or monitor access.

Run with discovery_*.py on PYTHONPATH: python discovery_linux_check.py
"""
from pathlib import Path
import sys
import tempfile
import time
import unittest

import discovery_worker as worker


@unittest.skipUnless(sys.platform == "linux", "Linux process-tree check")
class ProcessTreeCheck(unittest.TestCase):
    def check_tree(self, early_exit):
        with tempfile.TemporaryDirectory(prefix="discovery-test-") as directory:
            pidfile = Path(directory, "child.pid")
            script = ("import os,time; pid=os.fork(); "
                      "\nif pid==0:\n os.setsid(); open(" + repr(str(pidfile)) +
                      ",'w').write(str(os.getpid())); time.sleep(60)"
                      "\nelse:\n " + ("time.sleep(.2)" if early_exit else "time.sleep(60)"))
            started = time.monotonic()
            result, metrics = worker.run_visit(Path(directory, "unused"),
                                               [sys.executable, "-c", script], deadline=6)
            self.assertLess(time.monotonic() - started, 6)
            self.assertEqual(result["error"], "worker_crash" if early_exit else "visit_timeout")
            self.assertFalse(Path("/proc", pidfile.read_text()).exists())
            self.assertFalse(worker.descendants())
            self.assertIn("cgroup_after", metrics)

    def test_timeout_detached_child(self):
        self.check_tree(False)

    def test_leader_exit_orphan(self):
        self.check_tree(True)


if __name__ == "__main__":
    unittest.main()
