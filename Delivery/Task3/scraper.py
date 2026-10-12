# trading holidays scraper - NASDAQ, NYSE, CME, OPRA
# steps:
#   1. scrape each exchange's website
#   2. clean the data (same holiday names, YYYY-MM-DD dates, 24h times)
#   3. save everything on trading_holidays.csv
#   4. if "--upload" is a flag, send the csv to google drive as a csv and as a google sheet

import csv
import io
import re
import sys
from datetime import datetime
from bs4 import BeautifulSoup
from curl_cffi import requests
from pypdf import PdfReader
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CSV_FILE = "trading_holidays.csv"
COLUMNS = ["exchange", "date", "weekday", "holiday", "status", "close_time", "source"]
FOLDER_ID = ""  # google drive folder id, empty = my drive

NASDAQ_URL = "https://www.nasdaqtrader.com/Trader.aspx?id=Calendar"
NYSE_URL   = "https://www.nyse.com/markets/hours-calendars"
CME_URL    = "https://www.cmegroup.com/tools-information/holiday-calendar.html"
OPRA_URL   = "https://www.opraplan.com/"


# UTILS

def get(url):
    r = requests.get(url, impersonate="chrome", timeout=30)
    r.raise_for_status()
    return r


def get_table(html, text):
    """Returns the first table that contains `text`, as a list of rows (each row = list of cell texts)."""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        if text in table.get_text():
            rows = []
            for tr in table.find_all("tr"):
                rows.append([td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])])
            return rows
    raise Exception("table not found: " + text)


def closed(exchange, day, name, source):
    return {"exchange": exchange, "day": day, "holiday": name, "status": "Closed", "close_time": "", "source": source}


def early_close(exchange, day, name, close_time, source):
    return {"exchange": exchange, "day": day, "holiday": name, "status": "Early Close", "close_time": close_time, "source": source}


# CLEANING

# every site writes the names differently ("Washington's Birthday", "President's Day - U.S.", ...)
HOLIDAYS = {
    "new year": "New Year's Day",
    "luther king": "Martin Luther King Jr. Day",
    "president": "Presidents' Day",
    "washington": "Presidents' Day",
    "good friday": "Good Friday",
    "memorial": "Memorial Day",
    "juneteenth": "Juneteenth",
    "junteenth": "Juneteenth",  # some sites spell it differently
    "independence": "Independence Day",
    "labor": "Labor Day",
    "thanksgiving": "Thanksgiving Day",
    "christmas": "Christmas Day",
}


def clean_name(name):
    for key in HOLIDAYS:
        if key in name.lower():
            return HOLIDAYS[key]
    return name


def early_close_name(day):
    # the sites only say "early close", so name it after the holiday it's next to
    if day.month == 11:
        return "Thanksgiving Day"
    if day.month == 7:
        return "Independence Day"
    if day.month == 12:
        return "Christmas Day"
    return "Early Close"


# Helps to find diferent date formats 
# just like  "January 1, 2026", "Jan 1, 2026", "July 3rd, 2025" or "MAY 26"
DATE_REGEX = r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?"


def find_dates(text, year=None):
    #All dates in the text. `year` is used when the text has no year ("MAY 26")
    dates = []
    for month, day, y in re.findall(DATE_REGEX, text, re.IGNORECASE):
        y = y or year
        if y:
            dates.append(datetime.strptime(f"{month[:3]} {day} {y}", "%b %d %Y").date())
    return dates


def clean_time(text):
    """"1:00 p.m." -> "13:00 ET",  "Close at 12:00 CT" -> "12:00 CT" """
    m = re.search(r"(\d{1,2}):(\d\d) ?(p\.m\.|a\.m\.|CT|ET)", text)
    if not m:
        return text
    hour, minute, suffix = int(m.group(1)), m.group(2), m.group(3)
    if suffix == "p.m." and hour != 12:
        hour += 12
    timezone = suffix if suffix in ("CT", "ET") else "ET"
    return f"{hour:02d}:{minute} {timezone}"


# SCRAPERS 

#nasdaq:
# table rows:  January 1, 2026 | New Years Day (Observed) | Closed
#            November 27, 2026 | Early Close* - U.S.      | 1:00 p.m.
def nasdaq():
    result = []
    for row in get_table(get(NASDAQ_URL).text, "Status"):
        if len(row) != 3:
            continue
        date_text, name, status = row
        for day in find_dates(date_text):
            if status == "Closed":
                result.append(closed("NASDAQ", day, clean_name(name), NASDAQ_URL))
            else:
                result.append(early_close("NASDAQ", day, early_close_name(day), clean_time(status), NASDAQ_URL))
    return result

#nyse:
# table:  Holiday        | 2026                | 2027               | 2028
#         Good Friday    | Friday, April 3     | Friday, March 26   | Friday, April 14

