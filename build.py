#!/usr/bin/env python3
"""Free company universe -> DuckDB (company_lists.duckdb).
Usage: build.py fetch gleif|ch | load gleif|ch|pdl <file> | export
Table: companies(source, source_id, name, country, city, website, sector_scheme, sector_code, sector, size, status)
"""
import sys, zipfile, subprocess, json, urllib.request, pathlib, duckdb
try: from duckdb.sqltypes import VARCHAR
except ImportError: from duckdb.typing import VARCHAR
HERE = pathlib.Path(__file__).parent; RAW = HERE / "raw"; DB = HERE / "company_lists.duckdb"

# UK SIC 2007 division -> section name (first 2 digits)
SIC = [(1,3,"Agriculture, forestry & fishing"),(5,9,"Mining & quarrying"),(10,33,"Manufacturing"),
 (35,35,"Energy & utilities"),(36,39,"Water, waste & remediation"),(41,43,"Construction"),
 (45,47,"Wholesale & retail"),(49,53,"Transport & storage"),(55,56,"Hospitality"),
 (58,63,"Information & communication"),(64,66,"Finance & insurance"),(68,68,"Real estate"),
 (69,75,"Professional & scientific"),(77,82,"Admin & support services"),(84,84,"Public administration"),
 (85,85,"Education"),(86,88,"Health & social work"),(90,93,"Arts & recreation"),(94,96,"Other services"),
 (97,98,"Households"),(99,99,"Extraterritorial")]
def sic_sector(code):
    try: d = int(str(code).strip()[:2])
    except Exception: return None
    return next((n for a, b, n in SIC if a <= d <= b), None)


# Damodaran industry group -> unified sector (UK SIC 2007 section names)
M, T, I, F, P, W, R = ("Manufacturing", "Transport & storage", "Information & communication", "Finance & insurance",
    "Professional & scientific", "Wholesale & retail", "Real estate")
DAMO = {"Advertising":P,"Aerospace/Defense":M,"Air Transport":T,"Apparel":M,"Auto & Truck":M,"Auto Parts":M,
 "Bank (Money Center)":F,"Banks (Regional)":F,"Beverage (Alcoholic)":M,"Beverage (Soft)":M,"Broadcasting":I,
 "Brokerage & Investment Banking":F,"Building Materials":M,"Business & Consumer Services":"Admin & support services",
 "Cable TV":I,"Chemical (Basic)":M,"Chemical (Diversified)":M,"Chemical (Specialty)":M,"Coal & Related Energy":"Mining & quarrying",
 "Computer Services":I,"Computers/Peripherals":M,"Construction Supplies":M,"Diversified":None,"Drugs (Biotechnology)":M,
 "Drugs (Pharmaceutical)":M,"Education":"Education","Electrical Equipment":M,"Electronics (Consumer & Office)":M,
 "Electronics (General)":M,"Engineering/Construction":"Construction","Entertainment":"Arts & recreation",
 "Environmental & Waste Services":"Water, waste & remediation","Farming/Agriculture":"Agriculture, forestry & fishing",
 "Financial Svcs. (Non-bank & Insurance)":F,"Food Processing":M,"Food Wholesalers":W,"Furn/Home Furnishings":M,
 "Green & Renewable Energy":"Energy & utilities","Healthcare Products":M,"Healthcare Support Services":"Health & social work",
 "Heathcare Information and Technology":I,"Homebuilding":"Construction","Hospitals/Healthcare Facilities":"Health & social work",
 "Hotel/Gaming":"Hospitality","Household Products":M,"Information Services":I,"Insurance (General)":F,"Insurance (Life)":F,
 "Insurance (Prop/Cas.)":F,"Investments & Asset Management":F,"Machinery":M,"Metals & Mining":"Mining & quarrying",
 "Office Equipment & Services":M,"Oil/Gas (Integrated)":"Mining & quarrying","Oil/Gas (Production and Exploration)":"Mining & quarrying",
 "Oil/Gas Distribution":"Energy & utilities","Oilfield Svcs/Equip.":"Mining & quarrying","Packaging & Container":M,
 "Paper/Forest Products":M,"Power":"Energy & utilities","Precious Metals":"Mining & quarrying","Publishing & Newspapers":I,
 "R.E.I.T.":R,"Real Estate (Development)":R,"Real Estate (General/Diversified)":R,"Real Estate (Operations & Services)":R,
 "Recreation":"Arts & recreation","Reinsurance":F,"Restaurant/Dining":"Hospitality","Retail (Automotive)":W,
 "Retail (Building Supply)":W,"Retail (Distributors)":W,"Retail (General)":W,"Retail (Grocery and Food)":W,"Retail (REITs)":R,
 "Retail (Special Lines)":W,"Rubber& Tires":M,"Semiconductor":M,"Semiconductor Equip":M,"Shipbuilding & Marine":M,"Shoe":M,
 "Software (Entertainment)":I,"Software (Internet)":I,"Software (System & Application)":I,"Steel":M,"Telecom (Wireless)":I,
 "Telecom. Equipment":M,"Telecom. Services":I,"Tobacco":M,"Transportation":T,"Transportation (Railroads)":T,"Trucking":T,
 "Utility (General)":"Energy & utilities","Utility (Water)":"Water, waste & remediation"}
