"""
Combined Tire Scraper — GitHub Actions Edition
===============================================
Scrapes Giga Tires + Priority Tire and writes results to a
timestamped CSV file. No Google auth needed.

Output: tire_prices_YYYY-MM-DD.csv  (in the same directory)
"""

import asyncio, json, re, datetime, sys
import nest_asyncio
nest_asyncio.apply()

import pandas as pd
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from urllib.parse import urljoin

# ─────────────────────────────────────────────────────────────────────────────
# GIGA TIRES
# ─────────────────────────────────────────────────────────────────────────────
GIGA_BASE_URL      = "https://www.giga-tires.com"
GIGA_MAX_RETRY     = 3
GIGA_PAGE_TIMEOUT  = 90_000
GIGA_WAIT_AFTER_MS = 5_000

GIGA_SEEDS = [
    {"model": "Maxtour LX",             "url": f"{GIGA_BASE_URL}/235-45-18/gt-radial-tires/maxtour-lx/tirecode/AS122"},
    {"model": "Maxclimate",             "url": f"{GIGA_BASE_URL}/225-40-18/gt-radial-tires/maxclimate/tirecode/100UA4532"},
    {"model": "Adventuro HT",           "url": f"{GIGA_BASE_URL}/235-75-15/gt-radial-tires/adventuro-ht/tirecode/100UA3630"},
    {"model": "Adventuro ATX",          "url": f"{GIGA_BASE_URL}/235-70-16/gt-radial-tires/adventuro-atx/tirecode/100UA3723"},
    {"model": "Champiro SX2",           "url": f"{GIGA_BASE_URL}/225-45-17/gt-radial-tires/champiro-sx2/tirecode/B611"},
    {"model": "Champiro HPY",           "url": f"{GIGA_BASE_URL}/255-35-18/gt-radial-tires/champiro-hpy/tirecode/B030"},
    {"model": "Maxmiler Pro",           "url": f"{GIGA_BASE_URL}/185-60-15/gt-radial-tires/maxmiler-pro/tirecode/B623"},
    {"model": "Champiro UHP A/S",       "url": f"{GIGA_BASE_URL}/195-55-15/gt-radial-tires/champiro-uhp-as/tirecode/100A2006"},
    {"model": "Champiro Touring A/S",   "url": f"{GIGA_BASE_URL}/185-65-14/gt-radial-tires/champiro-touring-a-s/tirecode/B513"},
    {"model": "Maxtour All Season",     "url": f"{GIGA_BASE_URL}/175-70-13/gt-radial-tires/maxtour-all-season/tirecode/AS065"},
    {"model": "Savero HT2",             "url": f"{GIGA_BASE_URL}/215-70-15/gt-radial-tires/savero-ht2/tirecode/B452"},
    {"model": "Adventuro AT3",          "url": f"{GIGA_BASE_URL}/235-75-15/gt-radial-tires/adventuro-at3/tirecode/AS087"},
    {"model": "Savero Komodo M/T Plus", "url": f"{GIGA_BASE_URL}/235-75-15/gt-radial-tires/savero-komodo-m-t-plus/tirecode/A289"},
]


def giga_empty(run_date, model, size, in_stock, url, error):
    return {
        "run_date": run_date, "source": "giga",
        "model": model, "size": size, "in_stock": in_stock,
        "price_per_tire": None, "original_price": None, "total_4_tires": None,
        "easy_score": None, "warranty": None, "reviews": None,
        "sku": None, "rating": None, "review_count": None,
        "url": url, "error": error,
    }


async def giga_safe_goto(page, url):
    for attempt in range(1, GIGA_MAX_RETRY + 1):
        try:
            await page.goto(url, wait_until="networkidle", timeout=GIGA_PAGE_TIMEOUT)
            await page.wait_for_function(
                """() => {
                    const price = document.querySelector(
                        '.product-price--lg .product-price__current-price'
                    );
                    return price && price.innerText.trim().startsWith('$');
                }""",
                timeout=20_000,
            )
            return True, ""
        except PlaywrightTimeout as e:
            if attempt < GIGA_MAX_RETRY:
                print(f"  [retry {attempt}]", end="", flush=True)
                await asyncio.sleep(5)
            else:
                return False, f"Timeout after {GIGA_MAX_RETRY} retries: {e}"
        except Exception as e:
            return False, f"Page load error: {e}"


