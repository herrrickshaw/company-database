"""Offline tests for build.py loaders, fetchers, lists/export and the CLI dispatch.

All inputs are synthetic files written under tmp_path; urllib is faked; DB/RAW/HERE are redirected to tmp_path.
"""
import importlib.util
import io
import json
import pathlib
import sys
import urllib.parse
import urllib.request
import zipfile

import duckdb
import pandas as pd
import pytest

import build


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "DB", tmp_path / "t.duckdb")
    monkeypatch.setattr(build, "RAW", tmp_path / "raw")
    monkeypatch.setattr(build, "HERE", tmp_path)
    (tmp_path / "raw").mkdir()
    return tmp_path


def rows(sql, params=()):
    c = build.db()
    try:
        return c.execute(sql, list(params)).fetchall()
    finally:
        c.close()


def zip_csv(tmp_path, df, name="x.zip", inner="data.csv"):
    csvp = tmp_path / "src.csv"
    df.to_csv(csvp, index=False)
    zp = tmp_path / name
    with zipfile.ZipFile(zp, "w") as z:
        z.write(csvp, inner)
    return zp


# --------------------------------------------------------------------------- duckdb import fallback
def test_vartype_import_falls_back_to_duckdb_typing(monkeypatch):
    """When duckdb.sqltypes is missing (older duckdb) build.py imports VARCHAR from duckdb.typing."""
    sentinel = object()
    fake_typing = type(sys)("duckdb.typing")
    fake_typing.VARCHAR = sentinel
    monkeypatch.setitem(sys.modules, "duckdb.sqltypes", None)   # makes `from duckdb.sqltypes import ...` raise ImportError
    monkeypatch.setitem(sys.modules, "duckdb.typing", fake_typing)
    src = pathlib.Path(build.__file__)
    spec = importlib.util.spec_from_file_location("build_fallback_copy", src)
    mod = importlib.util.module_from_spec(spec)
    # compile under the real filename so the fallback line counts toward coverage of build.py
    code = compile(src.read_text(), str(src), "exec")
    mod.__dict__["__file__"] = str(src)
    exec(code, mod.__dict__)
    assert mod.VARCHAR is sentinel
    assert "build_fallback_copy" not in sys.modules


def test_db_creates_schema_and_functions(tmpdb):
    c = build.db()
    cols = [r[0] for r in c.execute("DESCRIBE companies").fetchall()]
    assert cols == ["source", "source_id", "name", "country", "city", "website",
                    "sector_scheme", "sector_code", "sector", "size", "status"]
    assert c.execute("SELECT nic_sector(15), sic_sector('62012'), sic_sector(NULL)").fetchone() == \
        ("Manufacturing", "Information & communication", None)
    c.close()
    assert build.db().execute("SELECT count(*) FROM companies").fetchone() == (0,)   # IF NOT EXISTS: reopen is fine


# --------------------------------------------------------------------------- fetch / dl / unzip
def test_dl_downloads_and_returns_dest(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(urllib.request, "urlretrieve", lambda u, d: calls.append((u, d)))
    assert build.dl("http://x/y.zip", "dest.zip") == "dest.zip"
    assert calls == [("http://x/y.zip", "dest.zip")]
    assert "downloading http://x/y.zip" in capsys.readouterr().out


def test_fetch_gleif_reads_latest_metadata(tmpdb, monkeypatch):
    seen = []
    meta = {"data": {"full_file": {"csv": {"url": "http://gleif/full.zip"}}}}
    monkeypatch.setattr(urllib.request, "urlopen", lambda u: seen.append(u) or io.BytesIO(json.dumps(meta).encode()))
    monkeypatch.setattr(urllib.request, "urlretrieve", lambda u, d: seen.append((u, d)))
    out = build.fetch("gleif")
    assert out == build.RAW / "gleif_lei2.csv.zip"
    assert seen[0] == "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/lei2/latest"
    assert seen[1] == ("http://gleif/full.zip", build.RAW / "gleif_lei2.csv.zip")


def test_fetch_ch_picks_zip_from_index_page(tmpdb, monkeypatch):
    html = ('<a href="BasicCompanyDataAsOneFile-2026-10-01.zip">a</a>'
            '<a href="BasicCompanyData-2026-10-01-part1_5.zip">p</a>')
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda u: io.BytesIO(html.encode()))
    monkeypatch.setattr(urllib.request, "urlretrieve", lambda u, d: seen.append((u, d)))
    out = build.fetch("ch")
    assert out == build.RAW / "BasicCompanyDataAsOneFile-2026-10-01.zip"
    assert seen == [("https://download.companieshouse.gov.uk/BasicCompanyDataAsOneFile-2026-10-01.zip", out)]


