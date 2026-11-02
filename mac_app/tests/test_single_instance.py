import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from instance_signal import InstanceSignalServer, request_running_instance


class SingleInstanceTests(unittest.TestCase):
    def test_second_launch_signals_the_running_instance(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            path = Path(directory) / "s"
            activated = threading.Event()
            server = InstanceSignalServer(path, activated.set)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assertTrue(request_running_instance(path, timeout=1))
                self.assertTrue(activated.wait(1))
            finally:
                server.close()
                thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
