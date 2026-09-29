"""
Chandler PD open-data arrest bookings  (data.chandlerpd.com/catalog/arrest-bookings/download/csv)

Verified 2026-09-28: ~25 MB CSV, ~67,000 rows, columns:
  id,event_type,agency,arrest_id,arrest_number,arrest_year,arrest_date,arrest_time,arrest_date_time,arrest_day,
  arrest_place_name,arrest_address,arrest_city,arrest_district,arrest_beat,arrest_latitude,arrest_longitude,
  arrestee_unique_number,arrestee_age,arrestee_race,arrestee_gender,arrestee_ethnicity,arrestee_ethnicity_category,
  arrest_type,arrest_charge_count,arrest_charge_severity,arrest_class,arrest_charge,officer_race,officer_gender,
  officer_unique_number,officer_team,officer_age,officer_years_of_service,related_offense_report_number
NOTE: the dataset carries NO arrestee names (only arrestee_unique_number), so it cannot be matched to a person by
name. It is retained and searchable by date / charge / address / report number only.
"""
import csv, io
from .base import Http

SITE = "chandler"
URL = "https://data.chandlerpd.com/catalog/arrest-bookings/download/csv"

def fetch(http: Http):
    body, snap = http.get(SITE, "bookings", URL, ext="csv")
    text = body.decode("utf-8", "replace")
    rdr = csv.DictReader(io.StringIO(text))
    rows = [r for r in rdr]
    return {"columns": rdr.fieldnames or [], "rows": rows, "snapshot": snap}
