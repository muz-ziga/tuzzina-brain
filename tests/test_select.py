"""Select CLI tests. Stubs TuzzinaClient.integrations()/get_integration()
to drive the interactive and non-TTY flows without network."""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

# Insert src/ at the front of sys.path for project imports.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# The strategy-selection CLI lives at src/strategy_select.py.
# (Never src/select.py: that name shadows Python's stdlib `select`
# on import and breaks any fresh process importing socket/urllib
# afterwards with a circular ImportError.)
_SELECT_PY = Path(__file__).parent.parent / "src" / "strategy_select.py"


def _load_select_mod():
    spec = importlib.util.spec_from_file_location("brain_select_mod",
                                                  str(_SELECT_PY))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["brain_select_mod"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop("brain_select_mod", None)
        raise
    return mod


class FakeResp:
    def __init__(self, payload, code=200):
        self.payload = payload
        self.code = code

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        if self.code >= 400:
            raise urllib.error.HTTPError("u", self.code, "err", {}, None)
        return self

    def __exit__(self, *a):
        return False


import urllib.error
import urllib.request

from channels.strategy_store import path_for
from tuzzina.client import TuzzinaClient, TuzzinaError


def _make_client(items, lookup=None):
    """Return a TuzzinaClient whose integrations()/get_integration()
    are stubbed."""
    client = TuzzinaClient("http://x/api", "k")
    client.integrations = lambda: items
    def _lookup(iid):
        for it in items:
            if it.get("id") == iid:
                return it
        raise TuzzinaError(f"integration {iid!r} not found in Tuzzina")
    client.get_integration = _lookup
    return client


# Capture the select.py CLI without invoking it through __main__,
# so we can test the non-TTY flow without affecting the real env.
def _run_select(argv, stdin_text, *, base_dir=None, monkey_key=True):
    if monkey_key:
        os.environ["TUZZINA_API_KEY"] = "k"
    select_mod = _load_select_mod()
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        if stdin_text is not None:
            sys.stdin = io.StringIO(stdin_text)
        try:
            rc = select_mod.main(argv + (["--base-dir", base_dir]
                                          if base_dir else []))
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        finally:
            sys.stdin = sys.__stdin__
    return rc, buf_out.getvalue(), buf_err.getvalue()


def _run_select_with_client(argv, stdin_text, *, base_dir, client):
    """Run select.main with a custom client. Avoids the real
    TuzzinaClient construction by replacing _build_client AFTER
    load (so the load does not reset our patch)."""
    os.environ["TUZZINA_API_KEY"] = "k"
    select_mod = _load_select_mod()
    select_mod._build_client = lambda base, key: client
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        sys.stdin = io.StringIO(stdin_text) if stdin_text is not None \
            else sys.__stdin__
        try:
            rc = select_mod.main(argv + (["--base-dir", base_dir]
                                          if base_dir else []))
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        finally:
            sys.stdin = sys.__stdin__
    return rc, buf_out.getvalue(), buf_err.getvalue()


class DiscoveryTest(unittest.TestCase):
    def test_empty_list_does_not_crash(self):
        os.environ["TUZZINA_API_KEY"] = "k"
        client = _make_client([])
        items = client.integrations()
        self.assertEqual(items, [])

    def test_selected_integration_exists(self):
        os.environ["TUZZINA_API_KEY"] = "k"
        client = _make_client([
            {"id": "i1", "name": "Juzzir Facebook",
             "identifier": "facebook", "picture": "x"},
            {"id": "i2", "name": "Muzziga", "identifier": "facebook"},
        ])
        integ = client.get_integration("i1")
        self.assertEqual(integ["name"], "Juzzir Facebook")
        self.assertEqual(integ["identifier"], "facebook")

    def test_selected_integration_missing_raises(self):
        os.environ["TUZZINA_API_KEY"] = "k"
        client = _make_client([{"id": "i1", "name": "Juzzir"}])
        with self.assertRaises(TuzzinaError):
            client.get_integration("nope")


class NonTtyFlowTest(unittest.TestCase):
    def test_non_tty_saves_strategy(self):
        tmp = tempfile.mkdtemp(prefix="tbra_select_")
        items = [{"id": "cmtr8cxod", "name": "Juzzir",
                   "identifier": "facebook"}]
        payload = json.dumps({
            "integration_id": "cmtr8cxod",
            "strategy": {
                "brand": {"tone": "loud", "audience": "devs"},
                "hashtags": {"enabled": True, "max": 3},
                "links_policy": "hide",
            },
        })
        rc, out, err = _run_select_with_client(
            ["--stdin-json"], payload, base_dir=tmp,
            client=_make_client(items))
        self.assertEqual(rc, 0, msg=f"err={err}")
        result = json.loads(out)
        self.assertEqual(result["integration_id"], "cmtr8cxod")
        self.assertEqual(result["platform"], "facebook")
        path = path_for("cmtr8cxod", base_dir=tmp)
        self.assertTrue(path.is_file())

    def test_non_tty_rejects_traversal_id(self):
        tmp = tempfile.mkdtemp(prefix="tbra_select_")
        payload = json.dumps({
            "integration_id": "../etc/passwd",
            "strategy": {"brand": {"tone": "x"}},
        })
        rc, out, err = _run_select_with_client(
            ["--stdin-json"], payload, base_dir=tmp,
            client=_make_client([]))
        self.assertNotEqual(rc, 0, msg=f"err={err}")
        self.assertIn("integration_id", err.lower())

    def test_non_tty_rejects_missing_integration_id(self):
        tmp = tempfile.mkdtemp(prefix="tbra_select_")
        rc, out, err = _run_select(
            ["--stdin-json"], json.dumps({}), base_dir=tmp)
        self.assertNotEqual(rc, 0)
        self.assertIn("--integration-id", err.lower())

    def test_non_tty_rejects_integration_not_in_tuzzina(self):
        tmp = tempfile.mkdtemp(prefix="tbra_select_")
        items = [{"id": "i1", "name": "Juzzir", "identifier": "facebook"}]
        payload = json.dumps({
            "integration_id": "unknown",
            "strategy": {"brand": {"tone": "x"}},
        })
        rc, out, err = _run_select_with_client(
            ["--stdin-json"], payload, base_dir=tmp,
            client=_make_client(items))
        self.assertNotEqual(rc, 0, msg=f"err={err}")
        self.assertIn("not found", err.lower())


if __name__ == "__main__":
    unittest.main()