def test_fetch_creates_raw_dir_and_ignores_unknown_source(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "RAW", tmp_path / "newraw")
    monkeypatch.setattr(urllib.request, "urlopen", lambda u: pytest.fail("no network for unknown source"))
    assert build.fetch("nope") is None
    assert (tmp_path / "newraw").is_dir()


def test_unzip_extracts_into_stem_dir(tmpdb):
    zp = zip_csv(tmpdb, pd.DataFrame({"a": [1]}), name="Bundle.zip", inner="inner.csv")
    out = build.unzip(zp)
    assert out == build.RAW / "Bundle_x" / "inner.csv"
    assert out.read_text().splitlines() == ["a", "1"]
    assert build.unzip(zp) == out   # second call: directory already exists


# --------------------------------------------------------------------------- load: each source
def test_load_gleif_maps_columns(tmpdb):
    df = pd.DataFrame({"LEI": ["L1", "L2"], "Entity.LegalName": ["Alpha Ltd", "Beta SA"],
                       "Entity.LegalAddress.Country": ["GB", "FR"], "Entity.LegalAddress.City": ["Leeds", "Lyon"],
                       "Entity.EntityStatus": ["ACTIVE", "INACTIVE"]})
    build.load("gleif", zip_csv(tmpdb, df))
    assert rows("SELECT * FROM companies ORDER BY source_id") == [
        ("gleif", "L1", "Alpha Ltd", "GB", "Leeds", None, None, None, None, None, "ACTIVE"),
        ("gleif", "L2", "Beta SA", "FR", "Lyon", None, None, None, None, None, "INACTIVE")]


def test_load_ch_keeps_only_active_and_maps_sic(tmpdb):
    df = pd.DataFrame({
        "CompanyName": ["Soft Co", "Dormant Co", "Gone Co", "NoSic Co"],
        "CompanyNumber": ["00000001", "00000002", "00000003", "00000004"],
        "RegAddress.PostTown": ["LEEDS", "HULL", "YORK", "BATH"],
        "CompanyStatus": ["Active", "Active", "Dissolved", "Active"],
        "URI": ["http://u/1", "http://u/2", "http://u/3", "http://u/4"],
        "SICCode.SicText_1": ["62012 - Business software", "99999 - Dormant Company", "10110 - Meat", None],
    })
    build.load("ch", zip_csv(tmpdb, df))
    got = {r[0]: r for r in rows("SELECT source_id, name, country, city, website, sector_scheme, sector_code, sector, status FROM companies")}
    assert set(got) == {"00000001", "00000002", "00000004"}          # Dissolved dropped
    assert got["00000001"] == ("00000001", "Soft Co", "GB", "LEEDS", "http://u/1", "UK SIC 2007", "62012",
                               "Information & communication", "Active")
    assert got["00000002"][6:8] == ("99999", None)                  # dormant is not Extraterritorial
    assert got["00000004"][6:8] in {("", None), (None, None)}