# India CIN carries (NIC-2004 for companies incorporated before 2023; NIC-2008 == UK SIC 2007 divisions from 2023 -- break found empirically)
# India CIN carries a 5-digit NIC (2004-era) code at chars 2-6; division -> unified sector (approximate)
NIC = [(1,5,"Agriculture, forestry & fishing"),(10,14,"Mining & quarrying"),(15,37,"Manufacturing"),(40,40,"Energy & utilities"),
 (41,41,"Water, waste & remediation"),(45,45,"Construction"),(50,52,"Wholesale & retail"),(55,55,"Hospitality"),
 (60,63,"Transport & storage"),(64,64,"Information & communication"),(65,67,"Finance & insurance"),(70,70,"Real estate"),
 (71,71,"Admin & support services"),(72,72,"Information & communication"),(73,74,"Professional & scientific"),
 (75,75,"Public administration"),(80,80,"Education"),(85,85,"Health & social work"),(90,90,"Water, waste & remediation"),
 (91,91,"Other services"),(92,93,"Arts & recreation"),(95,99,"Households")]
def nic_sector(div):
    return next((n for a, b, n in NIC if a <= div <= b), None)

ISO = {"Argentina":"AR","Australia":"AU","Austria":"AT","Azerbaijan":"AZ","Bahamas":"BS","Bahrain":"BH","Bangladesh":"BD","Barbados":"BB","Belgium":"BE","Benin":"BJ","Bermuda":"BM","Botswana":"BW","Brazil":"BR","British Virgin Islands":"VG","Bulgaria":"BG","Burkina Faso":"BF","Cambodia":"KH","Canada":"CA","Cayman Islands":"KY","Chile":"CL","China":"CN","Colombia":"CO","Costa Rica":"CR","Croatia":"HR","Curaçao":"CW","Cyprus":"CY","Czech Republic":"CZ","Denmark":"DK","Dominican Republic":"DO","Ecuador":"EC","Egypt":"EG","El Salvador":"SV","Estonia":"EE","Finland":"FI","France":"FR","Gabon":"GA","Georgia":"GE","Germany":"DE","Ghana":"GH","Gibraltar":"GI","Greece":"GR","Greenland":"GL","Guernsey":"GG","Hong Kong":"HK","Hungary":"HU","Iceland":"IS","India":"IN","Indonesia":"ID","Ireland":"IE","Isle of Man":"IM","Israel":"IL","Italy":"IT","Ivory Coast":"CI","Jamaica":"JM","Japan":"JP","Jersey":"JE","Jordan":"JO","Kazakhstan":"KZ","Kenya":"KE","Kuwait":"KW","Latvia":"LV","Liberia":"LR","Liechtenstein":"LI","Lithuania":"LT","Luxembourg":"LU","Macau":"MO","Macedonia":"MK","Malawi":"MW","Malaysia":"MY","Mali":"ML","Malta":"MT","Marshall Islands":"MH","Martinique":"MQ","Mauritius":"MU","Mexico":"MX","Moldova":"MD","Monaco":"MC","Mongolia":"MN","Montenegro":"ME","Morocco":"MA","Myanmar":"MM","Namibia":"NA","Netherlands":"NL","New Zealand":"NZ","Niger":"NE","Nigeria":"NG","Norway":"NO","Oman":"OM","Pakistan":"PK","Palestinian Authority":"PS","Panama":"PA","Papua New Guinea":"PG","Peru":"PE","Philippines":"PH","Poland":"PL","Portugal":"PT","Qatar":"QA","Romania":"RO","Russia":"RU","Rwanda":"RW","Saint Lucia":"LC","Saudi Arabia":"SA","Senegal":"SN","Serbia":"RS","Singapore":"SG","Slovakia":"SK","Slovenia":"SI","South Africa":"ZA","South Korea":"KR","Spain":"ES","Sri Lanka":"LK","Sudan":"SD","Sweden":"SE","Switzerland":"CH","Taiwan":"TW","Tanzania":"TZ","Thailand":"TH","Togo":"TG","Trinidad & Tobago":"TT","Tunisia":"TN","Turkey":"TR","Turks & Caicos Islands":"TC","Uganda":"UG","Ukraine":"UA","United Arab Emirates":"AE","United Kingdom":"GB","United States":"US","Uruguay":"UY","Venezuela":"VE","Vietnam":"VN","Zambia":"ZM","Zimbabwe":"ZW"}

