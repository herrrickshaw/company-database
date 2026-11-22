"""Offline tests for fetch_fi.py (a script that runs at import): urllib and time.sleep are faked."""
import io
import json
import pathlib
import re
import threading
import time
import urllib.request


SRC = pathlib.Path(__file__).resolve().parent.parent / "fetch_fi.py"


class FakeWeb:
    def __init__(self, total, failing=(), empty=()):
        self.total, self.failing, self.empty = total, set(failing), set(empty)
        self.requests, self.headers, self.attempts = [], [], {}
        self.lock = threading.Lock()

    def urlopen(self, req, timeout=None):
        url = getattr(req, "full_url", req)
        page = int(re.search(r"page=(\d+)$", url).group(1))
        with self.lock:
            self.requests.append((page, isinstance(req, str), timeout))
            self.attempts[page] = self.attempts.get(page, 0) + 1
            if not isinstance(req, str):
                self.headers.append(req.get_header("User-agent"))
        if page in self.failing:
            raise OSError("boom")
        n = 0 if page in self.empty else max(0, min(100, self.total - (page - 1) * 100))
        return io.BytesIO(json.dumps({"totalResults": self.total, "companies": [{"i": i} for i in range(n)]}).encode())


def run_script(tmp_path, monkeypatch, web):
    sleeps = []
    monkeypatch.setattr(urllib.request, "urlopen", web.urlopen)
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    # the script derives its output dir from __file__; point that at tmp_path but keep the real
    # filename on the code object so coverage attributes the run to fetch_fi.py
    text = SRC.read_text().replace("pathlib.Path(__file__).parent", repr(str(tmp_path)).join(["pathlib.Path(", ")"]))
    assert str(tmp_path) in text
    g = {"__name__": "__main__"}
    exec(compile(text, str(SRC), "exec"), g)
    return g, sleeps


def test_fetches_all_pages_with_exact_page_count(tmp_path, monkeypatch, capsys):
    web = FakeWeb(total=200)                       # exactly 2 pages, not 3
    run_script(tmp_path, monkeypatch, web)
    out = tmp_path / "raw" / "fi"
    assert sorted(p.name for p in out.iterdir()) == ["page_1.json", "page_2.json"]
    assert len(json.loads((out / "page_2.json").read_text())["companies"]) == 100
    printed = capsys.readouterr().out
    assert "total 200 pages 2" in printed and printed.splitlines()[-1] == "done, fails 0"
    assert "0 pages done, fails 0" in printed
    assert set(web.headers) == {"company-lists/1.0"}
    assert all(t == 60 for _, is_str, t in web.requests if not is_str)


def test_partial_last_page_rounds_up(tmp_path, monkeypatch, capsys):
    web = FakeWeb(total=201)
    run_script(tmp_path, monkeypatch, web)
    assert sorted(p.name for p in (tmp_path / "raw" / "fi").iterdir()) == ["page_1.json", "page_2.json", "page_3.json"]
    assert len(json.loads((tmp_path / "raw" / "fi" / "page_3.json").read_text())["companies"]) == 1
    assert "total 201 pages 3" in capsys.readouterr().out


def test_existing_pages_are_skipped_resumable(tmp_path, monkeypatch):
    out = tmp_path / "raw" / "fi"; out.mkdir(parents=True)
    (out / "page_1.json").write_text("{}")
    web = FakeWeb(total=200)
    _, sleeps = run_script(tmp_path, monkeypatch, web)
    assert (out / "page_1.json").read_text() == "{}"           # untouched
    page_fetches = [p for p, is_str, _ in web.requests if not is_str]
    assert page_fetches == [2]
    assert sleeps == [0.15]                                    # polite delay only after a real fetch


def test_failing_page_retries_with_backoff_and_is_counted(tmp_path, monkeypatch, capsys):
    web = FakeWeb(total=200, failing={2})
    _, sleeps = run_script(tmp_path, monkeypatch, web)
    assert web.attempts[2] == 6
    assert sorted(s for s in sleeps if s != 0.15) == [1, 2, 4, 8, 16, 32]
    assert not (tmp_path / "raw" / "fi" / "page_2.json").exists()
    assert (tmp_path / "raw" / "fi" / "page_1.json").exists()
    assert capsys.readouterr().out.splitlines()[-1] == "done, fails 1"


def test_get_returns_company_count_and_skip_marker(tmp_path, monkeypatch):
    web = FakeWeb(total=150)
    g, _ = run_script(tmp_path, monkeypatch, web)
    assert g["get"](1) == (1, 0)        # already on disk now
    g["OUT"].joinpath("page_2.json").rename(g["OUT"] / "moved.json")
    assert g["get"](2) == (2, 50)       # re-fetched, 50 companies on the short page


def test_empty_page_is_saved_and_not_counted_as_failure(tmp_path, monkeypatch, capsys):
    web = FakeWeb(total=200, empty={2})
    _, sleeps = run_script(tmp_path, monkeypatch, web)
    assert json.loads((tmp_path / "raw" / "fi" / "page_2.json").read_text())["companies"] == []
    assert capsys.readouterr().out.splitlines()[-1] == "done, fails 0"
    assert web.attempts[2] == 1 and sleeps == [0.15, 0.15]
