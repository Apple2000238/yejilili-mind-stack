import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_complete_dashboard_is_bundled_without_household_layers():
    text = (ROOT / "dashboard.html").read_text("utf-8")
    assert "Breath" in text and "Reverie" in text and "Constellations" in text
    assert "Drive Ledger" in text and "Thought Pool" in text
    for removed in ["opening.html", "Atmosphere", "Gravity", "Catroom", "嘉嘉", "Nox"]:
        assert removed not in text


def test_removed_household_modules_stay_absent():
    for name in ["catroom_store.py", "room_store.py", "rhythm_store.py", "opening.html"]:
        assert not (ROOT / name).exists()


def test_public_mcp_surface_still_has_continuity_loop(tmp_path):
    env = dict(os.environ, OMBRE_BUCKETS_DIR=str(tmp_path))
    code = "import server; print(','.join(sorted(server.mcp._tool_manager._tools)))"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            text=True, capture_output=True, check=True)
    tools = set(result.stdout.strip().split(","))
    assert {"hold", "breath", "trace", "wander", "drive", "undercurrent"} <= tools
    assert not {"room", "rhythm"} & tools