def db():
    c = duckdb.connect(str(DB)); c.create_function("nic_sector", nic_sector, ['INTEGER'], VARCHAR, null_handling='special'); c.create_function("sic_sector", sic_sector, [VARCHAR], VARCHAR, null_handling='special')
    c.execute("""CREATE TABLE IF NOT EXISTS companies(source VARCHAR, source_id VARCHAR, name VARCHAR, country VARCHAR,
      city VARCHAR, website VARCHAR, sector_scheme VARCHAR, sector_code VARCHAR, sector VARCHAR, size VARCHAR, status VARCHAR)""")
    return c

def dl(url, dest):
    print("downloading", url); urllib.request.urlretrieve(url, dest); return dest

def fetch(what):
    RAW.mkdir(exist_ok=True)
    if what == "gleif":
        meta = json.load(urllib.request.urlopen("https://goldencopy.gleif.org/api/v2/golden-copies/publishes/lei2/latest"))
        return dl(meta["data"]["full_file"]["csv"]["url"], RAW / "gleif_lei2.csv.zip")
    if what == "ch":
        html = urllib.request.urlopen("https://download.companieshouse.gov.uk/en_output.html").read().decode()
        import re; f = re.search(r"BasicCompanyDataAsOneFile-[\d-]+\.zip", html).group(0)
        return dl("https://download.companieshouse.gov.uk/" + f, RAW / f)

def unzip(path):
    out = RAW / (pathlib.Path(path).stem + "_x"); out.mkdir(exist_ok=True)
    zipfile.ZipFile(path).extractall(out); return next(out.glob("*.csv"))

