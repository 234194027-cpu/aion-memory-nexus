from unittest.mock import patch, MagicMock


class TestVectorIndexBackend:
    def test_null_backend_is_available(self):
        from src.memory.services.vector_index_backend import NullVectorIndex

        backend = NullVectorIndex()
        assert backend.is_available() is False

    def test_null_backend_query_returns_empty(self):
        from src.memory.services.vector_index_backend import NullVectorIndex

        backend = NullVectorIndex()
        result = backend.query([0.1] * 1024, 10)
        assert result == []

    def test_null_backend_upsert_returns_false(self):
        from src.memory.services.vector_index_backend import NullVectorIndex

        backend = NullVectorIndex()
        result = backend.upsert("test_id", [0.1] * 1024, {})
        assert result is False

    def test_null_backend_delete_returns_false(self):
        from src.memory.services.vector_index_backend import NullVectorIndex

        backend = NullVectorIndex()
        result = backend.delete("test_id")
        assert result is False

    def test_get_backend_returns_null_when_python(self):
        from src.memory.services.vector_index_backend import get_vector_index_backend

        with patch("src.memory.services.vector_index_backend.settings") as mock_settings:
            mock_settings.VECTOR_INDEX_BACKEND = "python"
            backend = get_vector_index_backend()
            assert backend.is_available() is False

    def test_get_backend_returns_null_when_zvec_not_installed(self):
        from src.memory.services.vector_index_backend import get_vector_index_backend

        with patch("src.memory.services.vector_index_backend.settings") as mock_settings:
            mock_settings.VECTOR_INDEX_BACKEND = "zvec"
            with patch.dict("sys.modules", {"src.memory.services.zvec_index": None}):
                backend = get_vector_index_backend()
                assert backend.is_available() is False

    def test_get_backend_falls_back_on_zvec_import_error(self):
        from src.memory.services.vector_index_backend import get_vector_index_backend

        with patch("src.memory.services.vector_index_backend.settings") as mock_settings:
            mock_settings.VECTOR_INDEX_BACKEND = "zvec"
            with patch("builtins.__import__", side_effect=ImportError("zvec not found")):
                backend = get_vector_index_backend()
                assert backend.is_available() is False

    def test_get_backend_falls_back_on_zvec_init_error(self):
        from src.memory.services.vector_index_backend import get_vector_index_backend

        with patch("src.memory.services.vector_index_backend.settings") as mock_settings:
            mock_settings.VECTOR_INDEX_BACKEND = "zvec"
            mock_zvec_module = MagicMock()
            mock_zvec_module.ZvecIndex = MagicMock(side_effect=Exception("init error"))
            with patch.dict("sys.modules", {"src.memory.services.zvec_index": mock_zvec_module}):
                backend = get_vector_index_backend()
                assert backend.is_available() is False