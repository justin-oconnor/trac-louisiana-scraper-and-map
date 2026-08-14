# Imports our libraries
import json
import os
import sys
from pathlib import Path

import pandas as pd
from datawrapper import Datawrapper

# Sets our constants
CSV_COLUMN_COUNTY_NAME = "County Name"
MAP_VALUE_COLUMN = "All Pending Cases - Number"
REPRESENTED_COLUMN = "All Pending Cases - Represented"
NUMERIC_COUNT_COLUMNS = [
    "All Pending Cases - Number",
    "All Pending Cases - Represented",
    "Cases Filed Last 90 Days - Number",
    "Cases Filed Last 90 Days - Represented",
]

# All three charts share these constants
SOURCE_NAME = "Transactional Records Access Clearinghouse, Syracuse University"
SOURCE_URL = "https://tracreports.org/phptools/immigration/addressrep/"

# Pending-cases map constants
MAP_TITLE = "Where do immigrants with pending deportation proceedings live in Louisiana?"
LEGEND_TITLE = "Pending cases"
MAP_DESCRIPTION_PREFIX = (
    "The map below shows how many immigrants with addresses in each parish "
    "had pending deportation proceedings against them in the Justice "
    "Department's Immigration Courts "
)
# Constants shared by both maps but not table
MAP_NOTES = (
    "Figures are from the Transactional Records Access Clearinghouse's "
    "analysis of millions of public records obtained from the Executive "
    "Office for Immigration Review. In cases in which an immigrant is "
    "detained, the address shown may be that of the detention facility "
    "where they are being held. The labeled locations are Louisiana's "
    "nine ICE detention facilities."
)


# Custom map labels for Louisiana's immigration detention facilities.
# Using Claude, coordinates were geocoded from each address via OpenStreetMap/Nominatim, cross-checked
# against the facility name in the geocoder's result (not just the address
# string) to catch false-positive number address matches on the wrong street. Allen Parish
# PSC's coordinates come from the Global Detention Project's page
# for that facility (globaldetentionproject.org), which lists its coordinates.
LABEL_PLACES = [
    {"text": "Allen Parish PSC", "lat": 30.622560, "lon": -92.774930, "align": "tl"},
    {"text": "Central LA ICE Processing Center", "lat": 31.7088575, "lon": -92.1529335, "align": "bl"},
    {"text": "Jackson Parish CC", "lat": 32.2158444, "lon": -92.7172768, "align": "tl"},
    {"text": "LA ICE Processing Center", "lat": 30.9598118, "lon": -91.6071326, "align": "br"},
    {"text": "Pine Prairie ICE Processing Center", "lat": 30.7890963, "lon": -92.4225038, "align": "br"},
    {"text": "Richwood CC", "lat": 32.4583842, "lon": -92.0782853, "align": "tr"},
    {"text": "River CC", "lat": 31.5971775, "lon": -91.5584304, "align": "tr"},
    {"text": "South LA ICE Processing Center", "lat": 30.4875023, "lon": -92.5831512, "align": "bl"},
    {"text": "Winn CC", "lat": 31.8502693, "lon": -92.7792943, "align": "tl"},
]

# Representation-odds map constants
REPRESENTATION_MAP_TITLE = "How does legal representation in deportation cases vary across Louisiana?"
REPRESENTATION_DESCRIPTION_PREFIX = (
    "The map below shows what share of immigrants with addresses in each "
    "parish who had pending deportation proceedings against them in the "
    "Justice Department's Immigration Courts "
)
REPRESENTATION_VALUE_COLUMN = "All Pending Cases - Odds of Representation"
WITHOUT_REPRESENTATION_COLUMN = "Without Representation"
REPRESENTATION_MIN_CASES = 20  # parishes below this get grayed out, not colored
REPRESENTATION_LEGEND_TITLE = "Share of pending cases with legal representation (%)"
REPRESENTATION_SMALL_SAMPLE_LABEL = f"Fewer than {REPRESENTATION_MIN_CASES} pending cases"
REPRESENTATION_SMALL_SAMPLE_COLOR = "#cccccc"

# Hard-codes the bin edges for the representation-odds map based on current (through June 2026) data
# ranging from 26.1% to 82.4% in parishes with sufficient cases. THESE BOUNDARIES
# MAY HAVE TO BE CHANGED BY HAND IF TRAC'S DATA CHANGES SIGNIFICANTLY. They were chosen
# to give a good visual spread across the map while representing the fact that distance from 100%
# is really what we're trying to communicate visually.
REPRESENTATION_BIN_EDGES = [0, 30, 40, 50, 60, 70, 80, 90, 100]

