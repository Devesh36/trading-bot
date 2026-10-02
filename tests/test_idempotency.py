import pytest
from storage.database import Database
from strategy.signal import Signal
from runtime import ProcessLock


def test_signal_reservation_survives_restart(tmp_path):
    path = str(tmp_path / "paper.db")
    signal = Signal("LONG", "BTCUSD", confirmation_candle=1800)
    db = Database(path)
    assert db.reserve_signal(signal)
    db.close()
    db = Database(path)
    assert db.seen(signal.signal_id) and not db.reserve_signal(signal)
    assert len(signal.client_id()) == 32
    db.close()


def test_atomic_rollback(tmp_path):
    db = Database(str(tmp_path / "paper.db"))
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.set("cash", 5)
            raise RuntimeError("crash")
    assert db.get("cash") is None


def test_single_process_lock(tmp_path):
    path = str(tmp_path / "bot.lock")
    with ProcessLock(path):
        with pytest.raises(RuntimeError):
            with ProcessLock(path):
                pass
