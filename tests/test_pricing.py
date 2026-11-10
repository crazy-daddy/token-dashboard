import os
import unittest

from token_dashboard.pricing import load_pricing, cost_for, format_for_user

PRICING = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "pricing.json"))


class CostTests(unittest.TestCase):
    def setUp(self):
        self.p = load_pricing(PRICING)

    def _u(self, **kw):
        base = {
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "cache_create_5m_tokens": 0, "cache_create_1h_tokens": 0,
        }
        base.update(kw)
        return base

    def test_known_opus_input_cost(self):
        c = cost_for("claude-opus-4-7", self._u(input_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 5.00, places=4)
        self.assertFalse(c["estimated"])

    def test_known_sonnet_output_cost(self):
        c = cost_for("claude-sonnet-4-6", self._u(output_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 15.00, places=4)

    def test_unknown_opus_falls_back(self):
        c = cost_for("claude-opus-9-9-experimental", self._u(input_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 4.00, places=4)
        self.assertTrue(c["estimated"])

    def test_unknown_fable_falls_back(self):
        # Before the fable tier existed, fable models priced as None ($0).
        c = cost_for("claude-fable-9", self._u(output_tokens=1_000_000), self.p)
        self.assertAlmostEqual(c["usd"], 50.00, places=4)
        self.assertTrue(c["estimated"])

    def test_unknown_unparseable_returns_none(self):
        c = cost_for("custom-local-model", self._u(input_tokens=9999), self.p)
        self.assertIsNone(c["usd"])

    def test_cache_read_cheaper_than_input(self):
        c_in = cost_for("claude-opus-4-7", self._u(input_tokens=1_000_000), self.p)
        c_cr = cost_for("claude-opus-4-7", self._u(cache_read_tokens=1_000_000), self.p)
        self.assertLess(c_cr["usd"], c_in["usd"])


class LongContextTests(unittest.TestCase):
    """Haiku 5.5 bills prompts over 100K tokens at higher rates."""

    def setUp(self):
        import tempfile
        from token_dashboard.db import init_db
        self.p = load_pricing(PRICING)
        self.db = os.path.join(tempfile.mkdtemp(), "t.db")
        init_db(self.db)

    def _add(self, uuid, model, input_tokens, cache_read, output):
        import sqlite3
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, model,"
                " input_tokens, cache_read_tokens, output_tokens) VALUES (?,?,?,?,?,?,?,?,?)",
                (uuid, "s1", "p", "assistant", "2026-11-01T00:00:00Z", model,
                 input_tokens, cache_read, output),
            )

    def _cost(self, model):
        from token_dashboard.db import model_breakdown
        from token_dashboard.pricing import long_context_thresholds
        rows = model_breakdown(self.db, long_context_thresholds=long_context_thresholds(self.p))
        row = next(r for r in rows if r["model"] == model)
        return cost_for(model, row, self.p)["usd"]

    def test_prompt_at_threshold_uses_base_rate(self):
        self._add("a", "claude-haiku-5-5", 0, 100_000, 1_000_000)
        # 100K cache read @ $0.01 + 1M output @ $0.50
        self.assertAlmostEqual(self._cost("claude-haiku-5-5"), 0.001 + 0.50, places=6)

    def test_prompt_over_threshold_uses_long_rate(self):
        self._add("a", "claude-haiku-5-5", 1, 100_000, 1_000_000)
        # 1 input @ $0.50 + 100K cache read @ $0.05 + 1M output @ $2.50
        self.assertAlmostEqual(self._cost("claude-haiku-5-5"), 0.0000005 + 0.005 + 2.50, places=5)

    def test_mixed_turns_split_per_message(self):
        self._add("a", "claude-haiku-5-5", 0, 50_000, 1_000_000)
        self._add("b", "claude-haiku-5-5", 0, 200_000, 1_000_000)
        expected = (50_000 * 0.01 + 1_000_000 * 0.50 + 200_000 * 0.05 + 1_000_000 * 2.50) / 1_000_000
        self.assertAlmostEqual(self._cost("claude-haiku-5-5"), expected, places=6)

    def test_model_without_long_tier_unaffected(self):
        self._add("a", "claude-opus-5-5", 0, 200_000, 1_000_000)
        self.assertAlmostEqual(self._cost("claude-opus-5-5"), 200_000 * 0.20 / 1e6 + 20.00, places=6)


class PlanFormatTests(unittest.TestCase):
    def setUp(self):
        self.p = load_pricing(PRICING)

    def test_api_plan_returns_raw(self):
        out = format_for_user(12.34, "api", self.p)
        self.assertEqual(out["display_usd"], 12.34)
        self.assertIsNone(out["subscription_usd"])

    def test_pro_plan_returns_subscription_subtitle(self):
        out = format_for_user(12.34, "pro", self.p)
        self.assertEqual(out["subscription_usd"], 20)
        self.assertIn("Pro", out["subtitle"])


if __name__ == "__main__":
    unittest.main()