# This hardcodes in the six color stops from the pending-cases map for use in the 
# representation-odds map. Everything else below (which bin each parish
# lands in, how many shades get interpolated) is computed at run time from
# REPRESENTATION_BIN_EDGES and that day's scraped data, never from fixed
# per-parish numbers. representation_bin_colors() below reverses this
# ramp (dark = lowest odds, per the brief) and interpolates it up from 6
# stops to one shade per REPRESENTATION_BIN_EDGES band, since Datawrapper
# picked continuous colors between these stops for the pending-cases map,
# and this map has to do that math itself now -- see the module docstring
# for why.
_REPRESENTATION_COLOR_STOPS = [
    (0.0, "#feebe2"),
    (0.2, "#fcc5c0"),
    (0.4, "#fa9fb5"),
    (0.6, "#f768a1"),
    (0.8, "#c51b8a"),
    (1.0, "#7a0177"),
]


def _interpolate_hex(position: float, stops=_REPRESENTATION_COLOR_STOPS) -> str:
    # Linearly interpolates an RGB hex color at `position` (0-1) along
    # `stops`, a low-to-high list of (position, hex) pairs. Plain linear RGB
    # blending between the same stop colors Datawrapper itself uses.
    for (pos0, hex0), (pos1, hex1) in zip(stops, stops[1:]):
        if pos0 <= position <= pos1:
            fraction = 0.0 if pos1 == pos0 else (position - pos0) / (pos1 - pos0)
            rgb0 = tuple(int(hex0[i : i + 2], 16) for i in (1, 3, 5))
            rgb1 = tuple(int(hex1[i : i + 2], 16) for i in (1, 3, 5))
            blended = tuple(round(rgb0[c] + fraction * (rgb1[c] - rgb0[c])) for c in range(3))
            return "#{:02x}{:02x}{:02x}".format(*blended)
    return stops[-1][1]


def representation_bin_colors(bin_count: int = len(REPRESENTATION_BIN_EDGES) - 1) -> list[str]:
    # bin_count shades sampled evenly across _REPRESENTATION_COLOR_STOPS,
    # then reversed so index 0 (the lowest-odds bin) is darkest. Defaults
    # to one shade per REPRESENTATION_BIN_EDGES band, so the palette always
    # has exactly as many colors as the number of bins.
    ramp = [_interpolate_hex(i / (bin_count - 1)) for i in range(bin_count)]
    return list(reversed(ramp))

STATE_FILE = Path(__file__).with_name("datawrapper_chart_ids.json")
DATA_THROUGH_FILE = Path(__file__).with_name("data_through.txt")

# Louisiana parish name (as TRAC's table renders it, "<Name> Parish") -> the
# parish's 5-digit Census FIPS/GEOID code (state code 22 + 3-digit county
# code). Source: the FIPS column of Wikipedia's "List of parishes in
# Louisiana", cross-checked against 16 parish names actually seen in TRAC's
# table output.
LOUISIANA_PARISH_FIPS = {
    "Acadia Parish": "22001",
    "Allen Parish": "22003",
    "Ascension Parish": "22005",
    "Assumption Parish": "22007",
    "Avoyelles Parish": "22009",
    "Beauregard Parish": "22011",
    "Bienville Parish": "22013",
    "Bossier Parish": "22015",
    "Caddo Parish": "22017",
    "Calcasieu Parish": "22019",
    "Caldwell Parish": "22021",
    "Cameron Parish": "22023",
    "Catahoula Parish": "22025",
    "Claiborne Parish": "22027",
    "Concordia Parish": "22029",
    "De Soto Parish": "22031",
    "East Baton Rouge Parish": "22033",
    "East Carroll Parish": "22035",
    "East Feliciana Parish": "22037",
    "Evangeline Parish": "22039",
    "Franklin Parish": "22041",
    "Grant Parish": "22043",
    "Iberia Parish": "22045",
    "Iberville Parish": "22047",
    "Jackson Parish": "22049",
    "Jefferson Parish": "22051",
    "Jefferson Davis Parish": "22053",
    "Lafayette Parish": "22055",
    "Lafourche Parish": "22057",
    "LaSalle Parish": "22059",
    "Lincoln Parish": "22061",
    "Livingston Parish": "22063",
    "Madison Parish": "22065",
    "Morehouse Parish": "22067",
    "Natchitoches Parish": "22069",
    "Orleans Parish": "22071",
    "Ouachita Parish": "22073",
    "Plaquemines Parish": "22075",
    "Pointe Coupee Parish": "22077",
    "Rapides Parish": "22079",
    "Red River Parish": "22081",
    "Richland Parish": "22083",
    "Sabine Parish": "22085",
    "St. Bernard Parish": "22087",
    "St. Charles Parish": "22089",
    "St. Helena Parish": "22091",
    "St. James Parish": "22093",
    "St. John the Baptist Parish": "22095",
    "St. Landry Parish": "22097",
    "St. Martin Parish": "22099",
    "St. Mary Parish": "22101",
    "St. Tammany Parish": "22103",
    "Tangipahoa Parish": "22105",
    "Tensas Parish": "22107",
    "Terrebonne Parish": "22109",
    "Union Parish": "22111",
    "Vermilion Parish": "22113",
    "Vernon Parish": "22115",
    "Washington Parish": "22117",
    "Webster Parish": "22119",
    "West Baton Rouge Parish": "22121",
    "West Carroll Parish": "22123",
    "West Feliciana Parish": "22125",
    "Winn Parish": "22127",
}