def test_load_damodaran_maps_country_and_industry(tmpdb):
    pq = tmpdb / "d.parquet"
    pd.DataFrame({"Exchange:Ticker": ["LSE:AAA", "NYSE:BBB", "XXX:CCC", "NYSE:DDD"],
                  "Company Name": ["Aaa plc", "Bbb Inc", "Ccc", "Ddd"],
                  "Country": ["United Kingdom", "United States", "Atlantis", "India"],
                  "Industry Group": ["Software (System & Application)", "Steel", "Steel", "Diversified"]}).to_parquet(pq)
    build.load("damodaran", pq)
    got = {r[0]: r[1:] for r in rows(
        "SELECT source_id, country, sector_scheme, sector_code, sector, status, city, website, size FROM companies")}
    assert got["LSE:AAA"] == ("GB", "Damodaran industry group", "Software (System & Application)",
                              "Information & communication", "Listed", None, None, None)
    assert got["NYSE:BBB"][0] == "US" and got["NYSE:BBB"][3] == "Manufacturing"
    assert got["XXX:CCC"][0] is None           # unknown country name
    assert got["NYSE:DDD"][3] is None          # 'Diversified' has no sector


def test_load_damodaran_replaces_previous_rows(tmpdb):
    pq = tmpdb / "d.parquet"
    pd.DataFrame({"Exchange:Ticker": ["A:1"], "Company Name": ["One"], "Country": ["India"],
                  "Industry Group": ["Steel"]}).to_parquet(pq)
    build.load("damodaran", pq)
    build.load("damodaran", pq)
    assert rows("SELECT count(*) FROM companies") == [(1,)]


def test_load_in_mca_prints_row_count(tmpdb, capsys):
    df = pd.DataFrame({
        "Entity.LegalName": ["A Pvt Ltd"], "Entity.LegalAddress.City": ["Pune"], "Entity.EntityStatus": ["ACTIVE"],
        "Entity.RegistrationAuthority.RegistrationAuthorityID": ["RA000394"],
        "Entity.RegistrationAuthority.RegistrationAuthorityEntityID": ["L15100MH1990PLC000001"]})
    build.load("in_mca", zip_csv(tmpdb, df))
    assert capsys.readouterr().out.strip().endswith("in_mca 1 rows")
    assert rows("SELECT sector_scheme, sector_code, city, status, country FROM companies") == \
        [("NIC (from CIN)", "15100", "Pune", "ACTIVE", "IN")]


def test_load_in_mca_cin_regex_shape(tmpdb):
    """Only [LU] + 5 digits + 2 letters + 4 digits + 3 letters + 6 digits is accepted."""
    cins = ["L15100MH1990PLC000001", "X15100MH1990PLC000001", "U15100MH1990PLC00001",
            "U15100MH1990PLC0000011", "U15100mh1990PLC000001", "U15100MH1990PLC000001 "]
    df = pd.DataFrame({
        "Entity.LegalName": "N", "Entity.LegalAddress.City": "c", "Entity.EntityStatus": "ACTIVE",
        "Entity.RegistrationAuthority.RegistrationAuthorityID": "RA000394",
        "Entity.RegistrationAuthority.RegistrationAuthorityEntityID": cins})
    build.load("in_mca", zip_csv(tmpdb, df))
    assert [r[0] for r in rows("SELECT source_id FROM companies")] == ["L15100MH1990PLC000001"]


def _iem_file(tmp_path):
    pq = tmp_path / "iem.parquet"
    pd.DataFrame({
        "cin": ["U15100MH2010PTC000001", "U15100MH2010PTC000001", "U62010MH2023PTC000002",
                "U45200DL2024PTC000003", "BAD", None, "U15100MH2011PTC000004"],
        "company_name": ["Old Name", "New Name", "Y Ltd", "Z Ltd", "Bad", "Null", "Already Ltd"],
        "month": ["2024-01", "2024-06", "2024-01", "2024-01", "2024-01", "2024-01", "2024-01"],
        "mca_status": ["Active", "Strike Off", "Active", "Active", "Active", "Active", "Active"]}).to_parquet(pq)
    return pq