def nyse():
    html = get(NYSE_URL).text
    table = get_table(html, "Juneteenth")
    years = table[0][1:]

    result = []
    for row in table[1:]:
        name, cells = row[0], row[1:]
        for year, cell in zip(years, cells):
            for day in find_dates(cell, year):
                result.append(closed("NYSE", day, clean_name(name), NYSE_URL))

    # early closes are only in the footnotes under the table:
    #   "Each market will close early at 1:00 p.m. ... on Friday, November 27, 2026, ..."
    soup = BeautifulSoup(html, "html.parser")
    for text in soup.find_all(string=re.compile("close early")):
        note = text.parent.get_text(" ", strip=True)
        m = re.search(r"close early at ([\d:]+ [ap]\.m\.)(.*?)(\. [A-Z]|$)", note)
        if m:
            time_text, dates_text = m.group(1), m.group(2)
            for day in find_dates(dates_text):
                result.append(early_close("NYSE", day, early_close_name(day), clean_time(time_text), NYSE_URL))
    return result


# cme 
#   Holiday                             | Closes early                                  | Closed
#   Memorial Day Monday, May 25         | Friday, May 22, 2026 Close at 12:00 CT        | Monday, May 25, 2026
def cme():
    result = []
    for row in get_table(get(CME_URL).text, "TRADING FLOOR CLOSES EARLY")[1:]:
        if len(row) != 3 or not row[0]:
            continue
        name, early_text, closed_text = row
        for day in find_dates(closed_text):
            result.append(closed("CME", day, clean_name(name), CME_URL))
        for day in find_dates(early_text):
            result.append(early_close("CME", day, clean_name(name), clean_time(early_text), CME_URL))
    return result


def opra():
    # opra only publishes a pdf per year,
	# so i will take the newest on the site
    html = get(OPRA_URL).text
    pdfs = re.findall(r"(https://cdn\.opraplan\.com/documents/notices/[\w.-]*?(20\d\d)_holiday_schedule\.pdf)", html, re.IGNORECASE)
    pdfs.sort(key=lambda pdf: pdf[1])
    url, year = pdfs[-1]

    pdf = PdfReader(io.BytesIO(get(url).content))
    text = "\n".join(page.extract_text() for page in pdf.pages)

    result = []
    # holiday lines:  "MEMORIAL DAY     MAY 26"
    for name, date_text in re.findall(r"^\s*([A-Z][A-Z’'. ]+?)\s{2,}(\w+ \d{1,2}(?:, \d{4})?)", text, re.MULTILINE):
        for day in find_dates(date_text, year):
            result.append(closed("OPRA", day, clean_name(name), url))

    # footnotes:  "* Each market will close 3 hours early on Friday, November 28, 2025 ..."
    for how_early, dates_text in re.findall(r"close (.+?) early on (.*?)(?=\n\s*\*|\Z)", text, re.DOTALL):
        for day in find_dates(dates_text, year):
            result.append(early_close("OPRA", day, early_close_name(day), how_early + " early", url))
    return result


#  google drive

def login_to_drive():
    # first run: prints a google login link, after that token.json is reused
    scopes = ["https://www.googleapis.com/auth/drive.file"]
    creds = None
    try:
        creds = Credentials.from_authorized_user_file("token.json", scopes)
    except FileNotFoundError:
        pass

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", scopes)
        creds = flow.run_local_server(port=0, open_browser=False)

    with open("token.json", "w") as f:
        f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def upload(drive, name, mime_type):
    # if the file is already on drive, update it so the link stays the same
    query = f"name = '{name}' and mimeType = '{mime_type}' and trashed = false"
    if FOLDER_ID:
        query += f" and '{FOLDER_ID}' in parents"
    found = drive.files().list(q=query, fields="files(id)").execute()["files"]

    media = MediaFileUpload(CSV_FILE, mimetype="text/csv")
    if found:
        file = drive.files().update(fileId=found[0]["id"], media_body=media, fields="webViewLink").execute()
    else:
        info = {"name": name, "mimeType": mime_type}
        if FOLDER_ID:
            info["parents"] = [FOLDER_ID]
        file = drive.files().create(body=info, media_body=media, fields="webViewLink").execute()
    print("uploaded", name, file["webViewLink"])



def main():
    #scrape
    holidays = []
    for scraper in [nasdaq, nyse, cme, opra]:
        try:
            found = scraper()
            print(scraper.__name__, len(found))
            holidays += found
        except Exception as e:
            print(scraper.__name__, "failed:", e)  # keep going with the other exchanges

    #turn into csv rows and skip duplicates
    rows = []
    for h in holidays:
        row = [h["exchange"], h["day"].isoformat(), h["day"].strftime("%A"), h["holiday"], h["status"], h["close_time"], h["source"]]
        if row not in rows:
            rows.append(row)
    rows.sort()

    #save
    with open(CSV_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    print("saved", len(rows), "rows to", CSV_FILE)

    #upload
    if "--upload" in sys.argv:
        drive = login_to_drive()
        upload(drive, "trading_holidays.csv", "text/csv")
        upload(drive, "Trading Holidays", "application/vnd.google-apps.spreadsheet")  # google converts it to a sheet


main()