# The following functions are the main logic for loading, cleaning, and publishing the data to Datawrapper.
def load_and_clean_csv(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str)

    unmatched = sorted(set(df[CSV_COLUMN_COUNTY_NAME]) - set(LOUISIANA_PARISH_FIPS))
    if unmatched:
        raise ValueError(
            "These parish names from the CSV have no FIPS code in "
            "LOUISIANA_PARISH_FIPS -- TRAC's table formatting may have "
            f"changed: {unmatched}"
        )

    # Strips the thousands separator from the numeric columns and converts them to int
    for col in NUMERIC_COUNT_COLUMNS:
        df[col] = df[col].str.replace(",", "", regex=False).astype(int)

    # Parses the "odds of representation" columns as float
    for col in df.columns:
        if col.endswith("Odds of Representation"):
            df[col] = df[col].astype(float)

    return df

# Loads the date_through text from the file written by trac_louisiana_scraper.py, or raises an error.
def load_data_through() -> str:
    if not DATA_THROUGH_FILE.exists():
        raise FileNotFoundError(
            f"{DATA_THROUGH_FILE.name} not found -- run trac_louisiana_scraper.py "
            "first, it writes this alongside the CSV."
        )
    return DATA_THROUGH_FILE.read_text().strip()

# Loads the chart IDs from either environment variables or the local state file (for local runs).
def load_chart_ids() -> dict:
    """Prefer env vars (durable across GitHub Actions runs); fall back to
    the local state file (durable across local re-runs)."""
    map_id = os.environ.get("DATAWRAPPER_MAP_CHART_ID")
    representation_id = os.environ.get("DATAWRAPPER_REPRESENTATION_CHART_ID")
    table_id = os.environ.get("DATAWRAPPER_TABLE_CHART_ID")
    if map_id and representation_id and table_id:
        return {
            "map_chart_id": map_id,
            "representation_chart_id": representation_id,
            "table_chart_id": table_id,
        }

    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())

    return {}

# Saves the chart IDs to a local state file (for local runs)
def save_chart_ids(ids: dict) -> None:
    STATE_FILE.write_text(json.dumps(ids, indent=2))

# The following functions are the main logic for creating and updating the Datawrapper charts.
# This is for the pending-cases map.
def upsert_map_chart(
    dw: Datawrapper, chart_id: str | None, df: pd.DataFrame, data_through: str
) -> str:
    map_df = df[[CSV_COLUMN_COUNTY_NAME, MAP_VALUE_COLUMN]].copy()
    map_df.insert(0, "GEOID", map_df[CSV_COLUMN_COUNTY_NAME].map(LOUISIANA_PARISH_FIPS))

    metadata = {
        "axes": {"keys": "GEOID", "values": MAP_VALUE_COLUMN},
        "visualize": {
            "basemap": "louisiana-counties",
            "map-key-attr": "GEOID",
            "colorscale": {
                "mode": "continuous",
                "interpolation": "natural-9",
            },
            "tooltip": {
                "title": "{{ county_name }}",
                "body": f"Pending cases ({data_through}): "
                "<b>{{ all_pending_cases_number }}</b>",
            },
            "legend": {"enabled": True, "title": LEGEND_TITLE},
            "map-padding": 2,
            "max-map-height": 450,
            "labels": {
                "enabled": True,
                "type": "places",
                "max": len(LABEL_PLACES),
                "places": [
                    {
                        "x": place["lon"],
                        "y": place["lat"],
                        "text": place["text"],
                        "type": "point",
                        "align": place["align"],
                        "custom": True,
                        "visible": True,
                        "inverted": True,
                    }
                    for place in LABEL_PLACES
                ],
            },
        },
        "describe": {
            "source-name": SOURCE_NAME,
            "source-url": SOURCE_URL,
            "intro": MAP_DESCRIPTION_PREFIX + data_through + ".",
        },
        "annotate": {"notes": MAP_NOTES},
    }

    if chart_id:
        dw.update_chart(chart_id, title=MAP_TITLE, data=map_df, metadata=metadata)
    else:
        chart = dw.create_chart(
            title=MAP_TITLE,
            chart_type="d3-maps-choropleth",
            data=map_df,
            metadata=metadata,
        )
        chart_id = chart["id"]

    dw.publish_chart(chart_id)
    return chart_id