def test_load_iem_cin_latest_month_wins_and_skips_in_mca(tmpdb):
    build.db().execute("INSERT INTO companies VALUES ('in_mca','U15100MH2011PTC000004','Already Ltd','IN',NULL,NULL,NULL,NULL,NULL,NULL,NULL)")
    build.load("iem_cin", _iem_file(tmpdb))
    got = {r[0]: r[1:] for r in rows(
        "SELECT source_id, name, sector_code, sector, status, country FROM companies WHERE source='iem_cin'")}
    assert set(got) == {"U15100MH2010PTC000001", "U62010MH2023PTC000002", "U45200DL2024PTC000003"}
    assert got["U15100MH2010PTC000001"] == ("New Name", "15100", "Manufacturing", "Strike Off", "IN")   # latest month
    assert got["U62010MH2023PTC000002"][2] == "Information & communication"   # >=2023 -> SIC table
    assert got["U45200DL2024PTC000003"][2] == "Wholesale & retail"            # SIC 45, not NIC 45 (Construction)


def test_load_iem_cin_year_2022_uses_nic_and_2023_uses_sic(tmpdb):
    pq = tmpdb / "iem.parquet"
    pd.DataFrame({"cin": ["U45200DL2022PTC000001", "U45200DL2023PTC000002"], "company_name": ["a", "b"],
                  "month": ["2024-01"] * 2, "mca_status": ["Active"] * 2}).to_parquet(pq)
    build.load("iem_cin", pq)
    got = dict(rows("SELECT source_id, sector FROM companies"))
    assert got == {"U45200DL2022PTC000001": "Construction", "U45200DL2023PTC000002": "Wholesale & retail"}


def test_load_defence_maps_country_and_industry_code(tmpdb):
    tsv = tmpdb / "def.tsv"
    pd.DataFrame({
        "registry_id": [1, 2, 3, 4, 5, 6],
        "company": ["A", "B", "C", "D", "E", "F"],
        "country": ["UK", "US", "France", "Narnia", "Norway", "Sweden"],
        "source_url": ["u1", "u2", "u3", "u4", "u5", "u6"],
        "industry_code": ["SIC 3040 aerospace", "SIC 4581 airports", "NAF 30.30Z Aerospace", "KSIC 25 metal",
                          "bogus", None],
        "size_signal": ["s1", "s2", "s3", "s4", "s5", "s6"]}).to_csv(tsv, sep="\t", index=False)
    build.load("defence", tsv)
    got = {r[0]: r[1:] for r in rows(
        "SELECT source_id, country, sector, website, size, status, sector_scheme, sector_code FROM companies")}
    assert got["1"][:2] == ("GB", "Manufacturing")        # 'UK' alias
    assert got["2"][:2] == ("US", "Wholesale & retail")   # SIC 45xx
    assert got["3"][:2] == ("FR", "Manufacturing")
    assert got["4"][:2] == (None, "Manufacturing")        # unknown country, KSIC division 25
    assert got["5"][1] is None                            # no scheme prefix
    assert got["6"][1] is None                            # NaN industry code
    assert got["1"][2:] == ("u1", "s1", "defence-sector", "defence registry", "SIC 3040 aerospace")


def test_load_fr_sirene_nomenclature_switch(tmpdb):
    pq = tmpdb / "fr.parquet"
    pd.DataFrame({
        "siren": ["1", "2", "3", "4", "5", "6", "7"],
        "denominationUniteLegale": ["rev2", "rev1", "n93", "nap", "inactive", "noname", "nonomen"],
        "nomenclatureActivitePrincipaleUniteLegale": ["NAFRev2", "NAFRev1", "NAF1993", "NAP", "NAFRev2", "NAFRev2", None],
        "activitePrincipaleUniteLegale": ["62.01Z", "72.2Z", "15.1A", "99.99", "62.01Z", "62.01Z", "62.01Z"],
        "categorieEntreprise": ["PME", "GE", "ETI", "PME", "PME", "PME", "PME"],
        "etatAdministratifUniteLegale": ["A", "A", "A", "A", "C", "A", "A"]}
    ).assign(denominationUniteLegale=lambda d: d.denominationUniteLegale.where(d.siren != "6", None)).to_parquet(pq)
    build.load("fr_sirene", pq)
    got = {r[0]: r[1:] for r in rows(
        "SELECT source_id, name, sector_scheme, sector, size, status, country FROM companies")}
    assert set(got) == {"1", "2", "3", "4", "7"}      # inactive (C) and unnamed dropped
    assert got["1"] == ("rev2", "NAF NAFRev2", "Information & communication", "PME", "Active", "FR")
    assert got["2"][2] == "Information & communication"     # NAF rev1 -> NIC-2004 division 72
    assert got["3"][2] == "Manufacturing"                   # NAF1993 division 15
    assert got["4"][2] is None                              # NAP skipped
    assert got["7"][1] == "NAF ?" and got["7"][2] is None   # missing nomenclature