async def giga_get_size_links(page, seed_url, model_name):
    print(f"\n[GIGA] ── {model_name}")
    print(f"  Loading seed: {seed_url}")
    ok, err = await giga_safe_goto(page, seed_url)
    if not ok:
        print(f"  ❌ Seed failed: {err}")
        return [], err

    links = await page.evaluate("""() => {
        const seen = new Set(), out = [];
        document.querySelectorAll('ul.j-dropdown-list li.j-dropdown-item').forEach(li => {
            const a = li.querySelector('a[href]');
            if (!a) return;
            const href = a.getAttribute('href');
            if (!href || href.startsWith('javascript') || seen.has(href)) return;
            seen.add(href);
            out.push({
                size:     a.innerText.trim().replace('- Out of Stock', '').trim(),
                href:     href,
                in_stock: !li.classList.contains('not-orderable'),
            });
        });
        return out;
    }""")

    print(f"  Found {len(links)} size variant(s).")
    return links, ""


async def giga_scrape_page(page, run_date, model, size, url, in_stock):
    result = giga_empty(run_date, model, size, in_stock, url, "")
    ok, err = await giga_safe_goto(page, url)
    if not ok:
        result["error"] = err
        return result
    try:
        data = await page.evaluate("""() => {
            const txt = sel => { const el = document.querySelector(sel); return el ? el.innerText.trim() : null; };
            return {
                price:    txt('.product-price--lg .product-price__current-price'),
                was:      txt('.product-price--lg .product-price__old-price .price'),
                total4:   txt('.product-price--md .product-price__current-price'),
                score:    txt('.easyscore__rating'),
                warranty: txt('.product-details-page__item-title-badge'),
                reviews:  txt('#product_just_stars .ind_cnt a'),
            };
        }""")
        result["price_per_tire"] = data["price"].replace("$","").strip()  if data.get("price")    else None
        result["original_price"] = data["was"].replace("$","").strip()    if data.get("was")      else None
        result["total_4_tires"]  = data["total4"].replace("$","").strip() if data.get("total4")   else None
        result["easy_score"]     = " ".join(data["score"].split())        if data.get("score")    else None
        result["warranty"]       = " ".join(data["warranty"].split())     if data.get("warranty") else None
        result["reviews"]        = data.get("reviews")
        if not result["price_per_tire"]:
            result["error"] = "Price not found"
    except Exception as e:
        result["error"] = f"Parse error: {e}"
    return result


async def run_giga(run_date):
    results = []
    print("\n" + "═"*60)
    print("  GIGA TIRES")
    print("═"*60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage","--disable-gpu"],
        )
        ctx  = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )
        page = await ctx.new_page()
        stealth = Stealth()
        await stealth.apply_stealth_async(page)

        for seed in GIGA_SEEDS:
            model_name = seed["model"]
            try:
                size_links, seed_err = await giga_get_size_links(page, seed["url"], model_name)
            except Exception as e:
                seed_err = f"Seed crashed: {e}"
                size_links = []

            if not size_links:
                results.append(giga_empty(run_date, model_name, None, None, seed["url"], seed_err or "No sizes found"))
                continue

            total = len(size_links)
            for i, item in enumerate(size_links, 1):
                size     = item.get("size")
                url      = urljoin(GIGA_BASE_URL, item.get("href"))
                in_stock = item.get("in_stock")
                print(f"  [{i:02}/{total}] {str(size):15s}", end="  ", flush=True)
                try:
                    data = await giga_scrape_page(page, run_date, model_name, size, url, in_stock)
                except Exception as e:
                    data = giga_empty(run_date, model_name, size, in_stock, url, f"Crashed: {e}")
                results.append(data)
                price = f"${data.get('price_per_tire') or 'N/A'}"
                stock = "In Stock" if in_stock else "OOS"
                err   = f"  !! {data.get('error')}" if data.get("error") else ""
                print(f"{price:>8}  {stock}{err}")
                await asyncio.sleep(0.5)

        await browser.close()

    print(f"\n  Giga: {len(results)} rows collected.")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# PRIORITY TIRE
# ─────────────────────────────────────────────────────────────────────────────
PRIORITY_BASE_URL   = "https://www.prioritytire.com"
PRIORITY_CONCURRENT = 10
PRIORITY_MAX_RETRY  = 3

