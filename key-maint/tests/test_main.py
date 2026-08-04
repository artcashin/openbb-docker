from app.main import build_servers


class TestBuildServers:
    def test_two_loopback_binds_with_roles(self, tmp_path):
        servers = build_servers(str(tmp_path / "c.env"), str(tmp_path / "a.env"))
        cfgs = [(s.config.host, s.config.port) for s in servers]
        assert cfgs == [("127.0.0.1", 8446), ("127.0.0.1", 8447)]
        # Order is the contract: index 0 admin, index 1 network.