def _ee_parquet(tmp_path):
    c = duckdb.connect()
    c.execute("""CREATE TABLE ee(ariregistri_kood BIGINT, nimi VARCHAR, yldandmed STRUCT(
        staatus VARCHAR,
        teatatud_tegevusalad STRUCT(emtak_kood VARCHAR, on_pohitegevusala BOOLEAN, lopp_kpv DATE)[],
        info_majandusaasta_aruannetest STRUCT(tegevusala_emtak_kood VARCHAR)[]))""")
    c.execute("""INSERT INTO ee VALUES
      (1,'main',  {'staatus':'R','teatatud_tegevusalad':[{'emtak_kood':'47111','on_pohitegevusala':false,'lopp_kpv':NULL},
                                                       {'emtak_kood':'62011','on_pohitegevusala':true,'lopp_kpv':NULL}],
                   'info_majandusaasta_aruannetest':[{'tegevusala_emtak_kood':'10110'}]}),
      (2,'mainended', {'staatus':'R','teatatud_tegevusalad':[{'emtak_kood':'62011','on_pohitegevusala':true,'lopp_kpv':DATE '2020-01-01'},
                                                         {'emtak_kood':'55101','on_pohitegevusala':false,'lopp_kpv':NULL}],
                   'info_majandusaasta_aruannetest':[]}),
      (3,'annual', {'staatus':'R','teatatud_tegevusalad':[],
                   'info_majandusaasta_aruannetest':[{'tegevusala_emtak_kood':'10110'},{'tegevusala_emtak_kood':'86101'}]}),
      (4,'struck', {'staatus':'N','teatatud_tegevusalad':[{'emtak_kood':'62011','on_pohitegevusala':true,'lopp_kpv':NULL}],
                   'info_majandusaasta_aruannetest':[]}),
      (6,'firstopen', {'staatus':'R','teatatud_tegevusalad':[{'emtak_kood':'55101','on_pohitegevusala':false,'lopp_kpv':NULL},
                                                         {'emtak_kood':'47111','on_pohitegevusala':false,'lopp_kpv':NULL}],
                   'info_majandusaasta_aruannetest':[]}),
      (5,'nothing', {'staatus':'R','teatatud_tegevusalad':[],'info_majandusaasta_aruannetest':[]})""")
    p = tmp_path / "ee.parquet"
    c.execute(f"COPY ee TO '{p}' (FORMAT parquet)")
    return p


DUCKDB_2 = int(duckdb.__version__.split(".")[0]) >= 2


def _single_arrow_db(monkeypatch):
    """build.db() with the deprecated `x -> ...` lambda syntax re-enabled (duckdb>=2 rejects it by default)."""
    orig = build.db

    def db():
        c = orig()
        try:
            c.execute("SET lambda_syntax='ENABLE_SINGLE_ARROW'")
        except duckdb.Error:
            pass
        return c
    monkeypatch.setattr(build, "db", db)


@pytest.mark.xfail(DUCKDB_2, strict=True, raises=duckdb.BinderException,
                   reason="build.py:load('ee_ariregister') uses the deprecated `x -> ...` lambda arrow, which duckdb>=2 "
                          "rejects at bind time, so the whole Estonian load fails on current duckdb")
def test_load_ee_ariregister_works_on_default_lambda_syntax(tmpdb):
    build.load("ee_ariregister", _ee_parquet(tmpdb))
    assert rows("SELECT count(*) FROM companies") == [(5,)]


