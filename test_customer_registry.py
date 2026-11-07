import os
import unittest
from unittest.mock import patch

import customer_registry


class CustomerRegistryTests(unittest.TestCase):
    def test_database_is_not_configured_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(customer_registry.database_configured())
            self.assertFalse(customer_registry.register_customer("U-test"))

    def test_missing_database_refuses_schema_creation(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                customer_registry.ensure_schema()

    def test_empty_user_id_is_ignored(self):
        self.assertFalse(customer_registry.register_customer(""))


if __name__ == "__main__":
    unittest.main()
