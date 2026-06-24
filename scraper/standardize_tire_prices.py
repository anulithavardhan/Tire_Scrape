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
    """
    Equivalent to Excel:
    =LET(x,REGEXEXTRACT(B6378,"(LT|P)?\\d{3}/\\d{2}R\\d{2}|\\d{2,3}X\\d{1,2}\\.\\d{2}R\\d{2}"),
    IF(LEFT(x,1)="P",MID(x,2,99),x))

    Keeps LT.
    Removes leading P.
    Handles flotation sizes like 35X12.50R20.
    """
    if pd.isna(size):
        return None

    text = str(size).upper().strip()

    match = re.search(r"(LT|P)?\d{3}/\d{2}R\d{2}|\d{2,3}X\d{1,2}\.\d{2}R\d{2}", text)
    if not match:
        return None

    x = match.group(0)

    if x.startswith("P"):
        x = x[1:]

    return x


def map_key_from_raw_size(raw_size):
    """
    Matches your MAP table logic:
    225/65R17 -> 2256517
    LT265/70R17 -> LT2657017
    35X12.50R20 -> 35X12.5020LT style may need exact table alignment
    """
    if pd.isna(raw_size) or raw_size is None:
        return None

    text = str(raw_size).upper().strip()

    if text.startswith("LT"):
        return "LT" + re.sub(r"[^0-9]", "", text)

    if "X" in text:
        # This follows your MAP table style better than pure digit stripping.
        # Example: 35X12.50R20 -> 35X12.5020LT
        return text.replace("R", "").strip() + "LT"

    return re.sub(r"[^0-9]", "", text)


def brand_product_key(model):
    if pd.isna(model) or model is None:
        return None
    return f"GT RADIAL {str(model).upper().strip()}"


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

    return df[["website", "model", "size", "price_per_tire", "url"]]


def standardize_simpletire(df):
    df = df.copy()

    df["website"] = "Simple Tire"
    df["model"] = df["productName"].apply(normalize_model)
    df["price_per_tire"] = df["price"].apply(clean_money)

    return df[["website", "model", "size", "price_per_tire", "url"]]


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

    final_df["Brand+Product"] = final_df["model"].apply(brand_product_key)
    final_df["MAP_KEY"] = final_df["RAW SIZE"].apply(map_key_from_raw_size)

    map_df = pd.read_csv("scraper/map_prices.csv")
    map_df["Brand+Product"] = map_df["Brand+Product"].astype(str).str.upper().str.strip()
    map_df["MAP_KEY"] = map_df["Key 1 [Raw size]"].astype(str).str.upper().str.strip()
    map_df["MAP"] = map_df["MAP"].apply(clean_money)

    final_df = final_df.merge(
        map_df[["Brand+Product", "MAP_KEY", "MAP"]],
        how="left",
        on=["Brand+Product", "MAP_KEY"]
    )

    final_df = final_df[
        [
            "website",
            "model",
            "size",
            "price_per_tire",
            "url",
            "RAW SIZE",
            "DATE",
            "MAP",
        ]
    ]

    out_file = f"tire_prices_{TODAY}.csv"
    final_df.to_csv(out_file, index=False)

    print(f"Saved standardized file: {out_file}")
    print(f"Rows: {len(final_df)}")

    missing_map = final_df[final_df["MAP"].isna()]
    if not missing_map.empty:
        print(f"Warning: {len(missing_map)} rows did not match MAP.")
        print(missing_map[["website", "model", "size", "RAW SIZE"]].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