def test_load_ee_ariregister_activity_precedence(tmpdb, monkeypatch):
    _single_arrow_db(monkeypatch)
    build.load("ee_ariregister", _ee_parquet(tmpdb))
    got = {r[0]: r[1:] for r in rows(
        "SELECT source_id, name, sector_code, sector, status, sector_scheme, country FROM companies")}
    assert set(got) == {"1", "2", "3", "5", "6"}          # staatus != 'R' dropped
    assert got["1"][1:3] == ("62011", "Information & communication")   # open main activity beats other / annual
    assert got["2"][1:3] == ("55101", "Hospitality")                   # main ended -> first open activity
    assert got["3"][1:3] == ("86101", "Health & social work")          # last annual-report activity
    assert got["5"][1:3] == (None, None)
    assert got["6"][1:3] == ("55101", "Hospitality")                   # several open activities: the first one
    assert got["1"][3:] == ("Registered", "EMTAK 2008", "EE")


@pytest.mark.xfail(strict=True, raises=TypeError,
                   reason="build.py:nic_sector compares `a <= None` and raises TypeError for None")
def test_nic_sector_none_returns_none():
    """sic_sector() tolerates None (the UDF is registered with null_handling='special'); nic_sector() should too."""
    assert build.nic_sector(None) is None


@pytest.mark.xfail(strict=True, raises=duckdb.InvalidInputException,
                   reason="build.py:load(fr_sirene): try_cast of an unparsable NAFRev1/NAF1993 code gives NULL, the "
                          "nic_sector UDF raises TypeError on None and the whole load aborts")
def test_load_fr_sirene_unparsable_naf_rev1_code_gives_null_sector(tmpdb):
    pq = tmpdb / "fr.parquet"
    pd.DataFrame({"siren": ["1"], "denominationUniteLegale": ["x"],
                  "nomenclatureActivitePrincipaleUniteLegale": ["NAFRev1"], "activitePrincipaleUniteLegale": [""],
                  "categorieEntreprise": ["PME"], "etatAdministratifUniteLegale": ["A"]}).to_parquet(pq)
    build.load("fr_sirene", pq)
    assert rows("SELECT source_id, sector FROM companies") == [("1", None)]


def test_load_no_brreg_filters_and_maps(tmpdb):
    csvp = tmpdb / "no.csv"
    pd.DataFrame({
        "organisasjonsnummer": ["1", "2", "3", "4", "5", "6"],
        "navn": ["ok", "bankrupt", "winding", "forced", "missingflags", "site"],
        "forretningsadresse.poststed": ["OSLO"] * 6,
        "hjemmeside": ["", "", "", "", "", "www.x.no"],
        "naeringskode1.kode": ["62.010", "62.010", "62.010", "62.010", "68.320", "49.410"],
        "antallAnsatte": ["5", "1", "1", "1", "2", "3"],
        "organisasjonsform.kode": ["AS", "AS", "AS", "AS", "ENK", "AS"],
        "konkurs": ["false", "true", "false", "false", None, "false"],
        "underAvvikling": ["false", "false", "true", "false", None, "false"],
        "underTvangsavviklingEllerTvangsopplosning": ["false", "false", "false", "true", None, "false"]}
    ).to_csv(csvp, index=False)
    build.load("no_brreg", csvp)
    got = {r[0]: r[1:] for r in rows(
        "SELECT source_id, name, city, website, sector_code, sector, size, status, country FROM companies")}
    assert set(got) == {"1", "5", "6"}
    assert got["1"] == ("ok", "OSLO", None, "62.010", "Information & communication", "5", "AS", "NO")
    assert got["5"][4] == "Real estate" and got["5"][6] == "ENK"
    assert got["6"][2] == "www.x.no"


def test_load_pdl_maps_columns(tmpdb):
    csvp = tmpdb / "pdl.csv"
    pd.DataFrame({"id": ["p1"], "name": ["Pdl Co"], "website": ["pdl.example"], "industry": ["software"],
                  "size": ["11-50"], "country": ["germany"], "locality": ["berlin"]}).to_csv(csvp, index=False)
    build.load("pdl", csvp)
    assert rows("SELECT * FROM companies") == [
        ("pdl", "p1", "Pdl Co", "germany", "berlin", "pdl.example", "PDL industry", None, "software", "11-50", None)]


