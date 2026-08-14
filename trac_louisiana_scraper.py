# Imports our libraries
import csv
import json

from playwright.sync_api import sync_playwright

# Sets our constants for the scraper
URL = "https://tracreports.org/phptools/immigration/addressrep/"
LOUISIANA_FIPS_ID = "_22"  # Louisiana = FIPS state code 22
DATA_THROUGH_SELECTOR = ".font-bold.text-sm.text-grey-600.text-center"

COLUMNS = [
    "County Name",
    "All Pending Cases - Number",
    "All Pending Cases - Represented",
    "All Pending Cases - Odds of Representation",
    "Cases Filed Last 90 Days - Number",
    "Cases Filed Last 90 Days - Represented",
    "Cases Filed Last 90 Days - Odds of Representation",
]

# Scrapes the Louisiana county-level table from TRAC Immigration
def scrape_louisiana_county_table(headless: bool = True):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle")

        # Scrapes the "through <month> <year>" text from the page header
        data_through = page.locator(DATA_THROUGH_SELECTOR).inner_text().strip()

        # Clicks the Louisiana shape on the map to drill into the state-level table
        louisiana = page.locator(f"svg#mapcanvas path#{LOUISIANA_FIPS_ID}")
        louisiana.wait_for(state="visible", timeout=15000)
        louisiana.dispatch_event("click")

        # Waits for the "Back to U.S. Map" link to appear, which indicates the local data has loaded
        page.get_by_text("Back to U.S. Map", exact=True).wait_for(
            state="visible", timeout=15000
        )

        # Switches to the county-level table by clicking the "County" radio input
        page.locator("#county-btn").check()

        # Waits for the table header to say "County Name" before scraping, which indicates the county-level table has rendered
        page.locator("#tableHead").get_by_text("County Name", exact=True).wait_for(
            state="visible", timeout=15000
        )

        # Scrapes the table rows and builds a list of dictionaries, one per parish
        rows = page.locator("#tableData tr")
        row_count = rows.count()

        data = []
        for i in range(row_count):
            cells = rows.nth(i).locator("th, td")
            values = [cells.nth(j).inner_text().strip() for j in range(cells.count())]
            if len(values) == len(COLUMNS):
                data.append(dict(zip(COLUMNS, values)))

        browser.close()
        return data, data_through

# Writes the scraped data to CSV and text files
if __name__ == "__main__":
    rows, data_through = scrape_louisiana_county_table(headless=True)

    print(f"Scraped {len(rows)} Louisiana parishes")
    print(json.dumps(rows[:3], indent=2))
    print(f"Data currency text: {data_through!r}")

    with open("louisiana_county_cases.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print("Wrote louisiana_county_cases.csv")

    with open("data_through.txt", "w") as f:
        f.write(data_through)
    print("Wrote data_through.txt")