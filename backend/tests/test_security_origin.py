import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class SecurityOriginTest(unittest.TestCase):
    def test_local_dev_loopback_origin_is_allowed(self):
        self.assertIn("http://127.0.0.1:5173", main._ALLOWED_ORIGINS)

    def test_cors_origins_match_csrf_origins(self):
        cors = next(
            middleware
            for middleware in main.app.user_middleware
            if middleware.cls.__name__ == "CORSMiddleware"
        )
        self.assertEqual(set(cors.kwargs["allow_origins"]), main._ALLOWED_ORIGINS)


if __name__ == "__main__":
    unittest.main()