def test_load_unknown_source_loads_nothing(tmpdb, capsys):
    build.load("unknown", "nofile")
    assert capsys.readouterr().out.strip() == "unknown 0 rows"


# --------------------------------------------------------------------------- dedupe edge cases
def _ins(*rs):
    c = build.db()
    c.executemany("INSERT INTO companies VALUES (?,?,?,?,NULL,NULL,NULL,NULL,?,NULL,NULL)", rs)
    c.close()


def test_dedupe_prints_summary_and_counts_merges(tmpdb, capsys):
    _ins(("ch", "C1", "Alpha Ltd", "GB", None), ("damodaran", "L:A", "Alpha plc", "GB", None))
    build.dedupe()
    out = capsys.readouterr().out
    assert "[('ch', 1)]" in out and out.strip().endswith("1 merged")


def test_dedupe_keeps_unmatched_rows_empty_name_and_null_country(tmpdb):
    _ins(("damodaran", "L:X", "Solo Corp", "GB", None),
         ("damodaran", "L:E", "(Holdings)", "GB", None),    # normalises to '' -> never merged
         ("damodaran", "L:E2", "(Holdings)", "GB", None),
         ("damodaran", "L:N1", "Nocountry Inc", None, None),
         ("damodaran", "L:N2", "Nocountry Inc", None, None),
         ("ch", "C9", "Nocountry Ltd", None, None))
    build.dedupe()
    ids = {r[0] for r in rows("SELECT source_id FROM companies_dedup")}
    assert ids == {"L:X", "L:E", "L:E2", "L:N1", "L:N2", "C9"}


def test_dedupe_merged_sector_fills_gap_but_registry_sector_wins(tmpdb):
    _ins(("ch", "C1", "Alpha Ltd", "GB", None), ("damodaran", "L:A", "Alpha plc", "GB", "Manufacturing"),
         ("ch", "C2", "Beta Ltd", "GB", "Real estate"), ("damodaran", "L:B", "Beta plc", "GB", "Manufacturing"))
    build.dedupe()
    got = dict(rows("SELECT source_id, sector FROM companies_dedup"))
    assert got == {"C1": "Manufacturing", "C2": "Real estate"}


def test_dedupe_iem_rank2_is_registry_but_loses_listing_tiebreak(tmpdb):
    _ins(("iem_cin", "I1", "Gamma Pvt Ltd", "IN", None), ("damodaran", "N:G", "Gamma Limited", "IN", None),
         ("defence", "D1", "Gamma", "IN", None), ("fi_prh", "F1", "Delta Oy", "FI", None))
    build.dedupe()
    got = {r[0]: (r[1], r[2]) for r in rows("SELECT source_id, sources, listed_ticker FROM companies_dedup")}
    assert set(got) == {"I1", "F1"}
    assert set(got["I1"][0].split("+")) == {"iem_cin", "damodaran", "defence"}   # aggregation order is unspecified
    assert got["I1"][0].startswith("iem_cin+") and got["I1"][1] == "N:G"


def test_dedupe_is_rerunnable(tmpdb):
    _ins(("ch", "C1", "Alpha Ltd", "GB", None))
    build.dedupe(); build.dedupe()
    assert rows("SELECT count(*) FROM companies_dedup") == [(1,)]