# This is for the representation-odds map.
def _representation_category_labels(rep_df: pd.DataFrame) -> tuple[pd.Series, list[str]]:
    """Bin REPRESENTATION_VALUE_COLUMN into the fixed, editorial bands
    defined by REPRESENTATION_BIN_EDGES, and return a text category for
    every row (small-sample parishes get REPRESENTATION_SMALL_SAMPLE_LABEL
    instead of a null).

    REPRESENTATION_BIN_EDGES uses unequal, hand-chosen widths.

    The bin EDGES themselves are a fixed list (never data-dependent, never
    reshaped by that run's data), but which parishes fall in which bin is
    still recomputed fresh from that run's real REPRESENTATION_VALUE_COLUMN
    every time.
    """
    eligible = rep_df[MAP_VALUE_COLUMN] >= REPRESENTATION_MIN_CASES

    values = rep_df.loc[eligible, REPRESENTATION_VALUE_COLUMN]
    out_of_range = values[(values < 0) | (values > 100)]
    if not out_of_range.empty:
        raise ValueError(
            f"{REPRESENTATION_VALUE_COLUMN} has values outside the expected "
            f"0-100 range, can't bin onto the fixed REPRESENTATION_BIN_EDGES "
            f"scale -- TRAC's data may have changed format: "
            f"{out_of_range.to_dict()}"
        )

    edges = REPRESENTATION_BIN_EDGES
    labels = [f"{edges[i]:g}–{edges[i + 1]:g}%" for i in range(len(edges) - 1)]
    binned = pd.cut(values, bins=edges, labels=labels, include_lowest=True)

    category = pd.Series(REPRESENTATION_SMALL_SAMPLE_LABEL, index=rep_df.index, dtype=object)
    category.loc[eligible] = binned.astype(str)
    return category, labels

# This is for the representation-odds map.
def upsert_representation_map_chart(
    dw: Datawrapper, chart_id: str | None, df: pd.DataFrame, data_through: str
) -> str:
    rep_df = df[
        [CSV_COLUMN_COUNTY_NAME, MAP_VALUE_COLUMN, REPRESENTED_COLUMN, REPRESENTATION_VALUE_COLUMN]
    ].copy()
    rep_df.insert(0, "GEOID", rep_df[CSV_COLUMN_COUNTY_NAME].map(LOUISIANA_PARISH_FIPS))
    rep_df[WITHOUT_REPRESENTATION_COLUMN] = rep_df[MAP_VALUE_COLUMN] - rep_df[REPRESENTED_COLUMN]

    # The column actually bound to the map's color (axes.values) holds text
    # category labels, never the raw number and never null. A null axes.values
    # entry kills the tooltip for that region with no setting to undo it.
    # REPRESENTATION_VALUE_COLUMN itself stays real and un-nulled for every
    # parish, so the tooltip always shows the exact percentage.
    category_column = f"{REPRESENTATION_VALUE_COLUMN} (category)"
    rep_df[category_column], bin_labels = _representation_category_labels(rep_df)
    # bin_labels is always exactly len(REPRESENTATION_BIN_EDGES) - 1 long, matching
    # representation_bin_colors()'s default length. zip() here
    # is just pairing two equal-length, already-ordered lists.
    color_map = dict(zip(bin_labels, representation_bin_colors()))
    color_map[REPRESENTATION_SMALL_SAMPLE_LABEL] = REPRESENTATION_SMALL_SAMPLE_COLOR

