from openpyxl import load_workbook

from leadgen.db import connect, set_step
from leadgen.export import CONTACT_COLUMNS, export_state


def test_export_columns_and_rows(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO institutions (unitid, state, name, website, sector, system_name, is_system) VALUES ('1','FL','Test U','https://t.edu','Public','',0)")
    conn.execute(
        "INSERT INTO persons (id, unitid, person_key, first_name, last_name, email, email_status, phone_status, "
        "responsibilities, manual_check, check_reasons) VALUES (1,'1','jane|doe','Jane','Doe','jane.doe@t.edu',"
        "'deduced','not found','x',1,'email deduced')"
    )
    conn.execute("INSERT INTO person_roles VALUES (1,'adv_vp','VP Advancement','exact','https://t.edu/a',NULL)")
    conn.execute("INSERT INTO person_roles VALUES (1,'fdn_ed','VP Advancement','exact','https://t.edu/a',NULL)")
    set_step(conn, "1", "extract", "done")
    conn.commit()
    path = export_state(conn, "FL", tmp_path)
    wb = load_workbook(path)
    assert wb.sheetnames == ["Contacts", "Coverage", "Stats", "Read me"]
    ws = wb["Contacts"]
    header = [c.value for c in ws[1]]
    assert header == CONTACT_COLUMNS
    row = dict(zip(header, [c.value for c in ws[2]]))
    assert row["email_status"] == "deduced" and row["manual_check_needed"] == "yes"
    assert row["target_profiles"] == "VP Advancement; Executive Director, University Foundation"
    assert ws.max_row == 2  # one row per person, not per profile
