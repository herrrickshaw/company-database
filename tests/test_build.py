"""Tests for build.py: sector maps, India CIN -> NIC/SIC switch, name normalisation and dedupe.

All rows are synthetic (invented names/IDs); no registry download is made. DB/RAW are redirected to tmp_path.
"""
import zipfile

import pandas as pd
import pytest

import build


# --------------------------------------------------------------------------- UK SIC 2007 sections
@pytest.mark.parametrize("code,sector", [
    ("01110", "Agriculture, forestry & fishing"),
    ("10110", "Manufacturing"), ("33190", "Manufacturing"),
    ("35110", "Energy & utilities"),
    ("62012", "Information & communication"),
    ("68100", "Real estate"),
    ("86101", "Health & social work"),
    ("99000", "Extraterritorial"),
    (" 47110", "Wholesale & retail"),
    ("", None), (None, None), ("abc", None),
    ("04000", None),            # no division 04 in SIC 2007
])
def test_sic_sector(code, sector):
    assert build.sic_sector(code) == sector


def test_sic_99999_dormant_company_is_not_extraterritorial():
    """Companies House code 99999 = 'Dormant Company' (no activity). Division 99 proper (99000) is
    extraterritorial organisations. Mapping 99999 to Extraterritorial put ~97k dormant UK companies
    (docs/counts_by_country_sector.csv: GB,Extraterritorial,96966) into that sector."""
    assert build.sic_sector("99999") is None


# --------------------------------------------------------------------------- NIC-2004 (ISIC Rev 3.1 divisions)
@pytest.mark.parametrize("div,sector", [
    (1, "Agriculture, forestry & fishing"), (5, "Agriculture, forestry & fishing"),
    (10, "Mining & quarrying"), (15, "Manufacturing"), (37, "Manufacturing"),
    (40, "Energy & utilities"), (41, "Water, waste & remediation"), (45, "Construction"),
    (52, "Wholesale & retail"), (64, "Information & communication"), (65, "Finance & insurance"),
    (71, "Admin & support services"), (72, "Information & communication"), (74, "Professional & scientific"),
    (85, "Health & social work"), (90, "Water, waste & remediation"), (92, "Arts & recreation"),
    (6, None), (99 + 1, None),
])
def test_nic_sector(div, sector):
    assert build.nic_sector(div) == sector


def test_nic_2004_division_93_is_other_services():
    """NIC-2004 / ISIC Rev 3.1 division 93 = 'Other service activities' (laundry, hairdressing, funeral),
    not recreation (that is 92)."""
    assert build.nic_sector(93) == "Other services"


def test_nic_2004_division_99_is_extraterritorial():
    """ISIC Rev 3.1 division 99 = extraterritorial organisations; 95-97 are households."""
    assert build.nic_sector(99) == "Extraterritorial"
    assert build.nic_sector(95) == "Households"


def test_damodaran_and_iso_maps_use_known_sections():
    sections = {n for _, _, n in build.SIC}
    assert {v for v in build.DAMO.values() if v} <= sections
    assert {n for _, _, n in build.NIC} <= sections
    assert all(len(v) == 2 and v.isupper() for v in build.ISO.values())


# --------------------------------------------------------------------------- DB-backed
@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "DB", tmp_path / "t.duckdb")
    monkeypatch.setattr(build, "RAW", tmp_path / "raw")
    (tmp_path / "raw").mkdir()
    return tmp_path


def norm(name):
    c = build.db()
    return c.execute(f"SELECT {build.NORM} FROM (SELECT ? AS name)", [name]).fetchone()[0]


@pytest.mark.parametrize("raw,nn", [
    ("Alpha Steels Private Limited", "alpha steels"),
    ("ALPHA STEELS PVT. LTD.", "alpha steels"),
    ("The Alpha Steels Co. Ltd", "alpha steels"),
    ("Alpha & Beta GmbH", "alpha and beta"),
    ("Société Générale S.A.", "societe generale"),
    ("Alpha Steels (India) Limited", "alpha steels"),
    ("O'Neil Holdings PLC", "oneil holdings"),
])
def test_norm(tmpdb, raw, nn):
    assert norm(raw) == nn