PRIORITY_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
}

PRIORITY_SEEDS = [
    {"model": "Maxtour LX",             "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/maxtour-lx/215-45r17-87v-7388"},
    {"model": "Maxclimate",             "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/gt-radial-maxclimate/225-40r18-92v-xl-173817"},
    {"model": "Adventuro HT",           "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/adventuro-ht/235-85r16-120-116s-e-10-ply-14309"},
    {"model": "Adventuro ATX",          "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/adventuro-atx/235-75r15-108s-xl-19048"},
    {"model": "Champiro SX2",           "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/champiro-sx2/235-45r17-94w-zr-11644"},
    {"model": "Champiro HPY",           "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/champiro-hpy/225-40r19-93y-xl-zr-63955"},
    {"model": "Champiro UHP A/S",       "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/champiro-uhp-a-s/215-55r17-94v-43619"},
    {"model": "Maxmiler Pro",           "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/maxmiler-pro/245-75r16-120-116q-e-10-ply-3623"},
    {"model": "Champiro Touring A/S",   "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/champiro-touring-a-s/205-55r16-91h-43691"},
    {"model": "Maxtour All Season",     "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/maxtour-all-season/185-65r15-88t-52698"},
    {"model": "Savero HT2",             "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/savero-ht2/275-55r20-111h-66171"},
    {"model": "Adventuro AT3",          "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/adventuro-at3/235-85r16-120-116s-e-10-ply-43734"},
    {"model": "Savero Komodo M/T Plus", "url": f"{PRIORITY_BASE_URL}/by-brand/gt-radial-tires/savero-komodo-m-t-plus/235-75r15-104-101q-c-6-ply-53015"},
]


def _priority_product_path(url):
    parts = url.replace(PRIORITY_BASE_URL, "").strip("/").split("/")
    return "/" + "/".join(parts[:3])


async def priority_get_size_urls(page, seed):
    model_name        = seed["model"]
    seed_url          = seed["url"]
    seed_product_path = _priority_product_path(seed_url)

    print(f"\n[PRIORITY] ── {model_name}")
    print(f"  Loading seed: {seed_url}")

    await page.goto(seed_url, wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(2)

    raw = await page.evaluate(
        "() => { const el = document.getElementById('__NEXT_DATA__'); return el ? el.textContent : null; }"
    )
    nd     = json.loads(raw)
    apollo = nd["props"]["pageProps"]["apolloState"]

    config_product = next(
        (v for k, v in apollo.items()
         if k.startswith("ConfigurableProduct:") and isinstance(v, dict) and "variants" in v),
        None,
    )
    if not config_product:
        print(f"  ⚠️  No ConfigurableProduct found — skipping.")
        return []

    print(f"  Found {len(config_product['variants'])} variants in apolloState")

    variants = []
    for v in config_product["variants"]:
        if not isinstance(v, dict):
            continue
        size_label = None
        for attr in (v.get("attributes") or []):
            if isinstance(attr, dict) and attr.get("__ref"):
                attr = apollo.get(attr["__ref"], {})
            lbl = attr.get("label") or attr.get("store_label")
            if lbl:
                size_label = lbl
                break

        prod_ref = v.get("product") or {}
        if isinstance(prod_ref, dict) and prod_ref.get("__ref"):
            simple = apollo.get(prod_ref["__ref"], {})
        else:
            simple = prod_ref

        sku_id = simple.get("id") or simple.get("uid")
        slug   = None
        for rew in (simple.get("url_rewrites") or []):
            if isinstance(rew, dict) and rew.get("__ref"):
                rew = apollo.get(rew["__ref"], {})
            if isinstance(rew, dict) and rew.get("url"):
                slug = "/" + rew["url"].lstrip("/")
                break

        if not slug and size_label and sku_id:
            size_slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]", "-", size_label.lower())).strip("-")
            slug = f"{seed_product_path}/{size_slug}-{sku_id}"

        if slug:
            variants.append({
                "model":    model_name,
                "size":     size_label or slug.split("/")[-1],
                "url":      PRIORITY_BASE_URL + slug,
                "sku":      str(sku_id) if sku_id else None,
                "in_stock": simple.get("stock_status") == "IN_STOCK" if "stock_status" in simple else None,
            })

    seen, deduped = set(), []
    for v in variants:
        if v["url"] not in seen:
            seen.add(v["url"])
            deduped.append(v)

    print(f"  → {len(deduped)} unique sizes ready to scrape")
    return deduped


