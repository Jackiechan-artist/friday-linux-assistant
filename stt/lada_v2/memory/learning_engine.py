"""
LADA - Learning Engine v2

Upgrades from audit:
  ✔ Failure counter decays over time (old failures less relevant)
  ✔ Success weight increases with recency
  ✔ Score = weighted_success / (weighted_success + weighted_failure)
  ✔ Not biased by old stale patterns
  ✔ Exports JSON-serializable weight model

Decay model:
  Each event has a timestamp.
  Weight of event = base_weight * exp(-lambda * age_days)
  Recent success worth more than old success.
  Recent failure worth more than old failure.
  lambda = 0.1 means half-life ~7 days.
"""

import math
import time
import json
from pathlib import Path
from typing import Optional, List
from utils.logger import LADALogger

logger = LADALogger("LEARNING")

DECAY_LAMBDA   = 0.1    # exponential decay constant
MIN_SAMPLES    = 2      # minimum events before trusting a score
NEUTRAL_SCORE  = 0.5    # score when no history

# Persist weights to this file
WEIGHT_FILE = Path(__file__).parent / "success_weight_model.json"


class LearningEngine:
    """
    Adaptive method selector with time-decayed scoring.
    Reads/writes to context_store SQLite + JSON weight model.
    """

    def __init__(self, context_store):
        self.context_store = context_store
        self._weight_cache: dict = {}   # in-memory cache

    # ── Public API ─────────────────────────────────────────

    def get_preferred_method(
        self,
        app_name: str,
        action:   str,
        default_chain: List[str],
    ) -> List[str]:
        """
        Return fallback chain reordered by decayed success score.
        Higher score → earlier in chain.
        """
        scored = []
        for method in default_chain:
            score = self._decayed_score(app_name, action, method)
            scored.append((method, score))
            logger.debug(f"Score {app_name}/{action}/{method}: {score:.3f}")

        scored.sort(key=lambda x: x[1], reverse=True)
        reordered = [m for m, _ in scored]

        if reordered != default_chain:
            logger.info(
                f"Learning reordered [{app_name}/{action}]: "
                f"{default_chain} → {reordered}"
            )

        return reordered

    def record_result(
        self,
        app_name:    str,
        action:      str,
        method:      str,
        success:     bool,
        error:       str       = "",
        duration_ms: float     = 0.0,
    ):
        """Record an execution result for future learning."""
        if not self.context_store:
            return
        if success:
            self.context_store.record_success_pattern(app_name, action, method)
        else:
            self.context_store.record_error_pattern(action, method, error)

        if action == "open_app" and success and duration_ms > 0:
            self.context_store.record_app_launch_time(
                app_name, duration_ms / 1000.0
            )

        # Invalidate cache for this key
        cache_key = f"{app_name}:{action}:{method}"
        self._weight_cache.pop(cache_key, None)

        # Periodically save weight model
        self._save_weights_if_needed()

    def get_expected_wait(self, app_name: str) -> float:
        """Return expected launch time for an app."""
        if not self.context_store:
            return 1.5
        return self.context_store.get_app_launch_time(app_name)

    def get_health_report(self) -> dict:
        """Return patterns that are consistently failing."""
        report = {}
        try:
            conn = self.context_store._get_conn()
            cur  = conn.cursor()
            cur.execute("""
                SELECT action, method, SUM(count) as total
                FROM error_patterns
                GROUP BY action, method
                HAVING total >= 5
                ORDER BY total DESC
            """)
            for action, method, count in cur.fetchall():
                report[f"{action}/{method}"] = f"{count} failures"
            conn.close()
        except Exception:
            pass
        return report

    def suggest_alternatives(self, action: str, failing_method: str) -> List[str]:
        from utils.retry_policy import METHOD_PRIORITY, DEFAULT_PRIORITY
        chain = METHOD_PRIORITY.get(action, DEFAULT_PRIORITY)
        return [m for m in chain if m != failing_method]

    # ── Scoring ────────────────────────────────────────────

    def _decayed_score(
        self,
        app_name: str,
        action:   str,
        method:   str,
    ) -> float:
        """
        Compute time-decayed success score.
        score = W_success / (W_success + W_failure)
        where W = sum(base * exp(-lambda * age_days))
        """
        cache_key = f"{app_name}:{action}:{method}"
        if cache_key in self._weight_cache:
            return self._weight_cache[cache_key]

        if not self.context_store:
            return NEUTRAL_SCORE

        try:
            conn  = self.context_store._get_conn()
            cur   = conn.cursor()
            now_s = time.time()

            # Success events (timestamp + count)
            cur.execute("""
                SELECT timestamp, success_count FROM success_patterns
                WHERE app_name=? AND action=? AND method=?
                ORDER BY timestamp DESC LIMIT 20
            """, (app_name, action, method))
            success_rows = cur.fetchall()

            # Failure events
            cur.execute("""
                SELECT timestamp, count FROM error_patterns
                WHERE action=? AND method=?
                ORDER BY timestamp DESC LIMIT 20
            """, (action, method))
            fail_rows = cur.fetchall()

            conn.close()

            # Total events check
            total_events = (
                sum(r[1] for r in success_rows) +
                sum(r[1] for r in fail_rows)
            )
            if total_events < MIN_SAMPLES:
                self._weight_cache[cache_key] = NEUTRAL_SCORE
                return NEUTRAL_SCORE

            # Weighted sums
            w_success = self._weighted_sum(success_rows, now_s)
            w_failure = self._weighted_sum(fail_rows, now_s)

            if w_success + w_failure == 0:
                score = NEUTRAL_SCORE
            else:
                score = w_success / (w_success + w_failure)

            self._weight_cache[cache_key] = score
            return score

        except Exception as e:
            logger.debug(f"Score error: {e}")
            return NEUTRAL_SCORE

    def _weighted_sum(self, rows: list, now_s: float) -> float:
        """
        Compute decay-weighted sum.
        rows: [(timestamp_unix, count), ...]
        """
        total = 0.0
        for ts, count in rows:
            age_days = (now_s - ts) / 86400.0
            weight   = count * math.exp(-DECAY_LAMBDA * age_days)
            total   += weight
        return total

    # ── Weight model persistence ──────────────────────────

    def _save_weights_if_needed(self):
        """Save in-memory weights to JSON (non-blocking)."""
        try:
            if len(self._weight_cache) % 20 == 0:   # every 20 new entries
                self.save_weight_model()
        except Exception:
            pass

    def save_weight_model(self):
        """Persist current weight cache to JSON file."""
        try:
            data = {
                "saved_at":    time.time(),
                "decay_lambda": DECAY_LAMBDA,
                "scores":      self._weight_cache,
            }
            WEIGHT_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(WEIGHT_FILE, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Weight model saved: {len(self._weight_cache)} entries")
        except Exception as e:
            logger.debug(f"Weight model save error: {e}")

    def load_weight_model(self):
        """Load persisted weight cache from JSON file."""
        try:
            if WEIGHT_FILE.exists():
                with open(WEIGHT_FILE) as f:
                    data = json.load(f)
                self._weight_cache = data.get("scores", {})
                logger.info(
                    f"Weight model loaded: {len(self._weight_cache)} entries "
                    f"(lambda={data.get('decay_lambda', '?')})"
                )
        except Exception as e:
            logger.debug(f"Weight model load error: {e}")
