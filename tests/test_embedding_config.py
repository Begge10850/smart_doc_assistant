import unittest

from embedding_config import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    validate_embedding_dimension,
)


class EmbeddingConfigTests(unittest.TestCase):
    def test_mpnet_contract_is_768_dimensions(self):
        self.assertEqual(EMBEDDING_MODEL, "sentence-transformers/all-mpnet-base-v2")
        self.assertEqual(EMBEDDING_DIMENSION, 768)
        validate_embedding_dimension([0.0] * EMBEDDING_DIMENSION)

    def test_wrong_dimension_is_rejected_before_database_write(self):
        with self.assertRaisesRegex(ValueError, "768-dimension"):
            validate_embedding_dimension([0.0] * 1536)


if __name__ == "__main__":
    unittest.main()