def load(what, path):
    c = db(); c.execute("DELETE FROM companies WHERE source=?", [what])
    if what == "gleif":
        f = unzip(path)
        c.execute(f"""INSERT INTO companies SELECT 'gleif', LEI, "Entity.LegalName", "Entity.LegalAddress.Country",
          "Entity.LegalAddress.City", NULL, NULL, NULL, NULL, NULL, "Entity.EntityStatus"
          FROM read_csv('{f}', all_varchar=true, header=true)""")
    elif what == "ch":
        f = unzip(path)
        c.execute(f"""INSERT INTO companies SELECT 'ch', companynumber, companyname, 'GB', regaddressposttown, uri,
          'UK SIC 2007', split_part(siccodesictext_1,' - ',1), sic_sector(split_part(siccodesictext_1,' - ',1)), NULL, companystatus
          FROM read_csv('{f}', all_varchar=true, header=true, normalize_names=true, ignore_errors=true)
          WHERE companystatus='Active'""")
    elif what == "damodaran":
        import pandas as pd
        df = pd.read_parquet(path)
        out = pd.DataFrame({"source":"damodaran","source_id":df["Exchange:Ticker"],"name":df["Company Name"],
          "country":df["Country"].map(ISO),"city":None,"website":None,"sector_scheme":"Damodaran industry group",
          "sector_code":df["Industry Group"],"sector":df["Industry Group"].map(DAMO),"size":None,"status":"Listed"})
        c.register("o", out); c.execute("INSERT INTO companies SELECT * FROM o")
    elif what == "in_mca":
        f = unzip(path)
        c.execute(f"""INSERT INTO companies
          SELECT 'in_mca', cin, "Entity.LegalName", 'IN', "Entity.LegalAddress.City", NULL, 'NIC (from CIN)', substr(cin,2,5),
                 CASE WHEN try_cast(substr(cin,9,4) AS INTEGER) >= 2023 THEN sic_sector(substr(cin,2,5)) ELSE nic_sector(try_cast(substr(cin,2,2) AS INTEGER)) END, NULL, "Entity.EntityStatus"
          FROM (SELECT *, "Entity.RegistrationAuthority.RegistrationAuthorityEntityID" AS cin
                FROM read_csv('{f}', all_varchar=true, header=true))
          WHERE "Entity.RegistrationAuthority.RegistrationAuthorityID"='RA000394'
            AND regexp_matches(cin, '^[LU][0-9]{{5}}[A-Z]{{2}}[0-9]{{4}}[A-Z]{{3}}[0-9]{{6}}$')""")
    elif what == "iem_cin":
        # IEM-linked Indian companies with a CIN (from ~/Downloads/DPIIT_IEM_lists); skip CINs already in in_mca
        c.execute(f"""INSERT INTO companies
          SELECT 'iem_cin', cin, name, 'IN', NULL, NULL, 'NIC (from CIN)', substr(cin,2,5),
                 CASE WHEN try_cast(substr(cin,9,4) AS INTEGER) >= 2023 THEN sic_sector(substr(cin,2,5)) ELSE nic_sector(try_cast(substr(cin,2,2) AS INTEGER)) END,
                 NULL, mca_status
          FROM (SELECT cin, arg_max(company_name, month) AS name, arg_max(mca_status, month) AS mca_status
                FROM '{path}' WHERE cin IS NOT NULL AND regexp_matches(cin, '^[LU][0-9]{{5}}[A-Z]{{2}}[0-9]{{4}}[A-Z]{{3}}[0-9]{{6}}$') GROUP BY cin)
          WHERE cin NOT IN (SELECT source_id FROM companies WHERE source='in_mca')""")
    elif what == "defence":
        import pandas as pd, re
        t = pd.read_csv(path, sep="\t")
        iso = dict(ISO, **{"UK": "GB", "US": "US"})
        def sec(v):
            m = re.match(r"^(NAF|SIC|KSIC)\s+(\d{2})", str(v)); return sic_sector(m.group(2)) if m else None
        out = pd.DataFrame({"source":"defence","source_id":t["registry_id"].astype(str),"name":t["company"],
          "country":t["country"].map(iso),"city":None,"website":t["source_url"],"sector_scheme":"defence registry",
          "sector_code":t["industry_code"],"sector":t["industry_code"].map(sec),"size":t["size_signal"],"status":"defence-sector"})
        c.register("o", out); c.execute("INSERT INTO companies SELECT * FROM o")
    elif what == "fr_sirene":
        # NAF Rev2 (= NACE Rev2 = UK SIC 2007 divisions); NAF1993/Rev1 (= NACE 1.1 ~ NIC-2004) via nic_sector; NAP skipped
        c.execute(f"""INSERT INTO companies
          SELECT 'fr_sirene', siren, denominationUniteLegale, 'FR', NULL, NULL,
                 'NAF ' || coalesce(nomenclatureActivitePrincipaleUniteLegale,'?'), activitePrincipaleUniteLegale,
                 CASE nomenclatureActivitePrincipaleUniteLegale
                   WHEN 'NAFRev2' THEN sic_sector(substr(activitePrincipaleUniteLegale,1,2))
                   WHEN 'NAFRev1' THEN nic_sector(try_cast(substr(activitePrincipaleUniteLegale,1,2) AS INTEGER))
                   WHEN 'NAF1993' THEN nic_sector(try_cast(substr(activitePrincipaleUniteLegale,1,2) AS INTEGER)) END,
                 categorieEntreprise, 'Active'
          FROM '{path}' WHERE etatAdministratifUniteLegale='A' AND denominationUniteLegale IS NOT NULL""")
    elif what == "ee_ariregister":
        # EMTAK 2008 == NACE Rev2 == UK SIC 2007 divisions. Main activity from notified activities, else latest annual-report activity.
        c.execute(f"""INSERT INTO companies
          SELECT 'ee_ariregister', CAST(ariregistri_kood AS VARCHAR), nimi, 'EE', NULL, NULL, 'EMTAK 2008', code, sic_sector(substr(code,1,2)), NULL, 'Registered'
          FROM (SELECT ariregistri_kood, nimi, yldandmed.staatus AS st,
                  coalesce(list_filter(yldandmed.teatatud_tegevusalad, x -> x.on_pohitegevusala AND x.lopp_kpv IS NULL)[1].emtak_kood,
                           list_filter(yldandmed.teatatud_tegevusalad, x -> x.lopp_kpv IS NULL)[1].emtak_kood,
                           yldandmed.info_majandusaasta_aruannetest[-1].tegevusala_emtak_kood) AS code
                FROM '{path}') WHERE st='R'""")
    elif what == "no_brreg":
        # Norway: naeringskode1 is NACE Rev2 (SN2007) == UK SIC 2007 divisions; skip bankrupt / winding-up / deleted
        c.execute(f"""INSERT INTO companies
          SELECT 'no_brreg', organisasjonsnummer, navn, 'NO', "forretningsadresse.poststed", NULLIF(hjemmeside,''),
                 'NACE (SN2007)', "naeringskode1.kode", sic_sector(substr("naeringskode1.kode",1,2)), antallAnsatte, "organisasjonsform.kode"
          FROM read_csv('{path}', all_varchar=true, header=true, ignore_errors=true)
          WHERE coalesce(konkurs,'false')<>'true' AND coalesce(underAvvikling,'false')<>'true'
            AND coalesce(underTvangsavviklingEllerTvangsopplosning,'false')<>'true'""")
    elif what == "pdl":
        # PDL free company dataset: pipe/CSV/JSON; id,name,website,industry,size,country,locality,...
        c.execute(f"""INSERT INTO companies SELECT 'pdl', id, name, country, locality, website, 'PDL industry', NULL,
          industry, size, NULL FROM read_csv_auto('{path}', all_varchar=true, ignore_errors=true)""")
    print(what, c.execute("SELECT count(*) FROM companies WHERE source=?", [what]).fetchone()[0], "rows")


