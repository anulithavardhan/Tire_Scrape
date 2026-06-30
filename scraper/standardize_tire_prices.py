import os
import re
import glob
import datetime
import pandas as pd


TODAY = datetime.datetime.utcnow().strftime("%Y-%m-%d")


def clean_money(value):
    if pd.isna(value):
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_bool(value):
    if pd.isna(value):
        return False

    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()

    return text in ["true", "1", "yes", "y", "in stock"]


def normalize_model(value):
    if pd.isna(value):
        return None

    text = str(value).strip()

    replacements = {
        "gt radial maxtour lx": "Maxtour LX",
        "maxtour lx": "Maxtour LX",

        "gt radial maxclimate": "Maxclimate",
        "maxclimate": "Maxclimate",
        "maxclimate tire": "Maxclimate",

        "gt radial adventuro ht": "Adventuro HT",
        "adventuro ht": "Adventuro HT",

        "gt radial adventuro atx": "Adventuro ATX",
        "adventuro atx": "Adventuro ATX",
    }

    key = text.lower().replace("-", " ").replace("  ", " ").strip()
    return replacements.get(key, text)


def raw_size_from_size(size):
    if pd.isna(size):
        return None

    text = str(size).upper().strip()

    match = re.search(
        r"(LT|P)?\d{3}/\d{2}R\d{2}|\d{2,3}X\d{1,2}\.\d{2}R\d{2}",
        text
    )

    if not match:
        return None

    x = match.group(0)

    if x.startswith("P"):
        x = x[1:]

    return x


def standardize_existing_giga_priority(df):
    df = df.copy()

    df["website"] = df["source"].map({
        "giga": "Giga",
        "priority": "Priority",
        "Giga": "Giga",
        "Priority": "Priority",
    }).fillna(df["source"])

    df["model"] = df["model"].apply(normalize_model)
    df["price_per_tire"] = df["price_per_tire"].apply(clean_money)
    df["in_stock"] = df["in_stock"].apply(normalize_bool)

    # Keep only in-stock Giga/Priority rows
    df = df[df["in_stock"] == True]

    return df[["website", "model", "size", "price_per_tire", "in_stock", "url"]]


def standardize_simpletire(df):
    df = df.copy()

    df["website"] = "Simple Tire"
    df["model"] = df["productName"].apply(normalize_model)
    df["price_per_tire"] = df["price"].apply(clean_money)

    # SimpleTire script does not currently return in_stock.
    # So for now, treat rows with a valid price as available.
    df["in_stock"] = df["price_per_tire"].notna()

    # Keep only rows that appear available
    df = df[df["in_stock"] == True]

    return df[["website", "model", "size", "price_per_tire", "in_stock", "url"]]


def main():
    existing_files = sorted(glob.glob("tire_prices_*.csv"))

    if not existing_files:
        raise FileNotFoundError("No tire_prices_*.csv file found from the existing Python scraper.")

    latest_existing_file = existing_files[-1]
    print(f"Reading existing Giga/Priority file: {latest_existing_file}")

    all_frames = []

    existing_df = pd.read_csv(latest_existing_file)
    all_frames.append(standardize_existing_giga_priority(existing_df))

    if os.path.exists("simpletire_raw.csv"):
        print("Reading SimpleTire file: simpletire_raw.csv")
        simple_df = pd.read_csv("simpletire_raw.csv")
        all_frames.append(standardize_simpletire(simple_df))
    else:
        print("No simpletire_raw.csv found. Keeping only Giga/Priority rows.")

    final_df = pd.concat(all_frames, ignore_index=True)

    final_df["RAW SIZE"] = final_df["size"].apply(raw_size_from_size)
    final_df["DATE"] = TODAY

    map_df = pd.read_csv("scraper/map_prices.csv")

    map_df["model"] = map_df["Brand+Product"].apply(normalize_model)
    map_df["RAW SIZE"] = map_df["Rawsize"].apply(raw_size_from_size)
    map_df["MAP"] = map_df["MAP"].apply(clean_money)

    final_df = final_df.merge(
        map_df[["model", "RAW SIZE", "MAP"]],
        how="left",
        on=["model", "RAW SIZE"]
    )

    final_df = final_df[
        [
            "website",
            "model",
            "size",
            "price_per_tire",
            "in_stock",
            "url",
            "RAW SIZE",
            "DATE",
            "MAP",
        ]
    ]

    out_file = f"tire_prices_{TODAY}.csv"
    final_df.to_csv(out_file, index=False)

    print(f"Saved standardized file: {out_file}")
    print(f"Rows after keeping only in-stock rows: {len(final_df)}")

    missing_map = final_df[final_df["MAP"].isna()]
    if not missing_map.empty:
        print(f"Warning: {len(missing_map)} rows did not match MAP.")
        print(missing_map[["website", "model", "size", "RAW SIZE"]].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