# The metadata for the representation-odds map chart, including axes, visualization settings, tooltip formatting, legend, labels, and description.
    metadata = {
        "axes": {"keys": "GEOID", "values": category_column},
        "visualize": {
            "basemap": "louisiana-counties",
            "map-key-attr": "GEOID",
            "colorscale": {
                "mode": "continuous",
                "map": color_map,
                "categoryOrder": bin_labels + [REPRESENTATION_SMALL_SAMPLE_LABEL],
            },
            "tooltip": {
                "title": "{{ county_name }}",
                "body": (
                    f"Share of pending cases with legal representation "
                    f"({data_through}): "
                    "<b>{{ all_pending_cases_odds_of_representation }}%</b><br>"
                    "{{ without_representation }} of "
                    "{{ all_pending_cases_number }} pending cases without "
                    "representation"
                ),
            },
            "legend": {"enabled": True, "title": REPRESENTATION_LEGEND_TITLE},
            "map-padding": 2,
            "max-map-height": 450,
            "labels": {
                "enabled": True,
                "type": "places",
                "max": len(LABEL_PLACES),
                "places": [
                    {
                        "x": place["lon"],
                        "y": place["lat"],
                        "text": place["text"],
                        "type": "point",
                        "align": place["align"],
                        "custom": True,
                        "visible": True,
                        "inverted": True,
                    }
                    for place in LABEL_PLACES
                ],
            },
        },
        "describe": {
            "source-name": SOURCE_NAME,
            "source-url": SOURCE_URL,
            "intro": REPRESENTATION_DESCRIPTION_PREFIX
            + data_through
            + " were legally represented.",
        },
        "annotate": {"notes": MAP_NOTES},
    }

# Updates or creates the representation-odds map chart in Datawrapper, and publishes it.
    if chart_id:
        dw.update_chart(chart_id, title=REPRESENTATION_MAP_TITLE, data=rep_df, metadata=metadata)
    else:
        chart = dw.create_chart(
            title=REPRESENTATION_MAP_TITLE,
            chart_type="d3-maps-choropleth",
            data=rep_df,
            metadata=metadata,
        )
        chart_id = chart["id"]

    dw.publish_chart(chart_id)
    return chart_id


# This is for the table chart.
def upsert_table_chart(dw: Datawrapper, chart_id: str | None, df: pd.DataFrame) -> str:
    table_df = df.sort_values(MAP_VALUE_COLUMN, ascending=False)

    metadata = {
        "visualize": {"searchable": True, "sortable": True},
        "describe": {
            "source-name": SOURCE_NAME,
            "source-url": SOURCE_URL,
        },
    }

# Updates or creates the table chart in Datawrapper, and publishes it.
    if chart_id:
        dw.update_chart(chart_id, data=table_df, metadata=metadata)
    else:
        chart = dw.create_chart(
            title="Louisiana Pending Immigration Court Cases by Parish",
            chart_type="tables",
            data=table_df,
            metadata=metadata,
        )
        chart_id = chart["id"]

    dw.publish_chart(chart_id)
    return chart_id

# The main function that orchestrates loading the CSV, cleaning the data, loading chart IDs, and publishing the charts to Datawrapper.
def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "louisiana_county_cases.csv"

    token = os.environ.get("DATAWRAPPER_ACCESS_TOKEN")
    if not token:
        sys.exit(
            "DATAWRAPPER_ACCESS_TOKEN is not set. Create a token at "
            "app.datawrapper.de/account/api-tokens and set it as an env var "
            "(a GitHub Actions secret, when running in CI)."
        )

    df = load_and_clean_csv(csv_path)
    print(f"Loaded {len(df)} parishes from {csv_path}")

    data_through = load_data_through()
    print(f"Data currency text: {data_through!r}")

    dw = Datawrapper(access_token=token)
    ids = load_chart_ids()

    map_id = upsert_map_chart(dw, ids.get("map_chart_id"), df, data_through)
    representation_id = upsert_representation_map_chart(
        dw, ids.get("representation_chart_id"), df, data_through
    )
    table_id = upsert_table_chart(dw, ids.get("table_chart_id"), df)

    save_chart_ids(
        {
            "map_chart_id": map_id,
            "representation_chart_id": representation_id,
            "table_chart_id": table_id,
        }
    )

    print(f"Pending-cases map published:      https://www.datawrapper.de/_/{map_id}/")
    print(f"Representation-odds map published: https://www.datawrapper.de/_/{representation_id}/")
    print(f"Table published:                   https://www.datawrapper.de/_/{table_id}/")
    print(
        "\nIf DATAWRAPPER_MAP_CHART_ID / DATAWRAPPER_REPRESENTATION_CHART_ID / "
        "DATAWRAPPER_TABLE_CHART_ID aren't set as env vars yet, set them now "
        f"to these three IDs ({map_id}, {representation_id}, {table_id}) as "
        "GitHub Actions repo variables so future scheduled runs update these "
        "same charts instead of creating new ones."
    )


if __name__ == "__main__":
    main()