NORM = r"""regexp_replace(trim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
  regexp_replace(replace(replace(lower(strip_accents(name)), '.', ''), '''', ''), '[(].*?[)]', ' ', 'g'), '&', ' and ', 'g'), '[^a-z0-9 ]', ' ', 'g'), ' +', ' ', 'g'),
  '( (the|ltd|limited|pvt|private|plc|public|inc|incorporated|corp|corporation|co|company|llc|llp|sa|ag|gmbh|nv|bv|ab|as|oy|spa|sdn|bhd|tbk|kk|jsc|pjsc|lp|cv))+ *$', '', 'g')), '^the ', '')"""
REGISTRY = ("ch", "in_mca")

RANK = {"ch": 1, "in_mca": 1, "fr_sirene": 1, "no_brreg": 1, "ee_ariregister": 1, "fi_prh": 1, "iem_cin": 2, "damodaran": 3, "defence": 4}
def dedupe():
    """One row per (country, normalised name). Registry rows (rank<=2) win; a non-registry row merges into a registry
    row only if exactly one registry row has that name (else it stays, avoiding wrong merges). Among non-registry
    rows the lowest rank wins. GLEIF is excluded (no sector). Kept rows gain listed_ticker, also_in, and a filled sector."""
    c = db()
    c.execute(f"""CREATE OR REPLACE TABLE norm AS SELECT rowid AS rid, source, country, {NORM} AS nn,
        CASE source {' '.join(f"WHEN '{k}' THEN {v}" for k, v in RANK.items())} END AS rnk
        FROM companies WHERE source IN ({','.join(repr(k) for k in RANK)}) AND name IS NOT NULL""")
    c.execute("""CREATE OR REPLACE TABLE tgt AS
      SELECT n.rid, n.source, CASE WHEN n.nn='' OR n.country IS NULL THEN n.rid WHEN n.rnk<=2 THEN n.rid
             WHEN rs.rid IS NOT NULL THEN rs.rid WHEN rc.cnt>0 THEN n.rid ELSE fn.rid END AS target
      FROM norm n
      LEFT JOIN (SELECT country,nn,min(rid) rid FROM norm WHERE rnk<=2 GROUP BY 1,2 HAVING count(*)=1) rs USING(country,nn)
      LEFT JOIN (SELECT country,nn,count(*) cnt FROM norm WHERE rnk<=2 GROUP BY 1,2) rc USING(country,nn)
      LEFT JOIN (SELECT country,nn,arg_min(rid, CAST(rnk AS BIGINT)*1000000000+rid) rid FROM norm WHERE rnk>2 GROUP BY 1,2) fn USING(country,nn)""")
    c.execute("""CREATE OR REPLACE TABLE companies_dedup AS
      SELECT k.source, k.source_id, k.name, k.country, k.city, k.website, k.sector_scheme, k.sector_code,
             coalesce(k.sector, m.sec) AS sector, k.size, k.status, m.tick AS listed_ticker,
             concat_ws('+', k.source, m.alsoin) AS sources
      FROM (SELECT rowid AS rid, * FROM companies) k
      JOIN tgt t ON t.rid=k.rid AND t.target=t.rid
      LEFT JOIN (SELECT t2.target, string_agg(CASE WHEN c2.source='damodaran' THEN c2.source_id END, ', ') tick,
                        array_to_string(list(DISTINCT c2.source), '+') alsoin, min(c2.sector) sec
                 FROM tgt t2 JOIN (SELECT rowid AS rid, * FROM companies) c2 ON c2.rid=t2.rid WHERE t2.target<>t2.rid GROUP BY 1) m
        ON m.target=k.rid""")
    print(c.execute("""SELECT source, count(*) FROM companies_dedup GROUP BY 1 ORDER BY 1""").fetchall(),
          c.execute("SELECT count(*) FROM tgt WHERE target<>rid").fetchone()[0], "merged")