# --------------------------------------------------------------------------- lists / export
def test_lists_writes_country_sector_csvs(tmpdb, capsys):
    _ins(("ch", "C1", "Zed Ltd", "GB", "Real estate"), ("ch", "C2", "Alpha Ltd", "GB", "Real estate"),
         ("ch", "C3", "Mid Ltd", "GB", None), ("ch", "C4", "Cc Ltd", "GB", "Admin & support services"),
         ("in_mca", "I1", "Ind Ltd", "IN", "Information & communication"), ("ch", "C5", "Nope Ltd", None, "Real estate"))
    stale = tmpdb / "lists" / "OLD"; stale.mkdir(parents=True); (stale / "x.csv").write_text("x")
    build.dedupe(); capsys.readouterr()
    build.lists()
    assert capsys.readouterr().out.strip() == "4 files, 5 rows"
    out = tmpdb / "lists"
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*.csv")) == [
        "GB/admin_support_services.csv", "GB/real_estate.csv", "GB/unclassified.csv", "IN/information_communication.csv"]
    df = pd.read_csv(out / "GB" / "real_estate.csv")
    assert list(df.columns) == ["name", "sources", "source_id", "city", "website", "sector_code", "size", "status", "listed_ticker"]
    assert list(df["name"]) == ["Alpha Ltd", "Zed Ltd"]     # ORDER BY name
    assert list(pd.read_csv(out / "GB" / "unclassified.csv")["name"]) == ["Mid Ltd"]


def test_export_partitions_by_country_and_sector(tmpdb, capsys):
    _ins(("ch", "C1", "A", "GB", "Real estate"), ("ch", "C2", "B", "GB", "Real estate"),
         ("ch", "C3", "C", "GB", None), ("ch", "C4", "D", None, "Real estate"),
         ("in_mca", "I1", "E", "IN", "Manufacturing"))
    build.export()
    out = tmpdb / "by_sector_country"
    parts = sorted(urllib.parse.unquote(p.relative_to(out).parent.as_posix()) for p in out.rglob("*.parquet"))
    assert parts == ["country=GB/sector=Real estate", "country=IN/sector=Manufacturing"]
    printed = capsys.readouterr().out
    assert "('GB', 'Real estate', 2)" in printed and "('IN', 'Manufacturing', 1)" in printed
    # printed summary counts rows with a sector, even without country
    assert "('NULL'" not in printed


# --------------------------------------------------------------------------- CLI dispatch (run source as __main__)
def run_main(tmp_path, *argv):
    src = pathlib.Path(build.__file__)
    # HERE is derived from __file__; point it at tmp_path but keep the real filename for coverage
    text = src.read_text().replace("pathlib.Path(__file__).parent", "pathlib.Path(%r)" % str(tmp_path))
    assert str(tmp_path) in text
    g = {"__name__": "__main__"}
    old = sys.argv
    sys.argv = ["build.py", *argv]
    try:
        exec(compile(text, str(src), "exec"), g)
    finally:
        sys.argv = old
    return g


def test_main_dispatch_load_dedupe_lists_export(tmp_path, monkeypatch, capsys):
    pq = tmp_path / "d.parquet"
    pd.DataFrame({"Exchange:Ticker": ["A:1", "A:2"], "Company Name": ["One Ltd", "Two Ltd"],
                  "Country": ["India", "India"], "Industry Group": ["Steel", "Steel"]}).to_parquet(pq)
    run_main(tmp_path, "load", "damodaran", str(pq))
    run_main(tmp_path, "dedupe")
    run_main(tmp_path, "lists")
    run_main(tmp_path, "export")
    out = capsys.readouterr().out
    assert "damodaran 2 rows" in out and "1 files, 2 rows" in out
    assert (tmp_path / "company_lists.duckdb").exists()
    assert (tmp_path / "lists" / "IN" / "manufacturing.csv").exists()
    assert list((tmp_path / "by_sector_country").rglob("*.parquet"))


def test_main_dispatch_fetch(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda u: io.BytesIO(
        json.dumps({"data": {"full_file": {"csv": {"url": "http://g/f.zip"}}}}).encode()))
    monkeypatch.setattr(urllib.request, "urlretrieve", lambda u, d: seen.append((u, str(d))))
    run_main(tmp_path, "fetch", "gleif")
    assert seen == [("http://g/f.zip", str(tmp_path / "raw" / "gleif_lei2.csv.zip"))]


def test_main_unknown_command_is_noop(tmp_path):
    run_main(tmp_path, "bogus")
    assert not (tmp_path / "company_lists.duckdb").exists()