def priority_parse_next_data(html, fallback_size, url=None, sku=None):
    result = {
        "price_per_tire": None, "total_4_tires": None,
        "rating": None, "review_count": None,
        "warranty": None, "in_stock": None, "size": fallback_size,
    }
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return result
    try:
        nd     = json.loads(m.group(1))
        apollo = nd["props"]["pageProps"]["apolloState"]

        # priority_get_size_urls() already discovered the actual Apollo
        # SimpleProduct id/uid for this variant. Prefer it over the number at
        # the end of the public URL, which is not always the Apollo product id.
        target_id = str(sku) if sku is not None else None
        if target_id is None and url:
            last_seg = url.rstrip("/").split("/")[-1]
            id_match = re.search(r"-(\d+)$", last_seg)
            if id_match:
                target_id = id_match.group(1)

        simple = None
        for key, val in apollo.items():
            if key.startswith("SimpleProduct:") and isinstance(val, dict) and "price_range" in val:
                product_id = val.get("id") or val.get("uid")
                if target_id is None or str(product_id) == target_id:
                    simple = val
                    break

        config = next((v for k, v in apollo.items()
                       if k.startswith("ConfigurableProduct:") and isinstance(v, dict)), None)

        if simple:
            try:
                pr = simple["price_range"]
                if isinstance(pr, dict) and pr.get("__ref"):  pr = apollo.get(pr["__ref"], {})
                mp = pr.get("minimum_price") or {}
                if isinstance(mp, dict) and mp.get("__ref"):  mp = apollo.get(mp["__ref"], {})
                fp = mp.get("final_price") or {}
                if isinstance(fp, dict) and fp.get("__ref"):  fp = apollo.get(fp["__ref"], {})
                pv = fp.get("value")
                if pv is not None:
                    result["price_per_tire"] = float(pv)
                    result["total_4_tires"]  = round(float(pv) * 4, 2)
            except Exception:
                pass
            if simple.get("stock_status"):
                result["in_stock"] = simple["stock_status"] == "IN_STOCK"

        if config:
            rd = config.get("productRating") or {}
            if isinstance(rd, dict) and rd.get("__ref"):
                rd = apollo.get(rd["__ref"], {})
            rv = rd.get("rating_summary") or config.get("rating_summary")
            if rv:
                try:    result["rating"] = f"{float(rv)/20:.1f}/5"
                except: result["rating"] = str(rv)
            if config.get("review_count") is not None:
                result["review_count"] = str(config["review_count"])
            if config.get("treadlife_warranty_text"):
                result["warranty"] = config["treadlife_warranty_text"]

        soup = BeautifulSoup(html, "lxml")

        if result["price_per_tire"] is None:
            el = soup.select_one(".ProductPagePrice-finalPrice span")
            if el:
                try:
                    result["price_per_tire"] = float(el.get_text(strip=True).replace("$","").replace(",",""))
                    result["total_4_tires"]  = round(result["price_per_tire"] * 4, 2)
                except ValueError:
                    pass

        if result["in_stock"] is None:
            page_text = " ".join(soup.stripped_strings)
            if re.search(r"\bIn Stock\b", page_text, re.IGNORECASE):
                result["in_stock"] = True
            elif re.search(r"\bOut of Stock\b", page_text, re.IGNORECASE):
                result["in_stock"] = False
    except Exception as e:
        result["parse_error"] = str(e)
    return result