def insert(rows):
    c = build.db()
    c.executemany("INSERT INTO companies VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  [(r["source"], r["id"], r["name"], r["country"], None, None, None, None, r.get("sector"), None, None) for r in rows])
    c.close()


def test_dedupe(tmpdb):
    insert([
        dict(source="ch", id="C1", name="Alpha Widgets Limited", country="GB", sector="Manufacturing"),
        dict(source="damodaran", id="LSE:ALW", name="ALPHA WIDGETS PLC", country="GB"),              # merges into C1
        dict(source="ch", id="C2", name="Beta Foods Ltd", country="GB"),
        dict(source="ch", id="C3", name="Beta Foods Limited", country="GB"),                          # registry homonyms both kept
        dict(source="damodaran", id="LSE:BF", name="Beta Foods plc", country="GB", sector="Manufacturing"),  # 2 registry hits -> kept alone
        dict(source="defence", id="D1", name="Gamma Arms AB", country="SE"),
        dict(source="damodaran", id="STO:GAM", name="Gamma Arms", country="SE", sector="Manufacturing"),  # rank 3 beats defence (4)
        dict(source="damodaran", id="NYSE:DLT", name="Delta Corp", country="US"),
        dict(source="damodaran", id="LSE:DLT", name="Delta Corp", country="GB"),                       # different country: not merged
        dict(source="gleif", id="LEI1", name="Alpha Widgets Limited", country="GB"),                   # gleif excluded
    ])
    build.dedupe()
    c = build.db()
    rows = {r[0]: r[1:] for r in c.execute(
        "SELECT source_id, sources, listed_ticker, sector FROM companies_dedup ORDER BY 1").fetchall()}
    assert set(rows) == {"C1", "C2", "C3", "LSE:BF", "STO:GAM", "NYSE:DLT", "LSE:DLT"}
    assert rows["C1"] == ("ch+damodaran", "LSE:ALW", "Manufacturing")
    assert rows["STO:GAM"][0] == "damodaran+defence"


def _gleif_zip(tmp_path, cins):
    df = pd.DataFrame({"LEI": [f"LEI{i}" for i in range(len(cins))],
                       "Entity.LegalName": [f"Synthetic Co {i} Private Limited" for i in range(len(cins))],
                       "Entity.LegalAddress.City": "Pune", "Entity.EntityStatus": "ACTIVE",
                       "Entity.RegistrationAuthority.RegistrationAuthorityID": ["RA000394"] * (len(cins) - 1) + ["RA000999"],
                       "Entity.RegistrationAuthority.RegistrationAuthorityEntityID": cins})
    csvp = tmp_path / "golden.csv"; df.to_csv(csvp, index=False)
    zp = tmp_path / "raw" / "golden.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(csvp, "golden.csv")
    return zp


def test_load_in_mca_nic_switch_at_2023(tmpdb):
    cins = ["U15100MH2010PTC000001",   # NIC-2004 div 15 food -> Manufacturing
            "U62010MH2023PTC000002",   # 2023: NIC-2008 = SIC 2007 div 62 -> Information & communication
            "U72200KA2022PTC000003",   # NIC-2004 div 72 computer -> Information & communication
            "U45200DL2024PTC000004",   # 2024: SIC div 45 = motor trade -> Wholesale & retail (NIC-2004 45 would be Construction)
            "BADCIN",                  # malformed: dropped
            "U15100MH2010PTC000009"]   # not MCA (RA000999): dropped
    build.load("in_mca", _gleif_zip(tmpdb, cins))
    c = build.db()
    got = dict(c.execute("SELECT source_id, sector FROM companies WHERE source='in_mca'").fetchall())
    assert got == {"U15100MH2010PTC000001": "Manufacturing",
                   "U62010MH2023PTC000002": "Information & communication",
                   "U72200KA2022PTC000003": "Information & communication",
                   "U45200DL2024PTC000004": "Wholesale & retail"}


def test_load_is_idempotent_per_source(tmpdb):
    z = _gleif_zip(tmpdb, ["U15100MH2010PTC000001", "U15100MH2010PTC000002"])
    build.load("in_mca", z); build.load("in_mca", z)
    assert build.db().execute("SELECT count(*) FROM companies").fetchone()[0] == 1


def test_fetch_rejects_unknown_source(tmp_path, monkeypatch):
    # `build.py fetch typo` used to return None silently (no download, no error)
    monkeypatch.setattr(build, "RAW", tmp_path / "raw")
    monkeypatch.setattr(build.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("must not touch the network"))
    with pytest.raises(ValueError, match="nope"):
        build.fetch("nope")