def lists():
    import re, shutil
    c = db(); out = HERE / "lists"; shutil.rmtree(out, ignore_errors=True)
    slug = lambda x: re.sub(r"[^a-z0-9]+", "_", x.lower()).strip("_")
    cols = "name, sources, source_id, city, website, sector_code, size, status, listed_ticker"
    groups = c.execute("SELECT country, sector, count(*) FROM (SELECT country, coalesce(sector,'Unclassified') sector FROM companies_dedup WHERE country IS NOT NULL) GROUP BY 1,2 ORDER BY 1,2").fetchall()
    for country, sector, n in groups:
        d = out / country; d.mkdir(parents=True, exist_ok=True)
        c.execute(f"COPY (SELECT {cols} FROM companies_dedup WHERE country=? AND coalesce(sector,'Unclassified')=? ORDER BY name) TO '{d / (slug(sector) + '.csv')}' (HEADER)", [country, sector])
    print(len(groups), "files,", sum(g[2] for g in groups), "rows")

def export():
    c = db(); out = HERE / "by_sector_country"; out.mkdir(exist_ok=True)
    c.execute(f"""COPY (SELECT * FROM companies WHERE sector IS NOT NULL AND country IS NOT NULL)
      TO '{out}' (FORMAT parquet, PARTITION_BY (country, sector), OVERWRITE_OR_IGNORE)""")
    print(c.execute("SELECT country, sector, count(*) n FROM companies WHERE sector IS NOT NULL GROUP BY 1,2 ORDER BY n DESC LIMIT 10").fetchall())

if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "fetch": fetch(a[1])
    elif a[0] == "load": load(a[1], a[2])
    elif a[0] == "export": export()
    elif a[0] == "dedupe": dedupe()
    elif a[0] == "lists": lists()