async def priority_fetch_one(page, item, run_date, index, total):
    size, url = item["size"], item["url"]
    result = {
        "run_date": run_date, "source": "priority",
        "model": item.get("model"), "size": size,
        "sku": item.get("sku"), "in_stock": item.get("in_stock"),
        "price_per_tire": None, "total_4_tires": None,
        "original_price": None, "easy_score": None, "reviews": None,
        "rating": None, "review_count": None,
        "warranty": None, "url": url, "error": "",
    }

    for attempt in range(1, PRIORITY_MAX_RETRY + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            # Script tags are intentionally hidden. Wait for the JSON node to
            # exist in the DOM instead of waiting for it to become visible.
            await page.wait_for_selector(
                "script#__NEXT_DATA__",
                state="attached",
                timeout=20_000,
            )

            # Priority fully hydrates price, stock, SKU and URL data only for
            # the selected variant after browser navigation. Read that record
            # before parsing the page.
            selected_id = await page.evaluate("""() => {
                const el = document.getElementById('__NEXT_DATA__');
                if (!el) return null;
                const nd = JSON.parse(el.textContent);
                const apollo = nd?.props?.pageProps?.apolloState || {};
                for (const [key, value] of Object.entries(apollo)) {
                    if (key.startsWith('SimpleProduct:') &&
                        value && typeof value === 'object' &&
                        value.price_range) {
                        return String(value.id || value.uid || '');
                    }
                }
                return null;
            }""")

            expected_id = str(item.get("sku")) if item.get("sku") else None
            result["url"] = page.url

            # Some obsolete Priority variants redirect to another size. Do
            # not save the redirected product under the requested size label.
            if expected_id and selected_id and selected_id != expected_id:
                result["in_stock"] = False
                result["error"] = (
                    f"Variant {expected_id} redirected to {selected_id}; skipped"
                )
                break

            html = await page.content()
            result.update(
                priority_parse_next_data(
                    html,
                    size,
                    url=page.url,
                    sku=expected_id,
                )
            )

            if result.get("price_per_tire") is None:
                result["error"] = "Price not found after browser navigation"
            break

        except PlaywrightTimeout as e:
            if attempt < PRIORITY_MAX_RETRY:
                await asyncio.sleep(3 * attempt)
            else:
                result["error"] = f"Timeout after {PRIORITY_MAX_RETRY} retries: {e}"
        except Exception as e:
            result["error"] = f"Browser scrape error: {e}"
            break

    price = f"${result.get('price_per_tire') or 'N/A'}"
    stock = "In Stock" if result.get("in_stock") else "OOS"
    err   = f"  !! {result['error']}" if result.get("error") else ""
    print(f"  [{index:02}/{total}] {result['model']:22s}  {result['size']:28s}  {price:>10}  {stock}{err}")
    return result


async def run_priority(run_date):
    all_size_links = []
    print("\n" + "═"*60)
    print("  PRIORITY TIRE")
    print("═"*60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage","--disable-gpu"],
        )
        ctx  = await browser.new_context(user_agent=PRIORITY_HEADERS["User-Agent"])
        discovery_page = await ctx.new_page()
        detail_page = await ctx.new_page()
        await discovery_page.route("**attentive**", lambda r: r.abort())
        await discovery_page.route("**attn.tv**",   lambda r: r.abort())
        await detail_page.route("**attentive**", lambda r: r.abort())
        await detail_page.route("**attn.tv**",   lambda r: r.abort())

        for seed in PRIORITY_SEEDS:
            links = await priority_get_size_urls(discovery_page, seed)
            all_size_links.extend(links)

        total = len(all_size_links)
        print(f"\n  Total Priority sizes: {total}\n")

        results = []
        for i, item in enumerate(all_size_links, 1):
            result = await priority_fetch_one(
                detail_page, item, run_date, i, total
            )
            results.append(result)
            await asyncio.sleep(0.4)

        await browser.close()

    print(f"\n  Priority: {len(results)} rows collected.")
    return list(results)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
async def main():
    run_date = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    giga_results     = []
    priority_results = await run_priority(run_date)

    all_results = giga_results + priority_results

    df = pd.DataFrame(all_results)
    for col in ["price_per_tire", "original_price", "total_4_tires"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Column order
    col_order = [
        "run_date", "source", "model", "size",
        "original_price", "easy_score", "reviews",
        "sku", "rating", "review_count",
        "in_stock", "price_per_tire", "total_4_tires",
        "warranty", "url", "error"
    ]
    df = df.reindex(columns=col_order)

    date_str  = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    out_file  = f"tire_prices_{date_str}.csv"
    df.to_csv(out_file, index=False)

    print(f"\n✅ Saved → {out_file}  ({len(df)} rows)")
    print(f"   Giga: {len(giga_results)} rows | Priority: {len(priority_results)} rows")

    errors = df[df["error"].notna() & (df["error"] != "")]
    if not errors.empty:
        print(f"⚠️  {len(errors)} rows had errors — check the CSV 'error' column.")

    # Exit with error code if everything failed (helps Actions flag the run)
    if len(errors) == len(df):
        sys.exit(1)


asyncio.run(main())
