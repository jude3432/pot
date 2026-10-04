import unittest
from unittest.mock import patch

from database import repository


class FakeCursor:
    def __init__(self, fetch_results):
        self.fetch_results = list(fetch_results)
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((" ".join(query.split()), params))

    def fetchone(self):
        return self.fetch_results.pop(0)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class AtomicGameBalanceUpdateTests(unittest.TestCase):
    def test_game_deposit_updates_cached_balance_in_confirmation_transaction(self):
        cursor = FakeCursor([
            ("user-1", "pending", 20, 30, 40),
            (900, 2500, 90),
        ])
        connection = FakeConnection(cursor)

        with patch.object(repository.DatabaseManager, "get_connection", return_value=connection), \
                patch.object(repository.DatabaseManager, "put_connection"):
            result = repository.confirm_reserved_game_deposit(7, game_balance_credit=500)

        self.assertEqual(result, {
            "success": True,
            "bot_balance": 900,
            "game_balance": 2500,
            "game_bonus_amount": 90,
        })
        user_update, params = cursor.executed[1]
        self.assertIn("game_balance = COALESCE(game_balance, 0) + %s", user_update)
        self.assertEqual(params, (90, 500, "user-1"))
        self.assertTrue(connection.committed)

    def test_game_withdraw_updates_game_and_bot_balances_atomically(self):
        cursor = FakeCursor([
            (500, 200),
            (600, 600, 0),
        ])
        connection = FakeConnection(cursor)

        with patch.object(repository.DatabaseManager, "get_connection", return_value=connection), \
                patch.object(repository.DatabaseManager, "put_connection"):
            result = repository.settle_game_withdraw_with_active_bonus(
                "user-1", withdraw_amount=300, tx_id=8
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["new_balance"], 600)
        self.assertEqual(result["new_game_balance"], 600)
        self.assertEqual(result["game_bonus_amount"], 0)
        user_update, params = cursor.executed[1]
        self.assertIn("GREATEST(COALESCE(game_balance, 0) - %s, 0)", user_update)
        self.assertEqual(params, (100, 0, 300, "user-1"))
        self.assertTrue(connection.committed)


if __name__ == "__main__":
    unittest.main()